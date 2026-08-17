"""
Factory Pattern — Content type creation with validation.

Trade-offs:
  (+) One place to validate/enrich ALL content before DB insert
  (+) Consistent metadata (timestamps, IDs, source tracking)
  (+) New content types = new enum value + validation method
  (-) Can become a god-object if not disciplined
  (-) Validation logic here means routers are thinner (good) but
      less visible (bad for debugging)

Design choice: ContentFactory.create() validates, enriches, and inserts.
Each content type has its own validation rules. Factory emits events via
Observer after successful creation.
"""

from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .repositories import Repository
from .events import EventBus, EventType, get_event_bus


class ContentType(Enum):
    """All content types in SGOS."""
    STRIKE = "strike"
    REPLY = "reply"
    ALPHA = "alpha"
    OPPORTUNITY = "opportunity"
    POST = "post"
    BOARD_ITEM = "board_item"
    PROJECT = "project"
    STYLE_RULE = "style_rule"


@dataclass
class ContentItem:
    """Validated content ready for DB insertion."""
    content_type: ContentType
    data: dict
    created_at: str
    source: str = ""  # Which system created this (engine, scout, manual, etc.)


class ContentFactory:
    """
    Creates, validates, and persists content items.

    Usage:
        factory = ContentFactory(repo, bus)
        item = factory.create(ContentType.STRIKE, {
            "author": "@someone",
            "draft": "Your reply here...",
            "strike_score": 12.5,
            "grounding_pct": 85,
        })
        # item is validated, enriched, and inserted
    """

    def __init__(self, repo: Repository, bus: Optional[EventBus] = None):
        self.repo = repo
        self.bus = bus or get_event_bus()

    def create(self, content_type: ContentType, data: dict, source: str = "") -> ContentItem:
        """Validate, enrich, and insert a content item."""
        # Validate
        errors = self._validate(content_type, data)
        if errors:
            raise ValueError(f"Validation failed for {content_type.value}: {errors}")

        # Enrich with metadata
        now = datetime.now(timezone.utc).isoformat()
        data["created_at"] = now
        data["source"] = source

        item = ContentItem(
            content_type=content_type,
            data=data,
            created_at=now,
            source=source,
        )

        # Insert
        self._insert(item)

        # Emit event
        self._emit_created(item)

        return item

    def _validate(self, content_type: ContentType, data: dict) -> list[str]:
        """Validate content by type. Returns list of error strings (empty = valid)."""
        validators = {
            ContentType.STRIKE: self._validate_strike,
            ContentType.REPLY: self._validate_reply,
            ContentType.ALPHA: self._validate_alpha,
            ContentType.OPPORTUNITY: self._validate_opportunity,
            ContentType.POST: self._validate_post,
            ContentType.BOARD_ITEM: self._validate_board_item,
            ContentType.PROJECT: self._validate_project,
            ContentType.STYLE_RULE: self._validate_style_rule,
        }
        validator = validators.get(content_type)
        if validator is None:
            return [f"Unknown content type: {content_type}"]
        return validator(data)

    def _validate_strike(self, data: dict) -> list[str]:
        errors = []
        if not data.get("author"):
            errors.append("author (handle) required")
        if not data.get("draft"):
            errors.append("draft text required")
        if data.get("draft") and len(data["draft"]) < 80:
            errors.append(f"draft too short ({len(data['draft'])} < 80 chars)")
        if data.get("grounding_pct", 0) < 60:
            errors.append(f"grounding too low ({data.get('grounding_pct', 0)}% < 60%)")
        return errors

    def _validate_reply(self, data: dict) -> list[str]:
        errors = []
        if not data.get("target_handle"):
            errors.append("target_handle required")
        if not data.get("draft"):
            errors.append("draft text required")
        return errors

    def _validate_alpha(self, data: dict) -> list[str]:
        errors = []
        if not data.get("title"):
            errors.append("title required")
        if not data.get("content"):
            errors.append("content required")
        return errors

    def _validate_opportunity(self, data: dict) -> list[str]:
        errors = []
        if not data.get("source_post_id") and not data.get("source_url"):
            errors.append("source_post_id or source_url required")
        return errors

    def _validate_post(self, data: dict) -> list[str]:
        errors = []
        if not data.get("content") and not data.get("url"):
            errors.append("content or url required")
        return errors

    def _validate_board_item(self, data: dict) -> list[str]:
        errors = []
        if not data.get("board_id"):
            errors.append("board_id required")
        if not data.get("post_id") and not data.get("content"):
            errors.append("post_id or content required")
        return errors

    def _validate_project(self, data: dict) -> list[str]:
        errors = []
        if not data.get("name"):
            errors.append("name required")
        return errors

    def _validate_style_rule(self, data: dict) -> list[str]:
        errors = []
        if not data.get("category"):
            errors.append("category required")
        if not data.get("rule"):
            errors.append("rule text required")
        return errors

    def _insert(self, item: ContentItem):
        """Insert validated content into the appropriate table."""
        inserters = {
            ContentType.STRIKE: self._insert_strike,
            ContentType.REPLY: self._insert_reply,
            ContentType.ALPHA: self._insert_alpha,
            ContentType.OPPORTUNITY: self._insert_opportunity,
            ContentType.POST: self._insert_post,
            ContentType.BOARD_ITEM: self._insert_board_item,
            ContentType.PROJECT: self._insert_project,
            ContentType.STYLE_RULE: self._insert_style_rule,
        }
        inserter = inserters.get(item.content_type)
        if inserter:
            inserter(item.data)

    def _insert_strike(self, data: dict):
        self.repo.execute_insert("""
            INSERT INTO strikes (author, draft, strike_score, grounding_pct, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
        """, (data["author"], data["draft"], data.get("strike_score", 0),
              data.get("grounding_pct", 0), data["created_at"]))

    def _insert_reply(self, data: dict):
        self.repo.execute_insert("""
            INSERT INTO scout_history (target_handle, draft, score, grounding_pct, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (data["target_handle"], data["draft"], data.get("score", 0),
              data.get("grounding_pct", 0), data["created_at"]))

    def _insert_alpha(self, data: dict):
        self.repo.execute_insert("""
            INSERT INTO daily_alpha (title, content, score, source_url, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (data["title"], data["content"], data.get("score", 0),
              data.get("source_url", ""), data["created_at"]))

    def _insert_opportunity(self, data: dict):
        self.repo.execute_insert("""
            INSERT INTO pipeline_opportunities (source_post_id, source_url, format, score, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (data.get("source_post_id"), data.get("source_url", ""),
              data.get("format", "thread"), data.get("score", 0), data["created_at"]))

    def _insert_post(self, data: dict):
        self.repo.execute_insert("""
            INSERT INTO posts (content, url, topic, creator_handle, ingested_at)
            VALUES (?, ?, ?, ?, ?)
        """, (data.get("content", ""), data.get("url", ""),
              data.get("topic", ""), data.get("creator_handle", ""), data["created_at"]))

    def _insert_board_item(self, data: dict):
        self.repo.execute_insert("""
            INSERT INTO board_posts (board_id, post_id, content, added_at)
            VALUES (?, ?, ?, ?)
        """, (data["board_id"], data.get("post_id"), data.get("content", ""), data["created_at"]))

    def _insert_project(self, data: dict):
        self.repo.execute_insert("""
            INSERT INTO projects (name, description, status, repo_url, category, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (data["name"], data.get("description", ""), data.get("status", "active"),
              data.get("repo_url", ""), data.get("category", ""), data["created_at"], data["created_at"]))

    def _insert_style_rule(self, data: dict):
        self.repo.execute_insert("""
            INSERT INTO style_guide (category, rule, example, priority, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (data["category"], data["rule"], data.get("example", ""),
              data.get("priority", 5), data.get("source", data.get("source_name", "")),
              data["created_at"], data["created_at"]))

    def _emit_created(self, item: ContentItem):
        """Emit an event after successful creation."""
        event_map = {
            ContentType.STRIKE: EventType.STRIKE_GENERATED,
            ContentType.REPLY: EventType.REPLY_GENERATED,
            ContentType.ALPHA: EventType.ALPHA_GENERATED,
            ContentType.OPPORTUNITY: EventType.OPPORTUNITY_CREATED,
            ContentType.POST: EventType.POST_INGESTED,
        }
        event_type = event_map.get(item.content_type)
        if event_type:
            self.bus.emit(event_type, f"factory.{item.content_type.value}", {
                "content_type": item.content_type.value,
                "source": item.source,
            })
