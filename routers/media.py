"""Media endpoints — audio/video transcription via Whisper."""
import os
import tempfile
import urllib.request

from fastapi import APIRouter, HTTPException, Query, UploadFile, File

from config import settings
from transcribe import transcribe as whisper_transcribe, format_transcript, WHISPER_AVAILABLE

router = APIRouter(tags=["media"])


def _validate_external_url(url: str) -> str | None:
    """Validate URL is safe for download. Returns error message or None."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL format"
    if parsed.scheme not in settings.allowed_url_schemes:
        return f"Blocked scheme: {parsed.scheme}"
    if not parsed.hostname:
        return "No hostname in URL"
    hostname = parsed.hostname.lower()
    if hostname in settings.blocked_hosts:
        return f"Blocked internal address: {hostname}"
    if any(hostname.startswith(p) for p in settings.blocked_prefixes):
        return f"Blocked internal address: {hostname}"
    return None


@router.get("/transcribe/status")
async def transcribe_status():
    """Check if Whisper transcription is available."""
    return {"available": WHISPER_AVAILABLE, "engine": "faster-whisper", "ffmpeg": True}


@router.post("/transcribe")
async def transcribe_file(
    file: UploadFile = File(...),
    model_size: str = Query("base", description="Model: tiny, base, small, medium, large-v3"),
    language: str = Query(None, description="ISO 639-1 code or auto-detect"),
    format_type: str = Query("summary", description="Output format: text, timestamped, srt, summary"),
):
    """Transcribe a video or audio file using Whisper."""
    if not WHISPER_AVAILABLE:
        raise HTTPException(status_code=503, detail="faster-whisper not installed")

    max_bytes = settings.max_upload_bytes
    if file.size and file.size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {settings.max_upload_mb}MB)",
        )

    suffix = os.path.splitext(file.filename)[1] if file.filename else ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        if len(content) > max_bytes:
            os.unlink(tmp.name)
            raise HTTPException(
                status_code=413,
                detail=f"File too large (max {settings.max_upload_mb}MB)",
            )
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = whisper_transcribe(tmp_path, model_size=model_size, language=language)
        formatted = format_transcript(result, format_type)
        result["formatted"] = formatted
        return result
    finally:
        os.unlink(tmp_path)


@router.post("/transcribe/url")
async def transcribe_url(
    url: str = Query(..., description="URL to video/audio file"),
    model_size: str = Query("base"),
):
    """Transcribe from a URL (downloads first)."""
    if not WHISPER_AVAILABLE:
        raise HTTPException(status_code=503, detail="faster-whisper not installed")

    url_error = _validate_external_url(url)
    if url_error:
        raise HTTPException(status_code=400, detail=url_error)

    max_bytes = settings.max_upload_bytes
    suffix = os.path.splitext(url.split("?")[0])[1] or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name

    try:
        req = urllib.request.urlopen(url, timeout=60)
        content_length = req.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large (max {settings.max_upload_mb}MB)",
            )

        downloaded = 0
        with open(tmp_path, "wb") as f:
            while True:
                chunk = req.read(8192)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large (max {settings.max_upload_mb}MB)",
                    )
                f.write(chunk)

        result = whisper_transcribe(tmp_path, model_size=model_size)
        return result
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
