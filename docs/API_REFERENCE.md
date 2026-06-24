# API Reference

Base URL: `http://localhost:8420`
Interactive docs: `http://localhost:8420/docs` (Swagger UI)

## Authentication

If `SGOS_API_KEY` is set, include a Bearer token:

```bash
curl -H "Authorization: Bearer your-api-key" http://localhost:8420/health
```

If `SGOS_API_KEY` is empty (dev mode), no auth is required.

---

## Pipeline — Autonomous Viral Content Pipeline

The pipeline detects viral outliers, extracts their "DNA," generates content variants, scores them, and stores ranked opportunities for you to review and publish.

### Run the Pipeline

```
POST /pipeline/run
```

Runs the full pipeline: detect outliers → extract genomes → generate variants → score → store.

**Parameters (query):**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `hours` | int | `24` | Lookback window for outlier detection (1–168) |
| `limit` | int | `10` | Max outliers to process (1–50) |
| `num_variants` | int | `3` | Variants to generate per genome (1–10) |
| `platform` | str | `"reddit"` | Platform filter (`reddit`, `hackernews`, `twitter`) |
| `voice_prompt` | str | `""` | Voice/style guide for generation |

**Example:**

```bash
curl -X POST "http://localhost:8420/pipeline/run?hours=48&num_variants=5&platform=reddit"
```

**Response:**

```json
{
  "started_at": "2026-06-24T14:00:00Z",
  "outliers_processed": 10,
  "genomes_extracted": 8,
  "variants_generated": 40,
  "opportunities_created": 38,
  "alerts_sent": 1,
  "errors": [],
  "completed_at": "2026-06-24T14:01:23Z"
}
```

**Edge cases:**
- `skip_existing=True` (default): genomes already extracted are skipped
- If LLM is unavailable, genome extraction falls back to rule-based heuristics
- Duplicate variants (same content hash) are silently skipped (`save_opportunity` returns `-1`)
- High-scoring opportunities (≥75) trigger Telegram alerts automatically

---

### List Opportunities

```
GET /pipeline/opportunities
```

Get ranked content opportunities sorted by score (highest first).

**Parameters (query):**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | `10` | Max results (1–50) |
| `unseen_only` | bool | `true` | Only return unviewed, non-dismissed opportunities |

**Example:**

```bash
curl "http://localhost:8420/pipeline/opportunities?limit=5"
```

**Response:**

```json
{
  "opportunities": [
    {
      "id": 42,
      "genome_id": "abc123",
      "variant_type": "thread",
      "title": "5 Reasons Your AI Strategy Is Failing",
      "content": "1/5 Here's what most founders miss about AI...",
      "score": 87.3,
      "score_breakdown": {"engagement": {"raw": 90, "weight": 0.35, "weighted": 31.5}, ...},
      "hook": "5 Reasons Your AI Strategy Is Failing",
      "hook_type": "list",
      "structural_pattern": "listicle",
      "genome_engagement": 0.85,
      "viewed": 0,
      "dismissed": 0,
      "created_at": "2026-06-24T14:00:00Z"
    }
  ]
}
```

---

### Mark as Viewed

```
POST /pipeline/opportunities/{opportunity_id}/view
```

**Example:**

```bash
curl -X POST "http://localhost:8420/pipeline/opportunities/42/view"
```

**Response:** `{"status": "ok", "id": 42}`

---

### Dismiss

```
POST /pipeline/opportunities/{opportunity_id}/dismiss
```

**Example:**

```bash
curl -X POST "http://localhost:8420/pipeline/opportunities/42/dismiss"
```

**Response:** `{"status": "ok", "id": 42}`

---

### Format for Platform

```
GET /pipeline/opportunities/{opportunity_id}/format
```

Formats an opportunity's content for a specific platform with char counting and warnings.

**Parameters (query):**

| Parameter | Type | Default | Options | Description |
|-----------|------|---------|---------|-------------|
| `platform` | str | `"x"` | `x`, `linkedin`, `bluesky`, `newsletter` | Target platform |

**Example:**

```bash
curl "http://localhost:8420/pipeline/opportunities/42/format?platform=x"
```

**Response:**

```json
{
  "platform": "x_thread",
  "parts": [
    "1/4 The AI job revolution isn't coming — it's here.",
    "2/4 50% of current jobs will be automated by 2030.",
    "3/4 The winners will be those who adapt NOW.",
    "4/4 What skill are you building to stay ahead? 👇"
  ],
  "char_counts": [50, 54, 49, 52],
  "total_chars": 205,
  "warnings": [],
  "copy_ready": "1/4 The AI job revolution...\n\n2/4 50% of current jobs..."
}
```

**Platform-specific behavior:**

| Platform | Char Limit | Behavior |
|----------|-----------|----------|
| `x` (Twitter) | 280/part | Auto-numbered thread (1/N), splits at paragraph/sentence boundaries, strips markdown |
| `linkedin` | 3000 total | Preserves paragraphs, strips thread numbering and markdown, professional formatting |
| `bluesky` | 300/part | Same as X but with 300-char limit per part |
| `newsletter` | 5000 total | Preserves markdown, adds title as H2, hook as blockquote |

---

### Format for All Platforms

```
GET /pipeline/opportunities/{opportunity_id}/format/all
```

Returns formatting for all 4 platforms at once.

**Response:**

```json
{
  "opportunity_id": 42,
  "platforms": {
    "x": { "parts": [...], "copy_ready": "..." },
    "linkedin": { "parts": [...], "copy_ready": "..." },
    "bluesky": { "parts": [...], "copy_ready": "..." },
    "newsletter": { "parts": [...], "copy_ready": "..." }
  }
}
```

---

### List Genomes

```
GET /pipeline/genomes
```

**Parameters:** `limit` (default: 20, max: 100)

```bash
curl "http://localhost:8420/pipeline/genomes?limit=5"
```

---

### Top Genomes (Best Viral DNA)

```
GET /pipeline/genomes/top
```

**Parameters:** `limit` (default: 5, max: 20)

---

### Regenerate Variants for a Genome

```
POST /pipeline/genomes/{post_id}/regenerate
```

Generates fresh content variants from an existing genome.

**Parameters (query):**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `num_variants` | int | `3` | Variants to generate (1–10) |
| `voice_prompt` | str | `""` | Voice/style guide |

**Example:**

```bash
curl -X POST "http://localhost:8420/pipeline/genomes/abc123/regenerate?num_variants=5"
```

**Response:**

```json
{
  "genome_id": "abc123",
  "variants_generated": 4,
  "hook_type": "list",
  "pattern": "listicle"
}
```

**Edge cases:**
- Returns `{"error": "Genome not found for post_id: ..."}` (404) if genome doesn't exist
- Content-hash dedup prevents regenerating identical variants

---

### Pipeline Stats

```
GET /pipeline/stats
```

**Response:**

```json
{
  "total_genomes": 45,
  "total_opportunities": 187,
  "unseen_opportunities": 62,
  "top_genome": {
    "post_id": "abc123",
    "hook_type": "question",
    "engagement_score": 0.92
  },
  "hook_distribution": {
    "question": 12,
    "list": 10,
    "story": 8,
    "bold_claim": 7,
    "contrarian": 5
  }
}
```

---

## Bulk Actions

### Dismiss All Unseen

```
POST /pipeline/opportunities/dismiss-all
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `below_score` | float | `null` | Only dismiss opportunities below this score |

**Example:**

```bash
# Dismiss all unseen with score < 50
curl -X POST "http://localhost:8420/pipeline/opportunities/dismiss-all?below_score=50"
```

**Response:**

```json
{"status": "ok", "dismissed": 37, "below_score": 50.0}
```

---

### Regenerate Batch

```
POST /pipeline/opportunities/regenerate-batch
```

Re-generates variants for top-performing genomes.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | `5` | Number of top genomes to regenerate (1–20) |
| `min_score` | float | `60.0` | Only regenerate genomes from opportunities above this score |
| `voice_prompt` | str | `""` | Voice/style guide |
| `num_variants` | int | `3` | Variants per genome (1–5) |

**Example:**

```bash
curl -X POST "http://localhost:8420/pipeline/opportunities/regenerate-batch?limit=3&num_variants=5"
```

**Response:**

```json
{
  "status": "ok",
  "genomes_regenerated": 3,
  "total_variants": 14,
  "details": [...]
}
```

---

### Copy Batch (Platform-Formatted)

```
POST /pipeline/opportunities/copy-batch
```

Get top N unseen opportunities formatted for a platform. Marks them as viewed.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | `5` | Max results (1–20) |
| `platform` | str | `"x"` | Target platform |
| `min_score` | float | `0` | Minimum score filter |

**Example:**

```bash
curl -X POST "http://localhost:8420/pipeline/opportunities/copy-batch?limit=3&platform=linkedin"
```

**Response:**

```json
{
  "formatted": [
    {
      "id": 42,
      "title": "The AI Job Revolution",
      "score": 87.3,
      "platform": "linkedin",
      "parts": ["The AI Job Revolution..."],
      "copy_ready": "The AI Job Revolution...",
      "char_counts": [2847],
      "warnings": []
    }
  ],
  "count": 3
}
```

---

## Alerts

### Get Pending Alerts

```
GET /pipeline/alerts
```

Returns high-scoring unseen opportunities that haven't been alerted on yet.

**Parameters:** `threshold` (default: 75.0), `limit` (default: 10)

---

### Manually Check & Send Alerts

```
POST /pipeline/alerts/check
```

Checks for high-scoring opportunities and sends Telegram notifications if configured.

**Parameters:** `threshold` (default: 75.0)

**Response:**

```json
{
  "status": "alerted",
  "threshold": 75.0,
  "alerts_created": 3,
  "notified": 1,
  "top_opportunities": [
    {"id": 42, "score": 87.3, "title": "The AI Job Revolution", "variant_type": "thread"},
    {"id": 43, "score": 82.1, "title": "5 Things About...", "variant_type": "post"}
  ]
}
```

---

## Feedback — Performance Tracking & Adaptive Scoring

### Mark as Published

```
POST /feedback/published
```

**Parameters (query):**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `opportunity_id` | int | Yes | The opportunity being published |
| `genome_id` | str | Yes | Source genome ID |
| `variant_type` | str | Yes | Content format |
| `score` | float | Yes | Score at generation time |
| `score_breakdown` | str | No | JSON score breakdown |
| `platform` | str | No | Where it was published (default: `twitter`) |

**Example:**

```bash
curl -X POST "http://localhost:8420/feedback/published?opportunity_id=42&genome_id=abc123&variant_type=thread&score=87.3&platform=twitter"
```

**Response:**

```json
{
  "id": 1,
  "opportunity_id": 42,
  "status": "published",
  "published_at": "2026-06-24T15:30:00Z"
}
```

---

### Record Performance Metrics

```
POST /feedback/{feedback_id}/performance
```

**Body (JSON):**

```json
{
  "impressions": 15000,
  "engagements": 750,
  "likes": 500,
  "reposts": 120,
  "replies": 80,
  "clicks": 50
}
```

**Response:**

```json
{
  "id": 1,
  "engagement_rate": 5.0,
  "tier": "viral",
  "status": "recorded"
}
```

**Performance tiers:**

| Tier | Criteria |
|------|----------|
| `viral` | Engagement rate ≥ 5% AND impressions ≥ 1000 |
| `above_avg` | Engagement rate ≥ 3% |
| `avg` | Engagement rate ≥ 1% |
| `below_avg` | Everything else |

---

### Train Scorer Weights

```
POST /feedback/train
```

Analyzes feedback data and computes optimal scorer weights using Pearson correlation between each scoring dimension and actual engagement rates.

**Requirements:** Minimum 10 feedback records with performance metrics.

**Response (success):**

```json
{
  "status": "trained",
  "sample_size": 25,
  "confidence": 0.25,
  "correlations": {
    "engagement": 0.72,
    "structure": 0.58,
    "voice_match": 0.41
  },
  "new_weights": {
    "engagement": 0.42,
    "structure": 0.34,
    "voice_match": 0.24
  },
  "trained_at": "2026-06-24T16:00:00Z"
}
```

**Response (insufficient data):**

```json
{
  "status": "insufficient_data",
  "required": 10,
  "available": 4,
  "message": "Need 6 more performance records to train"
}
```

**Training algorithm:**
1. Collect all feedback records with `engagement_rate` and `score_breakdown`
2. For each scorer dimension, compute Pearson correlation with `engagement_rate`
3. Normalize correlations to weights (sum to 1.0)
4. Blend: 70% new weights + 30% existing weights (exponential moving average)
5. Persist to `scorer_weights` table
6. Auto-refresh the active `CompositeScorer` in the pipeline engine

---

### Feedback Stats

```
GET /feedback/stats
```

**Response:**

```json
{
  "total_published": 25,
  "with_performance_data": 18,
  "tier_distribution": {"viral": 3, "above_avg": 7, "avg": 5, "below_avg": 3},
  "avg_engagement_rate": 3.42,
  "best_variant_types": [
    {"variant_type": "thread", "avg_rate": 4.8, "cnt": 10},
    {"variant_type": "post", "avg_rate": 3.1, "cnt": 8}
  ],
  "top_performers": [...],
  "current_weights": {
    "engagement": {"weight": 0.42, "confidence": 0.25},
    "structure": {"weight": 0.34, "confidence": 0.25},
    "voice_match": {"weight": 0.24, "confidence": 0.25}
  }
}
```

---

## Research — Outlier Detection

### Get Outliers

```
GET /research/outliers
```

**Parameters:** `platform` (default: `reddit`), `hours` (default: 24), `limit` (default: 20)

---

### Get Trends

```
GET /research/trends
```

---

### Get Stats

```
GET /research/stats
```

---

### Daily Brief

```
GET /research/brief
```

Returns a natural-language summary of trending content.

---

## Search — Hybrid FTS5 + TF-IDF

### Keyword Search

```
GET /search
```

**Parameters:** `q` (query string), `limit` (default: 20)

---

### Hybrid Search (FTS5 + TF-IDF + RRF)

```
GET /search/hybrid
```

Combines full-text search with semantic similarity using Reciprocal Rank Fusion.

**Parameters:** `q`, `limit` (default: 20), `fts_weight` (default: 0.5), `tfidf_weight` (default: 0.5)

---

### Similar Posts

```
GET /search/similar
```

**Parameters:** `post_id`, `limit` (default: 10)

---

## Chat — LLM Streaming

### Stream Response

```
GET /chat/stream
```

Server-Sent Events (SSE) streaming. Send message as query parameter.

**Parameters:** `message` (string), `model` (optional), `context` (optional conversation history)

**Usage (frontend):**

```javascript
const eventSource = new EventSource('/chat/stream?message=Write+a+thread+about+AI');
eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.done) eventSource.close();
  else appendToChat(data.token);
};
```

---

## Content — Multi-Format Generation

### Repurpose Content

```
POST /content/repurpose
```

**Body (JSON):**

```json
{
  "content": "Original content to repurpose...",
  "target_format": "thread",
  "platform": "twitter"
}
```

**Supported formats:** `thread`, `post`, `article`, `newsletter`, `carousel`, `script`

---

### Generate Ideas

```
POST /content/ideas
```

---

### Score Content

```
POST /content/analytics/score
```

**Body:** `{"content": "text to score"}`

---

## Voice — Writing Style Profiles

### Build Voice Profile

```
POST /voice/build
```

**Body:** `{"name": "default", "samples": ["text1", "text2", ...]}`

---

### List Profiles

```
GET /voice/profiles
```

---

## Ingestion — Data Collection

### Trigger Ingestion

```
POST /ingest/posts
```

**Body:** `{"platform": "reddit", "subreddits": ["startups", "saas"]}`

---

### Get Job Status

```
GET /ingest/jobs
```

---

## Other Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics (text format) |
| `/metrics/json` | GET | Prometheus metrics (JSON) |
| `/scheduler/status` | GET | Background scheduler status |
| `/creators` | GET | List tracked creators |
| `/creators/discover` | GET | Discover new high-performing authors |
| `/boards` | GET/POST | Swipe-file board management |
| `/alerts` | GET | Outlier alert history |
| `/scrape` | POST | Firecrawl deep-scraping |
| `/transcribe` | POST | Whisper audio transcription |
| `/analytics/explain` | POST | Virality explanation (LLM) |
| `/analytics/patterns` | POST | Pattern analysis |
