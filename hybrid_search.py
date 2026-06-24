"""
SGOS Backend - Hybrid Search Engine
Combines FTS5 keyword search + TF-IDF vector search with Reciprocal Rank Fusion.
Best of both worlds: exact match precision + semantic recall.
"""
import json
from database import get_connection
from vector_search import search_similar


def hybrid_search(query: str, limit: int = 20, platform: str = None) -> list[dict]:
    """
    Hybrid search: merge FTS5 keyword results with TF-IDF vector results
    using Reciprocal Rank Fusion (RRF).

    RRF score = Σ 1/(k + rank_i) where k=60 (standard RRF constant).
    This rewards posts that appear in BOTH searches higher than either alone.
    """
    k_rrf = 60  # Standard RRF constant

    # ── 1. FTS5 Keyword Search ─────────────────────────────────────────────
    keyword_results = _fts5_search(query, limit=limit * 2, platform=platform)

    # ── 2. TF-IDF Vector Search ────────────────────────────────────────────
    vector_results = search_similar(query, limit=limit * 2, platform=platform)

    # ── 3. Reciprocal Rank Fusion ──────────────────────────────────────────
    rrf_scores = {}  # normalized_id -> {score, data, sources}

    # Keyword results ranked by FTS5 rank
    for rank, r in enumerate(keyword_results):
        # Normalize ID: use platform_id for dedup (vector search uses platform_id)
        pid = r.get("platform_id") or r.get("id", "")
        if pid not in rrf_scores:
            rrf_scores[pid] = {"score": 0, "data": r, "sources": []}
        rrf_scores[pid]["score"] += 1.0 / (k_rrf + rank + 1)
        rrf_scores[pid]["sources"].append("keyword")
        rrf_scores[pid]["keyword_rank"] = rank + 1

    # Vector results ranked by cosine similarity
    for rank, r in enumerate(vector_results):
        pid = r.get("post_id", "")  # This is platform_id
        if pid not in rrf_scores:
            # Fetch full post data from DB using platform_id
            full_post = _fetch_post_by_platform_id(pid)
            if full_post:
                rrf_scores[pid] = {"score": 0, "data": full_post, "sources": []}
            else:
                rrf_scores[pid] = {"score": 0, "data": r, "sources": []}
        rrf_scores[pid]["score"] += 1.0 / (k_rrf + rank + 1)
        rrf_scores[pid]["sources"].append("vector")
        rrf_scores[pid]["similarity"] = r.get("similarity", 0)

    # ── 4. Sort by RRF score, deduplicate, limit ───────────────────────────
    ranked = sorted(rrf_scores.values(), key=lambda x: x["score"], reverse=True)

    results = []
    for item in ranked[:limit]:
        data = item["data"]
        results.append({
            "id": data.get("id") or data.get("post_id", ""),
            "title": data.get("title", ""),
            "content": data.get("content", "")[:500],
            "platform": data.get("platform", ""),
            "url": data.get("url", ""),
            "score": data.get("score", 0),
            "z_score": data.get("z_score", 0),
            "author": data.get("author", ""),
            "hybrid_score": round(item["score"], 6),
            "matched_by": item["sources"],
            "keyword_rank": item.get("keyword_rank"),
            "semantic_similarity": item.get("similarity"),
        })

    return results


def _fts5_search(query: str, limit: int = 40, platform: str = None) -> list[dict]:
    """Run FTS5 full-text search, return list of post dicts."""
    conn = get_connection()
    c = conn.cursor()

    platform_where = ""
    params = [query]
    if platform and platform != "all":
        platform_where = "AND p.platform = ?"
        params.append(platform)

    # Sanitize FTS5 query to prevent operator injection
    safe_query = '"' + query.replace('"', '""') + '"'
    params = [safe_query] + params[1:] if platform and platform != "all" else [safe_query]

    try:
        rows = c.execute(f"""
            SELECT p.*, rank
            FROM posts_fts fts
            JOIN posts p ON p.rowid = fts.rowid
            WHERE posts_fts MATCH ? {platform_where}
            ORDER BY rank
            LIMIT ?
        """, params + [limit]).fetchall()
        results = [dict(r) for r in rows]
    except Exception:
        # Fallback to LIKE search
        plat_where = ""
        like_params = [f"%{query}%", f"%{query}%"]
        if platform and platform != "all":
            plat_where = "AND platform = ?"
            like_params.append(platform)
        rows = c.execute(f"""
            SELECT * FROM posts
            WHERE (title LIKE ? OR content LIKE ?) {plat_where}
            ORDER BY z_score DESC
            LIMIT ?
        """, like_params + [limit]).fetchall()
        results = [dict(r) for r in rows]

    conn.close()
    return results


def hybrid_search_with_context(query: str, limit: int = 10, platform: str = None) -> dict:
    """
    Enhanced hybrid search that also returns search analytics:
    - Which method found what
    - Overlap statistics
    - Top matching terms
    """
    results = hybrid_search(query, limit=limit, platform=platform)

    keyword_only = sum(1 for r in results if r["matched_by"] == ["keyword"])
    vector_only = sum(1 for r in results if r["matched_by"] == ["vector"])
    both = sum(1 for r in results if len(r["matched_by"]) > 1)

    return {
        "query": query,
        "results": results,
        "count": len(results),
        "analytics": {
            "keyword_hits": keyword_only,
            "semantic_hits": vector_only,
            "both_methods": both,
            "total_results": len(results),
        }
    }


def _fetch_post_by_platform_id(platform_id: str) -> dict | None:
    """Fetch full post data by platform_id (used by vector search results)."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM posts WHERE platform_id=?", (platform_id,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None
