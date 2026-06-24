#!/usr/bin/env python3
"""
SGOS Twitter Ingestion via SearXNG (remote 3090)
Uses SearXNG metasearch on the 3090 GPU server via SSH to find recent tweets.
Falls back to SSH-proxied search if local endpoint unavailable.
"""
import json
import re
import sys
import os
import subprocess
import shlex
from datetime import datetime, timezone

SGOS_API = os.environ.get("SGOS_API_URL", "http://localhost:8420")

# SearXNG config — on the 3090
SEARXNG_SSH_HOST = os.environ.get("SEARXNG_SSH_HOST", "3090-lan")
SEARXNG_LOCAL_PORT = int(os.environ.get("SEARXNG_LOCAL_PORT", "18080"))
SEARXNG_TUNNEL_PORT = int(os.environ.get("SEARXNG_TUNNEL_PORT", "18080"))

# Target accounts to monitor
TARGET_ACCOUNTS = [
    "karpathy", "swyx", "jxnlco", "goodself",
    "elikiiba", "nichochar", "laboringwei",
    "emaborg", "ai_for_success", "kaboroev",
    "StraughterG",
]


def search_via_ssh(handle: str) -> list[dict]:
    """
    SSH into the 3090 and query SearXNG for tweets from a handle.
    Returns parsed post dicts.
    """
    import urllib.parse
    query = urllib.parse.quote(f"from:{handle} site:x.com")
    
    # SSH + curl SearXNG on the 3090 — use shlex.quote for safe escaping
    curl_url = f"http://127.0.0.1:{SEARXNG_LOCAL_PORT}/search?q={query}&format=json&time_range=week&categories=general"
    ssh_cmd = f"curl -s {shlex.quote(curl_url)}"
    
    try:
        result = subprocess.run(
            ["ssh", SEARXNG_SSH_HOST, ssh_cmd],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            print(f"    SSH error: {result.stderr[:200]}")
            return []
        
        data = json.loads(result.stdout)
        raw_results = data.get("results", [])
        
        posts = []
        seen_ids = set()
        
        for r in raw_results:
            url = r.get("url", "")
            title = r.get("title", "")
            content = r.get("content", "")
            
            # Only actual tweet URLs
            if "/status/" not in url:
                continue
            
            id_match = re.search(r'/status/(\d+)', url)
            if not id_match:
                continue
            
            tweet_id = id_match.group(1)
            if tweet_id in seen_ids:
                continue
            seen_ids.add(tweet_id)
            
            # Combine title + snippet
            full_text = f"{title}\n{content}".strip()
            full_text = re.sub(r'\s+', ' ', full_text)
            
            # Extract engagement hints from content if available
            score = 0
            comment_count = 0
            
            posts.append({
                "platform": "twitter",
                "platform_id": f"tw_{tweet_id}",
                "subreddit": handle,
                "title": full_text[:200],
                "content": full_text,
                "author": handle,
                "url": url,
                "score": score,
                "comment_count": comment_count,
                "upvote_ratio": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        
        return posts
        
    except subprocess.TimeoutExpired:
        print(f"    SSH timeout for @{handle}")
        return []
    except json.JSONDecodeError:
        print(f"    Invalid JSON from SearXNG for @{handle}")
        return []
    except Exception as e:
        print(f"    Error for @{handle}: {e}")
        return []


def post_to_sgos(posts: list[dict]) -> dict:
    """POST posts to SGOS /ingest endpoint."""
    import requests
    try:
        resp = requests.post(f"{SGOS_API}/ingest/posts", json=posts, timeout=10)
        return resp.json()
    except Exception as e:
        print(f"  ❌ Failed to POST to SGOS: {e}")
        return {"error": str(e)}


def run():
    """Main entry point — search all target accounts via SearXNG on 3090."""
    print(f"🐦 Twitter Ingestion via SearXNG (3090)")
    print(f"   {len(TARGET_ACCOUNTS)} accounts to search\n")
    
    total_posts = []
    
    for handle in TARGET_ACCOUNTS:
        print(f"  @{handle}...", end=" ", flush=True)
        posts = search_via_ssh(handle)
        print(f"{len(posts)} tweets")
        total_posts.extend(posts)
    
    if total_posts:
        result = post_to_sgos(total_posts)
        print(f"\n✅ {len(total_posts)} tweets ingested → {result}")
    else:
        print("\n⚠️ No tweets found")
    
    return {"total": len(total_posts), "accounts": len(TARGET_ACCOUNTS)}


if __name__ == "__main__":
    run()
