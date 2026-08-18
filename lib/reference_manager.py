"""Reference image manager — bootstrap-first visual anchors for aesthetics/characters/elements.

Manages a library of reference images used to anchor H3 Ref2VA generation
and VLM curator style-adherence scoring. Bootstrap-first approach: references
are extracted from high-scoring productions before falling back to GPT-image-2.

Storage layout:
  ~/sgos-productions/references/{franchise}/aesthetic_v{N}.png
  ~/sgos-productions/references/{franchise}/characters/{name}_v{N}.png
  ~/sgos-productions/references/{franchise}/elements/{name}_v{N}.png
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REFERENCES_ROOT = Path(
    os.environ.get("REFERENCES_ROOT", str(Path.home() / "sgos-productions" / "references"))
)

# Max images per H3 Ref2VA config (H3 supports up to 9; leave headroom)
MAX_REF_IMAGES = 6


def _ensure_db():
    """Create ReferenceAssets table if missing."""
    from database import get_connection
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reference_assets (
            ref_id TEXT PRIMARY KEY,
            ref_type TEXT NOT NULL,
            franchise TEXT NOT NULL,
            key TEXT NOT NULL,
            path TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'bootstrap',
            qc_score REAL,
            version INTEGER DEFAULT 1,
            config_hash TEXT,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ref_type, franchise, key, version)
        );
        CREATE INDEX IF NOT EXISTS idx_ref_type ON reference_assets(ref_type);
        CREATE INDEX IF NOT EXISTS idx_ref_franchise ON reference_assets(franchise);
        CREATE INDEX IF NOT EXISTS idx_ref_key ON reference_assets(key);
    """)
    conn.commit()


def compute_ref_hash(image_path: Path) -> str:
    """SHA256 hex digest of an image file."""
    h = hashlib.sha256()
    with open(image_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def register_reference(
    ref_type: str,
    franchise: str,
    key: str,
    path: Path,
    source: str = "bootstrap",
    qc_score: Optional[float] = None,
    config_hash: Optional[str] = None,
) -> str:
    """Register a reference image. Returns ref_id.

    Copies the image to the canonical storage location and inserts a DB row.
    If the same (ref_type, franchise, key) already exists, increments version.
    """
    _ensure_db()
    from database import get_connection

    # Determine next version
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(version) FROM reference_assets WHERE ref_type=? AND franchise=? AND key=?",
        (ref_type, franchise, key),
    ).fetchone()
    version = (row[0] or 0) + 1

    # Canonical storage path
    if ref_type == "aesthetic":
        dest_dir = REFERENCES_ROOT / franchise
    elif ref_type == "character":
        dest_dir = REFERENCES_ROOT / franchise / "characters"
    elif ref_type == "element":
        dest_dir = REFERENCES_ROOT / franchise / "elements"
    else:
        dest_dir = REFERENCES_ROOT / franchise / ref_type
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{key}_v{version}.png"

    # Copy image to canonical location
    src = Path(path)
    if src != dest_path:
        shutil.copy2(str(src), str(dest_path))

    ref_id = uuid.uuid4().hex[:16]
    file_hash = compute_ref_hash(dest_path)

    conn.execute(
        """INSERT INTO reference_assets
           (ref_id, ref_type, franchise, key, path, source, qc_score, version, config_hash, generated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ref_id, ref_type, franchise, key, str(dest_path), source, qc_score,
         version, config_hash or file_hash, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    print(f"[REF] Registered {ref_type}/{franchise}/{key} v{version} ({source}) -> {dest_path}", flush=True)
    return ref_id


def get_reference(ref_type: str, franchise: str, key: str) -> Optional[dict]:
    """Get the latest version of a reference. Returns dict or None."""
    _ensure_db()
    from database import get_connection
    conn = get_connection()
    row = conn.execute(
        """SELECT * FROM reference_assets
           WHERE ref_type=? AND franchise=? AND key=?
           ORDER BY version DESC LIMIT 1""",
        (ref_type, franchise, key),
    ).fetchone()
    return dict(row) if row else None


def list_references(ref_type: Optional[str] = None, franchise: Optional[str] = None) -> list[dict]:
    """List references, optionally filtered by type and/or franchise."""
    _ensure_db()
    from database import get_connection
    conn = get_connection()
    conditions = []
    params: list = []
    if ref_type:
        conditions.append("ref_type = ?")
        params.append(ref_type)
    if franchise:
        conditions.append("franchise = ?")
        params.append(franchise)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM reference_assets {where} ORDER BY franchise, ref_type, key, version DESC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_refs_for_production(
    franchise: str,
    premise: str = "",
    character_ids: Optional[list] = None,
) -> list[str]:
    """Get reference image paths relevant for a production.

    Returns list of absolute paths, capped at MAX_REF_IMAGES.
    Priority: aesthetic (always) > characters (if matched) > elements (if premise mentions).
    """
    refs: list[str] = []

    # 1. Aesthetic ref (always include if available)
    aesthetic = get_reference("aesthetic", franchise, "default")
    if aesthetic and Path(aesthetic["path"]).exists():
        refs.append(aesthetic["path"])

    # 2. Character refs (if character_ids provided)
    if character_ids:
        for cid in character_ids:
            char_ref = get_reference("character", franchise, cid)
            if char_ref and Path(char_ref["path"]).exists():
                refs.append(char_ref["path"])
                if len(refs) >= MAX_REF_IMAGES:
                    break

    # 3. Element refs (keyword match against premise)
    if len(refs) < MAX_REF_IMAGES and premise:
        all_elements = list_references(ref_type="element", franchise=franchise)
        premise_lower = premise.lower()
        for elem in all_elements:
            if elem["key"].lower().replace("-", " ").replace("_", " ") in premise_lower:
                if Path(elem["path"]).exists():
                    refs.append(elem["path"])
                    if len(refs) >= MAX_REF_IMAGES:
                        break

    return refs[:MAX_REF_IMAGES]


def bootstrap_from_production(
    video_path: Path,
    franchise: str,
    qc_score: float,
    min_score: float = 7.0,
    max_refs_before_bootstrap: int = 3,
) -> Optional[str]:
    """Extract a frame from a high-scoring production as a bootstrap reference.

    Only bootstraps if:
    - qc_score >= min_score
    - franchise has fewer than max_refs_before_bootstrap aesthetic refs
    - Source is tagged 'bootstrap' (never overwrites GPT-generated refs)

    Returns ref_id or None if skipped.
    """
    if qc_score < min_score:
        return None

    # Check existing aesthetic ref count
    existing = list_references(ref_type="aesthetic", franchise=franchise)
    gpt_refs = [r for r in existing if r.get("source") == "gpt-seed"]
    if gpt_refs:
        # Don't bootstrap over GPT-generated refs
        return None
    if len(existing) >= max_refs_before_bootstrap:
        return None

    # Extract middle frame as thumbnail
    import subprocess
    thumb_dir = video_path.parent / f".thumb_{video_path.stem}"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb = thumb_dir / "mid.png"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path),
             "-vf", "select='eq(n\\,96)',scale=400:-1",
             "-frames:v", "1", str(thumb)],
            capture_output=True, text=True, timeout=15,
        )
        if not thumb.exists() or thumb.stat().st_size == 0:
            return None
    except Exception:
        return None

    ref_id = register_reference(
        ref_type="aesthetic",
        franchise=franchise,
        key="default",
        path=thumb,
        source="bootstrap",
        qc_score=qc_score,
    )

    # Cleanup temp
    try:
        shutil.rmtree(thumb_dir, ignore_errors=True)
    except Exception:
        pass

    return ref_id
