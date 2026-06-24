"""
Instagram Carousel Generator — generates slide content + HTML/CSS slides.
Creates 8-10 slide carousels from a topic, post, or idea.
Outputs: structured slide content + renderable HTML for each slide.
"""
import json
import os
import re
from datetime import datetime

CAROUSEL_STRUCTURE = """
Given a TOPIC, generate an Instagram carousel with exactly {slide_count} slides.

RULES:
- Slide 1: HOOK — bold, scroll-stopping headline. Under 10 words.
- Slides 2-{penultimate}: One insight per slide. Bold header (3-5 words) + 2-3 lines of explanation.
- Slide {last}: CTA — tell them what to do next. Include handle.

FORMAT: Return JSON with this exact structure:
{{
  "topic": "original topic",
  "hook": "the hook text",
  "slides": [
    {{"number": 1, "type": "hook", "headline": "...", "body": ""}},
    {{"number": 2, "type": "insight", "headline": "...", "body": "..."}},
    ...
    {{"number": N, "type": "cta", "headline": "...", "body": "..."}}
  ],
  "handle": "@StraughterG",
  "color_scheme": "dark"
}}
"""

HTML_SLIDE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1080px;
    height: 1080px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: {bg_color};
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    overflow: hidden;
  }}
  .slide {{
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 80px;
    text-align: center;
    position: relative;
  }}
  .slide-number {{
    position: absolute;
    top: 40px;
    right: 50px;
    font-size: 16px;
    color: {muted_color};
    font-weight: 600;
    letter-spacing: 2px;
  }}
  .headline {{
    font-size: {headline_size}px;
    font-weight: 800;
    color: {headline_color};
    line-height: 1.2;
    margin-bottom: 30px;
    max-width: 900px;
    letter-spacing: -1px;
  }}
  .body {{
    font-size: {body_size}px;
    color: {body_color};
    line-height: 1.6;
    max-width: 800px;
  }}
  .handle {{
    position: absolute;
    bottom: 50px;
    font-size: 18px;
    color: {muted_color};
    font-weight: 600;
    letter-spacing: 1px;
  }}
  .accent-line {{
    width: 60px;
    height: 4px;
    background: {accent_color};
    border-radius: 2px;
    margin-bottom: 40px;
  }}
</style>
</head>
<body>
  <div class="slide">
    <div class="slide-number">{slide_num}/{total}</div>
    {accent_html}
    <div class="headline">{headline}</div>
    <div class="body">{body}</div>
    <div class="handle">{handle}</div>
  </div>
</body>
</html>"""

# Color schemes
COLOR_SCHEMES = {
    "dark": {
        "bg_color": "#0a0a0a",
        "headline_color": "#ffffff",
        "body_color": "#a0a0a0",
        "muted_color": "#555555",
        "accent_color": "#00ff88",
    },
    "midnight": {
        "bg_color": "#0f172a",
        "headline_color": "#f1f5f9",
        "body_color": "#94a3b8",
        "muted_color": "#475569",
        "accent_color": "#38bdf8",
    },
    "warm": {
        "bg_color": "#1c1917",
        "headline_color": "#fafaf9",
        "body_color": "#a8a29e",
        "muted_color": "#57534e",
        "accent_color": "#f97316",
    },
    "neon": {
        "bg_color": "#09090b",
        "headline_color": "#fafafa",
        "body_color": "#a1a1aa",
        "muted_color": "#52525b",
        "accent_color": "#a855f7",
    },
}


def generate_carousel_content(topic: str, slide_count: int = 8, handle: str = "@StraughterG") -> dict:
    """
    Generate carousel slide content using structured templates.
    Returns a dict with slides ready to render.
    """
    # Generate content based on topic analysis
    # This is a rule-based generator — for LLM-enhanced version, use /carousel/generate endpoint
    
    # Split topic into key insights
    words = topic.split()
    
    slides = []
    
    # Slide 1: Hook
    slides.append({
        "number": 1,
        "type": "hook",
        "headline": topic[:80] if len(topic) <= 80 else topic[:77] + "...",
        "body": "",
    })
    
    # Slides 2 to N-1: Insights (placeholder structure — filled by LLM or manual)
    insight_templates = [
        "The Problem",
        "Why It Matters",
        "The Data",
        "The Insight",
        "The Framework",
        "The Example",
        "The Takeaway",
        "What To Do",
    ]
    
    for i in range(2, slide_count):
        idx = i - 2
        slides.append({
            "number": i,
            "type": "insight",
            "headline": insight_templates[idx % len(insight_templates)],
            "body": f"Key point {i - 1} about: {topic[:60]}",
        })
    
    # Last slide: CTA
    slides.append({
        "number": slide_count,
        "type": "cta",
        "headline": "Follow for more",
        "body": f"Save this post. Share with someone who needs it.\n{handle}",
    })
    
    return {
        "topic": topic,
        "handle": handle,
        "slide_count": slide_count,
        "slides": slides,
        "color_scheme": "dark",
        "generated_at": datetime.now().isoformat(),
    }


def render_slide_html(slide: dict, total_slides: int, handle: str = "@StraughterG", 
                      color_scheme: str = "dark") -> str:
    """Render a single slide as a 1080x1080 HTML file."""
    scheme = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["dark"])
    
    slide_type = slide.get("type", "insight")
    
    # Adjust sizing based on slide type
    if slide_type == "hook":
        headline_size = 64
        body_size = 28
        accent_html = '<div class="accent-line"></div>'
    elif slide_type == "cta":
        headline_size = 52
        body_size = 26
        accent_html = '<div class="accent-line"></div>'
    else:
        headline_size = 48
        body_size = 24
        accent_html = '<div class="accent-line"></div>'
    
    html = HTML_SLIDE_TEMPLATE.format(
        bg_color=scheme["bg_color"],
        headline_color=scheme["headline_color"],
        body_color=scheme["body_color"],
        muted_color=scheme["muted_color"],
        accent_color=scheme["accent_color"],
        headline_size=headline_size,
        body_size=body_size,
        slide_num=slide["number"],
        total=total_slides,
        headline=slide.get("headline", ""),
        body=slide.get("body", "").replace("\n", "<br>"),
        handle=handle,
        accent_html=accent_html,
    )
    
    return html


def generate_carousel(topic: str, slide_count: int = 8, handle: str = "@StraughterG",
                      color_scheme: str = "dark", output_dir: str = None) -> dict:
    """
    Full carousel generation: content + HTML slides.
    
    Returns:
        Dict with content structure + list of HTML file paths (if output_dir given).
    """
    content = generate_carousel_content(topic, slide_count, handle)
    
    html_slides = []
    file_paths = []
    
    for slide in content["slides"]:
        html = render_slide_html(slide, slide_count, handle, color_scheme)
        html_slides.append(html)
        
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            safe_topic = re.sub(r'[^a-z0-9]', '_', topic[:30].lower())
            filename = f"slide_{slide['number']:02d}_{safe_topic}.html"
            filepath = os.path.join(output_dir, filename)
            with open(filepath, "w") as f:
                f.write(html)
            file_paths.append(filepath)
    
    content["html_slides"] = html_slides
    content["file_paths"] = file_paths
    
    return content


# ─── LLM-Enhanced Carousel Generation ────────────────────────────────────────

def generate_carousel_with_llm(
    topic: str,
    slide_count: int = 8,
    handle: str = "@StraughterG",
    color_scheme: str = "dark",
    voice_name: str = None,
    output_dir: str = None,
) -> dict:
    """
    Generate carousel with real LLM-written slide copy.
    Uses the same LLM client as idea_generation.py.
    
    Returns:
        Dict with LLM-written slides + rendered HTML.
    """
    from idea_generation import _get_client
    
    client, model = _get_client()
    
    if not client:
        # Fallback to template-based generation
        print("⚠️ No LLM available, falling back to template-based generation")
        return generate_carousel(topic, slide_count, handle, color_scheme, output_dir)
    
    # Load voice profile if available
    voice_context = ""
    if voice_name:
        try:
            from database import get_connection
            conn = get_connection()
            ref = conn.execute(
                "SELECT content FROM voice_references WHERE profile_name=? AND source='skill_md' ORDER BY created_at DESC LIMIT 1",
                (voice_name,)
            ).fetchone()
            conn.close()
            if ref:
                voice_context = f"\n\nVOICE PROFILE (write in this style):\n{ref['content'][:3000]}"
        except Exception:
            pass
    
    system_prompt = f"""You are an elite Instagram carousel creator for tech/AI/creator content.

You write scroll-stopping carousels that feel personal, specific, and valuable.
Every slide must earn the next swipe.

RULES:
- Slide 1 (HOOK): Bold, curiosity-gap headline. Under 10 words. No body text.
- Slides 2-{slide_count - 1} (INSIGHT): One idea per slide. Bold 3-5 word header + 2-3 lines of specific, actionable explanation.
- Slide {slide_count} (CTA): Clear next step + handle.
{voice_context}

STYLE:
- Conversational but sharp
- Specific > generic (numbers, examples, names)
- Cut the corporate speak
- Each slide should be 30-60 words max"""

    user_prompt = f"""Generate a {slide_count}-slide Instagram carousel about: "{topic}"

Handle for CTA: {handle}

Return JSON with this exact structure:
{{
  "topic": "{topic}",
  "slides": [
    {{"number": 1, "type": "hook", "headline": "...", "body": ""}},
    {{"number": 2, "type": "insight", "headline": "...", "body": "..."}},
    ...
    {{"number": {slide_count}, "type": "cta", "headline": "...", "body": "..."}}
  ]
}}

Write real, compelling copy — not placeholders."""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,
            max_tokens=2500,
        )
        
        content_text = response.choices[0].message.content
        
        # Parse JSON response
        try:
            data = json.loads(content_text)
            slides = data.get("slides", [])
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', content_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
                slides = data.get("slides", [])
            else:
                # Last resort: extract array
                json_match = re.search(r'\[.*\]', content_text, re.DOTALL)
                if json_match:
                    slides = json.loads(json_match.group())
                else:
                    raise ValueError("Could not parse LLM response as JSON")
        
        # Validate and clean slides
        validated_slides = []
        for i, slide in enumerate(slides, 1):
            validated_slides.append({
                "number": i,
                "type": slide.get("type", "insight" if 1 < i < slide_count else ("hook" if i == 1 else "cta")),
                "headline": slide.get("headline", f"Slide {i}"),
                "body": slide.get("body", ""),
            })
        
        # Ensure we have the right number of slides
        while len(validated_slides) < slide_count:
            validated_slides.append({
                "number": len(validated_slides) + 1,
                "type": "insight",
                "headline": "Continue",
                "body": "More insights here...",
            })
        validated_slides = validated_slides[:slide_count]
        
        # Render HTML
        html_slides = []
        file_paths = []
        
        for slide in validated_slides:
            html = render_slide_html(slide, slide_count, handle, color_scheme)
            html_slides.append(html)
            
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
                safe_topic = re.sub(r'[^a-z0-9]', '_', topic[:30].lower())
                filename = f"slide_{slide['number']:02d}_{safe_topic}.html"
                filepath = os.path.join(output_dir, filename)
                with open(filepath, "w") as f:
                    f.write(html)
                file_paths.append(filepath)
        
        return {
            "topic": topic,
            "handle": handle,
            "slide_count": slide_count,
            "slides": validated_slides,
            "html_slides": html_slides,
            "file_paths": file_paths,
            "color_scheme": color_scheme,
            "generated_at": datetime.now().isoformat(),
            "llm_model": model,
            "generation_method": "llm",
        }
    
    except Exception as e:
        print(f"❌ LLM generation failed: {e}")
        # Fallback to template
        result = generate_carousel(topic, slide_count, handle, color_scheme, output_dir)
        result["generation_method"] = "template_fallback"
        result["llm_error"] = str(e)
        return result


# ─── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    
    topic = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "5 things AI creators need to know in 2025"
    slide_count = 8
    handle = "@StraughterG"
    color_scheme = "dark"
    
    print(f"🎨 Generating carousel: '{topic}'")
    result = generate_carousel(topic, slide_count, handle, color_scheme, output_dir="/tmp/sgos_carousels")
    
    print(f"\n📊 Carousel generated: {len(result['slides'])} slides")
    for slide in result["slides"]:
        print(f"  Slide {slide['number']} ({slide['type']}): {slide['headline']}")
    
    if result.get("file_paths"):
        print(f"\n📁 HTML files saved to: {result['file_paths'][0].rsplit('/', 1)[0]}")
        for fp in result["file_paths"]:
            print(f"  → {fp}")
