"""
Observer Pattern — Event bus for cross-feature communication.

Trade-offs:
  (+) Decouples features — strike engine doesn't import tracker or weights
  (+) New listeners = new subscribe() calls, zero changes to emitters
  (+) Event log for debugging/audit trail
  (-) Async-by-nature but our impl is synchronous (good for cron scripts)
  (-) Over-engineering for 5 features — but prevents the spaghetti we had

Design choice: Simple synchronous pub/sub with an in-memory event log.
Each cron script subscribes to relevant events, does its work, and the
event bus logs everything for the morning brief.

Currently these features are DISCONNECTED:
  strike posted → tracker should update → weights should retrain → evolution should snapshot
This observer wires them together so a single event cascade works.
"""

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
import json
from pathlib import Path


class EventType(Enum):
    """All cross-feature events in the system."""
    # Strike lifecycle
    STRIKE_GENERATED = "strike.generated"
    STRIKE_GROUNDING_PASSED = "strike.grounding_passed"
    STRIKE_GROUNDING_FAILED = "strike.grounding_failed"
    STRIKE_POSTED = "strike.posted"
    STRIKE_DISMISSED = "strike.dismissed"
    STRIKE_CYCLE_COMPLETE = "strike.cycle_complete"

    # Tracking / feedback
    TRACKING_UPDATED = "tracking.updated"
    TRACKING_STALE = "tracking.stale"

    # Scoring / weights
    WEIGHTS_TRAINED = "weights.trained"
    WEIGHTS_STALE = "weights.stale"

    # Content pipeline
    OPPORTUNITY_CREATED = "opportunity.created"
    OPPORTUNITY_DISMISSED = "opportunity.dismissed"
    REPLY_GENERATED = "reply.generated"
    REPLY_POSTED = "reply.posted"
    ALPHA_GENERATED = "alpha.generated"

    # Research / ingestion
    POST_INGESTED = "post.ingested"
    OUTLIER_DETECTED = "outlier.detected"
    EVOLUTION_SNAPSHOT = "evolution.snapshot"

    # System
    RATE_LIMITED = "system.rate_limited"
    ERROR = "system.error"


@dataclass
class Event:
    """An event that occurred in the system."""
    type: EventType
    source: str
    data: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "source": self.source,
            "data": self.data,
            "timestamp": self.timestamp,
        }


# Type alias for event handlers
EventHandler = Callable[[Event], None]


class EventBus:
    """
    Synchronous event bus with logging.

    Usage:
        bus = EventBus()
        bus.subscribe(EventType.STRIKE_POSTED, on_strike_posted)
        bus.emit(EventType.STRIKE_POSTED, "strike-engine", {"strike_id": 42})
    """

    LOG_FILE = Path.home() / "sgos-backend" / "event_log.jsonl"
    MAX_LOG_SIZE = 5 * 1024 * 1024  # 5MB — rotate when exceeded

    def __init__(self, log_events: bool = True):
        self._handlers: dict[EventType, list[EventHandler]] = {}
        self._log_events = log_events

    def subscribe(self, event_type: EventType, handler: EventHandler):
        """Register a handler for an event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler):
        """Remove a handler."""
        if event_type in self._handlers:
            self._handlers[event_type] = [h for h in self._handlers[event_type] if h != handler]

    def emit(self, event_type: EventType, source: str, data: dict = None) -> Event:
        """Emit an event — calls all registered handlers synchronously."""
        event = Event(type=event_type, source=source, data=data or {})

        # Log the event
        if self._log_events:
            self._log(event)

        # Call all handlers
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                # Don't let one handler crash the bus
                error_event = Event(
                    type=EventType.ERROR,
                    source="event_bus",
                    data={"original_event": event_type.value, "error": str(e)}
                )
                self._log(error_event)

        return event

    def _log(self, event: Event):
        """Append event to JSONL log file. Rotates when exceeding MAX_LOG_SIZE."""
        try:
            # Rotate if needed: rename current to .bak, keep only last 1 backup
            if self.LOG_FILE.exists():
                size = self.LOG_FILE.stat().st_size
                if size > self.MAX_LOG_SIZE:
                    bak = self.LOG_FILE.with_suffix(".jsonl.bak")
                    bak.unlink(missing_ok=True)
                    self.LOG_FILE.rename(bak)

            with open(self.LOG_FILE, "a") as f:
                f.write(json.dumps(event.to_dict()) + "\n")
        except (OSError, IOError):
            pass  # Don't crash on log failure

    def recent_events(self, limit: int = 50, event_type: Optional[EventType] = None) -> list[dict]:
        """Read recent events from log. Uses tail-based reading to avoid loading entire file."""
        events = []
        target_type = event_type.value if event_type else None
        try:
            # Read last N lines efficiently — seek from end
            with open(self.LOG_FILE, "rb") as f:
                f.seek(0, 2)  # Seek to end
                file_size = f.tell()
                # Read last 100KB (covers ~500+ events typically)
                read_size = min(file_size, 100 * 1024)
                f.seek(file_size - read_size)
                lines = f.read().decode("utf-8", errors="replace").splitlines()

            # Parse from the end, collecting matching events
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                    if target_type is None or evt.get("type") == target_type:
                        events.append(evt)
                        if len(events) >= limit:
                            break
                except json.JSONDecodeError:
                    continue

            events.reverse()  # Restore chronological order
        except (OSError, IOError):
            pass
        return events

    def stats(self) -> dict:
        """Event type counts from log."""
        counts: dict[str, int] = {}
        try:
            with open(self.LOG_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        evt = json.loads(line)
                        t = evt["type"]
                        counts[t] = counts.get(t, 0) + 1
        except (OSError, IOError, json.JSONDecodeError):
            pass
        return counts


# --- Singleton for cross-module access ---
_global_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get or create the global event bus."""
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus()
    return _global_bus
