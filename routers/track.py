"""Performance tracking router — close the feedback loop."""
from fastapi import APIRouter, Query
from tracker import track_tweet, refresh_tweet, refresh_due_tweets, get_performance_summary, train_scorer_from_performance

router = APIRouter(prefix="/track", tags=["tracking"])


@router.post("")
async def track(
    tweet_url: str = Query(..., description="Tweet URL to track"),
    opportunity_id: int = Query(None, description="Linked pipeline opportunity ID"),
    notes: str = Query("", description="Optional notes"),
):
    """Track a tweet's performance metrics."""
    return track_tweet(tweet_url, opportunity_id=opportunity_id, notes=notes)


@router.post("/refresh/{record_id}")
async def refresh(record_id: int):
    """Re-fetch metrics for a tracked tweet."""
    return refresh_tweet(record_id)


@router.post("/refresh-due")
async def refresh_due():
    """Refresh all tweets due for a re-check (called by scheduler)."""
    return refresh_due_tweets()


@router.get("/summary")
async def summary():
    """Get performance summary and feedback loop stats."""
    return get_performance_summary()


@router.post("/train")
async def train():
    """Retrain scorer weights from performance data (needs 10+ tracked tweets)."""
    return train_scorer_from_performance()


@router.get("/posts")
async def get_posts():
    """Get all tracked posts with latest metrics."""
    from database import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM post_performance ORDER BY COALESCE(rechecked_at, tracked_at) DESC"
    ).fetchall()
    posts = [dict(r) for r in rows]
    return {"posts": posts, "total": len(posts)}
