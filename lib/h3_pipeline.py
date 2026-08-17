"""H3 (Wan2GP) generation pipeline — job submission, status polling, retrieval.

Runs H3 jobs on a remote 3090 GPU server over SSH/SCP. Jobs are written to a
temp file, SCP'd to the box, and launched under a named tmux session. Status is
polled by SSH + tmux pane capture. Finished outputs are SCP'd back.

All remote identifiers (tmux session name, output filename) are validated
against a strict charset to prevent shell injection through ssh/scp.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

# Default 3090 box — override via env for portability / public-repo safety.
DEFAULT_HOST = os.environ.get("H3_GPU_HOST", "192.168.1.143")
DEFAULT_USER = os.environ.get("H3_GPU_USER", "straughter")

# Where wgp.py lives on the box, and where outputs land.
_h3_home = os.environ.get("H3_REMOTE_HOME", f"/home/{DEFAULT_USER}/Wan2GP")
REMOTE_RUN_DIR = _h3_home
REMOTE_JOBS_DIR = f"{_h3_home}/jobs"
REMOTE_OUTPUTS_DIR = f"{_h3_home}/outputs"

# Constants matching the runner/workflow (kept here so routers/lib agree).
WGP_CMD = "./wgp.py"

# Strict identifier charset — blocks path traversal / shell metacharacters.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.]+$")


def _ssh(user: str, host: str, remote_cmd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run `ssh user@host <remote_cmd>`; remote_cmd is a single shell string.

    NOTE: remote_cmd is executed by a remote shell, so callers MUST pass only
    pre-validated identifiers / quoted paths (see _safe_path).
    """
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           f"{user}@{host}", remote_cmd]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _scp(user: str, host: str, local: str, remote: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """SCP a file from local -> remote on the box."""
    cmd = ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", local,
           f"{user}@{host}:{remote}"]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _scp_from(user: str, host: str, remote: str, local: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """SCP a file from the box -> local."""
    cmd = ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           f"{user}@{host}:{remote}", local]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _safe_identifier(value: str, what: str) -> str:
    """Return `value` if it is a safe remote identifier, else raise ValueError."""
    if not _SAFE_ID_RE.match(value):
        raise ValueError(f"Unsafe {what} '{value}' (must be [A-Za-z0-9_.-])")
    return value


# ─── 1. Submit ─────────────────────────────────────────────────────────────────


def submit_h3_job(
    job_json: dict,
    host: str = DEFAULT_HOST,
    user: str = DEFAULT_USER,
) -> str:
    """Write job_json to a temp file, SCP it to the 3090, and launch wgp.py in
    a named tmux session. Returns the tmux session name for monitoring.

    The session name is derived from the job filename so callers can poll it.
    """
    # A short, safe session token.
    job_id = uuid.uuid4().hex[:12]
    session_name = f"h3-{job_id}"
    _safe_identifier(session_name, "tmux session name")

    # Local temp file to ferry the JSON to the box.
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", prefix=f"h3_{job_id}_", delete=False
    ) as tf:
        temp_path = tf.name
        json.dump(job_json, tf, ensure_ascii=False, indent=2)
        tf.write("\n")

    remote_job = f"{REMOTE_JOBS_DIR}/{job_id}.json"

    try:
        # 1) SCP the job file up.
        res = _scp(user, host, temp_path, remote_job)
        if res.returncode != 0:
            raise RuntimeError(
                f"SCP of job failed: {res.stderr.strip() or res.stdout.strip()}"
            )

        # 2) Launch wgp.py inside a named tmux session (detached).
        #    `cd $REMOTE_RUN_DIR && tmux new-session -d -s <session> './wgp.py <job>'`.
        launch = (
            f"cd {REMOTE_RUN_DIR} && "
            f"tmux new-session -d -s {_safe_identifier(session_name, 'session')} "
            f"'{WGP_CMD} {_safe_identifier(job_id + '.json', 'job file')}'"
        )
        res = _ssh(user, host, launch, timeout=30)
        if res.returncode != 0:
            raise RuntimeError(
                f"Failed to start tmux session {session_name}: "
                f"{res.stderr.strip() or res.stdout.strip()}"
            )
        return session_name
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


# ─── 2. Status ───────────────────────────────────────────────────────────────


def check_h3_status(
    session_name: str,
    host: str = DEFAULT_HOST,
    user: str = DEFAULT_USER,
) -> dict:
    """Check a running H3 job's status via tmux.

    Returns {status: 'running'|'complete'|'failed', progress: float,
             output_file: str|None, error: str|None}.
    """
    _safe_identifier(session_name, "tmux session name")

    # Does the session still exist?
    has = _ssh(user, host, f"tmux has-session -t {_safe_identifier(session_name, 'session')} 2>/dev/null && echo yes || echo no")
    if has.returncode != 0:
        return _error_status(f"ssh/tmux probe failed: {has.stderr.strip()}")
    if has.stdout.strip() != "yes":
        return _error_status("tmux session not found (job never started or already cleaned up)")

    # Capture the pane's current tail (progress + any saved/output lines).
    cap = _ssh(user, host, f"tmux capture-pane -p -t {_safe_identifier(session_name, 'session')} | tail -c 4000")
    if cap.returncode != 0:
        return _error_status(f"tmux capture failed: {cap.stderr.strip()}")
    pane = cap.stdout or ""

    # Pane still alive?
    alive = _ssh(user, host, f"tmux list-panes -t {_safe_identifier(session_name, 'session')} -F '#{{pane_dead}}' 2>/dev/null | head -1")
    if alive.returncode == 0 and alive.stdout.strip() == "1":
        status, output, err = _classify_terminal(pane)
        return {"status": status, "progress": _parse_progress(pane),
                "output_file": output, "error": err}

    return {"status": "running", "progress": _parse_progress(pane),
            "output_file": None, "error": None}


def _classify_terminal(pane: str):
    """Infer final state from a dead pane's captured output."""
    low = pane.lower()
    if any(tok in low for tok in ["error", "traceback", "exception", "failed", "cuda out of memory"]):
        return "failed", None, "error markers found in output"
    # A successful run typically logs saved output filenames.
    saved = _saved_output(pane)
    if saved:
        return "complete", saved, None
    return "failed", None, "process ended without a saved output marker"


def _saved_output(pane: str) -> Optional[str]:
    """Best-effort extract of the output filename from a saved/progress line."""
    m = re.search(r"(?:saved|output)\s*[:=]?\s*([A-Za-z0-9_./\-]+\.(?:mp4|mov|gif|webm))", pane, re.IGNORECASE)
    if m:
        return os.path.basename(m.group(1))
    return None


def _parse_progress(pane: str) -> float:
    """Parse 'shot N/M' and 'denoising X%' into a 0..1 progress float."""
    shots = re.findall(r"shot\s*(\d+)\s*/\s*(\d+)", pane, re.IGNORECASE)
    denoise = re.findall(r"denoising\s*(\d+)\s*%", pane, re.IGNORECASE)
    progress = 0.0
    if shots:
        n, m = int(shots[-1][0]), int(shots[-1][1])
        if m > 0:
            progress = max(progress, n / m)
    if denoise:
        # A single shot's denoise % maps into the current-slot window; treat as
        # coarse progress and clamp to the shot-level progress when both present.
        progress = max(progress, min(1.0, int(denoise[-1]) / 100.0))
    return round(min(1.0, max(0.0, progress)), 3)


def _error_status(error: str) -> dict:
    return {"status": "failed", "progress": 0.0, "output_file": None, "error": error}


# ─── 3. Retrieve ─────────────────────────────────────────────────────────────


def retrieve_h3_result(
    output_filename: str,
    local_path: str,
    host: str = DEFAULT_HOST,
    user: str = DEFAULT_USER,
) -> str:
    """SCP an output file back from 3090 -> local_path. Returns local path."""
    _safe_identifier(os.path.basename(output_filename), "output filename")
    remote = f"{REMOTE_OUTPUTS_DIR}/{os.path.basename(output_filename)}"
    local_dir = Path(local_path).parent if Path(local_path).suffix else Path(local_path)
    local_dir.mkdir(parents=True, exist_ok=True)

    res = _scp_from(user, host, remote, local_path)
    if res.returncode != 0:
        raise RuntimeError(f"SCP of result failed: {res.stderr.strip() or res.stdout.strip()}")
    return local_path


# ─── 4. Config builder with fallback ─────────────────────────────────────────


def _has_refs(meta: Any) -> tuple[bool, str | None, list | None]:
    """Return (has_audio_and_image, audio_ref, image_refs) from keeper_metadata."""
    if not isinstance(meta, dict):
        return False, None, None
    audio = meta.get("audio_guide") or meta.get("audio") or meta.get("audio_path")
    images = meta.get("image_refs") or meta.get("images") or meta.get("image_paths")
    if audio and images:
        return True, audio, (images if isinstance(images, list) else [images])
    return False, None, None


def build_h3_config_with_fallback(
    brief_h3_json: dict,
    keeper_metadata: Optional[dict] = None,
) -> dict:
    """Build a complete H3 job JSON from a brief's h3_job_json.

    - If keeper_metadata carries audio + image refs, build a Ref2VA (lip-sync)
      config: audio_prompt_type='A', audio_guide, image_refs, video_prompt_type.
    - Otherwise fall back to a standard FL2VA multishot config.
    Always ensures an explicit motion prompt per shot.
    """
    base = dict(brief_h3_json)
    has_refs, audio, images = _has_refs(keeper_metadata)

    shots = base.get("shots")
    if isinstance(shots, list) and shots:
        return _build_multishot(base, shots, has_refs, audio, images)

    # Single-job shape (matches the current brief h3_job_json).
    shot = _with_motion(base, has_refs, audio, images)
    if has_refs:
        # Ref2VA: condition on the keeper slice + identity stills.
        shot.update({
            "audio_prompt_type": "A",
            "audio_guide": audio,
            "image_refs": images,
            "video_prompt_type": "I",
        })
    else:
        # FL2VA multishot — strip any stale Ref2VA-only keys for cleanliness.
        for k in ("audio_prompt_type", "audio_guide", "image_refs", "video_prompt_type"):
            shot.pop(k, None)
    return shot


def _with_motion(shot: dict, has_refs: bool, audio: str | None, images: list | None) -> dict:
    """Copy a shot dict, ensuring an explicit `motion_prompt` field exists."""
    out = dict(shot)
    if "motion_prompt" not in out:
        # A crisp default motion directive tuned for H3 (fast, stable, camerawork).
        out["motion_prompt"] = (
            "dynamic stable motion, subtle handheld drift, natural subject "
            "movement, cohesive camera framing, no jumps"
        )
    return out


def _build_multishot(base, shots, has_refs, audio, images):
    """Expand a `shots` list into a structured multishot job JSON, each with an
    explicit motion prompt, keeping the run-level params from `base`."""
    built = []
    for i, shot in enumerate(shots):
        s = _with_motion(shot, has_refs, audio, images)
        # Carry run-level params unless the shot overrides them.
        for k, v in base.items():
            if k == "shots":
                continue
            s.setdefault(k, v)
        if has_refs:
            s.update({
                "audio_prompt_type": "A",
                "audio_guide": audio,
                "image_refs": images,
                "video_prompt_type": "I",
            })
        else:
            for k in ("audio_prompt_type", "audio_guide", "image_refs", "video_prompt_type"):
                s.pop(k, None)
            s.setdefault("image_start", base.get("image_start", "/path/to/your/first_frame.png"))
        built.append(s)
    out = {k: v for k, v in base.items() if k != "shots"}
    out["shots"] = built
    return out
