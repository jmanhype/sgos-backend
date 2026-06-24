"""
SGOS Backend - Database Layer
SQLite with FTS5 for full-text search, WAL mode for concurrent reads.
Thread-local connection pool for safe multi-threaded access.
"""
import sqlite3
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = os.environ.get("SGOS_DB_PATH", str(Path(__file__).parent / "sgos.db"))

# Thread-local storage for connection pooling
_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """
    Get a WAL-mode SQLite connection (thread-local, reused per thread).
    Each thread gets its own persistent connection — avoids the overhead of
    opening/closing on every call while keeping threads isolated.
    """
    conn = getattr(_local, 'connection', None)
    if conn is not None:
        # Validate the connection is still usable
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.Error:
            # Connection went bad (e.g., disk full, file moved) — recreate
            try:
                conn.close()
            except Exception:
                pass
            _local.connection = None

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")  # Wait 5s for locks instead of failing
    conn.execute("PRAGMA synchronous=NORMAL")  # WAL + NORMAL = safe + fast
    _local.connection = conn
    return conn


def close_connection():
    """Close the thread-local connection. Call on thread shutdown if needed."""
    conn = getattr(_local, 'connection', None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.connection = None


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            platform_id TEXT NOT NULL,
            subreddit TEXT,
            title TEXT,
            content TEXT,
            author TEXT,
            url TEXT,
            score INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            upvote_ratio REAL DEFAULT 0,
            z_score REAL DEFAULT 0,
            created_at TEXT NOT NULL,
            ingested_at TEXT NOT NULL,
            embedding BLOB,
            UNIQUE(platform, platform_id)
        );

        CREATE TABLE IF NOT EXISTS sub_stats (
            subreddit TEXT PRIMARY KEY,
            mean_score REAL DEFAULT 0,
            stddev_score REAL DEFAULT 0,
            sample_size INTEGER DEFAULT 0,
            last_updated TEXT
        );

        CREATE TABLE IF NOT EXISTS creators (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            username TEXT NOT NULL,
            display_name TEXT,
            followed BOOLEAN DEFAULT 0,
            avg_score REAL DEFAULT 0,
            post_count INTEGER DEFAULT 0,
            UNIQUE(platform, username)
        );

        CREATE TABLE IF NOT EXISTS ingest_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            source TEXT NOT NULL,
            posts_added INTEGER DEFAULT 0,
            posts_updated INTEGER DEFAULT 0,
            errors TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform);
        CREATE INDEX IF NOT EXISTS idx_posts_subreddit ON posts(subreddit);
        CREATE INDEX IF NOT EXISTS idx_posts_score ON posts(score DESC);
        CREATE INDEX IF NOT EXISTS idx_posts_zscore ON posts(z_score DESC);
        CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_posts_ingested ON posts(ingested_at DESC);
    """)

    # Create FTS5 virtual table if not exists
    try:
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
                title, content, subreddit, author,
                content=posts, content_rowid=rowid
            )
        """)
    except sqlite3.OperationalError:
        pass  # FTS table already exists

    conn.commit()
    conn.close()


def upsert_post(post: dict) -> str:
    """Insert or update a post. Returns 'added' or 'updated'."""
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    existing = c.execute(
        "SELECT score FROM posts WHERE platform=? AND platform_id=?",
        (post["platform"], post["platform_id"])
    ).fetchone()

    if existing:
        c.execute("""
            UPDATE posts SET
                score=?, comment_count=?, upvote_ratio=?, z_score=?,
                title=?, content=?, author=?, url=?
            WHERE platform=? AND platform_id=?
        """, (
            post.get("score", 0), post.get("comment_count", 0),
            post.get("upvote_ratio", 0), post.get("z_score", 0),
            post.get("title", ""), post.get("content", ""),
            post.get("author", ""), post.get("url", ""),
            post["platform"], post["platform_id"]
        ))
        conn.commit()
        conn.close()
        return "updated"
    else:
        c.execute("""
            INSERT INTO posts (id, platform, platform_id, subreddit, title, content,
                             author, url, score, comment_count, upvote_ratio,
                             z_score, created_at, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post.get("id", f"{post['platform']}_{post['platform_id']}"),
            post["platform"], post["platform_id"],
            post.get("subreddit", ""),
            post.get("title", ""), post.get("content", ""),
            post.get("author", ""), post.get("url", ""),
            post.get("score", 0), post.get("comment_count", 0),
            post.get("upvote_ratio", 0), post.get("z_score", 0),
            post.get("created_at", now), now
        ))
        conn.commit()
        conn.close()
        return "added"


def update_sub_stats(subreddit: str):
    """Recompute mean/stddev for a subreddit from its posts."""
    conn = get_connection()
    c = conn.cursor()

    row = c.execute("""
        SELECT AVG(score), COUNT(*) as n
        FROM posts WHERE subreddit=? AND score > 0
    """, (subreddit,)).fetchone()

    if row and row["n"] > 5:
        mean = row[0]
        # Compute stddev manually (SQLite doesn't have STDDEV)
        rows = c.execute(
            "SELECT score FROM posts WHERE subreddit=? AND score > 0",
            (subreddit,)
        ).fetchall()
        scores = [r["score"] for r in rows]
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        stddev = variance ** 0.5

        now = datetime.now(timezone.utc).isoformat()
        c.execute("""
            INSERT OR REPLACE INTO sub_stats (subreddit, mean_score, stddev_score, sample_size, last_updated)
            VALUES (?, ?, ?, ?, ?)
        """, (subreddit, mean, stddev, len(scores), now))

    conn.commit()
    conn.close()


def compute_z_scores(subreddit: str):
    """Update z_scores for all posts in a subreddit."""
    conn = get_connection()
    c = conn.cursor()

    stats = c.execute(
        "SELECT mean_score, stddev_score FROM sub_stats WHERE subreddit=?",
        (subreddit,)
    ).fetchone()

    if stats and stats["stddev_score"] > 0:
        mean = stats["mean_score"]
        stddev = stats["stddev_score"]

        posts = c.execute(
            "SELECT id, score FROM posts WHERE subreddit=?",
            (subreddit,)
        ).fetchall()

        for post in posts:
            z = (post["score"] - mean) / stddev if stddev > 0 else 0
            c.execute("UPDATE posts SET z_score=? WHERE id=?", (z, post["id"]))

    conn.commit()
    conn.close()


def get_outliers(platform: str = None, hours: int = 24, limit: int = 10) -> list[dict]:
    """Get top outlier posts by z_score."""
    conn = get_connection()
    c = conn.cursor()

    if platform:
        rows = c.execute("""
            SELECT * FROM posts
            WHERE platform=?
              AND ingested_at >= datetime('now', ? || ' hours')
            ORDER BY z_score DESC
            LIMIT ?
        """, (platform, f"-{hours}", limit)).fetchall()
    else:
        rows = c.execute("""
            SELECT * FROM posts
            WHERE ingested_at >= datetime('now', ? || ' hours')
            ORDER BY z_score DESC
            LIMIT ?
        """, (f"-{hours}", limit)).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_trending_topics(platform: str = None, days: int = 7, limit: int = 10) -> list[dict]:
    """Extract trending topics from post titles using simple keyword frequency."""
    conn = get_connection()
    c = conn.cursor()

    # Get recent post titles
    if platform:
        rows = c.execute("""
            SELECT title, subreddit, score, z_score FROM posts
            WHERE platform=? AND ingested_at >= datetime('now', ? || ' days')
            ORDER BY z_score DESC
            LIMIT 500
        """, (platform, f"-{days}")).fetchall()
    else:
        rows = c.execute("""
            SELECT title, subreddit, score, z_score FROM posts
            WHERE ingested_at >= datetime('now', ? || ' days')
            ORDER BY z_score DESC
            LIMIT 500
        """, (f"-{days}",)).fetchall()

    conn.close()

    # Simple keyword extraction (Phase 1 — will upgrade to proper NLP later)
    from collections import Counter
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "and", "but", "or",
        "not", "no", "nor", "so", "yet", "both", "either", "neither",
        "each", "every", "all", "any", "few", "more", "most", "other",
        "some", "such", "than", "too", "very", "just", "about", "up",
        "it", "its", "i", "my", "me", "we", "our", "you", "your", "he",
        "she", "they", "them", "this", "that", "these", "those", "what",
        "which", "who", "whom", "how", "when", "where", "why", "if",
        "then", "there", "here", "also", "new", "get", "got", "like",
        "one", "two", "first", "really", "need", "want", "use", "using",
        "show", "ask", "tell", "hn:", "hn", "launch", "launches", "today",
    }

    words = Counter()
    for row in rows:
        title = row["title"].lower() if row["title"] else ""
        # Split on non-alphanumeric
        tokens = [w.strip() for w in title.split() if w.strip() not in stopwords and len(w.strip()) > 2]
        for token in tokens:
            words[token] += 1

    # Return top topics with their frequency
    return [
        {"topic": word, "count": count, "posts": count}
        for word, count in words.most_common(limit)
    ]


def get_stats() -> dict:
    """Get overall database statistics."""
    conn = get_connection()
    c = conn.cursor()

    total = c.execute("SELECT COUNT(*) as n FROM posts").fetchone()["n"]
    platforms = {}
    for row in c.execute("SELECT platform, COUNT(*) as n FROM posts GROUP BY platform").fetchall():
        platforms[row["platform"]] = row["n"]

    subreddits = {}
    for row in c.execute("SELECT subreddit, COUNT(*) as n FROM posts WHERE platform='reddit' GROUP BY subreddit ORDER BY n DESC").fetchall():
        subreddits[row["subreddit"]] = row["n"]

    last_ingest = c.execute(
        "SELECT MAX(ingested_at) as ts FROM posts"
    ).fetchone()["ts"]

    outliers_24h = c.execute("""
        SELECT COUNT(*) as n FROM posts
        WHERE z_score > 2.0 AND ingested_at >= datetime('now', '-24 hours')
    """).fetchone()["n"]

    conn.close()
    return {
        "total_posts": total,
        "platforms": platforms,
        "subreddits": subreddits,
        "last_ingest": last_ingest,
        "outliers_24h": outliers_24h,
    }


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
