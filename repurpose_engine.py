"""
SGOS Backend - Multi-Format Content Repurposing Engine
Takes a viral outlier post → generates 5 ready-to-publish formats via Qwen LLM.
Formats: Twitter thread, LinkedIn post, Newsletter, TikTok script, IG carousel.
"""
import json
from idea_generation import _get_client
from voice_profile import get_voice_profile, generate_voice_prompt


def repurpose_post(
    post: dict,
    voice_name: str = "straughterg",
    formats: list[str] = None,
) -> dict:
    """
    Generate multi-format repurposed content from a single post.
    Actually calls the LLM and returns ready-to-publish content.

    Args:
        post: Dict with title, content, platform, score, z_score, url
        voice_name: Voice profile to use for generation
        formats: List of format keys to generate (default: all 5)

    Returns:
        Dict with generated content for each format
    """
    client, model = _get_client()
    if not client:
        return {
            "status": "error",
            "error": "No LLM client available. Check API keys.",
        }

    if formats is None:
        formats = ["twitter_thread", "linkedin_post", "newsletter", "tiktok_script", "ig_carousel"]

    # Get voice profile
    voice_text = ""
    try:
        voice = get_voice_profile(voice_name)
        if voice:
            voice_text = generate_voice_prompt(voice)
    except Exception:
        pass

    # Build context from the source post
    source_context = _build_source_context(post)

    # Build the master prompt
    system_prompt = f"""You are an elite content strategist and ghostwriter. You take viral content and repurpose it across multiple platforms.

{'VOICE PROFILE — write in this style:' if voice_text else ''}
{voice_text if voice_text else 'Write in a direct, bold, no-BS voice. Short paragraphs. Sharp hooks. Data-backed insights.'}

RULES:
- Write FULL ready-to-publish content, not descriptions of what to write
- Each format must stand alone — don't reference "the original post"
- Be specific: use real data points, examples, and insights from the source
- Adapt tone for each platform (casual on Twitter, professional on LinkedIn, etc.)
"""

    user_prompt = f"""{source_context}

Generate ALL of the following formats. Write the FULL content for each:

## 1. 🧵 TWITTER/X THREAD (6-8 tweets)
Format each tweet as "1/N", "2/N", etc.
Hook with the most shocking angle in tweet 1. One idea per tweet. Use em dashes. End with a CTA.

## 2. 💼 LINKEDIN POST (200-300 words)
Professional angle. Lead with a contrarian take. Use line breaks for readability. End with a thought-provoking question.

## 3. 📧 NEWSLETTER SECTION (400-500 words)
Deep dive format. Structure: Context → Key Insight → Why It Matters → What To Do About It. Include data points.

## 4. 🎬 TIKTOK/REEL SCRIPT (60 seconds)
[HOOK — first 3 seconds, pattern interrupt]
[SETUP — 10 seconds]
[POINT 1 — 15 seconds]
[POINT 2 — 15 seconds]
[POINT 3 — 10 seconds]
[CTA — 7 seconds]
Write the exact words to say. Mark timing.

## 5. 📸 INSTAGRAM CAROUSEL (8 slides)
Slide 1: Bold hook title (under 10 words)
Slides 2-7: One insight per slide (title + 1-2 sentences)
Slide 8: CTA + "Follow for more"
"""

    import time

    max_retries = 3
    last_error = None

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.8,
                max_tokens=6000,
                timeout=60,
            )

            raw_output = response.choices[0].message.content

            if not raw_output or len(raw_output) < 100:
                raise ValueError(f"LLM returned empty/short response ({len(raw_output or '')} chars)")

            # Parse the output into structured formats
            parsed = _parse_formats(raw_output, formats)

            return {
                "status": "generated",
                "source_post": {
                    "id": post.get("id", ""),
                    "title": post.get("title", ""),
                    "platform": post.get("platform", ""),
                    "z_score": post.get("z_score", 0),
                    "score": post.get("score", 0),
                },
                "voice_profile": voice_name,
                "formats": parsed,
                "raw_output": raw_output,
            }

        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s backoff
                continue
            return {
                "status": "error",
                "error": f"LLM call failed after {max_retries} attempts: {last_error}",
                "hint": "Aliyun may be temporarily overloaded. Try again in 30s.",
            }


def _build_source_context(post: dict) -> str:
    """Build context string from the source post."""
    lines = [
        f"SOURCE POST (viral outlier):",
        f"Title: {post.get('title', 'Untitled')}",
        f"Platform: {post.get('platform', 'unknown')}",
        f"Score: {post.get('score', 0)} | Comments: {post.get('comment_count', 0)} | Z-Score: {post.get('z_score', 0):.1f}",
        f"URL: {post.get('url', '')}",
    ]

    content = post.get("content", "")
    if content and len(content) > 10:
        lines.append(f"\nFull Content:\n{content[:2000]}")

    return "\n".join(lines)


def _parse_formats(raw: str, requested_formats: list[str]) -> dict:
    """Parse the LLM output into structured format blocks."""
    import re

    formats = {}

    # Split by bold headers like **1. 🧵 TWITTER** or ## 1. 🧵 TWITTER
    # The LLM uses either format
    sections = re.split(r'(?:\*\*\d+\.|##\s*\d+\.?\s*)', raw)

    format_keys = {
        "twitter": "twitter_thread",
        "linkedin": "linkedin_post",
        "newsletter": "newsletter",
        "tiktok": "tiktok_script",
        "reel": "tiktok_script",
        "instagram": "ig_carousel",
        "carousel": "ig_carousel",
    }

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Remove trailing ** if present
        section = section.rstrip("*").strip()

        # Detect which format this section is
        lower = section.lower()
        detected_key = None
        for keyword, key in format_keys.items():
            if keyword in lower[:100]:
                detected_key = key
                break

        if detected_key and detected_key in requested_formats:
            # Remove the format title line (first line with the emoji/name)
            lines = section.split("\n")
            # Skip the first line (title) and any trailing **
            content_lines = []
            skip_title = True
            for line in lines:
                if skip_title and (
                    line.strip().startswith("*") or
                    line.strip().startswith("#") or
                    any(emoji in line for emoji in ["🧵", "💼", "📧", "🎬", "📸"])
                ):
                    continue
                skip_title = False
                content_lines.append(line.rstrip("*"))
            content = "\n".join(content_lines).strip()
            if content:
                formats[detected_key] = content

    # For any missing formats, use the raw output section
    for fmt in requested_formats:
        if fmt not in formats:
            formats[fmt] = f"[Format: {fmt}]\n(Could not parse from LLM output — see raw_output for full text)"

    return formats
