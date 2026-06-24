"""
Pipeline Orchestrator — Coordinates the full viral content pipeline.

SOLID:
  - Single Responsibility: Only coordinates flow, no extraction/scoring/generation logic.
  - Dependency Inversion: Depends on protocols (interfaces), never concrete classes.
    Swap any component via constructor injection without touching this class.
"""
from datetime import datetime, timezone

from services.pipeline.protocols import (
    IGenomeExtractor,
    IGenomeRepository,
    ICompositeScorer,
    IVariantGenerator,
    ViralGenome,
    ContentVariant,
)


class PipelineEngine:
    """
    Autonomous Viral Content Pipeline orchestrator.

    Flow:
      1. Detect outliers (from research service)
      2. Extract genomes from new outliers
      3. Generate content variants matched to voice profiles
      4. Score and rank variants
      5. Store opportunities for user consumption

    All dependencies are injected via constructor (Dependency Inversion).
    """

    def __init__(
        self,
        extractor: IGenomeExtractor,
        repository: IGenomeRepository,
        scorer: ICompositeScorer,
        generator: IVariantGenerator,
    ):
        self._extractor = extractor
        self._repo = repository
        self._scorer = scorer
        self._generator = generator

    def process_outliers(
        self,
        outliers: list[dict],
        voice_prompt: str = "",
        num_variants: int = 3,
        skip_existing: bool = True,
    ) -> dict:
        """
        Full pipeline: outliers → genomes → variants → scored opportunities.

        Args:
            outliers: List of post dicts (from research service)
            voice_prompt: Optional voice/style guide for generation
            num_variants: How many variants per genome
            skip_existing: Skip posts that already have genomes

        Returns:
            Pipeline execution summary
        """
        results = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "outliers_processed": 0,
            "genomes_extracted": 0,
            "variants_generated": 0,
            "opportunities_created": 0,
            "errors": [],
        }

        for post in outliers:
            post_id = post.get("id", "")
            results["outliers_processed"] += 1

            try:
                # Step 1: Extract genome (skip if exists)
                if skip_existing and self._repo.exists(post_id):
                    continue

                genome = self._extractor.extract(post)
                self._repo.save(genome)
                results["genomes_extracted"] += 1

                # Step 2: Generate variants
                variants = self._generator.generate(
                    genome,
                    voice_prompt=voice_prompt,
                    num_variants=num_variants,
                )

                # Step 3: Score each variant
                for variant in variants:
                    score, breakdown = self._scorer.score(variant, genome)
                    variant.score = score
                    variant.score_breakdown = breakdown
                    opp_id = self._repo.save_opportunity(variant)
                    if opp_id != -1:  # -1 = duplicate, skipped
                        results["opportunities_created"] += 1

                results["variants_generated"] += len(variants)

            except Exception as e:
                results["errors"].append({
                    "post_id": post_id,
                    "error": str(e),
                })

        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        return results

    def get_opportunities(self, limit: int = 10, unseen_only: bool = True) -> list[dict]:
        """Get ranked content opportunities."""
        return self._repo.get_opportunities(limit=limit, unseen_only=unseen_only)

    def mark_viewed(self, opportunity_id: int) -> None:
        """Mark an opportunity as viewed."""
        self._repo.mark_viewed(opportunity_id)

    def dismiss(self, opportunity_id: int) -> None:
        """Dismiss an opportunity."""
        self._repo.dismiss_opportunity(opportunity_id)

    def get_genomes(self, limit: int = 20) -> list[dict]:
        """List recent genomes."""
        return [g.to_dict() for g in self._repo.list_recent(limit=limit)]

    def get_top_genomes(self, limit: int = 5) -> list[dict]:
        """Get highest-engagement genomes."""
        return [g.to_dict() for g in self._repo.get_top_genomes(limit=limit)]

    def refresh_scorer(self, new_scorer: ICompositeScorer) -> None:
        """Hot-swap the scorer (called after weight training)."""
        self._scorer = new_scorer

    def get_stats(self) -> dict:
        """Get pipeline statistics."""
        return self._repo.get_stats()

    def regenerate_for_genome(
        self,
        post_id: str,
        voice_prompt: str = "",
        num_variants: int = 3,
    ) -> dict:
        """Re-generate variants for an existing genome."""
        genome = self._repo.get(post_id)
        if not genome:
            return {"error": f"Genome not found for post_id: {post_id}"}

        variants = self._generator.generate(
            genome,
            voice_prompt=voice_prompt,
            num_variants=num_variants,
        )

        created = 0
        for variant in variants:
            score, breakdown = self._scorer.score(variant, genome)
            variant.score = score
            variant.score_breakdown = breakdown
            opp_id = self._repo.save_opportunity(variant)
            if opp_id != -1:
                created += 1

        return {
            "genome_id": post_id,
            "variants_generated": created,
            "hook_type": genome.hook_type,
            "pattern": genome.structural_pattern,
        }
