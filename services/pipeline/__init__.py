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

__all__ = ["pipeline_engine", "create_pipeline_engine", "PipelineEngine"]
