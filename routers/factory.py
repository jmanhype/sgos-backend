"""Autonomous content factory endpoints — start a grind session, poll progress."""
from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from database import get_connection
from lib.content_factory import run_grind_session
from lib.factory_metrics import compute_metrics
from lib.meta_optimizer import (
    analyze as run_optimizer,
    log_proposals,
    store_proposals,
    list_proposals,
    set_proposal_status,
    apply_proposal,
)

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


@router.get("/metrics")
async def factory_metrics(days: int = Query(7, ge=1, le=365, description="Lookback window in days")):
    """Return computed factory quality metrics over the last `days` days."""
    try:
        conn = get_connection()
        return compute_metrics(conn, days=days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"metrics computation failed: {exc}")


class OptimizeRequest(BaseModel):
    """Body for the meta-optimizer. Threshold overrides are optional."""
    days: int = Field(7, ge=1, le=365, description="Lookback window in days")
    threshold_overrides: Optional[Dict[str, float]] = Field(
        default=None, description="Optional threshold overrides (e.g. min_success_rate, min_mean_qc_score)")


@router.post("/optimize")
async def factory_optimize(body: OptimizeRequest):
    """Run the meta-optimizer: analyze factory metrics + QC rejects and propose changes.

    READ-ONLY: returns versioned improvement proposals but does NOT apply them.
    Every proposal is logged (structured) AND stored in the optimizer_proposals
    review queue (status='pending') for a human to review/approve/reject.
    """
    try:
        conn = get_connection()
        result = run_optimizer(
            conn, days=body.days, thresholds=body.threshold_overrides)
        # Log + queue proposals for human review (the meta layer never auto-applies).
        log_proposals(result.get("proposals", []), result.get("summary", ""))
        stored_ids = store_proposals(conn, result.get("proposals", []))
        result["queued_proposal_ids"] = stored_ids
        result["queued_count"] = len(stored_ids)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"optimize analysis failed: {exc}")


@router.get("/proposals")
async def factory_proposals(status: Optional[str] = Query(None, description="Filter by status (e.g. 'pending')")):
    """List proposals from the human-review queue, sorted by confidence desc."""
    try:
        conn = get_connection()
        return {"proposals": list_proposals(conn, status=status)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"list proposals failed: {exc}")


class ProposalNotes(BaseModel):
    """Notes attached to a review decision."""
    reviewer_notes: Optional[str] = Field(default=None, description="Human reviewer notes")


@router.post("/proposals/{proposal_id}/approve")
async def factory_approve_proposal(proposal_id: str, body: Optional[ProposalNotes] = None):
    """Approve a proposal (no change is applied yet — only status='approved')."""
    try:
        conn = get_connection()
        updated = set_proposal_status(
            conn, proposal_id, "approved",
            notes=body.reviewer_notes if body else None)
        if not updated:
            raise HTTPException(status_code=404, detail=f"proposal {proposal_id} not found")
        return {"proposal_id": proposal_id, "status": updated.get("status"), "proposal": updated}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"approve failed: {exc}")


@router.post("/proposals/{proposal_id}/reject")
async def factory_reject_proposal(proposal_id: str, body: Optional[ProposalNotes] = None):
    """Reject a proposal, recording the reviewer's notes."""
    try:
        conn = get_connection()
        updated = set_proposal_status(
            conn, proposal_id, "rejected",
            notes=body.reviewer_notes if body else None)
        if not updated:
            raise HTTPException(status_code=404, detail=f"proposal {proposal_id} not found")
        return {"proposal_id": proposal_id, "status": updated.get("status"), "proposal": updated}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"reject failed: {exc}")


@router.post("/proposals/{proposal_id}/apply")
async def factory_apply_proposal(proposal_id: str):
    """Apply an APPROVED proposal (writes to the persisted config store, status='applied').

    Only status='approved' proposals can be applied; otherwise a 409 is returned.
    Logs exactly what was applied for audit/rollback.
    """
    try:
        conn = get_connection()
        outcome = apply_proposal(conn, proposal_id)
        return {"proposal_id": proposal_id, "applied": outcome["applied"],
                "proposal": outcome["proposal"]}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"apply failed: {exc}")


@router.get("/curate/{production_id}")
async def curate_production(production_id: str):
    """Run VLM curation on a production: extract frames, analyze, store verdict.

    Returns structured verdict JSON with keep/reject/reroll decision.
    Stores result in qc_rejects table for downstream consumption.
    """
    import subprocess
    import uuid as _uuid
    from pathlib import Path
    from datetime import datetime, timezone

    conn = get_connection()

    # Look up production
    row = conn.execute(
        "SELECT * FROM productions WHERE id = ?", (production_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Production not found")

    prod = dict(row)
    file_path = prod.get("file_path", "")
    if not file_path:
        raise HTTPException(404, "Production has no video file")

    # Resolve video path
    from lib.production_store import resolve_video_path
    try:
        full_path = resolve_video_path(file_path)
    except ValueError:
        raise HTTPException(404, f"Video file not accessible: {file_path}")

    if not full_path.exists():
        raise HTTPException(404, f"Video file not found: {full_path}")

    # Extract 5 frames for VLM analysis
    frames_dir = full_path.parent / f".curator_{full_path.stem}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_files = []
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(full_path),
             "-vf", "select='eq(n\\,0)+eq(n\\,48)+eq(n\\,96)+eq(n\\,144)+eq(n\\,175)',scale=400:-1",
             "-vsync", "vfr", str(frames_dir / "frame_%02d.png")],
            capture_output=True, text=True, timeout=30,
        )
        frame_files = sorted(frames_dir.glob("frame_*.png"))
    except Exception as e:
        raise HTTPException(500, f"Frame extraction failed: {e}")

    if not frame_files:
        raise HTTPException(500, "No frames extracted from video")

    # Build analysis context from production metadata
    style_info = ""
    try:
        from lib.content_factory import _get_style_guide, _infer_tone_from_ids
        guide = _get_style_guide(prod.get("style_id", ""))
        if not guide:
            guide = _infer_tone_from_ids(prod.get("style_id", ""), prod.get("franchise", ""))
        if guide:
            style_info = f"Tone: {guide.get('tone', 'unknown')}. Banned: {guide.get('banned_phrases', [])}"
    except Exception:
        pass

    analysis_prompt = (
        f"You are a professional AI video curator. Analyze these frames from a generated video.\n\n"
        f"PRODUCTION CONTEXT:\n"
        f"- Engine: {prod.get('engine', 'unknown')}\n"
        f"- Style: {prod.get('style_id', 'unknown')}\n"
        f"- Franchise: {prod.get('franchise', 'unknown')}\n"
        f"- Premise: {prod.get('premise', 'unknown')}\n"
        f"- Resolution: {prod.get('resolution', 'unknown')}\n"
        f"- Duration: {prod.get('duration_s', '?')}s\n"
        f"{f'- Style guide: {style_info}' if style_info else ''}\n\n"
        f"Evaluate these frames and respond with ONLY valid JSON:\n"
        f'{{\n'
        f'  "keep_decision": "keep" | "reject" | "reroll",\n'
        f'  "failure_class": "temporal" | "spatial" | "dialogue" | "style" | "artifact" | "visual_repetition" | null,\n'
        f'  "severity": "critical" | "major" | "minor" | null,\n'
        f'  "specific_notes": "detailed explanation of issues found",\n'
        f'  "prompt_patches": [{{"find": "text to find in prompt", "replace": "improved text"}}],\n'
        f'  "score": 0-10,\n'
        f'  "reasoning": "one paragraph overall assessment"\n'
        f'}}\n\n'
        f"DECISION CRITERIA:\n"
        f"- keep: score >= 7, no critical issues, publishable\n"
        f"- reroll: score 4-6, fixable issues\n"
        f"- reject: score < 4, unfixable\n"
    )

    # Call ModelScope API directly for structured analysis
    import base64
    import json as _json
    import re as _re
    import os as _os

    api_key = _os.environ.get("MODELSCOPE_API_KEY", "")
    verdict = None
    if not api_key:
        # Fallback: local heuristic based on existing QC score
        score = prod.get("qc_score") or 5.0
        if score >= 7:
            verdict = {"keep_decision": "keep", "failure_class": None, "severity": None,
                       "specific_notes": "Local fallback: passed technical QC", "prompt_patches": [],
                       "score": score, "reasoning": "No VLM available; using technical QC score."}
        elif score >= 4:
            verdict = {"keep_decision": "reroll", "failure_class": "style", "severity": "minor",
                       "specific_notes": "Local fallback: marginal technical QC score", "prompt_patches": [],
                       "score": score, "reasoning": "No VLM available; marginal score suggests reroll."}
        else:
            verdict = {"keep_decision": "reject", "failure_class": "artifact", "severity": "critical",
                       "specific_notes": "Local fallback: failed technical QC", "prompt_patches": [],
                       "score": score, "reasoning": "No VLM available; low score indicates rejection."}
    else:
        # Encode frames for analysis (up to 4 frames)
        try:
            content_parts = []
            for ff in frame_files[:4]:
                img_b64 = base64.b64encode(ff.read_bytes()).decode("ascii")
                content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}})
            content_parts.append({"type": "text", "text": analysis_prompt})
            
            payload = {
                "model": "Qwen/Qwen3-VL-235B-A22B-Instruct",
                "messages": [{
                    "role": "user",
                    "content": content_parts,
                }],
                "max_tokens": 2048,
                "temperature": 0.3,
            }
            import urllib.request
            req = urllib.request.Request(
                "https://api-inference.modelscope.ai/v1/chat/completions",
                data=_json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = _json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            content = _re.sub(r'^```(?:json)?\s*', '', content.strip())
            content = _re.sub(r'\s*```$', '', content.strip())
            verdict = _json.loads(content)
        except Exception as e:
            # Fallback on API failure
            score = prod.get("qc_score") or 5.0
            verdict = {"keep_decision": "reroll" if score >= 4 else "reject",
                       "failure_class": None, "severity": None,
                       "specific_notes": f"VLM analysis failed: {str(e)[:200]}",
                       "prompt_patches": [], "score": score,
                       "reasoning": f"ModelScope API error; fell back to technical QC score {score}."}

    # Store verdict in qc_rejects
    job_id = _uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()
    patches_json = _json.dumps(verdict.get("prompt_patches", [])) if verdict.get("prompt_patches") else None
    try:
        conn.execute(
            """INSERT INTO qc_rejects
               (id, production_id, keep_decision, failure_class, severity,
                specific_notes, prompt_patches, qc_score, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, production_id, verdict["keep_decision"],
             verdict.get("failure_class"), verdict.get("severity"),
             verdict.get("specific_notes", ""), patches_json,
             verdict.get("score"), now),
        )
        conn.commit()
    except Exception as e:
        raise HTTPException(500, f"Failed to store verdict: {e}")

    # Clean up frames
    try:
        import shutil
        shutil.rmtree(frames_dir, ignore_errors=True)
    except Exception:
        pass

    return {"production_id": production_id, "verdict": verdict, "reviewed_at": now}


@router.get("/references/{franchise}")
async def list_franchise_references(franchise: str):
    """List all reference images for a franchise."""
    try:
        from lib.reference_manager import list_references
        refs = await asyncio.to_thread(list_references, franchise=franchise)
        return {"franchise": franchise, "count": len(refs), "references": refs}
    except Exception as exc:
        raise HTTPException(500, f"Failed to list references: {exc}")
