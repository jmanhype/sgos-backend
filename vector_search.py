"""
SGOS Backend - Lightweight Vector Search
Uses TF-IDF + cosine similarity for semantic post matching.
No external embedding API needed — runs entirely local.
"""
import math
import re
import json
from collections import Counter

from database import get_connection


def init_vector_tables():
    """Create vector/embedding tables."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS post_embeddings (
            post_id TEXT PRIMARY KEY,
            title TEXT,
            content TEXT,
            tfidf_vector TEXT,  -- JSON: {"word": weight, ...}
            norm REAL DEFAULT 0,
            platform TEXT,
            subreddit TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (post_id) REFERENCES posts(platform_id)
        )
    """)

    conn.commit()
    conn.close()


def tokenize(text: str) -> list[str]:
    """Simple tokenizer: lowercase, strip punctuation, remove stopwords."""
    text = text.lower()
    words = re.findall(r'\b[a-z][a-z0-9_\-]{1,}\b', text)

    stopwords = {
        'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had',
        'her', 'was', 'one', 'our', 'out', 'has', 'have', 'been', 'some',
        'them', 'than', 'its', 'over', 'such', 'that', 'this', 'with', 'will',
        'each', 'from', 'they', 'into', 'more', 'other', 'about', 'many',
        'then', 'these', 'would', 'there', 'their', 'what', 'which', 'when',
        'where', 'who', 'how', 'just', 'like', 'also', 'very', 'most',
        'after', 'before', 'because', 'between', 'does', 'did', 'doing',
        'here', 'only', 'those', 'should', 'could', 'might', 'must',
        'http', 'https', 'www', 'com', 'org', 'net', 'reddit', 'hackernews',
    }

    return [w for w in words if w not in stopwords and len(w) > 2]


def compute_tfidf(posts: list[dict]) -> dict:
    """
    Compute TF-IDF vectors for a corpus of posts.
    Returns dict of {post_id: {word: tfidf_weight}}
    """
    # Document frequency
    doc_freq = Counter()
    post_tokens = {}

    for post in posts:
        pid = post.get('platform_id') or post.get('id')
        text = f"{post.get('title', '')} {post.get('content', '')}"
        tokens = tokenize(text)
        post_tokens[pid] = tokens

        unique_tokens = set(tokens)
        for token in unique_tokens:
            doc_freq[token] += 1

    n_docs = len(posts)
    vectors = {}

    for pid, tokens in post_tokens.items():
        if not tokens:
            continue

        # Term frequency
        tf = Counter(tokens)
        max_tf = max(tf.values()) if tf else 1

        # TF-IDF
        vector = {}
        for word, count in tf.items():
            tf_val = count / max_tf  # Normalized TF
            df_val = doc_freq.get(word, 1)
            idf_val = math.log(n_docs / (1 + df_val)) + 1  # Smoothed IDF
            vector[word] = round(tf_val * idf_val, 4)

        vectors[pid] = vector

    return vectors


def cosine_similarity(vec_a: dict, vec_b: dict) -> float:
    """Compute cosine similarity between two sparse vectors."""
    # Dot product
    common_keys = set(vec_a.keys()) & set(vec_b.keys())
    dot = sum(vec_a[k] * vec_b[k] for k in common_keys)

    # Magnitudes
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


def build_index(platform: str = None, rebuild: bool = False) -> dict:
    """
    Build/update the TF-IDF index for all posts.
    """
    init_vector_tables()
    conn = get_connection()

    # Get all posts
    query = "SELECT * FROM posts"
    params = []
    if platform:
        query += " WHERE platform=?"
        params.append(platform)

    rows = conn.execute(query, params).fetchall()
    posts = [dict(r) for r in rows]

    if not posts:
        conn.close()
        return {"error": "No posts to index"}

    # Check what's already indexed
    if not rebuild:
        indexed = conn.execute("SELECT post_id FROM post_embeddings").fetchall()
        indexed_ids = {r['post_id'] for r in indexed}
        posts = [p for p in posts if p.get('platform_id') not in indexed_ids]

    if not posts:
        conn.close()
        return {"status": "up_to_date", "total_indexed": len(indexed_ids) if not rebuild else 0}

    # Compute TF-IDF vectors
    vectors = compute_tfidf(posts)

    # Store embeddings
    inserted = 0
    for pid, vector in vectors.items():
        post = next((p for p in posts if p.get('platform_id') == pid), None)
        if not post:
            continue

        norm = math.sqrt(sum(v * v for v in vector.values()))

        conn.execute("""
            INSERT OR REPLACE INTO post_embeddings (post_id, title, content, tfidf_vector, norm, platform, subreddit)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            pid,
            post.get('title', ''),
            post.get('content', '')[:500],
            json.dumps(vector),
            norm,
            post.get('platform', ''),
            post.get('subreddit', ''),
        ))
        inserted += 1

    conn.commit()
    total = conn.execute("SELECT COUNT(*) as cnt FROM post_embeddings").fetchone()['cnt']
    conn.close()

    return {
        "status": "built",
        "newly_indexed": inserted,
        "total_indexed": total,
    }


def search_similar(query_text: str, limit: int = 10, platform: str = None) -> list[dict]:
    """
    Find posts similar to a query text using TF-IDF cosine similarity.
    Optimized: pre-filters by keyword overlap to avoid full O(n) scan.
    """
    init_vector_tables()
    conn = get_connection()

    # Compute query vector first
    query_tokens = tokenize(query_text)
    if not query_tokens:
        conn.close()
        return []

    query_tf = Counter(query_tokens)
    max_tf = max(query_tf.values()) if query_tf else 1
    query_vector = {word: count / max_tf for word, count in query_tf.items()}
    query_keys = set(query_vector.keys())

    # Load embeddings
    query_sql = "SELECT * FROM post_embeddings"
    params = []
    if platform:
        query_sql += " WHERE platform=?"
        params.append(platform)

    rows = conn.execute(query_sql, params).fetchall()

    if not rows:
        conn.close()
        return []

    # Pre-filter: only compute cosine for docs sharing at least 1 query token
    results = []
    for row in rows:
        doc_vector = json.loads(row['tfidf_vector'] or '{}')
        doc_keys = set(doc_vector.keys())
        
        # Skip docs with zero keyword overlap (O(1) set intersection check)
        if not query_keys & doc_keys:
            continue
        
        sim = cosine_similarity(query_vector, doc_vector)
        if sim > 0.05:
            results.append({
                'post_id': row['post_id'],
                'title': row['title'],
                'content': row['content'],
                'platform': row['platform'],
                'subreddit': row['subreddit'],
                'similarity': round(sim, 4),
            })

    conn.close()
    results.sort(key=lambda x: x['similarity'], reverse=True)
    return results[:limit]


def find_similar_posts(post_id: str, limit: int = 5) -> list[dict]:
    """
    Find posts similar to a specific post (by platform_id).
    """
    init_vector_tables()
    conn = get_connection()

    # Get the target post's vector
    row = conn.execute(
        "SELECT tfidf_vector FROM post_embeddings WHERE post_id=?", (post_id,)
    ).fetchone()

    if not row:
        conn.close()
        return []

    target_vector = json.loads(row['tfidf_vector'])

    # Compare against all other posts
    all_rows = conn.execute(
        "SELECT * FROM post_embeddings WHERE post_id != ?", (post_id,)
    ).fetchall()

    results = []
    for r in all_rows:
        doc_vector = json.loads(r['tfidf_vector'] or '{}')
        sim = cosine_similarity(target_vector, doc_vector)
        if sim > 0.1:
            results.append({
                'post_id': r['post_id'],
                'title': r['title'],
                'content': r['content'],
                'platform': r['platform'],
                'subreddit': r['subreddit'],
                'similarity': round(sim, 4),
            })

    conn.close()
    results.sort(key=lambda x: x['similarity'], reverse=True)
    return results[:limit]


if __name__ == "__main__":
    # Build index
    print("Building TF-IDF index...")
    result = build_index(rebuild=True)
    print(f"Result: {result}")

    # Test search
    print("\nSearching for 'AI agent automation tools'...")
    results = search_similar("AI agent automation tools", limit=5)
    for r in results:
        print(f"  sim={r['similarity']:.3f} | {r['platform']}/{r['subreddit']} | {r['title'][:60]}")
