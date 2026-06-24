"""
SGOS Pipeline — Autonomous Viral Content Pipeline.

Public API: wire into main.py via `from services.pipeline import pipeline_engine`.

Architecture (SOLID):
  - protocols.py    → Interface contracts (Interface Segregation)
  - genome.py       → Viral DNA extraction (Single Responsibility, Open/Closed)
  - repository.py   → Genome persistence (Single Responsibility)
  - scoring.py      → Pluggable scoring strategies (Open/Closed, Liskov)
  - voice_match.py  → Voice similarity scoring (Open/Closed)
  - generator.py    → Content variant generation (Open/Closed)
  - orchestrator.py → Pipeline coordination (Dependency Inversion)
"""
from services.pipeline.orchestrator import PipelineEngine
from services.pipeline.genome import LLMGenomeExtractor
from services.pipeline.repository import GenomeRepository
from services.pipeline.scoring import CompositeScorer, EngagementScorer, StructureScorer
from services.pipeline.voice_match import VoiceMatchScorer
from services.pipeline.generator import LLMVariantGenerator
from services.feedback import feedback_service


def create_pipeline_engine() -> PipelineEngine:
    """
    Dependency injection factory.
    Wires concrete implementations into the orchestrator via protocols.
    Swap any component without touching the orchestrator.
    
    Scorer weights are trained from performance feedback data when available.
    """
    repo = GenomeRepository()
    extractor = LLMGenomeExtractor()
    
    # Use trained weights if available, otherwise defaults
    trained = feedback_service.get_trained_weights()
    
    if trained:
        scorers = []
        if "engagement" in trained:
            scorers.append(EngagementScorer(weight=trained["engagement"]))
        if "structure" in trained:
            scorers.append(StructureScorer(weight=trained["structure"]))
        if "voice_match" in trained:
            scorers.append(VoiceMatchScorer(weight=trained["voice_match"]))
        if not scorers:
            scorers = _default_scorers()
    else:
        scorers = _default_scorers()
    
    scorer = CompositeScorer(scorers)
    generator = LLMVariantGenerator()

    return PipelineEngine(
        extractor=extractor,
        repository=repo,
        scorer=scorer,
        generator=generator,
    )


def _default_scorers() -> list:
    """Default scorer configuration."""
    return [
        EngagementScorer(weight=0.35),
        StructureScorer(weight=0.40),
        VoiceMatchScorer(weight=0.25),
    ]


pipeline_engine = create_pipeline_engine()


def refresh_scorer_from_feedback() -> dict:
    """
    Rebuild the pipeline scorer with latest trained weights.
    Called after training or on scheduler tick.
    Returns training status dict.
    """
    trained = feedback_service.get_trained_weights()
    if not trained:
        return {"status": "no_weights", "message": "No trained weights yet"}
    
    scorers = []
    if "engagement" in trained:
        scorers.append(EngagementScorer(weight=trained["engagement"]))
    if "structure" in trained:
        scorers.append(StructureScorer(weight=trained["structure"]))
    if "voice_match" in trained:
        scorers.append(VoiceMatchScorer(weight=trained["voice_match"]))
    
    if not scorers:
        return {"status": "no_weights"}
    
    new_scorer = CompositeScorer(scorers)
    pipeline_engine.refresh_scorer(new_scorer)
    
    return {
        "status": "refreshed",
        "weights": trained,
    }


def auto_train_and_refresh() -> dict:
    """
    Attempt to train weights from feedback data and refresh the scorer.
    Called by scheduler after pipeline runs.
    """
    result = feedback_service.train_weights()
    if result["status"] == "trained":
        refresh_result = refresh_scorer_from_feedback()
        result["scorer_refreshed"] = True
        result["active_weights"] = refresh_result.get("weights")
    return result


__all__ = [
    "pipeline_engine", "create_pipeline_engine", "PipelineEngine",
    "refresh_scorer_from_feedback", "auto_train_and_refresh",
]
