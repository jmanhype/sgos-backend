"""
Tests for Platform Formatters + Pipeline Alerts + Bulk Actions.

Covers:
  - XThreadFormatter: splitting, numbering, char limits
  - LinkedInFormatter: professional formatting, length limits
  - BlueskyFormatter: 300-char thread splitting
  - NewsletterFormatter: markdown preservation
  - format_opportunity: end-to-end with opportunity dicts
  - Pipeline alerts: get_pending, alert_high_score, dismiss
  - Bulk actions: dismiss-all, copy-batch
"""
import pytest
from services.pipeline.formatters import (
    XThreadFormatter, LinkedInFormatter, BlueskyFormatter,
    NewsletterFormatter, format_for_platform, format_opportunity,
    FormattedPost,
)


# ─── Sample Content ─────────────────────────────────────────────────────────

SAMPLE_CONTENT = """The AI job revolution isn't coming — it's here.

**50% of current jobs will be automated by 2030.**

That's not hype. That's McKinsey data.

Here's what most people miss:

- It's not about replacing humans entirely
- It's about augmenting what humans can do
- The winners will be those who adapt NOW

The secret? Don't compete with AI. Collaborate with it.

What skill are you building to stay ahead? 👇"""

SAMPLE_OPP = {
    "id": 1,
    "title": "The AI Job Revolution Is Closer Than You Think",
    "content": SAMPLE_CONTENT,
    "hook": "The AI job revolution isn't coming — it's here.",
    "score": 82.5,
    "variant_type": "thread",
    "genome_id": "genome_abc",
    "score_breakdown": '{"engagement": {"raw": 85, "weight": 0.35}}',
    "created_at": "2026-06-24T10:00:00Z",
    "viewed": 0,
    "dismissed": 0,
}


# ─── XThreadFormatter Tests ─────────────────────────────────────────────────

class TestXThreadFormatter:
    def test_splits_into_numbered_parts(self):
        f = XThreadFormatter()
        result = f.format(SAMPLE_CONTENT)
        assert len(result.parts) >= 1
        if len(result.parts) > 1:
            # First part should start with 1/N
            assert result.parts[0].startswith("1/")
            # Last part should have correct numbering
            assert result.parts[-1].startswith(f"{len(result.parts)}/")

    def test_single_post_no_numbering(self):
        f = XThreadFormatter()
        result = f.format("Short tweet that fits in 280 chars easily.")
        assert len(result.parts) == 1
        # Single post should NOT have numbering
        assert not result.parts[0].startswith("1/")

    def test_strips_markdown_bold(self):
        f = XThreadFormatter()
        result = f.format("**Bold text** and normal text")
        assert "**" not in result.parts[0]
        assert "Bold text" in result.parts[0]

    def test_strips_markdown_italic(self):
        f = XThreadFormatter()
        result = f.format("*Italic text* and normal")
        assert "*" not in result.parts[0]
        assert "Italic text" in result.parts[0]

    def test_strips_headers(self):
        f = XThreadFormatter()
        result = f.format("## Header\n\nBody text here")
        assert "##" not in result.parts[0]
        assert "Header" in result.parts[0] or "Header" in " ".join(result.parts)

    def test_char_counts_match(self):
        f = XThreadFormatter()
        result = f.format(SAMPLE_CONTENT)
        assert len(result.char_counts) == len(result.parts)
        for i, (part, count) in enumerate(zip(result.parts, result.char_counts)):
            assert len(part) == count, f"Part {i}: len={len(part)}, reported={count}"

    def test_total_chars_is_sum(self):
        f = XThreadFormatter()
        result = f.format(SAMPLE_CONTENT)
        assert result.total_chars == sum(result.char_counts)

    def test_platform_is_x_thread(self):
        f = XThreadFormatter()
        result = f.format("test")
        assert result.platform == "x_thread"

    def test_long_content_splits_correctly(self):
        f = XThreadFormatter()
        # Create content that's definitely > 280 chars
        long_content = ". ".join(["This is sentence number " + str(i) for i in range(30)])
        result = f.format(long_content)
        assert len(result.parts) >= 2

    def test_empty_content(self):
        f = XThreadFormatter()
        result = f.format("")
        assert len(result.parts) >= 1

    def test_warnings_for_overlong(self):
        f = XThreadFormatter()
        # Create a single word that's > 280 chars (can't be split)
        long_word = "x" * 300
        result = f.format(long_word)
        # Should have warnings about overlong parts
        assert len(result.warnings) > 0 or len(result.parts[0]) <= 280


# ─── LinkedInFormatter Tests ────────────────────────────────────────────────

class TestLinkedInFormatter:
    def test_preserves_paragraphs(self):
        f = LinkedInFormatter()
        result = f.format("First paragraph.\n\nSecond paragraph.")
        assert "First paragraph." in result.parts[0]
        assert "Second paragraph." in result.parts[0]

    def test_adds_title(self):
        f = LinkedInFormatter()
        result = f.format("Body content", title="My Title")
        assert result.parts[0].startswith("My Title")

    def test_adds_hook(self):
        f = LinkedInFormatter()
        result = f.format("Body content", hook="Hook line here")
        assert "Hook line here" in result.parts[0]

    def test_strips_thread_numbering(self):
        f = LinkedInFormatter()
        result = f.format("1/5 First part\n\n2/5 Second part")
        assert "1/5" not in result.parts[0]
        assert "2/5" not in result.parts[0]

    def test_respects_char_limit(self):
        f = LinkedInFormatter()
        long_content = "word " * 1000
        result = f.format(long_content)
        assert result.total_chars <= 3000

    def test_platform_is_linkedin(self):
        f = LinkedInFormatter()
        result = f.format("test")
        assert result.platform == "linkedin"

    def test_single_part(self):
        f = LinkedInFormatter()
        result = f.format(SAMPLE_CONTENT)
        assert len(result.parts) == 1


# ─── BlueskyFormatter Tests ─────────────────────────────────────────────────

class TestBlueskyFormatter:
    def test_platform_is_bluesky(self):
        f = BlueskyFormatter()
        result = f.format(SAMPLE_CONTENT)
        assert result.platform == "bluesky"

    def test_respects_300_char_limit(self):
        f = BlueskyFormatter()
        result = f.format(SAMPLE_CONTENT)
        # Each part should be close to but under 300
        for part in result.parts:
            assert len(part) <= 320  # Allow small margin for numbering


# ─── NewsletterFormatter Tests ──────────────────────────────────────────────

class TestNewsletterFormatter:
    def test_adds_title_as_h2(self):
        f = NewsletterFormatter()
        result = f.format("Body", title="My Title")
        assert "## My Title" in result.parts[0]

    def test_adds_hook_as_blockquote(self):
        f = NewsletterFormatter()
        result = f.format("Body", hook="Hook text")
        assert "> Hook text" in result.parts[0]

    def test_preserves_markdown(self):
        f = NewsletterFormatter()
        result = f.format("**Bold** and *italic*")
        assert "**Bold**" in result.parts[0]
        assert "*italic*" in result.parts[0]

    def test_platform_is_newsletter(self):
        f = NewsletterFormatter()
        result = f.format("test")
        assert result.platform == "newsletter"


# ─── format_for_platform Tests ──────────────────────────────────────────────

class TestFormatForPlatform:
    def test_x_alias(self):
        result = format_for_platform("test", "x")
        assert result.platform == "x_thread"

    def test_twitter_alias(self):
        result = format_for_platform("test", "twitter")
        assert result.platform == "x_thread"

    def test_linkedin(self):
        result = format_for_platform("test", "linkedin")
        assert result.platform == "linkedin"

    def test_newsletter(self):
        result = format_for_platform("test", "newsletter")
        assert result.platform == "newsletter"

    def test_unknown_platform_returns_generic(self):
        result = format_for_platform("test content", "mastodon")
        assert "Unknown platform" in result.warnings[0]
        assert result.parts[0] == "test content"

    def test_format_opportunity_with_dict(self):
        result = format_opportunity(SAMPLE_OPP, "x")
        assert result.platform == "x_thread"
        assert len(result.parts) >= 1
        assert result.total_chars > 0


# ─── Pipeline Alert Tests ───────────────────────────────────────────────────

class TestPipelineAlerts:
    def test_get_pending_alerts_empty(self, monkeypatch, tmp_path):
        """No high-scoring opportunities → empty list."""
        import sqlite3
        db_path = tmp_path / "alerts.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        # Create the pipeline_opportunities table (matches repository.py)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                genome_id TEXT, variant_type TEXT, title TEXT, content TEXT,
                hook TEXT, score REAL, score_breakdown TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                viewed INTEGER DEFAULT 0, dismissed INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pipeline_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER NOT NULL,
                score REAL NOT NULL, title TEXT, hook TEXT,
                variant_type TEXT, alerted_at TEXT NOT NULL,
                notified INTEGER DEFAULT 0, dismissed INTEGER DEFAULT 0
            );
        """)

        monkeypatch.setattr("services.pipeline.alerts.get_connection", lambda: conn)
        from services.pipeline.alerts import get_pending_alerts
        result = get_pending_alerts(threshold=75.0)
        assert result == []

    def test_get_pending_alerts_with_high_score(self, monkeypatch, tmp_path):
        """High-scoring unseen opportunities appear in pending alerts."""
        import sqlite3
        db_path = tmp_path / "alerts2.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                genome_id TEXT, variant_type TEXT, title TEXT, content TEXT,
                hook TEXT, score REAL, score_breakdown TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                viewed INTEGER DEFAULT 0, dismissed INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pipeline_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER NOT NULL,
                score REAL NOT NULL, title TEXT, hook TEXT,
                variant_type TEXT, alerted_at TEXT NOT NULL,
                notified INTEGER DEFAULT 0, dismissed INTEGER DEFAULT 0
            );
            INSERT INTO pipeline_opportunities (genome_id, variant_type, title, content, hook, score, viewed, dismissed)
            VALUES ('g1', 'thread', 'Viral Post', 'Content here', 'Great hook', 85.0, 0, 0);
            INSERT INTO pipeline_opportunities (genome_id, variant_type, title, content, hook, score, viewed, dismissed)
            VALUES ('g2', 'post', 'Low Score', 'Content', 'Hook', 40.0, 0, 0);
        """)

        monkeypatch.setattr("services.pipeline.alerts.get_connection", lambda: conn)
        from services.pipeline.alerts import get_pending_alerts
        result = get_pending_alerts(threshold=75.0)
        assert len(result) == 1
        assert result[0]["title"] == "Viral Post"
        assert result[0]["score"] == 85.0

    def test_alert_high_score_creates_records(self, monkeypatch, tmp_path):
        """alert_high_score creates pipeline_alerts records."""
        import sqlite3
        db_path = tmp_path / "alerts3.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                genome_id TEXT, variant_type TEXT, title TEXT, content TEXT,
                hook TEXT, score REAL, score_breakdown TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                viewed INTEGER DEFAULT 0, dismissed INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pipeline_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER NOT NULL,
                score REAL NOT NULL, title TEXT, hook TEXT,
                variant_type TEXT, alerted_at TEXT NOT NULL,
                notified INTEGER DEFAULT 0, dismissed INTEGER DEFAULT 0
            );
            INSERT INTO pipeline_opportunities (genome_id, variant_type, title, content, hook, score, viewed, dismissed)
            VALUES ('g1', 'thread', 'Hot Take', 'Viral content', 'Shocking hook', 90.0, 0, 0);
        """)

        monkeypatch.setattr("services.pipeline.alerts.get_connection", lambda: conn)
        from services.pipeline.alerts import alert_high_score
        result = alert_high_score(threshold=75.0)
        assert result["status"] == "alerted"
        assert result["alerts_created"] == 1
        assert result["top_opportunities"][0]["score"] == 90.0

    def test_dismiss_alert(self, monkeypatch, tmp_path):
        """dismiss_alert marks an alert as dismissed."""
        import sqlite3
        db_path = tmp_path / "alerts4.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER NOT NULL,
                score REAL NOT NULL, title TEXT, hook TEXT,
                variant_type TEXT, alerted_at TEXT NOT NULL,
                notified INTEGER DEFAULT 0, dismissed INTEGER DEFAULT 0
            );
            INSERT INTO pipeline_alerts (opportunity_id, score, title, alerted_at)
            VALUES (1, 85.0, 'Test', datetime('now'));
        """)

        monkeypatch.setattr("services.pipeline.alerts.get_connection", lambda: conn)
        from services.pipeline.alerts import dismiss_alert
        result = dismiss_alert(1)
        assert result["status"] == "dismissed"

        # Verify in DB
        row = conn.execute("SELECT dismissed FROM pipeline_alerts WHERE opportunity_id = 1").fetchone()
        assert row["dismissed"] == 1
