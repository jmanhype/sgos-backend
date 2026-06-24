"""
SGOS Backend - AI Idea Generation Engine
Takes trending outliers + voice profile -> generates execution-ready content ideas.

This is the core differentiator: research -> ideas in YOUR voice.
Uses Perplexity API (OpenAI-compatible) for research-aware generation.
"""
import os
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

from database import get_outliers, get_trending_topics, get_stats, get_connection
from voice_profile import get_voice_profile, generate_voice_prompt, init_voice_tables

DB_PATH = Path(__file__).parent / "sgos.db"

# ─── LLM Configuration ────────────────────────────────────────────────────────

def _get_client():
    """Get OpenAI-compatible client. Priority: env vars > Aliyun (from Hermes config)."""
    if not HAS_OPENAI:
        return None, None
    
    # Check env vars first
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    custom_url = os.environ.get("LLM_BASE_URL")
    custom_key = os.environ.get("LLM_API_KEY")
    
    # Try Perplexity (check if it works — may be out of quota)
    if perplexity_key:
        try:
            client = OpenAI(api_key=perplexity_key, base_url="https://api.perplexity.ai", max_retries=5, timeout=60)
            # Quick check — if quota exceeded, fall through
            return client, "sonar"
        except Exception:
            pass
    
    if openai_key:
        return OpenAI(api_key=openai_key, max_retries=5, timeout=60), "gpt-4o-mini"

    if custom_url and custom_key:
        return OpenAI(api_key=custom_key, base_url=custom_url, max_retries=5, timeout=60), os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    # Fallback: Aliyun (from Hermes config)
    aliyun_url = "https://llm-k189xkia71r72n1w.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    aliyun_key = os.environ.get("ALIYUN_API_KEY", "")
    if not aliyun_key:
        # Try to read from Hermes config
        try:
            import yaml
            config_path = os.path.expanduser("~/.hermes/config.yaml")
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                # Get from providers section
                providers = config.get("providers", {})
                for name, p in providers.items():
                    if "aliyun" in name.lower() or p.get("base_url", "").find("aliyun") >= 0:
                        aliyun_key = p.get("api_key", "")
                        aliyun_url = p.get("base_url", aliyun_url)
                        break
                # Also check top-level
                if not aliyun_key and config.get("api_key"):
                    aliyun_key = config["api_key"]
                    aliyun_url = config.get("base_url", aliyun_url)
        except Exception:
            pass
    
    if aliyun_key:
        return OpenAI(api_key=aliyun_key, base_url=aliyun_url, max_retries=5, timeout=60), "qwen-plus"
    
    return None, None


# ─── Idea Generation ──────────────────────────────────────────────────────────

def generate_ideas(
    topic: str = None,
    voice_name: str = None,
    hours: int = 48,
    count: int = 5,
    platform: str = None,
) -> dict:
    """
    Generate content ideas from trending outliers + voice profile.
    
    Args:
        topic: Optional topic filter (if None, uses all outliers)
        voice_name: Voice profile name to use (if None, uses default style)
        hours: How far back to look for outliers
        count: Number of ideas to generate
        platform: Filter outliers by platform
    
    Returns:
        Dict with ideas, sources, and metadata
    """
    client, model = _get_client()
    
    # Gather context: outliers + trends
    outliers = get_outliers(platform=platform, hours=hours, limit=10)
    trends = get_trending_topics(platform=platform, days=7, limit=10)
    stats = get_stats()
    
    # Load voice profile if available
    voice_prompt = ""
    if voice_name:
        init_voice_tables()
        profile = get_voice_profile(voice_name)
        if profile:
            voice_prompt = generate_voice_prompt(profile)
        
        # Also load the full voice reference (SKILL.md) if available — much richer than statistical profile
        try:
            conn = get_connection()
            ref = conn.execute(
                "SELECT content FROM voice_references WHERE profile_name=? AND source='skill_md' ORDER BY created_at DESC LIMIT 1",
                (voice_name,)
            ).fetchone()
            conn.close()
            if ref:
                # The full skill content IS the voice system prompt — much better than statistical summary
                voice_prompt = f"## VOICE PROFILE (use this style for ALL output):\n\n{ref['content'][:6000]}"
        except Exception:
            pass  # voice_references table may not exist yet
    
    # Build context from outliers
    outlier_context = []
    for i, post in enumerate(outliers, 1):
        outlier_context.append(
            f"{i}. [{post['platform']}] \"{post['title']}\"\n"
            f"   Score: {post['score']} | Comments: {post['comment_count']} | "
            f"z-score: {post.get('z_score', 0):.1f}\n"
            f"   URL: {post.get('url', 'N/A')}"
        )
    
    trend_context = ", ".join(f"{t['topic']} ({t['count']}x)" for t in trends[:8])
    
    # Topic filter
    topic_line = f"\nTOPIC FOCUS: {topic}" if topic else ""
    
    # Build the prompt
    system_prompt = f"""You are an elite content strategist for a creator who builds in public about AI, tech, and creator economy.

Your job: analyze trending content and generate execution-ready ideas that will perform well.

{voice_prompt}

Each idea must include:
- A specific hook (first line that stops the scroll)
- The angle (what makes YOUR take different from the original)
- The format (thread, single post, carousel, newsletter, video script)
- Why it works (what psychological trigger it hits)
- A content brief (3-5 bullet outline)

Be specific, not generic. Reference the actual data points from the source posts.
Ideas should feel like they could go viral — not like corporate marketing."""

    user_prompt = f"""ANALYZE these trending posts and generate {count} content ideas.

## Current Trending Outliers (last {hours}h):
{chr(10).join(outlier_context) if outlier_context else 'No outliers found yet.'}

## Trending Topics This Week:
{trend_context or 'No trend data yet.'}

## Database Stats:
- Total posts tracked: {stats['total_posts']}
- Outliers detected (24h): {stats['outliers_24h']}
{topic_line}

Generate exactly {count} ideas. For each:
1. HOOK: The opening line (under 15 words, creates curiosity gap)
2. ANGLE: What makes this take unique (1 sentence)
3. FORMAT: Best format (thread/carousel/single/newsletter/video)
4. WHY_IT_WORKS: The psychological trigger (1 sentence)
5. BRIEF: 3-5 bullet content outline

Return as JSON array with keys: hook, angle, format, why_it_works, brief (array), source_inspiration (title of the outlier that inspired this)."""

    if client:
        # Call LLM
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.8,
                max_tokens=2000,
                response_format={"type": "json_object"} if "sonar" in (model or "") else None,
            )
            
            content = response.choices[0].message.content
            
            # Parse response
            try:
                ideas = json.loads(content)
                if isinstance(ideas, dict) and "ideas" in ideas:
                    ideas = ideas["ideas"]
                elif isinstance(ideas, dict):
                    ideas = [ideas]
            except json.JSONDecodeError:
                # Try to extract JSON from response
                import re
                json_match = re.search(r'\[.*\]', content, re.DOTALL)
                if json_match:
                    ideas = json.loads(json_match.group())
                else:
                    ideas = [{"raw_response": content}]
            
            return {
                "status": "generated",
                "count": len(ideas),
                "ideas": ideas,
                "model": model,
                "voice_profile": voice_name,
                "sources_analyzed": len(outliers),
                "trends_used": [t['topic'] for t in trends[:5]],
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "model": model,
            }
    else:
        # No LLM available — return structured outliers as raw material
        return {
            "status": "no_llm",
            "message": "No LLM API key configured. Set PERPLEXITY_API_KEY or OPENAI_API_KEY.",
            "raw_material": {
                "outliers": [
                    {"title": p["title"], "score": p["score"], "z_score": p.get("z_score", 0), "url": p.get("url")}
                    for p in outliers[:count]
                ],
                "trends": [t['topic'] for t in trends[:5]],
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


# ─── Content Repurposing ──────────────────────────────────────────────────────

def repurpose_content(
    source_text: str,
    formats: list[str] = None,
    voice_name: str = None,
) -> dict:
    """
    Turn one piece of content into multiple formats.
    
    Args:
        source_text: The original content to repurpose
        formats: List of target formats (thread, linkedin, newsletter, carousel, single_post)
        voice_name: Voice profile to use
    
    Returns:
        Dict with generated variants
    """
    client, model = _get_client()
    
    if not formats:
        formats = ["thread", "linkedin", "newsletter", "carousel"]
    
    # Load voice
    voice_prompt = ""
    if voice_name:
        init_voice_tables()
        profile = get_voice_profile(voice_name)
        if profile:
            voice_prompt = generate_voice_prompt(profile)
    
    format_instructions = {
        "thread": "A Twitter/X thread (5-8 tweets, each under 280 chars, numbered)",
        "linkedin": "A LinkedIn post (professional tone, 200-300 words, with line breaks)",
        "newsletter": "A newsletter section (conversational, 300-500 words, with a takeaway)",
        "carousel": "An Instagram carousel (7-10 slides, each with 1 key point, under 20 words per slide)",
        "single_post": "A single viral post (under 280 chars, punchy, with a hook)",
        "video_script": "A 60-second video script (hook, 3 points, CTA)",
    }
    
    format_list = "\n".join(f"- {f}: {format_instructions.get(f, f)}" for f in formats)
    
    system_prompt = f"""You are a content repurposing expert.

{voice_prompt}

Given a source piece of content, rewrite it for multiple platforms while preserving the core insight and adapting the format, tone, and length for each platform's audience."""

    user_prompt = f"""Repurpose this content into {len(formats)} formats.

SOURCE CONTENT:
---
{source_text}
---

TARGET FORMATS:
{format_list}

Return JSON with keys: {', '.join(f'"{f}"' for f in formats)}
Each value should be the full text for that format."""

    if client:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=3000,
            )
            
            content = response.choices[0].message.content
            
            try:
                variants = json.loads(content)
            except json.JSONDecodeError:
                import re
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    variants = json.loads(json_match.group())
                else:
                    variants = {"raw": content}
            
            return {
                "status": "generated",
                "formats": formats,
                "variants": variants,
                "model": model,
                "voice_profile": voice_name,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            
        except Exception as e:
            return {"status": "error", "error": str(e)}
    else:
        return {
            "status": "no_llm",
            "message": "No LLM configured. Set PERPLEXITY_API_KEY or OPENAI_API_KEY.",
            "source": source_text[:200],
        }


# ─── Ideas Database (save/retrieve generated ideas) ───────────────────────────

def init_ideas_table():
    """Create ideas storage table."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook TEXT NOT NULL,
            angle TEXT,
            format TEXT,
            why_it_works TEXT,
            brief TEXT,
            source_inspiration TEXT,
            topic TEXT,
            voice_profile TEXT,
            status TEXT DEFAULT 'draft',
            created_at TEXT DEFAULT (datetime('now')),
            used_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_ideas(ideas_result: dict, topic: str = None) -> int:
    """Save generated ideas to database. Returns count saved."""
    init_ideas_table()
    if ideas_result.get("status") != "generated":
        return 0
    
    conn = get_connection()
    saved = 0
    for idea in ideas_result.get("ideas", []):
        if isinstance(idea, dict) and "hook" in idea:
            conn.execute("""
                INSERT INTO ideas (hook, angle, format, why_it_works, brief, source_inspiration, topic, voice_profile)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                idea.get("hook", ""),
                idea.get("angle", ""),
                idea.get("format", ""),
                idea.get("why_it_works", ""),
                json.dumps(idea.get("brief", [])),
                idea.get("source_inspiration", ""),
                topic,
                ideas_result.get("voice_profile"),
            ))
            saved += 1
    conn.commit()
    conn.close()
    return saved


def get_saved_ideas(limit: int = 20, status: str = "draft", topic: str = None) -> list[dict]:
    """Retrieve saved ideas from database."""
    init_ideas_table()
    conn = get_connection()
    
    query = "SELECT * FROM ideas WHERE 1=1"
    params = []
    if status:
        query += " AND status=?"
        params.append(status)
    if topic:
        query += " AND topic=?"
        params.append(topic)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    ideas = []
    for row in rows:
        idea = dict(row)
        if idea.get("brief"):
            try:
                idea["brief"] = json.loads(idea["brief"])
            except json.JSONDecodeError:
                pass
        ideas.append(idea)
    
    return ideas


if __name__ == "__main__":
    print("🧠 SGOS Idea Generation Test\n")
    result = generate_ideas(count=3)
    print(json.dumps(result, indent=2))
