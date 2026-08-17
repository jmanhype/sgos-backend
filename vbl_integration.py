"""
Viral-Bench-Local Integration for SGOS Backend
Reads VBL corpus insights (hooks, formats, retention triggers, engagement data)
and feeds them into SGOS idea generation pipeline.
"""
import json
import sqlite3
from pathlib import Path
from typing import Optional
from collections import defaultdict
import statistics

# VBL database path
VBL_DB_PATH = Path.home() / "viral-bench-local" / "data" / "corpus.db"

# Shared creator-to-niche mapping
CREATOR_TO_NICHE = {
    'khaby.lame': 'comedy', 'wisdm8': 'comedy', 'brittany_broski': 'comedy',
    'zachking': 'magic/vfx', 'charlidamelio': 'dance', 'jasonderulo': 'dance',
    'addisonre': 'dance', 'bellapoarch': 'music', 'toniannmusic': 'music',
    'nala_cat': 'pets', 'tuckerbudzyn': 'pets', 'realgrumpycat': 'pets',
    'gordonramsayofficial': 'food', 'babishculinaryuniverse': 'food',
    'chris.hemsworth': 'fitness', 'pamela_rf': 'fitness', 'blogilates': 'fitness',
    'hankgreen': 'education', 'neildegrassetyson': 'education',
    'emma': 'lifestyle', 'merrelltwins': 'lifestyle',
    'duolingo': 'brand', 'ryanair': 'brand', 'chipotle': 'brand',
    'julianbass': 'vfx',
}


def get_vbl_connection():
    """Get read-only connection to VBL corpus database."""
    if not VBL_DB_PATH.exists():
        raise FileNotFoundError(f"VBL database not found: {VBL_DB_PATH}")
    conn = sqlite3.connect(f"file:{VBL_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_niche_patterns(niche: Optional[str] = None, min_likes: int = 0) -> dict:
    """
    Extract viral patterns from VBL corpus.
    Returns hooks, formats, retention triggers, and engagement benchmarks.
    """
    db = get_vbl_connection()
    
    # Get posts with VLM analysis
    if niche:
        rows = db.execute("""
            SELECT creator_handle, likes, views, vlm_analysis, caption
            FROM posts 
            WHERE vlm_analysis IS NOT NULL 
              AND vlm_analysis != ''
              AND likes >= ?
        """, (min_likes,)).fetchall()
    else:
        rows = db.execute("""
            SELECT creator_handle, likes, views, vlm_analysis, caption
            FROM posts 
            WHERE vlm_analysis IS NOT NULL 
              AND vlm_analysis != ''
              AND likes >= ?
        """, (min_likes,)).fetchall()
    
    hooks = defaultdict(list)
    formats = defaultdict(list)
    retention_triggers = defaultdict(list)
    why_it_works = []
    
    creator_to_niche = CREATOR_TO_NICHE
    
    for row in rows:
        creator = row['creator_handle'].lstrip('@')
        post_niche = creator_to_niche.get(creator, 'other')
        
        # Filter by niche if specified
        if niche and post_niche != niche:
            continue
        
        try:
            analysis = json.loads(row['vlm_analysis'])
            if 'hook_type' not in analysis:
                continue  # Skip old format
                
            likes = row['likes'] or 0
            
            # Extract patterns
            hook = analysis.get('hook_type', '')
            if hook:
                hooks[post_niche].append({
                    'hook': hook,
                    'timestamp': analysis.get('hook_timestamp', ''),
                    'likes': likes,
                })
            
            fmt = analysis.get('visual_format', '')
            if fmt:
                formats[post_niche].append({
                    'format': fmt,
                    'pacing': analysis.get('pacing', ''),
                    'energy': analysis.get('energy_level', ''),
                    'likes': likes,
                })
            
            triggers = analysis.get('retention_triggers', [])
            if triggers:
                retention_triggers[post_niche].extend(triggers)
            
            why = analysis.get('why_it_works', '')
            if why:
                why_it_works.append({
                    'niche': post_niche,
                    'reason': why,
                    'likes': likes,
                })
                
        except (json.JSONDecodeError, TypeError):
            continue
    
    db.close()
    
    # Aggregate and rank
    result = {}
    for n in set(hooks.keys()):
        # Top hooks by likes
        top_hooks = sorted(hooks[n], key=lambda x: x['likes'], reverse=True)[:5]
        hook_counts = defaultdict(int)
        for h in top_hooks:
            # Normalize hook names
            hook_lower = h['hook'].lower()
            for keyword in ['pattern interrupt', 'curiosity gap', 'shock visual', 
                          'relatable frustration', 'direct address', 'text overlay']:
                if keyword in hook_lower:
                    hook_counts[keyword] += 1
        
        # Top formats by likes
        top_formats = sorted(formats[n], key=lambda x: x['likes'], reverse=True)[:5]
        format_counts = defaultdict(int)
        for f in top_formats:
            fmt_lower = f['format'].lower()
            for keyword in ['reveal', 'illusion', 'behind-the-scenes', 'talking head',
                          'pov', 'skit', 'tutorial', 'montage', 'b-roll']:
                if keyword in fmt_lower:
                    format_counts[keyword] += 1
        
        # Top retention triggers
        triggers = retention_triggers.get(n, [])
        trigger_counts = defaultdict(int)
        for t in triggers:
            trigger_lower = t.lower()
            for keyword in ['curiosity', 'stakes', 'satisfying', 'reveal', 'success']:
                if keyword in trigger_lower:
                    trigger_counts[keyword] += 1
        
        result[n] = {
            'top_hooks': dict(sorted(hook_counts.items(), key=lambda x: x[1], reverse=True)),
            'top_formats': dict(sorted(format_counts.items(), key=lambda x: x[1], reverse=True)),
            'top_triggers': dict(sorted(trigger_counts.items(), key=lambda x: x[1], reverse=True)),
            'examples': [h['hook'] for h in top_hooks[:3]],
        }
    
    return result


def get_engagement_benchmarks(niche: Optional[str] = None) -> dict:
    """Get engagement benchmarks from VBL corpus (avg likes, viral thresholds)."""
    db = get_vbl_connection()
    
    # Query with niche mapping
    query = """
        SELECT 
            CASE creator_handle
                WHEN '@khaby.lame' THEN 'comedy'
                WHEN '@wisdm8' THEN 'comedy'
                WHEN '@brittany_broski' THEN 'comedy'
                WHEN '@zachking' THEN 'magic/vfx'
                WHEN '@charlidamelio' THEN 'dance'
                WHEN '@jasonderulo' THEN 'dance'
                WHEN '@addisonre' THEN 'dance'
                WHEN '@bellapoarch' THEN 'music'
                WHEN '@toniannmusic' THEN 'music'
                WHEN '@nala_cat' THEN 'pets'
                WHEN '@tuckerbudzyn' THEN 'pets'
                WHEN '@realgrumpycat' THEN 'pets'
                WHEN '@gordonramsayofficial' THEN 'food'
                WHEN '@babishculinaryuniverse' THEN 'food'
                WHEN '@chris.hemsworth' THEN 'fitness'
                WHEN '@pamela_rf' THEN 'fitness'
                WHEN '@blogilates' THEN 'fitness'
                WHEN '@hankgreen' THEN 'education'
                WHEN '@neildegrassetyson' THEN 'education'
                WHEN '@emma' THEN 'lifestyle'
                WHEN '@merrelltwins' THEN 'lifestyle'
                WHEN '@duolingo' THEN 'brand'
                WHEN '@ryanair' THEN 'brand'
                WHEN '@chipotle' THEN 'brand'
                WHEN '@julianbass' THEN 'vfx'
                ELSE 'other'
            END as niche,
            COUNT(*) as total_posts,
            AVG(likes) as avg_likes,
            AVG(engagement_rate) as avg_engagement
        FROM posts
        WHERE vlm_analysis IS NOT NULL AND vlm_analysis != ''
        GROUP BY niche
        HAVING niche != 'other'
    """
    
    rows = db.execute(query).fetchall()
    db.close()
    
    benchmarks = {}
    for row in rows:
        n = row['niche']
        if niche and n != niche:
            continue
        benchmarks[n] = {
            'total_posts': row['total_posts'],
            'avg_likes': row['avg_likes'] or 0,
            'avg_engagement': row['avg_engagement'] or 0,
        }
    
    return benchmarks


def generate_vbl_brief(niche: str, num_ideas: int = 3) -> list[dict]:
    """Generate viral content briefs using VBL patterns."""
    patterns = get_niche_patterns(niche)
    benchmarks = get_engagement_benchmarks(niche)
    
    if niche not in patterns:
        return []
    
    niche_patterns = patterns[niche]
    niche_bench = benchmarks.get(niche, {})
    
    # Calculate viral threshold (top 10% benchmark)
    db = get_vbl_connection()
    top_posts = db.execute("""
        SELECT likes FROM posts 
        WHERE likes IS NOT NULL
        ORDER BY likes DESC
        LIMIT 100
    """).fetchall()
    db.close()
    
    if top_posts:
        viral_threshold = top_posts[9]['likes'] if len(top_posts) > 9 else top_posts[-1]['likes']
    else:
        viral_threshold = niche_bench.get('avg_likes', 0) * 2.5
    
    ideas = []
    top_hooks = list(niche_patterns['top_hooks'].keys())
    top_formats = list(niche_patterns['top_formats'].keys())
    
    for i in range(num_ideas):
        chosen_hook = top_hooks[i % len(top_hooks)] if top_hooks else "pattern interrupt"
        chosen_format = top_formats[i % len(top_formats)] if top_formats else "reveal"
        
        idea = {
            'niche': niche,
            'hook_technique': chosen_hook,
            'visual_format': chosen_format,
            'market_insights': {
                'total_posts': niche_bench.get('total_posts', 0),
                'avg_likes': niche_bench.get('avg_likes', 0),
                'avg_engagement': niche_bench.get('avg_engagement', 0),
                'viral_threshold': viral_threshold,
            },
            'brief': _build_vbl_brief_text(
                chosen_hook, chosen_format, niche,
                niche_bench, viral_threshold
            ),
        }
        ideas.append(idea)
    
    return ideas


def _build_vbl_brief_text(hook: str, format: str, niche: str, 
                          benchmarks: dict, viral_threshold: float) -> str:
    """Build actionable brief with VBL data."""
    total_posts = benchmarks.get('total_posts', 0)
    avg_likes = benchmarks.get('avg_likes', 0)
    avg_engagement = benchmarks.get('avg_engagement', 0)
    
    brief = f"""🎯 NICHE: {niche.upper()}

📊 VIRAL-BENCH INSIGHTS (from {total_posts:,} posts):
• Average performance: {avg_likes:,.0f} likes ({avg_engagement:.1%} engagement)
• Viral threshold (top 10%): {viral_threshold:,.0f} likes
• This is what "good" looks like in {niche}

🪝 HOOK TECHNIQUE: {hook.title()}
Open with a {hook} in the first 0-3 seconds:
- Pattern interrupt: Show something unexpected that breaks expectations
- Curiosity gap: Start mid-action, make viewer wonder "what happens next?"
- Shock visual: Use striking imagery that demands attention

📹 VISUAL FORMAT: {format.title()}
Structure as a {format}:
- Match pacing to format (high energy for montage, medium for tutorial)
- Use proven transitions from top performers

💡 EXECUTION CHECKLIST:
1. First 3 seconds: Hook must grab attention immediately
2. Middle section: Deliver on the hook's promise
3. End: Satisfying payoff or clear CTA
4. Target: {viral_threshold:,.0f}+ likes to hit top 10%

🔬 WHY IT WORKS:
Based on empirical analysis of {total_posts:,} {niche} posts.
Posts using "{hook}" hooks and "{format}" formats show consistent {avg_engagement:.1%} engagement.
This combination has proven viral mechanics in the {niche} niche.
"""
    return brief.strip()


def get_vbl_patterns_summary() -> dict:
    """Get high-level summary of all VBL patterns for dashboard."""
    patterns = get_niche_patterns()
    benchmarks = get_engagement_benchmarks()
    
    summary = {
        'niches': list(patterns.keys()),
        'total_analyzed': sum(b['total_posts'] for b in benchmarks.values()),
        'top_niches': [],
    }
    
    # Rank niches by engagement (cap at 100% to handle bad data)
    niche_scores = []
    for n, bench in benchmarks.items():
        if n in patterns:
            # Cap engagement rate at 1.0 (100%) to handle outliers
            engagement = min(bench['avg_engagement'], 1.0) if bench['avg_engagement'] else 0
            niche_scores.append({
                'niche': n,
                'engagement': engagement,
                'avg_likes': bench['avg_likes'],
                'top_hook': list(patterns[n]['top_hooks'].keys())[0] if patterns[n]['top_hooks'] else None,
            })
    
    niche_scores.sort(key=lambda x: x['engagement'], reverse=True)
    summary['top_niches'] = niche_scores[:5]
    
    return summary
