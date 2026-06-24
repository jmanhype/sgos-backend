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

    # Alert on high-scoring opportunities
    if result.get("opportunities_created", 0) > 0:
        try:
            from services.pipeline.alerts import alert_high_score
            alert_result = alert_high_score(threshold=75.0)
            result["alerts_sent"] = alert_result.get("notified", 0)
        except Exception:
            pass

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


# ─── Platform Formatting ────────────────────────────────────────────────────

@router.get("/opportunities/{opportunity_id}/format")
async def format_opportunity(
    opportunity_id: int,
    platform: str = Query("x", description="Target platform: x, linkedin, bluesky, newsletter"),
):
    """
    Format an opportunity for a specific platform.
    Returns platform-ready content with char counts and warnings.
    """
    from services.pipeline.formatters import format_opportunity as fmt_opp

    # Get the opportunity
    opps = pipeline_engine.get_opportunities(limit=200, unseen_only=False)
    opp = next((o for o in opps if o["id"] == opportunity_id), None)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    result = fmt_opp(opp, platform)
    return {
        "platform": result.platform,
        "parts": result.parts,
        "char_counts": result.char_counts,
        "total_chars": result.total_chars,
        "warnings": result.warnings,
        "copy_ready": "\n\n".join(result.parts),
    }


@router.get("/opportunities/{opportunity_id}/format/all")
async def format_all_platforms(opportunity_id: int):
    """Format an opportunity for ALL supported platforms at once."""
    from services.pipeline.formatters import format_opportunity as fmt_opp, FORMATTERS

    opps = pipeline_engine.get_opportunities(limit=200, unseen_only=False)
    opp = next((o for o in opps if o["id"] == opportunity_id), None)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    results = {}
    for platform in ["x", "linkedin", "bluesky", "newsletter"]:
        result = fmt_opp(opp, platform)
        results[platform] = {
            "parts": result.parts,
            "char_counts": result.char_counts,
            "total_chars": result.total_chars,
            "warnings": result.warnings,
            "copy_ready": "\n\n".join(result.parts),
        }

    return {"opportunity_id": opportunity_id, "platforms": results}


# ─── Bulk Actions ───────────────────────────────────────────────────────────

@router.post("/opportunities/dismiss-all")
async def dismiss_all_unseen(
    below_score: float = Query(None, description="Only dismiss opportunities below this score"),
):
    """Dismiss all unseen opportunities (optionally filtered by score)."""
    opps = pipeline_engine.get_opportunities(limit=500, unseen_only=True)

    dismissed = 0
    for opp in opps:
        if below_score is not None and opp.get("score", 0) >= below_score:
            continue
        pipeline_engine.dismiss(opp["id"])
        dismissed += 1

    return {"status": "ok", "dismissed": dismissed, "below_score": below_score}


@router.post("/opportunities/regenerate-batch")
async def regenerate_batch(
    limit: int = Query(5, ge=1, le=20),
    min_score: float = Query(60.0, description="Only regenerate genomes from opportunities above this score"),
    voice_prompt: str = Query(""),
    num_variants: int = Query(3, ge=1, le=5),
):
    """Re-generate variants for top-performing genomes."""
    genomes = pipeline_engine.get_top_genomes(limit=limit)

    results = []
    for genome in genomes:
        result = pipeline_engine.regenerate_for_genome(
            post_id=genome["post_id"],
            voice_prompt=voice_prompt,
            num_variants=num_variants,
        )
        results.append(result)

    total_variants = sum(r.get("variants_generated", 0) for r in results)
    return {
        "status": "ok",
        "genomes_regenerated": len(results),
        "total_variants": total_variants,
        "details": results,
    }


@router.post("/opportunities/copy-batch")
async def copy_batch(
    limit: int = Query(5, ge=1, le=20),
    platform: str = Query("x"),
    min_score: float = Query(0),
):
    """Get top N unseen opportunities formatted for a platform, ready to copy."""
    from services.pipeline.formatters import format_opportunity as fmt_opp

    opps = pipeline_engine.get_opportunities(limit=limit, unseen_only=True)
    if min_score > 0:
        opps = [o for o in opps if o.get("score", 0) >= min_score]

    formatted = []
    for opp in opps[:limit]:
        result = fmt_opp(opp, platform)
        formatted.append({
            "id": opp["id"],
            "title": opp.get("title", ""),
            "score": opp.get("score", 0),
            "platform": platform,
            "parts": result.parts,
            "copy_ready": "\n\n".join(result.parts),
            "char_counts": result.char_counts,
            "warnings": result.warnings,
        })
        # Mark as viewed
        pipeline_engine.mark_viewed(opp["id"])

    return {"formatted": formatted, "count": len(formatted)}


# ─── Alerts ─────────────────────────────────────────────────────────────────

@router.get("/alerts")
async def pipeline_alerts(
    threshold: float = Query(75.0, description="Minimum score to alert on"),
    limit: int = Query(10),
):
    """Get unseen high-scoring opportunities that would trigger alerts."""
    from services.pipeline.alerts import get_pending_alerts
    return {"alerts": get_pending_alerts(threshold=threshold, limit=limit)}


@router.post("/alerts/check")
async def check_pipeline_alerts(
    threshold: float = Query(75.0),
):
    """Manually trigger alert check for high-scoring opportunities."""
    from services.pipeline.alerts import alert_high_score
    return alert_high_score(threshold=threshold)
