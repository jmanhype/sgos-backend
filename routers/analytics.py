"""Analytics endpoints — virality explanation, pattern analysis."""
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["analytics"])


@router.get("/analytics/explain/{post_id}")
async def analytics_explain(post_id: str):
    """
    Explain WHY a post went viral using LLM analysis.
    Returns hook type, emotional trigger, replication strategy.
    """
    from viral_analytics import explain_virality
    result = explain_virality(post_id)
    if "error" in result and "raw_response" not in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/analytics/patterns")
async def analytics_patterns(
    limit: int = Query(10, description="Number of viral posts to analyze"),
):
    """
    Aggregate patterns across all viral posts.
    Shows platform distribution, common hooks, avg engagement.
    """
    from viral_analytics import analyze_viral_patterns
    return analyze_viral_patterns(limit=limit)
