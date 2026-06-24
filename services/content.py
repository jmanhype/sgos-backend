"""Content service — repurposing, idea generation, scoring."""
import json

from database import get_connection
from repositories.posts import posts_repo


class ContentService:
    @staticmethod
    def build_repurpose_prompt(post: dict) -> dict:
        context = f"""ORIGINAL POST (viral outlier, z-score: {post['z_score']:.1f}):
Title: {post['title']}
Platform: {post['platform']}/{post['subreddit']}
Score: {post['score']} upvotes, {post['comment_count']} comments
URL: {post['url']}"""

        if post.get("content") and len(post["content"]) > 10:
            context += f"\nContent: {post['content'][:500]}"

        prompt = f"""{context}

---

REPURPOSE this viral post into 5 content formats. For each format, write the FULL ready-to-publish content:

## 1. \U0001f9f5 Twitter/X Thread (6-8 posts)
## 2. \U0001f4bc LinkedIn Post (200-300 words)
## 3. \U0001f4e7 Newsletter Section (400-500 words)
## 4. \U0001f3ac TikTok/Reel Script (60 seconds)
## 5. \U0001f4f8 Instagram Carousel (8 slides)

Write each piece as if it's going live TODAY."""

        return {
            "post": post,
            "prompt": prompt,
            "formats": ["twitter_thread", "linkedin_post", "newsletter", "tiktok_script", "ig_carousel"],
        }

    @staticmethod
    def find_post(post_id: str | None = None, title: str | None = None) -> dict | None:
        if post_id:
            return posts_repo.get_by_id(post_id)
        if title:
            return posts_repo.find_by_title(title)
        return None

    @staticmethod
    def score_post(post_id: str) -> dict:
        post = posts_repo.get_by_id(post_id)
        if not post:
            return {"post_id": post_id, "error": "Post not found"}

        from idea_generation import _get_client
        client, model = _get_client()

        prompt = f"""Score this post's content quality on 5 dimensions (1-10 scale each).

Post: "{post.get('title', '')}"
Content: {post.get('content', 'N/A')[:500]}
Platform: {post.get('platform')} | Score: {post.get('score', 0)} | Comments: {post.get('comment_count', 0)}

Respond in JSON only:
{{
  "hook_strength": 8,
  "value_density": 7,
  "shareability": 9,
  "originality": 6,
  "emotional_impact": 8,
  "overall": 7.6,
  "one_liner": "Brief verdict"
}}"""

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=400,
            )
            content = response.choices[0].message.content.strip()
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            scores = json.loads(content)
            return {"post_id": post_id, "title": post.get("title"), "scores": scores}
        except Exception as e:
            return {"post_id": post_id, "error": str(e)}


content_service = ContentService()
