"""
Pipeline Tests — Comprehensive test suite for the Autonomous Viral Content Pipeline.

Covers: unit tests, integration tests, edge cases, error handling, SOLID compliance.
"""
import pytest
from services.pipeline.protocols import ViralGenome, ContentVariant
from services.pipeline.genome import LLMGenomeExtractor
from services.pipeline.repository import GenomeRepository
from services.pipeline.scoring import (
    EngagementScorer,
    StructureScorer,
    CompositeScorer,
)
from services.pipeline.generator import LLMVariantGenerator
from services.pipeline.orchestrator import PipelineEngine


# ─── Test Fixtures ───────────────────────────────────────────────────────────

SAMPLE_POST = {
    "id": "reddit_abc123",
    "platform": "reddit",
    "platform_id": "abc123",
    "subreddit": "technology",
    "title": "Why I Quit My $300K Google Job to Build a $500/mo SaaS",
    "content": "After 7 years at Google, I left to build something of my own. "
               "The money was great but I was miserable. Here's what I learned "
               "about going from big tech to indie hacker. First, the reality: "
               "my SaaS makes $500/mo. That's not a typo. But I'm happier than "
               "I've ever been. The 5 things I wish I knew before quitting...",
    "author": "techrebel",
    "url": "https://reddit.com/r/technology/abc123",
    "score": 15420,
    "comment_count": 892,
    "upvote_ratio": 0.94,
    "z_score": 4.2,
    "created_at": "2026-06-24T10:00:00Z",
    "ingested_at": "2026-06-24T12:00:00Z",
}

SAMPLE_POST_2 = {
    "id": "reddit_def456",
    "platform": "reddit",
    "platform_id": "def456",
    "subreddit": "startups",
    "title": "7 Tools That Replaced My Entire Dev Team",
    "content": "I replaced 3 developers with these AI tools. Here's the stack...",
    "author": "leanfounder",
    "url": "https://reddit.com/r/startups/def456",
    "score": 8900,
    "comment_count": 456,
    "upvote_ratio": 0.89,
    "z_score": 3.1,
    "created_at": "2026-06-24T08:00:00Z",
    "ingested_at": "2026-06-24T11:00:00Z",
}


def make_genome(**overrides) -> ViralGenome:
    """Factory for test genomes."""
    defaults = {
        "post_id": "test_post_001",
        "hook_type": "story",
        "hook_text": "Why I Quit My $300K Google Job",
        "emotional_arc": ["curiosity", "surprise", "inspiration"],
        "structural_pattern": "narrative",
        "key_phrases": ["$300K", "quit Google", "indie hacker"],
        "content_length_words": 250,
        "platform_signals": {
            "platform": "reddit",
            "score": 15420,
            "z_score": 4.2,
            "upvote_ratio": 0.94,
            "comment_count": 892,
        },
        "engagement_score": 0.85,
    }
    defaults.update(overrides)
    return ViralGenome(**defaults)


def make_variant(**overrides) -> ContentVariant:
    """Factory for test variants."""
    defaults = {
        "genome_id": "test_post_001",
        "variant_type": "thread",
        "title": "Why I Left Big Tech for $500/mo",
        "content": "1/ I quit my $300K Google job to build a SaaS.\n\n"
                   "It makes $500/mo. Here's why I'm happier.\n\n"
                   "2/ After 7 years, the money stopped mattering.\n\n"
                   "I was building someone else's dream.\n\n"
                   "3/ The 5 things I wish I knew:\n"
                   "- Revenue ≠ happiness\n"
                   "- Ship fast, perfect later\n"
                   "- Talk to users daily\n"
                   "- Your network is everything\n"
                   "- Health > hustle\n\n"
                   "4/ Would I do it again? In a heartbeat.\n\n"
                   "Follow for more indie hacker insights. 🔥",
        "score": 0.0,
        "score_breakdown": {},
        "hook": "1/ I quit my $300K Google job to build a SaaS.",
    }
    defaults.update(overrides)
    return ContentVariant(**defaults)


# ─── Unit Tests: ViralGenome ────────────────────────────────────────────────

class TestViralGenome:
    def test_creation(self):
        g = make_genome()
        assert g.post_id == "test_post_001"
        assert g.hook_type == "story"
        assert g.engagement_score == 0.85

    def test_to_dict(self):
        g = make_genome()
        d = g.to_dict()
        assert "post_id" in d
        assert "hook_type" in d
        assert "emotional_arc" in d
        assert d["engagement_score"] == 0.85

    def test_from_dict(self):
        original = make_genome()
        d = original.to_dict()
        restored = ViralGenome.from_dict(d)
        assert restored.post_id == original.post_id
        assert restored.hook_type == original.hook_type
        assert restored.emotional_arc == original.emotional_arc

    def test_empty_genome(self):
        g = ViralGenome(post_id="empty", hook_type="", hook_text="", emotional_arc=[],
                        structural_pattern="", key_phrases=[], content_length_words=0,
                        platform_signals={}, engagement_score=0.0)
        assert g.engagement_score == 0.0
        assert g.key_phrases == []


# ─── Unit Tests: ContentVariant ─────────────────────────────────────────────

class TestContentVariant:
    def test_creation(self):
        v = make_variant()
        assert v.genome_id == "test_post_001"
        assert v.variant_type == "thread"
        assert v.score == 0.0

    def test_to_dict(self):
        v = make_variant(score=75.5)
        d = v.to_dict()
        assert d["score"] == 75.5
        assert "content" in d

    def test_empty_content(self):
        v = make_variant(content="", title="")
        assert v.content == ""
        d = v.to_dict()
        assert d["content"] == ""


# ─── Unit Tests: Genome Extractor ───────────────────────────────────────────

class TestGenomeExtractor:
    def setup_method(self):
        self.extractor = LLMGenomeExtractor()

    def test_rule_based_fallback(self):
        """Rule-based extractor should always work (no LLM dependency)."""
        genome = self.extractor._rule_based_extract(SAMPLE_POST)
        assert genome.post_id == "reddit_abc123"
        assert genome.hook_type in LLMGenomeExtractor.HOOK_TYPES
        assert genome.structural_pattern in LLMGenomeExtractor.STRUCTURAL_PATTERNS
        assert genome.engagement_score > 0

    def test_hook_detection_question(self):
        assert self.extractor._detect_hook_type("Is AI going to replace developers?") == "question"

    def test_hook_detection_list(self):
        assert self.extractor._detect_hook_type("10 ways to improve your code") == "list"

    def test_hook_detection_statistic(self):
        assert self.extractor._detect_hook_type("95% of startups fail") == "statistic"

    def test_hook_detection_story(self):
        assert self.extractor._detect_hook_type("I built a tool that makes $10K/mo") == "story"

    def test_hook_detection_contrarian(self):
        assert self.extractor._detect_hook_type("Stop using React, it's wrong for most projects") == "contrarian"

    def test_pattern_detection_listicle(self):
        assert self.extractor._detect_pattern("title", "1. First thing\n2. Second thing") == "listicle"

    def test_pattern_detection_how_to(self):
        assert self.extractor._detect_pattern("title", "Step 1, do this. Second, do that.") == "how_to"

    def test_engagement_score_high_z(self):
        score = self.extractor._compute_engagement_score({"z_score": 5.0, "upvote_ratio": 0.95, "comment_count": 500})
        assert score > 0.5

    def test_engagement_score_low_z(self):
        score = self.extractor._compute_engagement_score({"z_score": 0.5, "upvote_ratio": 0.5, "comment_count": 0})
        assert score < 0.3

    def test_key_phrase_extraction(self):
        text = 'She said "this changes everything" and then the BREAKTHROUGH happened'
        phrases = self.extractor._extract_key_phrases(text)
        assert len(phrases) > 0

    def test_emotional_arc(self):
        text = "This is amazing and shocking, I wonder why it works"
        emotions = self.extractor._detect_emotional_arc(text)
        assert len(emotions) > 0


# ─── Unit Tests: Scoring System ─────────────────────────────────────────────

class TestScoringSystem:
    def test_engagement_scorer_high(self):
        scorer = EngagementScorer(weight=0.5)
        genome = make_genome(engagement_score=0.9)
        variant = make_variant()
        score = scorer.score(variant, genome)
        assert score > 50

    def test_engagement_scorer_low(self):
        scorer = EngagementScorer(weight=0.5)
        genome = make_genome(engagement_score=0.1)
        variant = make_variant()
        score = scorer.score(variant, genome)
        assert score < 50

    def test_structure_scorer_good_content(self):
        scorer = StructureScorer(weight=0.5)
        genome = make_genome()
        variant = make_variant()
        score = scorer.score(variant, genome)
        assert 0 <= score <= 100

    def test_structure_scorer_empty_content(self):
        scorer = StructureScorer(weight=0.5)
        genome = make_genome()
        variant = make_variant(content="", title="")
        score = scorer.score(variant, genome)
        assert score < 40

    def test_composite_scorer(self):
        scorer = CompositeScorer([
            EngagementScorer(weight=0.4),
            StructureScorer(weight=0.6),
        ])
        genome = make_genome(engagement_score=0.8)
        variant = make_variant()
        final_score, breakdown = scorer.score(variant, genome)
        assert 0 <= final_score <= 100
        assert "engagement" in breakdown
        assert "structure" in breakdown
        assert breakdown["engagement"]["weight"] == 0.4

    def test_composite_scorer_zero_weight_raises(self):
        with pytest.raises(ValueError):
            CompositeScorer([EngagementScorer(weight=0.0)])

    def test_scorer_name_property(self):
        e = EngagementScorer()
        s = StructureScorer()
        assert e.name == "engagement"
        assert s.name == "structure"

    def test_scorer_weight_property(self):
        e = EngagementScorer(weight=0.7)
        assert e.weight == 0.7


# ─── Unit Tests: Generator ──────────────────────────────────────────────────

class TestGenerator:
    def setup_method(self):
        self.generator = LLMVariantGenerator()

    def test_template_fallback(self):
        """Template generator should always work without LLM."""
        genome = make_genome()
        variants = self.generator._template_generate(genome, "", 3)
        assert len(variants) == 3
        for v in variants:
            assert v.genome_id == genome.post_id
            assert v.variant_type in LLMVariantGenerator.VARIANT_TYPES
            assert len(v.content) > 0

    def test_template_with_voice(self):
        genome = make_genome()
        variants = self.generator._template_generate(genome, "Write in casual, punchy tone", 2)
        assert len(variants) == 2
        assert "casual" in variants[0].content.lower() or "voice" in variants[0].content.lower()

    def test_template_variant_types(self):
        genome = make_genome()
        variants = self.generator._template_generate(genome, "", 5)
        types = [v.variant_type for v in variants]
        assert types == ["thread", "post", "newsletter", "script", "carousel"]

    def test_adapt_hook_short(self):
        hook = "This is a very long hook that should be truncated for short formats like scripts"
        adapted = self.generator._adapt_hook(hook, "script")
        assert len(adapted.split()) <= 13  # 12 words + ellipsis

    def test_adapt_hook_long_format(self):
        hook = "Short hook"
        adapted = self.generator._adapt_hook(hook, "thread")
        assert adapted == hook

    def test_json_parse_markdown(self):
        text = '```json\n[{"type": "thread", "title": "Test"}]\n```'
        result = self.generator._parse_json(text)
        assert isinstance(result, list)
        assert result[0]["type"] == "thread"

    def test_json_parse_invalid(self):
        result = self.generator._parse_json("not json at all")
        assert result == []


# ─── Integration Tests: Repository ──────────────────────────────────────────

class TestGenomeRepository:
    def setup_method(self):
        self.repo = GenomeRepository()

    def test_save_and_get(self):
        genome = make_genome(post_id="test_save_get")
        self.repo.save(genome)
        retrieved = self.repo.get("test_save_get")
        assert retrieved is not None
        assert retrieved.hook_type == genome.hook_type
        assert retrieved.engagement_score == genome.engagement_score

    def test_exists(self):
        genome = make_genome(post_id="test_exists")
        self.repo.save(genome)
        assert self.repo.exists("test_exists")
        assert not self.repo.exists("nonexistent_post")

    def test_list_recent(self):
        genomes = self.repo.list_recent(limit=5)
        assert isinstance(genomes, list)
        assert len(genomes) <= 5

    def test_get_top_genomes(self):
        # Save genomes with different engagement scores
        for i, score in enumerate([0.1, 0.5, 0.9]):
            g = make_genome(post_id=f"test_top_{i}", engagement_score=score)
            self.repo.save(g)
        top = self.repo.get_top_genomes(limit=2)
        assert len(top) <= 2
        if len(top) >= 2:
            assert top[0].engagement_score >= top[1].engagement_score

    def test_save_and_get_opportunity(self):
        genome = make_genome(post_id="test_opp_genome")
        self.repo.save(genome)

        variant = make_variant(genome_id="test_opp_genome", score=80.0)
        opp_id = self.repo.save_opportunity(variant)
        assert opp_id > 0

        opps = self.repo.get_opportunities(limit=10, unseen_only=False)
        assert any(o["id"] == opp_id for o in opps)

    def test_mark_viewed(self):
        genome = make_genome(post_id="test_viewed_genome")
        self.repo.save(genome)
        variant = make_variant(genome_id="test_viewed_genome")
        opp_id = self.repo.save_opportunity(variant)
        self.repo.mark_viewed(opp_id)
        # Should not appear in unseen
        opps = self.repo.get_opportunities(limit=50, unseen_only=True)
        assert not any(o["id"] == opp_id for o in opps)

    def test_dismiss(self):
        genome = make_genome(post_id="test_dismiss_genome")
        self.repo.save(genome)
        variant = make_variant(genome_id="test_dismiss_genome")
        opp_id = self.repo.save_opportunity(variant)
        self.repo.dismiss_opportunity(opp_id)
        opps = self.repo.get_opportunities(limit=50, unseen_only=True)
        assert not any(o["id"] == opp_id for o in opps)

    def test_stats(self):
        stats = self.repo.get_stats()
        assert "total_genomes" in stats
        assert "total_opportunities" in stats
        assert "unseen_opportunities" in stats


# ─── Integration Tests: Pipeline Orchestrator ───────────────────────────────

class TestPipelineEngine:
    def setup_method(self):
        # Clean pipeline tables to avoid content_hash dedup collisions
        from database import get_connection
        conn = get_connection()
        conn.execute("DELETE FROM pipeline_opportunities")
        conn.execute("DELETE FROM viral_genomes")
        conn.commit()

        self.engine = PipelineEngine(
            extractor=LLMGenomeExtractor(),
            repository=GenomeRepository(),
            scorer=CompositeScorer([
                EngagementScorer(weight=0.4),
                StructureScorer(weight=0.6),
            ]),
            generator=LLMVariantGenerator(),
        )

    def test_process_outliers(self):
        """Full pipeline: outliers → genomes → variants → opportunities."""
        result = self.engine.process_outliers(
            outliers=[SAMPLE_POST],
            num_variants=2,
            skip_existing=False,
        )
        assert result["outliers_processed"] == 1
        assert result["genomes_extracted"] == 1
        assert result["variants_generated"] == 2
        assert result["opportunities_created"] == 2
        assert result["errors"] == []

    def test_process_multiple_outliers(self):
        result = self.engine.process_outliers(
            outliers=[SAMPLE_POST, SAMPLE_POST_2],
            num_variants=2,
            skip_existing=False,
        )
        assert result["outliers_processed"] == 2
        assert result["genomes_extracted"] == 2
        assert result["opportunities_created"] == 4

    def test_skip_existing(self):
        """Pipeline should skip posts that already have genomes."""
        # First run
        self.engine.process_outliers(
            outliers=[SAMPLE_POST],
            num_variants=2,
            skip_existing=False,
        )
        # Second run with skip
        result = self.engine.process_outliers(
            outliers=[SAMPLE_POST],
            num_variants=2,
            skip_existing=True,
        )
        assert result["genomes_extracted"] == 0

    def test_get_opportunities(self):
        self.engine.process_outliers(
            outliers=[SAMPLE_POST],
            num_variants=2,
            skip_existing=False,
        )
        opps = self.engine.get_opportunities(limit=10, unseen_only=False)
        assert isinstance(opps, list)
        assert len(opps) > 0

    def test_get_genomes(self):
        genomes = self.engine.get_genomes(limit=10)
        assert isinstance(genomes, list)

    def test_get_top_genomes(self):
        top = self.engine.get_top_genomes(limit=3)
        assert isinstance(top, list)

    def test_stats(self):
        stats = self.engine.get_stats()
        assert "total_genomes" in stats
        assert "total_opportunities" in stats

    def test_regenerate(self):
        # First create a genome
        self.engine.process_outliers(
            outliers=[SAMPLE_POST],
            num_variants=1,
            skip_existing=False,
        )
        # Clear opportunities so regeneration doesn't hit content_hash dedup
        from database import get_connection
        get_connection().execute("DELETE FROM pipeline_opportunities")
        get_connection().commit()

        result = self.engine.regenerate_for_genome(
            post_id=SAMPLE_POST["id"],
            num_variants=2,
        )
        assert result["variants_generated"] == 2

    def test_regenerate_missing(self):
        result = self.engine.regenerate_for_genome(post_id="nonexistent")
        assert "error" in result

    def test_with_voice_prompt(self):
        result = self.engine.process_outliers(
            outliers=[SAMPLE_POST],
            voice_prompt="Write in a casual, witty tone with short sentences.",
            num_variants=2,
            skip_existing=False,
        )
        assert result["genomes_extracted"] == 1
        assert result["opportunities_created"] == 2


# ─── Edge Cases ──────────────────────────────────────────────────────────────

class TestEdgeCases:
    def setup_method(self):
        self.extractor = LLMGenomeExtractor()

    def test_empty_post(self):
        post = {"id": "empty", "title": "", "content": "", "score": 0}
        genome = self.extractor._rule_based_extract(post)
        assert genome.post_id == "empty"
        assert genome.hook_type in LLMGenomeExtractor.HOOK_TYPES

    def test_post_missing_fields(self):
        post = {"id": "minimal"}
        genome = self.extractor._rule_based_extract(post)
        assert genome.post_id == "minimal"

    def test_very_long_content(self):
        post = {
            "id": "long",
            "title": "Test",
            "content": "word " * 10000,
            "score": 100,
            "comment_count": 50,
            "upvote_ratio": 0.9,
            "z_score": 3.0,
        }
        genome = self.extractor._rule_based_extract(post)
        assert genome.content_length_words > 0

    def test_special_characters_in_title(self):
        post = {"id": "special", "title": "Héllo Wörld 🔥 — 'quotes' & <html>", "score": 100}
        genome = self.extractor._rule_based_extract(post)
        assert genome.post_id == "special"

    def test_zero_engagement(self):
        score = self.extractor._compute_engagement_score({})
        assert score >= 0
        assert score <= 1


# ─── SOLID Compliance Tests ─────────────────────────────────────────────────

class TestSOLIDCompliance:
    """Verify the architecture follows SOLID principles."""

    def test_protocol_is_runtime_checkable(self):
        """Protocols should be runtime_checkable for isinstance checks."""
        from services.pipeline.protocols import IGenomeExtractor
        extractor = LLMGenomeExtractor()
        assert isinstance(extractor, IGenomeExtractor)

    def test_dependency_inversion(self):
        """Orchestrator should accept any protocol implementation."""
        engine = PipelineEngine(
            extractor=LLMGenomeExtractor(),
            repository=GenomeRepository(),
            scorer=CompositeScorer([EngagementScorer()]),
            generator=LLMVariantGenerator(),
        )
        assert engine is not None

    def test_open_closed_new_scorer(self):
        """New scorers should be addable without modifying existing code."""
        class NoveltyScorer:
            name = "novelty"
            weight = 0.3
            def score(self, variant, genome):
                return 75.0  # Fixed score for test

        composite = CompositeScorer([
            EngagementScorer(weight=0.3),
            StructureScorer(weight=0.4),
            NoveltyScorer(),
        ])
        genome = make_genome()
        variant = make_variant()
        score, breakdown = composite.score(variant, genome)
        assert "novelty" in breakdown
        assert 0 <= score <= 100

    def test_single_responsibility(self):
        """Each class should have one clear job."""
        # Extractor only extracts
        extractor = LLMGenomeExtractor()
        assert hasattr(extractor, "extract")
        assert not hasattr(extractor, "save")
        assert not hasattr(extractor, "score")

        # Scorer only scores
        scorer = EngagementScorer()
        assert hasattr(scorer, "score")
        assert not hasattr(scorer, "extract")
        assert not hasattr(scorer, "save")


# ─── Regression Tests (2026-06-25 E2E Audit) ────────────────────────────────

class TestTemplateGeneratorRegression:
    """Regression: template generator must produce actual content, not skeleton briefs."""

    def _make_genome(self, hook="Why I Quit My Job", pattern="narrative"):
        return ViralGenome(
            post_id="test_reg_001",
            hook_type="story",
            hook_text=hook,
            emotional_arc=["curiosity", "insight", "action"],
            structural_pattern=pattern,
            key_phrases=["quit my job", "build something new", "indie hacker"],
            content_length_words=200,
            platform_signals={"platform": "reddit"},
            engagement_score=0.8,
            raw_post={"id": "test_reg_001"},
        )

    def test_template_generates_real_content_not_briefs(self):
        """Variants must contain actual content, not 'This is a content brief' scaffolding."""
        gen = LLMVariantGenerator()
        genome = self._make_genome()
        variants = gen.generate(genome, num_variants=3)

        assert len(variants) == 3
        for v in variants:
            # Must NOT contain the old scaffolding text
            assert "This is a content brief" not in v.content, \
                f"Variant {v.variant_type} still has skeleton scaffolding"
            assert "**Key angles to explore:**" not in v.content, \
                f"Variant {v.variant_type} still has skeleton format"
            # Must contain the hook
            assert "Quit My Job" in v.content or "quit my job" in v.content.lower()

    def test_thread_has_numbered_tweets(self):
        """Thread variant must have numbered tweets."""
        gen = LLMVariantGenerator()
        genome = self._make_genome()
        variants = gen.generate(genome, num_variants=3)

        thread = next(v for v in variants if v.variant_type == "thread")
        assert "1/" in thread.content
        assert "🧵" in thread.content

    def test_script_has_section_markers(self):
        """Script variant must have [HOOK], [SETUP], [CLOSE] markers."""
        gen = LLMVariantGenerator()
        genome = self._make_genome()
        variants = gen._template_generate(genome, "", num_variants=5)

        script = next(v for v in variants if v.variant_type == "script")
        assert "[HOOK" in script.content
        assert "[SETUP" in script.content
        assert "[CLOSE" in script.content

    def test_carousel_has_slides(self):
        """Carousel variant must have numbered slides."""
        gen = LLMVariantGenerator()
        genome = self._make_genome()
        variants = gen._template_generate(genome, "", num_variants=5)

        carousel = next(v for v in variants if v.variant_type == "carousel")
        assert "Slide 1" in carousel.content
        assert "SUMMARY" in carousel.content


class TestHookDetectionRegression:
    """Regression: hook detection must classify beyond just 'bold_claim'."""

    def setup_method(self):
        self.extractor = LLMGenomeExtractor()

    def test_story_detection(self):
        """Personal narrative titles should detect as 'story', not 'bold_claim'."""
        story_titles = [
            "I built a local LLM for my wife's tax work",
            "I tried the exact replica prompt 101 times",
            "I was on that flight — here's what happened",
            "Thanks to AI, I restored a photo of my late uncle",
            "I quit my job to build a SaaS",
            "Victory: my wife finally recognized my hobby",
        ]
        for title in story_titles:
            post = {"id": f"test_{title[:10]}", "title": title, "content": "", "score": 100, "comment_count": 10}
            genome = self.extractor._rule_based_extract(post)
            assert genome.hook_type == "story", \
                f"Title '{title}' should be 'story' but got '{genome.hook_type}'"

    def test_question_detection(self):
        """Titles ending in ? should be 'question'."""
        post = {"id": "test_q", "title": "Is Grok openly rebelling?", "content": "", "score": 100, "comment_count": 10}
        genome = self.extractor._rule_based_extract(post)
        assert genome.hook_type == "question"

    def test_list_detection(self):
        """Titles with 'N ways/tips/things' should be 'list'."""
        post = {"id": "test_l", "title": "7 Ways to Make Your AI Sound Human", "content": "", "score": 100, "comment_count": 10}
        genome = self.extractor._rule_based_extract(post)
        assert genome.hook_type == "list"

    def test_statistic_detection(self):
        """Titles with percentages/multipliers should be 'statistic'."""
        post = {"id": "test_s", "title": "This prompt increased my output by 300%", "content": "", "score": 100, "comment_count": 10}
        genome = self.extractor._rule_based_extract(post)
        assert genome.hook_type == "statistic"

    def test_contrarian_detection(self):
        """Titles with contrarian keywords should detect correctly."""
        post = {"id": "test_c", "title": "Everyone is wrong about prompt engineering", "content": "", "score": 100, "comment_count": 10}
        genome = self.extractor._rule_based_extract(post)
        assert genome.hook_type == "contrarian"

    def test_tutorial_detection(self):
        """Tutorial keywords should detect as 'tutorial'."""
        post = {"id": "test_t", "title": "A beginner's guide to fine-tuning LoRAs", "content": "", "score": 100, "comment_count": 10}
        genome = self.extractor._rule_based_extract(post)
        assert genome.hook_type == "tutorial"
