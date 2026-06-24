"""Content generation endpoints — repurposing, ideas, carousels, scoring.
Thin router — business logic delegated to ContentService + domain modules."""
from fastapi import APIRouter, HTTPException, Query

from services.content import content_service

router = APIRouter(tags=["content"])


# ─── Repurposing ──────────────────────────────────────────────────────────────

@router.post("/repurpose")
async def repurpose_outlier(
    post_id: str = Query(None),
    title: str = Query(None),
):
    """Generate a repurposing prompt from an outlier post."""
    post = content_service.find_post(post_id=post_id, title=title)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return content_service.build_repurpose_prompt(post)


@router.post("/repurpose/ai")
async def repurpose_ai(
    post_id: str = Query(None, description="Post ID to repurpose"),
    title: str = Query(None, description="Search by title instead"),
    voice: str = Query("straughterg", description="Voice profile name"),
    formats: str = Query(None, description="Comma-separated formats (default: all 5)"),
):
    """AI-powered multi-format repurposing."""
    from repurpose_engine import repurpose_post

    post = content_service.find_post(post_id=post_id, title=title)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    fmt_list = [f.strip() for f in formats.split(",")] if formats else None
    return repurpose_post(post, voice_name=voice, formats=fmt_list)


# ─── Ideas ────────────────────────────────────────────────────────────────────

@router.post("/ideas/generate")
async def generate_ideas_endpoint(
    topic: str = Query(None, description="Topic filter"),
    voice: str = Query(None, description="Voice profile name"),
    hours: int = Query(48, description="Look back N hours for outliers"),
    count: int = Query(5, description="Number of ideas to generate"),
    platform: str = Query(None, description="Platform filter"),
    save: bool = Query(True, description="Save ideas to database"),
):
    """Generate AI content ideas from trending outliers + voice profile."""
    from idea_generation import generate_ideas, save_ideas

    result = generate_ideas(
        topic=topic, voice_name=voice, hours=hours, count=count, platform=platform
    )
    if save and result.get("status") == "generated":
        saved = save_ideas(result, topic=topic)
        result["saved"] = saved
    return result


@router.get("/ideas")
async def list_ideas(
    limit: int = Query(20, description="Max results"),
    status: str = Query("draft", description="Filter by status"),
    topic: str = Query(None, description="Topic filter"),
):
    """List saved ideas from database."""
    from idea_generation import get_saved_ideas
    ideas = get_saved_ideas(limit=limit, status=status, topic=topic)
    return {"count": len(ideas), "ideas": ideas}


# ─── Carousels ────────────────────────────────────────────────────────────────

@router.post("/carousel/generate")
async def generate_carousel_endpoint(
    topic: str = Query(..., description="Topic for the carousel"),
    slide_count: int = Query(8, description="Number of slides (6-12)"),
    handle: str = Query("@StraughterG", description="Instagram handle for CTA"),
    color_scheme: str = Query("dark", description="Color scheme: dark, midnight, warm, neon"),
):
    """Generate an Instagram carousel with HTML slides."""
    from carousel_gen import generate_carousel
    content = generate_carousel(
        topic=topic,
        slide_count=min(max(slide_count, 4), 12),
        handle=handle,
        color_scheme=color_scheme,
    )
    content.pop("file_paths", None)
    return content


@router.post("/carousel/generate/ai")
async def generate_carousel_ai_endpoint(
    topic: str = Query(..., description="Topic for the carousel"),
    slide_count: int = Query(8, description="Number of slides (6-12)"),
    handle: str = Query("@StraughterG", description="Instagram handle for CTA"),
    color_scheme: str = Query("dark", description="Color scheme: dark, midnight, warm, neon"),
    voice: str = Query(None, description="Voice profile name for style"),
):
    """Generate carousel with LLM-written slide copy."""
    from carousel_gen import generate_carousel_with_llm
    content = generate_carousel_with_llm(
        topic=topic,
        slide_count=min(max(slide_count, 4), 12),
        handle=handle,
        color_scheme=color_scheme,
        voice_name=voice,
    )
    content.pop("file_paths", None)
    return content


@router.get("/carousel/schemes")
async def list_color_schemes():
    """List available color schemes for carousels."""
    from carousel_gen import COLOR_SCHEMES
    return {scheme: colors for scheme, colors in COLOR_SCHEMES.items()}


@router.post("/carousel/render")
async def render_single_slide(
    headline: str = Query(...),
    body: str = Query(""),
    slide_number: int = Query(1),
    total_slides: int = Query(8),
    handle: str = Query("@StraughterG"),
    color_scheme: str = Query("dark"),
    slide_type: str = Query("insight"),
):
    """Render a single slide as HTML (for preview)."""
    from carousel_gen import render_slide_html
    slide = {"number": slide_number, "type": slide_type, "headline": headline, "body": body}
    html = render_slide_html(slide, total_slides, handle, color_scheme)
    return {"html": html, "slide": slide}


# ─── Analytics Scoring ────────────────────────────────────────────────────────

@router.post("/analytics/score/{post_id}")
async def score_post(post_id: str):
    """Score a post's content quality using LLM."""
    result = content_service.score_post(post_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
