"""Search endpoints — FTS5 keyword, TF-IDF vector, hybrid search."""
from fastapi import APIRouter, Query

from database import get_connection

router = APIRouter(tags=["search"])


@router.get("/search")
async def search_posts(
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    platform: str = Query(None, description="Platform filter (reddit, hackernews, twitter, or all)"),
    limit: int = Query(20, ge=1, le=200, description="Max results"),
):
    """Full-text search across all ingested posts. Supports keyword search via FTS5."""
    conn = get_connection()
    c = conn.cursor()

    # Build platform filter — None or "all" means search everything
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
        # Fallback to LIKE search if FTS not available
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


@router.get("/search/hybrid")
async def hybrid_search_endpoint(
    q: str = Query(..., description="Search query"),
    platform: str = Query(None, description="Platform filter"),
    limit: int = Query(20, description="Max results"),
):
    """
    Hybrid search: merges FTS5 keyword + TF-IDF vector results
    via Reciprocal Rank Fusion. Posts found by BOTH methods rank highest.
    """
    from hybrid_search import hybrid_search_with_context
    return hybrid_search_with_context(q, limit=limit, platform=platform)


@router.post("/search/build-index")
async def rebuild_index(platform: str = None):
    """Build or rebuild the TF-IDF search index."""
    from vector_search import build_index
    result = build_index(platform=platform, rebuild=True)
    return result


@router.get("/search/similar")
async def search_similar_posts(
    q: str = Query(...),
    limit: int = Query(10),
    platform: str = None,
):
    """
    Semantic search: find posts similar to a query text.
    Uses TF-IDF cosine similarity (no external API needed).
    """
    from vector_search import search_similar
    results = search_similar(q, limit=limit, platform=platform)
    return {"query": q, "results": results, "count": len(results)}


@router.get("/search/related/{post_id}")
async def get_related_posts(post_id: str, limit: int = Query(5)):
    """Find posts similar to a specific post."""
    from vector_search import find_similar_posts
    results = find_similar_posts(post_id, limit=limit)
    return {"post_id": post_id, "related": results, "count": len(results)}
