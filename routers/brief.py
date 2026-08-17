"""Brief endpoints — Morning brief and genome evolution on demand."""
from fastapi import APIRouter, Query

router = APIRouter(prefix="/brief", tags=["brief"])


@router.post("/morning")
async def morning_brief():
    """Generate and deliver the morning content intelligence brief to Telegram."""
    from morning_brief import run_brief
    result = run_brief()
    return result


@router.post("/evolution")
async def evolution_report():
    """Generate and deliver the genome evolution report to Telegram."""
    from genome_evolution import run_evolution_report
    result = run_evolution_report()
    return result


@router.get("/evolution/data")
async def evolution_data(
    days_recent: int = Query(7, ge=1, le=30),
    days_baseline: int = Query(14, ge=7, le=60),
):
    """Get raw evolution analysis data (no Telegram delivery)."""
    from genome_evolution import analyze_evolution
    return analyze_evolution(days_recent=days_recent, days_baseline=days_baseline)


@router.get("/trending")
async def trending_topics(
    hours: int = Query(72, ge=6, le=168),
    limit: int = Query(8, ge=1, le=20),
):
    """Get trending keywords from recent viral content."""
    from morning_brief import get_trending_topics
    return get_trending_topics(hours=hours, limit=limit)


@router.get("/meta-shifts")
async def meta_shifts(days: int = Query(7, ge=3, le=30)):
    """Get meta shift analysis — emerging vs declining topics."""
    from morning_brief import get_meta_shifts
    return get_meta_shifts(days=days)
