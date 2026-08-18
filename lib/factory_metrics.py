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

import re
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

            # reroll_effectiveness: mean score(reroll attempt) - score(original)
            # over production groups that actually rerolled (reroll_count 0 AND 1).
            rows = conn.execute(
                f"""SELECT production_id,
                           MIN(CASE WHEN reroll_count = 0 THEN qc_score END) AS first_score,
                           MIN(CASE WHEN reroll_count = 1 THEN qc_score END) AS last_score
                    FROM factory_jobs
                    WHERE created_at >= ? AND qc_score IS NOT NULL
                    GROUP BY production_id
                    HAVING COUNT(DISTINCT reroll_count) > 1""",
                (cutoff,),
            ).fetchall()
            deltas = []
            for g in rows:
                first_s = g["first_score"]
                last_s = g["last_score"]
                if first_s is not None and last_s is not None:
                    deltas.append(last_s - first_s)
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


# ─── Dialogue diversity ────────────────────────────────────────────────────────

_QUOTED_DIALOGUE = re.compile(r'"([^"]{2,})"')
_STOP = {"the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to",
         "for", "with", "it", "is", "this", "that", "you", "we", "they", "i",
         "me", "my", "your", "so", "just", "like", "about", "not", "no", "one",
         "every", "nobody", "it's", "we're", "don't", "you're"}


def _extract_dialogue(prompt: Optional[str]) -> list[str]:
    """Pull quoted dialogue lines from a production's stored prompt."""
    if not prompt:
        return []
    return [q for q in _QUOTED_DIALOGUE.findall(prompt) if len(q.strip()) > 1]


def _token_set(text: str) -> set[str]:
    """Lowercased content-word token set for a line (stopwords + short words removed)."""
    return {w for w in re.findall(r"[a-z]{3,}", text.lower())
            if w not in _STOP}


def _pairwise_distinctness(dialogues: list[list[str]]) -> float:
    """Mean 1 - Jaccard overlap over all unordered pairs of productions.

    The token sets (stopwords removed) of each production is compared; distinctness
    is 1.0 when no two productions share a dialogue content word and 0.0 when every
    pair is identical. Returns 0.0 with <2 dialogued productions.
    """
    # Flatten each production's multi-line dialogue into one token set.
    flat = [set(w for line in dls for w in _token_set(line)) for dls in dialogues if dls]
    if len(flat) < 2:
        return 0.0
    diffs = []
    for i in range(len(flat)):
        for j in range(i + 1, len(flat)):
            a, b = flat[i], flat[j]
            union = a | b
            if not union:
                diffs.append(1.0)  # both empty dialogue -> distinct trivially
                continue
            inter = len(a & b)
            diffs.append(1.0 - inter / len(union))
    return _round(sum(diffs) / len(diffs))


def compute_dialogue_diversity(conn: sqlite3.Connection, days: int = 7,
                               limit: int = 60) -> dict:
    """Compute dialogue diversity metrics over the last `days` days.

    Reads the last `limit` accepted productions' `prompt` text, extracts quoted
    dialogue lines, and reports:
      - pairwise_distinctness   : mean 1-Jaccard across all production pairs
      - style_conditioned_variance : variance of within-style pairwise distinctness
      - override_rate           : fraction of productions whose dialogue tokens
                                  differ from their style's inferred tone signature
      - per_style               : per-style distinctness + sample size
      - n_with_dialogue / n_sampled : volume context (empty dialogue deflates NPP)
    """
    days = max(1, min(int(days), 365))
    where, cutoff = _since_clause(days)
    try:
        rows = conn.execute(
            f"""SELECT id, style_id, prompt FROM productions
                WHERE {where}
                ORDER BY generated_at DESC LIMIT ?""",
            (cutoff, int(limit)),
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"[factory_metrics] dialogue_diversity query failed: {exc}", flush=True)
        rows = []

    samples: list[dict] = []
    for r in rows:
        dls = _extract_dialogue(r["prompt"])
        samples.append({
            "production_id": r["id"],
            "style_id": r["style_id"] or "",
            "dialogue": dls,
            "tokens": set(w for line in dls for w in _token_set(line)),
        })

    dialogued = [s for s in samples if s["tokens"]]
    n_with_dialogue = len(dialogued)

    # 1) Overall pairwise distinctness across all dialogued productions.
    distinctness = _pairwise_distinctness([s["dialogue"] for s in dialogued])

    # 2) Style-conditioned: within-style distinctness per style, then variance across styles.
    by_style: Dict[str, list] = {}
    for s in dialogued:
        by_style.setdefault(s["style_id"], []).append(s["dialogue"])
    per_style = []
    style_dists = []
    for sid, dls_list in by_style.items():
        d = _pairwise_distinctness(dls_list)
        per_style.append({"style_id": sid, "distinctness": d, "n": len(dls_list)})
        if len(dls_list) >= 2:
            style_dists.append(d)
    if style_dists:
        mean_d = sum(style_dists) / len(style_dists)
        style_variance = sum((x - mean_d) ** 2 for x in style_dists) / len(style_dists)
    else:
        style_variance = 0.0

    # 3) Override rate: does a production's dialogue match its style's inferred tone
    #    signature? Use content_factory's inferred tone (if importable) as the
    #    canonical signature; fall back to majority-within-style tokens.
    override_count = 0
    by_style_tokens: Dict[str, set] = {}
    for s in dialogued:
        by_style_tokens.setdefault(s["style_id"], set()).update(s["tokens"])
    try:
        from lib.content_factory import _infer_tone_from_ids, _get_style_guide
    except Exception:  # noqa: BLE001
        _infer_tone_from_ids = None
        _get_style_guide = None

    for s in dialogued:
        expected: Optional[set] = None
        if _get_style_guide is not None:
            g = _get_style_guide(s["style_id"])
            if g and g.get("tone"):
                expected = _token_set(g["tone"])
        # If no guide tone, fall back to the within-style majority token set.
        if not expected:
            size = len(by_style_tokens.get(s["style_id"], set()))
            if size >= 2:
                expected = by_style_tokens[s["style_id"]]
        if expected and s["tokens"]:
            if not (s["tokens"] & expected):  # no overlap with the style's signature
                override_count += 1

    n_eval = max(1, len(dialogued))
    return {
        "window_days": days,
        "n_sampled": len(samples),
        "n_with_dialogue": n_with_dialogue,
        "pairwise_distinctness": distinctness,
        "style_conditioned_variance": _round(style_variance),
        "override_rate": _round(override_count / n_eval),
        "per_style": per_style,
    }
