"""Creators service — tracking, discovery, stats."""
from database import get_connection
from creators import (
    add_creator,
    remove_creator,
    list_creators,
    get_creator_posts,
    get_creator_stats,
)


class CreatorsService:
    @staticmethod
    def follow(handle: str, platform: str = "twitter", niche: str = "") -> dict:
        return add_creator(handle, platform, niche=niche)

    @staticmethod
    def unfollow(handle: str, platform: str = "twitter") -> dict:
        removed = remove_creator(handle, platform)
        if not removed:
            return {"status": "not_found", "handle": handle, "platform": platform}
        return {"status": "unfollowed", "handle": handle}

    @staticmethod
    def list_all(platform: str | None = None, niche: str | None = None) -> dict:
        return {"creators": list_creators(platform=platform, niche=niche)}

    @staticmethod
    def get_posts(handle: str, limit: int = 20, outliers_only: bool = False) -> dict:
        posts = get_creator_posts(handle=handle, limit=limit, outliers_only=outliers_only)
        return {"handle": handle, "posts": posts, "count": len(posts)}

    @staticmethod
    def stats() -> dict:
        return get_creator_stats()

    @staticmethod
    def discover(
        platform: str | None = None,
        min_score: int = 100,
        limit: int = 10,
    ) -> dict:
        """Auto-discover high-performing creators not already tracked."""
        # Get tracked handles before opening our own query (list_creators manages its own conn)
        tracked = [c["handle"] for c in list_creators()]

        conn = get_connection()

        query = """
            SELECT author, platform, subreddit,
                   COUNT(*) as post_count,
                   AVG(score) as avg_score,
                   MAX(score) as max_score,
                   AVG(COALESCE(comment_count, 0)) as avg_comments
            FROM posts
            WHERE author IS NOT NULL AND author != ''
        """
        params: list = []

        if platform:
            query += " AND platform = ?"
            params.append(platform)

        query += """
            GROUP BY author, platform
            HAVING post_count >= 3 AND avg_score >= ?
            ORDER BY avg_score DESC
            LIMIT ?
        """
        params.extend([min_score, limit * 3])

        rows = conn.execute(query, params).fetchall()

        discovered = []
        for row in rows:
            r = dict(row)
            author = r["author"]
            if author not in tracked and author.lower() not in ["[deleted]", "automoderator"]:
                discovered.append({
                    "author": author,
                    "platform": r["platform"],
                    "subreddit": r.get("subreddit"),
                    "post_count": r["post_count"],
                    "avg_score": round(r["avg_score"], 1),
                    "max_score": r["max_score"],
                    "avg_comments": round(r["avg_comments"], 1),
                    "virality_score": round(r["avg_score"] * (1 + r["post_count"] * 0.1), 1),
                })
                if len(discovered) >= limit:
                    break

        return {
            "discovered": discovered,
            "count": len(discovered),
            "already_tracked": len(tracked),
        }


creators_service = CreatorsService()
