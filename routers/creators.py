"""Creator tracking endpoints — follow, unfollow, list, stats, discovery.
Thin router — all logic delegated to CreatorsService."""
from fastapi import APIRouter, Query

from services.creators import creators_service

router = APIRouter(tags=["creators"])


@router.post("/creators/follow")
async def follow_creator(
    handle: str = Query(...),
    platform: str = Query("twitter"),
    niche: str = Query(""),
):
    """Start tracking a creator."""
    return creators_service.follow(handle, platform, niche=niche)


@router.delete("/creators/unfollow")
async def unfollow_creator(
    handle: str = Query(...),
    platform: str = Query("twitter"),
):
    """Stop tracking a creator."""
    return creators_service.unfollow(handle, platform)


@router.get("/creators")
async def get_creators(platform: str = None, niche: str = None):
    """List all tracked creators."""
    return creators_service.list_all(platform=platform, niche=niche)


@router.get("/creators/{handle}/posts")
async def get_tracked_posts(
    handle: str,
    limit: int = Query(20),
    outliers_only: bool = Query(False),
):
    """Get posts from a tracked creator."""
    return creators_service.get_posts(handle, limit=limit, outliers_only=outliers_only)


@router.get("/creators/stats")
async def creator_stats():
    """Get stats for all tracked creators."""
    return creators_service.stats()


@router.post("/creators/discover")
async def discover_creators(
    platform: str = Query(None, description="Filter by platform"),
    min_score: int = Query(100, description="Minimum avg post score"),
    limit: int = Query(10, description="Max creators to return"),
):
    """Auto-discover high-performing creators from the post database."""
    return creators_service.discover(platform=platform, min_score=min_score, limit=limit)
