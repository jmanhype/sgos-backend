"""Research service — outlier detection, trend analysis, brief generation."""
from datetime import datetime, timezone

from database import get_outliers, get_trending_topics, get_stats


class ResearchService:
    @staticmethod
    def generate_brief() -> dict:
        outliers = get_outliers(hours=48, limit=5, platform=None)
        trends = get_trending_topics(days=7, limit=5, platform=None)
        stats = get_stats()

        brief_lines = [
            f"# Daily Content Brief — {datetime.now(timezone.utc).strftime('%B %d, %Y')}",
            "",
            f"\U0001f4ca Database: {stats['total_posts']} posts tracked | {stats['outliers_24h']} outliers in 24h",
            "",
            "## \U0001f525 Top Outliers (posts performing 2-5\u00d7 above average)",
            "",
        ]

        for i, post in enumerate(outliers, 1):
            brief_lines.append(f"**{i}. [{post['title']}]({post['url']})**")
            brief_lines.append(
                f"   {post['platform']}/{post['subreddit']} | "
                f"\u2b06\ufe0f {post['score']} | "
                f"\U0001f4ac {post['comment_count']} | "
                f"z-score: {post['z_score']:.1f}"
            )
            if post.get("content") and len(post["content"]) > 10:
                snippet = post["content"][:200].replace("\n", " ")
                brief_lines.append(f"   > {snippet}...")
            brief_lines.append("")

        brief_lines.extend(["## \U0001f4c8 Trending Topics", ""])
        for topic in trends:
            brief_lines.append(f"- **{topic['topic']}** ({topic['count']} mentions)")

        brief_lines.extend([
            "",
            "## \U0001f4a1 Content Opportunities",
            "",
            "Pick an outlier above and generate content about it.",
            "Use StraughterG-os to create a thread, post, or article in your voice.",
        ])

        return {
            "brief": "\n".join(brief_lines),
            "outliers": outliers,
            "trends": trends,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def get_outliers(platform: str = "reddit", hours: int = 24, limit: int = 10) -> dict:
        outliers = get_outliers(platform=platform, hours=hours, limit=limit)
        return {"count": len(outliers), "hours": hours, "platform": platform, "outliers": outliers}

    @staticmethod
    def get_trends(platform: str = "reddit", days: int = 7, limit: int = 10) -> dict:
        topics = get_trending_topics(platform=platform, days=days, limit=limit)
        return {"count": len(topics), "days": days, "platform": platform, "topics": topics}

    @staticmethod
    def health_check() -> dict:
        from config import settings
        stats = get_stats()
        return {
            "status": "ok",
            "version": settings.version,
            "total_posts": stats["total_posts"],
            "last_ingest": stats["last_ingest"],
            "outliers_24h": stats["outliers_24h"],
        }

    @staticmethod
    def get_stats() -> dict:
        return get_stats()


research_service = ResearchService()
