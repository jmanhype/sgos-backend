# Development Guide

## Prerequisites

- Python 3.12+
- Node.js 20+
- uv (Python package manager)
- npm

## Project Setup

### Backend

```bash
git clone https://github.com/jmanhype/sgos-backend.git
cd sgos-backend

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install uv
uv pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys (see Configuration below)

# Run the server
python -m uvicorn main:app --host 0.0.0.0 --port 8420 --reload
```

Verify: `curl http://localhost:8420/health`

### Frontend

```bash
git clone https://github.com/jmanhype/StraughterG-os.git
cd StraughterG-os

npm install

# Configure environment
cp .env.local.example .env.local
# Edit .env.local with your API keys

# Run the dev server
npm run dev
```

Open: [http://localhost:3000](http://localhost:3000)

### Full Stack (Docker Compose)

```bash
# From the sgos-backend root
docker-compose up
```

This starts:
- Backend on port 8420
- Frontend on port 3000
- Caddy reverse proxy (if configured)

---

## Environment Configuration

### Backend (.env)

```bash
# Authentication (empty = dev mode, no auth required)
SGOS_API_KEY=

# Database
SGOS_DB_PATH=sgos.db

# LLM (OpenAI-compatible API)
SGOS_LLM_BASE_URL=https://api.openai.com/v1
SGOS_LLM_API_KEY=***
*SGOS_LLM_MODEL=qwen-plus

# Telegram alerts (optional)
SGOS_TELEGRAM_BOT_TOKEN=
SGOS_TELEGRAM_CHAT_ID=

# Scheduler
SGOS_SCHEDULER_INTERVAL_HOURS=4

# Uploads
SGOS_MAX_UPLOAD_MB=100
```

### Frontend (.env.local)

```bash
# Qwen / DashScope
AI_API_KEY=***
*...N
# Z.AI / ZhiPu / GLM
ZAI_API_KEY=***
Z...N
# Backend URL
NEXT_PUBLIC_API_URL=http://localhost:8420
```

---

## Running Tests

### Backend (180 tests)

```bash
cd sgos-backend

# All tests
pytest tests/ -v

# By category
pytest tests/ -v -k "TestPipeline"      # Pipeline unit tests
pytest tests/ -v -k "TestAdaptive"      # Feedback loop + closed-loop tests
pytest tests/ -v -k "TestFormatter"     # Platform formatters + alerts
pytest tests/ -v -k "TestAPI"           # API integration tests
pytest tests/ -v -k "TestPerformance"   # Load tests
pytest tests/ -v -k "TestSecurity"      # Security tests
pytest tests/ -v -k "TestEdgeCases"     # Edge cases

# Quick (no verbose)
pytest tests/ -q

# With coverage
pytest tests/ --cov=services --cov=routers --cov-report=term-missing
```

### Frontend

```bash
cd StraughterG-os

# Build check (catches TypeScript errors)
npx next build

# Dev server
npm run dev
```

---

## Test Structure

```
tests/
├── test_api.py              # 56 tests — HTTP endpoint integration
│   ├── TestHealthEndpoint
│   ├── TestResearchEndpoints
│   ├── TestPipelineEndpoints
│   ├── TestFeedbackEndpoints
│   ├── TestChatStreamEndpoint
│   ├── TestSecurity          (auth bypass, injection, oversized)
│   ├── TestPerformance       (concurrent requests, response times)
│   └── TestEdgeCases         (empty bodies, special chars, nulls)
│
├── test_pipeline.py         # 57 tests — Pipeline unit tests
│   ├── TestScoringSystem     (EngagementScorer, StructureScorer, CompositeScorer)
│   ├── TestGenerator         (template fallback, voice prompts, variant types)
│   ├── TestGenomeRepository  (save, get, list, dedup, stats)
│   ├── TestPipelineEngine    (process_outliers, regenerate, skip_existing)
│   ├── TestEdgeCases         (empty posts, missing fields, zero engagement)
│   └── TestSOLIDCompliance   (protocol checks, open/closed, single responsibility)
│
├── test_adaptive.py         # 29 tests — Feedback loop + closed-loop
│   ├── TestFeedbackService   (mark_published, record_performance, stats)
│   ├── TestScorerTraining    (train_weights, correlation, blending, edge cases)
│   ├── TestVoiceMatchScorer  (TF-IDF, cosine similarity, lazy loading)
│   ├── TestClosedLoop        (auto_train, refresh_scorer, end-to-end)
│   └── TestEdgeCases         (no data, single record, zero engagement)
│
└── test_formatters.py       # 34 tests — Formatters + alerts
    ├── TestXThreadFormatter  (splitting, numbering, char limits, markdown stripping)
    ├── TestLinkedInFormatter (paragraphs, title, hook, char limit, markdown)
    ├── TestBlueskyFormatter  (platform, 300-char limit)
    ├── TestNewsletterFormatter (markdown preservation, title, hook)
    ├── TestFormatForPlatform (aliases, unknown platform, opportunity dict)
    └── TestPipelineAlerts    (pending alerts, high score, dismiss)
```

---

## Adding a New Scorer

Implement `IVariantScorer` protocol. No changes to existing code needed (Open/Closed).

```python
# services/pipeline/my_scorer.py

from services.pipeline.protocols import ViralGenome, ContentVariant

class ReadabilityScorer:
    """Scores variants on readability (Flesch-Kincaid, sentence length)."""

    def __init__(self, weight: float = 0.2):
        self._weight = weight

    @property
    def name(self) -> str:
        return "readability"

    @property
    def weight(self) -> float:
        return self._weight

    def score(self, variant: ContentVariant, genome: ViralGenome) -> float:
        # Your scoring logic (return 0-100)
        sentences = variant.content.split(".")
        avg_sentence_length = len(variant.content.split()) / max(len(sentences), 1)

        if 10 <= avg_sentence_length <= 20:
            return 90.0  # Optimal
        elif avg_sentence_length < 10:
            return 70.0  # Too choppy
        else:
            return max(30.0, 100.0 - (avg_sentence_length - 20) * 3)
```

Register it in `services/pipeline/__init__.py`:

```python
from services.pipeline.my_scorer import ReadabilityScorer

def _default_scorers() -> list:
    return [
        EngagementScorer(weight=0.30),
        StructureScorer(weight=0.35),
        VoiceMatchScorer(weight=0.20),
        ReadabilityScorer(weight=0.15),  # NEW
    ]
```

That's it. `CompositeScorer` picks it up automatically. Tests should verify:
- Score range is 0–100
- `name` property is unique
- `weight` property matches constructor arg
- Protocol compliance: `isinstance(scorer, IVariantScorer)`

---

## Adding a New Platform Formatter

Implement a `PlatformFormatter` subclass:

```python
# services/pipeline/formatters.py

class MastodonFormatter(PlatformFormatter):
    """Mastodon: 500 chars per post, thread support."""
    platform = "mastodon"
    max_chars = 500

    def format(self, content: str, title: str = "", hook: str = "") -> FormattedPost:
        # Your formatting logic
        text = self._clean_markdown(content)
        parts = self._split_to_limit(text, self.max_chars)
        return FormattedPost(
            platform="mastodon",
            parts=parts,
            char_counts=[len(p) for p in parts],
            total_chars=sum(len(p) for p in parts),
            warnings=[],
        )
```

Register in `FORMATTERS`:

```python
FORMATTERS["mastodon"] = MastodonFormatter()
```

The API endpoints (`/format`, `/format/all`, `/copy-batch`) pick it up automatically.

---

## Common Development Tasks

### Reset the Database

```bash
rm sgos.db
# Tables are auto-created on first request
```

### Seed Test Data

```bash
# Run the pipeline manually with sample data
curl -X POST "http://localhost:8420/pipeline/run?hours=168&limit=5"
```

### Debug Pipeline Output

```bash
# Check what genomes were extracted
curl "http://localhost:8420/pipeline/genomes?limit=10" | python -m json.tool

# Check opportunities with scores
curl "http://localhost:8420/pipeline/opportunities?limit=5&unseen_only=false" | python -m json.tool

# Check feedback loop status
curl "http://localhost:8420/feedback/stats" | python -m json.tool
```

### Add a New Router

1. Create `routers/my_domain.py` with `router = APIRouter(prefix="/my-domain", tags=["my-domain"])`
2. Create `services/my_service.py` with business logic
3. Register in `main.py`: `app.include_router(my_router)`
4. Add tests in `tests/test_api.py`

### Change Scheduler Interval

```bash
# .env
SGOS_SCHEDULER_INTERVAL_HOURS=2
```

Restart the server. The scheduler picks up the new interval on startup.

---

## Architecture Decisions

### Why SQLite (not PostgreSQL)?

- Single-file deployment (no DB server to manage)
- Perfectly handles the workload (hundreds of records, not millions)
- WAL mode supports concurrent reads
- Docker-ready: database is a volume mount

### Why TF-IDF for Voice Matching (not embeddings)?

- **Latency:** TF-IDF runs in <1ms. Embedding models take 100–500ms per call.
- **Scale:** Pipeline scores 15+ variants per run. At 500ms each, that's 7.5 seconds of pure latency.
- **Quality:** For style similarity (not semantic similarity), TF-IDF cosine is sufficient. The user's vocabulary and phrase patterns are strong voice signals.
- **No extra dependencies:** No vector DB, no embedding model to host.

### Why Composite Scoring (not a single LLM call)?

- **Interpretability:** You can see exactly why a variant scored 87 (engagement: 90, structure: 82, voice: 92).
- **Trainability:** Each dimension's correlation with real performance is measured independently. Weights adjust based on what actually predicts success.
- **Speed:** Rule-based scorers run in <1ms. Only the generation step needs an LLM.
- **Extensibility:** Add a new dimension without retraining or modifying existing scorers.

### Why Content Hashing for Deduplication?

- Prevents duplicate opportunities when the pipeline runs on the same outliers twice
- Prevents regeneration of identical variants
- Uses `genome_id + variant_type + content` as the fingerprint — same genome can produce different variant types without collision

### Why Exponential Moving Average for Weight Training?

- `final = 0.7 × new + 0.3 × old` prevents overfitting to small sample sizes
- As more data accumulates, new signal naturally dominates (each training run blends again)
- A single batch of 10 records won't dramatically shift weights
- At 100+ records, confidence reaches 1.0 and the system trusts the data fully

---

## Code Conventions

- **Routers are thin.** No business logic in routers — delegate to services.
- **Services are testable.** No HTTP awareness in services — they take dicts and return dicts.
- **Protocols over inheritance.** Use `Protocol` classes for interfaces, not ABCs.
- **Singletons for stateful services.** Thread-safe double-checked locking.
- **Errors are data.** Pipeline collects errors per-post, doesn't abort on single failures.
- **Imports are lazy.** LLM clients are imported inside methods, not at module level (allows testing without API keys).
- **No raw SQL in routers.** All DB access goes through services or repositories.

---

## Deployment

### Docker

```bash
docker build -t sgos-backend .
docker run -p 8420:8420 --env-file .env -v $(pwd)/sgos.db:/app/sgos.db sgos-backend
```

### Docker Compose (Full Stack)

```yaml
version: '3.8'
services:
  backend:
    build: ./sgos-backend
    ports: ["8420:8420"]
    env_file: ./sgos-backend/.env
    volumes: ["./sgos.db:/app/sgos.db"]

  frontend:
    build: ./StraughterG-os
    ports: ["3000:3000"]
    env_file: ./StraughterG-os/.env.local
    depends_on: [backend]
```

### Production Checklist

- [ ] Set `SGOS_API_KEY` to a strong secret
- [ ] Set `SGOS_LLM_API_KEY` to your LLM provider key
- [ ] Configure Telegram bot token for alerts
- [ ] Mount `sgos.db` as a persistent volume
- [ ] Set `SGOS_SCHEDULER_INTERVAL_HOURS` to desired frequency
- [ ] Run tests: `pytest tests/ -q`
- [ ] Verify health: `curl http://localhost:8420/health`
- [ ] Verify docs: Open `http://localhost:8420/docs`
