"""
SGOS Backend — Comprehensive Test Suite
Covers: unit tests, integration tests, edge cases, error handling, performance.
"""
import json
import time
import threading
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    """Create test app instance."""
    from main import app
    return app


@pytest.fixture(scope="session")
def client(app):
    """Create test client."""
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def sample_post():
    """Sample post for testing."""
    return {
        "id": "reddit_test123",
        "platform": "reddit",
        "platform_id": "test123",
        "title": "Test Viral Post About AI Agents",
        "content": "This is a test post about AI agents that went viral.",
        "url": "https://reddit.com/r/artificial/test123",
        "author": "testuser",
        "subreddit": "artificial",
        "score": 1500,
        "comment_count": 250,
        "z_score": 3.5,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture
def sample_messages():
    """Sample chat messages for testing."""
    return [
        {"role": "user", "content": "Generate viral hooks about AI"},
        {"role": "assistant", "content": "Here are 5 viral hooks..."},
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS — Config, Models, Services
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfig:
    """Test configuration validation."""

    def test_config_loads(self):
        from config import settings
        assert settings.version == "0.1.0"
        assert settings.port == 8420

    def test_config_defaults(self):
        from config import settings
        assert settings.max_upload_mb == 100
        assert settings.db_busy_timeout == 5000
        assert "localhost" in settings.blocked_hosts

    def test_max_upload_bytes_property(self):
        from config import settings
        assert settings.max_upload_bytes == 100 * 1024 * 1024


class TestModels:
    """Test Pydantic models."""

    def test_post_model(self):
        from models.domain import Post
        post = Post(
            id="test123",
            platform="reddit",
            platform_id="abc",
            title="Test",
            url="https://example.com",
            score=100,
            z_score=2.5,
        )
        assert post.id == "test123"
        assert post.platform == "reddit"

    def test_search_request_validation(self):
        from models.requests import SearchRequest
        req = SearchRequest(q="AI agents", limit=10)
        assert req.q == "AI agents"
        assert req.limit == 10

    def test_search_request_min_length(self):
        from models.requests import SearchRequest
        with pytest.raises(Exception):
            SearchRequest(q="")  # min_length=1

    def test_health_response(self):
        from models.responses import HealthResponse
        resp = HealthResponse(status="ok", version="0.1.0", total_posts=500)
        assert resp.status == "ok"


class TestDatabase:
    """Test database operations."""

    def test_get_connection(self):
        from database import get_connection
        conn = get_connection()
        assert conn is not None
        # Test query
        cursor = conn.execute("SELECT 1")
        assert cursor.fetchone()[0] == 1

    def test_connection_pool_thread_safety(self):
        from database import get_connection
        results = []

        def worker():
            conn = get_connection()
            cursor = conn.execute("SELECT 1")
            results.append(cursor.fetchone()[0])

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r == 1 for r in results)
        assert len(results) == 5


class TestObservability:
    """Test metrics and logging."""

    def test_metrics_record(self):
        from observability import Metrics
        m = Metrics()
        m.record_request("/test", "GET", 200, 50.0)
        m.record_request("/test", "GET", 500, 100.0)
        snap = m.snapshot()
        assert snap["total_requests"] == 2
        assert snap["total_errors"] == 1
        assert snap["error_rate"] == 50.0

    def test_metrics_latency_percentiles(self):
        from observability import Metrics
        m = Metrics()
        for i in range(100):
            m.record_request("/test", "GET", 200, float(i))
        snap = m.snapshot()
        assert snap["latency_ms"]["p50"] < snap["latency_ms"]["p95"]
        assert snap["latency_ms"]["p95"] < snap["latency_ms"]["p99"]

    def test_metrics_prometheus_format(self):
        from observability import Metrics
        m = Metrics()
        m.record_request("/test", "GET", 200, 50.0)
        text = m.prometheus_text()
        assert "sgos_requests_total" in text
        assert "sgos_errors_total" in text
        assert "sgos_latency_ms" in text

    def test_structlog_configured(self):
        from observability import log
        # Should not raise
        log.info("test.message", key="value")


class TestScheduler:
    """Test ingestion scheduler."""

    def test_scheduler_add_job(self):
        from scheduler import IngestionScheduler
        s = IngestionScheduler()
        s.add_job("test", interval_seconds=3600)
        status = s.status()
        assert len(status["jobs"]) == 1
        assert status["jobs"][0]["type"] == "test"

    def test_scheduler_status(self):
        from scheduler import IngestionScheduler
        s = IngestionScheduler()
        s.add_job("full", interval_seconds=14400)
        status = s.status()
        assert status["running"] is False
        assert len(status["jobs"]) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — API Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    """Test /health endpoint."""

    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "total_posts" in data

    def test_health_has_version(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "version" in data


class TestResearchEndpoints:
    """Test research endpoints."""

    def test_outliers(self, client):
        resp = client.get("/outliers?platform=reddit&hours=24&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
        assert "outliers" in data

    def test_outliers_validation(self, client):
        # hours must be >= 1
        resp = client.get("/outliers?hours=0")
        assert resp.status_code == 422

    def test_trends(self, client):
        resp = client.get("/trends?platform=reddit&days=7&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "topics" in data

    def test_stats(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 200

    def test_brief(self, client):
        resp = client.get("/brief")
        assert resp.status_code == 200
        data = resp.json()
        assert "brief" in data
        assert "outliers" in data


class TestSearchEndpoints:
    """Test search endpoints."""

    def test_search(self, client):
        resp = client.get("/search?q=AI&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data

    def test_search_empty_query(self, client):
        resp = client.get("/search?q=")
        assert resp.status_code == 422

    def test_hybrid_search(self, client):
        resp = client.get("/search/hybrid?q=test&limit=5")
        assert resp.status_code == 200


class TestIngestionEndpoints:
    """Test ingestion endpoints."""

    def test_ingest_jobs(self, client):
        resp = client.get("/ingest/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data

    def test_ingest_status_not_found(self, client):
        resp = client.get("/ingest/status/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_ingest_posts(self, client, sample_post):
        resp = client.post("/ingest/posts", json=[sample_post])
        assert resp.status_code == 200
        data = resp.json()
        assert "added" in data or "updated" in data


class TestVoiceEndpoints:
    """Test voice profile endpoints."""

    def test_list_voices(self, client):
        resp = client.get("/voices")
        assert resp.status_code == 200
        data = resp.json()
        # Returns list directly or dict with "voices" key
        voices = data if isinstance(data, list) else data.get("voices", [])
        assert isinstance(voices, list)


class TestBoardEndpoints:
    """Test board endpoints."""

    def test_list_boards(self, client):
        resp = client.get("/boards")
        assert resp.status_code == 200
        data = resp.json()
        assert "boards" in data


class TestCreatorEndpoints:
    """Test creator endpoints."""

    def test_list_creators(self, client):
        resp = client.get("/creators")
        assert resp.status_code == 200

    def test_creator_stats(self, client):
        resp = client.get("/creators/stats")
        assert resp.status_code == 200


class TestMediaEndpoints:
    """Test media endpoints."""

    def test_transcribe_status(self, client):
        resp = client.get("/transcribe/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data


class TestMetricsEndpoints:
    """Test observability endpoints."""

    def test_metrics_prometheus(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "sgos_requests_total" in resp.text

    def test_metrics_json(self, client):
        resp = client.get("/metrics/json")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_requests" in data
        assert "latency_ms" in data

    def test_scheduler_status(self, client):
        resp = client.get("/scheduler/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "jobs" in data


class TestChatStreamEndpoint:
    """Test SSE streaming endpoint."""

    def test_stream_endpoint_exists(self, client):
        # Just verify the endpoint responds (don't wait for full stream)
        resp = client.post(
            "/chat/stream",
            json={"messages": [{"role": "user", "content": "hi"}]},
            timeout=5,
        )
        # May timeout or return partial — just checking it exists
        assert resp.status_code in (200, 408, 504)


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE CASE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_search_special_characters(self, client):
        resp = client.get("/search?q=test%26special%3Cchars%3E&limit=5")
        assert resp.status_code == 200

    def test_search_very_long_query(self, client):
        long_query = "test " * 100  # 500 chars
        resp = client.get(f"/search?q={long_query}&limit=5")
        assert resp.status_code == 200

    def test_outliers_extreme_hours(self, client):
        resp = client.get("/outliers?hours=720&limit=5")  # 30 days
        assert resp.status_code == 200

    def test_ingest_empty_posts(self, client):
        resp = client.post("/ingest/posts", json=[])
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == 0

    def test_ingest_posts_missing_fields(self, client):
        resp = client.post("/ingest/posts", json=[{"title": "no platform"}])
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# ERROR HANDLING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorHandling:
    """Test error responses."""

    def test_invalid_json_body(self, client):
        resp = client.post("/ingest/posts", content="not json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 422

    def test_missing_required_field(self, client):
        resp = client.get("/search")  # Missing q parameter
        assert resp.status_code == 422

    def test_invalid_query_params(self, client):
        resp = client.get("/outliers?hours=-1")
        assert resp.status_code == 422

    def test_nonexistent_endpoint(self, client):
        resp = client.get("/nonexistent")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# PERFORMANCE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerformance:
    """Test performance characteristics."""

    def test_health_response_time(self, client):
        start = time.time()
        resp = client.get("/health")
        duration = (time.time() - start) * 1000
        assert resp.status_code == 200
        assert duration < 500  # Should be fast

    def test_search_response_time(self, client):
        start = time.time()
        resp = client.get("/search?q=AI&limit=10")
        duration = (time.time() - start) * 1000
        assert resp.status_code == 200
        assert duration < 1000  # Under 1 second

    def test_concurrent_requests(self, client):
        """Test handling of concurrent requests."""
        results = []

        def make_request():
            resp = client.get("/health")
            results.append(resp.status_code)

        threads = [threading.Thread(target=make_request) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(code == 200 for code in results)
        assert len(results) == 10


# ═══════════════════════════════════════════════════════════════════════════════
# SECURITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurity:
    """Test security features."""

    def test_csrf_blocks_bad_origin(self, client):
        resp = client.post(
            "/ingest",
            headers={"Origin": "https://evil.com"},
        )
        assert resp.status_code == 403

    def test_csrf_allows_good_origin(self, client):
        resp = client.post(
            "/ingest",
            headers={"Origin": "http://localhost:3000"},
        )
        # Should not be 403 (may be other status if ingestion fails)
        assert resp.status_code != 403

    def test_health_exempts_auth(self, client):
        """Health endpoint should work without auth."""
        resp = client.get("/health")
        assert resp.status_code == 200
