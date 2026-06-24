"""
Scoring System — Pluggable variant scoring with composite pattern.

SOLID:
  - Single Responsibility: Each scorer evaluates ONE dimension.
  - Open/Closed: Add new scorers by implementing IVariantScorer — no changes to existing code.
  - Liskov: Any scorer can replace any other via the protocol.
  - Dependency Inversion: CompositeScorer depends on IVariantScorer, not concrete classes.
"""
import re

from services.pipeline.protocols import ViralGenome, ContentVariant


class EngagementScorer:
    """
    Scores variants based on the source genome's engagement metrics.
    Higher-performing source posts → higher score for variants.
    """

    def __init__(self, weight: float = 0.4):
        self._weight = weight

    @property
    def name(self) -> str:
        return "engagement"

    @property
    def weight(self) -> float:
        return self._weight

    def score(self, variant: ContentVariant, genome: ViralGenome) -> float:
        """Score based on source genome engagement (0-100)."""
        # Engagement score is 0-1, scale to 0-100
        base = genome.engagement_score * 100

        # Bonus for high z-score source
        z_bonus = min(genome.platform_signals.get("z_score", 0) / 5.0, 1.0) * 15

        # Bonus for high upvote ratio
        ratio_bonus = genome.platform_signals.get("upvote_ratio", 0.5) * 10

        return min(base + z_bonus + ratio_bonus, 100)


class StructureScorer:
    """
    Scores variants based on structural quality of the generated content.
    Evaluates: hook strength, length appropriateness, formatting, CTA presence.
    """

    def __init__(self, weight: float = 0.6):
        self._weight = weight

    @property
    def name(self) -> str:
        return "structure"

    @property
    def weight(self) -> float:
        return self._weight

    def score(self, variant: ContentVariant, genome: ViralGenome) -> float:
        """Score based on structural quality (0-100)."""
        scores = []

        # 1. Hook strength (0-25)
        scores.append(self._score_hook(variant) * 25)

        # 2. Length appropriateness (0-25)
        scores.append(self._score_length(variant, genome) * 25)

        # 3. Formatting quality (0-25)
        scores.append(self._score_formatting(variant) * 25)

        # 4. Completeness (0-25)
        scores.append(self._score_completeness(variant) * 25)

        return sum(scores)

    def _score_hook(self, variant: ContentVariant) -> float:
        """Evaluate hook strength (0-1)."""
        content = variant.content.lower()
        first_line = content.split("\n")[0] if content else ""

        score = 0.3  # Base score

        # Question hook
        if "?" in first_line:
            score += 0.2

        # Bold claim or specific number
        if re.search(r'\d', first_line):
            score += 0.15

        # Short punchy hook (< 100 chars)
        if len(first_line) < 100:
            score += 0.15

        # Emotional words
        emotional = ["secret", "shocking", "game-changer", "mistake", "truth", "hidden"]
        if any(w in first_line for w in emotional):
            score += 0.2

        return min(score, 1.0)

    def _score_length(self, variant: ContentVariant, genome: ViralGenome) -> float:
        """Evaluate if content length matches optimal for the format."""
        word_count = len(variant.content.split())
        target = genome.content_length_words

        # Format-specific optimal ranges
        ranges = {
            "thread": (200, 500),
            "post": (100, 300),
            "newsletter": (400, 800),
            "script": (150, 250),
            "carousel": (100, 200),
        }

        low, high = ranges.get(variant.variant_type, (100, 500))

        if low <= word_count <= high:
            return 1.0
        elif word_count < low:
            return word_count / low
        else:
            # Penalize over-long but gently
            overshoot = (word_count - high) / high
            return max(0.3, 1.0 - overshoot * 0.5)

    def _score_formatting(self, variant: ContentVariant) -> float:
        """Evaluate formatting quality — paragraphs, structure, readability."""
        content = variant.content
        score = 0.3  # Base

        # Has paragraph breaks
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        if len(paragraphs) >= 3:
            score += 0.2
        elif len(paragraphs) >= 2:
            score += 0.1

        # Has list items or numbered items
        if re.search(r'^[\d\-\*•]\s', content, re.MULTILINE):
            score += 0.15

        # Has bold/italic or emphasis
        if "**" in content or "__" in content or "*" in content:
            score += 0.1

        # Has emoji or visual breaks
        if any(c for c in content if ord(c) > 0x1F600):
            score += 0.1

        # Not a wall of text (no single paragraph > 300 chars)
        max_para = max((len(p) for p in paragraphs), default=0)
        if max_para < 300:
            score += 0.15

        return min(score, 1.0)

    def _score_completeness(self, variant: ContentVariant) -> float:
        """Evaluate if the content feels complete and publishable."""
        content = variant.content.strip()
        score = 0.5  # Base — assume partially complete

        # Has a title
        if variant.title and len(variant.title) > 10:
            score += 0.2

        # Ends with a conclusion or CTA
        last_line = content.split("\n")[-1].strip().lower()
        if any(w in last_line for w in ["follow", "subscribe", "share", "comment", "try", "let me know"]):
            score += 0.15
        elif last_line.endswith((".", "!", "?")):
            score += 0.1

        # More than 50 words
        if len(content.split()) > 50:
            score += 0.15

        return min(score, 1.0)


class CompositeScorer:
    """
    Combines multiple scorers using weighted average.

    SOLID: Depends on IVariantScorer protocol (Dependency Inversion).
    New scorers are added without modifying this class (Open/Closed).
    """

    def __init__(self, scorers: list):
        self._scorers = scorers
        total_weight = sum(s.weight for s in scorers)
        if total_weight == 0:
            raise ValueError("At least one scorer with non-zero weight required")
        self._total_weight = total_weight

    def score(self, variant: ContentVariant, genome: ViralGenome) -> tuple[float, dict]:
        """
        Score a variant using all registered scorers.
        Returns (final_score, breakdown_dict).
        """
        breakdown = {}
        weighted_sum = 0.0

        for scorer in self._scorers:
            raw = scorer.score(variant, genome)
            breakdown[scorer.name] = {
                "raw": round(raw, 2),
                "weight": scorer.weight,
                "weighted": round(raw * scorer.weight / self._total_weight, 2),
            }
            weighted_sum += raw * scorer.weight / self._total_weight

        return round(weighted_sum, 2), breakdown
