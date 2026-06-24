"""
Topic-based SearXNG ingestion — searches for trending topics beyond specific creators.
Uses SearXNG on 3090-lan via SSH (localhost:18080 on the remote machine).
Pulls from Twitter/X, Reddit, Hacker News via SearXNG metasearch.
"""
import subprocess
import json
import sys
import time
import re
from datetime import datetime, timezone
from database import upsert_post

# ─── Topic Categories ──────────────────────────────────────────────────────────
# These are the high-signal topics that matter for content creation intelligence.
# Each topic generates multiple search queries for breadth.

TOPIC_QUERIES = {
    "ai_agents": [
        "AI agents building startups",
        "autonomous AI coding agents",
        "AI agent frameworks 2025",
    ],
    "content_creation": [
        "viral content strategy 2025",
        "creator economy trends",
        "content repurposing AI tools",
    ],
    "open_source": [
        "trending open source projects",
        "open source AI models",
        "developer tools launch",
    ],
    "startups": [
        "startup growth hacks 2025",
        "indie hacker revenue",
        "bootstrapped SaaS success",
    ],
    "ai_tools": [
        "best AI tools creators",
        "AI image generation comparison",
        "AI video generation tools",
    ],
    "marketing": [
        "growth marketing strategies",
        "Twitter algorithm 2025",
        "organic reach social media",
    ],
}

# ─── SearXNG Access ────────────────────────────────────────────────────────────

def searxng_search(query: str, categories: str = "social media,science", time_range: str = "week", max_results: int = 20) -> list:
    """
    Search SearXNG on 3090-lan via SSH.
    Returns list of result dicts with title, url, content, engine.
    """
    from urllib.parse import quote_plus
    
    encoded_query = quote_plus(query)
    encoded_categories = quote_plus(categories)
    
    ssh_cmd = (
        f'curl -s "http://127.0.0.1:18080/search'
        f'?q={encoded_query}'
        f'&categories={encoded_categories}'
        f'&time_range={time_range}'
        f'&format=json"'
    )
    
    try:
        result = subprocess.run(
            ["ssh", "3090-lan", ssh_cmd],
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode != 0:
            print(f"  ❌ SSH failed for '{query}': {result.stderr[:200]}")
            return []
        
        data = json.loads(result.stdout)
        return data.get("results", [])[:max_results]
    
    except subprocess.TimeoutExpired:
        print(f"  ⏰ Timeout for '{query}'")
        return []
    except json.JSONDecodeError:
        print(f"  ❌ Invalid JSON for '{query}'")
        return []
    except Exception as e:
        print(f"  ❌ Error for '{query}': {e}")
        return []


def clean_text(text: str) -> str:
    """Clean HTML tags and excess whitespace from SearXNG results."""
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def infer_platform(url: str, engine: str) -> str:
    """Infer platform from URL or engine name."""
    url_lower = url.lower()
    if "twitter.com" in url_lower or "x.com" in url_lower:
        return "twitter"
    if "reddit.com" in url_lower:
        return "reddit"
    if "news.ycombinator.com" in url_lower or "hackernews" in engine.lower():
        return "hackernews"
    if "youtube.com" in url_lower:
        return "youtube"
    if "linkedin.com" in url_lower:
        return "linkedin"
    if "substack.com" in url_lower:
        return "substack"
    if "tiktok.com" in url_lower:
        return "tiktok"
    if "instagram.com" in url_lower:
        return "instagram"
    return "web"


def extract_subreddit(url: str, engine: str) -> str:
    """Extract subreddit or source from URL."""
    match = re.search(r'reddit\.com/r/([^/]+)', url)
    if match:
        return match.group(1)
    if "news.ycombinator.com" in url:
        return "frontpage"
    # Use engine name as source
    return engine.lower().replace(" ", "_")[:30]


def extract_author(url: str, title: str) -> str:
    """Try to extract author from URL."""
    # Twitter/X: extract handle
    match = re.search(r'(?:twitter|x)\.com/([^/]+)/status', url)
    if match:
        return f"@{match.group(1)}"
    # Reddit: try to find author in URL
    match = re.search(r'reddit\.com/user/([^/]+)', url)
    if match:
        return match.group(1)
    return "unknown"


# ─── Ingestion Logic ───────────────────────────────────────────────────────────

def ingest_topics(topics: dict = None, time_range: str = "week", dry_run: bool = False) -> dict:
    """
    Ingest posts from topic-based SearXNG searches.
    
    Args:
        topics: Dict of category -> list of queries. Defaults to TOPIC_QUERIES.
        time_range: SearXNG time_range (day, week, month, year)
        dry_run: If True, don't actually save to DB
    
    Returns:
        Dict with stats about what was ingested.
    """
    if topics is None:
        topics = TOPIC_QUERIES
    
    stats = {"total_results": 0, "added": 0, "updated": 0, "skipped": 0, "errors": 0}
    seen_urls = set()
    
    print(f"\n🔍 Topic Ingestion — {len(topics)} categories, time_range={time_range}")
    print("=" * 60)
    
    for category, queries in topics.items():
        print(f"\n📂 Category: {category}")
        
        for query in queries:
            print(f"  🔎 Searching: '{query}'")
            results = searxng_search(query, time_range=time_range)
            
            if not results:
                print(f"    → No results")
                continue
            
            print(f"    → {len(results)} results")
            stats["total_results"] += len(results)
            
            for r in results:
                url = r.get("url", "")
                if not url or url in seen_urls:
                    stats["skipped"] += 1
                    continue
                seen_urls.add(url)
                
                title = clean_text(r.get("title", ""))
                content = clean_text(r.get("content", ""))
                
                if not title or len(title) < 10:
                    stats["skipped"] += 1
                    continue
                
                # Build post dict
                platform = infer_platform(url, r.get("engine", ""))
                platform_id = url.split("?")[0].rstrip("/").split("/")[-1] or str(hash(url))
                
                post = {
                    "id": f"{platform}_{platform_id}",
                    "platform": platform,
                    "platform_id": platform_id,
                    "subreddit": extract_subreddit(url, r.get("engine", "")),
                    "title": title,
                    "content": content[:2000] if content else "",
                    "author": extract_author(url, title),
                    "url": url,
                    "score": 0,  # SearXNG doesn't give scores
                    "comment_count": 0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "topic_category": category,
                    "search_query": query,
                }
                
                if dry_run:
                    print(f"    [DRY] {title[:60]}...")
                    stats["added"] += 1
                else:
                    try:
                        result = upsert_post(post)
                        if result == "added":
                            stats["added"] += 1
                        else:
                            stats["updated"] += 1
                    except Exception as e:
                        print(f"    ❌ DB error: {e}")
                        stats["errors"] += 1
            
            # Rate limit: don't hammer SearXNG
            time.sleep(1)
    
    print(f"\n{'=' * 60}")
    print(f"✅ Topic Ingestion Complete:")
    print(f"   Total results: {stats['total_results']}")
    print(f"   Added: {stats['added']}")
    print(f"   Updated: {stats['updated']}")
    print(f"   Skipped (dupes/empty): {stats['skipped']}")
    print(f"   Errors: {stats['errors']}")
    
    return stats


def ingest_custom_queries(queries: list[str], time_range: str = "week") -> dict:
    """Ingest from a custom list of search queries."""
    topics = {"custom": queries}
    return ingest_topics(topics=topics, time_range=time_range)


# ─── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from database import init_db
    init_db()
    
    dry_run = "--dry-run" in sys.argv
    time_range = "week"
    
    if "--day" in sys.argv:
        time_range = "day"
    elif "--month" in sys.argv:
        time_range = "month"
    
    # Custom queries from CLI args
    custom = [a for a in sys.argv[1:] if not a.startswith("--")]
    if custom:
        print(f"Running custom queries: {custom}")
        ingest_custom_queries(custom, time_range=time_range)
    else:
        ingest_topics(time_range=time_range, dry_run=dry_run)
