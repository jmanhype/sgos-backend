"""
SGOS Backend - X/Twitter Ingestion via Web Scraping
Uses nitter instances and syndication API as fallbacks when xurl is unavailable.
"""
import re
import time
import requests
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from database import get_connection, init_db, upsert_post, update_sub_stats, compute_z_scores
from creators import init_creator_tables, add_creator, add_creator_post, list_creators

DB_PATH = Path(__file__).parent / "sgos.db"

# Nitter instances (public, may go down — fallback chain)
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.woodland.cafe",
    "https://nitter.1d4.us",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Target AI/tech accounts to track
TARGET_ACCOUNTS = [
    {"handle": "elikiiba", "niche": "ai", "tags": ["founder", "ai-agents"]},
    {"handle": "kaboroev", "niche": "ai", "tags": ["infra", "mlops"]},
    {"handle": "nichochar", "niche": "ai", "tags": ["ai-tools", "builder"]},
    {"handle": "ai_for_success", "niche": "ai", "tags": ["prompts", "tutorials"]},
    {"handle": "swyx", "niche": "ai", "tags": ["latent-space", "ai-engineering"]},
    {"handle": "karpathy", "niche": "ai", "tags": ["ml", "education"]},
    {"handle": "emaborg", "niche": "ai", "tags": ["agents", "tools"]},
    {"handle": "jxnlco", "niche": "ai", "tags": ["structured-output", "instructor"]},
    {"handle": "laboringwei", "niche": "ai", "tags": ["research", "papers"]},
    {"handle": "goodside", "niche": "ai", "tags": ["prompt-engineering", "security"]},
]


def fetch_nitter_profile(handle: str, instance_idx: int = 0) -> list[dict]:
    """
    Fetch recent posts from a Nitter instance.
    Returns list of post dicts.
    """
    if instance_idx >= len(NITTER_INSTANCES):
        print(f"  ❌ All nitter instances failed for @{handle}")
        return []

    instance = NITTER_INSTANCES[instance_idx]
    url = f"{instance}/{handle}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  ⚠️ {instance} returned {resp.status_code}, trying next...")
            time.sleep(1)
            return fetch_nitter_profile(handle, instance_idx + 1)

        html = resp.text
        posts = []

        # Parse tweets from HTML (simple regex extraction)
        # Each tweet is in a .timeline-item div
        tweet_blocks = re.findall(
            r'<div class="timeline-item[^"]*">(.*?)</div>\s*</div>\s*</div>',
            html, re.DOTALL
        )

        if not tweet_blocks:
            # Try alternate pattern
            tweet_blocks = re.findall(
                r'<div class="tweet-body[^"]*">(.*?)</div>\s*(?:<div class="tweet-stats|$)',
                html, re.DOTALL
            )

        for block in tweet_blocks[:20]:
            # Extract tweet text
            text_match = re.search(r'<div class="tweet-content[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
            if not text_match:
                continue

            text = text_match.group(1)
            text = re.sub(r'<[^>]+>', '', text)  # Strip HTML tags
            text = text.strip()

            if len(text) < 10:
                continue

            # Extract stats
            replies = re.search(r'icon-comment[^>]*>\s*</span>\s*(\d+)', block)
            retweets = re.search(r'icon-retweet[^>]*>\s*</span>\s*(\d+)', block)
            likes = re.search(r'icon-heart[^>]*>\s*</span>\s*(\d+)', block)

            reply_count = int(replies.group(1)) if replies else 0
            retweet_count = int(retweets.group(1)) if retweets else 0
            like_count = int(likes.group(1)) if likes else 0

            # Extract timestamp
            time_match = re.search(r'<span class="tweet-date"[^>]*>.*?title="([^"]+)"', block)
            posted_at = ""
            if time_match:
                try:
                    dt = datetime.strptime(time_match.group(1), "%b %d, %Y · %I:%M %p %Z")
                    posted_at = dt.replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    posted_at = datetime.now(timezone.utc).isoformat()

            # Extract tweet ID from link
            id_match = re.search(r'/' + re.escape(handle) + '/status/(\d+)', block)
            tweet_id = id_match.group(1) if id_match else str(hash(text)[:10])

            score = like_count + retweet_count * 3  # Weight retweets higher

            posts.append({
                "platform": "twitter",
                "platform_id": f"tw_{tweet_id}",
                "subreddit": handle,
                "title": text[:200],
                "content": text,
                "author": handle,
                "url": f"https://x.com/{handle}/status/{tweet_id}",
                "score": score,
                "comment_count": reply_count,
                "upvote_ratio": 0,
                "created_at": posted_at or datetime.now(timezone.utc).isoformat(),
            })

        return posts

    except requests.exceptions.Timeout:
        print(f"  ⏳ {instance} timed out, trying next...")
        return fetch_nitter_profile(handle, instance_idx + 1)
    except Exception as e:
        print(f"  ❌ {instance} error: {e}")
        if instance_idx + 1 < len(NITTER_INSTANCES):
            return fetch_nitter_profile(handle, instance_idx + 1)
        return []


def fetch_syndication(handle: str) -> list[dict]:
    """
    Alternative: Use Twitter's syndication/embed API (more reliable but limited).
    """
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}"
    try:
        resp = requests.get(url, headers={
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "text/html",
        }, timeout=15)

        if resp.status_code != 200:
            return []

        html = resp.text
        posts = []

        # Extract tweet data from script tags
        tweet_data = re.findall(r'data-tweet-id="(\d+)"[^>]*>(.*?)</div>', html, re.DOTALL)

        for tweet_id, block in tweet_data[:20]:
            text_match = re.search(r'<p[^>]*class="[^"]*timeline-Tweet-text[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
            if not text_match:
                continue

            text = re.sub(r'<[^>]+>', '', text_match.group(1)).strip()
            if len(text) < 10:
                continue

            posts.append({
                "platform": "twitter",
                "platform_id": f"tw_{tweet_id}",
                "subreddit": handle,
                "title": text[:200],
                "content": text,
                "author": handle,
                "url": f"https://x.com/{handle}/status/{tweet_id}",
                "score": 0,
                "comment_count": 0,
                "upvote_ratio": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

        return posts
    except Exception as e:
        print(f"  ❌ Syndication API error: {e}")
        return []


def ingest_twitter(accounts: list[dict] = None, use_creators_db: bool = True) -> dict:
    """
    Ingest posts from target Twitter accounts.
    Stores in both main posts table and creator_posts table.
    """
    init_db()
    if use_creators_db:
        init_creator_tables()

    targets = accounts or TARGET_ACCOUNTS
    total_fetched = 0
    total_added = 0
    results = {}

    for account in targets:
        handle = account["handle"]
        print(f"🐦 @{handle}...", end=" ", flush=True)

        # Try nitter first, then syndication
        posts = fetch_nitter_profile(handle)
        if not posts:
            posts = fetch_syndication(handle)

        if not posts:
            print("0 posts (all sources failed)")
            results[handle] = {"fetched": 0, "added": 0, "status": "failed"}
            continue

        # Register creator if using creator tracking
        creator_id = None
        if use_creators_db:
            creator = add_creator(
                handle, "twitter",
                display_name=account.get("display_name", handle),
                niche=account.get("niche", ""),
                tags=account.get("tags", []),
            )
            creator_id = creator.get("id")

        added = 0
        for post in posts:
            # Store in main posts table (for unified search/outlier detection)
            if upsert_post(post) == "added":
                added += 1

            # Store in creator tracking table
            if creator_id:
                add_creator_post(creator_id, {
                    "platform_post_id": post["platform_id"],
                    "title": post["title"],
                    "content": post["content"],
                    "url": post["url"],
                    "score": post["score"],
                    "comment_count": post["comment_count"],
                    "posted_at": post["created_at"],
                })

        # Update stats
        update_sub_stats(handle)
        compute_z_scores(handle)

        total_fetched += len(posts)
        total_added += added
        results[handle] = {"fetched": len(posts), "added": added, "status": "ok"}
        print(f"{len(posts)} posts, {added} new")

        # Rate limiting
        time.sleep(2)

    return {
        "platform": "twitter",
        "accounts_scraped": len(targets),
        "total_fetched": total_fetched,
        "total_added": total_added,
        "accounts": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    print("🐦 Twitter Ingestion Test\n")
    result = ingest_twitter(TARGET_ACCOUNTS[:3])  # Test with 3 accounts
    print(f"\n✅ Results: {json.dumps(result, indent=2)}")
