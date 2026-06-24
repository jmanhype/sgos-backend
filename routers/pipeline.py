"""
Pipeline endpoints — Autonomous Viral Content Pipeline API.
Thin router — all logic delegated to PipelineEngine.
"""
from fastapi import APIRouter, HTTPException, Query

from services.pipeline import pipeline_engine

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post("/run")
async def run_pipeline(
    hours: int = Query(24, ge=1, le=168, description="Outlier lookback window"),
    limit: int = Query(10, ge=1, le=50, description="Max outliers to process"),
    num_variants: int = Query(3, ge=1, le=10, description="Variants per genome"),
    platform: str = Query("reddit", description="Platform filter"),
    voice_prompt: str = Query("", description="Voice/style guide for generation"),
):
    """
    Run the full viral content pipeline:
    1. Detect outliers
    2. Extract genomes
    3. Generate variants
    4. Score & store opportunities
    """
    from database import get_outliers

    outliers = get_outliers(platform=platform, hours=hours, limit=limit)
    if not outliers:
        return {"message": "No outliers found", "hours": hours, "platform": platform}

    result = pipeline_engine.process_outliers(
        outliers=outliers,
        voice_prompt=voice_prompt,
        num_variants=num_variants,
        skip_existing=True,
    )
    return result


@router.get("/opportunities")
async def get_opportunities(
    limit: int = Query(10, ge=1, le=50),
    unseen_only: bool = Query(True),
):
    """Get ranked content opportunities."""
    return {
        "opportunities": pipeline_engine.get_opportunities(
            limit=limit, unseen_only=unseen_only
        ),
    }


@router.post("/opportunities/{opportunity_id}/view")
async def mark_viewed(opportunity_id: int):
    """Mark an opportunity as viewed."""
    pipeline_engine.mark_viewed(opportunity_id)
    return {"status": "ok", "id": opportunity_id}


@router.post("/opportunities/{opportunity_id}/dismiss")
async def dismiss(opportunity_id: int):
    """Dismiss an opportunity."""
    pipeline_engine.dismiss(opportunity_id)
    return {"status": "ok", "id": opportunity_id}


@router.get("/genomes")
async def list_genomes(limit: int = Query(20, ge=1, le=100)):
    """List recently extracted viral genomes."""
    return {"genomes": pipeline_engine.get_genomes(limit=limit)}


@router.get("/genomes/top")
async def top_genomes(limit: int = Query(5, ge=1, le=20)):
    """Get highest-engagement genomes (best viral DNA)."""
    return {"genomes": pipeline_engine.get_top_genomes(limit=limit)}


@router.post("/genomes/{post_id}/regenerate")
async def regenerate(
    post_id: str,
    num_variants: int = Query(3, ge=1, le=10),
    voice_prompt: str = Query(""),
):
    """Re-generate variants for an existing genome."""
    result = pipeline_engine.regenerate_for_genome(
        post_id=post_id,
        voice_prompt=voice_prompt,
        num_variants=num_variants,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/stats")
async def pipeline_stats():
    """Get pipeline statistics."""
    return pipeline_engine.get_stats()
