"""
Feedback Service — Tracks content performance and feeds it back into scoring.

The closed loop: generate → publish → measure → learn.

SOLID:
  - Single Responsibility: Only handles performance tracking + weight training.
  - Open/Closed: New metrics types can be added without changing core logic.
  - Dependency Inversion: Depends on DB protocol, not concrete implementation.
"""
import json
import math
import time
import threading
from datetime import datetime, timezone
from database import get_connection


class FeedbackService:
    """
    Tracks which pipeline opportunities were published and their real-world
    performance. Uses this data to train scorer weights.
    """

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "FeedbackService":
        """Singleton — one feedback service per process."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._init_tables()

    def _init_tables(self):
        """Create feedback tables if they don't exist."""
        conn = get_connection()
        c = conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS performance_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER NOT NULL,
                genome_id TEXT NOT NULL,
                variant_type TEXT NOT NULL,
                score_at_generation REAL NOT NULL,
                score_breakdown TEXT,
                published_at TEXT NOT NULL,
                platform TEXT DEFAULT 'twitter',
                
                -- Real performance metrics (filled in later)
                impressions INTEGER,
                engagements INTEGER,
                likes INTEGER,
                reposts INTEGER,
                replies INTEGER,
                clicks INTEGER,
                engagement_rate REAL,
                
                -- Derived
                performance_tier TEXT,  -- 'viral', 'above_avg', 'avg', 'below_avg'
                feedback_entered_at TEXT,
                
                created_at TEXT DEFAULT (datetime('now'))
            );
            
            CREATE TABLE IF NOT EXISTS scorer_weights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scorer_name TEXT UNIQUE NOT NULL,
                weight REAL NOT NULL,
                trained_at TEXT DEFAULT (datetime('now')),
                sample_size INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0
            );
            
            CREATE INDEX IF NOT EXISTS idx_feedback_genome 
                ON performance_feedback(genome_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_tier 
                ON performance_feedback(performance_tier);
        """)
        conn.commit()

    # ─── Publishing Tracking ─────────────────────────────────────────────

    def mark_published(
        self,
        opportunity_id: int,
        genome_id: str,
        variant_type: str,
        score_at_generation: float,
        score_breakdown: str = "{}",
        platform: str = "twitter",
    ) -> dict:
        """Mark an opportunity as published. Returns feedback record."""
        conn = get_connection()
        c = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        
        c.execute("""
            INSERT INTO performance_feedback 
                (opportunity_id, genome_id, variant_type, score_at_generation,
                 score_breakdown, published_at, platform)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (opportunity_id, genome_id, variant_type, score_at_generation,
              score_breakdown, now, platform))
        conn.commit()
        
        feedback_id = c.lastrowid
        return {
            "id": feedback_id,
            "opportunity_id": opportunity_id,
            "status": "published",
            "published_at": now,
        }

    def record_performance(
        self,
        feedback_id: int,
        impressions: int = 0,
        engagements: int = 0,
        likes: int = 0,
        reposts: int = 0,
        replies: int = 0,
        clicks: int = 0,
    ) -> dict:
        """Record real-world performance metrics for a published opportunity."""
        conn = get_connection()
        c = conn.cursor()
        
        # Calculate engagement rate
        engagement_rate = (engagements / impressions * 100) if impressions > 0 else 0
        
        # Determine performance tier
        tier = self._classify_tier(engagement_rate, impressions)
        now = datetime.now(timezone.utc).isoformat()
        
        c.execute("""
            UPDATE performance_feedback SET
                impressions=?, engagements=?, likes=?, reposts=?,
                replies=?, clicks=?, engagement_rate=?,
                performance_tier=?, feedback_entered_at=?
            WHERE id=?
        """, (impressions, engagements, likes, reposts,
              replies, clicks, engagement_rate, tier, now, feedback_id))
        conn.commit()
        
        return {
            "id": feedback_id,
            "engagement_rate": round(engagement_rate, 2),
            "tier": tier,
            "status": "recorded",
        }

    # ─── Analytics ───────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get feedback statistics summary."""
        conn = get_connection()
        c = conn.cursor()
        
        total = c.execute("SELECT COUNT(*) FROM performance_feedback").fetchone()[0]
        with_metrics = c.execute(
            "SELECT COUNT(*) FROM performance_feedback WHERE impressions IS NOT NULL"
        ).fetchone()[0]
        
        tier_counts = {}
        rows = c.execute("""
            SELECT performance_tier, COUNT(*) as cnt 
            FROM performance_feedback 
            WHERE performance_tier IS NOT NULL
            GROUP BY performance_tier
        """).fetchall()
        for row in rows:
            tier_counts[row["performance_tier"]] = row["cnt"]
        
        avg_engagement = c.execute(
            "SELECT AVG(engagement_rate) FROM performance_feedback WHERE engagement_rate IS NOT NULL"
        ).fetchone()[0] or 0
        
        # Best performing variant types
        by_type = c.execute("""
            SELECT variant_type, AVG(engagement_rate) as avg_rate, COUNT(*) as cnt
            FROM performance_feedback
            WHERE engagement_rate IS NOT NULL
            GROUP BY variant_type
            ORDER BY avg_rate DESC
        """).fetchall()
        
        # Best hook types (from genome)
        by_hook = c.execute("""
            SELECT f.variant_type, f.performance_tier, f.engagement_rate,
                   f.score_at_generation, f.genome_id
            FROM performance_feedback f
            WHERE f.engagement_rate IS NOT NULL
            ORDER BY f.engagement_rate DESC
            LIMIT 10
        """).fetchall()
        
        # Current weights
        weights = {}
        weight_rows = c.execute("SELECT scorer_name, weight, confidence FROM scorer_weights").fetchall()
        for row in weight_rows:
            weights[row["scorer_name"]] = {
                "weight": row["weight"],
                "confidence": row["confidence"],
            }
        
        return {
            "total_published": total,
            "with_performance_data": with_metrics,
            "tier_distribution": tier_counts,
            "avg_engagement_rate": round(avg_engagement, 2),
            "best_variant_types": [dict(r) for r in by_type],
            "top_performers": [dict(r) for r in by_hook],
            "current_weights": weights,
        }

    def get_feedback_list(self, limit: int = 50, tier: str | None = None) -> list[dict]:
        """List feedback records."""
        conn = get_connection()
        c = conn.cursor()
        
        where = "WHERE performance_tier = ?" if tier else ""
        params = [tier] if tier else []
        
        rows = c.execute(f"""
            SELECT * FROM performance_feedback 
            {where}
            ORDER BY published_at DESC 
            LIMIT ?
        """, params + [limit]).fetchall()
        
        return [dict(r) for r in rows]

    # ─── Scorer Training ─────────────────────────────────────────────────

    def train_weights(self) -> dict:
        """
        Analyze feedback data and compute optimal scorer weights.
        
        Strategy: For each scorer dimension, compute correlation between
        that dimension's score and actual engagement_rate. Scorers that
        better predict real performance get higher weights.
        
        Requires minimum 10 data points with performance metrics.
        """
        conn = get_connection()
        c = conn.cursor()
        
        rows = c.execute("""
            SELECT score_breakdown, engagement_rate, performance_tier
            FROM performance_feedback
            WHERE engagement_rate IS NOT NULL AND score_breakdown IS NOT NULL
        """).fetchall()
        
        if len(rows) < 10:
            return {
                "status": "insufficient_data",
                "required": 10,
                "available": len(rows),
                "message": f"Need {10 - len(rows)} more performance records to train",
            }
        
        # Extract per-scorer scores and engagement rates
        scorer_scores: dict[str, list[float]] = {}
        engagement_rates: list[float] = []
        
        for row in rows:
            try:
                breakdown = json.loads(row["score_breakdown"])
                er = row["engagement_rate"]
                engagement_rates.append(er)
                
                for scorer_name, data in breakdown.items():
                    if scorer_name not in scorer_scores:
                        scorer_scores[scorer_name] = []
                    scorer_scores[scorer_name].append(data.get("raw", 50))
            except (json.JSONDecodeError, TypeError):
                continue
        
        if len(engagement_rates) < 10:
            return {"status": "insufficient_data", "available": len(engagement_rates)}
        
        # Compute correlation of each scorer with engagement_rate
        correlations = {}
        for scorer_name, scores in scorer_scores.items():
            if len(scores) == len(engagement_rates):
                corr = self._pearson_correlation(scores, engagement_rates)
                correlations[scorer_name] = max(corr, 0.05)  # Floor at 0.05
        
        if not correlations:
            return {"status": "no_correlations", "message": "Could not compute correlations"}
        
        # Normalize correlations to weights (they must sum to 1.0)
        total_corr = sum(correlations.values())
        new_weights = {k: v / total_corr for k, v in correlations.items()}
        
        # Blend with current weights (exponential moving average)
        # 70% new signal, 30% current (prevents overfitting to small samples)
        current_weights = {}
        for row in c.execute("SELECT scorer_name, weight FROM scorer_weights").fetchall():
            current_weights[row["scorer_name"]] = row["weight"]
        
        blend_alpha = 0.7
        final_weights = {}
        for name, new_w in new_weights.items():
            old_w = current_weights.get(name, 1.0 / len(new_weights))
            final_weights[name] = round(blend_alpha * new_w + (1 - blend_alpha) * old_w, 4)
        
        # Normalize final weights
        total_final = sum(final_weights.values())
        for name in final_weights:
            final_weights[name] = round(final_weights[name] / total_final, 4)
        
        # Persist
        confidence = min(len(rows) / 100, 1.0)  # Confidence grows with data
        now = datetime.now(timezone.utc).isoformat()
        
        for name, weight in final_weights.items():
            c.execute("""
                INSERT INTO scorer_weights (scorer_name, weight, trained_at, sample_size, confidence)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scorer_name) DO UPDATE SET
                    weight=excluded.weight, trained_at=excluded.trained_at,
                    sample_size=excluded.sample_size, confidence=excluded.confidence
            """, (name, weight, now, len(rows), confidence))
        
        conn.commit()
        
        return {
            "status": "trained",
            "sample_size": len(rows),
            "confidence": round(confidence, 2),
            "correlations": {k: round(v, 3) for k, v in correlations.items()},
            "new_weights": final_weights,
            "trained_at": now,
        }

    def get_trained_weights(self) -> dict[str, float] | None:
        """Get current trained weights, or None if not yet trained."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT scorer_name, weight FROM scorer_weights"
        ).fetchall()
        
        if not rows:
            return None
        
        return {row["scorer_name"]: row["weight"] for row in rows}

    # ─── Internal Helpers ────────────────────────────────────────────────

    @staticmethod
    def _classify_tier(engagement_rate: float, impressions: int) -> str:
        """Classify performance into tiers."""
        if engagement_rate >= 5.0 and impressions >= 1000:
            return "viral"
        elif engagement_rate >= 3.0:
            return "above_avg"
        elif engagement_rate >= 1.0:
            return "avg"
        else:
            return "below_avg"

    @staticmethod
    def _pearson_correlation(x: list[float], y: list[float]) -> float:
        """Compute Pearson correlation coefficient."""
        n = len(x)
        if n < 2:
            return 0.0
        
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        
        num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        den_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
        den_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))
        
        if den_x == 0 or den_y == 0:
            return 0.0
        
        return num / (den_x * den_y)


# Singleton instance
feedback_service = FeedbackService.get_instance()
