"""Meta-optimizer — reads factory metrics + QC rejects and proposes improvements.

The meta-optimizer is what makes the factory SELF-IMPROVING: it watches the
factory's own performance data (productions, factory_jobs, qc_rejects) and emits
**versioned proposals** for prompt/policy/tone/threshold changes.

Design principles:
- READ-ONLY. This module NEVER modifies the visual register, tones, thresholds,
  or code. It only *proposes*; a human (or a reviewer endpoint) decides whether
  to apply a proposal. This keeps the meta layer safe to run on a schedule.
- Evidence-driven. Every proposal carries the metric data that supports it so a
  human can verify before applying.
- Threshold-based. Pattern detection fires only when metrics cross configured
  thresholds (low scores, high rerolls, style fatigue, dialogue collapse).

Proposal output shape (see SKILL.md in the meta-optimizer skill):
    {
      "proposals": [
        {
          "type": "prompt_patch" | "tone_adjustment" |
                   "threshold_change" | "style_retirement",
          "target": str,          # style_id, tone category, or threshold name
          "current_value": any,
          "proposed_value": any,
          "evidence": str,        # metric data supporting the change
          "confidence": float,   # 0..1 how strongly the data supports it
          "risk": "low" | "medium" | "high",
        }
      ],
      "summary": str,
      "metrics_snapshot": dict,
    }
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from observability import log

    def _log_info(event: str, **kw: Any) -> None:
        log.info(event, **kw)

    def _log_warning(event: str, **kw: Any) -> None:
        log.warning(event, **kw)
except Exception:  # noqa: BLE001  (allow standalone/test import without observability)
    def _log_info(event: str, **kw: Any) -> None:
        print(f"[meta-optimizer] {event} {kw}", flush=True)

    def _log_warning(event: str, **kw: Any) -> None:
        print(f"[meta-optimizer][warn] {event} {kw}", flush=True)


# Default thresholds for firing proposals (overridable via request body).
DEFAULT_THRESHOLDS = {
    "min_success_rate": 0.5,        # below -> prompt/tone adjustment proposals
    "min_mean_qc_score": 6.0,       # below -> prompt_patch proposals
    "max_reroll_rate": 0.6,         # above -> tone_adjustment proposals
    "max_override_rate": 0.5,       # above -> tone_adjustment proposals
    "min_pairwise_distinctness": 0.35,  # below -> tone_adjustment (diversity collapse)
    "min_style_volume": 3,          # a style needs this many productions to be judged
    "min_style_success_rate": 0.3,  # style below -> style_retirement candidate
    "min_style_dialogue_distinctness": 0.2,  # style dialogue fatigue threshold
}


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def _proposal(type_: str, target: str, current: Any, proposed: Any, evidence: str,
              confidence: float, risk: str) -> dict:
    """Build a proposal dict in the canonical shape."""
    return {
        "type": type_,
        "target": target,
        "current_value": current,
        "proposed_value": proposed,
        "evidence": evidence,
        "confidence": round(_clip(confidence), 3),
        "risk": risk,
    }


def _style_fatigue(probable, style_row) -> float:
    """Heuristic confidence that a style is fatigued (low success + low diversity)."""
    score = float(style_row.get("mean_score") or 0.0)
    success = float(style_row.get("success_rate") or 0.0)
    distinct = float(style_row.get("dialogue_distinctness", 1.0))
    conf = 0.3
    if success < 0.4:
        conf += 0.25
    if score < 5.0:
        conf += 0.25
    if distinct < 0.25:
        conf += 0.2
    return _clip(conf)


def analyze(conn, days: int = 7, thresholds: Optional[dict] = None,
            limit_dialogue: int = 60) -> dict:
    """Run the meta-optimizer: gather metrics, detect patterns, propose changes.

    `conn` is a sqlite3 connection (WAL-safe for reads). Returns proposals + a
    metrics snapshot. Does NOT apply any proposal.
    """
    from lib.factory_metrics import compute_metrics, compute_dialogue_diversity

    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    metrics = compute_metrics(conn, days=days)
    diversity = compute_dialogue_diversity(conn, days=days, limit=limit_dialogue)
    metrics["dialogue_diversity"] = diversity

    proposals: List[dict] = []

    # ── Pattern 1: overall success collapse → prompt/tone adjustment ──
    success_rate = float(metrics.get("success_rate") or 0.0)
    mean_score = float(metrics.get("mean_qc_score") or 0.0)
    if metrics.get("total_productions", 0) >= th["min_style_volume"]:
        if success_rate < th["min_success_rate"]:
            proposals.append(_proposal(
                "tone_adjustment", "global",
                round(success_rate, 3), ">= " + str(th["min_success_rate"]),
                f"success_rate={round(success_rate,3)} below threshold {th['min_success_rate']} "
                f"over {days}d ({metrics.get('total_productions')} productions)",
                confidence=0.6, risk="medium"))
        if mean_score < th["min_mean_qc_score"] and mean_score > 0.0:
            proposals.append(_proposal(
                "prompt_patch", "global_prompt",
                round(mean_score, 3), ">= " + str(th["min_mean_qc_score"]),
                f"mean_qc_score={round(mean_score,3)} below {th['min_mean_qc_score']} over {days}d",
                confidence=0.55, risk="medium"))

    # ── Pattern 2: rising reroll / override rates → tone adjustment ──
    reroll = float(metrics.get("reroll_rate") or 0.0)
    if reroll > th["max_reroll_rate"]:
        proposals.append(_proposal(
            "threshold_change", "reroll_rate_decay",
            round(reroll, 3), "investigate tone drift; consider regenerating affected styles",
            f"reroll_rate={round(reroll,3)} exceeds {th['max_reroll_rate']}; "
            "many productions need >1 attempt (premise/tone mismatch).",
            confidence=0.55, risk="high"))

    override = float(diversity.get("override_rate") or 0.0)
    if override > th["max_override_rate"]:
        proposals.append(_proposal(
            "tone_adjustment", "inferred_tone",
            round(override, 3), "tighten tone-to-dialogue binding",
            f"override_rate={round(override,3)} exceeds {th['max_override_rate']} — "
            "produced dialogue frequently diverges from the style's inferred tone.",
            confidence=0.6, risk="medium"))

    # ── Pattern 3: dialogue diversity collapse → tone_adjustment ──
    distinct = float(diversity.get("pairwise_distinctness") or 0.0)
    if diversity.get("n_with_dialogue", 0) >= 2 and distinct < th["min_pairwise_distinctness"]:
        proposals.append(_proposal(
            "tone_adjustment", "dialogue_style",
            round(distinct, 3), "increase dialogue template variety per tone",
            f"pairwise_distinctness={round(distinct,3)} below "
            f"{th['min_pairwise_distinctness']} ({diversity.get('n_with_dialogue')} dialogued "
            f"productions) — styles are converging on shared fallback lines.",
            confidence=0.65, risk="medium"))

    # ── Pattern 4: per-style fatigue & retirement candidates (from qc_rejects too) ──
    qc_rejects = _load_qc_rejects(conn, days)
    rejects_by_style: Dict[str, int] = {}
    for r in qc_rejects:
        sid = r.get("style_id") or "unknown"
        rejects_by_style[sid] = rejects_by_style.get(sid, 0) + 1

    style_rows = metrics.get("per_style", []) or []
    for sr in style_rows:
        sid = sr.get("style_id", "")
        cnt = int(sr.get("count") or 0)
        if cnt < int(th["min_style_volume"]):
            continue
        success = float(sr.get("success_rate") or 0.0)
        rejects = rejects_by_style.get(sid, 0)
        if success < th["min_style_success_rate"]:
            proposals.append(_proposal(
                "style_retirement", sid,
                round(success, 3), "retire / rework style",
                f"style={sid} success_rate={round(success,3)} below "
                f"{th['min_style_success_rate']} with {cnt} productions and {rejects} QC rejects.",
                confidence=_style_fatigue(True, sr), risk="high"))

    # ── Per-style dialogue distinctness (fatigue) from the diversity breakdown ──
    for st in diversity.get("per_style", []) or []:
        sid = st.get("style_id", "")
        if int(st.get("n") or 0) < 2:
            continue
        sd = float(st.get("distinctness") or 1.0)
        if sd < th["min_style_dialogue_distinctness"]:
            proposals.append(_proposal(
                "tone_adjustment", f"style:{sid}:dialogue",
                round(sd, 3), "diversify dialogue templates for this style",
                f"style={sid} dialogue distinctness={round(sd,3)} below "
                f"{th['min_style_dialogue_distinctness']} — repeated/same lines across productions "
                f"({st.get('n')} sampled).",
                confidence=0.6, risk="medium"))

    # ── Summary ──
    if proposals:
        summary = (
            f"{len(proposals)} improvement proposal(s) over the last {days}d. "
            f"success_rate={round(success_rate,2)}, mean_qc={round(mean_score,2)}, "
            f"reroll={round(reroll,2)}, dialogue_distinctness={round(distinct,2)}."
        )
    else:
        summary = (
            f"Factory metrics are healthy over the last {days}d "
            f"(success_rate={round(success_rate,2)}, mean_qc={round(mean_score,2)}, "
            f"reroll={round(reroll,2)}, dialogue_distinctness={round(distinct,2)}). No changes proposed."
        )

    return {
        "proposals": proposals,
        "summary": summary,
        "metrics_snapshot": metrics,
    }


def _load_qc_rejects(conn, days: int) -> List[dict]:
    """Load qc_rejects rows (with a style_id join) for the last `days` days."""
    try:
        cutoff = _iso_days_ago(days)
        rows = conn.execute(
            """SELECT q.production_id, q.keep_decision, q.failure_class, q.severity,
                      q.qc_score, q.reviewed_at, p.style_id
               FROM qc_rejects q
               LEFT JOIN productions p ON p.id = q.production_id
               WHERE q.reviewed_at >= ?""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        print(f"[meta-optimizer] qc_rejects query failed: {exc}", flush=True)
        return []


def _iso_days_ago(days: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()


def log_proposals(proposals: List[dict], summary: str) -> None:
    """Persist/emit proposals for human review via the structured logger."""
    _log_info("optimize.proposals", count=len(proposals), summary=summary)
    for p in proposals:
        _log_warning(
            "optimize.proposal",
            type=p["type"], target=p["target"],
            current=p["current_value"], proposed=p["proposed_value"],
            confidence=p["confidence"], risk=p["risk"],
        )
