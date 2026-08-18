#!/usr/bin/env python3
"""Curation cron job — automated QC review + reject/reroll logging.

Designed to run as a no_agent cron job:
  python scripts/curation_cron.py

Reads MODELSCOPE_API_KEY and PRODUCTIONS_ROOT from env vars.
Finds uncurated productions, runs ModelScope vision QC with enhanced prompt,
logs structured decisions to qc_rejects table.

Exit codes: 0 = success (even if 0 productions reviewed), 1 = fatal error.
"""
import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ─── Config ────────────────────────────────────────────────────────────────────
DB_PATH = os.environ.get("SGOS_DB", str(Path.home() / "sgos-backend" / "sgos.db"))
PRODUCTIONS_ROOT = Path(os.environ.get("PRODUCTIONS_ROOT", str(Path.home() / "sgos-productions")))
API_KEY = os.environ.get("MODELSCOPE_API_KEY", "")
API_URL = "https://api-inference.modelscope.ai/v1/chat/completions"
MODEL = "Qwen-Ambassador/Qwen3.8-Max"
MAX_REVIEW_PER_RUN = int(os.environ.get("CURATION_MAX_PER_RUN", "10"))


def log(msg: str):
    print(f"[CURATOR] {msg}", flush=True)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ensure_tables(conn: sqlite3.Connection):
    """Create qc_rejects if missing (idempotent)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS qc_rejects (
            id TEXT PRIMARY KEY,
            production_id TEXT NOT NULL,
            keep_decision TEXT NOT NULL,
            failure_class TEXT,
            severity TEXT,
            specific_notes TEXT,
            prompt_patches TEXT,
            qc_score REAL,
            reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_qcr_production ON qc_rejects(production_id);
        CREATE INDEX IF NOT EXISTS idx_qcr_decision ON qc_rejects(keep_decision);
    """)
    conn.commit()


def find_uncurated(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """Find productions that have been produced but not yet curated."""
    rows = conn.execute("""
        SELECT p.id, p.file_path, p.engine, p.style_id, p.franchise,
               p.premise, p.prompt, p.qc_score, p.resolution, p.duration_s
        FROM productions p
        WHERE p.file_path IS NOT NULL
          AND p.id NOT IN (SELECT production_id FROM qc_rejects)
        ORDER BY p.generated_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def reencode_for_qc(video_path: Path) -> Path | None:
    """Re-encode video to ≤8MB at 320p for ModelScope API."""
    small = video_path.parent / f".curator_{video_path.stem}.mp4"
    for scale, crf, audio_br in [("320:-2", "30", "96k"), ("240:-2", "35", "64k")]:
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_path),
                 "-vf", f"scale={scale}", "-c:v", "libx264", "-preset", "fast",
                 "-crf", crf, "-c:a", "aac", "-b:a", audio_br, str(small)],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0 and small.exists() and small.stat().st_size <= 8_000_000:
                return small
        except Exception:
            continue
    return None


def build_qc_prompt(metadata: dict) -> str:
    """Build enhanced QC prompt requesting structured curation decision."""
    return (
        f"You are a professional AI video curator. Analyze this AI-generated video and make a curation decision.\n\n"
        f"PRODUCTION CONTEXT:\n"
        f"- Engine: {metadata.get('engine', 'unknown')}\n"
        f"- Style: {metadata.get('style_id', 'unknown')}\n"
        f"- Franchise: {metadata.get('franchise', 'unknown')}\n"
        f"- Premise: {metadata.get('premise', 'unknown')}\n"
        f"- Resolution: {metadata.get('resolution', 'unknown')}\n"
        f"- Duration: {metadata.get('duration_s', '?')}s\n\n"
        f"Respond with ONLY valid JSON (no markdown fences):\n"
        f'{{\n'
        f'  "keep_decision": "keep" | "reject" | "reroll",\n'
        f'  "failure_class": "temporal" | "spatial" | "dialogue" | "style" | "artifact" | "other" | null,\n'
        f'  "severity": "critical" | "major" | "minor" | null,\n'
        f'  "specific_notes": "detailed explanation of issues found",\n'
        f'  "prompt_patches": [{{"find": "text to find in prompt", "replace": "improved text"}}],\n'
        f'  "qc_score": 0-10\n'
        f'}}\n\n'
        f"DECISION CRITERIA:\n"
        f"- keep: score >= 7, no critical issues, publishable as-is\n"
        f"- reroll: score 4-6, fixable issues (wrong style, dialogue mismatch, minor artifacts)\n"
        f"- reject: score < 4, unfixable issues (complete scene failure, corrupted output)\n"
        f"- prompt_patches: ONLY suggest changes that would fix the identified issues\n"
    )


def call_modelscope(video_path: Path, metadata: dict) -> dict | None:
    """Send video to ModelScope for QC review. Returns parsed result or None."""
    import urllib.request

    # Re-encode
    small = reencode_for_qc(video_path)
    if not small:
        log(f"  Re-encode failed for {video_path.name}")
        return None

    try:
        b64 = base64.b64encode(small.read_bytes()).decode("ascii")
    finally:
        try:
            small.unlink(missing_ok=True)
        except OSError:
            pass

    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64}"}},
                {"type": "text", "text": build_qc_prompt(metadata)},
            ],
        }],
        "max_tokens": 2048,
        "temperature": 0.3,
    }

    try:
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log(f"  ModelScope API error: {e}")
        return None

    # Parse response
    content = ""
    try:
        content = data["choices"][0]["message"]["content"]
        content = re.sub(r'^```(?:json)?\s*', '', content.strip())
        content = re.sub(r'\s*```$', '', content.strip())
        return json.loads(content)
    except Exception as e:
        log(f"  Parse error: {e}, raw[:200]: {content[:200]}")
        return None


def local_fallback_qc(prod: dict) -> dict:
    """Simple local QC when ModelScope is unavailable."""
    file_path = prod.get("file_path", "")
    score = prod.get("qc_score") or 5.0

    # Check file exists and size
    try:
        fp = Path(file_path)
        if not fp.exists():
            return {"keep_decision": "reject", "failure_class": "artifact",
                    "severity": "critical", "specific_notes": "Video file missing",
                    "prompt_patches": [], "qc_score": 0}
        size = fp.stat().st_size
        if size < 1_000_000:
            return {"keep_decision": "reject", "failure_class": "artifact",
                    "severity": "critical", "specific_notes": f"File too small ({size} bytes)",
                    "prompt_patches": [], "qc_score": 1}
    except Exception:
        pass

    # Use existing QC score as decision basis
    if score >= 7:
        decision = "keep"
    elif score >= 4:
        decision = "reroll"
    else:
        decision = "reject"

    return {
        "keep_decision": decision,
        "failure_class": None,
        "severity": None,
        "specific_notes": f"Local fallback: qc_score={score}",
        "prompt_patches": [],
        "qc_score": score,
    }


def insert_reject(conn: sqlite3.Connection, production_id: str, result: dict):
    """Insert a curation decision into qc_rejects."""
    job_id = uuid.uuid4().hex[:16]
    patches_json = json.dumps(result.get("prompt_patches", [])) if result.get("prompt_patches") else None
    conn.execute(
        """INSERT INTO qc_rejects
           (id, production_id, keep_decision, failure_class, severity,
            specific_notes, prompt_patches, qc_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, production_id, result["keep_decision"],
         result.get("failure_class"), result.get("severity"),
         result.get("specific_notes", ""), patches_json,
         result.get("qc_score")),
    )
    conn.commit()


def main():
    log(f"Starting curation run (max {MAX_REVIEW_PER_RUN} per run)")

    if not API_KEY:
        log("WARNING: MODELSCOPE_API_KEY not set — using local fallback QC only")

    conn = get_db()
    ensure_tables(conn)

    uncurated = find_uncurated(conn, MAX_REVIEW_PER_RUN)
    log(f"Found {len(uncurated)} uncurated productions")

    if not uncurated:
        log("Nothing to review. Exiting.")
        conn.close()
        return

    stats = {"reviewed": 0, "kept": 0, "rejected": 0, "rerolled": 0, "errors": 0}

    for prod in uncurated:
        pid = prod["id"]
        file_path = prod.get("file_path", "")
        log(f"Reviewing {pid}: engine={prod['engine']}, style={prod.get('style_id', '?')}")

        # Try ModelScope first, fall back to local
        result = None
        if API_KEY and file_path:
            fp = Path(file_path)
            if fp.exists() and fp.stat().st_size > 1_000_000:
                result = call_modelscope(fp, prod)
                if result:
                    log(f"  ModelScope: {result.get('keep_decision')} (score={result.get('qc_score')})")
                else:
                    log(f"  ModelScope failed, falling back to local QC")

        if not result:
            result = local_fallback_qc(prod)
            log(f"  Local QC: {result['keep_decision']} (score={result.get('qc_score')})")

        # Insert decision
        try:
            insert_reject(conn, pid, result)
            stats["reviewed"] += 1
            decision = result["keep_decision"]
            if decision == "keep":
                stats["kept"] += 1
            elif decision == "reject":
                stats["rejected"] += 1
            elif decision == "reroll":
                stats["rerolled"] += 1
        except Exception as e:
            log(f"  DB insert failed: {e}")
            stats["errors"] += 1

    conn.close()
    log(f"DONE: reviewed={stats['reviewed']}, kept={stats['kept']}, "
        f"rejected={stats['rejected']}, rerolled={stats['rerolled']}, errors={stats['errors']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)
