"""Factory quality metrics — computed from the productions table.

Metrics cover: throughput, success/reroll rates, per-engine and per-style
breakdowns, failure categories, and GPU-cost-per-acceptable-video.

Design notes:
- factory_jobs table is being introduced separately (Qwen); this module
  queries it opportunistically via _table_exists() and degrades to
  productions-only metrics when absent, so it works before and after
  that migration lands.
- All queries are read-only; caller supplies the connection (WAL mode
  from database.get_connection() is safe for concurrent reads).
- No secrets, no file paths, no premises are returned — aggregates only.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Return True if `table` exists (read-only schema probe)."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _since_clause(days: int) -> tuple[str, str]:
    """Return (where-fragment, iso-cutoff) for the last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return "generated_at >= ?", cutoff


def _col(conn: sqlite3.Connection, sql: str, params: tuple, default: Any = 0) -> Any:
    """Fetch a single scalar with a default on error/NULL."""
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return default
        val = row[0]
        return default if val is None else val
    except sqlite3.Error as exc:
        print(f"[factory_metrics] query failed: {exc}", flush=True)
        return default


def _round(x: float, nd: int = 4) -> float:
    return round(float(x), nd)


# ─── Failure categorisation ────────────────────────────────────────────────────

_INFRA_MARKERS = ("ssh", "scp", "tmux", "download", "disk_full", "network", "connection", "ego-bridge", "401", "502")
_TIMEOUT_MARKERS = ("timeout", "timed out", "deadline")
_CREATIVE_MARKERS = ("qc", "gibberish", "artifact", "mouth", "desync", "lyric", "rejected")


def _categorise_failure(reason: Optional[str]) -> str:
    """Map a failure_reason string to infra|creative|timeout|other."""
    if not reason:
        return "other"
    low = reason.lower()
    if any(m in low for m in _INFRA_MARKERS):
        return "infra"
    if any(m in low for m in _TIMEOUT_MARKERS):
        return "timeout"
    if any(m in low for m in _CREATIVE_MARKERS):
        return "creative"
    return "other"


# ─── Main entry point ─────────────────────────────────────────────────────────


def compute_metrics(conn: sqlite3.Connection, days: int = 7) -> dict:
    """Compute factory quality metrics over the last `days` days.

    Reads the `productions` table; additionally uses `factory_jobs` for
    reroll lineage when that table exists.
    """
    days = max(1, min(int(days), 365))
    where, cutoff = _since_clause(days)
    params = (cutoff,)

    metrics: Dict[str, Any] = {
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── Overall throughput & success ──
    total = _col(
        conn,
        f"SELECT COUNT(*) FROM productions WHERE {where}",
        params,
    )
    completed = _col(
        conn,
        f"SELECT COUNT(*) FROM productions WHERE {where} AND qc_status IN ('ok', 'complete', 'accepted')",
        params,
    )
    failed = _col(
        conn,
        f"SELECT COUNT(*) FROM productions WHERE {where} AND qc_status IN ('failed', 'rejected')",
        params,
    )
    metrics["total_productions"] = total
    metrics["success_rate"] = _round(completed / total) if total else 0.0

    # ── QC score ──
    mean_score = _col(
        conn,
        f"SELECT AVG(qc_score) FROM productions WHERE {where} AND qc_score IS NOT NULL",
        params,
    )
    metrics["mean_qc_score"] = _round(mean_score) if mean_score else 0.0

    # ── Per-engine breakdown ──
    per_engine: Dict[str, dict] = {}
    try:
        rows = conn.execute(
            f"""SELECT engine,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN qc_status IN ('ok','complete','accepted') THEN 1 ELSE 0 END) AS ok,
                       AVG(qc_score) AS avg_score
                FROM productions WHERE {where} AND engine != ''
                GROUP BY engine""",
            params,
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"[factory_metrics] per-engine query failed: {exc}", flush=True)
        rows = []
    for r in rows:
        cnt = r["cnt"] or 0
        per_engine[r["engine"]] = {
            "count": cnt,
            "success_rate": _round((r["ok"] or 0) / cnt) if cnt else 0.0,
            "mean_score": _round(r["avg_score"]) if r["avg_score"] is not None else 0.0,
        }
    metrics["per_engine"] = per_engine

    # ── Per-style breakdown (top 20 by count) ──
    per_style: List[dict] = []
    try:
        rows = conn.execute(
            f"""SELECT style_id,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN qc_status IN ('ok','complete','accepted') THEN 1 ELSE 0 END) AS ok,
                       AVG(qc_score) AS avg_score
                FROM productions WHERE {where} AND style_id != ''
                GROUP BY style_id ORDER BY cnt DESC LIMIT 20""",
            params,
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"[factory_metrics] per-style query failed: {exc}", flush=True)
        rows = []
    for r in rows:
        cnt = r["cnt"] or 0
        per_style.append(
            {
                "style_id": r["style_id"],
                "count": cnt,
                "mean_score": _round(r["avg_score"]) if r["avg_score"] is not None else 0.0,
                "success_rate": _round((r["ok"] or 0) / cnt) if cnt else 0.0,
            }
        )
    metrics["per_style"] = per_style

    # ── Failure categories ──
    failure_categories = {"infra": 0, "creative": 0, "timeout": 0, "other": 0}
    try:
        rows = conn.execute(
            f"SELECT failure_reason FROM productions WHERE {where} AND qc_status IN ('failed','rejected')",
            params,
        ).fetchall()
    except sqlite3.Error:
        rows = []
    for r in rows:
        failure_categories[_categorise_failure(r["failure_reason"])] += 1
    metrics["failure_categories"] = failure_categories

    # ── Reroll metrics & time-to-success (factory_jobs, when present) ──
    reroll_rate = 0.0
    reroll_effectiveness = 0.0
    ttfs = 0.0
    gpu_per_ok = 0.0

    if _table_exists(conn, "factory_jobs"):
        try:
            # reroll_rate: fraction of production groups needing >1 job attempt
            row = conn.execute(
                f"""SELECT COUNT(*) AS groups,
                           SUM(CASE WHEN attempts > 1 THEN 1 ELSE 0 END) AS rerolled
                    FROM (
                        SELECT production_id, COUNT(*) AS attempts
                        FROM factory_jobs WHERE created_at >= ?
                        GROUP BY production_id
                    )""",
                (cutoff,),
            ).fetchone()
            if row and row["groups"]:
                reroll_rate = _round((row["rerolled"] or 0) / row["groups"])

            # reroll_effectiveness: mean score(last attempt) - score(first attempt)
            # over production groups with >1 attempt and scored attempts.
            rows = conn.execute(
                f"""SELECT production_id,
                           MIN(created_at) AS first_ts,
                           MAX(created_at) AS last_ts
                    FROM factory_jobs WHERE created_at >= ?
                    GROUP BY production_id
                    HAVING COUNT(*) > 1""",
                (cutoff,),
            ).fetchall()
            deltas = []
            for g in rows:
                first = conn.execute(
                    "SELECT qc_score FROM factory_jobs WHERE production_id=? AND created_at=? ORDER BY rowid LIMIT 1",
                    (g["production_id"], g["first_ts"]),
                ).fetchone()
                last = conn.execute(
                    "SELECT qc_score FROM factory_jobs WHERE production_id=? AND created_at=? ORDER BY rowid DESC LIMIT 1",
                    (g["production_id"], g["last_ts"]),
                ).fetchone()
                if first and last and first["qc_score"] is not None and last["qc_score"] is not None:
                    deltas.append(last["qc_score"] - first["qc_score"])
            if deltas:
                reroll_effectiveness = _round(sum(deltas) / len(deltas))

            # time_to_first_success: mean seconds from first job of a group to
            # the first successful job of that group.
            ttfs_rows = conn.execute(
                f"""SELECT production_id, MIN(created_at) AS first_ts,
                           MIN(CASE WHEN status='complete' THEN created_at END) AS ok_ts
                    FROM factory_jobs WHERE created_at >= ?
                    GROUP BY production_id""",
                (cutoff,),
            ).fetchall()
            spans = []
            for g in ttfs_rows:
                if g["ok_ts"]:
                    try:
                        t0 = datetime.fromisoformat(g["first_ts"])
                        t1 = datetime.fromisoformat(g["ok_ts"])
                        spans.append((t1 - t0).total_seconds())
                    except (TypeError, ValueError):
                        continue
            if spans:
                ttfs = _round(sum(spans) / len(spans), 1)

            # gpu_minutes_per_acceptable_video: total render minutes / ok videos.
            gpu_row = conn.execute(
                f"""SELECT SUM(render_duration_s) FROM factory_jobs WHERE created_at >= ?""",
                (cutoff,),
            ).fetchone()
            ok_count = conn.execute(
                f"""SELECT COUNT(DISTINCT production_id) FROM factory_jobs
                    WHERE created_at >= ? AND status='complete'""",
                (cutoff,),
            ).fetchone()
            if gpu_row and ok_count and gpu_row[0] and ok_count[0]:
                gpu_per_ok = _round((gpu_row[0] / 60.0) / ok_count[0], 2)
        except sqlite3.Error as exc:
            print(f"[factory_metrics] factory_jobs queries failed: {exc}", flush=True)

    metrics["reroll_rate"] = reroll_rate
    metrics["reroll_effectiveness"] = reroll_effectiveness
    metrics["time_to_first_success_s"] = ttfs
    metrics["gpu_minutes_per_acceptable_video"] = gpu_per_ok

    return metrics
