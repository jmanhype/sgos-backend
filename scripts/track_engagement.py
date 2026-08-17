#!/usr/bin/env python3
"""
Track engagement on recent StraughterG posts.
Scrapes last 50 tweets, groups into threads, reports likes/replies/retweets.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


def get_recent_tweets(handle: str, count: int = 50):
    """Fetch recent tweets from a handle."""
    cmd = ["bird", "user-tweets", handle, "--count", str(count), "--json"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    
    if result.returncode != 0:
        print(f"❌ Failed to fetch tweets: {result.stderr}")
        return []
    
    # Strip non-JSON lines (bird prints warnings to stdout)
    lines = result.stdout.strip().split('\n')
    json_lines = []
    in_json = False
    for line in lines:
        if line.strip().startswith(('{', '[')):
            in_json = True
        if in_json:
            json_lines.append(line)
    
    json_text = '\n'.join(json_lines)
    
    try:
        data = json.loads(json_text)
        # Handle both list and {"tweets": [...]} formats
        if isinstance(data, dict):
            return data.get("tweets", [])
        return data
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse tweet JSON: {e}")
        return []


def group_into_threads(tweets):
    """Group tweets into threads by conversationId."""
    threads = {}
    
    for tweet in tweets:
        conv_id = tweet.get("conversationId", tweet.get("id"))
        
        if conv_id not in threads:
            threads[conv_id] = []
        
        threads[conv_id].append(tweet)
    
    # Sort each thread by createdAt
    for conv_id in threads:
        threads[conv_id].sort(key=lambda t: t["createdAt"])
    
    return threads


def analyze_thread(tweets):
    """Calculate engagement metrics for a thread."""
    if not tweets:
        return None
    
    first = tweets[0]
    
    # Sum engagement across all tweets in thread
    total_likes = sum(t.get("likeCount", 0) for t in tweets)
    total_replies = sum(t.get("replyCount", 0) for t in tweets)
    total_retweets = sum(t.get("retweetCount", 0) for t in tweets)
    
    # bird CLI doesn't include views
    total_views = sum(t.get("viewCount", 0) for t in tweets)
    
    # Calculate engagement rate (only if we have views)
    if total_views > 0:
        engagement_rate = (total_likes + total_replies + total_retweets) / total_views * 100
    else:
        engagement_rate = 0
    
    return {
        "thread_id": first["id"],
        "posted_at": first["createdAt"],
        "tweet_count": len(tweets),
        "first_text": first.get("text", "")[:100],
        "likes": total_likes,
        "replies": total_replies,
        "retweets": total_retweets,
        "views": total_views,
        "engagement_rate": round(engagement_rate, 2),
        "engagement_score": total_likes + (total_replies * 3) + (total_retweets * 2)
    }


def main():
    print("=" * 60)
    print("📊 SGOS Engagement Tracker")
    print("=" * 60)
    
    # Fetch recent tweets
    print("\nFetching @StraughterG tweets...")
    tweets = get_recent_tweets("StraughterG", 50)
    
    if not tweets:
        print("❌ No tweets found")
        return 1
    
    print(f"Found {len(tweets)} tweets")
    
    # Group into threads
    threads = group_into_threads(tweets)
    print(f"Grouped into {len(threads)} threads\n")
    
    # Analyze each thread
    thread_stats = []
    for conv_id, thread_tweets in threads.items():
        stats = analyze_thread(thread_tweets)
        if stats:
            thread_stats.append(stats)
    
    # Sort by engagement score
    thread_stats.sort(key=lambda t: t["engagement_score"], reverse=True)
    
    # Show top 10
    print("🏆 TOP 10 THREADS BY ENGAGEMENT")
    print("=" * 60)
    
    for i, stats in enumerate(thread_stats[:10], 1):
        # Parse date format: "Tue Jul 07 05:20:44 +0000 2026"
        try:
            posted = datetime.strptime(stats["posted_at"], "%a %b %d %H:%M:%S %z %Y")
            age_days = (datetime.now(posted.tzinfo) - posted).days
        except:
            posted = datetime.now()
            age_days = 0
        
        print(f"\n{i}. {stats['first_text']}...")
        print(f"   Posted: {posted.strftime('%Y-%m-%d %H:%M')} ({age_days}d ago)")
        print(f"   Tweets: {stats['tweet_count']}")
        print(f"   ❤️ {stats['likes']} | 💬 {stats['replies']} | 🔁 {stats['retweets']} | 👁️ {stats['views']}")
        print(f"   Engagement rate: {stats['engagement_rate']}%")
        print(f"   Score: {stats['engagement_score']}")
    
    # Calculate totals
    total_threads = len(thread_stats)
    total_likes = sum(t["likes"] for t in thread_stats)
    total_replies = sum(t["replies"] for t in thread_stats)
    total_retweets = sum(t["retweets"] for t in thread_stats)
    
    # Last 7 days
    week_ago = datetime.now() - timedelta(days=7)
    recent_threads = []
    for t in thread_stats:
        # Parse date format: "Tue Jul 07 05:20:44 +0000 2026"
        try:
            posted = datetime.strptime(t["posted_at"], "%a %b %d %H:%M:%S %z %Y")
            if posted.replace(tzinfo=None) >= week_ago:
                recent_threads.append(t)
        except:
            pass
    
    print("\n" + "=" * 60)
    print("📈 SUMMARY")
    print("=" * 60)
    print(f"Total threads analyzed: {total_threads}")
    print(f"Total engagement: ❤️ {total_likes} | 💬 {total_replies} | 🔁 {total_retweets}")
    print(f"Last 7 days: {len(recent_threads)} threads")
    
    if recent_threads:
        avg_likes = sum(t["likes"] for t in recent_threads) / len(recent_threads)
        avg_replies = sum(t["replies"] for t in recent_threads) / len(recent_threads)
        print(f"  Avg per thread: ❤️ {avg_likes:.1f} | 💬 {avg_replies:.1f}")
    
    # Save to JSON for trend analysis
    output_file = Path("data/engagement_tracker.json")
    output_file.parent.mkdir(exist_ok=True)
    
    with open(output_file, "w") as f:
        json.dump({
            "analyzed_at": datetime.now().isoformat(),
            "threads": thread_stats
        }, f, indent=2)
    
    print(f"\n✅ Saved to {output_file}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
