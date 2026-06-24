"""
SGOS Ingestion Scheduler — Cron-based auto-ingest with progress tracking.
Runs periodic ingestion jobs without external cron or Celery.
"""
import threading
import time
from datetime import datetime, timezone

from services.ingestion import ingestion_service, ingestion_progress


class IngestionScheduler:
    """Background scheduler for periodic data ingestion."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._running = False
        self._schedule: list[dict] = []
        self._last_run: dict[str, float] = {}

    def add_job(self, job_type: str, interval_seconds: int, enabled: bool = True):
        """Register a recurring ingestion job."""
        self._schedule.append({
            "type": job_type,
            "interval": interval_seconds,
            "enabled": enabled,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def start(self):
        """Start the scheduler in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ingestion-scheduler")
        self._thread.start()

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        """Main scheduler loop — checks every 60s for due jobs."""
        while self._running:
            now = time.time()
            for job in self._schedule:
                if not job["enabled"]:
                    continue
                job_type = job["type"]
                last = self._last_run.get(job_type, 0)
                if now - last >= job["interval"]:
                    self._run_job(job_type)
                    self._last_run[job_type] = now
            time.sleep(60)

    def _run_job(self, job_type: str):
        """Execute an ingestion job with error handling."""
        try:
            job_id = ingestion_service.run_ingest_async(job_type)
        except Exception as e:
            print(f"[Scheduler] Job {job_type} failed: {e}")

    def status(self) -> dict:
        """Get scheduler status."""
        now = time.time()
        jobs = []
        for job in self._schedule:
            job_type = job["type"]
            last = self._last_run.get(job_type, 0)
            next_run = last + job["interval"] if last else 0
            jobs.append({
                "type": job_type,
                "interval_seconds": job["interval"],
                "enabled": job["enabled"],
                "last_run": datetime.fromtimestamp(last, tz=timezone.utc).isoformat() if last else None,
                "next_run": datetime.fromtimestamp(next_run, tz=timezone.utc).isoformat() if next_run else None,
                "seconds_until_next": max(0, int(next_run - now)) if next_run else None,
            })
        return {
            "running": self._running,
            "jobs": jobs,
        }


# Global scheduler instance
scheduler = IngestionScheduler()


def init_scheduler():
    """Initialize and start the ingestion scheduler with default jobs."""
    # Reddit + HN full ingestion every 4 hours
    scheduler.add_job("full", interval_seconds=4 * 3600, enabled=True)
    scheduler.start()
