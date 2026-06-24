"""
Pipeline Alerts — Notify when high-scoring opportunities are generated.

Integrates with existing alert_system for Telegram delivery.
Also exposes pending alerts for SSE/polling from the frontend.

SOLID:
  - Single Responsibility: Only handles alert detection + dispatch.
  - Open/Closed: New notification channels added without changes.
"""
import time
import threading
from datetime import datetime, timezone
from database import get_connection


# Cooldown: don't alert on same opportunity twice
_alert_cooldown: dict[int, float] = {}
_cooldown_lock = threading.Lock()
COOLDOWN_SECONDS = 3600 * 24  # 24 hours


def _init_alert_table():
    """Create pipeline_alerts table if not exists."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER NOT NULL,
            score REAL NOT NULL,
            title TEXT,
            hook TEXT,
            variant_type TEXT,
            alerted_at TEXT NOT NULL,
            notified INTEGER DEFAULT 0,
            dismissed INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_score ON pipeline_alerts(score)")
    conn.commit()


def get_pending_alerts(threshold: float = 75.0, limit: int = 10) -> list[dict]:
    """Get unseen high-scoring opportunities (not yet alerted on)."""
    _init_alert_table()
    conn = get_connection()
    rows = conn.execute("""
        SELECT po.id, po.score, po.title, po.hook, po.variant_type, po.genome_id, po.created_at
        FROM pipeline_opportunities po
        WHERE po.score >= ? AND po.viewed = 0 AND po.dismissed = 0
        AND po.id NOT IN (
            SELECT opportunity_id FROM pipeline_alerts WHERE dismissed = 0
        )
        ORDER BY po.score DESC
        LIMIT ?
    """, (threshold, limit)).fetchall()

    return [dict(r) for r in rows]


def alert_high_score(threshold: float = 75.0, limit: int = 10) -> dict:
    """
    Check for high-scoring unseen opportunities and create alerts.
    Sends Telegram notification if configured.
    Returns summary of alerts created.
    """
    _init_alert_table()

    pending = get_pending_alerts(threshold=threshold, limit=limit)
    if not pending:
        return {"status": "no_alerts", "threshold": threshold, "notified": 0}

    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    created = 0

    for opp in pending:
        # Check cooldown
        with _cooldown_lock:
            last = _alert_cooldown.get(opp["id"], 0)
            if time.time() - last < COOLDOWN_SECONDS:
                continue
            _alert_cooldown[opp["id"]] = time.time()

        conn.execute("""
            INSERT INTO pipeline_alerts (opportunity_id, score, title, hook, variant_type, alerted_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (opp["id"], opp["score"], opp.get("title", ""), opp.get("hook", ""),
              opp.get("variant_type", ""), now))
        created += 1

    conn.commit()

    # Try to send Telegram notification
    notified = 0
    if created > 0:
        notified = _send_telegram_alert(pending[:3])

    return {
        "status": "alerted",
        "threshold": threshold,
        "alerts_created": created,
        "notified": notified,
        "top_opportunities": [
            {
                "id": p["id"],
                "score": p["score"],
                "title": p.get("title", ""),
                "variant_type": p.get("variant_type", ""),
            }
            for p in pending[:5]
        ],
    }


def dismiss_alert(opportunity_id: int) -> dict:
    """Dismiss an alert for an opportunity."""
    _init_alert_table()
    conn = get_connection()
    conn.execute(
        "UPDATE pipeline_alerts SET dismissed = 1 WHERE opportunity_id = ?",
        (opportunity_id,)
    )
    conn.commit()
    return {"status": "dismissed", "opportunity_id": opportunity_id}


def get_alert_history(limit: int = 20) -> list[dict]:
    """Get history of pipeline alerts."""
    _init_alert_table()
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM pipeline_alerts
        ORDER BY alerted_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def _send_telegram_alert(opportunities: list[dict]) -> int:
    """
    Send a Telegram notification for high-scoring opportunities.
    Uses the alert_system's Telegram sender directly (bypasses outlier threshold).
    Returns number of notifications sent.
    """
    if not opportunities:
        return 0

    try:
        from alert_system import get_alert_config, _send_telegram
    except (ImportError, AttributeError):
        return 0

    # Build message
    lines = ["🔥 *SGOS Pipeline Alert*", ""]
    lines.append(f"📊 {len(opportunities)} high-scoring opportunities ready:")
    lines.append("")

    for i, opp in enumerate(opportunities, 1):
        title = opp.get("title", "Untitled")[:60]
        score = opp.get("score", 0)
        vtype = opp.get("variant_type", "post")
        lines.append(f"{i}. *{title}*")
        lines.append(f"   Score: {score:.0f} | Format: {vtype}")
        lines.append("")

    lines.append("Open the Pipeline Dashboard to review →")

    message = "\n".join(lines)

    try:
        config = get_alert_config()
        if not config.get("enabled"):
            return 0
        result = _send_telegram(message, config)
        return 1 if result.get("status") == "sent" else 0
    except Exception:
        return 0
