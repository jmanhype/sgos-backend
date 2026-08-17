"""
Reply Engine Router — generates insightful replies to viral posts.
Monitors target accounts, generates scored/grounded reply drafts,
tracks engagement on posted replies.
"""
import sqlite3
import time
import json
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from database import get_connection as get_db
from observability import log

router = APIRouter(prefix="/replies", tags=["replies"])


# ─── Models ────────────────────────────────────────────────────────────────

class AddTarget(BaseModel):
    handle: str
    niche: str = "tech"
    priority: int = 1
    notes: str = ""

class GenerateReply(BaseModel):
    tweet_url: str
    tweet_text: str = ""
    tweet_author: str = ""

class MarkPosted(BaseModel):
    tweet_url: str


# ─── DB Init ───────────────────────────────────────────────────────────────

def init_reply_tables():
    """Create reply engine tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reply_targets (
            handle TEXT PRIMARY KEY,
            niche TEXT DEFAULT 'tech',
            priority INTEGER DEFAULT 1,
            last_checked TEXT,
            notes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS reply_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tweet_url TEXT NOT NULL,
            tweet_author TEXT DEFAULT '',
            tweet_text TEXT DEFAULT '',
            reply_text TEXT NOT NULL,
            score INTEGER DEFAULT 0,
            grounding_score INTEGER DEFAULT 0,
            variant_type TEXT DEFAULT 'value_add',
            dismissed INTEGER DEFAULT 0,
            posted INTEGER DEFAULT 0,
            posted_tweet_url TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            posted_at TEXT
        );
    """)
    conn.commit()


# ─── Seed default targets ──────────────────────────────────────────────────

DEFAULT_TARGETS = [
    ("LinusTech", "hardware", 3),
    ("GamersNexus", "hardware", 3),
    ("EFF", "privacy", 3),
    ("brianroemmele", "AI/tech", 3),
    ("reach_vb", "AI/ML", 2),
    ("hardwareunboxed", "hardware", 2),
    ("TechCrunch", "tech-news", 2),
    ("sama", "AI", 2),
    ("aaboroborov", "AI-tools", 2),
    ("OpenAI", "AI", 2),
    ("AnthropicAI", "AI", 2),
    ("GoogleDevs", "dev-tools", 1),
    ("vercel", "web-dev", 1),
    ("github", "dev-tools", 1),
    ("ycombinator", "startups", 1),
    ("hackernews_", "tech", 1),
]

def seed_targets():
    """Seed default target accounts if table is empty."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM reply_targets").fetchone()[0]
    if count == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO reply_targets (handle, niche, priority) VALUES (?, ?, ?)",
            DEFAULT_TARGETS
        )
        conn.commit()


# ─── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/targets")
async def list_targets():
    """List all monitored target accounts."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM reply_targets ORDER BY priority DESC, handle ASC"
    ).fetchall()
    return {"targets": [dict(r) for r in rows]}


@router.post("/targets")
async def add_target(req: AddTarget):
    """Add a new target account to monitor."""
    handle = req.handle.lstrip("@")
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO reply_targets (handle, niche, priority, notes) VALUES (?, ?, ?, ?)",
        (handle, req.niche, req.priority, req.notes)
    )
    conn.commit()
    log.info("reply.target.added", handle=handle)
    return {"status": "ok", "handle": handle}


@router.delete("/targets/{handle}")
async def remove_target(handle: str):
    """Remove a target account."""
    handle = handle.lstrip("@")
    conn = get_db()
    conn.execute("DELETE FROM reply_targets WHERE handle = ?", (handle,))
    conn.commit()
    log.info("reply.target.removed", handle=handle)
    return {"status": "ok"}


@router.post("/generate")
async def generate_replies(req: GenerateReply):
    """Generate 2-3 reply drafts for a viral tweet."""
    tweet_url = req.tweet_url
    tweet_text = req.tweet_text
    tweet_author = req.tweet_author

    if not tweet_url:
        raise HTTPException(400, "tweet_url is required")

    # Extract author from URL if not provided
    if not tweet_author and "x.com/" in tweet_url:
        parts = tweet_url.split("x.com/")[1].split("/")
        tweet_author = parts[0] if parts else ""

    # Generate reply drafts using the existing LLM pipeline
    drafts = _generate_reply_drafts(tweet_url, tweet_text, tweet_author)

    # Store in DB
    conn = get_db()
    stored = []
    for draft in drafts:
        cursor = conn.execute(
            """INSERT INTO reply_drafts
               (tweet_url, tweet_author, tweet_text, reply_text, score, grounding_score, variant_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tweet_url, tweet_author, tweet_text, draft["text"],
             draft["score"], draft["grounding_score"], draft["variant_type"])
        )
        stored.append({**draft, "id": cursor.lastrowid})
    conn.commit()

    log.info("reply.drafts.generated", tweet_url=tweet_url, count=len(stored))
    return {"drafts": stored, "tweet_url": tweet_url}


@router.get("/drafts")
async def list_drafts(dismissed: bool = False, posted: bool = False):
    """List reply drafts, optionally filtered."""
    conn = get_db()

    conditions = []
    params = []
    if not dismissed:
        conditions.append("dismissed = 0")
    if not posted:
        conditions.append("posted = 0")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM reply_drafts {where} ORDER BY score DESC, created_at DESC"
    ).fetchall()
    return {"drafts": [dict(r) for r in rows]}


@router.post("/drafts/{draft_id}/dismiss")
async def dismiss_draft(draft_id: int):
    """Dismiss a reply draft."""
    conn = get_db()
    conn.execute("UPDATE reply_drafts SET dismissed = 1 WHERE id = ?", (draft_id,))
    conn.commit()
    return {"status": "ok"}


@router.post("/drafts/{draft_id}/posted")
async def mark_posted(draft_id: int, req: MarkPosted):
    """Mark a reply draft as posted and start tracking."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        "UPDATE reply_drafts SET posted = 1, posted_tweet_url = ?, posted_at = ? WHERE id = ?",
        (req.tweet_url, now, draft_id)
    )
    conn.commit()

    # Also add to post_performance for tracking
    _track_posted_reply(req.tweet_url)

    log.info("reply.posted", draft_id=draft_id, tweet_url=req.tweet_url)
    return {"status": "ok"}


@router.get("/posted")
async def list_posted():
    """List all posted replies with engagement data."""
    conn = get_db()

    # Join with post_performance for live metrics
    rows = conn.execute("""
        SELECT rd.*, pp.impressions, pp.likes, pp.retweets, pp.replies,
               pp.quotes, pp.bookmarks, pp.engagement_rate, pp.external_engagement_rate
        FROM reply_drafts rd
        LEFT JOIN post_performance pp ON rd.posted_tweet_url = pp.tweet_url
        WHERE rd.posted = 1
        ORDER BY rd.posted_at DESC
    """).fetchall()
    return {"posted": [dict(r) for r in rows]}


@router.get("/stats")
async def reply_stats():
    """Summary stats for the reply engine."""
    conn = get_db()
    stats = {}
    stats["total_targets"] = conn.execute("SELECT COUNT(*) FROM reply_targets").fetchone()[0]
    stats["total_drafts"] = conn.execute("SELECT COUNT(*) FROM reply_drafts WHERE dismissed=0 AND posted=0").fetchone()[0]
    stats["posted_today"] = conn.execute(
        "SELECT COUNT(*) FROM reply_drafts WHERE posted=1 AND date(posted_at) = date('now')"
    ).fetchone()[0]
    stats["posted_total"] = conn.execute("SELECT COUNT(*) FROM reply_drafts WHERE posted=1").fetchone()[0]
    return stats


# ─── Internal helpers ──────────────────────────────────────────────────────

def _generate_reply_drafts(tweet_url: str, tweet_text: str, tweet_author: str) -> list:
    """Generate 2-3 reply drafts using the LLM."""
    from openai import OpenAI
    import os

    client = OpenAI(
        api_key=os.environ.get("SGOS_LLM_API_KEY", ""),
        base_url=os.environ.get("SGOS_LLM_BASE_URL", "https://llm-k189xkia71r72n1w.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"),
    )

    # Load voice profile if available
    voice_context = ""
    try:
        conn = get_db()
        vp = conn.execute(
            "SELECT name, tone_markers, hook_patterns, common_words FROM voice_profiles LIMIT 1"
        ).fetchone()
        if vp:
            parts = []
            if vp[1]:  # tone_markers
                parts.append(f"Tone: {vp[1][:200]}")
            if vp[2]:  # hook_patterns
                parts.append(f"Hooks: {vp[2][:200]}")
            if vp[3]:  # common_words
                parts.append(f"Words: {vp[3][:200]}")
            voice_context = "\n\nVOICE PROFILE (" + vp[0] + "):\n" + "\n".join(parts)
    except Exception as e:
        print(f"[warn] Voice profile error: {e}")

    prompt = f"""You are writing a reply to a viral tweet on X (Twitter).

TWEET URL: {tweet_url}
TWEET AUTHOR: @{tweet_author}
TWEET TEXT: {tweet_text}

Generate exactly 3 reply drafts. Each reply should be:
- 1-3 sentences (under 280 chars)
- Insightful, not generic ("great post!" is banned)
- Adds a missing piece, contrarian steel-man, or unexpected connection
- Written in a direct, no-BS voice — no hedging, no filler
- Makes the reader think "huh, I didn't consider that"

Each reply MUST be one of these variant types:
1. "value_add" — expands with unique data or angle the author missed
2. "contrarian" — respectfully disagrees with evidence
3. "synthesizer" — connects to a broader pattern the tweet hints at

DO NOT fabricate claims. If you cite a number, it must be verifiable.
DO NOT use exclamation marks excessively.
DO NOT start with "Actually" or "Great point".

Format your response as valid JSON:
[
  {{"text": "reply text here", "variant_type": "value_add", "score": 70, "grounding_score": 90}},
  {{"text": "reply text here", "variant_type": "contrarian", "score": 65, "grounding_score": 85}},
  {{"text": "reply text here", "variant_type": "synthesizer", "score": 60, "grounding_score": 80}}
]

Score = predicted engagement (0-100). Grounding = confidence all claims are factual (0-100).{voice_context}"""

    try:
        response = client.chat.completions.create(
            model=os.environ.get("SGOS_LLM_MODEL", "qwen-latest-series-invite-beta-v34"),
            messages=[{"role": "user", "content": prompt}],
            extra_body={"enable_thinking": True},
            temperature=0.8,
            max_tokens=1000,
        )
        content = response.choices[0].message.content or ""

        # Parse JSON from response
        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            drafts = json.loads(content[start:end])
            return drafts[:3]
        elif content.strip():
            # Fallback: return raw text as a single draft
            return [{"text": content[:280], "variant_type": "value_add", "score": 50, "grounding_score": 70}]
        else:
            return [{"text": "Generation returned empty response.", "variant_type": "value_add", "score": 0, "grounding_score": 0}]

    except Exception as e:
        log.error("reply.generate.failed", error=str(e))
        return [{"text": f"Generation failed: {str(e)[:100]}", "variant_type": "value_add", "score": 0, "grounding_score": 0}]


def _track_posted_reply(tweet_url: str):
    """Add a posted reply to the post_performance table for tracking."""
    import urllib.request

    # Extract tweet ID and author from URL
    if "x.com/" not in tweet_url:
        return

    parts = tweet_url.split("x.com/")[1].split("/")
    author = parts[0] if parts else ""
    tweet_id = parts[-1].split("?")[0] if len(parts) >= 3 else ""

    if not tweet_id or not tweet_id.isdigit():
        return

    # Fetch metrics from FXTwitter
    try:
        fxt_url = f"https://api.fxtwitter.com/{author}/status/{tweet_id}"
        req = urllib.request.Request(fxt_url, headers={"User-Agent": "Mozilla/5.0"})
        res = urllib.request.urlopen(req, timeout=10)
        data = json.loads(res.read())
        tweet_data = data.get("tweet", {})
        impressions = tweet_data.get("views", 0) or 0
        likes = tweet_data.get("likes", 0) or 0
        retweets = tweet_data.get("retweets", 0) or 0
        replies = tweet_data.get("replies", 0) or 0
        quotes = tweet_data.get("quotes", 0) or 0
        bookmarks = tweet_data.get("bookmarks", 0) or 0

        total_engagement = likes + retweets + replies + quotes + bookmarks
        eng_rate = (total_engagement / impressions * 100) if impressions > 0 else 0
        external_eng = likes + retweets + quotes + bookmarks + max(0, replies - 1)
        ext_rate = (external_eng / impressions * 100) if impressions > 0 else 0
    except Exception as e:
        print(f"[warn] FXTwitter API failed: {e}")
        impressions = likes = retweets = replies = quotes = bookmarks = 0
        eng_rate = ext_rate = 0

    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    # Check for existing row first (no UNIQUE constraint on tweet_url)
    existing = conn.execute("SELECT id FROM post_performance WHERE tweet_url = ?", (tweet_url,)).fetchone()
    if existing:
        conn.execute("""
            UPDATE post_performance SET
                tweet_id=?, impressions=?, likes=?, retweets=?, replies=?, quotes=?, bookmarks=?,
                engagement_rate=?, external_engagement_rate=?, thread_length=0, is_pinned=0,
                content_type='reply', tracked_at=?, rechecked_at=?
            WHERE id=?
        """, (tweet_id, impressions, likes, retweets, replies, quotes, bookmarks,
              eng_rate, ext_rate, now, now, existing[0]))
    else:
        conn.execute("""
            INSERT INTO post_performance
            (tweet_url, tweet_id, impressions, likes, retweets, replies, quotes, bookmarks,
             engagement_rate, external_engagement_rate, thread_length, is_pinned,
             content_type, tracked_at, rechecked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'reply', ?, ?)
        """, (tweet_url, tweet_id, impressions, likes, retweets, replies, quotes, bookmarks,
              eng_rate, ext_rate, now, now))
    conn.commit()


# ─── Scout History ───────────────────────────────────────────────────────

def _ensure_scout_columns():
    """Add posted_tweet_url and posted_at columns if missing."""
    conn = get_db()
    try:
        conn.execute("ALTER TABLE scout_history ADD COLUMN posted_tweet_url TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE scout_history ADD COLUMN posted_at TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()


@router.get("/scout")
async def get_scout_history():
    """Get all scout history entries with parsed drafts."""
    _ensure_scout_columns()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM scout_history ORDER BY sent_at DESC"
    ).fetchall()

    entries = []
    for r in rows:
        d = dict(r)
        # Parse the JSON drafts
        try:
            d["drafts_parsed"] = json.loads(d.get("reply_drafts", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["drafts_parsed"] = []
        entries.append(d)

    return {"entries": entries, "total": len(entries)}


class ScoutPosted(BaseModel):
    tweet_url: str


@router.post("/scout/{scout_id}/posted")
async def mark_scout_posted(scout_id: int, body: ScoutPosted):
    """Mark a scout draft as posted and track it."""
    _ensure_scout_columns()
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        "UPDATE scout_history SET posted_tweet_url = ?, posted_at = ? WHERE id = ?",
        (body.tweet_url, now, scout_id)
    )
    conn.commit()

    # Also track in post_performance
    _track_posted_reply(body.tweet_url)

    return {"ok": True, "posted_at": now}


@router.post("/scout/{scout_id}/dismiss")
async def dismiss_scout(scout_id: int):
    """Dismiss a scout entry."""
    conn = get_db()
    conn.execute("UPDATE scout_history SET dismissed = 1 WHERE id = ?", (scout_id,))
    conn.commit()
    return {"ok": True}
