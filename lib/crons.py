"""
Command Pattern — Cron jobs as testable, retryable units.

Trade-offs:
  (+) Each cron job is a self-contained class with execute() + on_error()
  (+) Retry logic, timeout handling, and logging in ONE place
  (+) Easy to test: instantiate command, call execute(), assert result
  (+) on_success/on_error hooks for Observer integration
  (-) More boilerplate than a plain script — but scripts were broken
      because they had NO structure (wrong paths, no error handling)

Design choice: Each command wraps a cron script's logic in a class.
The execute() method handles DB connections, locking, timeouts, and
event emission. Subclasses implement _run() with the actual logic.

This FIXES the 4 broken crons by giving them proper structure:
- TrainWeightsCommand: correct DB path, proper error handling
- EvolutionCommand: runs bash script via subprocess, not python
- NudgeCommand: same — bash script via subprocess
- TrackerCommand: fixes the "0 updated" issue by using needs_tracking()
"""

import signal
import subprocess
import sys
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .repositories import Repository, StrikesRepository, PerformanceRepository
from .events import EventBus, EventType, get_event_bus
from database import get_connection


@dataclass
class CommandResult:
    """Result of a command execution."""
    success: bool
    message: str
    data: dict = None
    duration_seconds: float = 0.0
    error: Optional[str] = None


class CronCommand(ABC):
    """
    Base command for all cron jobs.

    Provides:
    - Lock file management (prevent concurrent runs)
    - Timeout enforcement
    - Event emission on success/error
    - Structured result logging
    """

    LOCK_DIR = Path.home() / ".hermes" / "scripts"
    SCRIPTS_DIR = Path.home() / ".hermes" / "scripts"
    DEFAULT_TIMEOUT = 300  # 5 minutes

    def __init__(self, bus: Optional[EventBus] = None):
        self.bus = bus or get_event_bus()
        self._lock_path = self.LOCK_DIR / f".{self.name}.lock"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique command name (used for lock file)."""
        pass

    @abstractmethod
    def _run(self) -> CommandResult:
        """Implement the actual command logic here."""
        pass

    def execute(self, timeout: Optional[int] = None) -> CommandResult:
        """Execute with locking, timeout, and event emission."""
        timeout = timeout or self.DEFAULT_TIMEOUT
        start = datetime.now(timezone.utc)

        # Acquire lock (atomic via O_EXCL — no TOCTOU race)
        if not self._acquire_lock():
            return CommandResult(
                success=False,
                message=f"{self.name}: Another instance running",
                error="lock_conflict"
            )

        try:
            # Set timeout alarm (Unix only)
            def _timeout_handler(signum, frame):
                raise TimeoutError(f"Command {self.name} timed out after {timeout}s")

            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(timeout)

            try:
                result = self._run()
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

            result.duration_seconds = (datetime.now(timezone.utc) - start).total_seconds()

            # Emit success event
            self.bus.emit(EventType.STRIKE_CYCLE_COMPLETE, self.name, {
                "success": result.success,
                "message": result.message,
                "duration": result.duration_seconds,
            })

            return result

        except TimeoutError as e:
            result = CommandResult(
                success=False,
                message=f"{self.name}: Timed out after {timeout}s",
                error="timeout",
                duration_seconds=timeout,
            )
            self.bus.emit(EventType.ERROR, self.name, {"error": "timeout", "detail": str(e)})
            return result

        except Exception as e:
            result = CommandResult(
                success=False,
                message=f"{self.name}: {str(e)}",
                error=str(e),
                duration_seconds=(datetime.now(timezone.utc) - start).total_seconds(),
            )
            self.bus.emit(EventType.ERROR, self.name, {"error": str(e)})
            return result

        finally:
            self._release_lock()

    def _acquire_lock(self) -> bool:
        """Acquire lock atomically via O_EXCL — no TOCTOU race condition."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # O_EXCL | O_CREAT: fails if file exists (atomic on POSIX)
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            # Check if the existing lock is stale (process dead)
            try:
                old_pid = int(self._lock_path.read_text().strip())
                os.kill(old_pid, 0)  # Signal 0 = check if alive
                return False  # Still running
            except (ProcessLookupError, ValueError, OSError):
                # Stale lock — remove and retry once
                self._lock_path.unlink(missing_ok=True)
                try:
                    fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, str(os.getpid()).encode())
                    os.close(fd)
                    return True
                except FileExistsError:
                    return False  # Another process grabbed it between our unlink and create

    def _release_lock(self):
        """Release the lock file."""
        self._lock_path.unlink(missing_ok=True)


class TrackerCommand(CronCommand):
    """
    Strike Performance Tracker — closes the feedback loop.

    Fixes:
    - Uses needs_tracking() from repository (stale/missing performance data)
    - Handles bird CLI timeouts gracefully
    - Emits TRACKING_UPDATED events for weight training
    """

    BIRD = "/opt/homebrew/bin/bird"
    MAX_TRACK_AGE_DAYS = 30

    @property
    def name(self) -> str:
        return "sgos-strike-tracker"

    def _run(self) -> CommandResult:
        strikes_repo = StrikesRepository()
        perf_repo = PerformanceRepository()

        # Get strikes that actually need tracking (stale or missing perf data)
        needs_update = strikes_repo.needs_tracking(self.MAX_TRACK_AGE_DAYS)
        total_posted = len(strikes_repo.with_tweet_ids(self.MAX_TRACK_AGE_DAYS))

        if not needs_update:
            return CommandResult(
                success=True,
                message=f"Tracker: {total_posted} posted strikes, 0 need updating",
                data={"total_posted": total_posted, "updated": 0},
            )

        updated = 0
        errors = 0
        batch_items = []  # Collect all upserts for batch write

        for strike in needs_update:
            tweet_id = strike.get("reply_tweet_id", "").strip()
            if not tweet_id:
                continue

            try:
                metrics = self._fetch_metrics(tweet_id)
                if metrics:
                    metrics["tweet_id"] = tweet_id
                    batch_items.append(metrics)
                    updated += 1
                    self.bus.emit(EventType.TRACKING_UPDATED, self.name, {
                        "tweet_id": tweet_id,
                        "strike_id": strike.get("id"),
                    })
            except Exception as e:
                errors += 1
                if "429" in str(e) or "rate" in str(e).lower():
                    # Rate limited — stop for this cycle
                    break

        # Batch write all performance data in ONE connection
        if batch_items:
            perf_repo.upsert_batch(batch_items)

        return CommandResult(
            success=True,
            message=f"Tracker: {updated}/{len(needs_update)} updated ({errors} errors, {total_posted} total posted)",
            data={"updated": updated, "errors": errors, "total_posted": total_posted, "needed_update": len(needs_update)},
        )

    def _fetch_metrics(self, tweet_id: str) -> Optional[dict]:
        """Fetch tweet metrics via bird CLI."""
        try:
            result = subprocess.run(
                [self.BIRD, "--json", "tweet", tweet_id],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "HOME": str(Path.home())}
            )
            if result.returncode != 0:
                return None

            import json
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                impressions = data.get("view_count", 0) or data.get("impressions", 0) or 0
                likes = data.get("like_count", 0) or data.get("likes", 0) or 0
                replies = data.get("reply_count", 0) or data.get("replies", 0) or 0
                retweets = data.get("retweet_count", 0) or data.get("retweets", 0) or 0
                total_engagement = likes + replies + retweets
                engagement_rate = (total_engagement / impressions * 100) if impressions > 0 else 0.0

                return {
                    "impressions": impressions,
                    "likes": likes,
                    "replies": replies,
                    "retweets": retweets,
                    "engagement_rate": round(engagement_rate, 2),
                }
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
            return None


class EvolutionCommand(CronCommand):
    """
    Genome Evolution Report — weekly snapshot of content genome changes.

    Fix: Runs as bash subprocess, NOT as Python (was a .sh file executed by python3).
    """

    @property
    def name(self) -> str:
        return "sgos-evolution-report"

    def _run(self) -> CommandResult:
        script = self.SCRIPTS_DIR / "sgos_evolution_report.sh"
        if not script.exists():
            # Fallback: run genome_evolution.py directly
            py_script = Path.home() / "sgos-backend" / "genome_evolution.py"
            if py_script.exists():
                result = subprocess.run(
                    [sys.executable, str(py_script)],
                    capture_output=True, text=True, timeout=120,
                    cwd=str(Path.home() / "sgos-backend"),
                )
                success = result.returncode == 0
                return CommandResult(
                    success=success,
                    message=result.stdout.strip() or result.stderr.strip(),
                    error=result.stderr if not success else None,
                )
            return CommandResult(success=False, message="Evolution script not found", error="missing_script")

        # Run as BASH (not python!)
        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True, text=True, timeout=120,
            cwd=str(Path.home() / "sgos-backend"),
        )

        success = result.returncode == 0
        if success:
            self.bus.emit(EventType.EVOLUTION_SNAPSHOT, self.name, {})

        return CommandResult(
            success=success,
            message=result.stdout.strip() or result.stderr.strip(),
            error=result.stderr if not success else None,
        )


class TrainWeightsCommand(CronCommand):
    """
    Retrain scorer weights from performance data.

    Fix: Correct DB path (was Path(__file__).parent which resolves to ~/.hermes/scripts/).
    """

    @property
    def name(self) -> str:
        return "sgos-train-weights"

    def _run(self) -> CommandResult:
        import statistics

        conn = get_connection()

        try:
            strikes = conn.execute('''
                SELECT s.strike_score, s.urgency_score, s.audience_score,
                       s.engagement_velocity, s.topic_match, s.follower_tier,
                       pp.impressions, pp.likes, pp.replies, pp.engagement_rate
                FROM strikes s
                JOIN post_performance pp ON pp.tweet_id = s.reply_tweet_id
                WHERE s.status = 'posted' AND s.reply_tweet_id IS NOT NULL
            ''').fetchall()

            n = len(strikes)
            if n < 3:
                return CommandResult(
                    success=True,
                    message=f"Trainer: Not enough data ({n} strikes, need 3+)",
                    data={"strikes_count": n},
                )

            urgency = [r['urgency_score'] or 0 for r in strikes]
            audience = [r['audience_score'] or 0 for r in strikes]
            velocity = [r['engagement_velocity'] or 0 for r in strikes]
            topic = [r['topic_match'] or 0 for r in strikes]
            eng_rates = [r['engagement_rate'] or 0 for r in strikes]

            def corr(x, y):
                length = len(x)
                if length < 2:
                    return 0
                mx, my = sum(x) / length, sum(y) / length
                cov = sum((a - mx) * (b - my) for a, b in zip(x, y)) / length
                try:
                    sx = statistics.stdev(x) if len(set(x)) > 1 else 0.001
                    sy = statistics.stdev(y) if len(set(y)) > 1 else 0.001
                    return cov / (sx * sy)
                except (ZeroDivisionError, ValueError):
                    return 0

            weights = {
                "audience": round(abs(corr(audience, eng_rates)) * 10, 2),
                "topic": round(abs(corr(topic, eng_rates)) * 10, 2),
                "urgency": round(abs(corr(urgency, eng_rates)) * 10, 2),
                "velocity": round(abs(corr(velocity, eng_rates)) * 10, 2),
            }

            # Upsert weights (scorer_weights uses scorer_name + weight columns)
            for key, value in weights.items():
                existing = conn.execute(
                    "SELECT id FROM scorer_weights WHERE scorer_name = ?", (key,)
                ).fetchone()
                if existing:
                    conn.execute("UPDATE scorer_weights SET weight = ?, trained_at = datetime('now'), sample_size = ? WHERE scorer_name = ?", (value, n, key))
                else:
                    conn.execute("INSERT INTO scorer_weights (scorer_name, weight, sample_size, trained_at) VALUES (?, ?, ?, datetime('now'))", (key, value, n))

            conn.commit()
            self.bus.emit(EventType.WEIGHTS_TRAINED, self.name, {"weights": weights, "n": n})

            return CommandResult(
                success=True,
                message=f"Trainer: Updated weights from {n} strikes: {weights}",
                data={"weights": weights, "strikes_count": n},
            )
        except Exception:
            conn.rollback()
            raise


class NudgeCommand(CronCommand):
    """
    Alpha Nudge — afternoon reminder about unposted daily alphas.

    Fix: Runs as bash subprocess, NOT as Python (was a .sh file executed by python3).
    """

    @property
    def name(self) -> str:
        return "sgos-alpha-nudge"

    def _run(self) -> CommandResult:
        script = self.SCRIPTS_DIR / "sgos-alpha-nudge.sh"
        if not script.exists():
            # Fallback: run daily-alpha with --nudge flag directly
            py_script = self.SCRIPTS_DIR / "sgos-daily-alpha.py"
            if py_script.exists():
                result = subprocess.run(
                    [sys.executable, str(py_script), "--nudge"],
                    capture_output=True, text=True, timeout=60,
                    cwd=str(Path.home() / "sgos-backend"),
                )
                success = result.returncode == 0
                return CommandResult(
                    success=success,
                    message=result.stdout.strip() or result.stderr.strip(),
                    error=result.stderr if not success else None,
                )
            return CommandResult(success=False, message="Nudge script not found", error="missing_script")

        # Run as BASH (not python!)
        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True, text=True, timeout=60,
            cwd=str(Path.home() / "sgos-backend"),
        )

        return CommandResult(
            success=result.returncode == 0,
            message=result.stdout.strip() or result.stderr.strip(),
            error=result.stderr if result.returncode != 0 else None,
        )


# --- Command Registry ---

COMMANDS: dict[str, type[CronCommand]] = {
    "tracker": TrackerCommand,
    "evolution": EvolutionCommand,
    "train-weights": TrainWeightsCommand,
    "nudge": NudgeCommand,
}


def run_command(name: str, **kwargs) -> CommandResult:
    """Run a cron command by name."""
    cmd_class = COMMANDS.get(name)
    if cmd_class is None:
        return CommandResult(
            success=False,
            message=f"Unknown command: {name}. Available: {list(COMMANDS.keys())}",
            error="unknown_command",
        )
    cmd = cmd_class(**kwargs)
    return cmd.execute()


if __name__ == "__main__":
    """CLI entry point: python crons.py tracker|evolution|train-weights|nudge"""
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <command>")
        print(f"Commands: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    cmd_name = sys.argv[1]
    result = run_command(cmd_name)
    print(f"[{result.duration_seconds:.1f}s] {result.message}")
    sys.exit(0 if result.success else 1)
