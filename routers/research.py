"""Research endpoints — outliers, trends, daily brief, stats, health.
Thin router — all logic delegated to ResearchService."""
from fastapi import APIRouter, Query

from services.research import research_service

router = APIRouter(tags=["research"])


@router.get("/health")
async def health():
    """Health check with database stats."""
    try:
        result = research_service.health_check()
        return result
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


@router.get("/outliers")
async def list_outliers(
    platform: str = Query("reddit", description="Platform filter"),
    hours: int = Query(24, ge=1, le=720, description="Look back N hours"),
    limit: int = Query(10, ge=1, le=200, description="Max results"),
):
    """Get top outlier posts — posts performing significantly above their subreddit average."""
    return research_service.get_outliers(platform=platform, hours=hours, limit=limit)


@router.get("/trends")
async def list_trends(
    platform: str = Query("reddit", description="Platform filter"),
    days: int = Query(7, ge=1, le=90, description="Look back N days"),
    limit: int = Query(10, ge=1, le=100, description="Max topics"),
):
    """Get trending topics extracted from post titles."""
    return research_service.get_trends(platform=platform, days=days, limit=limit)


@router.get("/stats")
async def database_stats():
    """Full database statistics."""
    return research_service.get_stats()


@router.get("/brief")
async def daily_brief():
    """
    Generate a daily content brief — the key Phase 1 deliverable.
    Returns top outliers + trending topics formatted for content generation.
    """
    return research_service.generate_brief()
