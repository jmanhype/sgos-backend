"""Viral-Bench-Local integration endpoints - Generate content ideas from VBL corpus insights."""
from fastapi import APIRouter, Query, HTTPException, Request
from fastapi.responses import JSONResponse
from typing import Optional
import httpx
import os

router = APIRouter(prefix="/vbl", tags=["viral-bench"])

RESEARCH_SERVICE_URL = os.environ.get("RESEARCH_SERVICE_URL", "http://127.0.0.1:8001")


@router.get("/patterns")
async def get_patterns(niche: Optional[str] = Query(None, description="Filter by niche")):
    """Get viral patterns from VBL corpus (hooks, formats, retention triggers)."""
    from vbl_integration import get_niche_patterns
    patterns = get_niche_patterns(niche)
    return {"patterns": patterns}


@router.get("/benchmarks")
async def get_benchmarks(niche: Optional[str] = Query(None, description="Filter by niche")):
    """Get engagement benchmarks from VBL corpus (avg likes, viral thresholds)."""
    from vbl_integration import get_engagement_benchmarks
    benchmarks = get_engagement_benchmarks(niche)
    return {"benchmarks": benchmarks}


@router.post("/ideas")
async def generate_ideas_from_vbl(
    niche: str = Query(..., description="Niche to generate ideas for"),
    count: int = Query(3, ge=1, le=10, description="Number of ideas"),
):
    """Generate viral content ideas using VBL corpus insights."""
    from vbl_integration import generate_vbl_brief
    ideas = generate_vbl_brief(niche, count)
    if not ideas:
        raise HTTPException(
            status_code=404, 
            detail=f"No VBL data found for niche '{niche}'. Available niches: comedy, dance, pets, food, fitness, education, lifestyle, brand, magic/vfx, music, vfx"
        )
    return {"niche": niche, "count": len(ideas), "ideas": ideas}


@router.get("/summary")
async def vbl_summary():
    """Get high-level summary of all VBL patterns for dashboard."""
    from vbl_integration import get_vbl_patterns_summary
    summary = get_vbl_patterns_summary()
    return summary


@router.get("/niches")
async def list_niches():
    """List all available niches with post counts from VBL corpus."""
    from vbl_integration import CREATOR_TO_NICHE
    from pathlib import Path
    import sqlite3
    
    db_path = Path.home() / "viral-bench-local" / "data" / "corpus.db"
    conn = sqlite3.connect(str(db_path))
    
    # Count total posts and analyzed posts per niche
    rows = conn.execute("SELECT creator_handle, COUNT(*) as total, SUM(CASE WHEN vlm_analysis IS NOT NULL AND vlm_analysis != '' THEN 1 ELSE 0 END) as analyzed FROM posts GROUP BY creator_handle").fetchall()
    conn.close()
    
    # Aggregate by niche (strip @ prefix if present in DB)
    niche_stats = {}
    for creator, total, analyzed in rows:
        clean = creator.lstrip('@')
        niche = CREATOR_TO_NICHE.get(clean) or CREATOR_TO_NICHE.get(creator)
        if niche:
            if niche not in niche_stats:
                niche_stats[niche] = {"post_count": 0, "analyzed_count": 0}
            niche_stats[niche]["post_count"] += total
            niche_stats[niche]["analyzed_count"] += analyzed or 0
    
    # All 11 niches
    all_niches = [
        "brand", "comedy", "dance", "education", "fitness",
        "food", "lifestyle", "magic/vfx", "music", "pets", "vfx"
    ]
    
    result = []
    for name in all_niches:
        stats = niche_stats.get(name, {"post_count": 0, "analyzed_count": 0})
        result.append({
            "name": name,
            "post_count": stats["post_count"],
            "analyzed_count": stats["analyzed_count"],
        })
    
    return {"niches": result}


@router.get("/posts")
async def list_posts(
    niche: Optional[str] = Query(None, description="Filter by niche"),
    limit: int = Query(100, ge=1, le=500, description="Max posts to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """List all posts with analysis status."""
    from vbl_integration import CREATOR_TO_NICHE
    from pathlib import Path
    import sqlite3
    
    db_path = Path.home() / "viral-bench-local" / "data" / "corpus.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    
    # Build query
    if niche:
        # Filter by creator handles in this niche
        creators = [f"'{c}'" for c, n in CREATOR_TO_NICHE.items() if n == niche]
        creator_filter = f"AND creator_handle IN ({','.join(creators)})"
    else:
        creator_filter = ""
    
    query = f"""
        SELECT 
            id,
            creator_handle,
            caption,
            likes,
            views,
            engagement_rate,
            video_path,
            vlm_analysis,
            published_at
        FROM posts
        WHERE 1=1 {creator_filter}
        ORDER BY likes DESC
        LIMIT ? OFFSET ?
    """
    
    rows = conn.execute(query, (limit, offset)).fetchall()
    conn.close()
    
    # Format response
    posts = []
    for row in rows:
        has_video = row['video_path'] is not None and row['video_path'] != ''
        has_analysis = row['vlm_analysis'] is not None and row['vlm_analysis'] != ''
        
        analysis_data = None
        if has_analysis:
            try:
                import json
                analysis_data = json.loads(row['vlm_analysis'])
            except:
                analysis_data = None
        
        posts.append({
            "id": row['id'],
            "creator": row['creator_handle'],
            "caption": row['caption'],
            "likes": row['likes'],
            "views": row['views'],
            "engagement_rate": row['engagement_rate'],
            "has_video": has_video,
            "has_analysis": has_analysis,
            "analysis": analysis_data,
            "published_at": row['published_at'],
        })
    
    return {"posts": posts, "limit": limit, "offset": offset}


@router.get("/insights")
async def get_insights(
    dimension: str = Query("all", description="Pattern dimension: hook, format, pacing, energy, audio, or all"),
    niche: Optional[str] = Query(None, description="Filter by niche"),
    min_n: int = Query(3, ge=1, description="Minimum sample size"),
    top_k: int = Query(10, ge=1, le=50, description="Top patterns to return"),
    tier_analysis: bool = Query(True, description="Include top 10% vs bottom 50% breakdown"),
):
    """Proxy to research service: get pattern insights from VBL corpus with statistical analysis."""
    params = {
        "dimension": dimension,
        "min_n": min_n,
        "top_k": top_k,
        "tier_analysis": str(tier_analysis).lower(),
    }
    if niche:
        params["niche"] = niche
    
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(f"{RESEARCH_SERVICE_URL}/v1/insights", params=params)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="Research service unavailable")


@router.post("/chat")
async def research_chat(request: Request):
    """LLM-powered Q&A grounded in VBL corpus with optional niche filtering."""
    body = await request.json()
    payload = {
        "question": body.get("question", ""),
        "niche": body.get("niche"),
    }
    if body.get("conversation_id"):
        payload["conversation_id"] = body["conversation_id"]
    
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            resp = await client.post(f"{RESEARCH_SERVICE_URL}/v1/chat", json=payload)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="Research service unavailable")
