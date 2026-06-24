"""
Firecrawl Deep-Scrape Module
Scrapes full article text from outlier URLs using Firecrawl on 3090-lan:3002.
Stores scraped content back into the posts table for richer idea generation.
"""
import subprocess
import json
import os
import sys
import shlex
from datetime import datetime, timezone
from urllib.parse import urlparse

from database import get_connection


# ─── Firecrawl Access ─────────────────────────────────────────────────────────

FIRECRAWL_HOST = os.environ.get("FIRECRAWL_HOST", "3090-lan")
FIRECRAWL_PORT = int(os.environ.get("FIRECRAWL_PORT", "3002"))

ALLOWED_URL_SCHEMES = {"http", "https"}


def _validate_url(url: str) -> str | None:
    """Validate URL scheme and host. Returns error message or None."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL format"
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        return f"Blocked scheme: {parsed.scheme}. Only http/https allowed."
    if not parsed.hostname:
        return "No hostname in URL"
    # Block private/internal IPs
    hostname = parsed.hostname.lower()
    blocked_prefixes = ["localhost", "127.0.0.", "0.0.0.0", "169.254.", "10.", "172.16.", "192.168.", "metadata.google"]
    if any(hostname == b or hostname.startswith(b) for b in blocked_prefixes):
        return f"Blocked internal address: {hostname}"
    return None


def scrape_url(url: str, timeout: int = 30) -> dict:
    """
    Scrape a URL via Firecrawl on 3090-lan.
    
    Returns:
        Dict with: markdown, metadata, success status
    """
    if not url:
        return {"success": False, "error": "No URL provided"}
    
    # Validate URL to prevent SSRF and injection
    url_error = _validate_url(url)
    if url_error:
        return {"success": False, "error": url_error}
    
    # Build the curl command to run on 3090
    payload = json.dumps({
        "url": url,
        "formats": ["markdown"],
    })
    
    # Use shlex.quote to safely escape the payload for shell execution
    ssh_cmd = (
        f"curl -s -X POST http://localhost:{FIRECRAWL_PORT}/v1/scrape "
        f"-H 'Content-Type: application/json' "
        f"-d {shlex.quote(payload)}"
    )
    
    try:
        result = subprocess.run(
            ["ssh", FIRECRAWL_HOST, ssh_cmd],
            capture_output=True, text=True, timeout=timeout
        )
        
        if result.returncode != 0:
            return {"success": False, "error": f"SSH failed: {result.stderr[:200]}"}
        
        data = json.loads(result.stdout)
        
        if not data.get("success"):
            return {"success": False, "error": data.get("error", "Unknown error")}
        
        page_data = data.get("data", {})
        return {
            "success": True,
            "markdown": page_data.get("markdown", ""),
            "title": page_data.get("metadata", {}).get("title", ""),
            "description": page_data.get("metadata", {}).get("description", ""),
            "url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "word_count": len(page_data.get("markdown", "").split()),
        }
    
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Timeout after {timeout}s"}
    except json.JSONDecodeError:
        return {"success": False, "error": "Invalid JSON response from Firecrawl"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def deep_scrape_post(post_id: str) -> dict:
    """
    Deep-scrape a post by ID: fetch its URL via Firecrawl and store full text.
    
    Returns:
        Dict with scrape result and DB update status.
    """
    conn = get_connection()
    c = conn.cursor()
    
    # Find the post
    row = c.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not row:
        conn.close()
        return {"success": False, "error": f"Post not found: {post_id}"}
    
    post = dict(row)
    url = post.get("url", "")
    
    if not url:
        conn.close()
        return {"success": False, "error": "Post has no URL"}
    
    conn.close()
    
    # Scrape
    print(f"🔥 Scraping: {url}")
    result = scrape_url(url)
    
    if not result["success"]:
        return result
    
    # Store scraped content back in DB
    markdown = result.get("markdown", "")
    if markdown:
        conn = get_connection()
        c = conn.cursor()
        
        # Update content field with full scraped text (append to existing)
        existing_content = post.get("content", "") or ""
        full_content = existing_content
        if markdown and markdown not in existing_content:
            full_content = f"{existing_content}\n\n--- FIRECRAWL SCRAPE ---\n{markdown[:5000]}"
        
        # Update the post
        c.execute(
            "UPDATE posts SET content = ?, scraped_at = ? WHERE id = ?",
            (full_content, datetime.now(timezone.utc).isoformat(), post_id)
        )
        
        # Rebuild FTS index for this post (delete + re-insert for external content tables)
        try:
            title = post.get("title", "")
            rowid = c.execute("SELECT rowid FROM posts WHERE id = ?", (post_id,)).fetchone()
            if rowid:
                rowid = rowid[0]
                c.execute("DELETE FROM posts_fts WHERE rowid = ?", (rowid,))
                c.execute("INSERT INTO posts_fts(rowid, title, content) VALUES (?, ?, ?)", (rowid, title, full_content[:5000]))
        except Exception:
            pass  # FTS table may not exist or have different schema
        
        conn.commit()
        conn.close()
    
    return {
        "success": True,
        "post_id": post_id,
        "title": result.get("title") or post.get("title", ""),
        "url": url,
        "word_count": result.get("word_count", 0),
        "stored": bool(markdown),
        "scraped_at": result.get("scraped_at"),
    }


def deep_scrape_outliers(threshold: float = 3.0, limit: int = 5, hours: int = 48) -> dict:
    """
    Deep-scrape all outliers above a z-score threshold.
    
    Returns:
        Dict with results for each post.
    """
    conn = get_connection()
    c = conn.cursor()
    
    # Find outliers without scraped content
    rows = c.execute("""
        SELECT * FROM posts 
        WHERE z_score >= ? 
        AND (scraped_at IS NULL OR scraped_at = '')
        ORDER BY z_score DESC 
        LIMIT ?
    """, (threshold, limit)).fetchall()
    
    outliers = [dict(r) for r in rows]
    conn.close()
    
    if not outliers:
        return {"status": "no_outliers", "message": "No unscraped outliers found", "results": []}
    
    results = []
    for post in outliers:
        result = deep_scrape_post(post["id"])
        results.append(result)
        print(f"  {'✅' if result.get('success') else '❌'} {post.get('title', post['id'])[:50]} ({result.get('word_count', 0)} words)")
    
    scraped = sum(1 for r in results if r.get("success"))
    failed = sum(1 for r in results if not r.get("success"))
    
    return {
        "status": "complete",
        "total": len(results),
        "scraped": scraped,
        "failed": failed,
        "results": results,
    }


# ─── Ensure scraped_at column exists ──────────────────────────────────────────

def ensure_scraped_at_column():
    """Add scraped_at column to posts table if it doesn't exist."""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("ALTER TABLE posts ADD COLUMN scraped_at TEXT")
        conn.commit()
        print("✅ Added scraped_at column to posts table")
    except Exception:
        pass  # Column already exists
    conn.close()


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from database import init_db
    init_db()
    ensure_scraped_at_column()
    
    if len(sys.argv) > 1 and sys.argv[1] == "url":
        # Scrape a specific URL
        url = sys.argv[2] if len(sys.argv) > 2 else ""
        if url:
            result = scrape_url(url)
            print(json.dumps(result, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "post":
        # Scrape a specific post
        post_id = sys.argv[2] if len(sys.argv) > 2 else ""
        if post_id:
            result = deep_scrape_post(post_id)
            print(json.dumps(result, indent=2))
    else:
        # Scrape all outliers
        result = deep_scrape_outliers(threshold=3.0, limit=10)
        print(json.dumps(result, indent=2))
