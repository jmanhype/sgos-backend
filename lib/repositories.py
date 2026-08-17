"""
Repository Pattern — DB access abstraction.

Trade-offs:
  (+) Routers never touch raw SQL — schema changes in ONE place
  (+) Connection management (WAL, busy_timeout) centralized
  (+) Testable with in-memory SQLite
  (+) Batch operations avoid connection-per-query overhead
  (-) Slight overhead for simple queries vs raw SQL
  (-) Complex JOINs still leak into repository methods

Design choice: Each repository wraps ONE table but exposes domain queries
as methods (e.g., strikes.posted_last_24h()) rather than forcing callers
to compose SQL fragments.

SECURITY:
  - All column names are validated against ALLOWED_COLUMNS whitelist
  - Table names are constants, never user input
  - All user data uses parameterized queries (?)
"""

from typing import Optional, Any
from contextlib import contextmanager

from database import get_connection

# Whitelist of allowed column names per table — prevents SQL injection via kwargs
ALLOWED_COLUMNS: dict[str, set[str]] = {
    "projects": {"name", "description", "status", "repo_url", "category", "updated_at", "created_at"},
    "style_guide": {"category", "rule", "example", "priority", "source", "updated_at", "created_at"},
    "strikes": {"author", "draft", "strike_score", "grounding_pct", "status", "reply_tweet_id", "posted_at", "urgency_score", "audience_score", "engagement_velocity", "topic_match", "follower_tier", "created_at"},
    "scout_history": {"target_handle", "draft", "score", "grounding_pct", "status", "created_at"},
    "daily_alpha": {"title", "content", "score", "source_url", "posted", "created_at"},
    "post_performance": {"tweet_id", "impressions", "likes", "replies", "retweets", "engagement_rate", "tracked_at", "rechecked_at"},
    "reply_targets": {"handle", "followers", "topic", "active", "last_engaged", "engagement_count"},
}


class Repository:
    """Base repository — manages connection lifecycle and common patterns."""

    def __init__(self, db_path: Any = None):
        self.db_path = db_path

    @contextmanager
    def connection(self):
        """Context-managed connection with WAL mode and busy timeout."""
        conn = get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @contextmanager
    def batch(self):
        """Batch multiple operations in a single connection. Use for loops."""
        conn = get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a SELECT and return list of dicts."""
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """Execute a SELECT and return single row or None."""
        with self.connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Execute an INSERT/UPDATE/DELETE and return rowcount."""
        with self.connection() as conn:
            cursor = conn.execute(sql, params)
            return cursor.rowcount

    def execute_insert(self, sql: str, params: tuple = ()) -> int:
        """Execute an INSERT and return the lastrowid."""
        with self.connection() as conn:
            cursor = conn.execute(sql, params)
            return cursor.lastrowid

    def count(self, table: str, where: str = "1=1", params: tuple = ()) -> int:
        """Count rows matching a condition. Table must be in ALLOWED_COLUMNS."""
        # SECURITY: Validate table name against whitelist
        if table not in ALLOWED_COLUMNS:
            raise ValueError(f"Unknown table: {table}. Allowed: {list(ALLOWED_COLUMNS.keys())}")
        # SECURITY: Only allow simple WHERE clauses with parameterized values
        # Reject any WHERE that contains semicolons, comments, or subqueries
        if any(danger in where.lower() for danger in [";", "--", "/*", "drop", "delete", "insert", "update", "alter"]):
            raise ValueError(f"Unsafe WHERE clause rejected")
        result = self.query_one(f"SELECT count(*) as n FROM {table} WHERE {where}", params)
        return result["n"] if result else 0

    def safe_update(self, table: str, row_id: int, data: dict) -> int:
        """Update with column name validation. Only ALLOWED_COLUMNS pass through."""
        allowed = ALLOWED_COLUMNS.get(table)
        if allowed is None:
            raise ValueError(f"Unknown table: {table}")

        # Filter to only allowed columns — silently drops unknown keys
        safe_data = {k: v for k, v in data.items() if k in allowed}
        if not safe_data:
            return 0  # Nothing to update

        sets = ", ".join(f"{col} = ?" for col in safe_data)
        vals = tuple(safe_data.values()) + (row_id,)
        return self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", vals)


class StrikesRepository(Repository):
    """Repository for the strikes table."""

    def posted_last_24h(self, handle: Optional[str] = None) -> list[dict]:
        sql = """
            SELECT * FROM strikes
            WHERE status = 'posted'
            AND posted_at > datetime('now', '-24 hours')
        """
        params = ()
        if handle:
            sql += " AND author = ?"
            params = (handle,)
        return self.query(sql, params)

    def pending(self, limit: int = 50) -> list[dict]:
        return self.query(
            "SELECT * FROM strikes WHERE status = 'pending' ORDER BY strike_score DESC LIMIT ?",
            (limit,)
        )

    def by_author(self, handle: str, limit: int = 20) -> list[dict]:
        return self.query(
            "SELECT * FROM strikes WHERE author = ? ORDER BY created_at DESC LIMIT ?",
            (handle, limit)
        )

    def with_tweet_ids(self, days: int = 30) -> list[dict]:
        """Get posted strikes that have a reply_tweet_id for tracking."""
        return self.query("""
            SELECT * FROM strikes
            WHERE status = 'posted'
            AND reply_tweet_id IS NOT NULL
            AND reply_tweet_id != ''
            AND posted_at > datetime('now', ? || ' days')
        """, (f"-{days}",))

    def needs_tracking(self, days: int = 30) -> list[dict]:
        """Get posted strikes whose tracking data is stale or missing."""
        return self.query("""
            SELECT s.* FROM strikes s
            LEFT JOIN post_performance pp ON pp.tweet_id = s.reply_tweet_id
            WHERE s.status = 'posted'
            AND s.reply_tweet_id IS NOT NULL
            AND s.reply_tweet_id != ''
            AND s.posted_at > datetime('now', ? || ' days')
            AND (pp.tweet_id IS NULL OR pp.rechecked_at < datetime('now', '-6 hours'))
        """, (f"-{days}",))

    def update_score(self, strike_id: int, score: float):
        self.execute("UPDATE strikes SET strike_score = ? WHERE id = ?", (score, strike_id))

    def dismiss(self, strike_id: int):
        self.execute("UPDATE strikes SET status = 'dismissed' WHERE id = ?", (strike_id,))

    def mark_posted(self, strike_id: int, tweet_id: str = None):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        if tweet_id:
            self.execute(
                "UPDATE strikes SET status = 'posted', reply_tweet_id = ?, posted_at = ? WHERE id = ?",
                (tweet_id, now, strike_id)
            )
        else:
            self.execute(
                "UPDATE strikes SET status = 'posted', posted_at = ? WHERE id = ?",
                (now, strike_id)
            )

    def engagement_count(self, handle: str) -> int:
        result = self.query_one(
            "SELECT count(*) as n FROM strikes WHERE author = ? AND status = 'posted'",
            (handle,)
        )
        return result["n"] if result else 0

    def last_posted_at(self, handle: str) -> Optional[str]:
        row = self.query_one(
            "SELECT posted_at FROM strikes WHERE author = ? AND status = 'posted' ORDER BY posted_at DESC LIMIT 1",
            (handle,)
        )
        return row["posted_at"] if row else None

    def stats(self) -> dict:
        return self.query_one("""
            SELECT
                count(*) as total,
                sum(CASE WHEN status = 'posted' THEN 1 ELSE 0 END) as posted,
                sum(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                sum(CASE WHEN status = 'dismissed' THEN 1 ELSE 0 END) as dismissed,
                avg(CASE WHEN status = 'posted' THEN strike_score END) as avg_score
            FROM strikes
        """) or {}

    def recent_drafts(self, limit: int = 10) -> list[dict]:
        return self.query(
            "SELECT draft FROM strikes WHERE draft IS NOT NULL AND draft != '' ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )


class PostsRepository(Repository):
    """Repository for the posts table (ingested content)."""

    def recent(self, limit: int = 50, topic: Optional[str] = None) -> list[dict]:
        if topic:
            return self.query(
                "SELECT * FROM posts WHERE topic = ? ORDER BY ingested_at DESC LIMIT ?",
                (topic, limit)
            )
        return self.query("SELECT * FROM posts ORDER BY ingested_at DESC LIMIT ?", (limit,))

    def outliers(self, min_score: float = 0.8) -> list[dict]:
        return self.query("""
            SELECT p.*, pe.outlier_score
            FROM posts p
            LEFT JOIN post_embeddings pe ON pe.post_id = p.id
            WHERE pe.outlier_score >= ?
            ORDER BY pe.outlier_score DESC
        """, (min_score,))

    def by_creator(self, handle: str, limit: int = 20) -> list[dict]:
        return self.query(
            "SELECT * FROM posts WHERE creator_handle = ? ORDER BY ingested_at DESC LIMIT ?",
            (handle, limit)
        )

    def search(self, query: str, limit: int = 20) -> list[dict]:
        return self.query(
            "SELECT * FROM posts WHERE content LIKE ? OR title LIKE ? ORDER BY ingested_at DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", limit)
        )


class ReplyTargetsRepository(Repository):
    """Repository for the reply_targets table."""

    def active(self) -> list[dict]:
        return self.query(
            "SELECT * FROM reply_targets WHERE active = 1 ORDER BY last_engaged DESC"
        )

    def add(self, handle: str, followers: int = 0, topic: str = ""):
        existing = self.query_one("SELECT handle FROM reply_targets WHERE handle = ?", (handle,))
        if not existing:
            self.execute_insert(
                "INSERT INTO reply_targets (handle, followers, topic, active) VALUES (?, ?, ?, 1)",
                (handle, followers, topic)
            )

    def remove(self, handle: str):
        self.execute("UPDATE reply_targets SET active = 0 WHERE handle = ?", (handle,))

    def update_engagement(self, handle: str):
        self.execute(
            "UPDATE reply_targets SET last_engaged = datetime('now'), engagement_count = engagement_count + 1 WHERE handle = ?",
            (handle,)
        )

    def never_engaged(self) -> list[dict]:
        return self.query(
            "SELECT * FROM reply_targets WHERE active = 1 AND engagement_count = 0"
        )

    def stale(self, days: int = 30) -> list[dict]:
        return self.query(
            "SELECT * FROM reply_targets WHERE active = 1 AND last_engaged < datetime('now', ? || ' days')",
            (f"-{days}",)
        )


class PerformanceRepository(Repository):
    """Repository for post_performance table."""

    def upsert(self, tweet_id: str, impressions: int = 0, likes: int = 0,
               replies: int = 0, retweets: int = 0, engagement_rate: float = 0.0):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        existing = self.query_one("SELECT tweet_id FROM post_performance WHERE tweet_id = ?", (tweet_id,))
        if existing:
            self.execute("""
                UPDATE post_performance SET
                    impressions = ?, likes = ?, replies = ?, retweets = ?,
                    engagement_rate = ?, rechecked_at = ?
                WHERE tweet_id = ?
            """, (impressions, likes, replies, retweets, engagement_rate, now, tweet_id))
        else:
            self.execute_insert("""
                INSERT INTO post_performance (tweet_id, impressions, likes, replies, retweets, engagement_rate, tracked_at, rechecked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (tweet_id, impressions, likes, replies, retweets, engagement_rate, now, now))

    def upsert_batch(self, items: list[dict]):
        """Batch upsert for tracker loop — single connection for all items."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self.batch() as conn:
            for item in items:
                tweet_id = item["tweet_id"]
                existing = conn.execute(
                    "SELECT tweet_id FROM post_performance WHERE tweet_id = ?", (tweet_id,)
                ).fetchone()
                if existing:
                    conn.execute("""
                        UPDATE post_performance SET
                            impressions = ?, likes = ?, replies = ?, retweets = ?,
                            engagement_rate = ?, rechecked_at = ?
                        WHERE tweet_id = ?
                    """, (item.get("impressions", 0), item.get("likes", 0),
                          item.get("replies", 0), item.get("retweets", 0),
                          item.get("engagement_rate", 0.0), now, tweet_id))
                else:
                    conn.execute("""
                        INSERT INTO post_performance (tweet_id, impressions, likes, replies, retweets, engagement_rate, tracked_at, rechecked_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (tweet_id, item.get("impressions", 0), item.get("likes", 0),
                          item.get("replies", 0), item.get("retweets", 0),
                          item.get("engagement_rate", 0.0), now, now))

    def for_tweet(self, tweet_id: str) -> Optional[dict]:
        return self.query_one("SELECT * FROM post_performance WHERE tweet_id = ?", (tweet_id,))


class ProjectsRepository(Repository):
    """Repository for projects table."""

    TABLE = "projects"

    def all(self) -> list[dict]:
        return self.query("SELECT * FROM projects ORDER BY updated_at DESC")

    def get(self, project_id: int) -> Optional[dict]:
        return self.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))

    def exists_by_name(self, name: str) -> bool:
        row = self.query_one("SELECT id FROM projects WHERE name = ?", (name,))
        return row is not None

    def create(self, name: str, description: str = "", status: str = "active",
               repo_url: str = "", category: str = "") -> int:
        from datetime import datetime, timezone
        # Prevent duplicates
        if self.exists_by_name(name):
            raise ValueError(f"Project '{name}' already exists")
        # Validate status
        if status not in ("active", "paused", "archived", "draft"):
            raise ValueError(f"Invalid status: {status}")
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_insert("""
            INSERT INTO projects (name, description, status, repo_url, category, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (name, description, status, repo_url, category, now, now))

    def update(self, project_id: int, **kwargs):
        from datetime import datetime, timezone
        kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()
        # SECURITY: Use safe_update with column whitelist
        return self.safe_update("projects", project_id, kwargs)

    def delete(self, project_id: int):
        self.execute("DELETE FROM projects WHERE id = ?", (project_id,))


class StyleGuideRepository(Repository):
    """Repository for style_guide table."""

    VALID_CATEGORIES = {"voice", "engagement", "quality", "formatting", "creative"}

    def all(self) -> list[dict]:
        return self.query("SELECT * FROM style_guide ORDER BY category, priority DESC")

    def get(self, guide_id: int) -> Optional[dict]:
        return self.query_one("SELECT * FROM style_guide WHERE id = ?", (guide_id,))

    def by_category(self, category: str) -> list[dict]:
        return self.query(
            "SELECT * FROM style_guide WHERE category = ? ORDER BY priority DESC",
            (category,)
        )

    def create(self, category: str, rule: str, example: str = "",
               priority: int = 5, source: str = "") -> int:
        from datetime import datetime, timezone
        # Validate inputs
        if category not in self.VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {category}. Allowed: {self.VALID_CATEGORIES}")
        if not rule or not rule.strip():
            raise ValueError("Rule text cannot be empty")
        priority = max(1, min(10, int(priority)))
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_insert("""
            INSERT INTO style_guide (category, rule, example, priority, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (category, rule.strip(), example.strip(), priority, source, now, now))

    def update(self, guide_id: int, **kwargs):
        from datetime import datetime, timezone
        kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()
        # Validate category if present
        if "category" in kwargs and kwargs["category"] not in self.VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {kwargs['category']}")
        # Validate priority if present
        if "priority" in kwargs:
            kwargs["priority"] = max(1, min(10, int(kwargs["priority"])))
        # SECURITY: Use safe_update with column whitelist
        return self.safe_update("style_guide", guide_id, kwargs)

    def delete(self, guide_id: int):
        self.execute("DELETE FROM style_guide WHERE id = ?", (guide_id,))
