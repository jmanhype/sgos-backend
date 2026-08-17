"""
ProductHunt Ingestion — Fetches top products from the unofficial API (no auth needed).
Finds viral product launches and their discussion patterns.
"""
import time
import requests
from datetime import datetime, timezone
from database import upsert_post, compute_z_scores

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Accept": "application/json",
}


def fetch_producthunt_top(days: int = 7) -> list[dict]:
    """Fetch top products from ProductHunt's public GraphQL API."""
    url = "https://www.producthunt.com/frontend/graphql"
    
    posts = []
    for day_offset in range(min(days, 7)):
        target_date = (datetime.now(timezone.utc) - __import__('datetime').timedelta(days=day_offset)).strftime("%Y-%m-%d")
        
        query = {
            "operationName": "HomePage",
            "variables": {"date": target_date},
            "query": """query HomePage($date: DateTime) {
                posts(order: VOTES, postedAfter: $date, first: 10) {
                    edges {
                        node {
                            id
                            name
                            tagline
                            votesCount
                            commentsCount
                            url
                            slug
                            createdAt
                            topics { edges { node { name } } }
                        }
                    }
                }
            }"""
        }
        
        try:
            resp = requests.post(url, json=query, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                edges = data.get("data", {}).get("posts", {}).get("edges", [])
                for edge in edges:
                    node = edge.get("node", {})
                    topics = [t["node"]["name"] for t in node.get("topics", {}).get("edges", [])]
                    posts.append({
                        "id": f"producthunt_{node['id']}",
                        "platform": "producthunt",
                        "platform_id": str(node["id"]),
                        "title": f"{node['name']} — {node['tagline']}",
                        "content": node.get("tagline", ""),
                        "score": node.get("votesCount", 0),
                        "comment_count": node.get("commentsCount", 0),
                        "upvote_ratio": 1.0,
                        "url": node.get("url", f"https://www.producthunt.com/posts/{node.get('slug', '')}"),
                        "created_utc": int(datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00")).timestamp()) if node.get("createdAt") else int(time.time()),
                        "subreddit": "producthunt",
                        "tags": topics[:5],
                    })
            time.sleep(0.5)
        except Exception as e:
            print(f"    ⚠️  PH day {day_offset}: {e}")
            continue
    
    return posts


def ingest_producthunt(days: int = 7) -> dict:
    """Ingest top ProductHunt products."""
    print(f"  📥 ProductHunt: last {days} days")
    posts = fetch_producthunt_top(days=days)
    
    added = 0
    updated = 0
    for post in posts:
        try:
            result = upsert_post(post)
            if result == "inserted":
                added += 1
            elif result == "updated":
                updated += 1
        except Exception as e:
            print(f"    ⚠️  {post['id']}: {e}")
    
    compute_z_scores("producthunt")
    print(f"    PH: {len(posts)} products ({added} new)")
    return {"added": added, "updated": updated}


if __name__ == "__main__":
    result = ingest_producthunt()
    print(f"Result: {result}")
