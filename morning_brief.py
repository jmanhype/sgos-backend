"""
SGOS Morning Brief — Autonomous daily content intelligence engine.
Assembles viral opportunities, meta trends, and formatted content,
then delivers via Telegram.
"""
import json
import math
import os
import sqlite3
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

from database import get_connection
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MAX_MSG_LEN = 4096  # Telegram limit


def _conn():
    return get_connection()


# ─── Data Gathering ──────────────────────────────────────────────────────────

def get_top_opportunities(limit: int = 5, hours: int = 72) -> list[dict]:
    """Get actual top viral posts from the last N hours — real data, real engagement."""
    conn = _conn()
    rows = conn.execute("""
        SELECT id, title, platform, subreddit, author, url,
               score, comment_count, z_score, created_at, content
        FROM posts
        WHERE created_at > datetime('now', ?)
        AND title IS NOT NULL AND title != ''
        ORDER BY z_score DESC
        LIMIT ?
    """, (f"-{hours} hours", limit * 2)).fetchall()
    
    # Enrich with genome analysis when available
    result = []
    for r in rows:
        post = dict(r)
        # Compute a content score (0-100) from z_score + engagement
        z = post.get("z_score") or 0
        raw_score = post.get("score") or 0
        comments = post.get("comment_count") or 0
        
        # Weighted: z-score (50%) + normalized engagement (50%)
        eng_score = min(100, math.log1p(raw_score + comments) * 10)
        z_score_norm = min(100, z * 20)
        content_score = round(z_score_norm * 0.5 + eng_score * 0.5)
        
        post["score"] = content_score
        post["post_title"] = post["title"]
        post["post_score"] = raw_score
        post["opp_title"] = post["title"]
        post["opp_id"] = post["id"]
        
        # Try to get genome analysis for this post
        genome = conn.execute("""
            SELECT hook_type, hook_text, emotional_arc, structural_pattern, key_phrases
            FROM viral_genomes WHERE post_id = ?
        """, (post["id"],)).fetchone()
        
        if genome:
            post["hook"] = genome["hook_text"] or ""
            post["hook_type"] = genome["hook_type"] or ""
            post["emotional_arc"] = genome["emotional_arc"] or ""
            post["structural_pattern"] = genome["structural_pattern"] or ""
        else:
            post["hook"] = ""
            post["hook_type"] = ""
        
        # Skip if z_score is basically zero (not interesting)
        if z < 0.5:
            continue
        
        result.append(post)
    
    return result[:limit]


def get_trending_topics(hours: int = 72, limit: int = 8) -> list[dict]:
    """Extract trending keywords from recent high-performing posts."""
    conn = _conn()
    rows = conn.execute("""
        SELECT title, content, platform, z_score, score, subreddit
        FROM posts
        WHERE ingested_at > datetime('now', ?)
        AND title IS NOT NULL AND title != ''
        ORDER BY z_score DESC
        LIMIT 200
    """, (f"-{hours} hours",)).fetchall()

    # Simple keyword extraction — high-value words from titles
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "and", "but", "or",
        "nor", "not", "so", "yet", "both", "either", "neither", "each",
        "every", "all", "any", "few", "more", "most", "other", "some", "such",
        "no", "only", "own", "same", "than", "too", "very", "just", "because",
        "if", "when", "while", "that", "this", "it", "its", "they", "we",
        "you", "he", "she", "i", "me", "my", "our", "your", "his", "her",
        "what", "which", "who", "whom", "how", "where", "there", "here",
        "then", "about", "up", "out", "new", "get", "got", "like",
    }

    word_scores = defaultdict(float)
    word_counts = Counter()

    for row in rows:
        title = (row["title"] or "").lower()
        # Weight by z-score — viral posts' keywords matter more
        weight = max(1.0, (row["z_score"] or 0) + 1.0)
        for word in title.split():
            clean = word.strip(".,!?;:'\"()-[]{}")
            if len(clean) > 3 and clean not in stopwords:
                word_scores[clean] += weight
                word_counts[clean] += 1

    # Sort by weighted score, filter singletons
    topics = []
    for w, s in sorted(word_scores.items(), key=lambda x: -x[1]):
        if word_counts[w] > 1:
            topics.append({"keyword": w, "score": round(s, 1), "mentions": word_counts[w]})
    topics = topics[:limit]

    return topics


def get_meta_shifts(days: int = 7) -> list[dict]:
    """Detect emerging vs declining topics by comparing recent vs older periods."""
    conn = _conn()
    now = datetime.now(timezone.utc)
    recent_cutoff = (now - timedelta(days=days)).isoformat()
    older_cutoff = (now - timedelta(days=days * 2)).isoformat()

    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "and", "but", "or",
        "not", "that", "this", "it", "its", "they", "we", "you", "he", "she",
        "i", "me", "my", "our", "your", "his", "her", "what", "which", "who",
        "how", "where", "there", "here", "then", "about", "up", "out", "new",
        "get", "got", "like", "just", "more", "also", "been", "only", "very",
    }

    def _extract_freq(cutoff_start, cutoff_end):
        rows = conn.execute("""
            SELECT title, z_score FROM posts
            WHERE ingested_at BETWEEN ? AND ?
            AND title IS NOT NULL AND title != ''
        """, (cutoff_start, cutoff_end)).fetchall()

        word_count = Counter()
        for r in rows:
            for w in (r["title"] or "").lower().split():
                clean = w.strip(".,!?;:'\"()-[]{}")
                if len(clean) > 3 and clean not in stopwords:
                    word_count[clean] += 1
        return word_count

    older = _extract_freq(older_cutoff, recent_cutoff)
    recent = _extract_freq(recent_cutoff, now.isoformat())

    # Normalize
    older_total = max(sum(older.values()), 1)
    recent_total = max(sum(recent.values()), 1)

    shifts = []
    all_words = set(recent.keys()) | set(older.keys())

    for word in all_words:
        recent_pct = recent.get(word, 0) / recent_total
        older_pct = older.get(word, 0) / older_total
        if recent_pct + older_pct < 0.005:
            continue
        change = ((recent_pct - older_pct) / max(older_pct, 0.001)) * 100
        if abs(change) > 20 and (recent.get(word, 0) >= 3 or older.get(word, 0) >= 3):
            shifts.append({
                "keyword": word,
                "direction": "up" if change > 0 else "down",
                "change_pct": round(abs(change)),
                "recent_count": recent.get(word, 0),
                "older_count": older.get(word, 0),
            })

    shifts.sort(key=lambda x: -x["change_pct"])
    return shifts[:6]


def get_outlier_highlights(hours: int = 24, limit: int = 3) -> list[dict]:
    """Get top viral outliers from the last N hours."""
    conn = _conn()
    rows = conn.execute("""
        SELECT id, title, platform, author, score, comment_count,
               z_score, url, subreddit, content
        FROM posts
        WHERE z_score >= 3.0
        AND ingested_at > datetime('now', ?)
        ORDER BY z_score DESC
        LIMIT ?
    """, (f"-{hours} hours", limit)).fetchall()
    return [dict(r) for r in rows]


def format_opportunity(opp: dict) -> str:
    """Format a single viral post opportunity with genome analysis."""
    platform = (opp.get("platform") or "?").upper()
    subreddit = opp.get("subreddit") or ""
    z = opp.get("z_score") or 0
    score = opp.get("post_score") or opp.get("score") or 0
    comments = opp.get("comment_count") or 0
    hook_type = opp.get("hook_type", "")
    date = (opp.get("created_at") or "")[:10]
    
    lines = [
        f"🔥 *TOP #{opp['_rank']}* (z={z:.1f} | score: {opp['score']:.0f}/100)",
        f"\"{opp['post_title'][:80]}\"",
        f"📱 {platform}/{subreddit} | ⬆️ {score:,} | 💬 {comments:,} | 📅 {date}",
    ]

    # Show genome hook if available
    hook = opp.get("hook") or ""
    if hook:
        lines.append(f"🧬 [{hook_type}] _\"{hook[:100]}\"_")
    elif hook_type:
        lines.append(f"🧬 Hook type: {hook_type}")

    if opp.get("url"):
        lines.append(f"🔗 {opp['url']}")

    return "\n".join(lines)


def format_outlier(post: dict) -> str:
    """Format a viral outlier highlight."""
    z = post.get("z_score", 0)
    emoji = "🔥" if z >= 4 else "📈"
    platform = (post.get("platform") or "?").upper()
    title = (post.get("title") or "Untitled")[:80]
    lines = [
        f"{emoji} *z={z:.1f}* — {title}",
        f"   {platform} / {(post.get('subreddit') or 'frontpage')} | ⬆️ {post.get('score', 0):,} | 💬 {post.get('comment_count', 0):,}",
    ]
    return "\n".join(lines)


def format_meta_shift(shift: dict) -> str:
    """Format a meta shift indicator."""
    arrow = "↑" if shift["direction"] == "up" else "↓"
    return f"  {arrow} \"{shift['keyword']}\" {'+' if shift['direction'] == 'up' else '-'}{shift['change_pct']}%"


# ─── Brief Assembly ──────────────────────────────────────────────────────────

def generate_brief() -> str:
    """Assemble the full morning brief message."""
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    sections = [f"☀️ *CREATOR INTELLIGENCE BRIEF — {today}*", ""]

    # Top opportunities (with preference boosts applied)
    opps = get_top_opportunities(limit=5, hours=72)
    
    # Apply user preference weights if available
    try:
        from telegram_feedback import apply_preference_boost, init_feedback_tables, record_brief_impressions
        init_feedback_tables()
        opps = apply_preference_boost(opps)
    except Exception:
        pass
    
    opps = opps[:3]  # Top 3 after boosting
    
    # Record impressions for feedback loop
    try:
        record_brief_impressions(opps, today)
    except Exception:
        pass
    if opps:
        sections.append("━━━ 🎯 TOP OPPORTUNITIES ━━━")
        for i, opp in enumerate(opps, 1):
            opp["_rank"] = i
            sections.append(format_opportunity(opp))
            sections.append("")

    # Viral outliers
    outliers = get_outlier_highlights(hours=24, limit=3)
    if outliers:
        sections.append("━━━ 📡 VIRAL OUTLIERS (24h) ━━━")
        for post in outliers:
            sections.append(format_outlier(post))
        sections.append("")

    # Trending topics
    topics = get_trending_topics(hours=72, limit=6)
    if topics:
        sections.append("━━━ 🧬 TRENDING KEYWORDS (72h) ━━━")
        for t in topics[:6]:
            sections.append(f"  • *{t['keyword']}* (×{t['mentions']}, weight: {t['score']})")
        sections.append("")

    # Meta shifts
    shifts = get_meta_shifts(days=7)
    if shifts:
        up_shifts = [s for s in shifts if s["direction"] == "up"][:3]
        down_shifts = [s for s in shifts if s["direction"] == "down"][:3]

        sections.append("━━━ 📊 META SHIFTS (7d) ━━━")
        if up_shifts:
            sections.append("  🟢 *Emerging:*")
            for s in up_shifts:
                sections.append(format_meta_shift(s))
        if down_shifts:
            sections.append("  🔴 *Declining:*")
            for s in down_shifts:
                sections.append(format_meta_shift(s))
        sections.append("")

    # Recommendation
    sections.append("━━━ 💡 RECOMMENDATION ━━━")
    if opps:
        top = opps[0]
        hook_type = top.get("hook_type", "")
        platform = (top.get("platform") or "").upper()
        z = top.get("z_score", 0)
        
        sections.append(f"  Lead with: \"{(top.get('post_title') or top.get('title') or '')[:60]}\"")
        
        # Dynamic angle based on what's actually working
        if hook_type == "bold_claim":
            sections.append(f"  Angle: Bold contrarian claim — {platform} audiences reward strong positions")
        elif hook_type == "personal":
            sections.append(f"  Angle: Personal story — authentic narrative outperforming on {platform}")
        elif hook_type == "news":
            sections.append(f"  Angle: Breaking news + hot take — add your unique angle to the conversation")
        elif hook_type == "story":
            sections.append(f"  Angle: Story arc — narrative structure with tension and resolution")
        else:
            sections.append(f"  Angle: This {platform} post hit z={z:.1f} — study the hook and replicate the pattern")
    elif outliers:
        top_outlier = outliers[0]
        sections.append(f"  Lead with: \"{(top_outlier.get('title') or '')[:60]}\"")
        sections.append(f"  Angle: This hit z={top_outlier.get('z_score', 0):.1f} — add your take")
    else:
        sections.append("  No high-scoring posts in the last 72h. Next ingest will refresh data.")

    sections.append("")
    sections.append(f"📊 _{len(opps)} opportunities | {len(outliers)} outliers | {len(topics)} trending_")
    sections.append("_React: 👍 good | 👎 skip | 🔥 fire_")
    sections.append("_Commands: /brief | /evolve | /stats_")

    return "\n".join(sections)


# ─── Delivery ────────────────────────────────────────────────────────────────

def send_telegram(message: str, chat_id: str | None = None, parse_mode: str = "Markdown") -> dict:
    """Send message via Telegram Bot API. Supports both Markdown and rich entities."""
    target = chat_id or CHAT_ID
    token = BOT_TOKEN

    if not token or not target:
        return {"status": "error", "reason": "No bot token or chat ID configured"}

    data = urllib.parse.urlencode({
        "chat_id": target,
        "text": message[:MAX_MSG_LEN],
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode()

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return {"status": "sent", "message_id": result.get("result", {}).get("message_id")}
    except Exception as e:
        if "can't parse" in str(e).lower():
            # Retry without formatting
            data = urllib.parse.urlencode({
                "chat_id": target,
                "text": message[:MAX_MSG_LEN],
                "disable_web_page_preview": True,
            }).encode()
            try:
                req = urllib.request.Request(url, data=data, method="POST")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read())
                    return {"status": "sent_plain", "message_id": result.get("result", {}).get("message_id")}
            except Exception as e2:
                return {"status": "error", "error": str(e2)}
        return {"status": "error", "error": str(e)}


def send_rich_message(markdown_content: str, chat_id: str | None = None) -> dict:
    """Send a rich message using sendRichMessage API (Bot API 10.1+)."""
    target = chat_id or CHAT_ID
    token = BOT_TOKEN

    if not token or not target:
        return {"status": "error", "reason": "No bot token or chat ID configured"}

    payload = json.dumps({
        "chat_id": target,
        "rich_message": {
            "markdown": markdown_content[:32768],
        },
    }).encode()

    url = f"https://api.telegram.org/bot{token}/sendRichMessage"
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return {
                "status": "sent",
                "message_id": result.get("result", {}).get("message_id"),
                "api": "sendRichMessage",
            }
    except Exception as e:
        return {"status": "error", "error": str(e), "api": "sendRichMessage"}


def run_brief() -> dict:
    """Generate and deliver the morning brief. Entry point for cron."""
    # Gather data
    opps = get_top_opportunities(limit=5, hours=72)
    
    # Apply user preference weights
    try:
        from telegram_feedback import apply_preference_boost, init_feedback_tables, record_brief_impressions
        init_feedback_tables()
        opps = apply_preference_boost(opps)
    except Exception:
        pass
    opps = opps[:3]
    
    outliers = get_outlier_highlights(hours=24, limit=3)
    topics = get_trending_topics(hours=72, limit=6)
    shifts = get_meta_shifts(days=7)
    
    # Build recommendation
    recommendation = {}
    if opps:
        top = opps[0]
        hook_type = top.get("hook_type", "")
        platform = (top.get("platform") or "").upper()
        z = top.get("z_score", 0)
        recommendation["title"] = top.get("post_title") or top.get("title") or ""
        if hook_type == "bold_claim":
            recommendation["angle"] = f"Bold contrarian claim — {platform} audiences reward strong positions"
        elif hook_type == "personal":
            recommendation["angle"] = f"Personal story — authentic narrative outperforming on {platform}"
        elif hook_type == "news":
            recommendation["angle"] = f"Breaking news + hot take — add your unique angle to the conversation"
        elif hook_type == "story":
            recommendation["angle"] = f"Story arc — narrative structure with tension and resolution"
        else:
            recommendation["angle"] = f"This {platform} post hit z={z:.1f} — study the hook and replicate the pattern"
    
    # Record impressions
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    try:
        record_brief_impressions(opps, today)
    except Exception:
        pass
    
    # Build rich text brief using sendRichMessage API (requires updated Telegram client)
    try:
        from telegram_rich import build_rich_brief_markdown
        md = build_rich_brief_markdown(opps, outliers, topics, shifts, recommendation)
        result = send_rich_message(md)
        mode = "rich_message"
    except Exception as e:
        # Fallback to HTML via sendMessage
        try:
            from telegram_rich import build_brief_html
            html = build_brief_html(opps, outliers, topics, shifts, recommendation)
            result = send_telegram(html, parse_mode="HTML")
            mode = "html"
        except Exception:
            brief = generate_brief()
            result = send_telegram(brief)
            mode = "basic_markdown"
    
    # Log to DB
    try:
        conn = _conn()
        # Ensure mode column exists (migration for existing tables)
        try:
            conn.execute("ALTER TABLE brief_log ADD COLUMN mode TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS brief_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at TEXT DEFAULT (datetime('now')),
                status TEXT,
                mode TEXT,
                opportunities INTEGER,
                outliers INTEGER,
                chunks INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO brief_log (status, mode, opportunities, outliers, chunks) VALUES (?, ?, ?, ?, ?)",
            (result["status"], mode, len(opps), len(outliers), 1)
        )
        conn.commit()
    except Exception:
        pass

    return {
        "status": result["status"],
        "mode": mode,
        "opportunities": len(opps),
        "outliers": len(outliers),
        "message_id": result.get("message_id"),
        "entity_error": result.get("entity_error"),
    }


if __name__ == "__main__":
    import sys
    result = run_brief()
    print(json.dumps(result, indent=2, default=str))
