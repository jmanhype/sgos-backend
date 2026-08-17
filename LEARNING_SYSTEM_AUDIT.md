# SGOS Learning System Audit

**Date:** 2026-06-27  
**Status:** Infrastructure exists, but NOT wired up

---

## Executive Summary

The SGOS platform has **complete learning system infrastructure** but **none of it is connected**. The system should be learning from your actual reply performance, but it's operating on hardcoded weights instead.

**The gap:** 28 posted strikes with ZERO tracked performance data. The feedback loop is broken at the source — we never capture `reply_tweet_id` when you post.

---

## What EXISTS (Fully Implemented)

### 1. `tracker.py` — Performance Tracking System ✅
- **Location:** `~/sgos-backend/tracker.py`
- **Capabilities:**
  - `track_tweet()` — fetches metrics via FXTwitter API (free, no auth)
  - `refresh_due_tweets()` — auto re-checks at 24h, 48h, 168h
  - `train_scorer_from_performance()` — trains hook_type, pattern, variant weights
  - `get_performance_summary()` — analytics with best performer, by hook type, by variant
- **Status:** Code is complete and functional
- **Data:** 2 records in `post_performance` table (1 test, 1 real)

### 2. `sgos-strike-tracker.py` — Cron Job ✅
- **Location:** `~/.hermes/scripts/sgos-strike-tracker.py`
- **Schedule:** Every 6 hours (cron job `780fc047a47b`)
- **Capabilities:**
  - Reads `reply_likes_1h`, `reply_likes_6h`, `reply_likes_24h`, `reply_impressions_24h`
  - Uses bird CLI to fetch metrics
  - Updates strikes table with performance data
- **Status:** Code exists but **last run had error** (reply_tweet_id is NULL for all posted strikes)

### 3. `services/feedback.py` — Feedback Service ✅
- **Location:** `~/sgos-backend/services/feedback.py`
- **Capabilities:**
  - `mark_published()` — marks opportunities as published
  - `record_performance()` — records real engagement metrics
  - `train_weights()` — analyzes performance data, computes optimal scorer weights
  - `get_stats()` — analytics summary with tier distribution, best variants, current weights
- **Status:** Code is complete and functional
- **Data:** 1 test record in `performance_feedback` table

### 4. `services/pipeline/scoring.py` — Pluggable Scoring ✅
- **Location:** `~/sgos-backend/services/pipeline/scoring.py`
- **Capabilities:**
  - `EngagementScorer` (weight 0.4)
  - `StructureScorer` (weight 0.6)
  - Composite pattern — add new scorers without changing existing code
- **Status:** Code exists but uses **hardcoded weights**, never reads from `scorer_weights` table

### 5. `scorer_weights` Table ✅
- **Schema:** `scorer_name`, `weight`, `trained_at`, `sample_size`, `confidence`
- **Status:** Table exists but **EMPTY** (0 records)

---

## What's NOT WIRED (The Broken Links)

### ❌ Gap 1: Strike Engine Uses Hardcoded Weights

**File:** `~/.hermes/scripts/sgos-strike-engine.py` (lines 428-433)

```python
raw_score = (
    urgency * 4.0 +      # Timing is most important
    audience * 1.5 +     # Bigger engagement = more impressions
    velocity * 2.0 +     # Fast-growing = viral potential
    topic * 3.0           # Niche match = higher engagement
)
```

**Problem:** These weights are **never read from `scorer_weights` table**. Even if we train weights, the strike engine ignores them.

**Fix needed:** Load weights from DB, fall back to hardcoded defaults if not trained yet.

---

### ❌ Gap 2: `reply_tweet_id` Never Captured

**Problem:** When you click "✓ Posted" in the UI, we mark the strike as posted but **never ask for or capture your reply URL/tweet ID**.

**Impact:** The strike tracker can't find tweets to track. All 28 posted strikes have `reply_tweet_id = NULL`.

**Fix needed:** 
1. Add input field in UI to paste reply URL when marking as posted
2. Or auto-detect by searching `from:StrughterG` replies and matching to parent tweet ID

---

### ❌ Gap 3: No Automated Training Loop

**Problem:** No cron job runs `tracker.py train` or `services/feedback.py train_weights()`.

**Impact:** Even if we had performance data, weights would never get updated.

**Fix needed:** Add cron job to run training weekly (or after every 10 new performance records).

---

### ❌ Gap 4: Viral Pipeline Doesn't Use Feedback

**Problem:** The pipeline scoring (`services/pipeline/scoring.py`) uses hardcoded 0.4/0.6 weights, never reads from `scorer_weights`.

**Impact:** The viral pipeline (threads) doesn't learn from performance either.

**Fix needed:** Load weights from DB in `CompositeScorer`.

---

## The Root Cause

**The system has the infrastructure but not the data.**

The learning loop should be:
```
Generate → Post → Track → Learn → Improve → Repeat
```

But it's actually:
```
Generate → Post → [reply_tweet_id not captured] → [tracker can't find tweets] → [no data] → [no learning] → [hardcoded weights] → Repeat
```

**The fix is simple:** Capture `reply_tweet_id` when marking as posted. Everything else will work.

---

## Proposed Fix (Priority Order)

### 1. **Wire Up Reply Tracking** (1 hour)
- Add input field in Strikes UI to paste reply URL when clicking "✓ Posted"
- Parse tweet ID from URL, store in `strikes.reply_tweet_id`
- Fix strike tracker to handle the data

### 2. **Load Weights from DB in Strike Engine** (30 min)
- Modify `score_tweet()` to read from `scorer_weights` table
- Fall back to hardcoded defaults if table is empty
- Log when using trained vs default weights

### 3. **Add Training Cron Job** (15 min)
- Schedule weekly training: `python3 ~/sgos-backend/tracker.py train`
- Or trigger after every 10 new performance records
- Log weight updates to Telegram

### 4. **Wire Viral Pipeline to Feedback** (30 min)
- Modify `CompositeScorer` to load weights from `scorer_weights`
- Use trained weights for `hook_type`, `pattern`, `variant` scoring
- Fall back to hardcoded if not trained

### 5. **Auto-Detect Reply URLs** (Future enhancement)
- Background job scans `from:StrughterG` replies
- Matches reply to parent tweet ID in `strikes` table
- Auto-populates `reply_tweet_id` without manual input

---

## Expected Impact

After wiring up the learning system:

- **Week 1:** 28 tracked replies, initial weights trained
- **Week 2:** Strike engine uses trained weights, better targeting
- **Month 1:** 100+ tracked replies, weights stabilize
- **Month 3:** Predictive model learns your voice/audience patterns

**The system will actually learn** which reply styles, topics, and targets convert to followers/engagement, and automatically optimize for those.

---

## Data Sources (Already Available)

- **FXTwitter API** — free, no auth, works for all public tweets
- **Bird CLI** — Chrome profile auth, already working
- **X Analytics** — ego-browser, already working
- **Post performance** — impressions, likes, replies, retweets, quotes
- **Strikes data** — urgency, audience, velocity, topic, follower_tier
- **Genome data** — hook_type, structural_pattern, variant_type

All the data exists. We just need to wire it.
