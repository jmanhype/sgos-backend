"""
SGOS Telegram Bot — Command handler for feedback loop + on-demand briefs.
Runs as a long-lived process, polling for messages from @StraughterGuthrieOS_bot.
"""
import json
import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TG-BOT] %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "643905554")

if not BOT_TOKEN:
    log.error("TELEGRAM_BOT_TOKEN not set!")
    sys.exit(1)

# ─── Message Handler ───────────────────────────────────────────────

def handle_message(text: str, chat_id: str, message_id: int = None, reply_to_id: int = None) -> str | None:
    """
    Process incoming Telegram message. Returns response text or None.
    
    Commands:
      /brief          — Generate and send morning brief now
      /evolve         — Generate evolution report now
      /stats          — Feedback loop statistics
      /ingest         — Trigger ingestion cycle
      /good [N]       — React 'good' to opportunity N (or last brief)
      /fire [N]       — React 'fire' to opportunity N
      /skip [N]       — React 'skip' to opportunity N
      👍 / 🔥 / 👎   — Emoji reactions (same as above)
    """
    text = text.strip()
    
    # ─── Command handlers ────────────────────────────────────
    if text.startswith("/brief"):
        try:
            from morning_brief import generate_brief
            brief = generate_brief()
            return brief
        except Exception as e:
            return f"❌ Brief generation failed: {e}"
    
    elif text.startswith("/evolve"):
        try:
            from genome_evolution import analyze_evolution, format_evolution_report
            data = analyze_evolution()
            report = format_evolution_report(data)
            return report
        except Exception as e:
            return f"❌ Evolution report failed: {e}"
    
    elif text.startswith("/stats"):
        try:
            from telegram_feedback import get_feedback_stats
            stats = get_feedback_stats()
            lines = [
                "📊 *FEEDBACK LOOP STATUS*",
                f"  Shown: {stats['total_shown']} opportunities",
                f"  Reacted: {stats['total_reacted']} ({stats['response_rate_pct']}%)",
                f"  Learned preferences: {stats['learned_preferences']}",
                f"  Loop active: {'✅' if stats['feedback_loop_active'] else '⏳ waiting'}",
            ]
            if stats.get("top_preferences"):
                lines.append("")
                lines.append("*Your taste profile:*")
                for p in stats["top_preferences"][:5]:
                    lines.append(f"  {p['direction']} {p['dimension']}: {p['value']} ({p['weight']})")
            if stats.get("reaction_breakdown"):
                lines.append("")
                rb = stats["reaction_breakdown"]
                lines.append(f"  Reactions: " + " | ".join(f"{k}: {v}" for k, v in rb.items()))
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Stats failed: {e}"
    
    elif text.startswith("/ingest"):
        try:
            from reddit_ingest import ingest_all
            # Run in background thread to not block
            import threading
            def run():
                result = ingest_all()
                # Send completion notification
                send_telegram(
                    f"✅ Ingestion complete\n"
                    f"  Added: {result['total_added']}\n"
                    f"  Updated: {result['total_updated']}"
                )
            threading.Thread(target=run, daemon=True).start()
            return "🔄 Ingestion started... I'll notify you when it's done."
        except Exception as e:
            return f"❌ Ingestion failed: {e}"
    
    elif text.startswith("/health"):
        try:
            import requests
            r = requests.get("http://localhost:8420/health", timeout=5)
            data = r.json()
            return (
                f"💚 *SGOS HEALTH*\n"
                f"  Posts: {data['total_posts']}\n"
                f"  Outliers (24h): {data['outliers_24h']}\n"
                f"  Last ingest: {data.get('last_ingest', 'never')[:19]}"
            )
        except Exception as e:
            return f"❌ Health check failed: {e}"
    
    elif text.startswith("/track"):
        # /track https://x.com/user/status/12345
        parts = text.split()
        if len(parts) < 2:
            return "📎 Usage: /track <tweet_url>\n\nPaste a tweet URL to start tracking its performance."
        url = parts[1]
        opp_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        try:
            from tracker import track_tweet
            result = track_tweet(url, opportunity_id=opp_id)
            if result["status"] == "tracked":
                return (
                    f"📊 *TRACKING STARTED*\n\n"
                    f"  Impressions: {result['impressions']:,}\n"
                    f"  Likes: {result['likes']}\n"
                    f"  RTs: {result['retweets']}\n"
                    f"  Replies: {result['replies']}\n"
                    f"  Engagement: {result['engagement_rate']}%\n\n"
                    f"Will re-check at 24h, 48h, 7d."
                )
            elif result["status"] == "already_tracked":
                return f"ℹ️ Already tracked (record #{result['id']})"
            else:
                return f"❌ Track failed: {result.get('error', 'unknown')}"
        except Exception as e:
            return f"❌ Track failed: {e}"
    
    elif text.startswith("/perf"):
        try:
            from tracker import get_performance_summary
            s = get_performance_summary()
            if s["total_tracked"] == 0:
                return "📊 No tweets tracked yet.\n\nUse /track <tweet_url> to start."
            lines = [
                f"📊 *PERFORMANCE SUMMARY*",
                f"",
                f"  Tracked: {s['total_tracked']} tweets",
                f"  Avg engagement: {s['avg_engagement_rate']}%",
                f"  Avg impressions: {s['avg_impressions']:,.0f}",
                f"  Total likes: {s['total_likes']}",
            ]
            if s.get("best_tweet"):
                b = s["best_tweet"]
                lines.append(f"")
                lines.append(f"  🏆 Best: {b['engagement_rate']}% ({b['impressions']} views)")
            if s.get("by_hook_type"):
                lines.append(f"")
                lines.append(f"  *By hook type:*")
                for h in s["by_hook_type"][:5]:
                    lines.append(f"    {h['hook']}: {h['avg_engagement']}% ({h['count']} posts)")
            train_status = "ready" if s["ready_to_train"] else f"{s['total_tracked']}/10 needed"
            train_icon = "✅" if s["ready_to_train"] else "⏳"
            lines.append(f"")
            lines.append(f"  {train_icon} Scorer training: {train_status}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Perf summary failed: {e}"
    
    # ─── Feedback reactions ──────────────────────────────────
    reaction = None
    opp_id = None
    
    # Parse: /fire 592 or /good or 👍 or 🔥 3
    lower = text.lower()
    
    # Emoji reactions
    emoji_map = {"👍": "good", "🔥": "fire", "👎": "skip", "❤️": "fire"}
    if lower in emoji_map:
        reaction = emoji_map[lower]
        # Try to find the most recent unreacted impression
        opp_id = _get_last_unreacted_opportunity()
    elif text.startswith("👍"):
        reaction = "good"
        parts = lower.replace("👍", "").strip().split()
        opp_id = int(parts[0]) if parts and parts[0].isdigit() else _get_last_unreacted_opportunity()
    elif text.startswith("🔥"):
        reaction = "fire"
        parts = lower.replace("🔥", "").strip().split()
        opp_id = int(parts[0]) if parts and parts[0].isdigit() else _get_last_unreacted_opportunity()
    elif text.startswith("👎"):
        reaction = "skip"
        parts = lower.replace("👎", "").strip().split()
        opp_id = int(parts[0]) if parts and parts[0].isdigit() else _get_last_unreacted_opportunity()
    
    # Text commands: /good 592, /fire, /skip 3
    elif lower.startswith("/good") or lower.startswith("/yes"):
        reaction = "good"
        parts = lower.split()
        opp_id = int(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else _get_last_unreacted_opportunity()
    elif lower.startswith("/fire") or lower.startswith("/love"):
        reaction = "fire"
        parts = lower.split()
        opp_id = int(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else _get_last_unreacted_opportunity()
    elif lower.startswith("/skip") or lower.startswith("/no") or lower.startswith("/pass"):
        reaction = "skip"
        parts = lower.split()
        opp_id = int(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else _get_last_unreacted_opportunity()
    
    if reaction and opp_id:
        from telegram_feedback import process_reaction
        result = process_reaction(opp_id, reaction)
        
        if result.get("status") == "recorded":
            emoji = {"good": "👍", "fire": "🔥", "skip": "👎"}.get(reaction, "✅")
            prefs = result.get("preference_updates", [])
            lines = [f"{emoji} Recorded: *{reaction}* on opportunity #{opp_id}"]
            if prefs:
                for p in prefs:
                    lines.append(f"  → {p['dimension']}: {p.get('value', '?')}")
            return "\n".join(lines)
        else:
            return f"⚠️ Could not record reaction: {result.get('status', 'unknown')}"
    
    elif reaction and not opp_id:
        return f"⚠️ No opportunity found to react to. Try: /{reaction} <id>"
    
    # ─── Help ─────────────────────────────────────────────────
    elif lower.startswith("/help") or lower.startswith("/start"):
        return (
            "🤖 *SGOS COMMANDS*\n\n"
            "📋 */brief* — Generate morning brief now\n"
            "🧬 */evolve* — Genome evolution report\n"
            "📊 */stats* — Feedback loop stats\n"
            "📎 */track <url>* — Track a tweet's performance\n"
            "📈 */perf* — Performance summary\n"
            "🔄 */ingest* — Trigger data ingestion\n"
            "💚 */health* — System status\n\n"
            "*Reactions:*\n"
            "👍 /good — I like this\n"
            "🔥 /fire — Love it, more like this\n"
            "👎 /skip — Not interested\n\n"
            "_Example: /fire 592 or just 👍_"
        )
    
    return None  # Unrecognized message


def _get_last_unreacted_opportunity() -> int | None:
    """Get the most recent unreacted opportunity from today's brief."""
    try:
        from database import get_connection
        conn = get_connection()
        row = conn.execute("""
            SELECT opportunity_id FROM brief_impressions 
            WHERE reaction IS NULL 
            ORDER BY shown_at DESC LIMIT 1
        """).fetchone()
        return row["opportunity_id"] if row else None
    except Exception:
        return None


# ─── Send ──────────────────────────────────────────────────────────

def send_telegram(message: str, chat_id: str = None):
    """Send a message via Telegram API."""
    import requests
    target = chat_id or CHAT_ID
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    # Split long messages
    chunks = []
    if len(message) > 4000:
        lines = message.split("\n")
        chunk = []
        chunk_len = 0
        for line in lines:
            if chunk_len + len(line) + 1 > 3900:
                chunks.append("\n".join(chunk))
                chunk = [line]
                chunk_len = len(line)
            else:
                chunk.append(line)
                chunk_len += len(line) + 1
        if chunk:
            chunks.append("\n".join(chunk))
    else:
        chunks = [message]
    
    results = []
    for chunk in chunks:
        try:
            resp = requests.post(url, json={
                "chat_id": target,
                "text": chunk,
                "parse_mode": "Markdown",
            }, timeout=10)
            results.append(resp.json())
        except Exception as e:
            log.error(f"Send failed: {e}")
            results.append({"ok": False, "error": str(e)})
    
    return results


# ─── Polling Loop ──────────────────────────────────────────────────

def poll_messages():
    """Long-poll Telegram for new messages."""
    import requests
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    offset = 0
    
    # Get last update offset to skip old messages
    try:
        resp = requests.get(url, params={"timeout": 0}, timeout=5)
        data = resp.json()
        if data.get("result"):
            offset = data["result"][-1]["update_id"] + 1
    except Exception:
        pass
    
    log.info(f"Bot polling started (offset={offset}). Waiting for messages...")
    
    while True:
        try:
            resp = requests.get(url, params={
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message"],
            }, timeout=35)
            
            data = resp.json()
            if not data.get("ok"):
                log.warning(f"API error: {data}")
                continue
            
            updates = data.get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                
                if not msg.get("text"):
                    continue
                
                # Security: only process messages from allowed user
                from_user = str(msg.get("from", {}).get("id", ""))
                if from_user != CHAT_ID:
                    log.info(f"Ignoring message from unauthorized user: {from_user}")
                    continue
                
                text = msg["text"]
                chat_id = str(msg["chat"]["id"])
                msg_id = msg.get("message_id")
                reply_to = msg.get("reply_to_message", {}).get("message_id")
                
                log.info(f"Message from {from_user}: {text[:50]}")
                
                # Handle the message
                response = handle_message(text, chat_id, msg_id, reply_to)
                
                if response:
                    send_telegram(response, chat_id=chat_id)
        
        except KeyboardInterrupt:
            log.info("Bot stopped.")
            break
        except Exception as e:
            log.error(f"Poll error: {e}")
            import time
            time.sleep(5)


if __name__ == "__main__":
    poll_messages()
