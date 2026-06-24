"""
SGOS Pipeline — Autonomous Viral Content Pipeline.

Public API: wire into main.py via `from services.pipeline import pipeline_engine`.

Architecture (SOLID):
  - protocols.py    → Interface contracts (Interface Segregation)
  - genome.py       → Viral DNA extraction (Single Responsibility, Open/Closed)
  - repository.py   → Genome persistence (Single Responsibility)
  - scoring.py      → Pluggable scoring strategies (Open/Closed, Liskov)
  - generator.py    → Content variant generation (Open/Closed)
  - orchestrator.py → Pipeline coordination (Dependency Inversion)
"""
from services.pipeline.orchestrator import PipelineEngine
from services.pipeline.genome import LLMGenomeExtractor
from services.pipeline.repository import GenomeRepository
from services.pipeline.scoring import CompositeScorer, EngagementScorer, StructureScorer
from services.pipeline.generator import LLMVariantGenerator


def create_pipeline_engine() -> PipelineEngine:
    """
    Dependency injection factory.
    Wires concrete implementations into the orchestrator via protocols.
    Swap any component without touching the orchestrator.
    """
    repo = GenomeRepository()
    extractor = LLMGenomeExtractor()
    scorer = CompositeScorer([
        EngagementScorer(weight=0.4),
        StructureScorer(weight=0.6),
    ])
    generator = LLMVariantGenerator()

    return PipelineEngine(
        extractor=extractor,
        repository=repo,
        scorer=scorer,
        generator=generator,
    )


pipeline_engine = create_pipeline_engine()

__all__ = ["pipeline_engine", "create_pipeline_engine", "PipelineEngine"]
