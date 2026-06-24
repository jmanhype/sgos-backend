# SGOS Backend

> StraughterG-OS Intelligence Backend — Autonomous Viral Content Pipeline, Research Engine, Adaptive Scoring

![Python](https://img.shields.io/badge/Python-3.12-blue?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green?style=flat-square)
![Tests](https://img.shields.io/badge/tests-180%20passed-brightgreen?style=flat-square)

---

## What is this?

A Python/FastAPI backend powering [StraughterG-OS](https://github.com/jmanhype/StraughterG-os) — a creator intelligence platform that autonomously detects viral outliers, extracts their structural DNA, generates content variants, scores them, and learns from real-world performance to improve over time.

### Features

- **Autonomous Viral Content Pipeline** — Detect → Extract → Generate → Score → Alert (runs every 4 hours)
- **Adaptive Scoring** — Closed feedback loop: publish → measure → train → improve weights
- **Platform Formatters** — One-click export to X threads, LinkedIn, Bluesky, Newsletters
- **Viral Outlier Detection** — Z-score analysis across Reddit, Hacker News, Twitter
- **Multi-Format Content Generation** — Threads, posts, newsletters, scripts, carousels
- **Voice Profiles** — TF-IDF cosine similarity matching for consistent writing style
- **Smart Alerts** — Telegram notifications when high-scoring opportunities are generated
- **Bulk Actions** — Dismiss all, regenerate batch, copy top N formatted
- **SSE Streaming** — Real-time LLM token delivery for chat
- **FTS5 + TF-IDF Search** — Hybrid keyword + semantic search with Reciprocal Rank Fusion
- **Creator Tracking** — Follow high-performing authors, discover new ones
- **Automated Ingestion** — Background scheduler pulls fresh data every 4 hours

### Architecture

```
main.py (slim app factory, ~110 LOC)
├── routers/ (13 domain routers — HTTP layer)
├── services/ (10 business logic modules)
│   └── pipeline/ (autonomous viral content pipeline)
│       ├── protocols.py    — Interface contracts (SOLID)
│       ├── genome.py       — Viral DNA extraction
│       ├── generator.py    — Content variant generation
│       ├── scoring.py      — Pluggable scoring strategies
│       ├── voice_match.py  — TF-IDF voice similarity
│       ├── repository.py   — SQLite storage
│       ├── orchestrator.py — Pipeline coordination
│       ├── formatters.py   — Platform-specific output (X, LinkedIn, Bluesky)
│       └── alerts.py       — High-score notifications
├── config.py (pydantic-settings, SGOS_ env prefix)
├── observability.py (structlog + Prometheus metrics)
└── scheduler.py (background ingestion + auto-train cron)
```

**Pattern:** Every router delegates to a service. Zero inline SQL in any router.

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture Guide](docs/ARCHITECTURE.md) | System overview, data flow, design patterns, DB schema |
| [API Reference](docs/API_REFERENCE.md) | Every endpoint with parameters, examples, edge cases |
| [Pipeline Deep Dive](docs/PIPELINE.md) | How the viral content pipeline works stage by stage |
| [Development Guide](docs/DEVELOPMENT.md) | Setup, testing, adding scorers/formatters, conventions |

## Quick Start

```bash
# Clone
git clone https://github.com/StraughterG/sgos-backend.git
cd sgos-backend

# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install uv
uv pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys

# Run
python -m uvicorn main:app --host 0.0.0.0 --port 8420
```

Health check: `curl http://localhost:8420/health`

## API Endpoints

| Domain | Endpoints | Description |
|--------|-----------|-------------|
| Pipeline | `/pipeline/run`, `/opportunities`, `/format`, `/bulk` | Autonomous viral content pipeline + platform export |
| Feedback | `/feedback/published`, `/performance`, `/train`, `/stats` | Performance tracking + adaptive scorer training |
| Research | `/outliers`, `/trends`, `/stats`, `/brief` | Viral outlier detection, trend analysis |
| Search | `/search`, `/search/hybrid`, `/search/similar` | FTS5 + TF-IDF hybrid search |
| Content | `/repurpose`, `/ideas`, `/carousel`, `/analytics/score` | Multi-format content generation |
| Chat | `/chat/stream` | SSE streaming LLM responses |
| Voice | `/voice/build`, `/voices`, `/analyze` | Writing style profiles |
| Creators | `/creators`, `/creators/discover` | Author tracking and discovery |
| Boards | `/boards` | Save posts to swipe file boards |
| Ingestion | `/ingest/posts`, `/ingest/jobs` | Data ingestion with progress tracking |
| Alerts | `/alerts`, `/alerts/history` | Outlier notifications |
| Media | `/transcribe`, `/transcribe/url` | Whisper audio/video transcription |
| Scraping | `/scrape`, `/outliers/deep-scrape` | Firecrawl deep-scraping |
| Analytics | `/analytics/explain`, `/analytics/patterns` | Virality explanation |

Full schema: `http://localhost:8420/docs` (Swagger UI)

## Environment Variables

All variables use the `SGOS_` prefix for pydantic-settings:

```bash
SGOS_API_KEY=your-secret     # Bearer token auth (empty = dev mode)
SGOS_DB_PATH=sgos.db          # SQLite database path
SGOS_MAX_UPLOAD_MB=100        # Max file upload size
```

See `.env.example` for the full list.

## Docker

```bash
docker build -t sgos-backend .
docker run -p 8420:8420 --env-file .env sgos-backend
```

Or use the root-level `docker-compose.yml` for the full stack (backend + frontend + Caddy).

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific categories
pytest tests/ -v -k "TestPipeline"       # Pipeline unit tests
pytest tests/ -v -k "TestAdaptive"       # Feedback loop + closed-loop
pytest tests/ -v -k "TestFormatter"      # Platform formatters + alerts
pytest tests/ -v -k "TestPerformance"    # Load tests
pytest tests/ -v -k "TestSecurity"       # Security tests
pytest tests/ -v -k "TestEdgeCases"      # Edge cases
```

180 tests covering: unit, integration, adaptive scoring, closed-loop feedback, platform formatting, pipeline alerts, edge cases, error handling, performance, security, and SOLID compliance.

## Observability

- **Structured logging** — `structlog` with JSON output
- **Prometheus metrics** — `GET /metrics` (text format), `GET /metrics/json`
- **Request tracing** — automatic latency tracking (p50/p95/p99)
- **Scheduler status** — `GET /scheduler/status`

## License

MIT. Do whatever you want with it.

---

*Built by [@StraughterG](https://x.com/StraughterG)*
