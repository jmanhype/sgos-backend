"""
Adaptive Intelligence Tests — Voice Match + Feedback + Training.

Covers:
  - VoiceMatchScorer: TF-IDF cosine similarity scoring
  - FeedbackService: publish tracking, performance recording, analytics
  - ScorerTrainer: weight training from performance data
  - Integration: full pipeline with voice match + feedback loop
  - Edge cases: empty profiles, no data, insufficient samples
"""
import json
import math
import pytest
from unittest.mock import patch, MagicMock

from services.pipeline.voice_match import VoiceMatchScorer
from services.pipeline.protocols import ViralGenome, ContentVariant
from services.feedback import FeedbackService


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def sample_genome():
    return ViralGenome(
        post_id="test_post_1",
        hook_type="bold_claim",
        hook_text="AI will replace 50% of jobs by 2030",
        emotional_arc=["shock", "curiosity", "determination"],
        structural_pattern="hook → evidence → counterpoint → CTA",
        key_phrases=["AI", "jobs", "automation", "future"],
        content_length_words=250,
        platform_signals={"z_score": 3.5, "upvote_ratio": 0.92, "score": 5000},
        engagement_score=0.85,
        raw_post={"title": "AI Jobs", "content": "AI will replace jobs"},
    )


@pytest.fixture
def sample_variant():
    return ContentVariant(
        genome_id="test_post_1",
        variant_type="thread",
        title="The AI Job Revolution Is Closer Than You Think",
        content="""The AI job revolution isn't coming — it's here.

**50% of current jobs will be automated by 2030.**

That's not hype. That's McKinsey data.

Here's what most people miss:

- It's not about replacing humans entirely
- It's about augmenting what humans can do
- The winners will be those who adapt NOW

The secret? Don't compete with AI. Collaborate with it.

What skill are you building to stay ahead? 👇""",
        hook="The AI job revolution isn't coming — it's here.",
    )


@pytest.fixture
def feedback_db(tmp_path):
    """Create a temporary feedback database."""
    import sqlite3
    db_path = tmp_path / "test_feedback.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn, str(db_path)


# ─── VoiceMatchScorer Tests ────────────────────────────────────────────────

class TestVoiceMatchScorer:
    """Unit tests for voice similarity scoring."""

    def test_name_and_weight(self):
        scorer = VoiceMatchScorer(weight=0.3)
        assert scorer.name == "voice_match"
        assert scorer.weight == 0.3

    def test_neutral_score_when_no_profile(self, sample_variant, sample_genome):
        """Without a voice profile, returns neutral 50."""
        scorer = VoiceMatchScorer(weight=0.25, profile_name="nonexistent")
        score = scorer.score(sample_variant, sample_genome)
        assert score == 50.0

    def test_tokenize(self):
        """Tokenization extracts words >= 3 chars, lowercased."""
        tokens = VoiceMatchScorer._tokenize("Hello World! The AI is AMAZING.")
        assert "hello" in tokens
        assert "world" in tokens
        assert "amazing" in tokens
        assert "the" in tokens  # 3 chars, included
        assert "ai" not in tokens  # too short (2 chars)
        assert "is" not in tokens  # too short (2 chars)

    def test_build_tfidf(self):
        """TF-IDF vector has log-normalized term frequencies."""
        vec = VoiceMatchScorer._build_tfidf("hello world hello world hello")
        assert "hello" in vec
        assert "world" in vec
        # hello appears 3x, world 2x → hello has higher TF
        assert vec["hello"] > vec["world"]

    def test_vector_norm(self):
        """L2 norm of vector."""
        vec = {"a": 3.0, "b": 4.0}
        assert VoiceMatchScorer._vector_norm(vec) == 5.0

    def test_cosine_similarity_identical(self):
        """Identical vectors → cosine = 1.0."""
        vec = {"a": 1.0, "b": 2.0, "c": 3.0}
        norm = VoiceMatchScorer._vector_norm(vec)
        sim = VoiceMatchScorer._cosine_similarity(vec, vec, norm, norm)
        assert abs(sim - 1.0) < 0.001

    def test_cosine_similarity_orthogonal(self):
        """Orthogonal vectors → cosine = 0.0."""
        vec_a = {"a": 1.0}
        vec_b = {"b": 1.0}
        norm_a = VoiceMatchScorer._vector_norm(vec_a)
        norm_b = VoiceMatchScorer._vector_norm(vec_b)
        sim = VoiceMatchScorer._cosine_similarity(vec_a, vec_b, norm_a, norm_b)
        assert sim == 0.0

    def test_cosine_similarity_partial_overlap(self):
        """Partial overlap → 0 < cosine < 1."""
        vec_a = {"hello": 1.0, "world": 2.0, "test": 0.5}
        vec_b = {"hello": 1.5, "world": 1.0, "other": 2.0}
        norm_a = VoiceMatchScorer._vector_norm(vec_a)
        norm_b = VoiceMatchScorer._vector_norm(vec_b)
        sim = VoiceMatchScorer._cosine_similarity(vec_a, vec_b, norm_a, norm_b)
        assert 0.0 < sim < 1.0

    def test_empty_content_returns_zero(self, sample_genome):
        """Empty variant content → score = 0."""
        scorer = VoiceMatchScorer(weight=0.25)
        scorer._voice_tfidf = {"hello": 1.0}
        scorer._voice_norm = 1.0
        scorer._loaded = True
        
        variant = ContentVariant(
            genome_id="test",
            variant_type="post", title="Empty", content="", hook=""
        )
        score = scorer.score(variant, sample_genome)
        assert score == 0.0

    def test_high_similarity_score(self, sample_genome):
        """Content very similar to voice → high score."""
        scorer = VoiceMatchScorer(weight=0.25)
        scorer._loaded = True
        
        # Voice profile has tech/AI focused content
        voice_text = "AI automation jobs future technology revolution adapt skills collaborate"
        scorer._voice_tfidf = scorer._build_tfidf(voice_text)
        scorer._voice_norm = scorer._vector_norm(scorer._voice_tfidf)
        
        variant = ContentVariant(
            genome_id="test",
            variant_type="post",
            title="AI Future",
            content="AI automation is changing jobs. The technology revolution requires adaptation. Collaborate with AI to build skills for the future.",
            hook="AI is here",
        )
        score = scorer.score(variant, sample_genome)
        assert score > 30  # Should have decent overlap


# ─── FeedbackService Tests ─────────────────────────────────────────────────

class TestFeedbackService:
    """Unit + integration tests for performance feedback tracking."""

    def _make_service(self, monkeypatch, tmp_path):
        """Create a FeedbackService with a temp DB."""
        import sqlite3
        db_path = tmp_path / "feedback_test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        
        # Mock get_connection to return our temp conn
        monkeypatch.setattr("services.feedback.get_connection", lambda: conn)
        
        # Reset singleton
        FeedbackService._instance = None
        svc = FeedbackService()
        return svc, conn

    def test_mark_published(self, monkeypatch, tmp_path):
        svc, conn = self._make_service(monkeypatch, tmp_path)
        
        result = svc.mark_published(
            opportunity_id=1,
            genome_id="genome_abc",
            variant_type="thread",
            score_at_generation=72.5,
            score_breakdown='{"engagement": {"raw": 85, "weight": 0.35}}',
            platform="twitter",
        )
        
        assert result["status"] == "published"
        assert result["opportunity_id"] == 1
        assert "id" in result
        
        # Verify in DB
        row = conn.execute("SELECT * FROM performance_feedback WHERE id = ?", (result["id"],)).fetchone()
        assert row is not None
        assert row["genome_id"] == "genome_abc"
        assert row["score_at_generation"] == 72.5

    def test_record_performance(self, monkeypatch, tmp_path):
        svc, conn = self._make_service(monkeypatch, tmp_path)
        
        # First publish
        pub = svc.mark_published(1, "g1", "post", 60.0)
        fb_id = pub["id"]
        
        # Then record metrics
        result = svc.record_performance(
            feedback_id=fb_id,
            impressions=10000,
            engagements=500,
            likes=350,
            reposts=80,
            replies=70,
            clicks=200,
        )
        
        assert result["status"] == "recorded"
        assert result["engagement_rate"] == 5.0  # 500/10000 * 100
        assert result["tier"] == "viral"  # ≥5% with ≥1000 impressions

    def test_performance_tiers(self, monkeypatch, tmp_path):
        svc, _ = self._make_service(monkeypatch, tmp_path)
        
        # Viral: ≥5% ER, ≥1000 impressions
        assert svc._classify_tier(5.0, 1000) == "viral"
        assert svc._classify_tier(8.0, 5000) == "viral"
        
        # Above avg: ≥3% ER
        assert svc._classify_tier(3.5, 500) == "above_avg"
        
        # Avg: ≥1% ER
        assert svc._classify_tier(1.5, 200) == "avg"
        
        # Below avg: <1% ER
        assert svc._classify_tier(0.5, 100) == "below_avg"

    def test_get_stats_empty(self, monkeypatch, tmp_path):
        svc, _ = self._make_service(monkeypatch, tmp_path)
        stats = svc.get_stats()
        
        assert stats["total_published"] == 0
        assert stats["with_performance_data"] == 0
        assert stats["avg_engagement_rate"] == 0

    def test_get_stats_with_data(self, monkeypatch, tmp_path):
        svc, _ = self._make_service(monkeypatch, tmp_path)
        
        # Publish 3 and record performance for 2
        pub1 = svc.mark_published(1, "g1", "thread", 70.0)
        pub2 = svc.mark_published(2, "g2", "post", 65.0)
        svc.mark_published(3, "g3", "newsletter", 55.0)  # No metrics
        
        svc.record_performance(pub1["id"], impressions=5000, engagements=250)
        svc.record_performance(pub2["id"], impressions=2000, engagements=40)
        
        stats = svc.get_stats()
        assert stats["total_published"] == 3
        assert stats["with_performance_data"] == 2
        assert stats["avg_engagement_rate"] > 0

    def test_get_feedback_list(self, monkeypatch, tmp_path):
        svc, _ = self._make_service(monkeypatch, tmp_path)
        
        # Publish several
        for i in range(5):
            pub = svc.mark_published(i, f"g{i}", "post", 50.0 + i)
            svc.record_performance(pub["id"], impressions=1000, engagements=50 + i * 10)
        
        records = svc.get_feedback_list(limit=3)
        assert len(records) == 3
        
        # Filter by tier
        viral = svc.get_feedback_list(tier="viral")
        assert all(r["performance_tier"] == "viral" for r in viral)

    def test_train_insufficient_data(self, monkeypatch, tmp_path):
        svc, _ = self._make_service(monkeypatch, tmp_path)
        
        result = svc.train_weights()
        assert result["status"] == "insufficient_data"
        assert result["available"] == 0

    def test_train_with_data(self, monkeypatch, tmp_path):
        svc, _ = self._make_service(monkeypatch, tmp_path)
        
        # Create 15 published posts with performance data and varied scores
        import random
        random.seed(42)
        
        for i in range(15):
            score = random.uniform(30, 90)
            breakdown = json.dumps({
                "engagement": {"raw": random.uniform(20, 100), "weight": 0.35},
                "structure": {"raw": random.uniform(30, 80), "weight": 0.40},
                "voice_match": {"raw": random.uniform(20, 70), "weight": 0.25},
            })
            pub = svc.mark_published(i, f"g{i}", "post", score, breakdown)
            
            # Correlate engagement with structure score
            structure_raw = json.loads(breakdown)["structure"]["raw"]
            impressions = random.randint(1000, 10000)
            engagements = int(impressions * (structure_raw / 100) * random.uniform(0.02, 0.08))
            
            svc.record_performance(pub["id"], impressions=impressions, engagements=engagements)
        
        result = svc.train_weights()
        
        assert result["status"] == "trained"
        assert result["sample_size"] == 15
        assert "new_weights" in result
        assert "correlations" in result
        
        # Weights should sum to ~1.0
        total = sum(result["new_weights"].values())
        assert abs(total - 1.0) < 0.01

    def test_train_returns_trained_weights(self, monkeypatch, tmp_path):
        svc, _ = self._make_service(monkeypatch, tmp_path)
        
        # Before training
        assert svc.get_trained_weights() is None
        
        # Create enough data
        for i in range(12):
            breakdown = json.dumps({
                "engagement": {"raw": 50 + i, "weight": 0.35},
                "structure": {"raw": 60 + i, "weight": 0.40},
            })
            pub = svc.mark_published(i, f"g{i}", "post", 50.0, breakdown)
            svc.record_performance(pub["id"], impressions=2000, engagements=100 + i * 5)
        
        svc.train_weights()
        
        # After training
        weights = svc.get_trained_weights()
        assert weights is not None
        assert "engagement" in weights
        assert "structure" in weights
        assert all(0 < w < 1 for w in weights.values())

    def test_pearson_correlation(self):
        """Test Pearson correlation computation."""
        # Perfect positive correlation
        x = [1, 2, 3, 4, 5]
        y = [2, 4, 6, 8, 10]
        corr = FeedbackService._pearson_correlation(x, y)
        assert abs(corr - 1.0) < 0.001
        
        # Perfect negative correlation
        y_neg = [10, 8, 6, 4, 2]
        corr_neg = FeedbackService._pearson_correlation(x, y_neg)
        assert abs(corr_neg - (-1.0)) < 0.001
        
        # No correlation (orthogonal)
        x_ortho = [1, 2, 3, 4, 5]
        y_ortho = [5, 3, 1, 3, 5]
        corr_ortho = FeedbackService._pearson_correlation(x_ortho, y_ortho)
        assert abs(corr_ortho) < 0.5  # Should be near zero

    def test_pearson_edge_cases(self):
        """Edge cases for Pearson correlation."""
        # Empty
        assert FeedbackService._pearson_correlation([], []) == 0.0
        
        # Single point
        assert FeedbackService._pearson_correlation([1], [2]) == 0.0
        
        # All same values (zero variance)
        assert FeedbackService._pearson_correlation([5, 5, 5], [1, 2, 3]) == 0.0


# ─── Integration Tests ─────────────────────────────────────────────────────

class TestAdaptivePipeline:
    """Integration tests for the full adaptive loop."""

    def test_voice_match_in_composite(self, sample_genome):
        """VoiceMatchScorer works within CompositeScorer."""
        from services.pipeline.scoring import CompositeScorer, EngagementScorer, StructureScorer
        
        scorer = CompositeScorer([
            EngagementScorer(weight=0.35),
            StructureScorer(weight=0.40),
            VoiceMatchScorer(weight=0.25, profile_name="nonexistent"),
        ])
        
        variant = ContentVariant(
            genome_id="test",
            variant_type="thread",
            title="Test Thread",
            content="This is a test thread about AI and the future of work.",
            hook="AI is changing everything",
        )
        
        score, breakdown = scorer.score(variant, sample_genome)
        
        assert 0 <= score <= 100
        assert "voice_match" in breakdown
        assert breakdown["voice_match"]["raw"] == 50.0  # Neutral — no profile

    def test_feedback_roundtrip(self, monkeypatch, tmp_path):
        """Full cycle: publish → record → analyze."""
        import sqlite3
        db_path = tmp_path / "roundtrip.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        
        monkeypatch.setattr("services.feedback.get_connection", lambda: conn)
        FeedbackService._instance = None
        svc = FeedbackService()
        
        # 1. Publish
        pub = svc.mark_published(
            opportunity_id=42,
            genome_id="genome_viral_1",
            variant_type="thread",
            score_at_generation=78.5,
            score_breakdown='{"engagement": {"raw": 85, "weight": 0.35, "weighted": 29.75}, "structure": {"raw": 72, "weight": 0.40, "weighted": 28.8}}',
        )
        
        # 2. Record performance
        perf = svc.record_performance(
            feedback_id=pub["id"],
            impressions=25000,
            engagements=1500,
            likes=900,
            reposts=350,
            replies=250,
            clicks=500,
        )
        
        assert perf["engagement_rate"] == 6.0
        assert perf["tier"] == "viral"
        
        # 3. Check stats
        stats = svc.get_stats()
        assert stats["total_published"] == 1
        assert stats["with_performance_data"] == 1
        assert stats["avg_engagement_rate"] == 6.0

    def test_scorer_weight_evolution(self, monkeypatch, tmp_path):
        """Weights change after training with biased data."""
        import sqlite3
        db_path = tmp_path / "evolution.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        
        monkeypatch.setattr("services.feedback.get_connection", lambda: conn)
        FeedbackService._instance = None
        svc = FeedbackService()
        
        # Create data where engagement strongly predicts performance
        # but structure does NOT
        for i in range(20):
            eng_raw = 20 + i * 4  # 20-96
            struct_raw = 50  # constant — no predictive power
            breakdown = json.dumps({
                "engagement": {"raw": eng_raw, "weight": 0.35},
                "structure": {"raw": struct_raw, "weight": 0.40},
            })
            pub = svc.mark_published(i, f"g{i}", "post", 50.0, breakdown)
            
            # Engagement rate correlates with engagement raw score
            er = eng_raw / 20  # 1.0 - 4.8%
            impressions = 2000
            engagements = int(impressions * er / 100)
            svc.record_performance(pub["id"], impressions=impressions, engagements=engagements)
        
        result = svc.train_weights()
        assert result["status"] == "trained"
        
        # Engagement should have higher correlation than structure
        assert result["correlations"]["engagement"] > result["correlations"]["structure"]
        
        # Engagement weight should be higher than structure
        assert result["new_weights"]["engagement"] > result["new_weights"]["structure"]


# ─── Closed Loop Tests ─────────────────────────────────────────────────────

class TestClosedLoop:
    """Tests for the full closed loop: train → refresh → score with new weights."""

    def test_refresh_scorer_updates_weights(self, monkeypatch, tmp_path):
        """After training, refresh_scorer_from_feedback updates the pipeline scorer."""
        import sqlite3
        db_path = tmp_path / "refresh.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")

        monkeypatch.setattr("services.feedback.get_connection", lambda: conn)
        FeedbackService._instance = None
        svc = FeedbackService()

        # Create 12 data points to enable training
        for i in range(12):
            breakdown = json.dumps({
                "engagement": {"raw": 30 + i * 5, "weight": 0.35},
                "structure": {"raw": 70, "weight": 0.40},
                "voice_match": {"raw": 50, "weight": 0.25},
            })
            pub = svc.mark_published(i, f"g{i}", "post", 50.0, breakdown)
            svc.record_performance(pub["id"], impressions=2000, engagements=100 + i * 10)

        # Train
        train_result = svc.train_weights()
        assert train_result["status"] == "trained"

        # Now test refresh
        from services.pipeline import refresh_scorer_from_feedback
        refresh_result = refresh_scorer_from_feedback()
        assert refresh_result["status"] == "refreshed"
        assert "weights" in refresh_result

    def test_auto_train_and_refresh(self, monkeypatch, tmp_path):
        """auto_train_and_refresh trains + applies weights in one call."""
        import sqlite3
        db_path = tmp_path / "auto.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")

        monkeypatch.setattr("services.feedback.get_connection", lambda: conn)
        FeedbackService._instance = None
        svc = FeedbackService()

        # Seed enough data
        for i in range(15):
            breakdown = json.dumps({
                "engagement": {"raw": 40 + i * 3, "weight": 0.35},
                "structure": {"raw": 60, "weight": 0.40},
            })
            pub = svc.mark_published(i, f"g{i}", "post", 50.0, breakdown)
            svc.record_performance(pub["id"], impressions=3000, engagements=150 + i * 8)

        from services.pipeline import auto_train_and_refresh
        result = auto_train_and_refresh()
        assert result["status"] == "trained"
        assert result.get("scorer_refreshed") is True
        assert "active_weights" in result

    def test_auto_train_insufficient_data(self):
        """auto_train_and_refresh returns insufficient_data when too few records."""
        from services.pipeline import auto_train_and_refresh
        result = auto_train_and_refresh()
        assert result["status"] in ("insufficient_data", "no_correlations")

    def test_refresh_without_weights(self, monkeypatch, tmp_path):
        """refresh returns no_weights when nothing has been trained."""
        import sqlite3
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")

        monkeypatch.setattr("services.feedback.get_connection", lambda: conn)
        FeedbackService._instance = None
        FeedbackService()  # Init tables

        from services.pipeline import refresh_scorer_from_feedback
        result = refresh_scorer_from_feedback()
        assert result["status"] == "no_weights"

    def test_pipeline_engine_refresh_scorer(self, sample_genome):
        """PipelineEngine.refresh_scorer hot-swaps the scorer."""
        from services.pipeline.scoring import CompositeScorer, EngagementScorer, StructureScorer
        from services.pipeline.orchestrator import PipelineEngine
        from services.pipeline.genome import LLMGenomeExtractor
        from services.pipeline.repository import GenomeRepository
        from services.pipeline.generator import LLMVariantGenerator

        # Create engine with default weights
        scorer_v1 = CompositeScorer([
            EngagementScorer(weight=0.5),
            StructureScorer(weight=0.5),
        ])
        engine = PipelineEngine(
            extractor=LLMGenomeExtractor(),
            repository=GenomeRepository(),
            scorer=scorer_v1,
            generator=LLMVariantGenerator(),
        )

        variant = ContentVariant(
            genome_id="test", variant_type="post",
            title="Test", content="Test content with some words", hook="Hook"
        )
        score_v1, breakdown_v1 = engine._scorer.score(variant, sample_genome)

        # Now refresh with heavily skewed weights
        scorer_v2 = CompositeScorer([
            EngagementScorer(weight=0.95),
            StructureScorer(weight=0.05),
        ])
        engine.refresh_scorer(scorer_v2)

        score_v2, breakdown_v2 = engine._scorer.score(variant, sample_genome)

        # Breakdown weights should now reflect v2
        assert breakdown_v2["engagement"]["weight"] == 0.95
        assert breakdown_v2["structure"]["weight"] == 0.05

        # Scores should differ (different weight emphasis)
        # v1: 50/50, v2: 95/5 — engagement-heavy scoring
        if score_v1 != score_v2:
            assert abs(score_v1 - score_v2) > 0.1  # Meaningful difference
