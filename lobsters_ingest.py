"""
Lobste.rs Ingestion — High-quality tech community (HackerNews alternative).
Public API, no auth needed. https://lobste.rs
"""
import time
import requests
from datetime import datetime, timezone, timedelta
from database import upsert_post, compute_z_scores

HEADERS = {"User-Agent": "SGOS-Bot/1.0", "Accept": "application/json"}

ENDPOINTS = [
    ("hottest", "https://lobste.rs/hottest.json"),
    ("newest", "https://lobste.rs/newest.json"),
    ("active", "https://lobste.rs/active.json"),
]


def fetch_lobsters(endpoint_name: str, url: str, limit: int = 25) -> list[dict]:
    """Fetch posts from a Lobste.rs endpoint."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return []

        posts = []
        for item in resp.json()[:limit]:
            post_id = item.get("short_id", "")
            if not post_id:
                continue

            created_at = item.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except Exception:
                dt = datetime.now(timezone.utc)

            posts.append({
                "platform": "lobsters",
                "platform_id": f"lobsters_{post_id}",
                "subreddit": "lobsters",
                "title": item.get("title", ""),
                "content": (item.get("description", "") or "")[:2000],
                "author": item.get("submitter_user", "") if isinstance(item.get("submitter_user"), str) else item.get("submitter_user", {}).get("username", ""),
                "url": item.get("url", f"https://lobste.rs/s/{post_id}"),
                "score": item.get("score", 0),
                "comment_count": item.get("comment_count", 0),
                "upvote_ratio": 1.0,
                "created_at": dt.isoformat(),
            })

        return posts
    except Exception as e:
        print(f"    ⚠️  Lobste.rs ({endpoint_name}): {e}")
        return []


def ingest_lobsters(days_back: int = 7) -> dict:
    """Ingest posts from Lobste.rs."""
    print(f"  📥 Lobste.rs")
    total_added = 0
    total_updated = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    for name, url in ENDPOINTS:
        posts = fetch_lobsters(name, url)
        time.sleep(0.5)

        for post in posts:
            # Filter by date
            try:
                post_date = datetime.fromisoformat(post["created_at"])
                if post_date.tzinfo is None:
                    post_date = post_date.replace(tzinfo=timezone.utc)
                if post_date < cutoff:
                    continue
            except Exception:
                pass

            try:
                result = upsert_post(post)
                if result == "inserted":
                    total_added += 1
                elif result == "updated":
                    total_updated += 1
            except Exception as e:
                print(f"    ⚠️  {post['platform_id']}: {e}")

        print(f"    {name}: {len(posts)} posts")

    try:
        compute_z_scores("lobsters")
    except Exception:
        pass

    print(f"    Lobste.rs total: {total_added} new, {total_updated} updated")
    return {"added": total_added, "updated": total_updated}


if __name__ == "__main__":
    result = ingest_lobsters()
    print(f"Result: {result}")
