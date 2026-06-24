"""Voice profile endpoints — build, retrieve, and use writing style profiles."""
from fastapi import APIRouter, HTTPException, Query

from database import get_connection
from voice_profile import (
    init_voice_tables,
    build_voice_profile,
    get_voice_profile,
    generate_voice_prompt,
    list_profiles,
    analyze_text,
)

router = APIRouter(tags=["voice"])


@router.post("/voice/build")
async def build_voice(name: str = Query(...), description: str = Query("")):
    """
    Build a voice profile from all posts by a specific author in the database.
    Analyzes writing patterns: hooks, closings, vocabulary, formatting.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT content, title, platform FROM posts WHERE author=? AND LENGTH(content) > 30",
        (name,),
    ).fetchall()
    conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No posts found for author '{name}'")

    texts = [{"content": f"{r['title']} {r['content']}", "source": r["platform"]} for r in rows]
    profile = build_voice_profile(name, texts, description)
    return profile


@router.post("/voice/build-from-text")
async def build_voice_from_text(name: str = Query(...), texts: list[str] = Query(None)):
    """Build a voice profile from manually provided text samples."""
    if not texts:
        raise HTTPException(status_code=400, detail="Provide texts as query params")
    samples = [{"content": t, "source": "manual"} for t in texts]
    profile = build_voice_profile(name, samples, "Manual upload")
    return profile


@router.get("/voice/{name}")
async def get_voice(name: str):
    """Get a stored voice profile."""
    profile = get_voice_profile(name)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Voice profile '{name}' not found")
    return profile


@router.get("/voice/{name}/prompt")
async def get_voice_prompt(name: str):
    """Get the system prompt fragment for a voice profile (inject into content generation)."""
    profile = get_voice_profile(name)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Voice profile '{name}' not found")
    prompt = generate_voice_prompt(profile)
    return {"name": name, "prompt": prompt}


@router.get("/voices")
async def list_all_voices():
    """List all voice profiles."""
    profiles = list_profiles()
    return profiles


@router.post("/analyze")
async def analyze_single(text: str = Query(...)):
    """Analyze a single text for style metrics (no storage)."""
    return analyze_text(text)
