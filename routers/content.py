"""Content generation endpoints — repurposing, ideas, carousels, scoring."""
from fastapi import APIRouter, HTTPException, Query

from database import get_connection

router = APIRouter(tags=["content"])


# ─── Repurposing ──────────────────────────────────────────────────────────────

@router.post("/repurpose")
async def repurpose_outlier(
    post_id: str = Query(None),
    title: str = Query(None),
):
    """
    Generate a repurposing prompt from an outlier post.
    Returns a multi-format content generation prompt ready for the chat engine.
    """
    conn = get_connection()

    post = None
    if post_id:
        row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if row:
            post = dict(row)

    if not post and title:
        row = conn.execute(
            "SELECT * FROM posts WHERE title LIKE ? ORDER BY z_score DESC LIMIT 1",
            (f"%{title}%",),
        ).fetchone()
        if row:
            post = dict(row)

    conn.close()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Build the repurposing prompt
    context = f"""ORIGINAL POST (viral outlier, z-score: {post['z_score']:.1f}):
Title: {post['title']}
Platform: {post['platform']}/{post['subreddit']}
Score: {post['score']} upvotes, {post['comment_count']} comments
URL: {post['url']}"""

    if post.get("content") and len(post["content"]) > 10:
        context += f"\nContent: {post['content'][:500]}"

    repurpose_prompt = f"""{context}

---

REPURPOSE this viral post into 5 content formats. For each format, write the FULL ready-to-publish content (not a description of what to write):

## 1. \U0001f9f5 Twitter/X Thread (6-8 posts)
Hook with the most shocking angle. One idea per tweet. Use em dashes. End with a CTA.

## 2. \U0001f4bc LinkedIn Post (200-300 words)
Professional angle. Lead with a contrarian take. Use line breaks for readability. End with a question.

## 3. \U0001f4e7 Newsletter Section (400-500 words)
Deep dive format. Context \u2192 Insight \u2192 Application. Include data points. Personal voice.

## 4. \U0001f3ac TikTok/Reel Script (60 seconds)
Hook in first 3 seconds. Pattern interrupt at 15s. 3 key points. CTA at end.

## 5. \U0001f4f8 Instagram Carousel (8 slides)
Slide 1: Hook title. Slides 2-7: One insight per slide with supporting text. Slide 8: CTA + handle.

Write each piece as if it's going live TODAY. Bold voice, sharp insights, no fluff."""

    return {
        "post": post,
        "prompt": repurpose_prompt,
        "formats": ["twitter_thread", "linkedin_post", "newsletter", "tiktok_script", "ig_carousel"],
    }


@router.post("/repurpose/ai")
async def repurpose_ai(
    post_id: str = Query(None, description="Post ID to repurpose"),
    title: str = Query(None, description="Search by title instead"),
    voice: str = Query("straughterg", description="Voice profile name"),
    formats: str = Query(None, description="Comma-separated formats (default: all 5)"),
):
    """
    AI-powered multi-format repurposing: generates actual ready-to-publish content
    for Twitter thread, LinkedIn, Newsletter, TikTok script, and IG carousel.
    """
    from repurpose_engine import repurpose_post

    conn = get_connection()
    post = None
    if post_id:
        row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if row:
            post = dict(row)
    elif title:
        row = conn.execute(
            "SELECT * FROM posts WHERE title LIKE ? ORDER BY z_score DESC LIMIT 1",
            (f"%{title}%",),
        ).fetchone()
        if row:
            post = dict(row)
    conn.close()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    fmt_list = None
    if formats:
        fmt_list = [f.strip() for f in formats.split(",")]

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
    """
    Score a post's content quality using LLM.
    Returns scores for hook strength, value density, shareability.
    """
    import json as _json
    from idea_generation import _get_client

    conn = get_connection()
    post = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    conn.close()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    post_dict = dict(post)
    client, model = _get_client()

    prompt = f"""Score this post's content quality on 5 dimensions (1-10 scale each).

Post: "{post_dict.get('title', '')}"
Content: {post_dict.get('content', 'N/A')[:500]}
Platform: {post_dict.get('platform')} | Score: {post_dict.get('score', 0)} | Comments: {post_dict.get('comment_count', 0)}

Respond in JSON only:
{{
  "hook_strength": 8,
  "value_density": 7,
  "shareability": 9,
  "originality": 6,
  "emotional_impact": 8,
  "overall": 7.6,
  "one_liner": "Brief verdict on why this post works or doesn't"
}}"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=400,
        )
        content = response.choices[0].message.content.strip()
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        scores = _json.loads(content)
        return {"post_id": post_id, "title": post_dict.get("title"), "scores": scores}
    except Exception as e:
        return {"post_id": post_id, "error": str(e)}
