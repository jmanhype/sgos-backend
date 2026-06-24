"""
SGOS Backend - Outlier Alert System
Sends Telegram notifications when viral outliers are detected (z_score >= threshold).
Uses Hermes gateway or direct Telegram Bot API.
"""
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = os.environ.get("SGOS_DB_PATH", str(Path(__file__).parent / "sgos.db"))


def get_alert_config() -> dict:
    """Load alert config from env or Hermes config."""
    config = {
        "enabled": False,
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "threshold": float(os.environ.get("OUTLIER_ALERT_THRESHOLD", "3.0")),
        "cooldown_hours": int(os.environ.get("ALERT_COOLDOWN_HOURS", "6")),
    }

    # Try Hermes config
    if not config["chat_id"]:
        try:
            import yaml
            hermes_config = os.path.expanduser("~/.hermes/config.yaml")
            if os.path.exists(hermes_config):
                with open(hermes_config) as f:
                    hc = yaml.safe_load(f)
                tg = hc.get("telegram", {})
                config["chat_id"] = tg.get("chat_id", "")
                config["bot_token"] = tg.get("bot_token", "")
        except Exception:
            pass

    if config["chat_id"] and config["bot_token"]:
        config["enabled"] = True

    return config


def send_outlier_alert(post: dict, method: str = "telegram") -> dict:
    """
    Send an alert for a viral outlier post.

    Args:
        post: Post dict with title, platform, score, z_score, url, content
        method: "telegram" or "hermes"

    Returns:
        Dict with status and any error info
    """
    config = get_alert_config()

    z_score = post.get("z_score", 0)
    if z_score < config["threshold"]:
        return {"status": "below_threshold", "z_score": z_score, "threshold": config["threshold"]}

    # Check cooldown
    if _is_in_cooldown(post, config["cooldown_hours"]):
        return {"status": "cooldown", "post_id": post.get("id", "")}

    # Format alert message
    message = _format_alert(post)

    if method == "telegram" and config["enabled"]:
        return _send_telegram(message, config)
    elif method == "hermes":
        return _send_hermes(message)
    else:
        # Save to alerts table as fallback
        return _save_local_alert(post, message)


def _format_alert(post: dict) -> str:
    """Format a viral outlier alert message."""
    z = post.get("z_score", 0)
    emoji = "🔥" if z >= 5 else "📈" if z >= 3 else "📊"

    title = post.get("title", "Untitled")[:100]
    platform = post.get("platform", "?").upper()
    score = post.get("score", 0)
    comments = post.get("comment_count", 0)
    url = post.get("url", "")
    author = post.get("author", "unknown")

    content = post.get("content", "")
    preview = content[:200].replace("\n", " ") if content else ""

    lines = [
        f"{emoji} *VIRAL OUTLIER DETECTED*",
        f"",
        f"*{title}*",
        f"",
        f"📊 Z-Score: `{z:.1f}`x above average",
        f"📱 Platform: {platform}",
        f"👤 Author: {author}",
        f"⬆️ Score: {score:,} | 💬 Comments: {comments:,}",
    ]

    if preview:
        lines.append(f"")
        lines.append(f"_{preview}..._")

    if url:
        lines.append(f"")
        lines.append(f"🔗 {url}")

    lines.append(f"")
    lines.append(f"💡 _Run repurpose to turn this into content_")

    return "\n".join(lines)


def _send_telegram(message: str, config: dict) -> dict:
    """Send alert via Telegram Bot API."""
    import urllib.request
    import urllib.parse

    bot_token = config["bot_token"]
    chat_id = config["chat_id"]

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }).encode()

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            _record_alert_sent(message)
            return {"status": "sent", "message_id": result.get("result", {}).get("message_id")}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _send_hermes(message: str) -> dict:
    """Send alert via Hermes send_message (if available)."""
    try:
        result = subprocess.run(
            ["hermes", "send", "--message", message[:500]],
            capture_output=True, text=True, timeout=10
        )
        _record_alert_sent(message)
        return {"status": "sent", "method": "hermes"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _save_local_alert(post: dict, message: str) -> dict:
    """Save alert to local DB when no external channel available."""
    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT,
                z_score REAL,
                platform TEXT,
                message TEXT,
                sent INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "INSERT INTO alert_log (post_id, z_score, platform, message, sent) VALUES (?, ?, ?, ?, 0)",
            (post.get("id", ""), post.get("z_score", 0), post.get("platform", ""), message)
        )
        conn.commit()
        return {"status": "saved_local", "post_id": post.get("id", "")}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()


def _is_in_cooldown(post: dict, cooldown_hours: int) -> bool:
    """Check if this post was already alerted recently."""
    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT,
                z_score REAL,
                platform TEXT,
                message TEXT,
                sent INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        row = conn.execute(
            """SELECT created_at FROM alert_log
               WHERE post_id = ? AND created_at > datetime('now', ?)
               LIMIT 1""",
            (post.get("id", ""), f"-{cooldown_hours} hours")
        ).fetchone()

        return row is not None
    except Exception:
        return False
    finally:
        conn.close()


def _record_alert_sent(message: str):
    """Mark that an alert was sent (for cooldown tracking)."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("CREATE TABLE IF NOT EXISTS alert_log (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT, sent INTEGER DEFAULT 0, created_at TEXT)")
        conn.execute(
            "INSERT INTO alert_log (message, sent, created_at) VALUES (?, 1, ?)",
            (message[:500], datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def check_and_alert_outliers(threshold: float = 3.0, limit: int = 5, hours: int = 24) -> dict:
    """
    Check for new outliers and send alerts for any above threshold.
    Called by the daily cron pipeline.
    """
    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT * FROM posts
        WHERE z_score >= ?
        AND ingested_at > datetime('now', ?)
        ORDER BY z_score DESC
        LIMIT ?
    """, (threshold, f"-{hours} hours", limit)).fetchall()

    conn.close()

    alerts_sent = 0
    alerts_saved = 0
    results = []

    for row in rows:
        post = dict(row)
        result = send_outlier_alert(post)
        results.append(result)

        if result["status"] == "sent":
            alerts_sent += 1
        elif result["status"] == "saved_local":
            alerts_saved += 1

    return {
        "checked": len(rows),
        "alerts_sent": alerts_sent,
        "alerts_saved": alerts_saved,
        "results": results,
    }


def get_alert_history(limit: int = 20) -> list[dict]:
    """Get recent alert history."""
    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT,
                z_score REAL,
                platform TEXT,
                message TEXT,
                sent INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        rows = conn.execute(
            "SELECT * FROM alert_log ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()
