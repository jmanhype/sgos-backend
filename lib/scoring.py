"""
Strategy Pattern — Pluggable scoring for different content types.

Trade-offs:
  (+) Each content type has its OWN scoring logic — no mega-if-chains
  (+) New content types = new strategy class, zero changes to existing code (Open/Closed)
  (+) Strategies are independently testable
  (-) More classes than a single score() function
  (-) Shared scoring components need composition, not inheritance

Design choice: Each strategy implements score(context) -> ScoreResult.
ScoringContext carries all the data a strategy might need. Strategies
compose reusable scoring components (freshness, grounding, audience).
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone, timedelta
import math


@dataclass
class ScoringContext:
    """All data a scoring strategy might need. Passed by value."""
    # Content metadata
    content_type: str = ""
    content: str = ""
    author_handle: str = ""

    # Author metrics
    author_followers: int = 0
    author_engagement_count: int = 0
    last_engaged_at: Optional[str] = None
    is_follow_up: bool = False

    # Topic/signal data
    topic_match: float = 0.0
    urgency_score: float = 0.0
    engagement_velocity: float = 0.0
    follower_tier: str = ""

    # Quality gates
    grounding_pct: float = 0.0
    draft_length: int = 0
    overlap_with_recent: float = 0.0
    banned_jargon_count: int = 0

    # Historical performance
    historical_engagement_rate: float = 0.0
    previous_strikes_count: int = 0


@dataclass
class ScoreResult:
    """Output of a scoring strategy."""
    score: float = 0.0
    grounded: bool = False
    passed_gates: bool = False
    reasons: list = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)

    def __str__(self):
        return f"ScoreResult(score={self.score:.1f}, grounded={self.grounded}, gates={self.passed_gates}, reasons={self.reasons})"


# --- Quality Gate Constants ---
MIN_STRIKE_SCORE = 5.0
ALERT_THRESHOLD = 6.0
MIN_GROUNDING = 60  # percent
MIN_DRAFT_LENGTH = 80
MAX_OVERLAP = 0.70  # 70% word overlap = too similar
COLD_COOLDOWN_HOURS = 24
HARD_BLOCK_THRESHOLD = 5  # 5+ replies to same person = hard block
FRESHNESS_BONUS = 2.0


# --- Reusable Scoring Components ---

def calc_freshness(ctx: ScoringContext) -> float:
    """Bonus for never-engaged targets. Penalty for over-engaged."""
    if ctx.previous_strikes_count >= HARD_BLOCK_THRESHOLD:
        return 0.0  # Hard block
    if ctx.previous_strikes_count == 0:
        return FRESHNESS_BONUS
    # Diminishing: 1 prev = 1.0, 2 = 0.5, 3 = 0.33, 4 = 0.25
    return 1.0 / ctx.previous_strikes_count


def calc_cooldown_ok(ctx: ScoringContext) -> bool:
    """True if enough time has passed since last cold engagement."""
    if ctx.is_follow_up:
        return True  # Follow-ups bypass cooldown
    if ctx.last_engaged_at is None:
        return True  # Never engaged
    try:
        last = datetime.fromisoformat(ctx.last_engaged_at.replace("Z", "+00:00"))
        elapsed = datetime.now(timezone.utc) - last
        return elapsed >= timedelta(hours=COLD_COOLDOWN_HOURS)
    except (ValueError, TypeError):
        return True


def calc_grounding_ok(ctx: ScoringContext) -> bool:
    """True if grounding percentage meets minimum."""
    return ctx.grounding_pct >= MIN_GROUNDING


def calc_draft_quality(ctx: ScoringContext) -> bool:
    """True if draft meets length and repetition thresholds."""
    if ctx.draft_length < MIN_DRAFT_LENGTH:
        return False
    if ctx.overlap_with_recent > MAX_OVERLAP:
        return False
    return True


# --- Strategies ---

class ScoringStrategy:
    """Base strategy — subclasses implement score()."""

    def score(self, ctx: ScoringContext) -> ScoreResult:
        raise NotImplementedError


class StrikeScoring(ScoringStrategy):
    """
    Scores strike candidates (reply drafts to target tweets).

    Components:
    - audience: follower tier bonus
    - topic: topic relevance
    - urgency: time-sensitivity
    - velocity: engagement momentum
    - freshness: never-engaged bonus / over-engaged penalty
    - gates: grounding%, draft length, cooldown, repetition
    """

    def __init__(self, weights: Optional[dict] = None):
        # Default trained weights
        self.weights = weights or {
            "audience": 4.78,
            "topic": 0.69,
            "urgency": 2.71,
            "velocity": 2.31,
        }

    def score(self, ctx: ScoringContext) -> ScoreResult:
        result = ScoreResult()
        reasons = []

        # --- Quality gates (must pass ALL) ---
        cooldown_ok = calc_cooldown_ok(ctx)
        if not cooldown_ok:
            reasons.append(f"24h cooldown active (last: {ctx.last_engaged_at})")
            result.reasons = reasons
            result.passed_gates = False
            return result

        grounding_ok = calc_grounding_ok(ctx)
        if not grounding_ok:
            reasons.append(f"Grounding {ctx.grounding_pct}% < {MIN_GROUNDING}% minimum")

        draft_ok = calc_draft_quality(ctx)
        if not draft_ok:
            if ctx.draft_length < MIN_DRAFT_LENGTH:
                reasons.append(f"Draft too short ({ctx.draft_length} < {MIN_DRAFT_LENGTH} chars)")
            if ctx.overlap_with_recent > MAX_OVERLAP:
                reasons.append(f"Too similar to recent ({ctx.overlap_with_recent:.0%} overlap)")

        # Hard block check
        freshness = calc_freshness(ctx)
        if freshness == 0.0:
            reasons.append(f"Hard blocked: {ctx.previous_strikes_count}+ replies to @{ctx.author_handle}")
            result.score = 0.0
            result.reasons = reasons
            result.passed_gates = False
            return result

        # --- Score calculation ---
        w = self.weights
        base_score = (
            w.get("audience", 1.0) * min(ctx.author_followers / 1000, 10) +
            w.get("topic", 1.0) * ctx.topic_match * 10 +
            w.get("urgency", 1.0) * ctx.urgency_score * 10 +
            w.get("velocity", 1.0) * ctx.engagement_velocity * 10
        )

        # Apply freshness multiplier
        final_score = base_score * freshness

        result.score = round(final_score, 2)
        result.grounded = grounding_ok
        result.passed_gates = grounding_ok and draft_ok and cooldown_ok
        result.reasons = reasons
        result.breakdown = {
            "audience": w.get("audience", 1.0) * min(ctx.author_followers / 1000, 10),
            "topic": w.get("topic", 1.0) * ctx.topic_match * 10,
            "urgency": w.get("urgency", 1.0) * ctx.urgency_score * 10,
            "velocity": w.get("velocity", 1.0) * ctx.engagement_velocity * 10,
            "freshness_mult": freshness,
        }

        return result


class ReplyScoring(ScoringStrategy):
    """
    Scores reply scout drafts (replies to viral posts from tracked creators).

    Simpler than StrikeScoring — focuses on viral momentum and topic fit.
    """

    def score(self, ctx: ScoringContext) -> ScoreResult:
        result = ScoreResult()
        reasons = []

        # Gate: draft quality
        if not calc_draft_quality(ctx):
            reasons.append("Draft quality gate failed")
            result.passed_gates = False
            result.reasons = reasons
            return result

        # Gate: grounding
        grounding_ok = calc_grounding_ok(ctx)
        if not grounding_ok:
            reasons.append(f"Grounding {ctx.grounding_pct}% < {MIN_GROUNDING}%")

        # Score: weighted blend
        score = (
            ctx.engagement_velocity * 3.0 +
            ctx.topic_match * 5.0 +
            ctx.urgency_score * 2.0
        )

        result.score = round(score, 2)
        result.grounded = grounding_ok
        result.passed_gates = grounding_ok and calc_draft_quality(ctx)
        result.reasons = reasons
        return result


class AlphaScoring(ScoringStrategy):
    """
    Scores daily alpha opportunities (actionable insights from research feed).

    Focuses on actionability and novelty.
    """

    def score(self, ctx: ScoringContext) -> ScoreResult:
        result = ScoreResult()

        # Alpha scoring is simpler: topic match + urgency + novelty (no recent overlap)
        score = (
            ctx.topic_match * 6.0 +
            ctx.urgency_score * 3.0 +
            (1.0 - ctx.overlap_with_recent) * 2.0  # Novelty bonus
        )

        result.score = round(score, 2)
        result.grounded = ctx.grounding_pct >= 50  # Alphas have lower grounding bar
        result.passed_gates = result.score >= 3.0
        return result


class OpportunityScoring(ScoringStrategy):
    """
    Scores viral pipeline opportunities (posts to repurpose/create from).

    Focuses on viral potential and topic fit.
    """

    def score(self, ctx: ScoringContext) -> ScoreResult:
        result = ScoreResult()

        score = (
            ctx.engagement_velocity * 4.0 +
            ctx.topic_match * 3.0 +
            ctx.urgency_score * 2.0 +
            calc_freshness(ctx) * 1.5
        )

        result.score = round(score, 2)
        result.passed_gates = result.score >= 4.0
        return result


# --- Strategy Registry ---

STRATEGIES: dict[str, ScoringStrategy] = {
    "strike": StrikeScoring(),
    "reply": ReplyScoring(),
    "alpha": AlphaScoring(),
    "opportunity": OpportunityScoring(),
}


def get_strategy(content_type: str) -> ScoringStrategy:
    """Factory for scoring strategies."""
    strategy = STRATEGIES.get(content_type)
    if strategy is None:
        raise ValueError(f"Unknown scoring strategy: {content_type}. Available: {list(STRATEGIES.keys())}")
    return strategy
