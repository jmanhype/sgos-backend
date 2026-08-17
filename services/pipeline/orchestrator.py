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

        # Auto-load voice profile if no voice_prompt provided
        if not voice_prompt:
            try:
                from voice_profile import get_voice_profile, generate_voice_prompt
                profile = get_voice_profile("jay_guthrie")
                if profile:
                    voice_prompt = generate_voice_prompt(profile)
            except Exception as e:
                print(f"[warn] Voice profile error: {e}")

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

                # Step 3: Verify grounding + Score each variant
                for variant in variants:
                    # 3a: Verify claims against source material
                    grounding = self._verify_grounding(variant, genome)
                    variant.grounding_score = grounding.get("grounding_score", 0)
                    
                    # Use corrected content if verifier fixed hallucinations
                    if grounding.get("corrected_content"):
                        variant.content = grounding["corrected_content"]
                    
                    # 3b: Score the (potentially corrected) variant
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

    def get_opportunity_by_id(self, opportunity_id: int) -> dict | None:
        """Get a single opportunity by ID."""
        return self._repo.get_opportunity_by_id(opportunity_id)

    def get_opportunities_for_genome(self, post_id: str, limit: int = 5) -> list[dict]:
        """Get opportunities for a specific genome."""
        return self._repo.get_opportunities_for_genome(post_id, limit=limit)

    def dismiss_all_unseen(self, below_score: float | None = None) -> int:
        """Dismiss all unseen opportunities in a single batch SQL operation."""
        return self._repo.dismiss_all_unseen(below_score=below_score)

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

        # Re-attach source post content for grounding (raw_post is not stored in DB)
        if not genome.raw_post:
            try:
                from database import get_connection
                conn = get_connection()
                # Extract the numeric ID from post_id like "hackernews_48663324"
                db_id = post_id.split("_")[-1] if "_" in post_id else post_id
                platform = post_id.split("_")[0] if "_" in post_id else ""
                row = conn.execute(
                    "SELECT id, title, content, url, author, platform FROM posts WHERE id LIKE ?",
                    (f"%{db_id}%",),
                ).fetchone()
                if row:
                    genome.raw_post = dict(row)
            except Exception as e:
                print(f"[warn] Source enrichment error: {e}")

        # Auto-load voice profile if not provided
        if not voice_prompt:
            try:
                from voice_profile import get_voice_profile, generate_voice_prompt
                profile = get_voice_profile("jay_guthrie")
                if profile:
                    voice_prompt = generate_voice_prompt(profile)
            except Exception as e:
                print(f"[warn] Voice profile error: {e}")

        variants = self._generator.generate(
            genome,
            voice_prompt=voice_prompt,
            num_variants=num_variants,
        )

        created = 0
        for variant in variants:
            # Verify grounding before scoring
            grounding = self._verify_grounding(variant, genome)
            variant.grounding_score = grounding.get("grounding_score", 0)
            if grounding.get("corrected_content"):
                variant.content = grounding["corrected_content"]
            
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
            "grounded": genome.raw_post is not None,
        }

    # ─── Grounding Verification ─────────────────────────────────────────────

    _verifier = None

    def _verify_grounding(self, variant, genome) -> dict:
        """
        Verify that a generated variant's factual claims are grounded in source material.
        
        Returns verification dict with grounding_score, corrected_content, etc.
        On failure (no LLM, no source), returns a permissive default.
        """
        # Extract source material — try genome.raw_post first, then posts table
        source_title = ""
        source_content = ""
        source_url = ""
        if genome.raw_post:
            source_title = genome.raw_post.get("title", "")
            source_content = genome.raw_post.get("content", "")
            source_url = genome.raw_post.get("url", "")

        # If raw_post is empty/missing, look up the posts table directly
        if not source_content or len(source_content) < 100:
            try:
                from database import get_connection as _get_conn
                _conn = _get_conn()
                _row = _conn.execute(
                    "SELECT title, content, url FROM posts WHERE id = ?",
                    (genome.post_id,)
                ).fetchone()
                if _row:
                    source_title = source_title or _row["title"] or ""
                    source_url = source_url or _row["url"] or ""
                    db_content = _row["content"] or ""
                    if len(db_content) > len(source_content):
                        source_content = db_content
            except Exception as e:
                print(f"[warn] Source enrichment error: {e}")

        # Strip HTML tags to get actual text length
        import re as _re
        clean_content = _re.sub(r'<[^>]+>', ' ', source_content).strip()
        clean_content = _re.sub(r'\s+', ' ', clean_content)
        
        # If source content is still thin (just links/metadata), scrape the URL
        if len(clean_content) < 100 and source_url:
            try:
                source_content = self._scrape_url(source_url)
                if source_content:
                    genome.raw_post = genome.raw_post or {}
                    genome.raw_post["content"] = source_content
            except Exception as e:
                print(f"[warn] Source enrichment error: {e}")

        if not source_content or len(source_content) < 50:
            return {"grounding_score": 0, "corrected_content": variant.content, "verified": False}

        # Lazy-init verifier
        if self._verifier is None:
            from verifier import GroundingVerifier
            self._verifier = GroundingVerifier()

        return self._verifier.verify(
            draft_content=variant.content,
            source_title=source_title,
            source_content=source_content,
            source_url=source_url,
        )

    def _scrape_url(self, url: str) -> str:
        """Scrape article content from a URL for grounding verification."""
        # For HN posts, scrape the discussion page (richer content than article links)
        if "hackernews" in str(url) or "news.ycombinator.com" in str(url):
            try:
                return self._scrape_hn(url)
            except Exception as e:
                print(f"[warn] Source enrichment error: {e}")
        
        # Try Firecrawl on port 3002 (local 3090 server via SSH tunnel)
        try:
            import urllib.request
            import json as _json
            
            req = urllib.request.Request(
                "http://localhost:3002/v1/scrape",
                data=_json.dumps({"url": url, "formats": ["markdown"]}).encode(),
                headers={"Content-Type": "application/json"},
            )
            res = urllib.request.urlopen(req, timeout=15)
            data = _json.loads(res.read())
            md = data.get("data", {}).get("markdown", "")
            if md and len(md) > 100:
                return md[:3000]
        except Exception as e:
            print(f"[warn] Source enrichment error: {e}")
        
        # Fallback: simple fetch with urllib
        try:
            import urllib.request
            import re
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            res = urllib.request.urlopen(req, timeout=10)
            html = res.read().decode("utf-8", errors="replace")
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 100:
                return text[:3000]
        except Exception as e:
            print(f"[warn] Source enrichment error: {e}")
        
        return ""

    def _scrape_hn(self, url_or_id: str) -> str:
        """Scrape HN discussion page for rich source content."""
        import urllib.request
        import re
        
        # Extract HN item ID
        item_id = None
        if "item?id=" in str(url_or_id):
            item_id = str(url_or_id).split("item?id=")[-1].split("&")[0]
        elif url_or_id.isdigit():
            item_id = url_or_id
        
        if not item_id:
            return ""
        
        hn_url = f"https://news.ycombinator.com/item?id={item_id}"
        req = urllib.request.Request(hn_url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        res = urllib.request.urlopen(req, timeout=15)
        html = res.read().decode("utf-8", errors="replace")
        
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'&[a-z]+;', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text[:5000] if len(text) > 100 else ""
