"""
Daily Alpha API endpoints
"""
from fastapi import APIRouter, HTTPException
from database import get_connection

router = APIRouter()

@router.get("/daily-alpha")
async def get_daily_alpha(limit: int = 10):
    """Get recent daily alpha recommendations with source data."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Fetch daily alphas with joined source data
    cursor.execute("""
        SELECT 
            da.id,
            da.sent_at,
            da.alpha_type,
            da.source_id,
            da.rank,
            da.impact_score,
            da.dismissed,
            da.posted,
            da.posted_url,
            CASE 
                WHEN da.alpha_type = 'pipeline' THEN p.title
                WHEN da.alpha_type = 'scout' THEN s.tweet_text
                WHEN da.alpha_type = 'trending' THEN 'Trending Topic'
            END as title,
            CASE 
                WHEN da.alpha_type = 'pipeline' THEN p.content
                WHEN da.alpha_type = 'scout' THEN s.tweet_text
                WHEN da.alpha_type = 'trending' THEN ''
            END as content,
            CASE 
                WHEN da.alpha_type = 'pipeline' THEN p.score
                WHEN da.alpha_type = 'scout' THEN NULL
                WHEN da.alpha_type = 'trending' THEN NULL
            END as score,
            CASE 
                WHEN da.alpha_type = 'pipeline' THEN p.grounding_score
                WHEN da.alpha_type = 'scout' THEN s.views
                WHEN da.alpha_type = 'trending' THEN NULL
            END as metric
        FROM daily_alpha da
        LEFT JOIN pipeline_opportunities p ON da.alpha_type = 'pipeline' AND da.source_id = CAST(p.id AS TEXT)
        LEFT JOIN scout_history s ON da.alpha_type = 'scout' AND da.source_id = CAST(s.id AS TEXT)
        ORDER BY da.sent_at DESC, da.rank ASC
        LIMIT ?
    """, (limit,))
    
    rows = cursor.fetchall()
    
    alphas = []
    for row in rows:
        alpha = {
            'id': row[0],
            'sent_at': row[1],
            'type': row[2],
            'source_id': row[3],
            'rank': row[4],
            'impact': row[5],
            'dismissed': bool(row[6]),
            'posted': bool(row[7]),
            'posted_url': row[8],
            'title': row[9] or 'Untitled',
            'content': row[10] or '',
            'score': row[11],
            'metric': row[12],
        }
        alphas.append(alpha)
    
    return {'alphas': alphas, 'total': len(alphas)}


@router.post("/daily-alpha/{alpha_id}/dismiss")
async def dismiss_alpha(alpha_id: int):
    """Dismiss a daily alpha recommendation."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("UPDATE daily_alpha SET dismissed = 1 WHERE id = ?", (alpha_id,))
    
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Alpha not found")
    
    conn.commit()
    
    return {'success': True, 'message': 'Alpha dismissed'}


@router.post("/daily-alpha/{alpha_id}/posted")
async def mark_alpha_posted(alpha_id: int, body: dict | None = None):
    """Mark a daily alpha as posted."""
    url = (body or {}).get("url")
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "UPDATE daily_alpha SET posted = 1, posted_url = ? WHERE id = ?",
        (url, alpha_id)
    )
    
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Alpha not found")
    
    conn.commit()
    
    return {'success': True, 'message': 'Alpha marked as posted'}
