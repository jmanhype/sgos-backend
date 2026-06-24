"""
SGOS Backend - Creator Tracking System
Track specific creators across platforms, monitor their viral posts, get alerts.
"""
import json
from datetime import datetime, timezone

from database import get_connection


def init_creator_tables():
    """Create creator tracking tables, migrating from old schema if needed."""
    conn = get_connection()
    c = conn.cursor()

    # Check if old schema exists (has 'username' column instead of 'handle')
    try:
        c.execute("PRAGMA table_info(creators)")
        cols = [row[1] for row in c.fetchall()]
        if cols and 'username' in cols and 'handle' not in cols:
            # Old schema detected - drop and recreate
            c.execute("DROP TABLE IF EXISTS creator_posts")
            c.execute("DROP TABLE IF EXISTS creator_alerts")
            c.execute("DROP TABLE IF EXISTS creators")
            conn.commit()
    except Exception:
        pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS creators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            handle TEXT NOT NULL,
            platform TEXT NOT NULL,  -- 'twitter', 'reddit', 'hackernews', 'youtube'
            display_name TEXT,
            bio TEXT,
            follower_count INTEGER DEFAULT 0,
            niche TEXT,  -- 'ai', 'saas', 'creator-economy', etc.
            tags TEXT,  -- JSON array
            is_active INTEGER DEFAULT 1,
            last_checked TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(handle, platform)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS creator_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL,
            platform_post_id TEXT,
            title TEXT,
            content TEXT,
            url TEXT,
            score INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            share_count INTEGER DEFAULT 0,
            engagement_rate REAL DEFAULT 0,
            z_score REAL DEFAULT 0,
            is_outlier INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            posted_at TEXT,
            FOREIGN KEY (creator_id) REFERENCES creators(id),
            UNIQUE(creator_id, platform_post_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS creator_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            alert_type TEXT DEFAULT 'outlier',  -- 'outlier', 'new_post', 'viral'
            message TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (creator_id) REFERENCES creators(id),
            FOREIGN KEY (post_id) REFERENCES creator_posts(id)
        )
    """)

    conn.commit()
    conn.close()


def add_creator(handle: str, platform: str, display_name: str = "", niche: str = "", tags: list[str] = None) -> dict:
    """Add a creator to track."""
    init_creator_tables()
    conn = get_connection()

    try:
        conn.execute(
            "INSERT OR IGNORE INTO creators (handle, platform, display_name, niche, tags) VALUES (?, ?, ?, ?, ?)",
            (handle, platform, display_name or handle, niche, json.dumps(tags or []))
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM creators WHERE handle=? AND platform=?",
            (handle, platform)
        ).fetchone()
        conn.close()
        return dict(row) if row else {"error": "Failed to add creator"}
    except Exception as e:
        conn.close()
        return {"error": str(e)}


def remove_creator(handle: str, platform: str) -> bool:
    """Stop tracking a creator."""
    init_creator_tables()
    conn = get_connection()
    conn.execute("UPDATE creators SET is_active=0 WHERE handle=? AND platform=?", (handle, platform))
    conn.commit()
    conn.close()
    return True


def list_creators(platform: str = None, niche: str = None) -> list[dict]:
    """List all tracked creators."""
    init_creator_tables()
    conn = get_connection()

    query = "SELECT c.*, COUNT(cp.id) as post_count, MAX(cp.posted_at) as latest_post FROM creators c LEFT JOIN creator_posts cp ON c.id = cp.creator_id WHERE c.is_active=1"
    params = []

    if platform:
        query += " AND c.platform=?"
        params.append(platform)
    if niche:
        query += " AND c.niche=?"
        params.append(niche)

    query += " GROUP BY c.id ORDER BY c.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_creator_post(creator_id: int, post_data: dict) -> dict:
    """Add a post from a tracked creator."""
    init_creator_tables()
    conn = get_connection()

    # Calculate engagement rate
    score = post_data.get('score', 0)
    comments = post_data.get('comment_count', 0)
    engagement = score + comments * 2  # Weight comments higher

    try:
        conn.execute("""
            INSERT OR IGNORE INTO creator_posts
            (creator_id, platform_post_id, title, content, url, score, comment_count, engagement_rate, posted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            creator_id,
            post_data.get('platform_post_id', ''),
            post_data.get('title', ''),
            post_data.get('content', '')[:2000],
            post_data.get('url', ''),
            score,
            comments,
            engagement,
            post_data.get('posted_at', datetime.now(timezone.utc).isoformat()),
        ))
        conn.commit()

        row = conn.execute(
            "SELECT * FROM creator_posts WHERE creator_id=? AND platform_post_id=?",
            (creator_id, post_data.get('platform_post_id', ''))
        ).fetchone()
        conn.close()

        result = dict(row) if row else {"error": "Failed to add post"}

        # Check if outlier and create alert
        if result.get('id') and score > 500:
            create_alert(creator_id, result['id'], 'outlier', f"High-performing post: {post_data.get('title', '')[:50]}")

        return result
    except Exception as e:
        conn.close()
        return {"error": str(e)}


def get_creator_posts(creator_id: int = None, handle: str = None, limit: int = 20, outliers_only: bool = False) -> list[dict]:
    """Get posts from a specific creator."""
    init_creator_tables()
    conn = get_connection()

    if handle and not creator_id:
        # Find creator by handle
        creator = conn.execute("SELECT id FROM creators WHERE handle=? AND is_active=1", (handle,)).fetchone()
        if creator:
            creator_id = creator['id']
        else:
            conn.close()
            return []

    query = """
        SELECT cp.*, c.handle, c.platform, c.display_name
        FROM creator_posts cp
        JOIN creators c ON cp.creator_id = c.id
        WHERE 1=1
    """
    params = []

    if creator_id:
        query += " AND cp.creator_id=?"
        params.append(creator_id)
    if outliers_only:
        query += " AND cp.is_outlier=1"

    query += " ORDER BY cp.posted_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_alert(creator_id: int, post_id: int, alert_type: str, message: str):
    """Create an alert for a creator's post."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO creator_alerts (creator_id, post_id, alert_type, message) VALUES (?, ?, ?, ?)",
        (creator_id, post_id, alert_type, message)
    )
    conn.commit()
    conn.close()


def get_alerts(unread_only: bool = True, limit: int = 20) -> list[dict]:
    """Get creator alerts."""
    init_creator_tables()
    conn = get_connection()

    query = """
        SELECT ca.*, c.handle, c.platform, c.display_name, cp.title as post_title, cp.url
        FROM creator_alerts ca
        JOIN creators c ON ca.creator_id = c.id
        JOIN creator_posts cp ON ca.post_id = cp.id
        WHERE 1=1
    """
    params = []

    if unread_only:
        query += " AND ca.is_read=0"

    query += " ORDER BY ca.created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_alert_read(alert_id: int):
    """Mark an alert as read."""
    conn = get_connection()
    conn.execute("UPDATE creator_alerts SET is_read=1 WHERE id=?", (alert_id,))
    conn.commit()
    conn.close()


def get_creator_stats(creator_id: int = None) -> dict:
    """Get stats for a creator or all creators.
    Queries the main posts table by author handle for real post data."""
    init_creator_tables()
    conn = get_connection()

    if creator_id:
        row = conn.execute("""
            SELECT c.handle, c.platform, c.display_name,
                (SELECT COUNT(*) FROM posts WHERE author = c.handle) as total_posts,
                (SELECT AVG(score) FROM posts WHERE author = c.handle AND score > 0) as avg_score,
                (SELECT MAX(score) FROM posts WHERE author = c.handle) as top_score,
                (SELECT SUM(comment_count) FROM posts WHERE author = c.handle) as total_comments,
                (SELECT COUNT(*) FROM posts WHERE author = c.handle AND z_score > 2.0) as outlier_count
            FROM creators c
            WHERE c.id = ?
        """, (creator_id,)).fetchone()
        conn.close()
        return dict(row) if row else {}
    else:
        rows = conn.execute("""
            SELECT c.handle, c.platform, c.display_name,
                (SELECT COUNT(*) FROM posts WHERE author = c.handle) as total_posts,
                (SELECT AVG(score) FROM posts WHERE author = c.handle AND score > 0) as avg_score,
                (SELECT MAX(score) FROM posts WHERE author = c.handle) as top_score,
                (SELECT SUM(comment_count) FROM posts WHERE author = c.handle) as total_comments,
                (SELECT COUNT(*) FROM posts WHERE author = c.handle AND z_score > 2.0) as outlier_count
            FROM creators c
            WHERE c.is_active = 1
            ORDER BY outlier_count DESC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    # Test
    init_creator_tables()

    # Add some test creators
    add_creator("elikiiba", "twitter", "Eli Kiba", "ai", ["founder", "ai-agents"])
    add_creator("kaboroev", "twitter", "Evgeny Kaborov", "ai", ["infra", "mlops"])
    add_creator("nichochar", "twitter", "Nicolas Charlot", "ai", ["ai-tools", "builder"])

    creators = list_creators()
    print(f"Tracking {len(creators)} creators:")
    for c in creators:
        print(f"  @{c['handle']} ({c['platform']}) — {c['niche']}")

    # Add a test post
    add_creator_post(creators[0]['id'], {
        'platform_post_id': 'test123',
        'title': 'Just shipped our AI agent framework',
        'content': 'After 6 months of building...',
        'url': 'https://x.com/elikiiba/status/test123',
        'score': 1200,
        'comment_count': 89,
    })

    posts = get_creator_posts(creator_id=creators[0]['id'])
    print(f"\nPosts from {creators[0]['handle']}: {len(posts)}")

    alerts = get_alerts()
    print(f"\nAlerts: {len(alerts)}")
    for a in alerts:
        print(f"  🔔 @{a['handle']}: {a['message']}")
