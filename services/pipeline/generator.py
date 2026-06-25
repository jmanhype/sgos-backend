"""
Variant Generator — Creates content variants from viral genomes.

SOLID:
  - Single Responsibility: Only generates content variants.
  - Open/Closed: New generation strategies via IVariantGenerator protocol.
  - Liskov: Any generator can replace another transparently.
"""
import json
import re

from services.pipeline.protocols import ViralGenome, ContentVariant


class LLMVariantGenerator:
    """
    Uses LLM to generate content variants that structurally mirror a viral genome.
    Falls back to template-based generation if LLM is unavailable.
    """

    VARIANT_TYPES = ["thread", "post", "newsletter", "script", "carousel"]

    VARIANT_TEMPLATES = {
        "thread": {
            "format": "Twitter/X Thread",
            "instructions": "6-8 tweets, each ≤280 chars. Thread with numbered tweets. First tweet is the hook.",
            "target_words": "200-400",
        },
        "post": {
            "format": "LinkedIn/Social Post",
            "instructions": "200-300 words. Professional but punchy. Open with a hook, close with a question or CTA.",
            "target_words": "150-300",
        },
        "newsletter": {
            "format": "Newsletter Section",
            "instructions": "400-600 words. Conversational tone. Include a key insight and actionable takeaway.",
            "target_words": "400-600",
        },
        "script": {
            "format": "Short Video Script",
            "instructions": "60-second script. Open with a pattern interrupt, build tension, deliver payoff.",
            "target_words": "150-200",
        },
        "carousel": {
            "format": "Instagram Carousel",
            "instructions": "8 slides. Slide 1 = hook title. Slides 2-7 = content. Slide 8 = CTA.",
            "target_words": "100-200",
        },
    }

    def generate(
        self,
        genome: ViralGenome,
        voice_prompt: str = "",
        num_variants: int = 3,
    ) -> list[ContentVariant]:
        """Generate content variants from a viral genome."""
        variants = self._try_llm_generate(genome, voice_prompt, num_variants)
        if not variants:
            variants = self._template_generate(genome, voice_prompt, num_variants)
        return variants

    def _try_llm_generate(
        self,
        genome: ViralGenome,
        voice_prompt: str,
        num_variants: int,
    ) -> list[ContentVariant] | None:
        """Attempt LLM-based variant generation."""
        try:
            from config import settings
            from openai import OpenAI

            if not settings.llm_base_url or not settings.llm_api_key:
                return None

            client = OpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
            )

            # Pick variant types to generate
            types = self.VARIANT_TYPES[:num_variants]
            type_instructions = "\n".join(
                f"  {i+1}. **{t}** ({self.VARIANT_TEMPLATES[t]['format']}): {self.VARIANT_TEMPLATES[t]['instructions']}"
                for i, t in enumerate(types)
            )

            voice_section = f"\n\nVoice/Style Guide:\n{voice_prompt}" if voice_prompt else ""

            prompt = f"""You are a viral content strategist. Analyze this viral genome and create {num_variants} content pieces that structurally mirror what made it go viral — but with YOUR unique angle.

## Source Viral Genome
- Hook Type: {genome.hook_type}
- Hook: "{genome.hook_text}"
- Structure: {genome.structural_pattern}
- Emotional Arc: {" → ".join(genome.emotional_arc)}
- Key Phrases: {', '.join(genome.key_phrases[:3])}
- Platform: {genome.platform_signals.get('platform', 'unknown')}
- Source Score: {genome.platform_signals.get('score', 0)} upvotes
- Z-Score: {genome.platform_signals.get('z_score', 0):.1f}
{voice_section}

## Generate These {num_variants} Formats
{type_instructions}

## Rules
- Mirror the STRUCTURE, not the CONTENT of the original
- Use the same hook type and emotional arc
- Each piece must be COMPLETE and ready to publish
- Include the exact hook as the opening line of each piece

## Output Format (JSON array)
Respond with a JSON array of objects:
[
  {{
    "type": "thread",
    "title": "Thread title/headline",
    "hook": "Opening hook line",
    "content": "Full content here..."
  }},
  ...
]"""

            response = client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=3000,
            )

            raw = response.choices[0].message.content.strip()
            variants_data = self._parse_json(raw)

            if not isinstance(variants_data, list):
                return None

            variants = []
            for i, v in enumerate(variants_data):
                vtype = v.get("type", types[i] if i < len(types) else "post")
                variants.append(ContentVariant(
                    genome_id=genome.post_id,
                    variant_type=vtype,
                    title=v.get("title", f"Variant {i+1}"),
                    content=v.get("content", ""),
                    hook=v.get("hook", ""),
                ))

            return variants

        except Exception:
            return None

    def _template_generate(
        self,
        genome: ViralGenome,
        voice_prompt: str,
        num_variants: int,
    ) -> list[ContentVariant]:
        """Fallback: generate structured draft variants from genome DNA.

        Produces actual content (not just briefs) by mapping genome elements
        to platform-appropriate structures.
        """
        variants = []
        types = self.VARIANT_TYPES[:num_variants]
        hook = genome.hook_text
        phrases = genome.key_phrases[:5] or [hook]
        arc = genome.emotional_arc or ["curiosity", "insight", "action"]

        for vtype in types:
            template = self.VARIANT_TEMPLATES[vtype]
            title = f"[{template['format']}] {self._adapt_hook(hook, vtype)}"

            if vtype == "thread":
                content = self._build_thread(hook, phrases, arc, genome)
            elif vtype == "post":
                content = self._build_post(hook, phrases, arc, genome)
            elif vtype == "newsletter":
                content = self._build_newsletter(hook, phrases, arc, genome)
            elif vtype == "script":
                content = self._build_script(hook, phrases, arc, genome)
            elif vtype == "carousel":
                content = self._build_carousel(hook, phrases, arc, genome)
            else:
                content = self._build_post(hook, phrases, arc, genome)

            if voice_prompt:
                content += f"\n\n---\nVoice guide: {voice_prompt[:200]}"

            variants.append(ContentVariant(
                genome_id=genome.post_id,
                variant_type=vtype,
                title=title,
                content=content,
                hook=hook,
            ))

        return variants

    # ─── Platform-Specific Content Builders ────────────────────────────────

    def _build_thread(self, hook: str, phrases: list, arc: list, genome) -> str:
        """Build a Twitter/X thread (6-8 tweets, each ≤280 chars)."""
        tweets = [f"🧵 {hook}"]  # Tweet 1: hook

        # Body tweets from key phrases and emotional arc
        for i, phrase in enumerate(phrases[:4]):
            emotion = arc[i % len(arc)] if arc else "insight"
            tweet = self._expand_phrase(phrase, emotion, max_chars=270)
            tweets.append(tweet)

        # Closing tweet: takeaway or CTA
        takeaway = self._build_takeaway(hook, arc)
        tweets.append(takeaway[:270])

        # Number the thread
        numbered = []
        for i, tweet in enumerate(tweets):
            numbered.append(f"{i+1}/ {tweet}")

        return "\n\n".join(numbered)

    def _build_post(self, hook: str, phrases: list, arc: list, genome) -> str:
        """Build a LinkedIn/social post (150-300 words)."""
        parts = [hook, ""]  # Opening hook

        # Body: 3-4 paragraphs from key phrases
        for phrase in phrases[:3]:
            emotion = arc[len(parts) % len(arc)] if arc else "insight"
            paragraph = self._expand_phrase(phrase, emotion, max_chars=400)
            parts.append(paragraph)
            parts.append("")

        # Pattern-specific insight
        if genome.structural_pattern == "how_to":
            parts.append(f"The key insight: {phrases[0] if phrases else hook}.")
        elif genome.structural_pattern == "narrative":
            parts.append(f"What most people miss about this: {phrases[0] if phrases else hook}.")
        else:
            parts.append(f"Here's why this matters: {phrases[0] if phrases else hook}.")

        parts.append("")
        parts.append(self._build_takeaway(hook, arc))

        return "\n".join(parts)

    def _build_newsletter(self, hook: str, phrases: list, arc: list, genome) -> str:
        """Build a newsletter section (400-600 words)."""
        parts = [f"## {hook}", ""]

        # Context paragraph
        parts.append(f"This week, something caught our attention: {hook}. "
                     f"It's a perfect example of {genome.structural_pattern} content "
                     f"that resonates because it taps into {arc[0] if arc else 'curiosity'}.")
        parts.append("")

        # Key insights from phrases
        parts.append("### Key Insights")
        parts.append("")
        for phrase in phrases[:4]:
            parts.append(f"**{phrase}** — This matters because it reveals a shift in how "
                        f"audiences engage with {genome.platform_signals.get('platform', 'social')} content.")
            parts.append("")

        # Actionable takeaway
        parts.append("### What You Can Do")
        parts.append("")
        parts.append(self._build_takeaway(hook, arc))

        return "\n".join(parts)

    def _build_script(self, hook: str, phrases: list, arc: list, genome) -> str:
        """Build a short video script (60-90 seconds)."""
        parts = ["[HOOK — first 3 seconds]", f"\"{hook}\"", ""]
        parts.append("[SETUP — 15 seconds]")
        parts.append(f"Today I want to talk about something that most people get wrong: "
                    f"{phrases[0] if phrases else hook}.")
        parts.append("")

        for i, phrase in enumerate(phrases[1:4], 1):
            parts.append(f"[POINT {i} — 15 seconds]")
            parts.append(f"{phrase}. Here's why this changes everything...")
            parts.append("")

        parts.append("[CLOSE — 10 seconds]")
        parts.append(self._build_takeaway(hook, arc))
        parts.append("")
        parts.append("[CTA] \"Follow for more insights like this.\"")

        return "\n".join(parts)

    def _build_carousel(self, hook: str, phrases: list, arc: list, genome) -> str:
        """Build a carousel (8-12 slides)."""
        slides = [f"Slide 1 (COVER): {hook}", ""]

        for i, phrase in enumerate(phrases[:5], 2):
            slides.append(f"Slide {i}: {phrase}")
            slides.append("")

        slides.append(f"Slide {len(phrases)+2} (SUMMARY): {self._build_takeaway(hook, arc)}")
        slides.append("")
        slides.append(f"Slide {len(phrases)+3} (CTA): Save this for later. Share with someone who needs it.")

        return "\n".join(slides)

    # ─── Content Helpers ───────────────────────────────────────────────────

    def _expand_phrase(self, phrase: str, emotion: str, max_chars: int = 300) -> str:
        """Expand a key phrase into a sentence/paragraph based on emotional tone."""
        templates = {
            "curiosity": f"Here's what's fascinating about {phrase} — it's not what you'd expect.",
            "shock": f"{phrase}. That stat alone should change how you think about this.",
            "inspiration": f"When you see {phrase}, it reminds you what's possible.",
            "humor": f"And honestly, {phrase} — which is both hilarious and painfully true.",
            "anger": f"The frustrating part? {phrase}. And yet nobody's talking about it.",
            "insight": f"The deeper insight here: {phrase}. Most people stop at the surface.",
            "action": f"So here's what to do: start with {phrase}. That's the first step.",
            "relief": f"The good news: {phrase}. It's simpler than you think.",
        }
        result = templates.get(emotion, f"{phrase} — and here's why it matters right now.")
        return result[:max_chars]

    def _build_takeaway(self, hook: str, arc: list) -> str:
        """Build a closing takeaway/CTA."""
        closing_emotion = arc[-1] if arc else "action"
        closings = {
            "action": "What's one thing you'll do differently after reading this?",
            "insight": "The pattern is clear — and once you see it, you can't unsee it.",
            "curiosity": "What else is hiding in plain sight? Drop your best example below.",
            "inspiration": "If this resonates, share it with someone who needs to hear it.",
            "relief": "It doesn't have to be complicated. Start small. Start today.",
        }
        return closings.get(closing_emotion, "What's your take? Share in the comments.")

    def _adapt_hook(self, hook: str, variant_type: str) -> str:
        """Adapt a hook for a specific format."""
        # Truncate for short formats
        if variant_type in ("script", "carousel"):
            words = hook.split()
            if len(words) > 12:
                return " ".join(words[:12]) + "..."
        return hook

    def _parse_json(self, text: str) -> list | dict:
        """Parse JSON from LLM response, handling markdown code fences."""
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return []
