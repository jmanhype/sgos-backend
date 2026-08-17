"""
Performance Tracker — Closes the feedback loop.

Tracks real tweet metrics via FXTwitter API (free, no auth).
Feeds engagement data back into the pipeline scorer.

Flow:
  1. User marks a draft as "Published" and provides tweet URL
  2. Tracker fetches metrics immediately
  3. Re-checks at 24h, 48h, 7d automatically
  4. Engagement data flows into scorer retraining
"""
import json
import os
import re
import sqlite3
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from database import get_connection

# FXTwitter API — free, no auth, works for all public tweets
FXTWITTER_API = "https://api.fxtwitter.com"

# Re-check schedule (hours after initial post)
RECHECK_HOURS = [24, 48, 168]  # 1 day, 2 days, 1 week


def parse_tweet_url(url: str) -> tuple[str, str]:
    """Extract username and tweet_id from a tweet URL."""
    # Handle x.com and twitter.com
    m = re.search(r'(?:x\.com|twitter\.com)/(\w+)/status/(\d+)', url)
    if not m:
        raise ValueError(f"Invalid tweet URL: {url}")
    return m.group(1), m.group(2)


def fetch_tweet_metrics(username: str, tweet_id: str) -> dict:
    """Fetch metrics for a single tweet via FXTwitter API."""
    url = f"{FXTWITTER_API}/{username}/status/{tweet_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "SGOS-Tracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read())
    except Exception as e:
        return {"error": str(e)}

    tweet = data.get("tweet", {})
    if not tweet:
        return {"error": "Tweet not found"}

    likes = tweet.get("likes", 0)
    retweets = tweet.get("retweets", 0)
    replies = tweet.get("replies", 0)
    quotes = tweet.get("quotes", 0)
    views = tweet.get("views", 0) or 0

    # Engagement rate = (likes + retweets + replies + quotes) / views * 100
    interactions = likes + retweets + replies + quotes
    engagement_rate = (interactions / views * 100) if views > 0 else 0.0

    return {
        "author": tweet.get("author", {}).get("screen_name", username),
        "text": tweet.get("text", "")[:500],
        "posted_at": tweet.get("created_at", ""),
        "impressions": views,
        "likes": likes,
        "retweets": retweets,
        "replies": replies,
        "quotes": quotes,
        "engagement_rate": round(engagement_rate, 2),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def track_tweet(
    tweet_url: str,
    opportunity_id: Optional[int] = None,
    genome_id: Optional[str] = None,
    variant_type: Optional[str] = None,
    predicted_score: Optional[float] = None,
    notes: str = "",
) -> dict:
    """
    Track a tweet's performance. Stores metrics and schedules re-checks.

    Returns the stored record.
    """
    username, tweet_id = parse_tweet_url(tweet_url)
    metrics = fetch_tweet_metrics(username, tweet_id)

    if "error" in metrics:
        return {"status": "error", "error": metrics["error"]}

    conn = get_connection()

    # Check if already tracked
    existing = conn.execute(
        "SELECT id FROM post_performance WHERE tweet_id = ?", (tweet_id,)
    ).fetchone()
    if existing:
        return {"status": "already_tracked", "id": existing["id"]}

    # Look up genome info from opportunity if linked
    source_hook = ""
    if opportunity_id:
        opp = conn.execute(
            "SELECT genome_id, variant_type, score, hook FROM pipeline_opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        if opp:
            genome_id = genome_id or opp["genome_id"]
            variant_type = variant_type or opp["variant_type"]
            predicted_score = predicted_score or opp["score"]

    if genome_id:
        genome = conn.execute(
            "SELECT hook_type FROM viral_genomes WHERE post_id = ?", (genome_id,)
        ).fetchone()
        if genome:
            source_hook = genome["hook_type"]

    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO post_performance
           (opportunity_id, genome_id, variant_type, predicted_score,
            tweet_url, tweet_id, impressions, likes, retweets, replies, quotes,
            engagement_rate, tracked_at, source_genome_hook, posted_at, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            opportunity_id, genome_id, variant_type, predicted_score,
            tweet_url, tweet_id,
            metrics["impressions"], metrics["likes"], metrics["retweets"],
            metrics["replies"], metrics["quotes"],
            metrics["engagement_rate"], now,
            source_hook, metrics.get("posted_at", ""), notes,
        ),
    )
    conn.commit()
    record_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    return {
        "status": "tracked",
        "id": record_id,
        "tweet_id": tweet_id,
        "impressions": metrics["impressions"],
        "likes": metrics["likes"],
        "retweets": metrics["retweets"],
        "replies": metrics["replies"],
        "engagement_rate": metrics["engagement_rate"],
    }


def refresh_tweet(record_id: int) -> dict:
    """Re-fetch metrics for an existing tracked tweet."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM post_performance WHERE id = ?", (record_id,)
    ).fetchone()
    if not row:
        return {"status": "error", "error": "Record not found"}

    username, tweet_id = parse_tweet_url(row["tweet_url"])
    metrics = fetch_tweet_metrics(username, tweet_id)
    if "error" in metrics:
        return {"status": "error", "error": metrics["error"]}

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE post_performance SET
           impressions=?, likes=?, retweets=?, replies=?, quotes=?,
           engagement_rate=?, rechecked_at=?
           WHERE id=?""",
        (
            metrics["impressions"], metrics["likes"], metrics["retweets"],
            metrics["replies"], metrics["quotes"],
            metrics["engagement_rate"], now, record_id,
        ),
    )
    conn.commit()

    return {
        "status": "refreshed",
        "id": record_id,
        "impressions": metrics["impressions"],
        "likes": metrics["likes"],
        "engagement_rate": metrics["engagement_rate"],
    }


def refresh_due_tweets() -> dict:
    """
    Find and refresh all tweets that are due for a re-check.
    Called by the scheduler periodically.
    """
    conn = get_connection()

    now = datetime.now(timezone.utc)
    refreshed = 0
    results = []

    rows = conn.execute(
        "SELECT * FROM post_performance WHERE tweet_id IS NOT NULL"
    ).fetchall()

    for row in rows:
        if not row["tracked_at"]:
            continue  # Skip records with no tracked_at timestamp
        tracked = datetime.fromisoformat(row["tracked_at"].replace("Z", "+00:00"))
        if tracked.tzinfo is None:
            tracked = tracked.replace(tzinfo=timezone.utc)
        age_hours = (now - tracked).total_seconds() / 3600
        last_check = row["rechecked_at"]
        if last_check:
            last_check_dt = datetime.fromisoformat(last_check.replace("Z", "+00:00"))
            if last_check_dt.tzinfo is None:
                last_check_dt = last_check_dt.replace(tzinfo=timezone.utc)
            hours_since_check = (now - last_check_dt).total_seconds() / 3600
        else:
            hours_since_check = age_hours

        # Check if any recheck window is due
        due = False
        for window in RECHECK_HOURS:
            if age_hours >= window and (not last_check or hours_since_check >= 12):
                due = True
                break

        if due:
            result = refresh_tweet(row["id"])
            results.append(result)
            if result["status"] == "refreshed":
                refreshed += 1

    return {"refreshed": refreshed, "results": results}


def get_performance_summary() -> dict:
    """Get summary stats for the feedback loop."""
    conn = get_connection()

    total = conn.execute("SELECT COUNT(*) FROM post_performance").fetchone()[0]
    if total == 0:
        return {"total_tracked": 0, "message": "No tweets tracked yet"}

    avg_engagement = conn.execute(
        "SELECT AVG(engagement_rate) FROM post_performance WHERE engagement_rate > 0"
    ).fetchone()[0] or 0

    avg_impressions = conn.execute(
        "SELECT AVG(impressions) FROM post_performance WHERE impressions > 0"
    ).fetchone()[0] or 0

    total_likes = conn.execute(
        "SELECT SUM(likes) FROM post_performance"
    ).fetchone()[0] or 0

    # Best performer
    best = conn.execute(
        "SELECT * FROM post_performance ORDER BY engagement_rate DESC LIMIT 1"
    ).fetchone()

    # Performance by hook type
    hook_stats = conn.execute("""
        SELECT source_genome_hook,
               COUNT(*) as count,
               AVG(engagement_rate) as avg_engagement,
               AVG(impressions) as avg_impressions
        FROM post_performance
        WHERE source_genome_hook != ''
        GROUP BY source_genome_hook
        ORDER BY avg_engagement DESC
    """).fetchall()

    # Performance by variant type
    variant_stats = conn.execute("""
        SELECT variant_type,
               COUNT(*) as count,
               AVG(engagement_rate) as avg_engagement
        FROM post_performance
        WHERE variant_type IS NOT NULL
        GROUP BY variant_type
        ORDER BY avg_engagement DESC
    """).fetchall()

    return {
        "total_tracked": total,
        "avg_engagement_rate": round(avg_engagement, 2),
        "avg_impressions": round(avg_impressions, 0),
        "total_likes": total_likes,
        "best_tweet": {
            "url": best["tweet_url"],
            "engagement_rate": best["engagement_rate"],
            "impressions": best["impressions"],
            "likes": best["likes"],
        } if best else None,
        "by_hook_type": [
            {
                "hook": r["source_genome_hook"],
                "count": r["count"],
                "avg_engagement": round(r["avg_engagement"], 2),
                "avg_impressions": round(r["avg_impressions"], 0),
            }
            for r in hook_stats
        ],
        "by_variant": [
            {
                "variant": r["variant_type"],
                "count": r["count"],
                "avg_engagement": round(r["avg_engagement"], 2),
            }
            for r in variant_stats
        ],
        "ready_to_train": total >= 10,
    }


def train_scorer_from_performance() -> dict:
    """
    Retrain the pipeline scorer weights based on actual performance data.

    Analyzes which genome features (hook_type, variant_type, structural_pattern)
    correlate with higher engagement, then adjusts pipeline scoring weights.
    """
    summary = get_performance_summary()
    if not summary.get("ready_to_train"):
        return {
            "status": "not_ready",
            "tracked": summary["total_tracked"],
            "needed": 10,
        }

    conn = get_connection()

    # Get all performance records with genome data
    records = conn.execute("""
        SELECT pp.*, vg.hook_type, vg.structural_pattern, vg.engagement_score as genome_engagement
        FROM post_performance pp
        LEFT JOIN viral_genomes vg ON pp.genome_id = vg.post_id
        WHERE pp.engagement_rate > 0
    """).fetchall()

    if len(records) < 10:
        return {"status": "not_ready", "tracked": len(records), "needed": 10}

    # Calculate feature importance based on actual engagement
    hook_performance = {}
    pattern_performance = {}
    variant_performance = {}

    for r in records:
        eng = r["engagement_rate"]

        # Hook type performance
        hook = r["hook_type"] or r["source_genome_hook"] or "unknown"
        if hook not in hook_performance:
            hook_performance[hook] = []
        hook_performance[hook].append(eng)

        # Structural pattern performance
        pattern = r["structural_pattern"] or "unknown"
        if pattern not in pattern_performance:
            pattern_performance[pattern] = []
        pattern_performance[pattern].append(eng)

        # Variant type performance
        variant = r["variant_type"] or "unknown"
        if variant not in variant_performance:
            variant_performance[variant] = []
        variant_performance[variant].append(eng)

    # Convert to weights (normalize to 0-1 range)
    def to_weights(perf_dict):
        avgs = {k: sum(v) / len(v) for k, v in perf_dict.items() if v}
        if not avgs:
            return {}
        max_val = max(avgs.values()) or 1
        return {k: round(v / max_val, 3) for k, v in avgs.items()}

    weights = {
        "hook_type_weights": to_weights(hook_performance),
        "pattern_weights": to_weights(pattern_performance),
        "variant_weights": to_weights(variant_performance),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": len(records),
    }

    # Store weights using the same schema as services/feedback.py
    for feature_name, feature_weights in [
        ("hook_type", weights["hook_type_weights"]),
        ("pattern", weights["pattern_weights"]),
        ("variant", weights["variant_weights"]),
    ]:
        # Serialize the per-hook weights as JSON in the weight column
        weight_val = sum(feature_weights.values()) / max(len(feature_weights), 1)
        conn.execute("""
            INSERT INTO scorer_weights (scorer_name, weight, trained_at, sample_size, confidence)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scorer_name) DO UPDATE SET
                weight=excluded.weight, trained_at=excluded.trained_at,
                sample_size=excluded.sample_size
        """, (feature_name, round(weight_val, 4), weights["trained_at"], len(records), 0.5))
    conn.commit()

    return {"status": "trained", **weights}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: tracker.py <command> [args]")
        print("  track <tweet_url> [opportunity_id]")
        print("  refresh <record_id>")
        print("  refresh-due")
        print("  summary")
        print("  train")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "track":
        url = sys.argv[2]
        opp_id = int(sys.argv[3]) if len(sys.argv) > 3 else None
        print(json.dumps(track_tweet(url, opportunity_id=opp_id), indent=2))

    elif cmd == "refresh":
        print(json.dumps(refresh_tweet(int(sys.argv[2])), indent=2))

    elif cmd == "refresh-due":
        print(json.dumps(refresh_due_tweets(), indent=2))

    elif cmd == "summary":
        print(json.dumps(get_performance_summary(), indent=2))

    elif cmd == "train":
        print(json.dumps(train_scorer_from_performance(), indent=2))

    else:
        print(f"Unknown command: {cmd}")
