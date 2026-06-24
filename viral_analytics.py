"""
Viral Analytics Engine — explains WHY posts went viral
Uses Qwen to analyze viral outliers and extract patterns
"""
from database import get_connection
from idea_generation import _get_client


def explain_virality(post_id: str) -> dict:
    """
    Analyze a viral post and explain why it performed well.
    Returns structured insights about the viral mechanics.
    """
    conn = get_connection()
    post = conn.execute("""
        SELECT * FROM posts WHERE id = ?
    """, (post_id,)).fetchone()
    conn.close()

    if not post:
        return {"error": "Post not found"}

    post_dict = dict(post)

    # Build analysis prompt
    prompt = f"""Analyze why this post went viral. Extract the viral mechanics.

POST:
Title: {post_dict.get('title', 'N/A')}
Platform: {post_dict.get('platform', 'N/A')}
Subreddit: {post_dict.get('subreddit', 'N/A')}
Score: {post_dict.get('score', 'N/A')}
Comments: {post_dict.get('comment_count', 'N/A')}
Upvote ratio: {post_dict.get('upvote_ratio', 'N/A')}
Z-score: {post_dict.get('z_score', 'N/A')} (viral threshold: 2.5)
Content: {post_dict.get('content', 'N/A')[:500]}

Respond in JSON format:
{{
  "hook_type": "one of: question, story, controversial_take, data_reveal, personal_experience, humor, tutorial",
  "emotional_trigger": "primary emotion: curiosity, outrage, inspiration, nostalgia, fear, excitement",
  "timing_factor": "why this resonated now (cultural moment, news cycle, trend)",
  "audience_insight": "what this tells us about the audience",
  "replication_strategy": "how to create similar viral content (2-3 sentences)",
  "content_angle": "unique angle that made this stand out",
  "engagement_pattern": "why people commented/shared"
}}

JSON response only, no markdown."""

    client, model = _get_client()
    content = None
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=800
        )
        content = response.choices[0].message.content.strip()

        # Parse JSON from response
        import json
        # Try to extract JSON from markdown code blocks if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        analysis = json.loads(content)
        return {
            "post_id": post_id,
            "title": post_dict.get("title"),
            "platform": post_dict.get("platform"),
            "z_score": post_dict.get("z_score"),
            "analysis": analysis
        }
    except Exception as e:
        return {
            "post_id": post_id,
            "title": post_dict.get("title"),
            "error": str(e),
            "raw_response": content
        }


def analyze_viral_patterns(limit: int = 10) -> dict:
    """
    Analyze patterns across top viral posts.
    Returns aggregate insights about what's working.
    """
    conn = get_connection()
    viral_posts = conn.execute("""
        SELECT *
        FROM posts
        WHERE z_score >= 2.5
        ORDER BY z_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    if not viral_posts:
        return {"error": "No viral posts found"}

    # Aggregate stats
    platforms = {}
    subreddits = {}
    hook_keywords = []
    avg_score = 0
    avg_comments = 0

    for post in viral_posts:
        post_dict = dict(post)
        platform = post_dict.get("platform", "unknown")
        subreddit = post_dict.get("subreddit", "unknown")

        platforms[platform] = platforms.get(platform, 0) + 1
        subreddits[subreddit] = subreddits.get(subreddit, 0) + 1

        # Extract potential hook keywords from title
        title = post_dict.get("title", "").lower()
        if "?" in title:
            hook_keywords.append("question")
        if any(word in title for word in ["how", "why", "what"]):
            hook_keywords.append("educational")
        if any(word in title for word in ["i", "my", "we"]):
            hook_keywords.append("personal")

        avg_score += post_dict.get("score", 0)
        avg_comments += post_dict.get("comment_count", 0)

    avg_score = avg_score / len(viral_posts) if viral_posts else 0
    avg_comments = avg_comments / len(viral_posts) if viral_posts else 0

    # Count hook types
    hook_counts = {}
    for hook in hook_keywords:
        hook_counts[hook] = hook_counts.get(hook, 0) + 1

    return {
        "total_viral_posts": len(viral_posts),
        "platform_distribution": platforms,
        "top_subreddits": dict(sorted(subreddits.items(), key=lambda x: x[1], reverse=True)[:5]),
        "common_hooks": hook_counts,
        "avg_score": round(avg_score, 1),
        "avg_comments": round(avg_comments, 1),
        "sample_posts": [
            {
                "title": dict(p).get("title"),
                "platform": dict(p).get("platform"),
                "z_score": dict(p).get("z_score")
            }
            for p in viral_posts[:3]
        ]
    }
