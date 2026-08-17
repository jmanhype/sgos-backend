"""H3 (Wan2GP) generation endpoints — submit, poll, retrieve music-video shots."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lib.h3_pipeline import (
    build_h3_config_with_fallback,
    check_h3_status,
    retrieve_h3_result,
    submit_h3_job,
)

router = APIRouter(prefix="/v1/h3", tags=["h3"])


class H3GenerateRequest(BaseModel):
    """A brief's h3_job_json plus an optional keeper_id for Ref2VA refs."""

    h3_job_json: Dict[str, Any] = Field(..., description="Brief h3_job_json to run")
    keeper_id: Optional[str] = Field(None, description="Keeper song id (for audio/image refs)")


class H3GenerateResponse(BaseModel):
    session_name: str = Field(..., description="tmux session name to poll for status")


@router.post("/generate", response_model=H3GenerateResponse)
async def h3_generate(body: H3GenerateRequest):
    """Build the H3 config (Ref2VA if keeper refs exist, else FL2VA multishot),
    submit the job on the 3090, and return the tmux session for polling."""
    if not body.h3_job_json:
        raise HTTPException(status_code=400, detail="h3_job_json must be a non-empty object")

    keeper_metadata: Optional[dict] = None
    if body.keeper_id:
        keeper_metadata = _load_keeper_metadata(body.keeper_id)

    try:
        config = build_h3_config_with_fallback(body.h3_job_json, keeper_metadata)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid H3 config: {exc}")

    try:
        session_name = submit_h3_job(config)
    except Exception as exc:  # sanitize: don't leak remote paths/hosts to API callers
        raise HTTPException(status_code=502, detail="H3 job submission failed (check server connectivity)")

    return H3GenerateResponse(session_name=session_name)


@router.get("/status/{session_name}")
async def h3_status(session_name: str):
    """Return the live status + progress of a submitted H3 job."""
    try:
        return check_h3_status(session_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid session name: {exc}")
    except Exception:
        raise HTTPException(status_code=502, detail="H3 status probe failed (check server connectivity)")


def _load_keeper_metadata(keeper_id: str) -> Optional[dict]:
    """Load a keeper's metadata dict (audio/image refs) following keepers.py.

    Returns None if the keeper isn't resolvable (fallback handled downstream).
    """
    try:
        from routers.keepers import KEEPERS_ROOT, _load_keeper_meta
        target = KEEPERS_ROOT / keeper_id
        if not target.is_dir():
            return None
        meta = _load_keeper_meta(target)
        return meta.model_dump(exclude_none=True)
    except HTTPException:
        return None
    except Exception:
        return None
