"""
Strike Engine API — Real-time opportunity detection and reply matching.

Endpoints:
  GET  /strikes              — List strikes (filterable by status)
  GET  /strikes/next         — Get the single best pending strike
  GET  /strikes/stats        — Aggregate stats
  POST /strikes/run          — Manual trigger detection cycle
  GET  /strikes/:id          — Get strike detail
  POST /strikes/:id/post     — Mark as posted (auto-discovers reply URL)
  POST /strikes/:id/dismiss  — Dismiss with reason
  POST /strikes/find-replies — Scan all posted strikes for missing reply URLs
  GET  /strikes/tracking/:id — Get engagement tracking data
"""

import re
import sqlite3
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from database import get_connection as _get_connection

router = APIRouter(prefix="/strikes", tags=["strikes"])

BIRD = "/opt/homebrew/bin/bird"
CHROME_PROFILE = "Default"


@contextmanager
def get_db():
    """Yield the pooled connection (no close — pool manages lifecycle)."""
    yield _get_connection()


def row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows):
    return [dict(r) for r in rows]


# ─── Models ──────────────────────────────────────────────────────────────────


class PostRequest(BaseModel):
    reply_tweet_id: Optional[str] = None
    reply_text: Optional[str] = None


class DismissRequest(BaseModel):
    reason: Optional[str] = "skipped"


# ─── List Strikes ────────────────────────────────────────────────────────────


@router.get("")
def list_strikes(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(20, le=100),
    offset: int = Query(0),
):
    """List strikes, newest first."""
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM strikes WHERE status = ? ORDER BY strike_score DESC, detected_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM strikes ORDER BY strike_score DESC, detected_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM strikes").fetchone()[0]
    return {"strikes": rows_to_list(rows), "total": total, "limit": limit, "offset": offset}


# ─── Next Strike (the one to act on) ────────────────────────────────────────


@router.get("/next")
def next_strike():
    """Get the single highest-scoring pending strike."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM strikes WHERE status = 'pending' ORDER BY strike_score DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {"strike": None, "message": "No pending strikes"}
    return {"strike": row_to_dict(row)}


# ─── Stats (literal routes MUST come before /{strike_id}) ───────────────────


@router.get("/stats")
def strike_stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM strikes").fetchone()[0]
        by_status = {}
        for row in conn.execute(
            "SELECT status, COUNT(*) as cnt FROM strikes GROUP BY status"
        ).fetchall():
            by_status[row["status"]] = row["cnt"]

        posted = conn.execute(
            "SELECT COUNT(*) FROM strikes WHERE status = 'posted'"
        ).fetchone()[0]
        avg_score = conn.execute(
            "SELECT AVG(strike_score) FROM strikes WHERE strike_score > 0"
        ).fetchone()[0] or 0

        top_posted = rows_to_list(
            conn.execute(
                """SELECT * FROM strikes WHERE status = 'posted'
                   ORDER BY reply_likes_24h DESC LIMIT 5"""
            ).fetchall()
        )

        recent_posted = conn.execute(
            """SELECT COUNT(*) FROM strikes WHERE status = 'posted'
               AND posted_at > datetime('now', '-7 days')"""
        ).fetchone()[0]

        avg_likes = conn.execute(
            """SELECT AVG(reply_likes_24h) FROM strikes
               WHERE status = 'posted' AND reply_likes_24h IS NOT NULL"""
        ).fetchone()[0]

        best_row = conn.execute("SELECT MAX(strike_score) FROM strikes").fetchone()
        best_score = best_row[0] if best_row and best_row[0] else 0.0

    pending = by_status.get("pending", 0)
    dismissed = by_status.get("dismissed", 0)
    expired = by_status.get("expired", 0)

    return {
        "total": total,
        "pending": pending,
        "posted": posted,
        "dismissed": dismissed,
        "expired": expired,
        "by_status": by_status,
        "recent_7d": recent_posted,
        "avg_score": round(avg_score, 2),
        "best_score": round(best_score, 2),
        "avg_likes_24h": round(avg_likes, 1) if avg_likes else None,
        "top_posted": top_posted,
    }


# ─── Auto-Reply Discovery ────────────────────────────────────────────────────


def find_my_reply(target_tweet_url: str) -> Optional[str]:
    """Use bird replies to find @StraughterG's reply on a target tweet.
    Returns the reply tweet ID or None."""
    try:
        result = subprocess.run(
            [BIRD, "--chrome-profile", CHROME_PROFILE, "--plain",
             "replies", target_tweet_url],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout

        # Parse plain output — look for @StraughterG reply blocks
        # Format: @handle (Name):\n<text>\ndate: ...\nurl: https://x.com/.../status/<id>
        blocks = output.split("─" * 20)
        for block in blocks:
            if "@StraughterG" in block:
                url_match = re.search(r"url:\s*(https://x\.com/\w+/status/(\d+))", block)
                if url_match:
                    return url_match.group(2)  # tweet ID
        return None
    except Exception as e:
        print(f"[warn] find_my_reply failed for {target_tweet_url}: {e}")
        return None


def find_my_reply_via_search(target_author: str) -> Optional[str]:
    """Fallback: use bird search to find @StraughterG's reply to a specific author.
    Returns the reply tweet ID or None."""
    try:
        result = subprocess.run(
            [BIRD, "--chrome-profile", CHROME_PROFILE, "--plain",
             "search", f"from:StraughterG to:{target_author}"],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout

        # Find the first @StraughterG block and extract its URL
        if "@StraughterG" in output:
            url_match = re.search(r"url:\s*(https://x\.com/\w+/status/(\d+))", output)
            if url_match:
                return url_match.group(2)
        return None
    except Exception as e:
        print(f"[warn] find_my_reply_via_search failed: {e}")
        return None


def auto_discover_reply(strike_id: int):
    """Background task: scan X for the reply to a posted strike."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT target_tweet_url, target_author FROM strikes WHERE id = ?",
            (strike_id,),
        ).fetchone()
        if not row:
            return

        # Try replies first, then search as fallback
        reply_id = find_my_reply(row["target_tweet_url"])
        if not reply_id:
            reply_id = find_my_reply_via_search(row["target_author"])

        if reply_id:
            conn.execute(
                "UPDATE strikes SET reply_tweet_id = ?, updated_at = datetime('now') WHERE id = ?",
                (reply_id, strike_id),
            )
            conn.commit()
            print(f"[auto-discover] Strike #{strike_id}: found reply {reply_id}")
        else:
            print(f"[auto-discover] Strike #{strike_id}: no reply found yet (may be too new)")


# ─── Manual Trigger ──────────────────────────────────────────────────────────


@router.post("/run")
def manual_run():
    """Trigger a detection cycle manually. Returns summary."""
    import subprocess
    import sys

    script = Path.home() / ".hermes" / "scripts" / "sgos-strike-engine.py"
    if not script.exists():
        raise HTTPException(500, "Strike engine script not found")

    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    return {
        "stdout": result.stdout[-2000:] if result.stdout else "",
        "stderr": result.stderr[-2000:] if result.stderr else "",
        "exit_code": result.returncode,
    }


# ─── Strike Detail (parameterized routes AFTER all literal routes) ───────────


@router.get("/{strike_id}")
def get_strike(strike_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM strikes WHERE id = ?", (strike_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Strike not found")
    return {"strike": row_to_dict(row)}


@router.post("/{strike_id}/post")
def post_strike(strike_id: int, req: PostRequest, background_tasks: BackgroundTasks):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM strikes WHERE id = ?", (strike_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Strike not found")

        conn.execute(
            """UPDATE strikes SET status = 'posted', posted_at = datetime('now'),
               reply_tweet_id = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (req.reply_tweet_id, strike_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM strikes WHERE id = ?", (strike_id,)).fetchone()

    # If no reply_tweet_id provided, auto-discover it in the background
    if not req.reply_tweet_id:
        background_tasks.add_task(auto_discover_reply, strike_id)
        return {"strike": row_to_dict(updated), "message": "Marked as posted. Auto-discovering reply URL..."}

    return {"strike": row_to_dict(updated), "message": "Marked as posted"}


@router.post("/{strike_id}/dismiss")
def dismiss_strike(strike_id: int, req: DismissRequest):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM strikes WHERE id = ?", (strike_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Strike not found")

        conn.execute(
            """UPDATE strikes SET status = 'dismissed', dismissed_reason = ?,
               updated_at = datetime('now') WHERE id = ?""",
            (req.reason, strike_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM strikes WHERE id = ?", (strike_id,)).fetchone()
    return {"strike": row_to_dict(updated), "message": "Dismissed"}


# ─── Bulk Reply Discovery ────────────────────────────────────────────────────


@router.post("/find-replies")
def find_replies(background_tasks: BackgroundTasks):
    """Scan all posted strikes missing reply URLs and try to auto-discover them."""
    with get_db() as conn:
        missing = conn.execute("""
            SELECT id FROM strikes
            WHERE status = 'posted' AND (reply_tweet_id IS NULL OR reply_tweet_id = '')
            ORDER BY posted_at DESC LIMIT 20
        """).fetchall()
        ids = [r["id"] for r in missing]

    if not ids:
        return {"message": "All posted strikes have reply URLs", "scanned": 0}

    for sid in ids:
        background_tasks.add_task(auto_discover_reply, sid)

    return {"message": f"Scanning {len(ids)} strikes for reply URLs...", "scanned": len(ids)}


# ─── Tracking ────────────────────────────────────────────────────────────────


@router.get("/tracking/{strike_id}")
def get_tracking(strike_id: int):
    with get_db() as conn:
        row = conn.execute(
            """SELECT id, target_tweet_id, target_tweet_url, reply_tweet_id,
               reply_likes_1h, reply_likes_6h, reply_likes_24h,
               reply_impressions_24h, posted_at, status
               FROM strikes WHERE id = ?""",
            (strike_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Strike not found")
    return {"tracking": row_to_dict(row)}
