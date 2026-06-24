# SGOS System Architecture

> StraughterG-OS — Creator Intelligence Platform
> Last updated: June 2026

---

## 1. System Overview

SGOS is a **self-hosted creator intelligence platform** that ingests social media content, detects viral patterns, and generates platform-optimized content using LLMs. It serves a single user (the creator) with a focus on Twitter, LinkedIn, Instagram, TikTok, and newsletters.

### Core Capabilities

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SGOS Platform                                │
├─────────────┬──────────────┬──────────────┬─────────────────────────┤
│  Research   │   Content    │   Analytics  │     Workflow            │
│  Engine     │   Engine     │   Layer      │     Tools               │
├─────────────┼──────────────┼──────────────┼─────────────────────────┤
│ Reddit      │ LLM Chat     │ Viral Score  │ Voice Profile           │
│ Hacker News │ Threads      │ Z-Score      │ Creator Tracking        │
│ YouTube     │ Articles     │ Outlier Det. │ Boards/Swipe Files      │
│ Twitter     │ Carousels    │ Trend Detect │ Project Management      │
│ Web Scrape  │ Repurposing  │ Alert System │ Transcription           │
│ SearXNG     │ Idea Gen     │ TF-IDF Search│ Style Guide             │
└─────────────┴──────────────┴──────────────┴─────────────────────────┘
```

### Current Metrics

| Dimension | Value |
|-----------|-------|
| Backend LOC | ~6,700 (22 Python files) |
| Frontend LOC | ~7,300 (21 TS/TSX files) |
| API Endpoints | 40+ |
| Data Store | SQLite + FTS5 |
| External Deps | Aliyun LLM, Firecrawl, SearXNG, yt-dlp, Whisper |
| Deployment | Local dev machine + 3090 GPU server |

---

## 2. Current Architecture (As-Is)

```
┌──────────────────────────────────────────────────────────┐
│                    Browser (localhost:3000)                │
│                                                          │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ NavSide │  │  Chat    │  │ Workspc  │  │  Views   │ │
│  │ bar     │  │  Panel   │  │ Sidebar  │  │ (×10)    │ │
│  └─────────┘  └────┬─────┘  └──────────┘  └──────────┘ │
│                    │                                     │
│              ┌─────▼──────┐                              │
│              │ page.tsx   │  ← God component (682 LOC)   │
│              │ (all state)│    owns routing + 15+ states  │
│              └─────┬──────┘                              │
│                    │ fetch('/api/chat')                   │
│              ┌─────▼──────┐                              │
│              │ route.ts   │  ← LLM proxy + research      │
│              └────────────┘                              │
│                                                          │
│  localStorage: sessions, settings, workspace state       │
└──────────────────────────┬───────────────────────────────┘
                           │ HTTP
┌──────────────────────────▼───────────────────────────────┐
│                  Backend (localhost:8420)                  │
│                                                          │
│  ┌─────────────────────────────────────────────────────┐ │
│  │              main.py (1,164 LOC)                    │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐│ │
│  │  │ 40+      │ │ Auth     │ │ CSRF     │ │ CORS   ││ │
│  │  │ endpoints│ │ middleware│ │ middleware│ │        ││ │
│  │  └──────────┘ └──────────┘ └──────────┘ └────────┘│ │
│  └─────────────────────────────────────────────────────┘ │
│         │           │           │           │             │
│  ┌──────▼──┐  ┌─────▼───┐  ┌───▼────┐  ┌───▼──────┐    │
│  │database │  │ingest    │  │voice   │  │search    │    │
│  │.py      │  │modules   │  │profile │  │engines   │    │
│  │(pool)   │  │(×5)      │  │.py     │  │(×3)      │    │
│  └──────┬──┘  └──────────┘  └────────┘  └──────────┘    │
│         │                                                │
│  ┌──────▼──────────────────────────────────────────────┐ │
│  │            SQLite + FTS5 (sgos.db)                  │ │
│  │  posts | creators | boards | voice | vector | fts5  │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                          │
│  External (via SSH to 3090):                             │
│  ┌────────────┐  ┌───────────┐  ┌──────────┐           │
│  │ Firecrawl  │  │ SearXNG   │  │ Whisper  │           │
│  │ :3002      │  │ :8888     │  │ (local)  │           │
│  └────────────┘  └───────────┘  └──────────┘           │
└──────────────────────────────────────────────────────────┘
```

### Current Architecture Problems

| # | Problem | Impact | Severity |
|---|---------|--------|----------|
| A1 | **God component** — `page.tsx` owns 15+ useState hooks and all routing | Adding any feature requires modifying the root file | HIGH |
| A2 | **God endpoint file** — `main.py` has 40+ route handlers in 1,164 lines | Merge conflicts, hard to navigate, no separation of concerns | HIGH |
| A3 | **No service layer** — endpoints directly call database functions | Business logic mixed with HTTP concerns, hard to test | HIGH |
| A4 | **Synchronous ingestion** — Reddit/HN/YouTube ingestion blocks the request | API timeouts on slow network, no retry, no progress tracking | MEDIUM |
| A5 | **No structured logging** — print statements and no log levels | Can't diagnose production issues, no audit trail | MEDIUM |
| A6 | **Client-only sessions** — localStorage means sessions don't survive device changes | No sync across devices, lost on cache clear | LOW (single-user) |
| A7 | **No config layer** — env vars scattered, no validation at startup | Silent failures when config is wrong | MEDIUM |
| A8 | **No API versioning** — breaking changes break the frontend | Frontend and backend are tightly coupled | LOW (co-deployed) |

---

## 3. Target Architecture

### 3.1 Design Principles

1. **Single-user, local-first** — No need for multi-tenant, cloud-scale infra. Optimize for developer experience.
2. **Incremental migration** — Don't rewrite everything. Move endpoints one module at a time.
3. **Convention over configuration** — Use FastAPI's built-in dependency injection for cross-cutting concerns.
4. **Type safety end-to-end** — Pydantic models in backend → OpenAPI spec → TypeScript types in frontend.
5. **Async where it matters** — Ingestion and LLM calls are I/O-bound. Use background tasks, not sync blocking.
6. **SQLite is fine** — For single-user with WAL mode, SQLite handles 100K+ rows easily. Don't migrate to Postgres without a real reason.

### 3.2 Target Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Frontend (Next.js 16)                          │
│                                                                     │
│  ┌──────────┐                                                       │
│  │ Layout   │  ← NavSidebar (persistent)                           │
│  │ Provider │                                                       │
│  └────┬─────┘                                                       │
│       │                                                             │
│  ┌────▼──────────────────────────────────────────┐                  │
│  │              State Management                  │                  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────┐  │                  │
│  │  │ useChat  │ │ useSess- │ │ useWorkspace │  │                  │
│  │  │ Store    │ │ ionStore │ │ Store        │  │                  │
│  │  └──────────┘ └──────────┘ └──────────────┘  │                  │
│  └───────────────────────────────────────────────┘                  │
│       │                                                             │
│  ┌────▼──────────────────────────────────────────┐                  │
│  │              View Components                   │                  │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ │                  │
│  │  │ Chat   │ │Research│ │ Search │ │Settings│ │                  │
│  │  │ Panel  │ │ Feed   │ │ View   │ │ View   │ │                  │
│  │  └────────┘ └────────┘ └────────┘ └────────┘ │                  │
│  └───────────────────────────────────────────────┘                  │
│       │                                                             │
│  ┌────▼──────────────┐                                              │
│  │  API Client Layer  │  ← Typed fetch wrapper, error handling     │
│  │  (lib/api.ts)      │    retry logic, abort controllers           │
│  └────────────────────┘                                              │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTP/JSON
┌──────────────────────────▼──────────────────────────────────────────┐
│                     Backend (FastAPI)                                │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │                   Middleware Stack                           │     │
│  │  CORS → CSRF Origin Check → Auth (Bearer) → Rate Limit     │     │
│  │  → Request ID → Structured Logger                           │     │
│  └────────────────────────────────────────────────────────────┘     │
│       │                                                             │
│  ┌────▼──────────────────────────────────────────────────────┐      │
│  │                    Router Layer                             │      │
│  │  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────────┐  │      │
│  │  │ research │ │ content  │ │ voice  │ │ ingestion     │  │      │
│  │  │ _router  │ │ _router  │ │ _router│ │ _router       │  │      │
│  │  └────┬─────┘ └────┬─────┘ └───┬────┘ └──────┬────────┘  │      │
│  │       │            │           │              │           │      │
│  │  ┌────▼─────┐ ┌────▼─────┐ ┌──▼───┐ ┌───────▼───────┐  │      │
│  │  │ search   │ │ repurpose│ │creatr│ │ ideas         │  │      │
│  │  │ _router  │ │ _router  │ │router│ │ _router       │  │      │
│  │  └──────────┘ └──────────┘ └──────┘ └───────────────┘  │      │
│  └───────────────────────────────────────────────────────────┘      │
│       │                                                             │
│  ┌────▼──────────────────────────────────────────────────────┐      │
│  │                   Service Layer                            │      │
│  │  ┌──────────────┐ ┌────────────┐ ┌──────────────────┐    │      │
│  │  │ ResearchSvc  │ │ ContentSvc │ │ IngestionSvc     │    │      │
│  │  │ (search,     │ │ (generate, │ │ (reddit, HN,     │    │      │
│  │  │  outliers,   │ │  repurpose,│ │  YouTube, Twitter│    │      │
│  │  │  trends)     │ │  score)    │ │  scrape, topics) │    │      │
│  │  └──────────────┘ └────────────┘ └──────────────────┘    │      │
│  │  ┌──────────────┐ ┌────────────┐ ┌──────────────────┐    │      │
│  │  │ VoiceSvc     │ │ CreatorSvc │ │ AlertSvc         │    │      │
│  │  │ (profile,    │ │ (track,    │ │ (outlier alerts, │    │      │
│  │  │  train)      │ │  stats)    │ │  creator spikes) │    │      │
│  │  └──────────────┘ └────────────┘ └──────────────────┘    │      │
│  └───────────────────────────────────────────────────────────┘      │
│       │                                                             │
│  ┌────▼──────────────────────────────────────────────────────┐      │
│  │                  Repository Layer                          │      │
│  │  ┌────────────────────────────────────────────────────┐   │      │
│  │  │ PostRepo │ BoardRepo │ CreatorRepo │ SessionRepo  │   │      │
│  │  └────────────────────────────────────────────────────┘   │      │
│  │  Thread-local SQLite connection pool (database.py)        │      │
│  └───────────────────────────────────────────────────────────┘      │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │              Background Task Queue (asyncio)              │       │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐    │       │
│  │  │ IngestionJob │ │ AnalysisJob  │ │ ScrapeJob    │    │       │
│  │  │ (Reddit/HN/  │ │ (Z-scores,   │ │ (Firecrawl   │    │       │
│  │  │  YT/Twitter) │ │  vectors)    │ │  via 3090)   │    │       │
│  │  └──────────────┘ └──────────────┘ └──────────────┘    │       │
│  └──────────────────────────────────────────────────────────┘       │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │              Config Layer (pydantic-settings)             │       │
│  │  ┌────────────────────────────────────────────────────┐  │       │
│  │  │ AppSettings │ DatabaseSettings │ LLMSettings      │  │       │
│  │  │ SSHSettings │ FirecrawlSettings│ AlertSettings    │  │       │
│  │  └────────────────────────────────────────────────────┘  │       │
│  └──────────────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │  SQLite     │
                    │  + FTS5     │
                    │  (sgos.db)  │
                    └─────────────┘
```

### 3.3 Backend Module Breakdown

#### Router Layer (`routers/`)

Each router maps to a domain concern. FastAPI's `APIRouter` with tags enables automatic OpenAPI grouping.

| Router | Current Endpoints | LOC | Responsibility |
|--------|-------------------|-----|----------------|
| `research.py` | `/outliers`, `/trends`, `/brief`, `/stats` | ~120 | Trend detection, outlier listing, daily brief |
| `search.py` | `/search`, `/search/hybrid`, `/search/similar`, `/search/related`, `/search/build-index` | ~100 | FTS5 + TF-IDF hybrid search |
| `content.py` | `/repurpose`, `/repurpose/ai`, `/ideas/generate`, `/ideas` | ~150 | LLM-powered content generation |
| `ingestion.py` | `/ingest`, `/ingest/sync`, `/ingest/posts`, `/ingest/youtube`, `/ingest/topics` | ~120 | Data pipeline triggers |
| `voice.py` | `/voice/build`, `/voice/build-from-text`, `/voice/{name}`, `/voice/{name}/prompt`, `/voices` | ~80 | Voice profile management |
| `creators.py` | `/creators/follow`, `/creators/unfollow`, `/creators`, `/creators/{handle}/posts`, `/creators/stats` | ~80 | Creator tracking |
| `boards.py` | `/boards`, `/boards/{id}`, `/boards/{id}/posts` | ~80 | Swipe file management |
| `alerts.py` | `/alerts`, `/alerts/{id}/read`, `/alerts/outliers/check`, `/alerts/history` | ~80 | Alert system |
| `media.py` | `/transcribe/file`, `/transcribe/url` | ~60 | Audio/video transcription |
| `analytics.py` | `/analyze` | ~30 | Viral analysis |

#### Service Layer (`services/`)

Pure business logic. No HTTP concerns. Receives typed inputs, returns typed outputs.

```python
# services/research.py
class ResearchService:
    def __init__(self, post_repo: PostRepository):
        self.post_repo = post_repo

    def get_outliers(self, platform: str, hours: int, limit: int) -> list[OutlierPost]:
        """Find posts with statistically unusual engagement."""
        posts = self.post_repo.by_platform(platform, hours)
        z_scores = compute_z_scores([p.score for p in posts])
        outliers = [
            OutlierPost.from_post(post, z)
            for post, z in zip(posts, z_scores)
            if z > 2.0
        ]
        return sorted(outliers, key=lambda o: o.z_score, reverse=True)[:limit]
```

#### Repository Layer (`repositories/`)

Thin wrappers around SQLite queries. All SQL lives here.

```python
# repositories/posts.py
class PostRepository:
    def __init__(self, get_conn: Callable[[], sqlite3.Connection]):
        self._get_conn = get_conn

    def by_platform(self, platform: str, hours: int) -> list[Post]:
        conn = self._get_conn()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = conn.execute(
            "SELECT * FROM posts WHERE platform = ? AND created_at > ? ORDER BY score DESC",
            (platform, cutoff.isoformat())
        ).fetchall()
        return [Post(**dict(r)) for r in rows]
```

### 3.4 Frontend Module Breakdown

#### State Management: Custom Hooks (not Zustand/Redux)

For a single-user app, Zustand/Redux is overkill. Custom hooks with `useReducer` provide the same benefits with zero dependencies:

```typescript
// hooks/useChatStore.ts
interface ChatState {
  messages: Message[];
  isLoading: boolean;
  latestScores: ViralScores | null;
  error: ChatError | null;
}

type ChatAction =
  | { type: 'SEND_MESSAGE'; content: string }
  | { type: 'RECEIVE_RESPONSE'; message: Message; scores?: ViralScores }
  | { type: 'SET_LOADING'; loading: boolean }
  | { type: 'SET_ERROR'; error: ChatError }
  | { type: 'CLEAR_MESSAGES' }
  | { type: 'RETRY_LAST' };

export function useChatStore() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  // ... action creators that call API and dispatch
  return { ...state, dispatch };
}
```

**Why not Zustand?** Single-user app, 5 stores max. `useReducer` + context gives us:
- Zero new dependencies
- Same devtools experience (React DevTools shows reducer state)
- Action creators are just functions that call `dispatch`
- Easy to test (pure reducer functions)

**Trade-off acknowledged:** If the app grows beyond ~10 stores or needs cross-store orchestration, migrate to Zustand. The reducer pattern makes this migration trivial (reducers become store definitions).

#### Page Decomposition

```
app/
├── layout.tsx          ← Providers, NavSidebar (persistent shell)
├── page.tsx            ← Thin router: reads activeNav, renders view
├── views/
│   ├── ChatView.tsx    ← ChatPanel + WorkspaceSidebar
│   ├── ResearchView.tsx← ResearchFeed
│   ├── SearchView.tsx  ← SearchView
│   ├── HistoryView.tsx ← HistoryView
│   ├── CreatorView.tsx ← CreatorView
│   ├── SettingsView.tsx← SettingsView
│   ├── BoardsView.tsx  ← BoardsView
│   ├── ProjectsView.tsx← ProjectsView
│   ├── VoiceView.tsx   ← VoiceView
│   └── TranscribeView.tsx
└── api/
    └── chat/
        └── route.ts    ← LLM proxy (stays server-side)
```

`page.tsx` drops from 682 → ~40 lines:

```typescript
export default function Home() {
  const [activeNav, setActiveNav] = useNavStore();
  
  return (
    <div className="flex h-screen">
      <NavSidebar activeNav={activeNav} onNavChange={setActiveNav} />
      <ViewRouter activeNav={activeNav} />
    </div>
  );
}
```

#### API Client Layer

Single typed wrapper replaces scattered `fetch()` calls:

```typescript
// lib/api.ts
class SgosClient {
  constructor(private baseUrl: string) {}

  private async request<T>(path: string, options?: RequestInit): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new SgosError(res.status, body.detail || res.statusText);
    }
    return res.json();
  }

  // Typed methods — auto-generated from OpenAPI spec
  outliers = (platform: string, hours = 24) =>
    this.request<OutlierPost[]>(`/outliers?platform=${platform}&hours=${hours}`);
  
  search = (q: string, limit = 20) =>
    this.request<SearchResult[]>(`/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  // ...
}

export const api = new SgosClient(process.env.NEXT_PUBLIC_SGOS_URL || 'http://localhost:8420');
```

### 3.5 Configuration Layer

Replace scattered `os.environ.get()` calls with validated `pydantic-settings`:

```python
# config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Server
    host: str = "0.0.0.0"
    port: int = 8420
    api_key: str = ""  # empty = dev mode (no auth)
    debug: bool = False
    
    # Database
    db_path: str = "sgos.db"
    db_busy_timeout: int = 5000
    
    # LLM
    llm_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen-latest-series-invite-beta-v34"
    llm_timeout: int = 60
    llm_max_retries: int = 5
    
    # SSH (3090 server)
    ssh_host: str = "3090-lan"
    searxng_port: int = 8888
    firecrawl_port: int = 3002
    
    # Ingestion
    ingestion_batch_size: int = 30
    ingestion_timeout: int = 30
    
    model_config = SettingsConfigDict(env_prefix="SGOS_", env_file=".env")

settings = Settings()  # Validated at import time — fails fast on bad config
```

**Trade-off:** `pydantic-settings` adds a dependency (~50KB). Worth it for:
- Fail-fast validation at startup (not silent failures mid-request)
- Single source of truth for all config
- Auto-generated `.env.example` from the schema
- Type-safe access everywhere (`settings.llm_timeout` vs `int(os.environ.get("SGOS_LLM_TIMEOUT", "60"))`)

### 3.6 Background Task Queue

**Decision: asyncio tasks, not Celery/RQ.**

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **asyncio BackgroundTasks** | Zero deps, built into FastAPI, simple | No persistence, lost on restart | ✅ Use now |
| **Celery + Redis** | Persistent, distributed, retry policies | Heavy: Redis + worker process + config | ❌ Overkill |
| **ARQ (async Redis Queue)** | Async-native, lightweight | Still needs Redis | ❌ Not needed yet |
| **SQLite job table** | Persistent, no deps, simple | Manual retry logic needed | ⏳ Phase 2 |

**Phase 1 (now):** FastAPI `BackgroundTasks` for ingestion. Jobs are fire-and-forget. If the server restarts, cron jobs re-run ingestion.

**Phase 2 (if needed):** Add a `jobs` table to SQLite:

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,          -- 'ingest_reddit', 'analyze', 'scrape'
    payload TEXT,                -- JSON
    status TEXT DEFAULT 'pending', -- pending | running | done | failed
    result TEXT,                 -- JSON
    error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    retries INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3
);
```

This gives persistence and retry without any external deps.

### 3.7 Structured Logging

**Decision: `structlog` with JSON output.**

```python
import structlog

logger = structlog.get_logger()

# In service layer:
logger.info("ingestion_complete", 
    platform="reddit", 
    posts_added=15, 
    duplicates_skipped=42, 
    duration_s=3.2)

# Output:
# {"event": "ingestion_complete", "platform": "reddit", "posts_added": 15, ...}
```

**Why not `logging` stdlib?** Structured JSON logs are:
- Grep-able by field (`jq 'select(.platform == "reddit")'`)
- Parseable by log aggregators if we ever deploy
- Include request IDs for tracing

**Trade-off:** `structlog` is a dependency. Alternative: use stdlib `logging` with a JSON formatter. Acceptable if we want zero deps, but `structlog`'s context binding is significantly more ergonomic.

### 3.8 API Versioning

**Decision: No versioning for now.**

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **URL prefix (`/v1/`)** | Standard, explicit | Frontend must update on every rename | ❌ Premature |
| **Header versioning** | Clean URLs | Harder to test in browser | ❌ |
| **No versioning** | Simple, co-deployed | Breaking changes require coordinated deploy | ✅ Use now |

**Rationale:** Frontend and backend are deployed together. Breaking changes are coordinated. Adding `/v1/` prefix now adds ceremony without benefit. When the API is consumed by a third client (mobile app, CLI tool), add versioning then.

**Escape hatch:** The router decomposition naturally enables future versioning — `routers/v2/research.py` can coexist with `routers/v1/research.py`.

---

## 4. Data Architecture

### 4.1 SQLite Schema (Current)

```
┌─────────────────────────────────────────────────────────┐
│                     sgos.db                             │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ posts    │    │ creators     │    │ boards       │  │
│  │ ──────── │    │ ──────────── │    │ ──────────── │  │
│  │ id (PK)  │    │ handle (PK)  │    │ id (PK)      │  │
│  │ platform │    │ platform     │    │ name         │  │
│  │ title    │    │ followed_at  │    │ created_at   │  │
│  │ content  │    │ tags         │    └──────┬───────┘  │
│  │ score    │    └──────┬───────┘           │          │
│  │ url      │           │          ┌────────▼───────┐  │
│  │ created  │    ┌──────▼───────┐  │ board_posts    │  │
│  └────┬─────┘    │creator_posts │  │ (junction)     │  │
│       │          └──────────────┘  └────────────────┘  │
│       │                                                 │
│  ┌────▼─────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ posts_fts5   │  │ voice_       │  │ vector_      │  │
│  │ (FTS index)  │  │ profiles     │  │ embeddings   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ alerts       │  │ ideas        │  │ sessions     │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 4.2 Database Strategy

**Decision: Stay on SQLite.**

| Concern | SQLite Reality | Do We Need More? |
|---------|---------------|-----------------|
| **Write throughput** | ~50K writes/sec with WAL | We ingest ~100 posts/day |
| **Read throughput** | Unlimited concurrent readers | 1 user, 1 browser |
| **Data size** | Handles 100GB+ databases | We have ~50MB |
| **Concurrency** | 1 writer, unlimited readers | No concurrent writers |
| **Backup** | Copy the file | Simple, works |
| **Full-text search** | FTS5 built-in, fast | Already using it |

**When to migrate to Postgres:**
- Multiple concurrent writers (team usage)
- Need for real-time subscriptions (LISTEN/NOTIFY)
- Full-text search becomes insufficient (need ranking, faceting)

None of these apply today.

### 4.3 Connection Pooling Strategy (Implemented)

```python
# database.py — Thread-local connection pool
_local = threading.local()

def get_connection() -> sqlite3.Connection:
    conn = getattr(_local, 'connection', None)
    if conn is not None:
        try:
            conn.execute("SELECT 1")  # Health check
            return conn
        except sqlite3.Error:
            _local.connection = None
    
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    _local.connection = conn
    return conn
```

This gives us:
- **Thread safety**: Each thread gets its own connection
- **Connection reuse**: No open/close overhead per query
- **Health checking**: Automatically reconnects on failure
- **WAL mode**: Concurrent reads during writes

---

## 5. Security Architecture

### 5.1 Defense in Depth

```
Layer 1: Network     → localhost-only binding (or firewall)
Layer 2: CORS        → Allow only localhost:3000
Layer 3: CSRF        → Origin header validation on mutations
Layer 4: Auth        → Bearer token (SGOS_API_KEY)
Layer 5: Input       → Pydantic validation, query param bounds
Layer 6: SSRF        → URL scheme/host blocking
Layer 7: Injection   → shlex.quote() on shell commands
Layer 8: Sandbox     → iframe sandbox on rendered HTML
Layer 9: File limits → 100MB upload cap, chunked download
```

### 5.2 Secrets Management

```
Current:  .env file + os.environ.get()  ← scattered, no validation
Target:   pydantic-settings + .env      ← validated at startup

Secrets that should NEVER be in code:
  - SGOS_API_KEY (backend auth)
  - SGOS_LLM_API_KEY (LLM provider)
  - FIRECRAWL_API_KEY (scraping)
  - ZAI_API_KEY (Z.AI search)

Frontend secrets flow:
  localStorage (Settings UI) → request body → API route → LLM provider
  (never stored server-side, never in version control)
```

### 5.3 SSH Command Safety

All SSH commands to the 3090 server go through a validated helper:

```python
def ssh_command(host: str, command: str, timeout: int = 30) -> str:
    """Execute a command on a remote host via SSH. All arguments are shell-quoted."""
    safe_cmd = shlex.quote(command)
    result = subprocess.run(
        ["ssh", host, safe_cmd],
        capture_output=True, text=True, timeout=timeout
    )
    return result.stdout
```

---

## 6. Performance Architecture

### 6.1 Hot Paths and Optimization

| Path | Current | Target | Optimization |
|------|---------|--------|--------------|
| `/search` | FTS5 full scan | FTS5 + LIMIT | Already fast (<50ms) |
| `/search/similar` | O(n) cosine sim | Pre-filter by keyword intersection | 5-10× faster for large datasets |
| `/outliers` | Load all posts, compute Z | Indexed query + batch Z-score | Already acceptable |
| Ingestion | Sequential HTTP | `ThreadPoolExecutor(max_workers=10)` | 5× faster |
| LLM call | 15-60s per request | Streaming SSE | Better UX, no timeout risk |
| Frontend render | Re-render on every keystroke | `React.memo` + debouncing | Already implemented |

### 6.2 Caching Strategy

```
Browser Cache:
  - Static assets (Next.js handles this)
  - Research feed data (stale-while-revalidate, 5min TTL)

Server Cache (future):
  - Trending topics (computed hourly, cached in-memory)
  - Voice profiles (computed once, cached until retrain)
  - Outlier detection results (cached until next ingestion)

NOT caching:
  - Search results (always fresh)
  - LLM responses (always regenerate)
  - Session data (always load from store)
```

### 6.3 Streaming Responses

**Current:** Chat API returns full response after 15-60s.
**Target:** Server-Sent Events (SSE) for progressive rendering.

```python
@app.get("/chat/stream")
async def chat_stream(messages: list[Message], workspace: WorkspaceState):
    async def event_generator():
        async for chunk in llm_service.stream(messages, workspace):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**Trade-off:** SSE adds complexity (connection management, partial response handling). Worth it because:
- Eliminates timeout anxiety on slow LLM responses
- Better UX (user sees text appearing, not a spinner)
- Matches how every major chat UI works

---

## 7. Deployment Architecture

### 7.1 Current (Development)

```
┌─────────────────────────────────┐
│  Mac (development machine)      │
│                                 │
│  ┌─────────────┐ ┌───────────┐ │
│  │ Next.js dev │ │ FastAPI   │ │
│  │ :3000       │ │ :8420     │ │
│  └─────────────┘ └─────┬─────┘ │
│                        │ SSH    │
└────────────────────────┼────────┘
                         │
                  ┌──────▼──────┐
                  │  3090 GPU   │
                  │  Server     │
                  │  ┌────────┐ │
                  │  │SearXNG │ │
                  │  │:8888   │ │
                  │  ├────────┤ │
                  │  │Fire-   │ │
                  │  │crawl   │ │
                  │  │:3002   │ │
                  │  └────────┘ │
                  └─────────────┘
```

### 7.2 Target (Production — when ready)

```
┌───────────────────────────────────────────────┐
│  VPS / Cloud Instance                         │
│                                               │
│  ┌──────────────────────────────────────┐     │
│  │  Caddy (reverse proxy + TLS)         │     │
│  │  :443 → auto-HTTPS via Let's Encrypt │     │
│  └──────────┬───────────────────────────┘     │
│             │                                 │
│  ┌──────────▼───────────────────────────┐     │
│  │  Docker Compose                      │     │
│  │  ┌──────────┐  ┌──────────────────┐  │     │
│  │  │ Next.js  │  │ FastAPI (uvicorn)│  │     │
│  │  │ :3000    │  │ :8420            │  │     │
│  │  └──────────┘  └────────┬─────────┘  │     │
│  │                         │            │     │
│  │  ┌──────────────────────▼──────────┐ │     │
│  │  │ SQLite (volume-mounted)         │ │     │
│  │  │ + automated daily backup        │ │     │
│  │  └─────────────────────────────────┘ │     │
│  └──────────────────────────────────────┘     │
│                                               │
│  Cron: daily ingestion, z-score recalculation │
│                                               │
└───────────────────────────────────────────────┘
```

**Why Caddy over Nginx?** Zero-config HTTPS, simpler syntax, single binary.
**Why Docker over bare metal?** Reproducible deploys, easy rollback, resource limits.
**Why not Kubernetes?** Single user, single server. K8s is 100× overkill.

### 7.3 Backup Strategy

```bash
#!/bin/bash
# Daily SQLite backup — atomic copy of WAL-mode database
BACKUP_DIR="/backups/sgos"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# sqlite3 .backup is safe during writes
sqlite3 /data/sgos.db ".backup '/backups/sgos/sgos_${TIMESTAMP}.db'"

# Keep last 7 days
find $BACKUP_DIR -name "*.db" -mtime +7 -delete
```

---

## 8. Migration Plan

### Phase 1: Foundation (1-2 days)
**Goal:** Config layer + structured logging + router split

1. Add `pydantic-settings` → `config.py`
2. Add `structlog` → replace `print()` calls
3. Split `main.py` into `routers/` modules (move endpoints, keep behavior identical)
4. Add `conftest.py` with test fixtures

**Risk:** Low. Pure refactoring, no behavior changes.
**Verification:** `curl` all endpoints, verify identical responses.

### Phase 2: Service Layer (2-3 days)
**Goal:** Extract business logic from endpoints

1. Create `services/` with one service per domain
2. Create `repositories/` with one repo per table
3. Move SQL from scattered locations into repos
4. Add unit tests for services (mock repos)

**Risk:** Medium. Must preserve all existing behavior.
**Verification:** Run all existing manual test flows.

### Phase 3: Frontend Decomposition (1-2 days)
**Goal:** Break up page.tsx god component

1. Create `hooks/` with `useChatStore`, `useSessionStore`, `useWorkspaceStore`
2. Create `views/` directory, move each view into its own file
3. Create `lib/api.ts` typed client
4. `page.tsx` becomes a thin router (~40 lines)

**Risk:** Low. Component decomposition is well-understood.
**Verification:** Full browser dogfood test of all views.

### Phase 4: Async Ingestion (1 day)
**Goal:** Non-blocking ingestion with progress tracking

1. Add `BackgroundTasks` to ingestion endpoints
2. Add `/ingest/status/{job_id}` endpoint
3. Frontend polls status, shows progress bar

**Risk:** Low. Additive change, existing sync endpoints remain.
**Verification:** Trigger ingestion, verify non-blocking + progress.

### Phase 5: Streaming Chat (2 days)
**Goal:** SSE streaming for LLM responses

1. Add `/chat/stream` endpoint with SSE
2. Frontend `EventSource` consumer in `useChatStore`
3. Progressive rendering in `ChatPanel`

**Risk:** Medium. Must handle partial responses, connection drops.
**Verification:** Send message, verify progressive rendering.

### Phase 6: Docker + Deploy (1 day)
**Goal:** Containerized deployment

1. `Dockerfile` for backend (Python 3.11 slim + deps)
2. `Dockerfile` for frontend (Next.js standalone output)
3. `docker-compose.yml` with Caddy reverse proxy
4. Health check endpoints
5. Backup cron job

**Risk:** Low. Well-documented patterns.
**Verification:** Deploy to VPS, run full test suite.

---

## 9. Alternatives Evaluated

### 9.1 Backend Framework

| Framework | Pros | Cons | Verdict |
|-----------|------|------|---------|
| **FastAPI** (current) | Async, Pydantic, OpenAPI auto-gen, dependency injection | Python ecosystem less mature for some ML tasks | ✅ Keep |
| **Django REST** | Batteries included, admin panel, ORM | Too heavy, synchronous-first, not async-native | ❌ |
| **Litestar** | Faster than FastAPI, better DI | Smaller ecosystem, less documentation | ⏳ Watch |
| **Express/Hono** (Node) | Same language as frontend, fast | Lose Python ML ecosystem (Whisper, numpy, etc.) | ❌ |

### 9.2 Database

| Database | Pros | Cons | Verdict |
|----------|------|------|---------|
| **SQLite** (current) | Zero config, embedded, FTS5 built-in, fast | Single writer, no horizontal scale | ✅ Keep |
| **PostgreSQL** | Full-featured, concurrent writers, extensions | Operational overhead, connection pooling needed | ❌ Not needed |
| **DuckDB** | Analytical queries, columnar | Not designed for OLTP, no FTS | ❌ Wrong tool |
| **Turso (libSQL)** | SQLite-compatible, edge replication, serverless | External dependency, cost | ⏳ Phase 2 option |

### 9.3 Frontend Framework

| Framework | Pros | Cons | Verdict |
|-----------|------|------|---------|
| **Next.js** (current) | SSR, API routes, file routing, React ecosystem | Heavy for SPA, Turbopack quirks | ✅ Keep |
| **Astro** | Lighter, islands architecture | Less mature for complex SPAs | ❌ |
| **SvelteKit** | Smaller bundles, simpler DX | Smaller ecosystem, learning curve | ❌ Sunk cost |
| **Tauri + Leptos** | Native app, Rust performance | Massive rewrite, Rust learning curve | ❌ |

### 9.4 LLM Provider

| Provider | Pros | Cons | Verdict |
|----------|------|------|---------|
| **Aliyun/Qwen** (current) | Cheap, good quality, intl endpoint | Rate limits, occasional downtime | ✅ Primary |
| **OpenAI** | Best quality, most reliable | Expensive ($$$ per token) | ❌ Budget |
| **Anthropic** | Best at long-form, safety | Expensive, slower | ❌ Budget |
| **Local (llama.cpp)** | Free, private, no rate limits | Needs GPU, lower quality | ⏳ Phase 2 |
| **Groq** | Fastest inference | Limited models, rate limits | ⏳ Backup |

### 9.5 State Management

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **useReducer + hooks** (target) | Zero deps, testable, simple | Manual boilerplate, no devtools | ✅ Use |
| **Zustand** | Minimal API, devtools, middleware | New dependency, learning curve | ⏳ If grows |
| **Redux Toolkit** | Full-featured, time-travel debug | Massive boilerplate, overkill | ❌ |
| **Jotai** | Atomic, fine-grained reactivity | Mental model shift, less predictable | ❌ |
| **Context + useState** (current) | Simple, built-in | Prop drilling, stale closures, re-render storms | ❌ Being replaced |

---

## 10. Key Decisions Log

| # | Decision | Date | Rationale |
|---|----------|------|-----------|
| D1 | Stay on SQLite | 2026-06 | Single user, WAL handles our load, zero ops overhead |
| D2 | Thread-local connection pool | 2026-06 | Avoids per-query connection overhead, thread-safe |
| D3 | FastAPI BackgroundTasks over Celery | 2026-06 | Zero deps, cron handles retry, complexity isn't justified |
| D4 | useReducer over Zustand | 2026-06 | Zero deps for single-user app, easy migration path if needed |
| D5 | pydantic-settings over manual env parsing | 2026-06 | Fail-fast validation, single source of truth |
| D6 | Origin-based CSRF over token-based | 2026-06 | Simpler, works with Bearer auth, sufficient for same-origin SPA |
| D7 | No API versioning | 2026-06 | Co-deployed frontend/backend, no third-party consumers yet |
| D8 | SSE streaming over WebSocket | 2026-06 | Simpler (HTTP-based), one-directional (server→client), built-in reconnect |
| D9 | Caddy over Nginx | 2026-06 | Zero-config HTTPS, simpler config, single binary |
| D10 | Docker Compose over Kubernetes | 2026-06 | Single server, single user, K8s is 100× overkill |

---

## 11. Future Requirements (Roadmap)

| Feature | Timeline | Architecture Impact |
|---------|----------|---------------------|
| **Multi-device sync** | Q3 2026 | Server-side sessions, WebSocket for live updates |
| **Scheduled content** | Q3 2026 | Job queue with cron scheduling, publish-at-time |
| **Analytics dashboard** | Q4 2026 | Chart.js/D3 views, time-series aggregation queries |
| **Local LLM fallback** | Q4 2026 | llama.cpp integration, model management, GPU scheduling |
| **Browser extension** | 2027 | API versioning required, OAuth2 flow, CORS expansion |
| **Team/multi-user** | 2027 | PostgreSQL migration, RBAC, per-user data isolation |
| **Mobile app** | 2027 | API versioning, push notifications, offline sync |

### When to Migrate Off SQLite

Migrate to PostgreSQL when ANY of these become true:
- Multiple concurrent writers (team usage)
- Database exceeds 10GB
- Need real-time subscriptions (LISTEN/NOTIFY)
- Need full-text search with ranking/faceting beyond FTS5
- Need geographic replication

### When to Add a Message Queue

Add Redis + ARQ when ANY of these become true:
- Ingestion jobs take >5 minutes and need progress tracking
- Need guaranteed delivery (not just cron retry)
- Need job prioritization (user-triggered > scheduled)
- Need distributed workers (multiple servers)

---

## 12. File Structure (Target)

```
sgos-backend/
├── config.py              ← pydantic-settings (all config)
├── main.py                ← App factory, middleware stack, lifespan (~80 lines)
├── database.py            ← Connection pool, migrations
├── routers/
│   ├── research.py        ← /outliers, /trends, /brief, /stats
│   ├── search.py          ← /search, /search/hybrid, /search/similar
│   ├── content.py         ← /repurpose, /ideas
│   ├── ingestion.py       ← /ingest, /ingest/youtube, /ingest/topics
│   ├── voice.py           ← /voice/*, /voices
│   ├── creators.py        ← /creators/*
│   ├── boards.py          ← /boards/*
│   ├── alerts.py          ← /alerts/*
│   ├── media.py           ← /transcribe/*
│   └── analytics.py       ← /analyze
├── services/
│   ├── research.py        ← Outlier detection, trend analysis
│   ├── content.py         ← LLM generation, repurposing
│   ├── ingestion.py       ← Reddit/HN/YT/Twitter pipelines
│   ├── voice.py           ← Profile extraction, training
│   ├── search.py          ← Hybrid search orchestration
│   ├── creators.py        ← Creator tracking, stats
│   └── alerts.py          ← Alert generation, deduplication
├── repositories/
│   ├── posts.py           ← Post CRUD, FTS queries
│   ├── boards.py          ← Board + junction table ops
│   ├── creators.py        ← Creator + creator_post ops
│   ├── voice.py           ← Voice profile storage
│   ├── vectors.py         ← TF-IDF index operations
│   └── alerts.py          ← Alert CRUD
├── models/
│   ├── requests.py        ← Pydantic request schemas
│   ├── responses.py       ← Pydantic response schemas
│   └── domain.py          ← Domain entities (Post, Board, Creator)
├── middleware/
│   ├── auth.py            ← Bearer token auth
│   ├── csrf.py            ← Origin validation
│   ├── logging.py         ← Request ID + structlog
│   └── rate_limit.py      ← Per-endpoint rate limiting
├── utils/
│   ├── ssh.py             ← Safe SSH command helper
│   ├── llm.py             ← LLM client (retry, timeout)
│   └── scoring.py         ← Z-score, viral score computation
├── tests/
│   ├── conftest.py        ← Fixtures, test DB
│   ├── test_research.py
│   ├── test_search.py
│   └── test_ingestion.py
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── pyproject.toml

StraughterG-os/
├── app/
│   ├── layout.tsx         ← Providers, persistent shell
│   ├── page.tsx           ← Thin router (~40 lines)
│   └── api/
│       └── chat/
│           └── route.ts   ← LLM proxy
├── views/
│   ├── ChatView.tsx
│   ├── ResearchView.tsx
│   ├── SearchView.tsx
│   ├── HistoryView.tsx
│   ├── CreatorView.tsx
│   ├── SettingsView.tsx
│   ├── BoardsView.tsx
│   ├── ProjectsView.tsx
│   ├── VoiceView.tsx
│   └── TranscribeView.tsx
├── components/
│   ├── ChatPanel.tsx
│   ├── NavSidebar.tsx
│   ├── WorkspaceSidebar.tsx
│   └── ... (reusable UI components)
├── hooks/
│   ├── useChatStore.ts    ← useReducer for chat state
│   ├── useSessionStore.ts ← Session management
│   ├── useWorkspaceStore.ts
│   └── useNavStore.ts
├── lib/
│   ├── api.ts             ← Typed API client
│   ├── types.ts           ← Shared TypeScript types
│   ├── research.ts        ← Z.AI MCP search
│   ├── systemPrompt.ts    ← Prompt builder
│   └── sessionStore.ts    ← localStorage persistence
├── Dockerfile
└── package.json
```

---

## 13. Monitoring and Observability

### 13.1 Health Checks

```python
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": settings.version,
        "database": {
            "posts": post_count,
            "size_mb": db_size_mb,
            "wal_checkpoint": wal_status,
        },
        "uptime_s": time.time() - start_time,
        "llm": "reachable" if llm_ping() else "unreachable",
    }
```

### 13.2 Metrics (Future)

When deploying to production, add Prometheus metrics:

```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

Tracked metrics:
- Request latency (p50, p95, p99) by endpoint
- Error rate by endpoint and status code
- LLM call duration and token usage
- Ingestion job duration and success rate
- Database query duration
- Active connections

### 13.3 Alerting

Current: Cron job checks for outliers and sends alerts via the `/alerts` endpoint.
Future: Add webhook notifications (Telegram, email) for critical alerts.

---

## 14. Migration Status

### ✅ Phase 1: Foundation — COMPLETED 2026-06-24

| Change | Before | After |
|--------|--------|-------|
| `main.py` | 1,164 LOC god-component | ~95 LOC app factory |
| Config | `os.environ.get()` scattered across 12+ files | `config.py` (pydantic-settings, `SGOS_` prefix) |
| Routing | 53 endpoints in one file | 11 domain routers in `routers/` |
| Endpoints verified | — | All 53 via live server (health, search, outliers, ideas, voices, creators, boards, transcribe) |

**New files created:**
- `config.py` — centralized settings (pydantic-settings v2.14)
- `routers/__init__.py`
- `routers/research.py` — `/health`, `/outliers`, `/trends`, `/stats`, `/brief`
- `routers/search.py` — `/search`, `/search/hybrid`, `/search/build-index`, `/search/similar`, `/search/related/{id}`
- `routers/ingestion.py` — `/ingest`, `/ingest/sync`, `/ingest/posts`, `/ingest/youtube`, `/ingest/topics`, `/ingest/search`
- `routers/voice.py` — `/voice/build`, `/voice/build-from-text`, `/voice/{name}`, `/voice/{name}/prompt`, `/voices`, `/analyze`
- `routers/creators.py` — `/creators/follow`, `/creators/unfollow`, `/creators`, `/creators/{handle}/posts`, `/creators/stats`, `/creators/discover`
- `routers/alerts.py` — `/alerts`, `/alerts/{id}/read`, `/alerts/outliers/check`, `/alerts/history`
- `routers/boards.py` — `/boards` (CRUD), `/boards/{id}/posts` (save/unsave)
- `routers/content.py` — `/repurpose`, `/repurpose/ai`, `/ideas/generate`, `/ideas`, `/carousel/*`, `/analytics/score/{id}`
- `routers/scrape.py` — `/scrape`, `/outliers/{id}/deep-scrape`, `/outliers/deep-scrape`
- `routers/media.py` — `/transcribe/status`, `/transcribe`, `/transcribe/url`
- `routers/analytics.py` — `/analytics/explain/{id}`, `/analytics/patterns`

### Phase 2: Service Layer — ✅ COMPLETED 2026-06-24

| Change | Before | After |
|--------|--------|-------|
| Business logic | Inline in 11 router files | Separated into `services/` layer |
| DB access | Direct `get_connection()` calls in routers | `repositories/posts.py` typed access |
| Data models | Raw dicts everywhere | `models/domain.py`, `requests.py`, `responses.py` |

**New files:**
- `models/__init__.py`
- `models/domain.py` — `Post`, `Creator`, `Board`, `VoiceProfile`, `OutlierAlert`
- `models/requests.py` — `SearchRequest`, `IngestPostRequest`, `VoiceBuildRequest`, etc.
- `models/responses.py` — `HealthResponse`, `SearchResponse`, `OutliersResponse`, `TrendsResponse`
- `repositories/__init__.py`
- `repositories/posts.py` — `PostRepository` with typed query methods
- `services/__init__.py`
- `services/research.py` — `ResearchService` (brief generation, outlier/trend queries)
- `services/content.py` — `ContentService` (repurpose prompts, scoring, idea generation)
- `services/ingestion.py` — `IngestionService` + `IngestionProgress` tracker

### Phase 3: Frontend Decomposition — ✅ COMPLETED 2026-06-24

| Change | Before | After |
|--------|--------|-------|
| `page.tsx` | 682 LOC god-component | ~170 LOC slim orchestrator |
| State management | 15+ `useState` hooks inline | `useSessionStore` + `useChatStore` |
| Components | `NavErrorBoundary` + `HomeView` inline | Extracted to `components/` |

**New files:**
- `hooks/useSessionStore.ts` — session CRUD, workspace state, persistence
- `hooks/useChatStore.ts` — messages, send/retry, files, templates, error handling
- `components/NavErrorBoundary.tsx` — React error boundary
- `components/HomeView.tsx` — dashboard cards + quick start

### Phase 4: Async Ingestion — ✅ COMPLETED 2026-06-24

| Change | Before | After |
|--------|--------|-------|
| Ingestion | Fire-and-forget `threading.Thread` | `IngestionService` with progress tracking |
| Status | None — had to check `/health` | `GET /ingest/status/{job_id}` + `GET /ingest/jobs` |

### Phase 5: SSE Streaming — ✅ COMPLETED 2026-06-24

| Change | Before | After |
|--------|--------|-------|
| Chat | Single JSON response (blocks until complete) | `POST /chat/stream` SSE — token-by-token delivery |

**New files:**
- `routers/chat.py` — SSE endpoint with system prompt builder for platform/tone/length

### Phase 6: Deployment — ✅ COMPLETED 2026-06-24

**New files:**
- `sgos-backend/Dockerfile` — Python 3.11-slim + ffmpeg + uv + health check
- `StraughterG-os/Dockerfile` — Node 20 multi-stage (deps → build → standalone)
- `docker-compose.yml` — 3 services: backend + frontend + Caddy
- `Caddyfile` — reverse proxy with gzip, security headers, auto-HTTPS
- `.env.example` — all configurable env vars documented
- `.dockerignore` — exclude node_modules, .next, __pycache__, .venv

---

*This document is a living reference. Update it as the system evolves.*
