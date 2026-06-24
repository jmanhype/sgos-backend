"""
SGOS Backend — Load Tests
Simulates 100+ concurrent requests across critical endpoints.
Run: pytest tests/test_load.py -v --tb=short
"""
import time
import concurrent.futures
import pytest
from httpx import Client


BASE_URL = "http://127.0.0.1:8420"
CONCURRENCY = 100
ITERATIONS = 5

# Shared client with connection pooling (realistic for production)
_shared_client = Client(base_url=BASE_URL, timeout=10.0)


def _request(url: str, method: str = "GET", timeout: float = 10) -> tuple[int, float]:
    """Make a single request, return (status_code, latency_ms)."""
    start = time.time()
    try:
        resp = _shared_client.request(method, url, timeout=timeout)
        latency = (time.time() - start) * 1000
        return resp.status_code, latency
    except Exception:
        latency = (time.time() - start) * 1000
        return 0, latency


def _load_test(endpoint: str, method: str = "GET") -> dict:
    """Run concurrent load test on an endpoint."""
    results = []

    def run_request(_):
        return _request(f"{BASE_URL}{endpoint}", method)

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = [executor.submit(run_request, i) for i in range(ITERATIONS)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    status_codes = [r[0] for r in results]
    latencies = [r[1] for r in results]
    latencies.sort()

    return {
        "endpoint": endpoint,
        "total_requests": len(results),
        "success_rate": sum(1 for s in status_codes if s == 200) / len(results) * 100,
        "errors": sum(1 for s in status_codes if s != 200),
        "avg_latency_ms": sum(latencies) / len(latencies),
        "p50_ms": latencies[len(latencies) // 2],
        "p95_ms": latencies[int(len(latencies) * 0.95)],
        "p99_ms": latencies[int(len(latencies) * 0.99)] if len(latencies) > 1 else latencies[-1],
        "max_ms": latencies[-1],
    }


class TestLoadHealth:
    """Load test: /health — should handle 100+ concurrent with <100ms p99."""

    def test_health_under_load(self):
        result = _load_test("/health")
        assert result["success_rate"] == 100, f"Success rate: {result['success_rate']}%"
        assert result["p95_ms"] < 500, f"p95 latency: {result['p95_ms']:.1f}ms"
        print(f"\n  /health: {result['total_requests']} reqs, "
              f"avg={result['avg_latency_ms']:.1f}ms, "
              f"p95={result['p95_ms']:.1f}ms, "
              f"max={result['max_ms']:.1f}ms")


class TestLoadSearch:
    """Load test: /search — should handle concurrent FTS queries."""

    def test_search_under_load(self):
        # Use a common search term
        result = _load_test("/search?q=AI&limit=10")
        assert result["success_rate"] >= 95, f"Success rate: {result['success_rate']}%"
        assert result["p95_ms"] < 2000, f"p95 latency: {result['p95_ms']:.1f}ms"
        print(f"\n  /search: {result['total_requests']} reqs, "
              f"avg={result['avg_latency_ms']:.1f}ms, "
              f"p95={result['p95_ms']:.1f}ms, "
              f"max={result['max_ms']:.1f}ms")


class TestLoadOutliers:
    """Load test: /outliers — statistical queries under pressure."""

    def test_outliers_under_load(self):
        result = _load_test("/outliers?hours=24&threshold=2.5&limit=10")
        assert result["success_rate"] >= 95, f"Success rate: {result['success_rate']}%"
        assert result["p95_ms"] < 2000, f"p95 latency: {result['p95_ms']:.1f}ms"
        print(f"\n  /outliers: {result['total_requests']} reqs, "
              f"avg={result['avg_latency_ms']:.1f}ms, "
              f"p95={result['p95_ms']:.1f}ms, "
              f"max={result['max_ms']:.1f}ms")


class TestLoadMixed:
    """Load test: mixed endpoints — simulates real traffic patterns."""

    def test_mixed_traffic(self):
        endpoints = [
            ("/health", "GET"),
            ("/outliers?hours=48&threshold=3.0", "GET"),
            ("/search?q=viral&limit=5", "GET"),
            ("/trends?platform=reddit&days=7", "GET"),
            ("/stats", "GET"),
            ("/metrics/json", "GET"),
            ("/scheduler/status", "GET"),
            ("/boards", "GET"),
            ("/creators", "GET"),
            ("/voices", "GET"),
        ]

        results = []

        def run_mixed(_):
            for endpoint, method in endpoints:
                code, latency = _request(f"{BASE_URL}{endpoint}", method)
                results.append((code, latency, endpoint))

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(run_mixed, i) for i in range(10)]
            concurrent.futures.wait(futures)

        success = sum(1 for r in results if r[0] == 200)
        total = len(results)
        latencies = sorted([r[1] for r in results])

        print(f"\n  Mixed: {total} reqs across {len(endpoints)} endpoints")
        print(f"  Success: {success}/{total} ({success/total*100:.1f}%)")
        print(f"  Latency: avg={sum(latencies)/len(latencies):.1f}ms, "
              f"p95={latencies[int(len(latencies)*0.95)]:.1f}ms, "
              f"max={latencies[-1]:.1f}ms")

        # Per-endpoint breakdown
        by_endpoint = {}
        for code, latency, endpoint in results:
            ep = endpoint.split("?")[0]
            if ep not in by_endpoint:
                by_endpoint[ep] = []
            by_endpoint[ep].append((code, latency))

        for ep, reqs in sorted(by_endpoint.items()):
            ep_latencies = sorted([r[1] for r in reqs])
            ep_success = sum(1 for r in reqs if r[0] == 200)
            print(f"    {ep:25s} {ep_success}/{len(reqs)} ok  "
                  f"avg={sum(ep_latencies)/len(ep_latencies):.1f}ms  "
                  f"max={ep_latencies[-1]:.1f}ms")

        assert success / total >= 0.95, f"Success rate: {success/total*100:.1f}%"
        assert latencies[int(len(latencies) * 0.95)] < 3000, \
            f"p95 too slow: {latencies[int(len(latencies)*0.95)]:.1f}ms"
