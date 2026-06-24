"""Creator tracking endpoints — follow, unfollow, list, stats, discovery."""
from fastapi import APIRouter, Query

from database import get_connection
from creators import (
    add_creator,
    remove_creator,
    list_creators,
    get_creator_posts,
    get_creator_stats,
)

router = APIRouter(tags=["creators"])


@router.post("/creators/follow")
async def follow_creator(
    handle: str = Query(...),
    platform: str = Query("twitter"),
    niche: str = Query(""),
):
    """Start tracking a creator."""
    return add_creator(handle, platform, niche=niche)


@router.delete("/creators/unfollow")
async def unfollow_creator(
    handle: str = Query(...),
    platform: str = Query("twitter"),
):
    """Stop tracking a creator."""
    remove_creator(handle, platform)
    return {"status": "unfollowed", "handle": handle}


@router.get("/creators")
async def get_creators(platform: str = None, niche: str = None):
    """List all tracked creators."""
    return {"creators": list_creators(platform=platform, niche=niche)}


@router.get("/creators/{handle}/posts")
async def get_tracked_posts(
    handle: str,
    limit: int = Query(20),
    outliers_only: bool = Query(False),
):
    """Get posts from a tracked creator."""
    posts = get_creator_posts(handle=handle, limit=limit, outliers_only=outliers_only)
    return {"handle": handle, "posts": posts, "count": len(posts)}


@router.get("/creators/stats")
async def creator_stats():
    """Get stats for all tracked creators."""
    return get_creator_stats()


@router.post("/creators/discover")
async def discover_creators(
    platform: str = Query(None, description="Filter by platform"),
    min_score: int = Query(100, description="Minimum avg post score"),
    limit: int = Query(10, description="Max creators to return"),
):
    """
    Auto-discover high-performing creators from the post database.
    Finds authors with consistently viral content you're not already tracking.
    """
    conn = get_connection()

    # Find top authors not already tracked
    tracked = [c["handle"] for c in list_creators()]

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
    params: list = [min_score, limit * 3]  # Get more to filter out tracked

    if platform:
        query = query.replace("FROM posts", "FROM posts WHERE platform = ?")
        params.insert(0, platform)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    # Filter out already-tracked creators
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
