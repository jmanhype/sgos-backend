"""
Feedback endpoints — Performance tracking + adaptive scoring.
Thin router — all logic delegated to FeedbackService.
"""
from fastapi import APIRouter, HTTPException, Query

from services.feedback import feedback_service

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("/published")
async def mark_published(
    opportunity_id: int = Query(...),
    genome_id: str = Query(...),
    variant_type: str = Query("post"),
    score: float = Query(50.0),
    score_breakdown: str = Query("{}"),
    platform: str = Query("twitter"),
):
    """Mark a pipeline opportunity as published."""
    return feedback_service.mark_published(
        opportunity_id=opportunity_id,
        genome_id=genome_id,
        variant_type=variant_type,
        score_at_generation=score,
        score_breakdown=score_breakdown,
        platform=platform,
    )


@router.post("/performance/{feedback_id}")
async def record_performance(
    feedback_id: int,
    impressions: int = Query(0),
    engagements: int = Query(0),
    likes: int = Query(0),
    reposts: int = Query(0),
    replies: int = Query(0),
    clicks: int = Query(0),
):
    """Record real-world performance metrics for a published post."""
    return feedback_service.record_performance(
        feedback_id=feedback_id,
        impressions=impressions,
        engagements=engagements,
        likes=likes,
        reposts=reposts,
        replies=replies,
        clicks=clicks,
    )


@router.get("/stats")
async def get_stats():
    """Get feedback statistics and adaptive scoring status."""
    return feedback_service.get_stats()


@router.get("/list")
async def list_feedback(
    limit: int = Query(50, ge=1, le=200),
    tier: str = Query(None),
):
    """List performance feedback records."""
    return {"records": feedback_service.get_feedback_list(limit=limit, tier=tier)}


@router.post("/train")
async def train_weights():
    """
    Retrain scorer weights based on performance feedback.
    Requires minimum 10 data points with performance metrics.
    """
    return feedback_service.train_weights()
