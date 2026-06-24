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
        """Fallback: generate skeleton variants from templates."""
        variants = []
        types = self.VARIANT_TYPES[:num_variants]

        for vtype in types:
            template = self.VARIANT_TEMPLATES[vtype]
            title = f"[{template['format']}] {self._adapt_hook(genome.hook_text, vtype)}"

            content_parts = [
                f"# {title}",
                "",
                f"**Hook:** {genome.hook_text}",
                "",
                f"## Structure: {genome.structural_pattern}",
                f"## Emotional Arc: {' → '.join(genome.emotional_arc)}",
                "",
                "---",
                f"*This is a content brief. Use the {template['format']} format ({template['target_words']} words).*",
                "",
                f"**Key angles to explore:**",
            ]

            for phrase in genome.key_phrases[:3]:
                content_parts.append(f"- {phrase}")

            if voice_prompt:
                content_parts.extend(["", "**Voice guide:**", voice_prompt[:200]])

            variants.append(ContentVariant(
                genome_id=genome.post_id,
                variant_type=vtype,
                title=title,
                content="\n".join(content_parts),
                hook=genome.hook_text,
            ))

        return variants

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
