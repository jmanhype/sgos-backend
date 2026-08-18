"""Production storage helpers — file paths, metadata sidecars, disk quota.

Security: all file paths validated via Path.is_relative_to() against
PRODUCTIONS_ROOT to prevent directory traversal. Files chmod 0644 after
write for host/container cross-access. Disk quota checked before downloads.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PRODUCTIONS_ROOT = Path(
    os.environ.get("PRODUCTIONS_ROOT", str(Path.home() / "sgos-productions"))
).resolve()

# Host-side path for bridge calls (bridges run on host, not in container).
# When running in Docker, PRODUCTIONS_ROOT=/productions but bridges need
# the actual host path. Set HOST_PRODUCTIONS_ROOT env var to match.
HOST_PRODUCTIONS_ROOT = Path(
    os.environ.get("HOST_PRODUCTIONS_ROOT", str(PRODUCTIONS_ROOT))
).resolve()


def to_host_path(container_path: Path) -> str:
    """Convert a container-local production path to a host path for bridge calls."""
    try:
        relative = container_path.relative_to(PRODUCTIONS_ROOT)
        return str(HOST_PRODUCTIONS_ROOT / relative)
    except ValueError:
        return str(container_path)


def validate_path_within_root(path: Path) -> Path:
    """R1: Validate that a resolved path is within PRODUCTIONS_ROOT.

    Uses Path.is_relative_to() (Python 3.9+) instead of startswith().
    Raises ValueError on traversal attempts.
    """
    resolved = path.resolve()
    if not resolved.is_relative_to(PRODUCTIONS_ROOT):
        raise ValueError(
            f"Path traversal blocked: {path} resolves outside {PRODUCTIONS_ROOT}"
        )
    return resolved


def get_production_path(
    date_str: str,
    franchise: str,
    engine: str,
    seed: int,
    ext: str = ".mp4",
) -> Path:
    """Generate deterministic video path: {ROOT}/{YYYY/MM/DD}/{franchise}_{engine}_{seed}_{HHMM}.mp4"""
    normalized = date_str.replace("/", "-")
    parts = normalized.split("-")
    if len(parts) == 3:
        date_dir = "/".join(parts)
    else:
        date_dir = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    hhmm = datetime.now(timezone.utc).strftime("%H%M")
    safe_franchise = franchise.replace("/", "_").replace(" ", "-").lower()[:40]
    filename = f"{safe_franchise}_{engine}_{seed}_{hhmm}{ext}"
    candidate = PRODUCTIONS_ROOT / date_dir / filename
    # Validate generated path (defense in depth)
    return validate_path_within_root(candidate)


def write_meta_sidecar(video_path: Path, metadata: dict) -> Path:
    """Write .meta.json sidecar next to the video file. Returns sidecar path."""
    sidecar = video_path.with_suffix(video_path.suffix + ".meta.json")
    # R1: validate sidecar path
    validate_path_within_root(sidecar)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
    # R3: ensure host-readable permissions
    _set_read_permissions(sidecar)
    return sidecar


def _set_read_permissions(path: Path) -> None:
    """R3: Set file to 0644 so host user can read container-written files."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError as e:
        logger.warning(f"chmod failed for {path}: {e}")


def check_disk_quota(min_free_gb: float = 10.0) -> bool:
    """R4: Return True if PRODUCTIONS_ROOT has at least min_free_gb free.

    Call BEFORE each download. If False, log skip and set state='disk_full'.
    """
    try:
        usage = shutil.disk_usage(str(PRODUCTIONS_ROOT))
        free_gb = usage.free / (1024 ** 3)
        if free_gb < min_free_gb:
            logger.warning(
                f"Disk quota exceeded: {free_gb:.1f}GB free < {min_free_gb}GB minimum"
            )
            return False
        return True
    except OSError as e:
        logger.error(f"Disk quota check failed: {e}")
        return False


def compute_file_hash(path: Path, algo: str = "sha256") -> str:
    """Compute hex digest of a COMPLETED file (post-rename, never .part).

    Returns empty string if file missing or path escapes root.
    """
    try:
        validated = validate_path_within_root(path)
    except ValueError:
        return ""
    if not validated.exists():
        return ""
    h = hashlib.new(algo)
    with open(validated, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def register_production(metadata: dict) -> str:
    """INSERT a production record into SQLite. Returns the production id.

    Handles IntegrityError on (engine, file_hash) UNIQUE as dedup-hit.
    Generates id and generated_at if not provided.
    """
    from sqlite3 import IntegrityError
    from database import get_connection

    prod_id = metadata.get("id") or uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO productions (
                id, style_id, franchise, premise, niche, engine,
                qc_status, failure_reason, seed, duration_s, resolution,
                file_path, file_size, file_hash, prompt, render_duration_s, config_hash, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                prod_id,
                metadata.get("style_id", ""),
                metadata.get("franchise", ""),
                metadata.get("premise", ""),
                metadata.get("niche", ""),
                metadata.get("engine", ""),
                metadata.get("qc_status", "pending"),
                metadata.get("failure_reason"),
                metadata.get("seed"),
                metadata.get("duration_s"),
                metadata.get("resolution"),
                metadata.get("file_path"),
                metadata.get("file_size"),
                metadata.get("file_hash"),
                metadata.get("prompt"),
                metadata.get("render_duration_s"),
                metadata.get("config_hash"),
                metadata.get("generated_at", now),
            ),
        )
        conn.commit()
        return prod_id
    except IntegrityError as e:
        # Dedup hit: same engine + file_hash already registered
        logger.info(f"Dedup hit for engine={metadata.get('engine')}, hash={metadata.get('file_hash')}: {e}")
        # Find existing record
        row = conn.execute(
            "SELECT id FROM productions WHERE engine = ? AND file_hash = ?",
            (metadata.get("engine", ""), metadata.get("file_hash", "")),
        ).fetchone()
        if row:
            return row["id"]
        # Fallback: return generated id even though insert failed
        return prod_id


def resolve_video_path(file_path: str) -> Path:
    """R2: Resolve a stored file_path to an absolute, validated Path.

    Tries relative to PRODUCTIONS_ROOT first, then absolute.
    Always validates containment via is_relative_to().
    Raises ValueError on traversal or missing file.
    """
    # Try relative to root first
    candidate = (PRODUCTIONS_ROOT / file_path).resolve()
    try:
        validated = validate_path_within_root(candidate)
        if validated.exists():
            return validated
    except ValueError:
        pass

    # Try as absolute path
    candidate = Path(file_path).resolve()
    validated = validate_path_within_root(candidate)
    if not validated.exists():
        raise ValueError(f"Video file not found: {file_path}")
    return validated


def get_video_content_type(path: Path) -> str:
    """R2: Determine Content-Type from file extension via mimetypes module.

    Never trusts client-supplied content type.
    """
    ct, _ = mimetypes.guess_type(str(path))
    return ct or "application/octet-stream"
