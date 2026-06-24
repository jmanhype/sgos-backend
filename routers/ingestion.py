"""Ingestion endpoints — trigger data pipelines for Reddit, HN, YouTube, topics, search."""
import json
import threading

from fastapi import APIRouter, Query

from config import settings
from database import upsert_post
from reddit_ingest import ingest_all, TARGET_SUBREDDITS
from topic_ingest import ingest_topics, ingest_custom_queries, TOPIC_QUERIES
from services.ingestion import ingestion_service, ingestion_progress

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
