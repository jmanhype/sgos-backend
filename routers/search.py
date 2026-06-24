"""Search endpoints — FTS5 keyword, TF-IDF vector, hybrid search.
Thin router — all logic delegated to SearchService."""
from fastapi import APIRouter, Query

from services.search import search_service

router = APIRouter(tags=["search"])


@router.get("/search")
async def search_posts(
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    platform: str = Query(None, description="Platform filter (reddit, hackernews, twitter, or all)"),
    limit: int = Query(20, ge=1, le=200, description="Max results"),
):
    """Full-text search across all ingested posts."""
    return search_service.keyword_search(q, platform=platform, limit=limit)


@router.get("/search/hybrid")
async def hybrid_search_endpoint(
    q: str = Query(..., description="Search query"),
    platform: str = Query(None, description="Platform filter"),
    limit: int = Query(20, description="Max results"),
):
    """Hybrid search: merges FTS5 keyword + TF-IDF vector results."""
    return search_service.hybrid_search(q, platform=platform, limit=limit)


@router.post("/search/build-index")
async def rebuild_index(platform: str = None):
    """Build or rebuild the TF-IDF search index."""
    return search_service.rebuild_index(platform=platform)


@router.get("/search/similar")
async def search_similar_posts(
    q: str = Query(...),
    limit: int = Query(10),
    platform: str = None,
):
    """Semantic search via TF-IDF cosine similarity."""
    return search_service.similar_posts(q, platform=platform, limit=limit)


@router.get("/search/related/{post_id}")
async def get_related_posts(post_id: str, limit: int = Query(5)):
    """Find posts similar to a specific post."""
    return search_service.related_posts(post_id, limit=limit)
