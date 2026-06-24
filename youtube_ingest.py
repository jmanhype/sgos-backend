"""
YouTube Ingestion via SearXNG + yt-dlp
- Discovers videos via SearXNG (no API key needed)
- Grabs metadata + transcripts via yt-dlp (free, no quota)
- Posts to /ingest/posts endpoint
"""
import json
import os
import re
import subprocess
import sys
import shlex
from datetime import datetime, timezone
from urllib.parse import quote_plus
from urllib.request import urlopen, Request


# ─── Config ────────────────────────────────────────────────────────────────────

SEARXNG_HOST = os.environ.get("SEARXNG_SSH_HOST", "3090-lan")
SEARXNG_PORT = int(os.environ.get("SEARXNG_LOCAL_PORT", "18080"))
BACKEND_URL = os.environ.get("SGOS_API_URL", "http://localhost:8420")

# YouTube channels to track
YOUTUBE_CHANNELS = [
    {"handle": "TwoMinutePapers", "name": "Two Minute Papers"},
    {"handle": "YannicKilcher", "name": "Yannic Kilcher"},
    {"handle": "NetworkChuck", "name": "NetworkChuck"},
    {"handle": "Fireship", "name": "Fireship"},
    {"handle": "maboroshi_ai", "name": "Maboroshi AI"},
    {"handle": "MatthewBerman", "name": "Matthew Berman"},
    {"handle": "aiexplained-official", "name": "AI Explained"},
    {"handle": "TheAIGRID", "name": "The AI Grid"},
    {"handle": "sentdex", "name": "Sentdex"},
    {"handle": "3blue1brown", "name": "3Blue1Brown"},
]

# Topic queries for broad YouTube discovery
YOUTUBE_TOPIC_QUERIES = [
    "AI agents tutorial 2025",
    "LLM fine-tuning guide",
    "AI startup pitch",
    "content creator AI tools",
    "AI video generation demo",
    "autonomous AI coding",
    "AI image generation comparison",
    "local LLM setup guide",
]


# ─── SearXNG YouTube Search ────────────────────────────────────────────────────

def searxng_youtube_search(query: str, time_range: str = "week", max_results: int = 20) -> list:
    """
    Search YouTube via SearXNG on 3090-lan.
    Returns list of result dicts with title, url, content.
    """
    encoded_query = quote_plus(f"{query} site:youtube.com")
    
    # Use shlex.quote for safe shell escaping
    curl_url = (
        f"http://127.0.0.1:{SEARXNG_PORT}/search"
        f"?q={encoded_query}"
        f"&categories=videos"
        f"&time_range={time_range}"
        f"&format=json"
    )
    ssh_cmd = f"curl -s {shlex.quote(curl_url)}"
    
    try:
        result = subprocess.run(
            ["ssh", SEARXNG_HOST, ssh_cmd],
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode != 0:
            return []
        
        data = json.loads(result.stdout)
        results = data.get("results", [])
        
        # Filter to youtube.com URLs only
        yt_results = []
        for r in results:
            url = r.get("url", "")
            if "youtube.com" in url or "youtu.be" in url:
                yt_results.append({
                    "title": r.get("title", ""),
                    "url": url,
                    "description": r.get("content", ""),
                    "engine": r.get("engine", "youtube"),
                })
        
        return yt_results[:max_results]
    
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        print(f"  ⚠️ SearXNG search failed: {e}")
        return []


# ─── yt-dlp Transcript & Metadata ──────────────────────────────────────────────

def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from URL."""
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'youtu\.be\/([0-9A-Za-z_-]{11})',
        r'youtube\.com\/embed\/([0-9A-Za-z_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


def get_youtube_transcript(url: str) -> dict:
    """
    Get video transcript and metadata via yt-dlp.
    Returns dict with transcript, title, duration, view_count, etc.
    """
    video_id = extract_video_id(url)
    if not video_id:
        return {"error": "Could not extract video ID"}
    
    try:
        # Get metadata first
        meta_cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-download",
            "--no-warnings",
            url
        ]
        
        result = subprocess.run(meta_cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            return {"error": f"yt-dlp metadata failed: {result.stderr[:200]}"}
        
        meta = json.loads(result.stdout)
        
        # Get transcript/subtitles
        transcript = ""
        subtitles_cmd = [
            "yt-dlp",
            "--write-auto-sub", "--write-sub",
            "--sub-lang", "en",
            "--skip-download",
            "--sub-format", "json3/srv3/vtt/best",
            "-o", f"/tmp/yt_sub_{video_id}",
            "--no-warnings",
            url
        ]
        
        sub_result = subprocess.run(subtitles_cmd, capture_output=True, text=True, timeout=20)
        
        # Try to read the subtitle file
        sub_paths = [
            f"/tmp/yt_sub_{video_id}.en.json3",
            f"/tmp/yt_sub_{video_id}.en.srv3",
            f"/tmp/yt_sub_{video_id}.en.vtt",
            f"/tmp/yt_sub_{video_id}.json3",
            f"/tmp/yt_sub_{video_id}.vtt",
        ]
        
        for sp in sub_paths:
            if os.path.exists(sp):
                with open(sp, 'r') as f:
                    raw = f.read()
                # Parse VTT/JSON3 to plain text
                transcript = parse_subtitle(raw, sp)
                # Clean up
                try:
                    os.remove(sp)
                except OSError:
                    pass
                break
        
        return {
            "success": True,
            "title": meta.get("title", ""),
            "description": meta.get("description", "")[:2000],
            "duration": meta.get("duration", 0),
            "view_count": meta.get("view_count", 0),
            "like_count": meta.get("like_count", 0),
            "comment_count": meta.get("comment_count", 0),
            "upload_date": meta.get("upload_date", ""),
            "channel": meta.get("channel", "") or meta.get("uploader", ""),
            "channel_id": meta.get("channel_id", ""),
            "transcript": transcript[:5000],
            "url": url,
            "video_id": video_id,
        }
    
    except subprocess.TimeoutExpired:
        return {"error": "yt-dlp timeout"}
    except json.JSONDecodeError:
        return {"error": "Could not parse yt-dlp output"}
    except Exception as e:
        return {"error": str(e)}


def parse_subtitle(raw: str, filepath: str) -> str:
    """Parse subtitle file to plain text."""
    text_lines = []
    
    if filepath.endswith('.vtt'):
        # WebVTT format
        for line in raw.split('\n'):
            line = line.strip()
            if line and not line.startswith('WEBVTT') and '-->' not in line and not line.isdigit():
                # Remove HTML tags
                clean = re.sub(r'<[^>]+>', '', line)
                if clean and clean not in text_lines[-1:]:
                    text_lines.append(clean)
    
    elif filepath.endswith('.json3') or filepath.endswith('.srv3'):
        try:
            data = json.loads(raw)
            events = data.get("events", [])
            for event in events:
                segs = event.get("segs", [])
                for seg in segs:
                    text = seg.get("utf8", "").strip()
                    if text and text != '\n':
                        text_lines.append(text)
        except json.JSONDecodeError:
            pass
    
    return " ".join(text_lines)


# ─── Post to Backend ───────────────────────────────────────────────────────────

def post_to_backend(post: dict) -> bool:
    """POST a single post to the backend /ingest/posts endpoint."""
    import urllib.request
    
    payload = json.dumps([post]).encode()  # Wrap in array — endpoint expects list[dict]
    req = urllib.request.Request(
        f"{BACKEND_URL}/ingest/posts",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"  ⚠️ Backend POST failed: {e}")
        return False


# ─── Channel Ingestion ─────────────────────────────────────────────────────────

def ingest_channel(channel: dict, max_videos: int = 5) -> dict:
    """
    Ingest recent videos from a YouTube channel via SearXNG + yt-dlp.
    """
    handle = channel["handle"]
    name = channel["name"]
    
    print(f"  🔎 Searching channel: {name} (@{handle})")
    
    # Search for recent videos from this channel
    results = searxng_youtube_search(
        f"channel:{handle}",
        time_range="month",
        max_results=max_videos
    )
    
    if not results:
        # Fallback: search by name
        results = searxng_youtube_search(
            f"{name}",
            time_range="month",
            max_results=max_videos
        )
    
    added = 0
    transcripts = 0
    
    for r in results:
        url = r["url"]
        
        # Get metadata + transcript via yt-dlp
        yt_data = get_youtube_transcript(url)
        
        if yt_data.get("error"):
            print(f"    ❌ {r['title'][:40]} — {yt_data['error']}")
            continue
        
        # Build post
        content_parts = [yt_data.get("description", "")]
        if yt_data.get("transcript"):
            content_parts.append(f"\n\n--- TRANSCRIPT ---\n{yt_data['transcript']}")
            transcripts += 1
        
        post = {
            "platform": "youtube",
            "platform_id": yt_data.get("video_id", extract_video_id(url)),
            "title": yt_data.get("title", r["title"]),
            "content": "\n".join(content_parts)[:8000],
            "url": url,
            "author": yt_data.get("channel", name),
            "author_id": yt_data.get("channel_id", handle),
            "score": yt_data.get("view_count", 0),
            "comment_count": yt_data.get("comment_count", 0),
            "created_at": format_yt_date(yt_data.get("upload_date", "")),
            "metadata": json.dumps({
                "duration": yt_data.get("duration", 0),
                "view_count": yt_data.get("view_count", 0),
                "like_count": yt_data.get("like_count", 0),
                "has_transcript": bool(yt_data.get("transcript")),
            }),
        }
        
        if post_to_backend(post):
            added += 1
            has_t = "📝" if yt_data.get("transcript") else "📹"
            views = yt_data.get("view_count", 0)
            print(f"    {has_t} {yt_data['title'][:50]} ({views:,} views)")
    
    return {"channel": name, "added": added, "transcripts": transcripts, "searched": len(results)}


def format_yt_date(date_str: str) -> str:
    """Convert YYYYMMDD to ISO format."""
    if not date_str or len(date_str) != 8:
        return datetime.now(timezone.utc).isoformat()
    try:
        dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


# ─── Topic Search Ingestion ────────────────────────────────────────────────────

def ingest_topic_searches(queries: list, max_per_query: int = 5) -> dict:
    """
    Search YouTube for topic queries and ingest results.
    """
    total_added = 0
    total_transcripts = 0
    
    for query in queries:
        print(f"  🔎 Topic: '{query}'")
        
        results = searxng_youtube_search(query, time_range="week", max_results=max_per_query)
        
        for r in results:
            yt_data = get_youtube_transcript(r["url"])
            
            if yt_data.get("error"):
                continue
            
            content_parts = [yt_data.get("description", "")]
            if yt_data.get("transcript"):
                content_parts.append(f"\n\n--- TRANSCRIPT ---\n{yt_data['transcript']}")
                total_transcripts += 1
            
            post = {
                "platform": "youtube",
                "platform_id": yt_data.get("video_id", extract_video_id(r["url"])),
                "title": yt_data.get("title", r["title"]),
                "content": "\n".join(content_parts)[:8000],
                "url": r["url"],
                "author": yt_data.get("channel", ""),
                "author_id": yt_data.get("channel_id", ""),
                "score": yt_data.get("view_count", 0),
                "comment_count": yt_data.get("comment_count", 0),
                "created_at": format_yt_date(yt_data.get("upload_date", "")),
                "metadata": json.dumps({
                    "duration": yt_data.get("duration", 0),
                    "view_count": yt_data.get("view_count", 0),
                    "like_count": yt_data.get("like_count", 0),
                    "has_transcript": bool(yt_data.get("transcript")),
                }),
            }
            
            if post_to_backend(post):
                total_added += 1
                has_t = "📝" if yt_data.get("transcript") else "📹"
                print(f"    {has_t} {yt_data['title'][:50]}")
    
    return {"added": total_added, "transcripts": total_transcripts, "queries": len(queries)}


# ─── Main Pipeline ─────────────────────────────────────────────────────────────

def run_full_youtube_ingestion():
    """Run complete YouTube ingestion: channels + topics."""
    from database import init_db
    init_db()
    
    print("=" * 60)
    print("🎬 YouTube Ingestion Pipeline")
    print("=" * 60)
    
    # Phase 1: Channel tracking
    print("\n📺 Phase 1: Channel Videos")
    channel_results = []
    for channel in YOUTUBE_CHANNELS:
        result = ingest_channel(channel, max_videos=3)
        channel_results.append(result)
    
    channel_added = sum(r["added"] for r in channel_results)
    channel_transcripts = sum(r["transcripts"] for r in channel_results)
    
    # Phase 2: Topic searches
    print(f"\n🔍 Phase 2: Topic Searches ({len(YOUTUBE_TOPIC_QUERIES)} queries)")
    topic_result = ingest_topic_searches(YOUTUBE_TOPIC_QUERIES, max_per_query=3)
    
    # Summary
    total_added = channel_added + topic_result["added"]
    total_transcripts = channel_transcripts + topic_result["transcripts"]
    
    print(f"\n{'=' * 60}")
    print(f"✅ YouTube Ingestion Complete:")
    print(f"   Channels searched: {len(YOUTUBE_CHANNELS)}")
    print(f"   Topic queries: {len(YOUTUBE_TOPIC_QUERIES)}")
    print(f"   Videos added: {total_added}")
    print(f"   Transcripts grabbed: {total_transcripts}")
    print(f"{'=' * 60}")
    
    return {
        "status": "complete",
        "channels_searched": len(YOUTUBE_CHANNELS),
        "topic_queries": len(YOUTUBE_TOPIC_QUERIES),
        "videos_added": total_added,
        "transcripts": total_transcripts,
    }


# ─── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "transcript":
        # Get transcript for a single URL
        url = sys.argv[2] if len(sys.argv) > 2 else ""
        if url:
            result = get_youtube_transcript(url)
            print(json.dumps(result, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "channel":
        # Ingest a single channel
        handle = sys.argv[2] if len(sys.argv) > 2 else ""
        name = sys.argv[3] if len(sys.argv) > 3 else handle
        if handle:
            result = ingest_channel({"handle": handle, "name": name})
            print(json.dumps(result, indent=2))
    else:
        result = run_full_youtube_ingestion()
        print(json.dumps(result, indent=2))
