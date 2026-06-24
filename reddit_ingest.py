"""
SGOS Backend - Multi-Source Ingestion Worker
Uses free, auth-less APIs:
  1. Pullpush (Reddit archive) — https://api.pullpush.io
  2. Hacker News (Firebase API) — https://hacker-news.firebaseio.com
"""
import time
import requests
from datetime import datetime, timezone, timedelta
from database import upsert_post, update_sub_stats, compute_z_scores

# ─── Configuration ───────────────────────────────────────────────

TARGET_SUBREDDITS = [
    "StableDiffusion",
    "artificial",
    "LocalLLaMA",
    "ChatGPT",
    "MachineLearning",
    "socialmedia",
    "NewTubers",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _request_with_retry(url: str, params: dict = None, timeout: int = 30, max_retries: int = 2) -> dict | None:
    """HTTP GET with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            wait = 2 ** attempt
            if attempt < max_retries - 1:
                print(f"    ⏳ Retry {attempt+1}/{max_retries} in {wait}s: {e}")
                time.sleep(wait)
            else:
                print(f"    ❌ All retries exhausted: {e}")
    return None


# ─── Pullpush (Reddit Archive) ──────────────────────────────────

def fetch_pullpush(subreddit: str, size: int = 50, sort: str = "score", sort_type: str = "score") -> list[dict]:
    """
    Fetch posts from Pullpush API (Reddit archive, no auth needed).
    https://api.pullpush.io/reddit/search/submission/
    Uses retry with exponential backoff for reliability.
    
    sort_type options: 'score' (top posts), 'created_utc' (newest), 'num_comments' (most discussed)
    """
    url = "https://api.pullpush.io/reddit/search/submission/"

    params = {
        "subreddit": subreddit,
        "size": min(size, 100),
        "sort": "desc",
        "sort_type": sort_type,
    }

    data = _request_with_retry(url, params=params, timeout=45, max_retries=3)
    if data is None:
        return []

    posts = []
    items = data.get("data", [])

    for item in items:
        if not item.get("id"):
            continue
        if item.get("removed_by_category") or item.get("author") == "[deleted]":
            continue

        created_utc = item.get("created_utc", 0)
        created_at = datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()

        post = {
            "platform": "reddit",
            "platform_id": item["id"],
            "subreddit": subreddit,
            "title": item.get("title", ""),
            "content": (item.get("selftext", "") or "")[:2000],
            "author": item.get("author", "[deleted]"),
            "url": f"https://reddit.com/r/{subreddit}/comments/{item['id']}/",
            "score": item.get("score", 0),
            "comment_count": item.get("num_comments", 0),
            "upvote_ratio": item.get("upvote_ratio", 0),
            "created_at": created_at,
        }
        posts.append(post)

    return posts


# ─── Hacker News (Firebase API) ─────────────────────────────────

def fetch_hackernews_top(n: int = 30) -> list[dict]:
    """
    Fetch top stories from Hacker News Firebase API.
    Free, no auth, high-signal for AI/tech content.
    Uses ThreadPoolExecutor for parallel story fetching (fixes N+1).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers=HEADERS, timeout=10
        )
        resp.raise_for_status()
        story_ids = resp.json()[:n]
    except (requests.RequestException, ValueError) as e:
        print(f"  ❌ HN top stories: {e}")
        return []

    def _fetch_story(story_id):
        try:
            r = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                headers=HEADERS, timeout=5
            )
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            return None

    posts = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_story, sid): sid for sid in story_ids}
        for future in as_completed(futures):
            item = future.result()
            if not item or item.get("type") != "story" or item.get("dead"):
                continue

            created_utc = item.get("time", 0)
            created_at = datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()

            posts.append({
                "platform": "hackernews",
                "platform_id": str(item["id"]),
                "subreddit": "frontpage",
                "title": item.get("title", ""),
                "content": item.get("text", "") or "",
                "author": item.get("by", ""),
                "url": item.get("url", f"https://news.ycombinator.com/item?id={item['id']}"),
                "score": item.get("score", 0),
                "comment_count": item.get("descendants", 0),
                "upvote_ratio": 0,
                "created_at": created_at,
            })

    return posts


def fetch_hackernews_best(n: int = 30) -> list[dict]:
    """Fetch 'best' stories (algorithmically ranked, not just top). Parallel fetching."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/beststories.json",
            headers=HEADERS, timeout=10
        )
        resp.raise_for_status()
        story_ids = resp.json()[:n]
    except (requests.RequestException, ValueError) as e:
        print(f"  ❌ HN best stories: {e}")
        return []

    def _fetch_story(story_id):
        try:
            r = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                headers=HEADERS, timeout=5
            )
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            return None

    posts = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_story, sid): sid for sid in story_ids}
        for future in as_completed(futures):
            item = future.result()
            if not item or item.get("type") != "story" or item.get("dead"):
                continue

            created_utc = item.get("time", 0)
            created_at = datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()

            posts.append({
                "platform": "hackernews",
                "platform_id": str(item["id"]),
                "subreddit": "best",
                "title": item.get("title", ""),
                "content": item.get("text", "") or "",
                "author": item.get("by", ""),
                "url": item.get("url", f"https://news.ycombinator.com/item?id={item['id']}"),
                "score": item.get("score", 0),
                "comment_count": item.get("descendants", 0),
                "upvote_ratio": 0,
                "created_at": created_at,
            })

    return posts


# ─── Ingestion Orchestration ────────────────────────────────────

def ingest_reddit(subreddits: list[str] = None) -> dict:
    """Ingest Reddit via Pullpush API — fetches both top-scored AND newest posts."""
    targets = subreddits or TARGET_SUBREDDITS
    print(f"  📥 Reddit (Pullpush): {len(targets)} subreddits")

    added = 0
    updated = 0

    for sub in targets:
        # Fetch top-scored posts (viral content)
        top_posts = fetch_pullpush(sub, size=25, sort_type="score")
        # Fetch newest posts (fresh content)
        new_posts = fetch_pullpush(sub, size=25, sort_type="created_utc")
        # Fetch most-discussed posts
        hot_posts = fetch_pullpush(sub, size=25, sort_type="num_comments")
        
        # Deduplicate by platform_id
        seen_ids = set()
        all_posts = []
        for post in top_posts + new_posts + hot_posts:
            pid = post["platform_id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                all_posts.append(post)
        
        for post in all_posts:
            result = upsert_post(post)
            if result == "added":
                added += 1
            else:
                updated += 1

        # Update stats after each subreddit
        update_sub_stats(sub)
        compute_z_scores(sub)

        print(f"    r/{sub}: {len(all_posts)} posts ({added} total new)")
        time.sleep(1)  # Rate limit

    return {"added": added, "updated": updated}


def ingest_hackernews() -> dict:
    """Ingest Hacker News top + best stories."""
    print(f"  📥 Hacker News: top + best stories")

    added = 0
    updated = 0

    # Top stories
    top_posts = fetch_hackernews_top(30)
    for post in top_posts:
        result = upsert_post(post)
        if result == "added":
            added += 1
        else:
            updated += 1

    time.sleep(1)

    # Best stories
    best_posts = fetch_hackernews_best(30)
    for post in best_posts:
        result = upsert_post(post)
        if result == "added":
            added += 1
        else:
            updated += 1

    # Update HN stats
    update_sub_stats("frontpage")
    compute_z_scores("frontpage")
    update_sub_stats("best")
    compute_z_scores("best")

    print(f"    HN: {len(top_posts) + len(best_posts)} stories ({added} new)")
    return {"added": added, "updated": updated}


def ingest_all(subreddits: list[str] = None) -> dict:
    """
    Full ingestion pipeline: Reddit (Pullpush) + Hacker News.
    Returns summary.
    """
    print(f"🚀 Starting multi-source ingestion...")
    total_added = 0
    total_updated = 0
    results = {}

    # Reddit via Pullpush
    try:
        reddit_result = ingest_reddit(subreddits)
        results["reddit"] = reddit_result
        total_added += reddit_result["added"]
        total_updated += reddit_result["updated"]
    except Exception as e:
        print(f"  ❌ Reddit ingestion failed: {e}")
        results["reddit"] = {"error": str(e)}

    # Hacker News
    try:
        hn_result = ingest_hackernews()
        results["hackernews"] = hn_result
        total_added += hn_result["added"]
        total_updated += hn_result["updated"]
    except Exception as e:
        print(f"  ❌ HN ingestion failed: {e}")
        results["hackernews"] = {"error": str(e)}

    summary = {
        "total_added": total_added,
        "total_updated": total_updated,
        "details": results,
    }

    print(f"\n📊 Ingestion complete:")
    print(f"   Posts added: {total_added}")
    print(f"   Posts updated: {total_updated}")

    return summary


if __name__ == "__main__":
    import sys
    from database import init_db
    init_db()

    if len(sys.argv) > 1:
        ingest_reddit(sys.argv[1:])
    else:
        ingest_all([])
