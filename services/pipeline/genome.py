"""
Genome Extractor — Analyzes viral posts to extract their "DNA."

SOLID:
  - Single Responsibility: Only extracts genomes, nothing else.
  - Open/Closed: Add new extraction strategies via IGenomeExtractor protocol.
  - Liskov: Any extractor can replace another transparently.
"""
import json
import re

from services.pipeline.protocols import ViralGenome


class LLMGenomeExtractor:
    """
    Uses an LLM to extract viral DNA from a post.

    Falls back to rule-based extraction if LLM is unavailable.
    """

    HOOK_TYPES = [
        "question", "statistic", "story", "contrarian", "list",
        "bold_claim", "tutorial", "meme", "personal", "news", "announcement",
    ]

    STRUCTURAL_PATTERNS = [
        "listicle", "narrative", "how_to", "rant", "analysis",
        "comparison", "case_study", "thread", "announcement", "question_post",
    ]

    def extract(self, post: dict) -> ViralGenome:
        """Extract viral genome, trying LLM first then falling back to rules."""
        genome = self._try_llm_extract(post)
        if genome is None:
            genome = self._rule_based_extract(post)
        return genome

    def _try_llm_extract(self, post: dict) -> ViralGenome | None:
        """Attempt LLM-based genome extraction."""
        try:
            from config import settings
            from openai import OpenAI

            if not settings.llm_base_url or not settings.llm_api_key:
                return None

            client = OpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
            )

            title = post.get("title", "")
            content = (post.get("content") or "")[:800]
            text = f"Title: {title}\n\nContent: {content}" if content else f"Title: {title}"

            prompt = f"""Analyze this viral post and extract its "viral DNA" — the structural elements that made it successful.

{text}

Platform: {post.get('platform', 'unknown')}
Score: {post.get('score', 0)} | Comments: {post.get('comment_count', 0)}

Respond in JSON only:
{{
  "hook_type": "one of: question, statistic, story, contrarian, list, bold_claim, tutorial, meme, personal, news",
  "hook_text": "the opening hook in 1-2 sentences",
  "emotional_arc": ["emotion1", "emotion2", "emotion3"],
  "structural_pattern": "one of: listicle, narrative, how_to, rant, analysis, comparison, case_study, thread, announcement, question_post",
  "key_phrases": ["phrase1", "phrase2", "phrase3"],
  "content_length_words": {len((title + ' ' + content).split())}
}}"""

            response = client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=500,
            )

            raw = response.choices[0].message.content.strip()
            data = self._parse_json(raw)

            return ViralGenome(
                post_id=post.get("id", ""),
                hook_type=data.get("hook_type", "unknown"),
                hook_text=data.get("hook_text", ""),
                emotional_arc=data.get("emotional_arc", []),
                structural_pattern=data.get("structural_pattern", "unknown"),
                key_phrases=data.get("key_phrases", []),
                content_length_words=data.get("content_length_words", 0),
                platform_signals=self._extract_platform_signals(post),
                engagement_score=self._compute_engagement_score(post),
                raw_post=post,
            )

        except Exception:
            return None

    def _rule_based_extract(self, post: dict) -> ViralGenome:
        """Fallback: heuristic genome extraction without LLM."""
        title = post.get("title", "")
        content = post.get("content") or ""
        full_text = f"{title} {content}"

        hook_type = self._detect_hook_type(title)
        pattern = self._detect_pattern(title, content)
        key_phrases = self._extract_key_phrases(full_text)
        emotional_arc = self._detect_emotional_arc(full_text)

        return ViralGenome(
            post_id=post.get("id", ""),
            hook_type=hook_type,
            hook_text=title[:150],
            emotional_arc=emotional_arc,
            structural_pattern=pattern,
            key_phrases=key_phrases,
            content_length_words=len(full_text.split()),
            platform_signals=self._extract_platform_signals(post),
            engagement_score=self._compute_engagement_score(post),
            raw_post=post,
        )

    def _detect_hook_type(self, title: str) -> str:
        """Heuristic hook detection from title."""
        t = title.lower().strip()
        if t.endswith("?"):
            return "question"
        if re.search(r'\d+\s*(ways|tips|things|reasons|steps|tools)', t):
            return "list"
        if re.search(r'\d+%|\d+x|\d+\s*(billion|million|thousand)', t):
            return "statistic"
        # Story: personal experiences, first-person narratives
        story_triggers = [
            "i built", "i made", "i created", "my journey", "how i",
            "i tried", "i was", "i said", "i turned", "i found",
            "i posted", "i quit", "i spent", "i launched", "i learned",
            "my wife", "my boss", "my friend", "my mom", "my dad",
            "thanks to", "victory", "finally", "after years",
            "what happened", "here's what", "this happened",
            "my experience", "my story", "my take",
        ]
        if any(w in t for w in story_triggers):
            return "story"
        if any(w in t for w in ["stop", "wrong", "actually", "wrong about", "lies about", "no one tells you"]):
            return "contrarian"
        if any(w in t for w in ["show", "launch", "introducing", "announcing", "show hn", "launch hn"]):
            return "announcement"
        if any(w in t for w in ["tutorial", "guide", "how to", "step by step", "beginner"]):
            return "tutorial"
        if any(w in t for w in ["meme", "lol", "lmao", "😂", "💀", "bruh"]):
            return "meme"
        return "bold_claim"

    def _detect_pattern(self, title: str, content: str) -> str:
        """Heuristic structural pattern detection."""
        full = f"{title} {content}".lower()
        if re.search(r'^\d+[\.\)]', content):
            return "listicle"
        if any(w in full for w in ["step 1", "first,", "second,", "third,"]):
            return "how_to"
        if any(w in full for w in ["vs", "versus", "compared to", "comparison"]):
            return "comparison"
        if any(w in full for w in ["i think", "my take", "hot take", "unpopular opinion"]):
            return "rant"
        if any(w in full for w in ["case study", "here's what happened", "deep dive"]):
            return "case_study"
        return "narrative"

    def _extract_key_phrases(self, text: str) -> list[str]:
        """Extract impactful phrases (quoted text, capitalized words, etc.)."""
        phrases = []
        # Quoted text
        phrases.extend(re.findall(r'["""]([^"""]{5,60})["""]', text))
        # ALL CAPS phrases (2+ words)
        phrases.extend(re.findall(r'[A-Z]{2,}(?:\s+[A-Z]{2,})+', text))
        # Deduplicate and limit
        seen = set()
        unique = []
        for p in phrases:
            p_lower = p.lower().strip()
            if p_lower not in seen and len(p_lower) > 3:
                seen.add(p_lower)
                unique.append(p.strip())
        return unique[:5]

    def _detect_emotional_arc(self, text: str) -> list[str]:
        """Simple emotion detection from text sentiment markers."""
        emotions = []
        emotion_words = {
            "curiosity": ["why", "how", "wonder", "curious", "what if", "secret"],
            "surprise": ["shockingly", "unexpected", "surprisingly", "believe", "unbelievable"],
            "excitement": ["amazing", "incredible", "game-changer", "revolutionary", "breakthrough"],
            "frustration": ["annoying", "frustrating", "hate", "terrible", "worst"],
            "inspiration": ["inspired", "motivated", "dream", "believe", "achieve"],
            "skepticism": ["doubt", "skeptical", "question", "prove it", "unlikely"],
        }
        lower = text.lower()
        for emotion, triggers in emotion_words.items():
            if any(t in lower for t in triggers):
                emotions.append(emotion)
        return emotions[:4] or ["neutral"]

    def _extract_platform_signals(self, post: dict) -> dict:
        """Extract platform-specific engagement signals."""
        return {
            "platform": post.get("platform", "unknown"),
            "subreddit": post.get("subreddit", ""),
            "upvote_ratio": post.get("upvote_ratio", 0),
            "score": post.get("score", 0),
            "comment_count": post.get("comment_count", 0),
            "z_score": post.get("z_score", 0),
        }

    def _compute_engagement_score(self, post: dict) -> float:
        """Compute normalized engagement score (0-1) from post metrics."""
        z = post.get("z_score", 0)
        ratio = post.get("upvote_ratio", 0.5)
        comments = min(post.get("comment_count", 0), 500) / 500  # Cap at 500

        # Weighted composite: z-score (40%), ratio (30%), comments (30%)
        z_norm = min(z / 5.0, 1.0)  # z=5 → 1.0
        return z_norm * 0.4 + ratio * 0.3 + comments * 0.3

    def _parse_json(self, text: str) -> dict:
        """Parse JSON from LLM response, handling markdown code fences."""
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
