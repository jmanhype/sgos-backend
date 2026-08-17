# SGOS Auto-Repurpose System Architecture

**Status:** Draft  
**Last Updated:** 2026-07-06  
**Author:** Hermes Agent  
**Reviewers:** @StraughterG

---

## Executive Summary

The current `ecomchigga_repurpose.py` script works when everything goes right but fails silently at every layer. This document proposes a restructured architecture that separates concerns, adds observability, and handles failures gracefully.

**Core Problem:** The system conflates scraping, generation, verification, and posting into a single monolithic script with no error boundaries or retry logic.

**Proposed Solution:** Modular pipeline with explicit stages, failure modes, and recovery strategies.

---

## Current System (As-Is)

### Flow
```
[Single Script: ecomchigga_repurpose.py]
  ├─ Scrape tweets (bird CLI)
  ├─ Group threads (conversationId)
  ├─ Fetch full threads (bird thread)
  ├─ Score & pick top content
  ├─ Extract topics (regex)
  ├─ Search SearXNG (localhost:4004)
  ├─ Search Firecrawl (localhost:3005)
  ├─ Generate thread (LLM)
  ├─ Verify grounding (LLM → JSON parse → fallback)
  ├─ Post via ego-browser (CDP)
  └─ Write rate limit file
```

### Problems
1. **No error boundaries** — CDP hang in posting blocks entire pipeline
2. **No retry logic** — JSON parse failures get rubber-stamped
3. **No observability** — Silent failures, no alerting
4. **Tight coupling** — Scraping, generation, posting all in one script
5. **No deduplication** — Same content posted repeatedly
6. **CDP session management** — ego-browser hangs after 2-3 replies
7. **Naive grounding** — Search queries like "month since since built"
8. **Character counting** — LLM can't reliably count, no post-processing

### Failure Modes (Current)
| Stage | Failure | Result |
|-------|---------|--------|
| Scraping | bird CLI error | Silent, no tweets scraped |
| Thread fetch | CDP hang | Script hangs forever |
| Generation | LLM timeout | Silent failure |
| Verification | JSON parse error | Fallback: 85/100 (rubber stamp) |
| Posting | CDP hang | Partial thread posted, script hangs |
| Rate limit | File locked | Race condition |

---

## Proposed Architecture (To-Be)

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│                    Scheduler (cron/cronjob)                   │
│                   Triggers: daily 10:00 AM                    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Orchestrator (Python)                      │
│  - Coordinates stages                                         │
│  - Handles retries/timeouts                                   │
│  - Writes audit log                                           │
│  - Sends alerts on failure                                    │
└────────────────────────┬────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
   [Scraping]      [Generation]      [Posting]
   (Stage 1)       (Stage 2)         (Stage 3)
```

### Stage 1: Content Scraping
**Responsibility:** Fetch tweets from target accounts, detect threads, fetch full content.

**Inputs:**
- Target handles list (`TARGET_HANDLES`)
- Max tweets per account (`MAX_TWEETS_TO_SCRAPE`)
- Posted content registry (SQLite)

**Outputs:**
- List of candidate threads/posts
- Metadata (engagement, timestamps, char count)

**Failure Handling:**
- bird CLI error → retry 3x with 10s backoff
- CDP hang → kill subprocess, retry with fresh session
- No content found → skip account, continue

**Key Decisions:**
1. **SQLite registry** for deduplication (vs. JSON file)
   - *Why:* Atomic writes, queryable, handles concurrent access
   - *Trade-off:* Slightly more complex than JSON
   - *Alternative:* Redis (overkill for single-machine)

2. **Per-account scraping** with timeout (vs. global timeout)
   - *Why:* One slow account shouldn't block others
   - *Trade-off:* More complex error handling
   - *Alternative:* Parallel scraping (race conditions with bird CLI)

3. **Thread expansion** with `bird thread` (vs. manual reply scraping)
   - *Why:* Captures full context, handles edge cases
   - *Trade-off:* Slower, CDP dependency
   - *Alternative:* Follow conversationId tree (fragile)

---

### Stage 2: Content Generation
**Responsibility:** Pick best content, ground with real context, generate repurposed version, verify quality.

**Inputs:**
- Candidate threads from Stage 1
- Grounding context (SearXNG + Firecrawl)
- Voice profile (style guide)
- Posted content registry

**Outputs:**
- Generated thread (list of tweets)
- Grounding verification report
- Quality score

**Failure Handling:**
- LLM timeout → retry 2x, then skip
- JSON parse error → retry with stricter prompt, then skip
- Grounding score < 70 → skip, try next candidate
- All candidates fail → alert, skip run

**Key Decisions:**
1. **Multi-candidate generation** (vs. single top pick)
   - *Why:* If top pick fails verification, try next
   - *Trade-off:* More LLM calls, higher cost
   - *Alternative:* Force-post with warning (risk of bad content)

2. **Stricter verification prompt** (vs. lenient fallback)
   - *Why:* Current fallback rubber-stamps hallucinations
   - *Trade-off:* More content skipped, lower throughput
   - *Alternative:* Human-in-the-loop (not autonomous)

3. **Post-processing character enforcement** (vs. trusting LLM)
   - *Why:* LLM can't reliably count characters
   - *Trade-off:* May need to split/merge tweets
   - *Alternative:* Accept 270-290 char range (looser constraint)

4. **Better grounding queries** (vs. naive topic extraction)
   - *Why:* "month since since built" doesn't find relevant context
   - *Trade-off:* More complex query extraction logic
   - *Alternative:* Skip grounding (lose factual accuracy)

**Grounding Query Strategy:**
```
Current: Extract topics → search "month since since built"
Proposed: 
  1. Extract key entities (tools, numbers, concepts)
  2. Generate 3-5 search queries per entity
  3. Deduplicate and rank by relevance
  4. Fetch top 5 sources
  5. Extract key facts (dates, stats, quotes)
```

---

### Stage 3: Content Posting
**Responsibility:** Post thread via ego-browser, handle CDP session management, track results.

**Inputs:**
- Generated thread from Stage 2
- Posting credentials (ego-browser)
- Rate limit state

**Outputs:**
- Posted tweet IDs
- Posting success/failure status

**Failure Handling:**
- CDP hang → kill, refresh session, retry from last successful tweet
- Post button disabled → wait, retry 3x, then abort
- Rate limit hit → wait, retry next cycle
- Partial thread posted → log, alert, don't retry (avoid duplicates)

**Key Decisions:**
1. **CDP session refresh every 2 tweets** (vs. single session)
   - *Why:* Prevents hangs on reply 3+
   - *Trade-off:* Slower posting, more overhead
   - *Alternative:* Keep session open (current approach, fails)

2. **Idempotent posting** with tweet ID tracking (vs. fire-and-forget)
   - *Why:* Can detect partial posts, avoid duplicates
   - *Trade-off:* More state management
   - *Alternative:* Accept partial posts (current approach)

3. **Staggered posting** with 20s delays (vs. immediate)
   - *Why:* Mimics human behavior, avoids rate limits
   - *Trade-off:* Slower (2 min for 7-tweet thread)
   - *Alternative:* Faster posting (risk of rate limit)

4. **ego-browser task space isolation** (vs. shared)
   - *Why:* Prevents collisions with manual posting
   - *Trade-off:* More task spaces, cleanup needed
   - *Alternative:* Global lock (blocks manual posting)

---

### Stage 4: Observability (New)
**Responsibility:** Log all stages, alert on failures, track metrics.

**Inputs:**
- Stage outputs
- Error states
- Performance metrics

**Outputs:**
- Audit log (JSON)
- Alerts (email/Slack/Telegram)
- Metrics dashboard

**Implementation:**
```python
# Audit log structure
{
  "run_id": "2026-07-06-10-00-00",
  "timestamp": "2026-07-06T10:00:00Z",
  "stages": {
    "scraping": {
      "status": "success",
      "accounts_scraped": 2,
      "tweets_scraped": 30,
      "threads_detected": 12,
      "duration_seconds": 45
    },
    "generation": {
      "status": "success",
      "candidates_tried": 3,
      "grounding_sources": 4,
      "verification_score": 85,
      "duration_seconds": 30
    },
    "posting": {
      "status": "partial",
      "tweets_posted": 3,
      "tweets_failed": 4,
      "tweet_ids": ["123", "124", "125"],
      "failure_reason": "CDP hang on tweet 4",
      "duration_seconds": 120
    }
  },
  "errors": [
    {
      "stage": "posting",
      "error": "CDP timeout",
      "retry_count": 2,
      "resolved": false
    }
  ],
  "alert_sent": true
}
```

**Alerting Strategy:**
- **Critical:** No content posted (all stages failed)
- **Warning:** Partial thread posted, verification failures
- **Info:** Successful run, metrics

**Delivery:**
- Telegram bot (already configured)
- Email (optional)
- Log file (always)

---

## Data Model

### SQLite Schema

```sql
-- Posted content registry
CREATE TABLE posted_content (
    id INTEGER PRIMARY KEY,
    source_handle TEXT NOT NULL,
    source_tweet_id TEXT NOT NULL,
    source_conversation_id TEXT,
    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tweet_ids TEXT,  -- JSON array of posted tweet IDs
    thread_length INTEGER,
    grounding_score INTEGER,
    status TEXT CHECK(status IN ('success', 'partial', 'failed'))
);

CREATE INDEX idx_source_tweet ON posted_content(source_tweet_id);
CREATE INDEX idx_posted_at ON posted_content(posted_at);

-- Grounding cache
CREATE TABLE grounding_cache (
    id INTEGER PRIMARY KEY,
    query TEXT NOT NULL,
    sources TEXT,  -- JSON array of source URLs
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ttl_hours INTEGER DEFAULT 24
);

CREATE INDEX idx_query ON grounding_cache(query);
```

**Why SQLite:**
- Single file, no server needed
- Atomic writes, handles concurrent access
- Queryable (find duplicates, recent posts)
- Backup is just file copy

**Why not JSON:**
- No atomic writes (race conditions)
- Can't query efficiently (load entire file)
- Gets slow with 1000+ entries

---

## Scalability Considerations

### Current Limits
- **Accounts:** 2 (hardcoded)
- **Posts per day:** 1 (rate limit)
- **Thread length:** 7 tweets (arbitrary)
- **Grounding sources:** 5 (SearXNG + Firecrawl)

### Scaling Up
**More accounts:**
- Current: Add to `TARGET_HANDLES` list
- Bottleneck: Sequential scraping (30s per account)
- Solution: Parallel scraping with asyncio (future)

**More posts per day:**
- Current: 24-hour rate limit
- Bottleneck: ego-browser session management
- Solution: Multiple ego-browser profiles (future)

**Longer threads:**
- Current: 7 tweets max
- Bottleneck: CDP hang on reply 3+
- Solution: Session refresh every 2 tweets (implemented in Stage 3)

**Better grounding:**
- Current: 5 sources max
- Bottleneck: SearXNG/Firecrawl rate limits
- Solution: Cache grounding results for 24h (Stage 2)

---

## Extensibility

### Adding New Features

**1. A/B Testing Voice Styles**
```
Current: Single voice profile
Future: 
  - Generate 2-3 versions per thread
  - Post to different accounts
  - Track engagement
  - Pick winner
```

**2. Multi-Platform Posting**
```
Current: X/Twitter only
Future:
  - LinkedIn (long-form)
  - Reddit (thread → post)
  - Newsletter (thread → email)
```

**3. Content Calendar**
```
Current: Post best content immediately
Future:
  - Schedule posts for optimal times
  - Balance content types (threads vs. long posts)
  - Avoid posting same topic twice in a week
```

**4. Engagement Tracking**
```
Current: No tracking
Future:
  - Scrape engagement metrics after 24h
  - Correlate with content type, time, account
  - Optimize posting strategy
```

---

## Trade-Offs

### Decisions Made

| Decision | Trade-Off | Rationale |
|----------|-----------|-----------|
| SQLite over JSON | Slightly more complex | Atomic writes, queryable |
| Multi-candidate generation | Higher LLM cost | Better content quality |
| CDP refresh every 2 tweets | Slower posting | Prevents hangs |
| Stricter verification | Lower throughput | Prevents hallucinations |
| Staggered posting (20s delays) | 2 min per thread | Mimics human behavior |
| Per-account timeouts | Complex error handling | One slow account doesn't block |

### Decisions Deferred

| Decision | Why Deferred | When to Revisit |
|----------|--------------|-----------------|
| Parallel scraping | bird CLI not thread-safe | If >5 accounts |
| Multiple ego-browser profiles | Session management complexity | If >2 posts/day |
| Human-in-the-loop | Breaks autonomy | If quality issues persist |
| External LLM (OpenAI/Anthropic) | Cost, latency | If local LLM quality insufficient |

---

## Implementation Plan

### Phase 1: Foundation (Week 1)
1. **SQLite registry** for deduplication
2. **Audit logging** with JSON output
3. **CDP session refresh** every 2 tweets
4. **Stricter verification** with retry logic

**Deliverable:** Reliable single-thread posting with observability

### Phase 2: Robustness (Week 2)
1. **Multi-candidate generation** (try 3, pick best)
2. **Better grounding queries** (entity extraction)
3. **Alerting** on failures (Telegram)
4. **Post-processing** character enforcement

**Deliverable:** High-quality content with failure recovery

### Phase 3: Scale (Week 3)
1. **Expand target accounts** (add 5-10)
2. **Optimize posting schedule** (best times)
3. **Grounding cache** (24h TTL)
4. **Metrics dashboard** (success rate, engagement)

**Deliverable:** Scalable system with monitoring

### Phase 4: Intelligence (Week 4+)
1. **A/B testing** voice styles
2. **Engagement tracking** (24h metrics)
3. **Content calendar** (scheduling)
4. **Multi-platform** (LinkedIn, Reddit)

**Deliverable:** Autonomous content optimization

---

## Alternatives Evaluated

### Alternative 1: n8n Workflow
**Approach:** Use existing n8n instance (ZimaBoard, port 5678) to orchestrate stages.

**Pros:**
- Visual workflow builder
- Built-in retry/error handling
- HTTP nodes for SearXNG/Firecrawl
- Webhook triggers

**Cons:**
- Can't run Python scripts easily
- No native bird CLI integration
- Can't manage ego-browser CDP sessions
- Overkill for single-machine setup

**Decision:** Rejected. Python orchestrator is simpler and more flexible.

---

### Alternative 2: Queue-Based Architecture (Redis + Celery)
**Approach:** Each stage is a Celery task, Redis as message broker.

**Pros:**
- True async processing
- Built-in retry/rate limiting
- Horizontal scaling
- Distributed workers

**Cons:**
- Requires Redis server
- Celery complexity (broker, workers, monitoring)
- Overkill for single-machine, 2 accounts
- Debugging is harder (distributed traces)

**Decision:** Rejected. Overengineered for current scale. Revisit if >10 accounts.

---

### Alternative 3: Microservices (FastAPI + Docker)
**Approach:** Each stage is a FastAPI service, communicate via HTTP.

**Pros:**
- Clean separation of concerns
- Independent scaling
- API-first design
- Easy to add new stages

**Cons:**
- Docker orchestration complexity
- Network overhead (localhost HTTP calls)
- Harder to debug (multiple containers)
- Overkill for monolithic script replacement

**Decision:** Rejected. Monolithic orchestrator with modular functions is simpler.

---

### Alternative 4: Serverless (AWS Lambda / Cloudflare Workers)
**Approach:** Each stage is a serverless function.

**Pros:**
- No server management
- Auto-scaling
- Pay-per-use

**Cons:**
- Can't run bird CLI (requires Chrome)
- Can't run ego-browser (requires persistent CDP)
- Cold start latency
- Vendor lock-in

**Decision:** Rejected. Requires persistent browser sessions, not serverless.

---

## Risk Assessment

### High Risk
1. **ego-browser instability** — CDP hangs, session management
   - *Mitigation:* Session refresh, timeout handling, alerting
   - *Contingency:* Manual posting if automation fails

2. **LLM hallucinations** — Grounding verification failures
   - *Mitigation:* Stricter verification, multi-candidate generation
   - *Contingency:* Human review for low-confidence content

3. **Rate limiting** — X/Twitter blocks account
   - *Mitigation:* Conservative rate limits, staggered posting
   - *Contingency:* Reduce posting frequency, rotate accounts

### Medium Risk
1. **bird CLI breaking changes** — API updates, auth issues
   - *Mitigation:* Version pinning, error handling
   - *Contingency:* Fallback to manual scraping

2. **SearXNG/Firecrawl downtime** — Grounding fails
   - *Mitigation:* Retry logic, graceful degradation
   - *Contingency:* Skip grounding, post with warning

3. **Content quality degradation** — LLM generates poor content
   - *Mitigation:* Quality scoring, human review
   - *Contingency:* Revert to manual content creation

### Low Risk
1. **SQLite corruption** — Database file locked/corrupted
   - *Mitigation:* WAL mode, regular backups
   - *Contingency:* Rebuild from X/Twitter history

2. **Cron job failure** — Scheduler doesn't trigger
   - *Mitigation:* Multiple schedulers (cron + cronjob)
   - *Contingency:* Manual trigger

---

## Success Metrics

### Phase 1 (Foundation)
- ✅ All stages complete without hanging
- ✅ Audit log captures all runs
- ✅ No duplicate posts
- ✅ CDP hangs reduced by 80%

### Phase 2 (Robustness)
- ✅ Grounding verification score > 80 (real, not fallback)
- ✅ 90% of runs post complete threads
- ✅ Alerts sent within 5 min of failure
- ✅ Character count enforcement (280-380) accurate

### Phase 3 (Scale)
- ✅ 5+ target accounts
- ✅ 3+ posts per week
- ✅ Grounding cache hit rate > 50%
- ✅ Dashboard shows 7-day trends

### Phase 4 (Intelligence)
- ✅ A/B tests running (2+ voice styles)
- ✅ Engagement tracking (likes, replies, bookmarks)
- ✅ Content calendar (scheduled posts)
- ✅ Multi-platform (X + LinkedIn)

---

## Conclusion

The current system works when everything goes right but is fragile at every layer. The proposed architecture separates concerns, adds observability, and handles failures gracefully.

**Key Improvements:**
1. **Modular stages** with explicit error boundaries
2. **SQLite registry** for deduplication and audit trail
3. **CDP session refresh** to prevent hangs
4. **Stricter verification** to prevent hallucinations
5. **Alerting** for failures
6. **Multi-candidate generation** for better quality

**Implementation:** 4 phases, starting with foundation (reliability), then robustness (quality), then scale (volume), then intelligence (optimization).

**Next Steps:**
1. Review this architecture document
2. Approve Phase 1 implementation
3. Start with SQLite registry and audit logging
4. Iterate based on real-world failures

---

## Appendix: File Structure (Proposed)

```
sgos-backend/
├── scripts/
│   ├── ecomchigga_repurpose.py          # Current monolithic script (deprecated)
│   ├── repurpose/                       # New modular system
│   │   ├── __init__.py
│   │   ├── orchestrator.py              # Coordinates stages
│   │   ├── scraping.py                  # Stage 1: Content scraping
│   │   ├── generation.py                # Stage 2: Content generation
│   │   ├── posting.py                   # Stage 3: Content posting
│   │   ├── observability.py             # Stage 4: Logging/alerting
│   │   ├── database.py                  # SQLite registry
│   │   ├── grounding.py                 # SearXNG/Firecrawl integration
│   │   ├── verification.py              # Grounding verification
│   │   └── config.py                    # Configuration
│   └── ...
├── data/
│   ├── repurpose.db                     # SQLite database
│   ├── audit_logs/                      # JSON audit logs
│   │   └── 2026-07-06-10-00-00.json
│   └── grounding_cache/                 # Cached grounding results
└── tests/
    ├── test_scraping.py
    ├── test_generation.py
    ├── test_posting.py
    └── test_orchestrator.py
```

---

**Document Version:** 1.0  
**Next Review:** After Phase 1 implementation
