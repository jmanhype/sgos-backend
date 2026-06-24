# The Viral Content Pipeline — Deep Dive

The pipeline is an autonomous system that watches for viral content, extracts what made it work, generates your own versions, and learns from what you publish.

## Concepts

### Viral Genome

A **ViralGenome** is the structural "DNA" extracted from a viral post. It captures *why* something went viral, not *what* it said.

```python
ViralGenome(
    post_id="reddit_abc123",
    hook_type="question",           # How it opens
    hook_text="Why do 90% of startups fail at marketing?",
    emotional_arc=["curiosity", "frustration", "hope"],
    structural_pattern="how_to",    # How it's organized
    key_phrases=["growth hacking", "product-market fit"],
    content_length_words=450,
    platform_signals={"z_score": 4.2, "upvote_ratio": 0.95},
    engagement_score=0.87,          # 0-1 normalized
)
```

**Hook types:** `question`, `statistic`, `story`, `contrarian`, `list`, `bold_claim`, `tutorial`, `meme`, `personal`, `news`

**Structural patterns:** `listicle`, `narrative`, `how_to`, `rant`, `analysis`, `comparison`, `case_study`, `thread`, `announcement`, `question_post`

### Content Variant

A **ContentVariant** is a generated content piece based on a genome. One genome → multiple variants.

```python
ContentVariant(
    genome_id="reddit_abc123",
    variant_type="thread",           # thread, post, newsletter, script, carousel
    title="5 Marketing Mistakes Killing Your Startup",
    content="1/5 Most founders think growth = ads. Wrong.\n\nHere are the 5 mistakes...",
    score=87.3,                      # 0-100 composite
    score_breakdown={
        "engagement": {"raw": 90.0, "weight": 0.35, "weighted": 31.5},
        "structure": {"raw": 82.0, "weight": 0.40, "weighted": 32.8},
        "voice_match": {"raw": 92.0, "weight": 0.25, "weighted": 23.0},
    },
    hook="5 Marketing Mistakes Killing Your Startup",
)
```

### Opportunity

An **Opportunity** is a stored ContentVariant in the database, ready for you to review. It has `viewed` and `dismissed` flags to track what you've seen.

---

## Pipeline Stages

### Stage 1: Outlier Detection (Research Service)

The scheduler pulls fresh posts from Reddit, Hacker News, and Twitter every 4 hours. Posts with a **z-score ≥ 3.0** (3 standard deviations above the mean for their subreddit/topic) are flagged as outliers.

```
Input:  Raw posts from ingested platforms
Output: List of outlier post dicts with z_score, engagement metrics
```

The z-score normalizes across subreddits — a post with 500 upvotes in r/startups (high-traffic) gets a different z-score than 500 upvotes in r/niche-community.

### Stage 2: Genome Extraction (LLMGenomeExtractor)

Extracts the structural elements that made the post viral.

**Strategy:** Try LLM first, fall back to rules if unavailable.

**LLM extraction** sends the post to an LLM with a structured JSON prompt:
- Hook type classification
- Emotional arc detection
- Structural pattern identification
- Key phrase extraction

**Rule-based fallback** uses heuristics:
- `?` at end of title → `"question"` hook
- `\d+ ways|tips|things` → `"list"` hook
- `I built|I made|my journey` → `"story"` hook
- Numbered lists in content → `"listicle"` pattern
- `step 1, step 2` → `"how_to"` pattern

**Engagement score** is a weighted composite:
```
engagement_score = z_score_norm * 0.4 + upvote_ratio * 0.3 + comments_norm * 0.3
```
Where `z_score_norm = min(z_score / 5.0, 1.0)` and `comments_norm = min(comments / 500, 1.0)`.

### Stage 3: Variant Generation (LLMVariantGenerator)

Generates 3–5 content variants per genome, each in a different format:

| Format | Typical Length | Use Case |
|--------|---------------|----------|
| `thread` | 200–500 words | X/Twitter multi-post thread |
| `post` | 100–300 words | LinkedIn/Reddit single post |
| `newsletter` | 400–800 words | Email newsletter section |
| `script` | 150–250 words | TikTok/Reels script |
| `carousel` | 100–200 words | Instagram carousel text |

**Voice matching:** If a `voice_prompt` is provided, the LLM is instructed to write in that style. The pipeline also has a separate `VoiceMatchScorer` that evaluates how well the output matches the user's voice profile.

### Stage 4: Composite Scoring

Each variant is scored by multiple independent scorers, combined via weighted average:

```
final_score = Σ (scorer_raw × scorer_weight) / Σ weights
```

#### EngagementScorer (default weight: 0.35)

Scores based on the source genome's real engagement:
- Base: `genome.engagement_score * 100` (0–100)
- Z-score bonus: `min(z_score / 5, 1) * 15` (0–15 points)
- Upvote ratio bonus: `upvote_ratio * 10` (0–10 points)

**Rationale:** Content derived from proven viral posts is more likely to succeed.

#### StructureScorer (default weight: 0.40)

Evaluates the generated content's structural quality across 4 dimensions (25 points each):

1. **Hook strength** (0–25):
   - Question in first line: +5
   - Specific number in first line: +3.75
   - Short first line (<100 chars): +3.75
   - Emotional words ("secret", "shocking", "mistake"): +5

2. **Length appropriateness** (0–25):
   - Matches format-specific optimal range → 25
   - Under: proportional penalty
   - Over: gentle penalty (0.5× overshoot ratio)

3. **Formatting quality** (0–25):
   - Paragraph breaks (≥3): +5
   - List items: +3.75
   - Bold/italic emphasis: +2.5
   - Emoji: +2.5
   - No wall-of-text paragraphs: +3.75

4. **Completeness** (0–25):
   - Has meaningful title: +5
   - Ends with CTA or conclusion: +3.75
   - Over 50 words: +3.75

#### VoiceMatchScorer (default weight: 0.25)

Uses **TF-IDF cosine similarity** to compare the variant against the user's voice profile samples.

**Why TF-IDF, not LLM?** Speed. Each variant needs a score. An LLM call per variant would add 2–5 seconds per variant × 15+ variants = 30–75 seconds of latency. TF-IDF runs in <1ms.

**Algorithm:**
1. Lazy-load voice profile from DB (once per scorer instance)
2. Build TF-IDF vector from voice profile samples
3. Build TF-IDF vector from variant content
4. Compute cosine similarity (0–1)
5. Amplify: `min(cosine * 150, 100)` — stretches the range since most content has some overlap

**Fallback:** If no voice profile exists, returns 50 (neutral — doesn't help or hurt the score).

---

## Adaptive Scoring (Feedback Loop)

The system learns from what you actually publish and how it performs.

### How Training Works

1. **Data collection:** You mark opportunities as "published" and later record real metrics (impressions, engagement rate).

2. **Correlation analysis:** For each scorer dimension, compute Pearson correlation between that dimension's raw score and actual engagement rate:

```
correlation(engagement_scores, engagement_rates) → 0.72
correlation(structure_scores, engagement_rates)  → 0.58
correlation(voice_match_scores, engagement_rates) → 0.41
```

3. **Weight normalization:** Correlations become weights (sum to 1.0):

```
engagement:  0.72 / (0.72 + 0.58 + 0.41) = 0.42
structure:   0.58 / (0.72 + 0.58 + 0.41) = 0.34
voice_match: 0.41 / (0.72 + 0.58 + 0.41) = 0.24
```

4. **Blending (anti-overfitting):** New weights are blended with existing weights:

```
final = 0.7 × new_weight + 0.3 × old_weight
```

This prevents overfitting to small sample sizes. As more data accumulates, the new signal dominates.

5. **Confidence metric:** `min(sample_size / 100, 1.0)` — full confidence at 100+ records.

6. **Hot-swap:** `refresh_scorer()` replaces the active `CompositeScorer` in the `PipelineEngine` singleton without restarting the server.

### When Training Runs

- **Manually:** `POST /feedback/train`
- **Automatically:** Scheduler calls `auto_train_and_refresh()` after each pipeline run
- **Minimum data:** 10 feedback records with performance metrics required

---

## Platform Formatters

Convert raw content variants into platform-ready, copy-pasteable output.

### XThreadFormatter

Splits content into ≤280-char parts with auto-numbering:

1. Strip markdown (bold, italic, headers, links → plain text)
2. Split at paragraph boundaries (preferred) or sentence boundaries (fallback)
3. Number each part: `1/N`, `2/N`, ...
4. Re-split any parts that exceed 280 chars after numbering
5. Final re-numbering pass to ensure correct totals

**Edge case:** A single word > 280 chars can't be split — it gets a warning.

### LinkedInFormatter

Single long-form post (≤3000 chars):
1. Strip thread numbering (`1/5`, `2/5`, etc.)
2. Strip markdown (LinkedIn doesn't render it)
3. Add title as first line, hook as second if different
4. Normalize line breaks
5. Trim at word boundary if over limit

### BlueskyFormatter

Same as X formatter but with 300-char limit per part.

### NewsletterFormatter

Markdown-preserved format:
1. Title becomes `## Title`
2. Hook becomes `> blockquote`
3. Content keeps markdown formatting
4. Trimmed to 5000 chars if needed

---

## Alert System

When the pipeline generates high-scoring opportunities (≥75), it sends push notifications.

### Alert Flow

1. After `process_outliers()`, the router calls `alert_high_score(threshold=75.0)`
2. `get_pending_alerts()` queries unseen opportunities above threshold that haven't been alerted on
3. Each opportunity gets a 24-hour cooldown (same opportunity won't trigger alerts twice in 24h)
4. Alert records are persisted to `pipeline_alerts` table
5. Telegram notification is sent via `alert_system._send_telegram()` (bypasses outlier threshold check)

### Telegram Message Format

```
🔥 *SGOS Pipeline Alert*

📊 3 high-scoring opportunities ready:

1. *The AI Job Revolution Is Here*
   Score: 87 | Format: thread

2. *5 Marketing Mistakes*
   Score: 82 | Format: post

3. *Why Nobody Reads Your Blog*
   Score: 79 | Format: newsletter

Open the Pipeline Dashboard to review →
```

---

## Deduplication

The pipeline prevents duplicate content via **content hashing**:

```python
content_hash = SHA256(f"{genome_id}:{variant_type}:{content}")[:16]
```

The `pipeline_opportunities` table has a unique index on `content_hash` (where non-empty). When `save_opportunity()` detects a duplicate, it returns `-1` and the variant is skipped.

This means:
- Running the pipeline twice on the same outliers won't create duplicate opportunities
- Regenerating variants for a genome won't recreate identical content
- The `test_regenerate` test clears existing opportunities before re-generating

---

## Database Migrations

The `GenomeRepository._ensure_table()` handles schema evolution:

1. **Create tables** (no indexes — they depend on columns that may not exist yet)
2. **Run migrations** (ALTER TABLE to add new columns)
3. **Create indexes** (safe after migrations)

This ordering prevents the bug where `CREATE INDEX` fails on a column that hasn't been added yet.

Current migrations:
- `content_hash` column added to `pipeline_opportunities` (added in Cycle 3)

---

## Configuration Tuning

### Scorer Weights

Default weights (before any training data):

| Scorer | Weight | Rationale |
|--------|--------|-----------|
| Engagement | 0.35 | Source post virality predicts variant success |
| Structure | 0.40 | Well-structured content outperforms poorly structured |
| Voice Match | 0.25 | Matching your voice makes content feel authentic |

These weights are replaced by trained weights once ≥10 feedback records exist.

### Alert Threshold

Default: 75.0. Opportunities scoring ≥75 trigger alerts.

Lower it if you want more notifications. Raise it if you only want the best.

### Variant Count

Default: 3 per genome. More variants = more options but more noise.

### Scheduler Interval

Default: 4 hours. Configured via `SGOS_SCHEDULER_INTERVAL_HOURS`.
