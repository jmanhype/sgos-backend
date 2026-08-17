"""
Lemmy Ingestion — Federated Reddit alternative with public API.
Fresh, current data from active communities.
https://join-lemmy.org
"""
import time
import requests
from datetime import datetime, timezone
from database import upsert_post, compute_z_scores

HEADERS = {
    "User-Agent": "SGOS-Bot/1.0",
    "Accept": "application/json",
}

# Lemmy instances and their active communities
LEMMY_SOURCES = [
    # lemmy.world — largest instance
    {
        "instance": "lemmy.world",
        "communities": [
            "technology", "news", "worldnews", "science",
            "programming", "linux", "privacy", "futurology",
            "artificial", "selfhosted", "dataisbeautiful",
        ],
    },
    # lemmy.ml — original instance
    {
        "instance": "lemmy.ml",
        "communities": [
            "technology", "news", "worldnews",
            "linux", "privacy",
        ],
    },
    # sh.itjust.works — popular instance
    {
        "instance": "sh.itjust.works",
        "communities": [
            "technology", "news", "gaming",
            "movies", "television", "music",
        ],
    },
    # lemmy.ca — Canadian instance
    {
        "instance": "lemmy.ca",
        "communities": ["technology", "news"],
    },
]

SORT_OPTIONS = ["TopWeek", "TopMonth", "Hot"]


def fetch_lemmy_posts(instance: str, community: str, sort: str = "TopWeek", limit: int = 25) -> list[dict]:
    """Fetch posts from a Lemmy community."""
    url = f"https://{instance}/api/v3/post/list"
    params = {
        "community_name": community,
        "sort": sort,
        "limit": limit,
    }

    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code != 200:
            return []

        data = resp.json()
        posts = []

        for item in data.get("posts", []):
            post = item.get("post", {})
            counts = item.get("counts", {})
            community_data = item.get("community", {})

            post_id = post.get("id", "")
            if not post_id:
                continue

            # Parse timestamp
            published = post.get("published", "")
            try:
                if published.endswith("Z"):
                    published = published.replace("Z", "+00:00")
                created_at = published
            except Exception:
                created_at = datetime.now(timezone.utc).isoformat()

            posts.append({
                "platform": "lemmy",
                "platform_id": f"{instance}_{community}_{post_id}",
                "subreddit": f"{community}@{instance}",
                "title": post.get("name", ""),
                "content": (post.get("body", "") or "")[:2000],
                "author": post.get("creator_id", ""),
                "url": post.get("url", f"https://{instance}/post/{post_id}"),
                "score": counts.get("score", 0),
                "comment_count": counts.get("comments", 0),
                "upvote_ratio": (
                    counts.get("upvotes", 0) / max(counts.get("upvotes", 0) + counts.get("downvotes", 0), 1)
                ),
                "created_at": created_at,
            })

        return posts

    except Exception as e:
        print(f"    ⚠️  {community}@{instance} ({sort}): {e}")
        return []


def ingest_lemmy(days_back: int = 7) -> dict:
    """Ingest posts from all configured Lemmy communities."""
    print(f"  📥 Lemmy: {len(LEMMY_SOURCES)} instances")
    total_added = 0
    total_updated = 0
    total_fetched = 0

    for source in LEMMY_SOURCES:
        instance = source["instance"]
        for community in source["communities"]:
            # Try multiple sort options to maximize coverage
            seen_ids = set()
            for sort in SORT_OPTIONS:
                posts = fetch_lemmy_posts(instance, community, sort=sort, limit=25)
                time.sleep(0.3)  # Be polite

                for post in posts:
                    pid = post["platform_id"]
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)

                    # Filter to recent posts only
                    try:
                        post_date = datetime.fromisoformat(post["created_at"])
                        cutoff = datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days_back)
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
                        total_fetched += 1
                    except Exception as e:
                        print(f"    ⚠️  {pid}: {e}")

            print(f"    {community}@{instance}: {len(seen_ids)} unique posts")

    # Compute z-scores for lemmy platform
    try:
        compute_z_scores("lemmy")
    except Exception:
        pass

    print(f"    Lemmy total: {total_fetched} fetched ({total_added} new, {total_updated} updated)")
    return {"added": total_added, "updated": total_updated, "fetched": total_fetched}


if __name__ == "__main__":
    result = ingest_lemmy()
    print(f"Result: {result}")
