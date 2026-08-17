"""
Telegram Feedback Loop — Closes the recommend → react → learn cycle.

User replies to brief messages with:
  👍 /good /yes  → +weight for that genome pattern
  👎 /skip /no   → -weight for that genome pattern
  🔥 /fire /love → strong +weight (high confidence signal)
  📝 /edit       → user modified it (learn from the diff later)

Each brief opportunity is tagged with its genome_id and hook_type.
When the user reacts, we update a preference model that biases
future scoring toward patterns the user actually publishes.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

from database import get_connection


# ─── Schema ─────────────────────────────────────────────────────────

def init_feedback_tables():
    """Create user preference tables if they don't exist."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS brief_impressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brief_date TEXT NOT NULL,
            opportunity_id INTEGER NOT NULL,
            genome_id TEXT,
            hook_type TEXT,
            structural_pattern TEXT,
            platform TEXT,
            score REAL,
            shown_at TEXT NOT NULL,
            reacted_at TEXT,
            reaction TEXT  -- 'good', 'skip', 'fire', 'edit', NULL
        );
        
        CREATE TABLE IF NOT EXISTS user_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dimension TEXT NOT NULL,      -- 'hook_type', 'structural_pattern', 'platform', 'keyword'
            value TEXT NOT NULL,          -- e.g. 'personal', 'narrative', 'reddit'
            weight REAL DEFAULT 1.0,      -- 1.0 = neutral, >1 = preferred, <1 = avoided
            signal_count INTEGER DEFAULT 0,  -- how many reactions informed this
            updated_at TEXT DEFAULT (datetime('now'))
        );
        
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pref_unique 
            ON user_preferences(dimension, value);
        CREATE INDEX IF NOT EXISTS idx_impression_opp 
            ON brief_impressions(opportunity_id);
    """)
    conn.commit()


# ─── Record what was shown ──────────────────────────────────────────

def record_brief_impressions(opportunities: list[dict], brief_date: str = None):
    """Record which opportunities were shown in today's brief."""
    if not brief_date:
        brief_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    conn = get_connection()
    shown_at = datetime.now(timezone.utc).isoformat()
    
    for opp in opportunities:
        opp_id = opp.get("id", 0)
        genome_id = opp.get("genome_id", "")
        hook_type = opp.get("hook_type", "")
        structural_pattern = opp.get("structural_pattern", "")
        platform = opp.get("platform", "")
        score = opp.get("score", opp.get("adjusted_score", 50))
        
        # Enrich from viral_genomes if missing
        if genome_id and (not hook_type or not platform):
            genome = conn.execute("""
                SELECT hook_type, structural_pattern, platform_signals
                FROM viral_genomes WHERE post_id = ?
            """, (genome_id,)).fetchone()
            if genome:
                hook_type = hook_type or genome["hook_type"]
                structural_pattern = structural_pattern or genome["structural_pattern"]
                if not platform and genome["platform_signals"]:
                    import json as _json
                    try:
                        ps = _json.loads(genome["platform_signals"])
                        platform = ps.get("platform", "")
                    except Exception:
                        pass
        
        conn.execute("""
            INSERT OR IGNORE INTO brief_impressions 
                (brief_date, opportunity_id, genome_id, hook_type, 
                 structural_pattern, platform, score, shown_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (brief_date, opp_id, genome_id, hook_type, structural_pattern, platform, score, shown_at))
    
    conn.commit()
    count = len(opportunities)
    return count


# ─── Process reactions ──────────────────────────────────────────────

REACTION_MAP = {
    "good": 1.0,
    "yes": 1.0,
    "👍": 1.0,
    "fire": 2.0,
    "love": 2.0,
    "🔥": 2.0,
    "skip": -0.5,
    "no": -0.5,
    "bad": -1.0,
    "👎": -1.0,
    "edit": 0.5,
    "📝": 0.5,
}


def process_reaction(opportunity_id: int, reaction: str) -> dict:
    """
    Process a user reaction to a brief opportunity.
    Updates preference weights for the opportunity's genome dimensions.
    """
    reaction = reaction.lower().strip()
    signal = REACTION_MAP.get(reaction)
    
    if signal is None:
        return {"status": "unknown_reaction", "reaction": reaction}
    
    conn = get_connection()
    
    # Find the impression
    row = conn.execute("""
        SELECT * FROM brief_impressions WHERE opportunity_id = ?
        ORDER BY shown_at DESC LIMIT 1
    """, (opportunity_id,)).fetchone()
    
    if not row:
        # Try to find it from pipeline_opportunities directly
        opp = conn.execute("""
            SELECT * FROM pipeline_opportunities WHERE id = ?
        """, (opportunity_id,)).fetchone()
        
        if not opp:
            return {"status": "not_found", "opportunity_id": opportunity_id}
        
        hook_type = ""
        structural_pattern = ""
        platform = ""
        genome_id = opp["genome_id"] if "genome_id" in opp.keys() else ""
    else:
        hook_type = row["hook_type"]
        structural_pattern = row["structural_pattern"]
        platform = row["platform"]
        genome_id = row["genome_id"]
        
        # Mark impression as reacted
        conn.execute("""
            UPDATE brief_impressions SET reaction = ?, reacted_at = ?
            WHERE opportunity_id = ? AND reaction IS NULL
        """, (reaction, datetime.now(timezone.utc).isoformat(), opportunity_id))
    
    # Update preference weights for each dimension
    updates = []
    now = datetime.now(timezone.utc).isoformat()
    
    dimensions = [
        ("hook_type", hook_type),
        ("structural_pattern", structural_pattern),
        ("platform", platform),
    ]
    
    for dim, val in dimensions:
        if not val:
            continue
        
        # Exponential moving average: new_weight = old * (1-alpha) + signal_adjustment * alpha
        alpha = 0.3
        adjustment = signal * 0.1  # Each reaction shifts weight by ±0.1-0.2
        
        existing = conn.execute("""
            SELECT weight, signal_count FROM user_preferences 
            WHERE dimension = ? AND value = ?
        """, (dim, val)).fetchone()
        
        if existing:
            old_weight = existing["weight"]
            new_weight = old_weight * (1 - alpha) + (1.0 + adjustment) * alpha
            new_weight = max(0.1, min(2.0, new_weight))  # Clamp between 0.1 and 2.0
            new_count = existing["signal_count"] + 1
            
            conn.execute("""
                UPDATE user_preferences SET weight = ?, signal_count = ?, updated_at = ?
                WHERE dimension = ? AND value = ?
            """, (round(new_weight, 4), new_count, now, dim, val))
        else:
            initial_weight = 1.0 + adjustment
            conn.execute("""
                INSERT INTO user_preferences (dimension, value, weight, signal_count, updated_at)
                VALUES (?, ?, ?, 1, ?)
            """, (dim, val, round(initial_weight, 4), now))
        
        updates.append({
            "dimension": dim,
            "value": val,
            "signal": signal,
        })
    
    conn.commit()
    
    return {
        "status": "recorded",
        "opportunity_id": opportunity_id,
        "reaction": reaction,
        "signal_strength": signal,
        "preference_updates": updates,
    }


# ─── Get preference-adjusted scores ─────────────────────────────────

def get_preference_weights() -> dict[str, dict[str, float]]:
    """
    Get all user preference weights, organized by dimension.
    Returns: {"hook_type": {"personal": 1.3, "news": 0.7}, ...}
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT dimension, value, weight, signal_count 
        FROM user_preferences 
        WHERE signal_count >= 1
        ORDER BY dimension, weight DESC
    """).fetchall()
    
    weights = {}
    for row in rows:
        dim = row["dimension"]
        if dim not in weights:
            weights[dim] = {}
        weights[dim][row["value"]] = {
            "weight": row["weight"],
            "signals": row["signal_count"],
        }
    
    return weights


def apply_preference_boost(opportunities: list[dict]) -> list[dict]:
    """
    Apply user preference weights to opportunity scores.
    Returns opportunities with adjusted scores, sorted by new score.
    """
    prefs = get_preference_weights()
    
    if not prefs:
        return opportunities  # No preferences yet, return as-is
    
    boosted = []
    for opp in opportunities:
        original_score = opp.get("score", 50)
        boost = 1.0
        
        # Check hook_type preference
        hook = opp.get("hook_type", "")
        if hook and "hook_type" in prefs and hook in prefs["hook_type"]:
            boost *= prefs["hook_type"][hook]["weight"]
        
        # Check structural_pattern preference
        pattern = opp.get("structural_pattern", "")
        if pattern and "structural_pattern" in prefs and pattern in prefs["structural_pattern"]:
            boost *= prefs["structural_pattern"][pattern]["weight"]
        
        # Check platform preference
        platform = opp.get("platform", "")
        if platform and "platform" in prefs and platform in prefs["platform"]:
            boost *= prefs["platform"][platform]["weight"]
        
        opp_copy = dict(opp)
        opp_copy["original_score"] = original_score
        opp_copy["preference_boost"] = round(boost, 3)
        opp_copy["adjusted_score"] = round(original_score * boost, 1)
        boosted.append(opp_copy)
    
    # Re-sort by adjusted score
    boosted.sort(key=lambda x: -x["adjusted_score"])
    return boosted


# ─── Telegram command handler ───────────────────────────────────────

def handle_telegram_feedback(text: str, reply_to_message_id: int = None) -> dict | None:
    """
    Parse Telegram message text for feedback commands.
    Returns a response dict if it's a feedback command, None otherwise.
    """
    text = text.strip().lower()
    
    # Direct reaction commands
    if text.startswith(("/", "👍", "👎", "🔥", "📝")):
        cmd = text.lstrip("/").strip()
        
        # Parse: /good 3 or /fire 5 or 👍 3
        parts = cmd.split()
        reaction = parts[0] if parts else cmd
        opp_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        
        if reaction in REACTION_MAP and opp_id:
            return process_reaction(opp_id, reaction)
    
    # Natural language
    feedback_phrases = {
        "i like": "good",
        "i love": "fire",
        "this is good": "good",
        "this is fire": "fire",
        "not interested": "skip",
        "skip this": "skip",
        "i'd post this": "good",
        "would post": "good",
        "pass": "skip",
        "nah": "skip",
    }
    
    for phrase, reaction in feedback_phrases.items():
        if phrase in text:
            # Try to extract an opportunity ID from context
            # For now, return the reaction type for the caller to resolve
            return {"status": "needs_opportunity_id", "reaction": reaction, "phrase": phrase}
    
    return None


# ─── Stats ──────────────────────────────────────────────────────────

def get_feedback_stats() -> dict:
    """Get summary of feedback loop activity."""
    conn = get_connection()
    
    total_shown = conn.execute("SELECT COUNT(*) as c FROM brief_impressions").fetchone()["c"]
    total_reacted = conn.execute("SELECT COUNT(*) as c FROM brief_impressions WHERE reaction IS NOT NULL").fetchone()["c"]
    
    reaction_breakdown = {}
    rows = conn.execute("""
        SELECT reaction, COUNT(*) as c FROM brief_impressions 
        WHERE reaction IS NOT NULL GROUP BY reaction
    """).fetchall()
    for r in rows:
        reaction_breakdown[r["reaction"]] = r["c"]
    
    prefs = get_preference_weights()
    
    # Top preferences
    top_prefs = []
    for dim, values in prefs.items():
        for val, data in sorted(values.items(), key=lambda x: -x[1]["weight"]):
            if data["signals"] >= 2:
                top_prefs.append({
                    "dimension": dim,
                    "value": val,
                    "weight": data["weight"],
                    "signals": data["signals"],
                    "direction": "🟢" if data["weight"] > 1.05 else "🔴" if data["weight"] < 0.95 else "⚪",
                })
    
    
    response_rate = round(total_reacted / max(total_shown, 1) * 100, 1)
    
    return {
        "total_shown": total_shown,
        "total_reacted": total_reacted,
        "response_rate_pct": response_rate,
        "reaction_breakdown": reaction_breakdown,
        "learned_preferences": len(top_prefs),
        "top_preferences": top_prefs[:10],
        "feedback_loop_active": total_reacted > 0,
    }


# ─── CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    
    init_feedback_tables()
    
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        stats = get_feedback_stats()
        print(json.dumps(stats, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "weights":
        weights = get_preference_weights()
        print(json.dumps(weights, indent=2))
    elif len(sys.argv) > 2 and sys.argv[1] == "react":
        # Usage: python telegram_feedback.py react <opp_id> <reaction>
        opp_id = int(sys.argv[2])
        reaction = sys.argv[3] if len(sys.argv) > 3 else "good"
        result = process_reaction(opp_id, reaction)
        print(json.dumps(result, indent=2))
    else:
        print("Usage:")
        print("  python telegram_feedback.py stats")
        print("  python telegram_feedback.py weights")
        print("  python telegram_feedback.py react <opp_id> <reaction>")
