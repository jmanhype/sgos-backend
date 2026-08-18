"""Production catalog API — query, register, and serve generated videos.

Security: video serving uses DB lookup → realpath containment check →
mimetypes-based Content-Type. Never trusts client-supplied paths or types.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from database import get_connection
from lib.production_store import (
    PRODUCTIONS_ROOT,
    compute_file_hash,
    get_video_content_type,
    register_production,
    resolve_video_path,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/productions", tags=["productions"])


class ProductionCreate(BaseModel):
    style_id: str
    franchise: str
    engine: str
    premise: Optional[str] = None
    niche: Optional[str] = None
    qc_status: Optional[str] = "pending"
    failure_reason: Optional[str] = None
    seed: Optional[int] = None
    duration_s: Optional[float] = None
    resolution: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    file_hash: Optional[str] = None
    prompt: Optional[str] = None
    render_duration_s: Optional[float] = None


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


@router.get("")
def list_productions(
    date: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)"),
    engine: Optional[str] = Query(None),
    style: Optional[str] = Query(None),
    franchise: Optional[str] = Query(None),
    niche: Optional[str] = Query(None),
    qc_status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    """List productions with optional filters."""
    conn = get_connection()
    conditions = []
    params: list = []

    if date:
        conditions.append("DATE(generated_at) = ?")
        params.append(date)
    if engine:
        conditions.append("engine = ?")
        params.append(engine)
    if style:
        conditions.append("style_id = ?")
        params.append(style)
    if franchise:
        conditions.append("franchise LIKE ?")
        params.append(f"%{franchise}%")
    if niche:
        conditions.append("niche = ?")
        params.append(niche)
    if qc_status:
        conditions.append("qc_status = ?")
        params.append(qc_status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM productions {where} ORDER BY generated_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) FROM productions {where}", params
    ).fetchone()[0]

    return {"productions": [_row_to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/today")
def today_summary():
    """Today's production count and summary."""
    conn = get_connection()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT engine, qc_status, COUNT(*) as cnt FROM productions WHERE DATE(generated_at) = ? GROUP BY engine, qc_status",
        (today,),
    ).fetchall()

    total = sum(r["cnt"] for r in rows)
    by_engine: dict = {}
    for r in rows:
        eng = r["engine"]
        if eng not in by_engine:
            by_engine[eng] = {"total": 0, "ok": 0, "failed": 0, "pending": 0}
        by_engine[eng]["total"] += r["cnt"]
        by_engine[eng][r["qc_status"] or "pending"] += r["cnt"]

    return {"date": today, "total": total, "by_engine": by_engine}


@router.get("/stats")
def production_stats():
    """Aggregate stats by engine, style, qc_status."""
    conn = get_connection()
    by_engine = conn.execute(
        "SELECT engine, COUNT(*) as cnt, AVG(duration_s) as avg_dur FROM productions GROUP BY engine"
    ).fetchall()
    by_style = conn.execute(
        "SELECT style_id, COUNT(*) as cnt FROM productions GROUP BY style_id ORDER BY cnt DESC LIMIT 20"
    ).fetchall()
    by_qc = conn.execute(
        "SELECT qc_status, COUNT(*) as cnt FROM productions GROUP BY qc_status"
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM productions").fetchone()[0]

    return {
        "total": total,
        "by_engine": [_row_to_dict(r) for r in by_engine],
        "top_styles": [_row_to_dict(r) for r in by_style],
        "by_qc_status": [_row_to_dict(r) for r in by_qc],
    }


@router.get("/{prod_id}")
def get_production(prod_id: str):
    """Get a single production by ID."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM productions WHERE id = ?", (prod_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Production not found")
    return _row_to_dict(row)


@router.post("")
def create_production(body: ProductionCreate):
    """Register a new production. Returns the assigned ID.

    Handles dedup: if (engine, file_hash) already exists, returns existing ID.
    Security: rejects file_path containing '..' to prevent traversal.
    """
    # Path traversal check
    if body.file_path and ".." in body.file_path:
        raise HTTPException(400, "Invalid file_path: path traversal not allowed")

    data = body.model_dump()
    prod_id = register_production(data)

    # Check if this was a dedup hit (register_production returns existing ID)
    return {"id": prod_id, "status": "created"}


@router.get("/{prod_id}/video")
def serve_video(prod_id: str):
    """R2: Serve video file for a production.

    Security: looks up file_path from DB by id (never from request),
    resolves realpath, validates containment within PRODUCTIONS_ROOT,
    determines Content-Type via mimetypes module. FileResponse supports
    Range/206 for video scrubbing.
    """
    conn = get_connection()
    row = conn.execute("SELECT file_path FROM productions WHERE id = ?", (prod_id,)).fetchone()
    if not row or not row["file_path"]:
        raise HTTPException(404, "Production not found or has no video file")

    # R2: Resolve and validate path containment
    try:
        full_path = resolve_video_path(row["file_path"])
    except ValueError as e:
        logger.error(f"serve_video path validation failed for {prod_id}: {e}")
        raise HTTPException(403, "Access denied")

    # R2: Content-Type from mimetypes, never trust client
    content_type = get_video_content_type(full_path)

    return FileResponse(
        path=str(full_path),
        media_type=content_type,
        filename=full_path.name,
    )


@router.get("/{prod_id}/qc")
def qc_frames(prod_id: str):
    """Extract 3 QC frames from a production video for vision review.

    Returns frame file paths (at 25%, 50%, 75% of duration) plus production
    metadata so the agent can visually review each frame against the prompt.
    Frames are saved next to the video in a .qc_<stem>/ directory.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM productions WHERE id = ?", (prod_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Production not found")

    prod = _row_to_dict(row)
    file_path = prod.get("file_path")
    if not file_path:
        raise HTTPException(404, "Production has no video file")

    # Resolve and validate path
    try:
        full_path = resolve_video_path(file_path)
    except ValueError as e:
        logger.error(f"qc_frames path validation failed for {prod_id}: {e}")
        raise HTTPException(403, "Access denied")

    if not full_path.exists():
        raise HTTPException(404, f"Video file not found at {full_path}")

    # Get video duration via ffprobe
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(full_path)],
            capture_output=True, text=True, timeout=15,
        )
        import json as _json
        data = _json.loads(r.stdout or "{}")
        duration = float(data.get("format", {}).get("duration", 0))
    except Exception:
        duration = 0

    if duration <= 0:
        raise HTTPException(422, "Cannot determine video duration")

    # Extract 3 frames at 25%, 50%, 75%
    frames_dir = full_path.parent / f".qc_{full_path.stem}"
    frames_dir.mkdir(parents=True, exist_ok=True)

    timestamps = [
        ("25pct", duration * 0.25),
        ("50pct", duration * 0.50),
        ("75pct", duration * 0.75),
    ]
    frames = []
    for label, ts in timestamps:
        out = frames_dir / f"{label}_{ts:.1f}s.jpg"
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(full_path),
                 "-frames:v", "1", "-q:v", "2", str(out)],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
                frames.append({
                    "label": label,
                    "timestamp_s": round(ts, 2),
                    "path": str(out),
                    "size": out.stat().st_size,
                })
        except Exception as e:
            logger.warning(f"qc_frames: extraction failed at {label}: {e}")

    if not frames:
        raise HTTPException(500, "Failed to extract any frames")

    return {
        "production_id": prod_id,
        "video_path": str(full_path),
        "duration_s": round(duration, 2),
        "frames": frames,
        "metadata": {
            "engine": prod.get("engine"),
            "style_id": prod.get("style_id"),
            "franchise": prod.get("franchise"),
            "premise": prod.get("premise"),
            "niche": prod.get("niche"),
            "prompt": prod.get("prompt"),
            "resolution": prod.get("resolution"),
            "qc_status": prod.get("qc_status"),
            "qc_score": prod.get("qc_score"),
            "generated_at": prod.get("generated_at"),
        },
    }
