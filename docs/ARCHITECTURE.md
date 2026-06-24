# Architecture Guide

## System Overview

SGOS (StraughterG-OS) is a **creator intelligence platform** with two components:

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (Next.js 16 + TypeScript)                         │
│  ~/StraughterG-os/  •  Port 3000                            │
│                                                             │
│  • Content generation UI (multi-model LLM chat)             │
│  • Pipeline Dashboard (viral opportunity management)        │
│  • Tone engine (4-axis control)                              │
│  • Viral score visualization                                │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP (REST + SSE)
┌────────────────────────▼────────────────────────────────────┐
│  Backend (Python 3.12 + FastAPI)                            │
│  ~/sgos-backend/  •  Port 8420                              │
│                                                             │
│  • 13 domain routers (HTTP layer)                            │
│  • 10 service modules (business logic)                      │
│  • Autonomous Viral Content Pipeline                        │
│  • Feedback loop with adaptive scoring                      │
│  • SQLite persistence (single file)                         │
│  • Background scheduler (4h ingestion cycle)                │
└─────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
sgos-backend/
├── main.py                    # App factory, middleware, CORS, startup
├── config.py                  # pydantic-settings (SGOS_ env prefix)
├── database.py                # SQLite connection + Row factory
├── scheduler.py               # Background ingestion cron (APScheduler)
├── alert_system.py            # Telegram outlier notifications
├── observability.py           # structlog + Prometheus metrics
│
├── routers/                   # HTTP layer — thin delegation to services
│   ├── research.py            #   Outlier detection, trend analysis
│   ├── pipeline.py            #   Viral content pipeline + bulk actions
│   ├── feedback.py            #   Performance tracking + weight training
│   ├── content.py             #   Multi-format content generation
│   ├── chat.py                #   SSE streaming LLM responses
│   ├── search.py              #   FTS5 + TF-IDF hybrid search
│   ├── voice.py               #   Writing style profiles
│   ├── creators.py            #   Author tracking + discovery
│   ├── ingestion.py           #   Data ingestion + job tracking
│   ├── alerts.py              #   Outlier alert management
│   ├── analytics.py           #   Virality explanation
│   ├── scrape.py              #   Firecrawl deep-scraping
│   ├── media.py               #   Whisper transcription
│   └── boards.py              #   Swipe-file board management
│
├── services/                  # Business logic — zero HTTP awareness
│   ├── pipeline/              #   Autonomous viral content pipeline
│   │   ├── protocols.py       #     Interface contracts (SOLID)
│   │   ├── genome.py          #     Viral DNA extraction
│   │   ├── generator.py       #     Content variant generation
│   │   ├── scoring.py         #     Pluggable scoring strategies
│   │   ├── voice_match.py     #     TF-IDF voice similarity
│   │   ├── repository.py      #     SQLite genome/opportunity storage
│   │   ├── orchestrator.py    #     Pipeline coordination
│   │   ├── formatters.py      #     Platform-specific output formatting
│   │   └── alerts.py          #     High-score notification dispatch
│   ├── feedback.py            #   Performance tracking + weight training
│   ├── research.py            #   Outlier detection + z-score analysis
│   ├── search.py              #   Hybrid search (FTS5 + TF-IDF + RRF)
│   ├── content.py             #   Content generation service
│   ├── chat.py                #   Chat streaming service
│   ├── voice.py               #   Voice profile builder
│   ├── creators.py            #   Creator tracking service
│   ├── ingestion.py           #   Data ingestion orchestration
│   └── boards.py              #   Board management service
│
└── tests/                     # 180 tests
    ├── test_api.py            #   API integration tests
    ├── test_pipeline.py       #   Pipeline unit tests
    ├── test_adaptive.py       #   Feedback loop + closed-loop tests
    └── test_formatters.py     #   Platform formatter + alert tests
```

## Design Principles

### SOLID Compliance

| Principle | How it's applied |
|-----------|-----------------|
| **Single Responsibility** | Each service handles ONE domain. Routers delegate — zero inline SQL. |
| **Open/Closed** | New scorers implement `IVariantScorer` protocol. New platforms implement `PlatformFormatter`. No existing code changes. |
| **Liskov Substitution** | Any scorer replaces any other via protocol. `CompositeScorer` doesn't know which scorers exist. |
| **Interface Segregation** | Small focused protocols: `IGenomeExtractor`, `IVariantScorer`, `IGenomeRepository` — not one god-interface. |
| **Dependency Inversion** | `PipelineEngine` depends on protocols, never concrete classes. Constructor injection. |

### Pattern: Thin Router → Service

Every router function delegates to a service. The router handles:
- HTTP parameter parsing and validation
- Error → HTTP status code mapping
- Response serialization

The service handles:
- Business logic
- Database access
- External API calls

```python
# Router (HTTP-aware)
@router.post("/run")
async def run_pipeline(hours: int = Query(24)):
    outliers = get_outliers(hours=hours)
    result = pipeline_engine.process_outliers(outliers=outliers)
    return result

# Service (no HTTP awareness)
class PipelineEngine:
    def process_outliers(self, outliers: list[dict], ...) -> dict:
        # Pure business logic, testable without HTTP
```

### Singleton Services

Stateful services use thread-safe singletons:

```python
class FeedbackService:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
```

The `pipeline_engine` is a module-level singleton created at import time via `create_pipeline_engine()` factory.

## Data Flow

### Autonomous Pipeline (every 4 hours)

```
Scheduler tick
    │
    ├─→ Ingest new posts from Reddit/HN/Twitter
    │
    ├─→ Research service detects outliers (z-score > 3.0)
    │
    └─→ Pipeline Engine
         │
         ├─ 1. Extract genomes (LLM or rule-based fallback)
         │     → ViralGenome: hook_type, emotional_arc, structural_pattern, key_phrases
         │
         ├─ 2. Generate variants (3-5 per genome, LLM-powered)
         │     → ContentVariant: thread, post, newsletter, script, carousel
         │
         ├─ 3. Score each variant (CompositeScorer)
         │     → EngagementScorer (40%): source post performance
         │     → StructureScorer (60%): hook strength, formatting, length, CTA
         │     → VoiceMatchScorer (30%): TF-IDF cosine similarity to user voice
         │
         ├─ 4. Store opportunities (deduped by content hash)
         │     → Alert if score ≥ 75 (Telegram push)
         │
         └─ 5. Auto-train weights (if ≥ 10 feedback records)
               → refresh_scorer() hot-swaps scorer without restart
```

### Feedback Loop (Closed)

```
User publishes opportunity
    │
    ├─→ POST /feedback/published (records genome_id + score at generation)
    │
    ├─→ POST /feedback/{id}/performance (later: impressions, engagement_rate)
    │
    └─→ POST /feedback/train (or auto after scheduler)
         │
         ├─ Pearson correlation: scorer_dimension ↔ engagement_rate
         ├─ Normalize correlations → new weights
         ├─ Blend: 70% new signal + 30% existing weights (prevents overfitting)
         └─→ persist to scorer_weights table
              │
              └─→ refresh_scorer() → next pipeline run uses trained weights
```

## Database Schema

Single SQLite file (`sgos.db` by default, configured via `SGOS_DB_PATH`).

### Core Tables

```sql
-- Viral DNA extracted from outlier posts
viral_genomes (
    post_id TEXT PRIMARY KEY,
    hook_type TEXT,           -- question, statistic, story, contrarian, list
    hook_text TEXT,
    emotional_arc TEXT,       -- JSON array: ["curiosity", "surprise", "excitement"]
    structural_pattern TEXT,  -- listicle, narrative, how_to, rant, analysis
    key_phrases TEXT,         -- JSON array
    content_length_words INTEGER,
    platform_signals TEXT,    -- JSON: {z_score, upvote_ratio, comment_count}
    engagement_score REAL,    -- 0-1 normalized
    created_at TEXT,
    opportunity_count INTEGER
)

-- Generated content opportunities
pipeline_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    genome_id TEXT REFERENCES viral_genomes(post_id),
    variant_type TEXT,         -- thread, post, newsletter, script, carousel
    title TEXT,
    content TEXT,
    content_hash TEXT UNIQUE,  -- SHA256[:16] dedup
    score REAL,                -- 0-100 composite
    score_breakdown TEXT,      -- JSON: {engagement: {raw, weight, weighted}, ...}
    hook TEXT,
    viewed BOOLEAN DEFAULT 0,
    dismissed BOOLEAN DEFAULT 0,
    created_at TEXT
)

-- Published content + real-world performance
performance_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER,
    genome_id TEXT,
    variant_type TEXT,
    score_at_generation REAL,
    score_breakdown TEXT,      -- JSON snapshot of scores at generation time
    published_at TEXT,
    platform TEXT DEFAULT 'twitter',
    -- Filled in later:
    impressions INTEGER,
    engagements INTEGER,
    likes INTEGER,
    reposts INTEGER,
    replies INTEGER,
    clicks INTEGER,
    engagement_rate REAL,      -- (engagements / impressions) * 100
    performance_tier TEXT      -- viral, above_avg, avg, below_avg
)

-- Trained scorer weights (updated by feedback loop)
scorer_weights (
    scorer_name TEXT UNIQUE,   -- engagement, structure, voice_match
    weight REAL,               -- 0-1, normalized to sum to 1.0
    trained_at TEXT,
    sample_size INTEGER,
    confidence REAL            -- min(sample_size / 100, 1.0)
)

-- Pipeline alert history
pipeline_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER,
    score REAL,
    title TEXT,
    hook TEXT,
    variant_type TEXT,
    alerted_at TEXT,
    notified INTEGER DEFAULT 0,
    dismissed INTEGER DEFAULT 0
)
```

## Threading Model

- **FastAPI** runs in an async event loop (uvicorn workers)
- **Background scheduler** (APScheduler) runs ingestion in a thread
- **FeedbackService** uses `_lock` for thread-safe singleton creation
- **Alert cooldowns** use `_cooldown_lock` for thread-safe dict access
- **SQLite** uses WAL mode for concurrent reads; writes are serialized

## Configuration

All settings use `SGOS_` prefix via pydantic-settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `SGOS_API_KEY` | *(empty)* | Bearer token auth. Empty = dev mode (no auth). |
| `SGOS_DB_PATH` | `sgos.db` | SQLite database file path |
| `SGOS_MAX_UPLOAD_MB` | `100` | Max file upload size |
| `SGOS_LLM_BASE_URL` | *(empty)* | OpenAI-compatible API base URL |
| `SGOS_LLM_API_KEY` | *(empty)* | LLM API key |
| `SGOS_LLM_MODEL` | `qwen-plus` | Default model for pipeline generation |
| `SGOS_TELEGRAM_BOT_TOKEN` | *(empty)* | Telegram bot token for alerts |
| `SGOS_TELEGRAM_CHAT_ID` | *(empty)* | Telegram chat ID for alerts |
| `SGOS_SCHEDULER_INTERVAL_HOURS` | `4` | Hours between scheduler runs |

## Error Handling

- **Service layer** raises Python exceptions
- **Router layer** catches and maps to HTTP status codes via `HTTPException`
- **Pipeline** wraps each post in try/except, collects errors in result dict
- **LLM calls** fail silently with fallback (genome extractor → rule-based, generator → template)

```python
# Pipeline error collection
results = {"errors": []}
for post in outliers:
    try:
        genome = extractor.extract(post)
        ...
    except Exception as e:
        results["errors"].append({"post_id": post_id, "error": str(e)})
# Pipeline completes even if some posts fail
```
