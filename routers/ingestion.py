"""Ingestion endpoints — trigger data pipelines for Reddit, HN, YouTube, topics, search."""
import json
import threading

from fastapi import APIRouter, Query

from database import upsert_post
from reddit_ingest import ingest_all, TARGET_SUBREDDITS
from topic_ingest import ingest_topics, ingest_custom_queries, TOPIC_QUERIES
from services.ingestion import ingestion_service

router = APIRouter(tags=["ingestion"])


@router.post("/ingest")
async def trigger_ingest():
    """
    Trigger a manual ingestion run.
    Returns a job_id — poll /ingest/status/{job_id} for progress.
    """
    job_id = ingestion_service.run_ingest_async("full")
    return {
        "status": "started",
        "job_id": job_id,
        "subreddits": TARGET_SUBREDDITS,
        "message": "Ingestion running in background. Poll /ingest/status/{job_id}.",
    }


@router.get("/ingest/status/{job_id}")
async def ingest_status(job_id: str):
    """Get the status of a running ingestion job."""
    status = ingestion_service.get_status(job_id)
    if not status:
        return {"error": "Job not found", "job_id": job_id}
    return status


@router.get("/ingest/jobs")
async def ingest_jobs(limit: int = Query(10, ge=1, le=50)):
    """List recent ingestion jobs and their status."""
    return {"jobs": ingestion_service.list_jobs(limit)}


@router.post("/ingest/sync")
async def trigger_ingest_sync():
    """
    Synchronous ingestion — blocks until complete.
    Use for testing or cron jobs that need to wait for completion.
    """
    result = ingest_all()
    return result


@router.post("/ingest/posts")
async def ingest_posts(posts: list[dict]):
    """Generic ingest endpoint — accepts posts from any source (search, scrapers, manual)."""
    added = 0
    updated = 0
    for post in posts:
        if "platform" not in post or "platform_id" not in post:
            continue
        post.setdefault("id", f"{post['platform']}_{post['platform_id']}")
        result = upsert_post(post)
        if result == "added":
            added += 1
        else:
            updated += 1
    return {"added": added, "updated": updated, "total": len(posts)}


@router.post("/ingest/youtube")
async def ingest_youtube_endpoint(
    mode: str = Query("full", description="Mode: full, channels, topics, transcript"),
    url: str = Query(None, description="URL for transcript mode"),
):
    """Trigger YouTube ingestion (channels + topics + transcripts)."""
    def run():
        from youtube_ingest import run_full_youtube_ingestion, get_youtube_transcript
        if mode == "transcript" and url:
            result = get_youtube_transcript(url)
            print(json.dumps(result, indent=2))
        else:
            run_full_youtube_ingestion()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return {"status": "started", "mode": mode, "message": "YouTube ingestion running in background."}


@router.post("/ingest/topics")
async def ingest_topics_endpoint(
    time_range: str = Query("week", description="Time range: day, week, month"),
    categories: str = Query(None, description="Comma-separated categories (or all)"),
):
    """Ingest posts from topic-based SearXNG searches across the web."""
    topics = TOPIC_QUERIES
    if categories:
        cat_list = [c.strip() for c in categories.split(",")]
        topics = {k: v for k, v in TOPIC_QUERIES.items() if k in cat_list}

    def run():
        try:
            ingest_topics(topics=topics, time_range=time_range)
        except Exception as e:
            print(f"Topic ingestion failed: {e}")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return {
        "status": "started",
        "categories": list(topics.keys()),
        "time_range": time_range,
        "message": "Topic ingestion running in background.",
    }


@router.post("/ingest/search")
async def ingest_search_endpoint(
    queries: list[str] = Query(..., description="Search queries to ingest from"),
    time_range: str = Query("week"),
):
    """Ingest posts from custom search queries via SearXNG."""
    def run():
        try:
            ingest_custom_queries(queries, time_range=time_range)
        except Exception as e:
            print(f"Search ingestion failed: {e}")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return {"status": "started", "queries": queries, "time_range": time_range}


# ─── Admin Endpoints ──────────────────────────────────────────────────────────

@router.get("/ingest/scheduler")
async def scheduler_status():
    """Get scheduler status: running jobs, intervals, next run times."""
    from scheduler import scheduler
    return scheduler.status()


@router.get("/ingest/sources")
async def ingestion_sources():
    """List all configured ingestion sources and their status."""
    from database import get_connection
    conn = get_connection()
    
    # Count posts per platform
    platform_counts = conn.execute(
        "SELECT platform, COUNT(*) as count, MAX(ingested_at) as last_seen FROM posts GROUP BY platform ORDER BY count DESC"
    ).fetchall()
    
    # Get subreddit targets
    sources = []
    for row in platform_counts:
        source = {
            "platform": row["platform"],
            "post_count": row["count"],
            "last_ingested": row["last_seen"],
        }
        # Add subreddit detail for Reddit
        if row["platform"] == "reddit":
            sub_counts = conn.execute(
                "SELECT subreddit, COUNT(*) as count FROM posts WHERE platform='reddit' AND subreddit IS NOT NULL GROUP BY subreddit ORDER BY count DESC"
            ).fetchall()
            source["subreddits"] = {r["subreddit"]: r["count"] for r in sub_counts}
            source["configured_subreddits"] = TARGET_SUBREDDITS
        sources.append(source)
    
    
    # Add configured topic queries
    return {
        "sources": sources,
        "topic_categories": list(TOPIC_QUERIES.keys()),
        "total_posts": sum(r["count"] for r in platform_counts),
    }


@router.get("/alerts/config")
async def get_alerts_config():
    """Get current alert configuration."""
    from alert_system import get_alert_config
    config = get_alert_config()
    # Don't expose full token
    if config.get("bot_token"):
        config["bot_token"] = config["bot_token"][:8] + "..." + config["bot_token"][-4:]
    return config


@router.put("/alerts/config")
async def update_alerts_config(
    threshold: float = Query(None, description="Z-score threshold for alerts"),
    cooldown_hours: int = Query(None, description="Hours between alerts for same post"),
    chat_id: str = Query(None, description="Telegram chat ID"),
    enabled: bool = Query(None, description="Enable/disable alerts"),
):
    """Update alert configuration. Persists to .env file."""
    import os
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    
    updates = {}
    if threshold is not None:
        updates["OUTLIER_ALERT_THRESHOLD"] = str(threshold)
        os.environ["OUTLIER_ALERT_THRESHOLD"] = str(threshold)
    if cooldown_hours is not None:
        updates["ALERT_COOLDOWN_HOURS"] = str(cooldown_hours)
        os.environ["ALERT_COOLDOWN_HOURS"] = str(cooldown_hours)
    if chat_id is not None:
        updates["TELEGRAM_CHAT_ID"] = chat_id
        os.environ["TELEGRAM_CHAT_ID"] = chat_id
    if enabled is not None:
        updates["ALERTS_ENABLED"] = str(enabled).lower()
        os.environ["ALERTS_ENABLED"] = str(enabled).lower()
    
    # Persist to .env
    if updates and os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()
        
        existing_keys = set()
        for i, line in enumerate(lines):
            key = line.split("=")[0].strip() if "=" in line else ""
            if key in updates:
                lines[i] = f"{key}={updates[key]}\n"
                existing_keys.add(key)
        
        # Append new keys
        for key, val in updates.items():
            if key not in existing_keys:
                lines.append(f"{key}={val}\n")
        
        with open(env_path, "w") as f:
            f.writelines(lines)
    
    from alert_system import get_alert_config
    config = get_alert_config()
    if config.get("bot_token"):
        config["bot_token"] = config["bot_token"][:8] + "..." + config["bot_token"][-4:]
    return {"status": "updated", "updates": list(updates.keys()), "config": config}
