"""Ingestion service — background job management with progress tracking."""
import threading
import time
from datetime import datetime, timezone


class IngestionProgress:
    """Thread-safe progress tracker for ingestion jobs."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, job_id: str, job_type: str) -> str:
        with self._lock:
            # Prune old completed jobs to prevent unbounded memory growth
            if len(self._jobs) > 100:
                oldest = sorted(
                    (k for k in self._jobs if self._jobs[k]["status"] in ("completed", "failed")),
                    key=lambda k: self._jobs[k]["started_at"],
                )
                for k in oldest[:50]:
                    del self._jobs[k]
            self._jobs[job_id] = {
                "id": job_id,
                "type": job_type,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None,
                "progress": 0,
                "total": 0,
                "message": "Starting...",
                "error": None,
            }
        return job_id

    def update(self, job_id: str, progress: int = None, total: int = None, message: str = None):
        with self._lock:
            if job_id in self._jobs:
                if progress is not None:
                    self._jobs[job_id]["progress"] = progress
                if total is not None:
                    self._jobs[job_id]["total"] = total
                if message is not None:
                    self._jobs[job_id]["message"] = message

    def complete(self, job_id: str, message: str = "Done"):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["status"] = "completed"
                self._jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
                self._jobs[job_id]["message"] = message

    def fail(self, job_id: str, error: str):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["status"] = "failed"
                self._jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
                self._jobs[job_id]["error"] = error

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, limit: int = 10) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["started_at"], reverse=True)
            return jobs[:limit]


# Global progress tracker
ingestion_progress = IngestionProgress()


class IngestionService:
    @staticmethod
    def run_ingest_async(job_type: str = "full") -> str:
        """Start an ingestion job in a background thread with progress tracking."""
        job_id = f"ingest_{int(time.time())}_{threading.get_ident()}"
        ingestion_progress.start(job_id, job_type)

        def run():
            try:
                from reddit_ingest import ingest_all
                ingestion_progress.update(job_id, message="Fetching from subreddits...")
                result = ingest_all()
                total = result.get("total_posts", 0) if isinstance(result, dict) else 0
                ingestion_progress.complete(job_id, f"Ingested {total} posts")
            except Exception as e:
                ingestion_progress.fail(job_id, str(e))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return job_id

    @staticmethod
    def get_status(job_id: str) -> dict | None:
        return ingestion_progress.get(job_id)

    @staticmethod
    def list_jobs(limit: int = 10) -> list[dict]:
        return ingestion_progress.list_recent(limit)


ingestion_service = IngestionService()
