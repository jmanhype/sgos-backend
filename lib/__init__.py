"""
SGOS Core Library — Design Pattern Infrastructure

Patterns implemented:
- Repository: DB access abstraction (no raw SQL in routers)
- Strategy: Pluggable scoring for different content types
- Observer: Event bus for cross-feature communication
- Factory: Content type creation with validation
- Command: Cron jobs as testable, retryable units
"""

from .repositories import Repository, StrikesRepository, PostsRepository, ReplyTargetsRepository
from .scoring import ScoringStrategy, StrikeScoring, ReplyScoring, AlphaScoring, OpportunityScoring, ScoringContext
from .events import EventBus, EventType
from .content import ContentFactory, ContentType, ContentItem
from .crons import CronCommand, TrackerCommand, EvolutionCommand, TrainWeightsCommand, NudgeCommand

__all__ = [
    'Repository', 'StrikesRepository', 'PostsRepository', 'ReplyTargetsRepository',
    'ScoringStrategy', 'StrikeScoring', 'ReplyScoring', 'AlphaScoring', 'OpportunityScoring', 'ScoringContext',
    'EventBus', 'EventType',
    'ContentFactory', 'ContentType', 'ContentItem',
    'CronCommand', 'TrackerCommand', 'EvolutionCommand', 'TrainWeightsCommand', 'NudgeCommand',
]
