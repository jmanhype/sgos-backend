"""Voice profile endpoints — build, retrieve, and use writing style profiles.
Thin router — all logic delegated to VoiceService."""
from fastapi import APIRouter, HTTPException, Query

from services.voice import voice_service

router = APIRouter(tags=["voice"])


@router.post("/voice/build")
async def build_voice(name: str = Query(...), description: str = Query("")):
    """Build a voice profile from all posts by a specific author."""
    profile = voice_service.build_from_author(name, description)
    if not profile:
        raise HTTPException(status_code=404, detail=f"No posts found for author '{name}'")
    return profile


@router.post("/voice/build-from-text")
async def build_voice_from_text(name: str = Query(...), texts: list[str] = Query(None)):
    """Build a voice profile from manually provided text samples."""
    if not texts:
        raise HTTPException(status_code=400, detail="Provide texts as query params")
    return voice_service.build_from_texts(name, texts)


@router.get("/voice/{name}")
async def get_voice(name: str):
    """Get a stored voice profile."""
    profile = voice_service.get(name)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Voice profile '{name}' not found")
    return profile


@router.get("/voice/{name}/prompt")
async def get_voice_prompt(name: str):
    """Get the system prompt fragment for a voice profile."""
    result = voice_service.get_prompt(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Voice profile '{name}' not found")
    return result


@router.get("/voices")
async def list_all_voices():
    """List all voice profiles."""
    return voice_service.list_all()


@router.post("/analyze")
async def analyze_single(text: str = Query(...)):
    """Analyze a single text for style metrics (no storage)."""
    return voice_service.analyze(text)
