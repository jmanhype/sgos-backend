"""Search service — FTS5 keyword, TF-IDF vector, hybrid search."""
from database import get_connection


class SearchService:
    @staticmethod
    def keyword_search(q: str, platform: str | None = None, limit: int = 20) -> dict:
        """Full-text search via FTS5 with LIKE fallback."""
        conn = get_connection()
        c = conn.cursor()

        platform_where = ""
        params: list = [q]
        if platform and platform != "all":
            platform_where = "AND p.platform = ?"
            params.append(platform)

        try:
            rows = c.execute(f"""
                SELECT p.*, rank
                FROM posts_fts fts
                JOIN posts p ON p.rowid = fts.rowid
                WHERE posts_fts MATCH ? {platform_where}
                ORDER BY rank
                LIMIT ?
            """, params + [limit]).fetchall()
            results = [dict(r) for r in rows]
        except Exception:
            plat_where = ""
            like_params: list = [f"%{q}%", f"%{q}%"]
            if platform and platform != "all":
                plat_where = "AND platform = ?"
                like_params.append(platform)
            rows = c.execute(f"""
                SELECT * FROM posts
                WHERE (title LIKE ? OR content LIKE ?) {plat_where}
                ORDER BY z_score DESC
                LIMIT ?
            """, like_params + [limit]).fetchall()
            results = [dict(r) for r in rows]

        return {"query": q, "count": len(results), "results": results}

    @staticmethod
    def hybrid_search(q: str, platform: str | None = None, limit: int = 20) -> dict:
        """Hybrid search: FTS5 + TF-IDF via Reciprocal Rank Fusion."""
        from hybrid_search import hybrid_search_with_context
        return hybrid_search_with_context(q, limit=limit, platform=platform)

    @staticmethod
    def similar_posts(q: str, platform: str | None = None, limit: int = 10) -> dict:
        """Semantic similarity search via TF-IDF cosine."""
        from vector_search import search_similar
        results = search_similar(q, limit=limit, platform=platform)
        return {"query": q, "results": results, "count": len(results)}

    @staticmethod
    def related_posts(post_id: str, limit: int = 5) -> dict:
        """Find posts similar to a specific post."""
        from vector_search import find_similar_posts
        results = find_similar_posts(post_id, limit=limit)
        return {"post_id": post_id, "related": results, "count": len(results)}

    @staticmethod
    def rebuild_index(platform: str | None = None) -> dict:
        """Build or rebuild TF-IDF search index."""
        from vector_search import build_index
        return build_index(platform=platform, rebuild=True)


search_service = SearchService()
