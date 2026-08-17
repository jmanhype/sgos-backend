"""Keeper metadata endpoints — query and slice keeper songs for the MV factory pipeline."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1/keepers", tags=["keepers"])

# Keeper audio root on the 3090 / bulk mount. Override via env for local dev.
KEEPERS_ROOT = Path(
    os.environ.get(
        "KEEPERS_ROOT",
        "/mnt/bulk/home/straughter/sgflix_audio_factory/keepers",
    )
)

# keeper_id must be a safe directory name — no path separators, no traversal
_KEEPER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_keeper_id(keeper_id: str) -> None:
    """Reject keeper_ids that could escape KEEPERS_ROOT."""
    if not _KEEPER_ID_RE.match(keeper_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid keeper_id '{keeper_id}': only alphanumeric, hyphens, underscores allowed",
        )
    resolved = (KEEPERS_ROOT / keeper_id).resolve()
    if not str(resolved).startswith(str(KEEPERS_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="Path traversal detected")


# ─── Schemas ────────────────────────────────────────────────────────────────────


class PhraseBoundary(BaseModel):
    start_s: float = Field(..., description="Phrase start time in seconds")
    end_s: float = Field(..., description="Phrase end time in seconds")
    lyric_text: str = Field("", description="Lyric text for this phrase")


class KeeperMetadata(BaseModel):
    id: str
    filename: str
    title: str
    bpm: Optional[float] = None
    duration_s: Optional[float] = None
    phrase_boundaries: List[PhraseBoundary] = Field(default_factory=list)
    transcript_path: Optional[str] = None
    created_at: Optional[str] = None


class SliceRequest(BaseModel):
    shot_durations: List[float] = Field(
        ..., description="Ordered list of shot durations in seconds"
    )
    fps: float = Field(24.0, description="Frames per second (for frame-count inputs)")
    align_to_phrases: bool = Field(
        True, description="Snap cuts to nearest phrase boundary when possible"
    )


class SliceCommand(BaseModel):
    shot_index: int
    start_s: float
    end_s: float
    duration_s: float
    ffmpeg_cmd: str


class SliceResponse(BaseModel):
    keeper_id: str
    total_shots: int
    commands: List[SliceCommand]


# ─── Helpers ─────────────────────────────────────────────────────────────────────


def _meta_path_for(keeper_dir: Path) -> Path:
    """Return the expected metadata JSON path for a keeper directory."""
    return keeper_dir / "metadata.json"


def _load_keeper_meta(keeper_dir: Path) -> KeeperMetadata:
    """Load metadata.json from a keeper directory, filling defaults."""
    meta_file = _meta_path_for(keeper_dir)
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"No metadata.json in {keeper_dir.name}")

    try:
        raw = json.loads(meta_file.read_text())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid metadata.json: {exc}")

    # Normalise into schema, tolerating missing fields
    boundaries = [
        PhraseBoundary(**b)
        for b in raw.get("phrase_boundaries", [])
        if isinstance(b, dict)
    ]

    return KeeperMetadata(
        id=raw.get("id", keeper_dir.name),
        filename=raw.get("filename", ""),
        title=raw.get("title", keeper_dir.name),
        bpm=raw.get("bpm"),
        duration_s=raw.get("duration_s"),
        phrase_boundaries=boundaries,
        transcript_path=raw.get("transcript_path"),
        created_at=raw.get("created_at"),
    )


def _list_keeper_dirs() -> List[Path]:
    """Return sorted list of keeper directories under KEEPERS_ROOT."""
    if not KEEPERS_ROOT.exists():
        return []
    return sorted([p for p in KEEPERS_ROOT.iterdir() if p.is_dir()])


def _align_time_to_phrase(t: float, boundaries: List[PhraseBoundary], snap_window: float = 0.5) -> float:
    """Snap t to the nearest phrase boundary within snap_window seconds."""
    best = t
    best_dist = snap_window + 1
    for b in boundaries:
        for edge in (b.start_s, b.end_s):
            dist = abs(edge - t)
            if dist < best_dist:
                best_dist = dist
                best = edge
    return best


def _build_slice_commands(
    keeper: KeeperMetadata,
    shot_durations: List[float],
    align: bool,
) -> List[SliceCommand]:
    """Generate ffmpeg slice commands for each shot window."""
    cmds: List[SliceCommand] = []
    cursor = 0.0
    duration_cap = keeper.duration_s

    for idx, dur in enumerate(shot_durations):
        start = cursor
        end = start + dur

        # Align end to phrase boundary when requested
        if align and keeper.phrase_boundaries:
            end = _align_time_to_phrase(end, keeper.phrase_boundaries)

        # Clamp to song duration
        if duration_cap is not None and end > duration_cap:
            end = duration_cap

        actual_dur = max(0.0, end - start)
        # Escape filename for shell safety (double quotes inside double quotes)
        safe_filename = keeper.filename.replace('"', '\\"')
        ffmpeg_cmd = (
            f"ffmpeg -ss {start:.3f} -to {end:.3f} "
            f'-i "{safe_filename}" -c copy '
            f"shot_{idx:03d}.wav"
        )

        cmds.append(
            SliceCommand(
                shot_index=idx,
                start_s=round(start, 3),
                end_s=round(end, 3),
                duration_s=round(actual_dur, 3),
                ffmpeg_cmd=ffmpeg_cmd,
            )
        )
        cursor = end

    return cmds


# ─── Endpoints ───────────────────────────────────────────────────────────────────


@router.get("", response_model=List[KeeperMetadata])
async def list_keepers():
    """List all available keeper songs with metadata summaries."""
    dirs = _list_keeper_dirs()
    keepers: List[KeeperMetadata] = []
    for d in dirs:
        try:
            keepers.append(_load_keeper_meta(d))
        except HTTPException:
            # Skip keepers without valid metadata
            continue
    return keepers


@router.get("/{keeper_id}", response_model=KeeperMetadata)
async def get_keeper(keeper_id: str):
    """Get full keeper details: BPM, duration, phrase timestamps, transcript path."""
    _validate_keeper_id(keeper_id)
    target = KEEPERS_ROOT / keeper_id
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Keeper '{keeper_id}' not found")
    return _load_keeper_meta(target)


@router.post("/{keeper_id}/slice", response_model=SliceResponse)
async def slice_keeper(keeper_id: str, body: SliceRequest):
    """Given shot durations, return ffmpeg commands to cut the keeper into matching windows.

    Cuts are aligned to phrase boundaries where possible when align_to_phrases=True.
    """
    _validate_keeper_id(keeper_id)
    target = KEEPERS_ROOT / keeper_id
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Keeper '{keeper_id}' not found")

    keeper = _load_keeper_meta(target)

    if not body.shot_durations:
        raise HTTPException(status_code=400, detail="shot_durations must be non-empty")

    commands = _build_slice_commands(keeper, body.shot_durations, body.align_to_phrases)

    return SliceResponse(
        keeper_id=keeper.id,
        total_shots=len(commands),
        commands=commands,
    )
