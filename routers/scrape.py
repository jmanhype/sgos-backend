"""Scrape endpoints — Firecrawl deep-scraping for outlier posts."""
import threading

from fastapi import APIRouter, Query

from firecrawl_scrape import (
    scrape_url,
    deep_scrape_post,
    deep_scrape_outliers,
)

router = APIRouter(tags=["scrape"])


@router.post("/scrape")
async def scrape_endpoint(url: str = Query(..., description="URL to scrape")):
    """Scrape a URL via Firecrawl and return markdown content."""
    return scrape_url(url)


@router.post("/outliers/{post_id}/deep-scrape")
async def deep_scrape_single(post_id: str):
    """Deep-scrape a specific outlier post — fetches full article text via Firecrawl."""
    return deep_scrape_post(post_id)


@router.post("/outliers/deep-scrape")
async def deep_scrape_all_outliers(
    threshold: float = Query(3.0, description="Min z-score to scrape"),
    limit: int = Query(5, description="Max posts to scrape"),
):
    """Deep-scrape all outlier posts above threshold via Firecrawl."""
    def run():
        try:
            deep_scrape_outliers(threshold=threshold, limit=limit)
        except Exception as e:
            print(f"Deep-scrape failed: {e}")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return {
        "status": "started",
        "threshold": threshold,
        "limit": limit,
        "message": "Deep-scrape running in background.",
    }
