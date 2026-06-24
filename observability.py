"""
SGOS Observability — Structured logging, request tracing, Prometheus-compatible metrics.
"""
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone

import structlog

# ─── Structured Logger ──────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

log = structlog.get_logger()


# ─── Request Metrics ────────────────────────────────────────────────────────
class Metrics:
    """Thread-safe request metrics collector."""

    def __init__(self):
        self._lock = threading.Lock()
        self._request_count = 0
        self._request_errors = 0
        self._request_latencies: list[float] = []
        self._endpoint_counts: dict[str, int] = defaultdict(int)
        self._endpoint_errors: dict[str, int] = defaultdict(int)
        self._start_time = time.time()

    def record_request(self, path: str, method: str, status_code: int, duration_ms: float):
        with self._lock:
            self._request_count += 1
            self._request_latencies.append(duration_ms)
            key = f"{method} {path}"
            self._endpoint_counts[key] += 1
            if status_code >= 400:
                self._request_errors += 1
                self._endpoint_errors[key] += 1
            # Keep last 1000 latencies
            if len(self._request_latencies) > 1000:
                self._request_latencies = self._request_latencies[-1000:]

    def snapshot(self) -> dict:
        with self._lock:
            latencies = sorted(self._request_latencies) if self._request_latencies else [0]
            n = len(latencies)
            return {
                "uptime_seconds": int(time.time() - self._start_time),
                "total_requests": self._request_count,
                "total_errors": self._request_errors,
                "error_rate": round(self._request_errors / max(self._request_count, 1) * 100, 2),
                "latency_ms": {
                    "p50": round(latencies[int(n * 0.5)], 1),
                    "p95": round(latencies[int(n * 0.95)], 1),
                    "p99": round(latencies[int(n * 0.99)], 1),
                    "avg": round(sum(latencies) / n, 1),
                    "max": round(max(latencies), 1),
                },
                "top_endpoints": dict(
                    sorted(self._endpoint_counts.items(), key=lambda x: -x[1])[:10]
                ),
                "error_endpoints": {k: v for k, v in self._endpoint_errors.items() if v > 0},
            }

    def prometheus_text(self) -> str:
        """Prometheus text exposition format."""
        snap = self.snapshot()
        lines = [
            "# HELP sgos_requests_total Total HTTP requests",
            "# TYPE sgos_requests_total counter",
            f"sgos_requests_total {snap['total_requests']}",
            "",
            "# HELP sgos_errors_total Total HTTP errors (4xx/5xx)",
            "# TYPE sgos_errors_total counter",
            f"sgos_errors_total {snap['total_errors']}",
            "",
            "# HELP sgos_latency_ms Request latency in milliseconds",
            "# TYPE sgos_latency_ms summary",
            f"sgos_latency_ms{{quantile=\"0.5\"}} {snap['latency_ms']['p50']}",
            f"sgos_latency_ms{{quantile=\"0.95\"}} {snap['latency_ms']['p95']}",
            f"sgos_latency_ms{{quantile=\"0.99\"}} {snap['latency_ms']['p99']}",
            "",
            "# HELP sgos_uptime_seconds Server uptime",
            "# TYPE sgos_uptime_seconds gauge",
            f"sgos_uptime_seconds {snap['uptime_seconds']}",
        ]
        return "\n".join(lines) + "\n"


metrics = Metrics()
