#!/usr/bin/env python3
"""
SGOS Daily Research Cron
Runs ingestion, computes outliers, generates content brief WITH AI ideas.
Designed to run via Hermes cron job.
"""
import subprocess
import sys
import os

# Change to backend directory
os.chdir(os.path.expanduser("~/sgos-backend"))

# Strip Perplexity key if quota-exceeded (fall back to Aliyun)
os.environ.pop("PERPLEXITY_API_KEY", None)


def run_ingestion():
    """Run the full ingestion pipeline."""
    result = subprocess.run(
        [sys.executable, "reddit_ingest.py"],
        capture_output=True, text=True, timeout=300
    )
    return result.stdout + result.stderr


def get_brief():
    """Fetch the daily brief — now with AI-generated ideas."""
    import requests
    try:
        resp = requests.get("http://localhost:8420/brief", timeout=10)
        if resp.status_code == 200:
            return resp.json()["brief"]
    except Exception:
        pass
    
    # Fallback: generate brief from database + LLM ideas
    from database import init_db, get_outliers, get_trending_topics, get_stats
    from idea_generation import generate_ideas, save_ideas
    from vector_search import build_index
    init_db()
    
    # Rebuild vector index with new posts
    build_index(rebuild=False)
    
    outliers = get_outliers(hours=48, limit=5, platform=None)
    trends = get_trending_topics(days=7, limit=5, platform=None)
    stats = get_stats()
    
    from datetime import datetime, timezone
    lines = [
        f"# Daily Content Brief — {datetime.now(timezone.utc).strftime('%B %d, %Y')}",
        "",
        f"📊 Database: {stats['total_posts']} posts | {stats['outliers_24h']} outliers in 24h",
        "",
        "## 🔥 Top Outliers",
        "",
    ]
    
    for i, post in enumerate(outliers, 1):
        lines.append(f"**{i}. {post['title']}**")
        lines.append(f"   {post['platform']} | ⬆️ {post['score']} | 💬 {post['comment_count']} | z: {post['z_score']:.1f}")
        lines.append(f"   {post.get('url', '')}")
        lines.append("")
    
    if trends:
        lines.extend(["## 📈 Trending Topics", ""])
        for t in trends:
            lines.append(f"- **{t['topic']}** ({t['count']} mentions)")
        lines.append("")
    
    # AI Idea Generation
    lines.extend(["## 🧠 AI-Generated Ideas", ""])
    try:
        ideas_result = generate_ideas(count=3, hours=72)
        if ideas_result.get("status") == "generated":
            save_ideas(ideas_result)
            for i, idea in enumerate(ideas_result["ideas"], 1):
                lines.append(f"### Idea {i}: {idea.get('hook', 'Untitled')}")
                lines.append(f"**Angle:** {idea.get('angle', '')}")
                lines.append(f"**Format:** {idea.get('format', 'thread')}")
                lines.append(f"**Why it works:** {idea.get('why_it_works', '')}")
                brief = idea.get("brief", [])
                if brief:
                    lines.append("**Brief:**")
                    for b in brief[:4]:
                        lines.append(f"  - {b}")
                lines.append("")
        else:
            lines.append(f"_Idea generation unavailable: {ideas_result.get('message', ideas_result.get('error', 'unknown'))}_")
            lines.append("")
    except Exception as e:
        lines.append(f"_Idea generation error: {e}_")
        lines.append("")
    
    return "\n".join(lines)


if __name__ == "__main__":
    # Run ingestion
    print("🔄 Running daily ingestion...")
    ingest_output = run_ingestion()
    print(ingest_output)
    
    # Get brief
    print("\n📋 Generating daily brief...")
    brief = get_brief()
    print(brief)
