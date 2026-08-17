"""Analytics endpoints — virality explanation, pattern analysis."""
from fastapi import APIRouter, HTTPException, Query
from database import get_connection

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


@router.get("/graph")
async def conversation_graph():
    """Return the conversation relationship graph — who's engaging back."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT handle, tier, relationship_score, total_likes_received,
                   total_replies_received, follows_us, last_engaged_at, notes
            FROM conversation_graph
            ORDER BY relationship_score DESC
        """).fetchall()
        result = {
            "total": len(rows),
            "accounts": [
                {
                    "handle": r["handle"],
                    "tier": r["tier"],
                    "score": r["relationship_score"],
                    "likes_received": r["total_likes_received"],
                    "replies_received": r["total_replies_received"],
                    "follows_us": bool(r["follows_us"]),
                    "last_engaged": r["last_engaged_at"],
                    "notes": r["notes"],
                }
                for r in rows
            ],
            "summary": {
                "mutual": sum(1 for r in rows if r["tier"] == "mutual"),
                "engaged": sum(1 for r in rows if r["tier"] == "engaged"),
                "warm": sum(1 for r in rows if r["tier"] == "warm"),
                "stranger": sum(1 for r in rows if r["tier"] == "stranger"),
            },
        }
        return result
    except Exception as e:
        return {"error": str(e), "accounts": [], "total": 0}
