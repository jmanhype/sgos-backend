"""
SGOS Genome Evolution Engine — Tracks viral hook mutations over time.
Detects emerging patterns, dying formats, and generates meta forecasts.
This is the proprietary intelligence layer that compounds with data.
"""
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import get_connection

# Hook archetypes — keyword clusters that map to content strategies
HOOK_ARCHETYPES = {
    "personal_narrative": {
        "keywords": ["i built", "i tested", "i tried", "my experience", "i spent", "i made", "i learned", "i discovered", "journey"],
        "description": "First-person experience stories",
    },
    "contrarian_take": {
        "keywords": ["stop", "wrong", "actually", "unpopular", "nobody", "lie", "myth", "overrated", "doesn't work", "waste"],
        "description": "Going against conventional wisdom",
    },
    "bold_claim": {
        "keywords": ["will replace", "is dead", "game changer", "revolutionary", "breakthrough", "transforms", "disrupts", "kills"],
        "description": "Strong predictive or declarative statements",
    },
    "tutorial_howto": {
        "keywords": ["how to", "step by step", "guide", "tutorial", "build", "create", "setup", "deploy", "integrate"],
        "description": "Educational content",
    },
    "curiosity_gap": {
        "keywords": ["secret", "hidden", "trick", "hack", "what they don't", "mistake", "surprising", "unexpected", "bizarre"],
        "description": "Information gap that demands clicking",
    },
    "social_proof": {
        "keywords": ["everyone", "viral", "trending", "millions", "popular", "huge", "massive", "explosive", "blew up"],
        "description": "Bandwagon and popularity signals",
    },
    "vulnerability": {
        "keywords": ["failed", "struggled", "mistakes", "wrong", "regret", "embarrassing", "hard truth", "honest", "admit"],
        "description": "Authentic admission of failure or difficulty",
    },
    "list_format": {
        "keywords": ["top", "best", "worst", "ranking", "list", "tools", "apps", "alternatives", "compared"],
        "description": "Curated lists and comparisons",
    },
}


def _conn():
    return get_connection()


def classify_hook(title: str) -> list[str]:
    """Classify a title into hook archetypes."""
    title_lower = title.lower()
    matches = []
    for archetype, config in HOOK_ARCHETYPES.items():
        if any(kw in title_lower for kw in config["keywords"]):
            matches.append(archetype)
    return matches or ["uncategorized"]


def analyze_evolution(days_recent: int = 7, days_baseline: int = 14) -> dict:
    """
    Compare hook archetype performance between recent and baseline periods.
    Uses LLM-extracted genomes (ground truth) when available, falls back to title classification.
    Returns emerging, declining, and stable patterns.
    """
    conn = _conn()

    # ── Phase 1: Use actual LLM-extracted genome data ──
    genome_rows = conn.execute("""
        SELECT g.hook_type, g.emotional_arc, g.structural_pattern,
               g.engagement_score, g.created_at, g.key_phrases,
               g.platform_signals
        FROM viral_genomes g
        ORDER BY g.created_at DESC
    """).fetchall()

    genome_stats = {}
    if genome_rows:
        hook_data = {}
        for row in genome_rows:
            ht = row["hook_type"]
            if ht not in hook_data:
                hook_data[ht] = {"count": 0, "engagement": [], "patterns": [], "emotions": []}
            hook_data[ht]["count"] += 1
            if row["engagement_score"] is not None:
                hook_data[ht]["engagement"].append(row["engagement_score"])
            if row["structural_pattern"]:
                hook_data[ht]["patterns"].append(row["structural_pattern"])
            if row["emotional_arc"]:
                try:
                    emotions = json.loads(row["emotional_arc"])
                    hook_data[ht]["emotions"].extend(emotions)
                except (json.JSONDecodeError, TypeError):
                    pass

        for ht, data in hook_data.items():
            genome_stats[ht] = {
                "count": data["count"],
                "avg_engagement": round(sum(data["engagement"]) / max(len(data["engagement"]), 1), 3),
                "top_pattern": max(set(data["patterns"]), key=data["patterns"].count) if data["patterns"] else "unknown",
                "top_emotions": list(set(data["emotions"]))[:3],
            }

    # ── Phase 2: Title-based classification for volume trends ──
    now = datetime.now(timezone.utc)
    recent_start = (now - timedelta(days=days_recent)).isoformat()
    baseline_start = (now - timedelta(days=days_baseline + days_recent)).isoformat()

    def _period_stats(start, end):
        """Get hook type distribution and performance for a time period."""
        rows = conn.execute("""
            SELECT title, z_score, score, platform, comment_count, subreddit
            FROM posts
            WHERE ingested_at BETWEEN ? AND ?
            AND title IS NOT NULL AND title != ''
        """, (start, end)).fetchall()

        stats = defaultdict(lambda: {"count": 0, "z_scores": [], "scores": [], "comments": []})
        total = 0

        for row in rows:
            title = row["title"]
            if not title:
                continue
            total += 1
            hooks = classify_hook(title)
            for hook_type in hooks:
                stats[hook_type]["count"] += 1
                if row["z_score"] is not None:
                    stats[hook_type]["z_scores"].append(row["z_score"])
                if row["score"] is not None:
                    stats[hook_type]["scores"].append(row["score"])
                if row["comment_count"] is not None:
                    stats[hook_type]["comments"].append(row["comment_count"])

        # Compute aggregates
        for hook_type in stats:
            s = stats[hook_type]
            s["avg_z"] = sum(s["z_scores"]) / max(len(s["z_scores"]), 1)
            s["max_z"] = max(s["z_scores"]) if s["z_scores"] else 0
            s["avg_score"] = sum(s["scores"]) / max(len(s["scores"]), 1)
            s["avg_comments"] = sum(s["comments"]) / max(len(s["comments"]), 1)
            s["pct_of_total"] = (s["count"] / max(total, 1)) * 100
            # Remove raw lists
            del s["z_scores"]
            del s["scores"]
            del s["comments"]

        return dict(stats), total

    baseline_stats, baseline_total = _period_stats(baseline_start, recent_start)
    recent_stats, recent_total = _period_stats(recent_start, now.isoformat())

    # Compute evolution signals
    emerging = []    # Low volume but high z-score + growing
    declining = []   # Was popular but z-scores dropping
    hot = []         # High volume AND high z-score
    opportunities = []  # Low volume but high z-score — underserved

    all_types = set(list(baseline_stats.keys()) + list(recent_stats.keys()))

    for hook_type in all_types:
        recent = recent_stats.get(hook_type, {"count": 0, "avg_z": 0, "max_z": 0, "pct_of_total": 0})
        baseline = baseline_stats.get(hook_type, {"count": 0, "avg_z": 0, "max_z": 0, "pct_of_total": 0})

        volume_change = ((recent["count"] - baseline["count"]) / max(baseline["count"], 1)) * 100
        z_change = recent["avg_z"] - baseline["avg_z"]
        pct_change = recent["pct_of_total"] - baseline["pct_of_total"]

        signal = {
            "archetype": hook_type,
            "description": HOOK_ARCHETYPES.get(hook_type, {}).get("description", ""),
            "recent_count": recent["count"],
            "baseline_count": baseline["count"],
            "volume_change_pct": round(volume_change),
            "recent_avg_z": round(recent["avg_z"], 2),
            "baseline_avg_z": round(baseline["avg_z"], 2),
            "z_change": round(z_change, 2),
            "recent_pct_share": round(recent["pct_of_total"], 1),
            "baseline_pct_share": round(baseline["pct_of_total"], 1),
        }

        if recent["count"] >= 3 and volume_change > 50 and z_change > 0:
            emerging.append(signal)
        elif recent["count"] >= 5 and recent["avg_z"] > 1.0 and recent["pct_of_total"] > 10:
            hot.append(signal)
        elif baseline["count"] >= 5 and volume_change < -30:
            declining.append(signal)
        elif recent["count"] >= 2 and recent["avg_z"] > 2.0 and recent["count"] < baseline_total * 0.05:
            opportunities.append(signal)

    # Sort by impact
    emerging.sort(key=lambda x: -(x["volume_change_pct"] * x["recent_avg_z"]))
    hot.sort(key=lambda x: -(x["recent_avg_z"] * x["recent_count"]))
    declining.sort(key=lambda x: x["volume_change_pct"])
    opportunities.sort(key=lambda x: -x["recent_avg_z"])

    return {
        "analysis_period": f"{days_baseline + days_recent}d baseline vs {days_recent}d recent",
        "baseline_posts": baseline_total,
        "recent_posts": recent_total,
        "genome_data": genome_stats,
        "emerging": emerging[:4],
        "hot": hot[:4],
        "declining": declining[:4],
        "opportunities": opportunities[:4],
        "recommendation": _generate_recommendation(emerging, hot, declining, opportunities),
    }


def _generate_recommendation(emerging, hot, declining, opportunities) -> str:
    """Generate an actionable recommendation from the evolution data."""
    parts = []

    if emerging:
        top = emerging[0]
        parts.append(
            f"Ride the wave: '{top['archetype']}' hooks are surging "
            f"(+{top['volume_change_pct']}% volume, z={top['recent_avg_z']:.1f}). "
            f"Front-run before saturation."
        )

    if opportunities:
        top = opportunities[0]
        parts.append(
            f"Underserved niche: '{top['archetype']}' posts have z={top['recent_avg_z']:.1f} "
            f"but low volume. Early movers win."
        )

    if declining:
        top = declining[0]
        parts.append(
            f"Avoid: '{top['archetype']}' is fading "
            f"({top['volume_change_pct']}% volume). Pivot away."
        )

    if hot:
        top = hot[0]
        parts.append(
            f"Reliable format: '{top['archetype']}' still performing "
            f"(z={top['recent_avg_z']:.1f}, {top['recent_pct_share']}% share). Safe bet."
        )

    return " ".join(parts) if parts else "Insufficient data for recommendation. More ingestion cycles needed."


def format_evolution_report(data: dict) -> str:
    """Format evolution analysis for Telegram delivery."""
    lines = [
        "🧬 *GENOME EVOLUTION REPORT*",
        f"📅 {data['analysis_period']}",
        f"📊 {data['baseline_posts']} → {data['recent_posts']} posts analyzed",
        "",
    ]

    # ── Genome Ground Truth (LLM-extracted) ──
    genome_data = data.get("genome_data", {})
    if genome_data:
        total_genomes = sum(g["count"] for g in genome_data.values())
        lines.append(f"━━━ 🔬 GENOME DATA ({total_genomes} extracted) ━━━")
        for hook_type, stats in sorted(genome_data.items(), key=lambda x: -x[1]["avg_engagement"]):
            eng = stats["avg_engagement"]
            emoji = "🔥" if eng >= 0.75 else "📈" if eng >= 0.5 else "📊"
            lines.append(f"  {emoji} *{hook_type}*: {stats['count']}× | engagement {eng:.2f}")
            if stats.get("top_emotions"):
                lines.append(f"    _emotions: {', '.join(stats['top_emotions'][:3])}_")
            if stats.get("top_pattern") and stats["top_pattern"] != "unknown":
                lines.append(f"    _pattern: {stats['top_pattern']}_")
        lines.append("")

    if data["emerging"]:
        lines.append("🟢 *EMERGING (Ride These)*")
        for e in data["emerging"]:
            lines.append(f"  ↑ {e['archetype']} (+{e['volume_change_pct']}%, z={e['recent_avg_z']:.1f})")
            if e["description"]:
                lines.append(f"    _{e['description']}_")
        lines.append("")

    if data["hot"]:
        lines.append("🔥 *HOT (Reliable Formats)*")
        for h in data["hot"]:
            lines.append(f"  ★ {h['archetype']} ({h['recent_pct_share']}% share, z={h['recent_avg_z']:.1f})")
        lines.append("")

    if data["opportunities"]:
        lines.append("💎 *UNDERSERVED (Early Mover Advantage)*")
        for o in data["opportunities"]:
            lines.append(f"  ◆ {o['archetype']} (z={o['recent_avg_z']:.1f}, only {o['recent_count']} posts)")
        lines.append("")

    if data["declining"]:
        lines.append("🔴 *DECLINING (Avoid)*")
        for d in data["declining"]:
            lines.append(f"  ↓ {d['archetype']} ({d['volume_change_pct']}% volume)")
        lines.append("")

    lines.append("━━━ 💡 STRATEGY ━━━")
    lines.append(f"  {data['recommendation']}")

    return "\n".join(lines)


def platform_breakdown(days: int = 7) -> dict:
    """Per-platform hook performance breakdown."""
    conn = _conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT title, z_score, platform FROM posts
        WHERE ingested_at > ? AND title IS NOT NULL
    """, (cutoff,)).fetchall()

    by_platform = defaultdict(lambda: defaultdict(lambda: {"count": 0, "z_total": 0}))
    for row in rows:
        hooks = classify_hook(row["title"])
        for h in hooks:
            by_platform[row["platform"]][h]["count"] += 1
            by_platform[row["platform"]][h]["z_total"] += (row["z_score"] or 0)

    result = {}
    for platform, hooks in by_platform.items():
        result[platform] = {
            hook: {
                "count": data["count"],
                "avg_z": round(data["z_total"] / max(data["count"], 1), 2),
            }
            for hook, data in sorted(hooks.items(), key=lambda x: -x[1]["z_total"] / max(x[1]["count"], 1))
            if data["count"] >= 2
        }

    return result


def run_evolution_report() -> dict:
    """Generate and deliver the evolution report. Entry point for cron."""
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)

    data = analyze_evolution(days_recent=7, days_baseline=14)
    message = format_evolution_report(data)

    # Send to Telegram
    from morning_brief import send_telegram
    result = send_telegram(message)

    # Persist to DB for historical tracking
    try:
        conn = _conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evolution_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT (datetime('now')),
                data TEXT
            )
        """)
        conn.execute(
            "INSERT INTO evolution_snapshots (data) VALUES (?)",
            (json.dumps(data, default=str),)
        )
        conn.commit()
    except Exception:
        pass

    return {
        "status": result.get("status", "error"),
        "emerging_count": len(data["emerging"]),
        "hot_count": len(data["hot"]),
        "declining_count": len(data["declining"]),
        "recommendation": data["recommendation"][:100],
    }


if __name__ == "__main__":
    result = run_evolution_report()
    print(json.dumps(result, indent=2, default=str))
