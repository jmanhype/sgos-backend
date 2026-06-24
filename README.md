# SGOS Backend

> StraughterG-OS Intelligence Backend — Research Engine, Trend Detection, Voice Profiles, Content Generation

![Python](https://img.shields.io/badge/Python-3.12-blue?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green?style=flat-square)
![Tests](https://img.shields.io/badge/tests-52%20passed-brightgreen?style=flat-square)

---

## What is this?

A Python/FastAPI backend powering [StraughterG-OS](https://github.com/StraughterG/StraughterG-os) — a creator intelligence platform that detects viral outliers, generates content, and tracks trending creators.

### Features

- **Viral Outlier Detection** — Z-score analysis across Reddit, Hacker News, Twitter
- **Multi-Format Content Generation** — Twitter threads, LinkedIn posts, TikTok scripts, IG carousels, newsletters
- **Voice Profiles** — Analyze author writing patterns, apply to generated content
- **SSE Streaming** — Real-time LLM token delivery for chat
- **FTS5 + TF-IDF Search** — Hybrid keyword + semantic search with Reciprocal Rank Fusion
- **Creator Tracking** — Follow high-performing authors, discover new ones
- **Automated Ingestion** — Background scheduler pulls fresh data every 4 hours
- **Content Scoring** — LLM-based virality analysis (hook strength, shareability, etc.)

### Architecture

```
main.py (slim app factory, ~110 LOC)
├── routers/ (12 domain routers — HTTP layer)
├── services/ (7 business logic modules)
├── repositories/ (typed DB access)
├── models/ (Pydantic schemas)
├── config.py (pydantic-settings, SGOS_ env prefix)
├── observability.py (structlog + Prometheus metrics)
└── scheduler.py (background ingestion cron)
```

**Pattern:** Every router delegates to a service. Zero inline SQL in any router.

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
pytest tests/ -v -k "TestPerformance"    # Load tests
pytest tests/ -v -k "TestSecurity"       # Security tests
pytest tests/ -v -k "TestEdgeCases"      # Edge cases
```

52 tests covering: unit, integration, edge cases, error handling, performance, security.

## Observability

- **Structured logging** — `structlog` with JSON output
- **Prometheus metrics** — `GET /metrics` (text format), `GET /metrics/json`
- **Request tracing** — automatic latency tracking (p50/p95/p99)
- **Scheduler status** — `GET /scheduler/status`

## License

MIT. Do whatever you want with it.

---

*Built by [@StraughterG](https://x.com/StraughterG)*
