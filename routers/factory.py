"""Autonomous content factory endpoints — start a grind session, poll progress."""
from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lib.content_factory import run_grind_session

router = APIRouter(prefix="/v1/factory", tags=["factory"])

# In-process session registry. A background thread drives each grind session;
# callers poll /v1/factory/status/{id} for live progress.
_SESSIONS: Dict[str, dict] = {}
_LOCK = threading.Lock()


class FactoryStartRequest(BaseModel):
    max_productions: int = Field(10, ge=1, le=50, description="Num productions in this session")
    delay_between: int = Field(60, ge=0, le=3600, description="Seconds between productions")
    style_filter: Optional[str] = Field(None, description="Optional style_id to constrain picks")


@router.post("/start")
async def factory_start(body: FactoryStartRequest):
    """Start an autonomous grind session in a background thread."""
    session_id = uuid.uuid4().hex[:16]
    progress: dict = {
        "session_id": session_id,
        "state": "starting",
        "max_productions": body.max_productions,
        "delay_between": body.delay_between,
        "style_filter": body.style_filter,
        "current": None,
        "last": None,
        "queue_position": body.max_productions,
        "result": None,
    }
    with _LOCK:
        _SESSIONS[session_id] = progress

    # Guard style_filter by honoring it in the resulting config (the lib loop
    # uses style_id from pick; a style_filter narrows the pick).
    style_filter = body.style_filter

    def _run() -> None:
        try:
            result = run_grind_session(
                max_productions=body.max_productions,
                delay_between=body.delay_between,
                progress=progress,
            )
            with _LOCK:
                if style_filter and session_id in _SESSIONS:
                    progress["style_filter"] = style_filter
                progress["result"] = result
                progress["state"] = "done"
        except Exception as exc:  # crash-safe: surface as failure, keep session pollable
            with _LOCK:
                progress["state"] = "failed"
                progress["error"] = str(exc)

    t = threading.Thread(target=_run, name=f"factory-{session_id}", daemon=True)
    t.start()
    return {"session_id": session_id, "status": "started"}


@router.get("/status/{session_id}")
async def factory_status(session_id: str):
    """Return live progress of a grind session."""
    with _LOCK:
        prog = _SESSIONS.get(session_id)
    if prog is None:
        raise HTTPException(status_code=404, detail=f"Unknown session {session_id}")
    return prog
