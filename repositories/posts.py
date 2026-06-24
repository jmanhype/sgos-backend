"""Post repository — CRUD and query operations for the posts table."""
from database import get_connection


class PostRepository:
    @staticmethod
    def get_by_id(post_id: str) -> dict | None:
        conn = get_connection()
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def find_by_title(title_fragment: str) -> dict | None:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM posts WHERE title LIKE ? ORDER BY z_score DESC LIMIT 1",
            (f"%{title_fragment}%",),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def search_fts(query: str, platform: str | None = None, limit: int = 20) -> list[dict]:
        conn = get_connection()
        platform_where = ""
        params: list = [query]
        if platform and platform != "all":
            platform_where = "AND p.platform = ?"
            params.append(platform)
        try:
            rows = conn.execute(f"""
                SELECT p.*, rank
                FROM posts_fts fts
                JOIN posts p ON p.rowid = fts.rowid
                WHERE posts_fts MATCH ? {platform_where}
                ORDER BY rank
                LIMIT ?
            """, params + [limit]).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            plat_where = ""
            like_params: list = [f"%{query}%", f"%{query}%"]
            if platform and platform != "all":
                plat_where = "AND platform = ?"
                like_params.append(platform)
            rows = conn.execute(f"""
                SELECT * FROM posts
                WHERE (title LIKE ? OR content LIKE ?) {plat_where}
                ORDER BY z_score DESC LIMIT ?
            """, like_params + [limit]).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def discover_authors(
        min_score: int = 100,
        platform: str | None = None,
        limit: int = 30,
    ) -> list[dict]:
        conn = get_connection()
        query = """
            SELECT author, platform, subreddit,
                   COUNT(*) as post_count,
                   AVG(score) as avg_score,
                   MAX(score) as max_score,
                   AVG(COALESCE(comment_count, 0)) as avg_comments
            FROM posts
            WHERE author IS NOT NULL AND author != ''
            GROUP BY author, platform
            HAVING post_count >= 3 AND avg_score >= ?
            ORDER BY avg_score DESC
            LIMIT ?
        """
        params: list = [min_score, limit]
        if platform:
            query = query.replace("FROM posts", "FROM posts WHERE platform = ?")
            params.insert(0, platform)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]


posts_repo = PostRepository()
