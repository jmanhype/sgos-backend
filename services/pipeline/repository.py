"""
Genome Repository — Persists and retrieves viral genomes to SQLite.

SOLID:
  - Single Responsibility: Only CRUD operations for genomes.
  - Dependency Inversion: Implements IGenomeRepository protocol.
"""
import json
from datetime import datetime, timezone

from database import get_connection
from services.pipeline.protocols import ViralGenome


class GenomeRepository:
    """SQLite-backed genome storage with performance tracking."""

    TABLE_NAME = "viral_genomes"

    def __init__(self):
        self._ensure_table()

    def _ensure_table(self):
        """Create the genomes table if it doesn't exist, with migrations."""
        conn = get_connection()

        # Create tables (no indexes yet — migrations may need to run first)
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
                post_id TEXT PRIMARY KEY,
                hook_type TEXT NOT NULL DEFAULT 'unknown',
                hook_text TEXT DEFAULT '',
                emotional_arc TEXT DEFAULT '[]',
                structural_pattern TEXT DEFAULT 'unknown',
                key_phrases TEXT DEFAULT '[]',
                content_length_words INTEGER DEFAULT 0,
                platform_signals TEXT DEFAULT '{{}}',
                engagement_score REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                opportunity_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS pipeline_opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                genome_id TEXT NOT NULL,
                variant_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                score REAL DEFAULT 0.0,
                score_breakdown TEXT DEFAULT '{{}}',
                hook TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                viewed BOOLEAN DEFAULT 0,
                dismissed BOOLEAN DEFAULT 0,
                FOREIGN KEY (genome_id) REFERENCES {self.TABLE_NAME}(post_id)
            );
        """)

        # Migration: add content_hash column if missing (pre-existing tables)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pipeline_opportunities)").fetchall()}
        if "content_hash" not in cols:
            conn.execute("ALTER TABLE pipeline_opportunities ADD COLUMN content_hash TEXT DEFAULT ''")

        # Now create indexes (safe after migration)
        conn.executescript(f"""
            CREATE INDEX IF NOT EXISTS idx_genomes_engagement
                ON {self.TABLE_NAME}(engagement_score DESC);
            CREATE INDEX IF NOT EXISTS idx_genomes_created
                ON {self.TABLE_NAME}(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_genomes_hook
                ON {self.TABLE_NAME}(hook_type);
            CREATE INDEX IF NOT EXISTS idx_genomes_pattern
                ON {self.TABLE_NAME}(structural_pattern);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_opps_content_hash
                ON pipeline_opportunities(content_hash) WHERE content_hash != '';
            CREATE INDEX IF NOT EXISTS idx_opps_score
                ON pipeline_opportunities(score DESC);
            CREATE INDEX IF NOT EXISTS idx_opps_created
                ON pipeline_opportunities(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_opps_viewed
                ON pipeline_opportunities(viewed);
        """)
        conn.commit()

    def save(self, genome: ViralGenome) -> None:
        """Insert or update a genome."""
        conn = get_connection()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(f"""
            INSERT OR REPLACE INTO {self.TABLE_NAME}
                (post_id, hook_type, hook_text, emotional_arc, structural_pattern,
                 key_phrases, content_length_words, platform_signals, engagement_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            genome.post_id,
            genome.hook_type,
            genome.hook_text,
            json.dumps(genome.emotional_arc),
            genome.structural_pattern,
            json.dumps(genome.key_phrases),
            genome.content_length_words,
            json.dumps(genome.platform_signals),
            genome.engagement_score,
            now,
        ))
        conn.commit()

    def get(self, post_id: str) -> ViralGenome | None:
        """Retrieve a single genome by post_id."""
        conn = get_connection()
        row = conn.execute(
            f"SELECT * FROM {self.TABLE_NAME} WHERE post_id = ?",
            (post_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_genome(row)

    def list_recent(self, limit: int = 20) -> list[ViralGenome]:
        """List most recently extracted genomes."""
        conn = get_connection()
        rows = conn.execute(
            f"SELECT * FROM {self.TABLE_NAME} ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [self._row_to_genome(r) for r in rows]

    def get_top_genomes(self, limit: int = 5) -> list[ViralGenome]:
        """Get highest-engagement genomes (best viral DNA)."""
        conn = get_connection()
        rows = conn.execute(
            f"SELECT * FROM {self.TABLE_NAME} ORDER BY engagement_score DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [self._row_to_genome(r) for r in rows]

    def exists(self, post_id: str) -> bool:
        """Check if a genome already exists for a post."""
        conn = get_connection()
        row = conn.execute(
            f"SELECT 1 FROM {self.TABLE_NAME} WHERE post_id = ?",
            (post_id,)
        ).fetchone()
        return row is not None

    def save_opportunity(self, variant) -> int:
        """Save a generated content opportunity (deduped by content hash)."""
        import hashlib
        conn = get_connection()
        
        # Content fingerprint — prevent duplicate generation
        content_hash = hashlib.sha256(
            f"{variant.genome_id}:{variant.variant_type}:{variant.content}".encode()
        ).hexdigest()[:16]
        
        # Check if this exact content already exists
        existing = conn.execute(
            "SELECT 1 FROM pipeline_opportunities WHERE content_hash = ? LIMIT 1",
            (content_hash,)
        ).fetchone()
        if existing:
            return -1  # Skip — already generated
        
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("""
            INSERT INTO pipeline_opportunities
                (genome_id, variant_type, title, content, content_hash, score, score_breakdown, hook, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            variant.genome_id,
            variant.variant_type,
            variant.title,
            variant.content,
            content_hash,
            variant.score,
            json.dumps(variant.score_breakdown),
            variant.hook,
            now,
        ))
        # Increment opportunity count on the genome
        conn.execute(
            f"UPDATE {self.TABLE_NAME} SET opportunity_count = opportunity_count + 1 WHERE post_id = ?",
            (variant.genome_id,)
        )
        conn.commit()
        return cursor.lastrowid

    def get_opportunities(self, limit: int = 10, unseen_only: bool = True) -> list[dict]:
        """Get content opportunities, ranked by score."""
        conn = get_connection()
        if unseen_only:
            rows = conn.execute("""
                SELECT po.*, vg.hook_type, vg.structural_pattern, vg.engagement_score as genome_engagement
                FROM pipeline_opportunities po
                LEFT JOIN viral_genomes vg ON po.genome_id = vg.post_id
                WHERE po.viewed = 0 AND po.dismissed = 0
                ORDER BY po.score DESC
                LIMIT ?
            """, (limit,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT po.*, vg.hook_type, vg.structural_pattern, vg.engagement_score as genome_engagement
                FROM pipeline_opportunities po
                LEFT JOIN viral_genomes vg ON po.genome_id = vg.post_id
                ORDER BY po.created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def mark_viewed(self, opportunity_id: int) -> None:
        """Mark an opportunity as viewed."""
        conn = get_connection()
        conn.execute(
            "UPDATE pipeline_opportunities SET viewed = 1 WHERE id = ?",
            (opportunity_id,)
        )
        conn.commit()

    def dismiss_opportunity(self, opportunity_id: int) -> None:
        """Dismiss an opportunity."""
        conn = get_connection()
        conn.execute(
            "UPDATE pipeline_opportunities SET dismissed = 1 WHERE id = ?",
            (opportunity_id,)
        )
        conn.commit()

    def get_stats(self) -> dict:
        """Get pipeline statistics."""
        conn = get_connection()
        genomes = conn.execute(f"SELECT COUNT(*) as n FROM {self.TABLE_NAME}").fetchone()["n"]
        opportunities = conn.execute("SELECT COUNT(*) as n FROM pipeline_opportunities").fetchone()["n"]
        unseen = conn.execute(
            "SELECT COUNT(*) as n FROM pipeline_opportunities WHERE viewed = 0 AND dismissed = 0"
        ).fetchone()["n"]

        top_genome = conn.execute(
            f"SELECT post_id, hook_type, engagement_score FROM {self.TABLE_NAME} ORDER BY engagement_score DESC LIMIT 1"
        ).fetchone()

        hook_types = conn.execute(
            f"SELECT hook_type, COUNT(*) as n FROM {self.TABLE_NAME} GROUP BY hook_type ORDER BY n DESC"
        ).fetchall()

        return {
            "total_genomes": genomes,
            "total_opportunities": opportunities,
            "unseen_opportunities": unseen,
            "top_genome": dict(top_genome) if top_genome else None,
            "hook_distribution": {r["hook_type"]: r["n"] for r in hook_types},
        }

    def get_opportunity_by_id(self, opportunity_id: int) -> dict | None:
        """Get a single opportunity by ID."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM pipeline_opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_opportunities_for_genome(self, post_id: str, limit: int = 5) -> list[dict]:
        """Get opportunities for a specific genome, sorted by score desc."""
        conn = get_connection()
        rows = conn.execute(
            """SELECT o.* FROM pipeline_opportunities o
               WHERE o.genome_id = ?
               ORDER BY o.score DESC
               LIMIT ?""",
            (post_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def dismiss_all_unseen(self, below_score: float | None = None) -> int:
        """Batch-dismiss all unseen opportunities in a single SQL statement."""
        conn = get_connection()
        if below_score is not None:
            result = conn.execute(
                """UPDATE pipeline_opportunities
                   SET dismissed = 1
                   WHERE viewed = 0 AND dismissed = 0 AND score < ?""",
                (below_score,),
            )
        else:
            result = conn.execute(
                """UPDATE pipeline_opportunities
                   SET dismissed = 1
                   WHERE viewed = 0 AND dismissed = 0"""
            )
        conn.commit()
        return result.rowcount

    def _row_to_genome(self, row) -> ViralGenome:
        """Convert a database row to a ViralGenome object."""
        return ViralGenome(
            post_id=row["post_id"],
            hook_type=row["hook_type"],
            hook_text=row["hook_text"],
            emotional_arc=json.loads(row["emotional_arc"]),
            structural_pattern=row["structural_pattern"],
            key_phrases=json.loads(row["key_phrases"]),
            content_length_words=row["content_length_words"],
            platform_signals=json.loads(row["platform_signals"]),
            engagement_score=row["engagement_score"],
        )
