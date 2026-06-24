"""
Pipeline Protocols — Interface contracts for all pipeline components.

SOLID: Interface Segregation + Dependency Inversion.
  - Each protocol is small and focused on ONE capability.
  - Orchestrator depends on protocols, never concrete classes.
  - New extractors/scorers/generators implement the protocol without
    modifying existing code (Open/Closed).
"""
from typing import Protocol, runtime_checkable


# ─── Domain Models ──────────────────────────────────────────────────────────

class ViralGenome:
    """
    Immutable value object representing a post's "viral DNA."

    Attributes:
        post_id: Source post identifier
        hook_type: Opening technique (question, stat, story, contrarian, list)
        hook_text: The actual hook sentence(s)
        emotional_arc: Trajectory of emotions through the content
        structural_pattern: Format pattern (listicle, narrative, how-to, rant, etc.)
        key_phrases: High-impact phrases that drive engagement
        content_length_words: Optimal word count for this genome
        platform_signals: Platform-specific engagement patterns
        engagement_score: Normalized 0-1 engagement metric
        raw_post: Reference to the source post dict
    """

    def __init__(
        self,
        post_id: str,
        hook_type: str,
        hook_text: str,
        emotional_arc: list[str],
        structural_pattern: str,
        key_phrases: list[str],
        content_length_words: int,
        platform_signals: dict,
        engagement_score: float,
        raw_post: dict | None = None,
    ):
        self.post_id = post_id
        self.hook_type = hook_type
        self.hook_text = hook_text
        self.emotional_arc = emotional_arc
        self.structural_pattern = structural_pattern
        self.key_phrases = key_phrases
        self.content_length_words = content_length_words
        self.platform_signals = platform_signals
        self.engagement_score = engagement_score
        self.raw_post = raw_post

    def to_dict(self) -> dict:
        return {
            "post_id": self.post_id,
            "hook_type": self.hook_type,
            "hook_text": self.hook_text,
            "emotional_arc": self.emotional_arc,
            "structural_pattern": self.structural_pattern,
            "key_phrases": self.key_phrases,
            "content_length_words": self.content_length_words,
            "platform_signals": self.platform_signals,
            "engagement_score": self.engagement_score,
        }

    @classmethod
    def from_dict(cls, data: dict, raw_post: dict | None = None) -> "ViralGenome":
        return cls(
            post_id=data["post_id"],
            hook_type=data.get("hook_type", "unknown"),
            hook_text=data.get("hook_text", ""),
            emotional_arc=data.get("emotional_arc", []),
            structural_pattern=data.get("structural_pattern", "unknown"),
            key_phrases=data.get("key_phrases", []),
            content_length_words=data.get("content_length_words", 0),
            platform_signals=data.get("platform_signals", {}),
            engagement_score=data.get("engagement_score", 0.0),
            raw_post=raw_post,
        )


class ContentVariant:
    """
    A generated content piece based on a viral genome.

    Attributes:
        genome_id: The source genome post_id
        variant_type: Content format (thread, post, newsletter, script, carousel)
        title: Headline / title of the variant
        content: Full body text, ready to publish
        score: Composite score (0-100)
        score_breakdown: Per-dimension scoring
        hook: The opening hook of this variant
    """

    def __init__(
        self,
        genome_id: str,
        variant_type: str,
        title: str,
        content: str,
        score: float = 0.0,
        score_breakdown: dict | None = None,
        hook: str = "",
    ):
        self.genome_id = genome_id
        self.variant_type = variant_type
        self.title = title
        self.content = content
        self.score = score
        self.score_breakdown = score_breakdown or {}
        self.hook = hook

    def to_dict(self) -> dict:
        return {
            "genome_id": self.genome_id,
            "variant_type": self.variant_type,
            "title": self.title,
            "content": self.content,
            "score": self.score,
            "score_breakdown": self.score_breakdown,
            "hook": self.hook,
        }


# ─── Protocols (Interfaces) ─────────────────────────────────────────────────

@runtime_checkable
class IGenomeExtractor(Protocol):
    """
    Extracts viral DNA from a post.

    Implementations:
      - LLMGenomeExtractor (uses LLM to analyze content)
      - RuleBasedExtractor (future: regex/heuristic fallback)
      - MLGenomeExtractor (future: trained classifier)
    """

    def extract(self, post: dict) -> ViralGenome:
        """Analyze a post and return its viral genome."""
        ...


@runtime_checkable
class IGenomeRepository(Protocol):
    """
    Persists and retrieves viral genomes + opportunities.

    Implementations:
      - GenomeRepository (SQLite-backed)
      - InMemoryRepository (testing)
    """

    def save(self, genome: ViralGenome) -> None: ...
    def get(self, post_id: str) -> ViralGenome | None: ...
    def list_recent(self, limit: int = 20) -> list[ViralGenome]: ...
    def get_top_genomes(self, limit: int = 5) -> list[ViralGenome]: ...
    def exists(self, post_id: str) -> bool: ...
    def save_opportunity(self, variant: ContentVariant) -> int: ...
    def get_opportunities(self, limit: int = 10, unseen_only: bool = True) -> list[dict]: ...
    def mark_viewed(self, opportunity_id: int) -> None: ...
    def dismiss_opportunity(self, opportunity_id: int) -> None: ...
    def get_stats(self) -> dict: ...


@runtime_checkable
class IVariantScorer(Protocol):
    """
    Scores a content variant. Each scorer evaluates ONE dimension.

    Composite pattern: multiple scorers combine into a final score.
    New scorers can be added without modifying existing ones (Open/Closed).

    Implementations:
      - EngagementScorer (based on source genome engagement)
      - StructureScorer (structural quality of the variant)
      - VoiceMatchScorer (future: similarity to user's voice profile)
    """

    @property
    def name(self) -> str: ...

    @property
    def weight(self) -> float: ...

    def score(self, variant: ContentVariant, genome: ViralGenome) -> float:
        """Return a score 0-100 for this dimension."""
        ...


@runtime_checkable
class ICompositeScorer(Protocol):
    """
    Combines multiple scorers into a weighted composite score.
    """

    def score(self, variant: ContentVariant, genome: ViralGenome) -> tuple[float, dict]:
        """Return (final_score, breakdown_dict)."""
        ...


@runtime_checkable
class IVariantGenerator(Protocol):
    """
    Generates content variants from a viral genome + voice profile.

    Implementations:
      - LLMVariantGenerator (uses LLM to create variants)
      - TemplateGenerator (future: pre-built templates)
    """

    def generate(
        self,
        genome: ViralGenome,
        voice_prompt: str = "",
        num_variants: int = 3,
    ) -> list[ContentVariant]:
        """Generate content variants from a genome."""
        ...
