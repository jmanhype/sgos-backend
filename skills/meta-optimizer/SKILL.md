---
name: meta-optimizer
version: 1.0.0
author: deepseek-fixer
license: internal
description: Use when running SGOS factory self-improvement.
metadata:
  hermes:
    tags: [factory, optimization, metrics, qc, self-improving]
    related_skills: []
---

# Meta-Optimizer

Makes the SGOS content factory SELF-IMPROVING. Reads the factory's own
performance tables and **proposes** versioned prompt/policy/tone/threshold
changes — it NEVER modifies code or the visual register directly.

## When to Use

- Running the factory self-improvement loop (scheduled or on demand).
- POSTing `/v1/factory/optimize` or calling `lib.meta_optimizer.analyze`.
- Investigating declining QC scores, rising reroll rates, style fatigue, or
  dialogue diversity collapse from `factory_jobs`/`productions`/`qc_rejects`.

## Role

- Read factory metrics + QC rejects over a time window.
- Detect patterns: declining scores, rising reroll rates, style fatigue,
  dialogue diversity collapse, tone/dialogue override.
- Emit structured, evidence-backed improvement **proposals** for human review.

## Inputs

- `days` (int, 1..365): lookback window. Default 7.
- `threshold_overrides` (optional dict): override firing thresholds
  (min_success_rate, min_mean_qc_score, max_reroll_rate, max_override_rate,
  min_pairwise_distinctness, min_style_success_rate, ...).

## Output shape

```json
{
  "proposals": [
    {
      "type": "prompt_patch" | "tone_adjustment" | "threshold_change" | "style_retirement",
      "target": "style_id | tone category | threshold name",
      "current_value": 0.42,
      "proposed_value": "new tone / how to change it",
      "evidence": "metric data supporting the change",
      "confidence": 0.65,
      "risk": "low" | "medium" | "high"
    }
  ],
  "summary": "human-readable verdict",
  "metrics_snapshot": {}
}
```

## How to run it

### Endpoint (recommended)

```bash
curl -X POST http://127.0.0.1:8000/v1/factory/optimize \
  -H 'Content-Type: application/json' \
  -d '{"days": 7, "threshold_overrides": {"min_success_rate": 0.6}}'
```

Read-only. Logs every proposal via the structured logger (`optimize.proposal`)
for human review. Never applies anything.

### Library

```python
from lib.meta_optimizer import analyze, log_proposals
from database import get_connection
conn = get_connection()
res = analyze(conn, days=7)
log_proposals(res["proposals"], res["summary"])
```

## The tables (all read-only)

- `productions` — one row per factory output. Columns include `style_id`,
  `franchise`, `premise`, `niche`, `engine`, `qc_status`, `failure_reason`,
  `prompt` (contains quoted dialogue lines), `generated_at`.
- `factory_jobs` — per-stage attempt lineage for each production (`reroll_count`,
  `qc_score`, `status`, `created_at`) to derive reroll rate / time-to-success.
- `qc_rejects` — human/VLM curation verdicts (`keep_decision`, `failure_class`,
  `severity`, `specific_notes`, `prompt_patches`, `qc_score`, `reviewed_at`).

Query them via `database.get_connection()` (WAL, read-safe).

## Pattern detection heuristics

- **Declining scores**: `mean_qc_score` below `min_mean_qc_score` → prompt_patch
  (only fires when real QC data exists; 0.0 = no data = skip).
- **Rising rerolls**: `reroll_rate` above `max_reroll_rate` → threshold_change /
  investigate tone drift.
- **Style fatigue**: a style's `success_rate` below `min_style_success_rate`
  with enough volume (`min_style_volume`) and/or rising QC rejects
  → `style_retirement` (highest risk).
- **Dialogue decline**: `pairwise_distinctness` below `min_pairwise_distinctness`
  → `tone_adjustment` (styles converging on shared fallback lines).
- **Per-style diversity collapse**: style dialogue distinctness below
  `min_style_dialogue_distinctness` → `tone_adjustment` for that style.
- **Override rate**: fraction of productions whose dialogue tokens don't overlap
  the style's inferred tone signature, above `max_override_rate`
  → `tone_adjustment` (tone-to-dialogue binding).

## Guarantees

- **READ-ONLY / propose-only.** Never write productions, factory_jobs,
  qc_rejects, the visual register, or any code. Only a human applies proposals.
- Evidence-driven: every proposal carries the metric that justifies it.
- Threshold-gated: nothing fires speculatively without crossing a threshold and
  meeting minimum sample sizes.
