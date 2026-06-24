"""
SGOS Backend - Voice Profile Engine
Analyzes a corpus of text to extract writing style patterns.
Used to train the content engine to write in the user's voice.
"""
import re
import json
from collections import Counter
from datetime import datetime, timezone

from database import get_connection


def init_voice_tables():
    """Create voice profile tables if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS voice_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            sample_count INTEGER DEFAULT 0,
            avg_word_count REAL DEFAULT 0,
            avg_sentence_length REAL DEFAULT 0,
            vocabulary_richness REAL DEFAULT 0,
            common_words TEXT,  -- JSON array
            punctuation_style TEXT,  -- JSON object
            tone_markers TEXT,  -- JSON array
            hook_patterns TEXT,  -- JSON array
            closing_patterns TEXT,  -- JSON array
            formatting_prefs TEXT,  -- JSON object
            raw_samples TEXT,  -- JSON array of original texts
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS voice_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_name TEXT NOT NULL,
            source TEXT,  -- 'twitter', 'linkedin', 'manual', etc.
            content TEXT NOT NULL,
            word_count INTEGER,
            char_count INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (profile_name) REFERENCES voice_profiles(name)
        )
    """)

    conn.commit()
    conn.close()


def analyze_text(text: str) -> dict:
    """
    Analyze a single text for style patterns.
    Returns a dict of style metrics.
    """
    words = re.findall(r'\b\w+\b', text.lower())
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    # Basic metrics
    word_count = len(words)
    char_count = len(text)
    sentence_count = max(len(sentences), 1)
    avg_word_length = sum(len(w) for w in words) / max(word_count, 1)
    avg_sentence_length = word_count / sentence_count

    # Vocabulary richness (type-token ratio)
    unique_words = set(words)
    vocabulary_richness = len(unique_words) / max(word_count, 1)

    # Common words (excluding stopwords)
    stopwords = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'is', 'it', 'this', 'that', 'are', 'was',
        'be', 'has', 'had', 'have', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'can', 'not', 'no', 'so', 'if',
        'as', 'i', 'you', 'he', 'she', 'we', 'they', 'me', 'my', 'your',
        'his', 'her', 'our', 'their', 'what', 'which', 'who', 'when', 'where',
        'how', 'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
        'some', 'such', 'than', 'too', 'very', 'just', 'about', 'up', 'out',
        'one', 'two', 'been', 'being', 'into', 'through', 'during', 'before',
        'after', 'above', 'below', 'between', 'under', 'again', 'then', 'once',
    }
    meaningful_words = [w for w in words if w not in stopwords and len(w) > 2]
    word_freq = Counter(meaningful_words)
    common_words = [w for w, _ in word_freq.most_common(20)]

    # Punctuation style
    punctuation = {
        'em_dashes': text.count('—') + text.count('--'),
        'ellipses': text.count('...'),
        'exclamation': text.count('!'),
        'question': text.count('?'),
        'colons': text.count(':'),
        'semicolons': text.count(';'),
        'parentheses': text.count('(') + text.count(')'),
        'quotes': text.count('"') + text.count("'"),
        'line_breaks': text.count('\n'),
    }

    # Tone markers
    tone_indicators = {
        'bold_statements': len(re.findall(r'\b(always|never|every|none|all|nothing)\b', text, re.I)),
        'questions': text.count('?'),
        'personal': len(re.findall(r'\b(I|my|me|mine)\b', text)),
        'direct_address': len(re.findall(r'\b(you|your|yours)\b', text)),
        'numbers': len(re.findall(r'\b\d+\b', text)),
        'abbreviations': len(re.findall(r'\b[A-Z]{2,}\b', text)),
        'hashtags': len(re.findall(r'#\w+', text)),
        'mentions': len(re.findall(r'@\w+', text)),
    }

    # Hook patterns (first sentence analysis)
    first_sentence = sentences[0] if sentences else ""
    hook_type = "unknown"
    if first_sentence.endswith('?'):
        hook_type = "question"
    elif re.match(r'^\d', first_sentence):
        hook_type = "number_lead"
    elif any(w in first_sentence.lower() for w in ['most', 'biggest', 'worst', 'best', '#1']):
        hook_type = "superlative"
    elif '—' in first_sentence or '--' in first_sentence:
        hook_type = "dramatic_pause"
    elif len(first_sentence.split()) <= 8:
        hook_type = "short_punch"
    elif first_sentence.startswith(('Here', 'This', 'Let')):
        hook_type = "declarative"

    # Closing patterns (last sentence analysis)
    last_sentence = sentences[-1] if sentences else ""
    close_type = "unknown"
    if last_sentence.endswith('?'):
        close_type = "question"
    elif last_sentence.endswith('!'):
        close_type = "exclamation"
    elif '...' in last_sentence:
        close_type = "trailing"
    elif any(w in last_sentence.lower() for w in ['follow', 'subscribe', 'check', 'link']):
        close_type = "cta"
    elif len(last_sentence.split()) <= 6:
        close_type = "short_punch"

    return {
        'word_count': word_count,
        'char_count': char_count,
        'avg_word_length': round(avg_word_length, 1),
        'avg_sentence_length': round(avg_sentence_length, 1),
        'vocabulary_richness': round(vocabulary_richness, 3),
        'common_words': common_words,
        'punctuation': punctuation,
        'tone_markers': tone_indicators,
        'hook_type': hook_type,
        'close_type': close_type,
    }


def build_voice_profile(name: str, texts: list[dict], description: str = "") -> dict:
    """
    Build a voice profile from a list of text samples.
    Each sample: {'content': str, 'source': str}
    Returns the aggregated profile.
    """
    init_voice_tables()
    conn = get_connection()

    if not texts:
        conn.close()
        return {"error": "No texts provided"}

    # Analyze each sample
    analyses = []
    for sample in texts:
        content = sample.get('content', '')
        if len(content) < 20:
            continue
        analysis = analyze_text(content)
        analyses.append(analysis)

        # Store sample
        conn.execute(
            "INSERT OR IGNORE INTO voice_samples (profile_name, source, content, word_count, char_count) VALUES (?, ?, ?, ?, ?)",
            (name, sample.get('source', 'unknown'), content, analysis['word_count'], analysis['char_count'])
        )

    if not analyses:
        conn.close()
        return {"error": "No valid texts to analyze"}

    # Aggregate metrics
    sample_count = len(analyses)
    avg_word_count = sum(a['word_count'] for a in analyses) / sample_count
    avg_sentence_length = sum(a['avg_sentence_length'] for a in analyses) / sample_count
    vocab_richness = sum(a['vocabulary_richness'] for a in analyses) / sample_count

    # Merge common words
    all_common = []
    for a in analyses:
        all_common.extend(a['common_words'][:10])
    common_freq = Counter(all_common)
    merged_common = [w for w, _ in common_freq.most_common(30)]

    # Aggregate punctuation
    punct_totals = {}
    for a in analyses:
        for k, v in a['punctuation'].items():
            punct_totals[k] = punct_totals.get(k, 0) + v

    # Aggregate tone
    tone_totals = {}
    for a in analyses:
        for k, v in a['tone_markers'].items():
            tone_totals[k] = tone_totals.get(k, 0) + v

    # Hook/close patterns
    hook_types = Counter(a['hook_type'] for a in analyses)
    close_types = Counter(a['close_type'] for a in analyses)

    # Build formatting preferences
    formatting = {
        'uses_em_dashes': punct_totals.get('em_dashes', 0) > sample_count * 0.3,
        'uses_ellipses': punct_totals.get('ellipses', 0) > sample_count * 0.2,
        'uses_numbers': tone_totals.get('numbers', 0) > sample_count * 0.5,
        'uses_hashtags': punct_totals.get('hashtags', 0) > sample_count * 0.3,
        'uses_line_breaks': punct_totals.get('line_breaks', 0) > sample_count * 2,
        'asks_questions': punct_totals.get('question', 0) > sample_count * 0.3,
        'uses_exclamation': punct_totals.get('exclamation', 0) > sample_count * 0.3,
    }

    profile = {
        'name': name,
        'description': description,
        'sample_count': sample_count,
        'avg_word_count': round(avg_word_count, 1),
        'avg_sentence_length': round(avg_sentence_length, 1),
        'vocabulary_richness': round(vocab_richness, 3),
        'common_words': merged_common,
        'punctuation_style': punct_totals,
        'tone_markers': tone_totals,
        'hook_patterns': dict(hook_types.most_common(5)),
        'closing_patterns': dict(close_types.most_common(5)),
        'formatting_prefs': formatting,
    }

    # Upsert profile
    conn.execute("""
        INSERT INTO voice_profiles (name, description, sample_count, avg_word_count, avg_sentence_length,
            vocabulary_richness, common_words, punctuation_style, tone_markers, hook_patterns,
            closing_patterns, formatting_prefs, raw_samples, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(name) DO UPDATE SET
            description=excluded.description,
            sample_count=excluded.sample_count,
            avg_word_count=excluded.avg_word_count,
            avg_sentence_length=excluded.avg_sentence_length,
            vocabulary_richness=excluded.vocabulary_richness,
            common_words=excluded.common_words,
            punctuation_style=excluded.punctuation_style,
            tone_markers=excluded.tone_markers,
            hook_patterns=excluded.hook_patterns,
            closing_patterns=excluded.closing_patterns,
            formatting_prefs=excluded.formatting_prefs,
            raw_samples=excluded.raw_samples,
            updated_at=datetime('now')
    """, (
        name, description, sample_count, round(avg_word_count, 1),
        round(avg_sentence_length, 1), round(vocab_richness, 3),
        json.dumps(merged_common), json.dumps(punct_totals),
        json.dumps(tone_totals), json.dumps(dict(hook_types.most_common(5))),
        json.dumps(dict(close_types.most_common(5))), json.dumps(formatting),
        json.dumps([s.get('content', '')[:500] for s in texts[:50]]),
    ))

    conn.commit()
    conn.close()

    return profile


def get_voice_profile(name: str) -> dict | None:
    """Retrieve a stored voice profile."""
    init_voice_tables()
    conn = get_connection()
    row = conn.execute("SELECT * FROM voice_profiles WHERE name=?", (name,)).fetchone()
    conn.close()

    if not row:
        return None

    return {
        'name': row['name'],
        'description': row['description'],
        'sample_count': row['sample_count'],
        'avg_word_count': row['avg_word_count'],
        'avg_sentence_length': row['avg_sentence_length'],
        'vocabulary_richness': row['vocabulary_richness'],
        'common_words': json.loads(row['common_words'] or '[]'),
        'punctuation_style': json.loads(row['punctuation_style'] or '{}'),
        'tone_markers': json.loads(row['tone_markers'] or '{}'),
        'hook_patterns': json.loads(row['hook_patterns'] or '{}'),
        'closing_patterns': json.loads(row['closing_patterns'] or '{}'),
        'formatting_prefs': json.loads(row['formatting_prefs'] or '{}'),
        'updated_at': row['updated_at'],
    }


def generate_voice_prompt(profile: dict) -> str:
    """
    Generate a system prompt fragment that encodes the user's voice.
    This gets injected into the content generation pipeline.
    """
    if not profile:
        return ""

    fmt = profile.get('formatting_prefs', {})
    hooks = profile.get('hook_patterns', {})
    closes = profile.get('closing_patterns', {})
    common = profile.get('common_words', [])[:10]

    top_hook = max(hooks, key=hooks.get) if hooks else 'short_punch'
    top_close = max(closes, key=closes.get) if closes else 'short_punch'

    hook_instructions = {
        'question': 'Open with a provocative question that creates a curiosity gap',
        'number_lead': 'Lead with a specific number or statistic',
        'superlative': 'Open with a bold superlative claim',
        'dramatic_pause': 'Use an em dash for dramatic effect in the opening',
        'short_punch': 'Open with a short, punchy statement (under 8 words)',
        'declarative': 'Open with a clear declarative statement',
    }

    close_instructions = {
        'question': 'Close with a thought-provoking question',
        'exclamation': 'Close with energy and emphasis',
        'trailing': 'Close with an ellipsis for contemplation',
        'cta': 'Close with a clear call-to-action',
        'short_punch': 'Close with a short, punchy one-liner',
    }

    parts = [
        f"WRITING STYLE PROFILE:",
        f"- Average post length: {profile.get('avg_word_count', 100):.0f} words",
        f"- Sentence length: {profile.get('avg_sentence_length', 15):.0f} words avg",
        f"- Vocabulary richness: {profile.get('vocabulary_richness', 0.6):.2f}",
        f"- Favorite words/themes: {', '.join(common)}",
        "",
        f"HOOK STYLE: {hook_instructions.get(top_hook, 'Open with impact')}",
        f"CLOSE STYLE: {close_instructions.get(top_close, 'Close with punch')}",
        "",
    ]

    if fmt.get('uses_em_dashes'):
        parts.append("- Use em dashes (—) for emphasis and pauses")
    if fmt.get('uses_line_breaks'):
        parts.append("- Use frequent line breaks for readability")
    if fmt.get('uses_numbers'):
        parts.append("- Include specific numbers and data points")
    if fmt.get('asks_questions'):
        parts.append("- Weave rhetorical questions throughout")
    if fmt.get('uses_exclamation'):
        parts.append("- Use exclamation marks for energy")
    if not fmt.get('uses_hashtags'):
        parts.append("- Minimize hashtags (use sparingly or not at all)")

    return "\n".join(parts)


def list_profiles() -> list[dict]:
    """List all voice profiles."""
    init_voice_tables()
    conn = get_connection()
    rows = conn.execute("SELECT name, description, sample_count, updated_at FROM voice_profiles ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    # Test with sample texts
    test_texts = [
        {"content": "Most people think AI is about automation. It's not. It's about amplification — taking what you're already good at and making it 10× better.", "source": "test"},
        {"content": "I built a content engine that scrapes viral posts, detects outliers with z-scores, and generates 5 content formats from a single click. Here's the architecture.", "source": "test"},
        {"content": "The difference between a $0 creator and a $10K/mo creator isn't talent. It's systems. Systems that run while you sleep.", "source": "test"},
    ]

    profile = build_voice_profile("test", test_texts, "Test profile")
    print(json.dumps(profile, indent=2))
    print("\n--- Voice Prompt ---")
    print(generate_voice_prompt(profile))
