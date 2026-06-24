"""
SGOS Backend - Boards / Swipe Files
Save posts to curated collections for research and inspiration.
"""
import json
from datetime import datetime, timezone

from database import get_connection


def init_board_tables():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS boards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            color TEXT DEFAULT '#00ff88',
            post_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS board_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board_id INTEGER NOT NULL,
            post_id TEXT NOT NULL,
            platform TEXT,
            platform_post_id TEXT,
            title TEXT,
            content TEXT,
            url TEXT,
            author TEXT,
            score INTEGER DEFAULT 0,
            z_score REAL DEFAULT 0,
            note TEXT DEFAULT '',
            saved_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (board_id) REFERENCES boards(id) ON DELETE CASCADE,
            UNIQUE(board_id, post_id)
        )
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_board_posts_board ON board_posts(board_id)
    """)

    conn.commit()
    conn.close()


def create_board(name: str, description: str = "", color: str = "#00ff88") -> dict:
    init_board_tables()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO boards (name, description, color) VALUES (?, ?, ?)",
            (name, description, color)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM boards WHERE name=?", (name,)).fetchone()
        conn.close()
        return dict(row) if row else {"error": "Failed to create board"}
    except sqlite3.IntegrityError:
        conn.close()
        return {"error": f"Board '{name}' already exists"}


def list_boards() -> list[dict]:
    init_board_tables()
    conn = get_connection()
    rows = conn.execute("""
        SELECT b.*, COUNT(bp.id) as post_count
        FROM boards b
        LEFT JOIN board_posts bp ON b.id = bp.board_id
        GROUP BY b.id
        ORDER BY b.updated_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_board(board_id: int) -> dict | None:
    init_board_tables()
    conn = get_connection()
    board = conn.execute("SELECT * FROM boards WHERE id=?", (board_id,)).fetchone()
    if not board:
        conn.close()
        return None

    posts = conn.execute("""
        SELECT * FROM board_posts WHERE board_id=? ORDER BY saved_at DESC
    """, (board_id,)).fetchall()

    conn.close()
    return {
        **dict(board),
        "posts": [dict(p) for p in posts],
    }


def delete_board(board_id: int) -> bool:
    init_board_tables()
    conn = get_connection()
    conn.execute("DELETE FROM board_posts WHERE board_id=?", (board_id,))
    conn.execute("DELETE FROM boards WHERE id=?", (board_id,))
    conn.commit()
    conn.close()
    return True


def save_post_to_board(board_id: int, post: dict, note: str = "") -> dict:
    init_board_tables()
    conn = get_connection()

    post_id = post.get("id") or post.get("post_id") or f"{post.get('platform','')}_{post.get('platform_post_id','')}"

    try:
        conn.execute("""
            INSERT OR IGNORE INTO board_posts
            (board_id, post_id, platform, platform_post_id, title, content, url, author, score, z_score, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            board_id,
            post_id,
            post.get("platform", ""),
            post.get("platform_post_id", ""),
            post.get("title", ""),
            (post.get("content", "") or "")[:2000],
            post.get("url", ""),
            post.get("author", ""),
            post.get("score", 0),
            post.get("z_score", 0),
            note,
        ))
        conn.execute("UPDATE boards SET updated_at=datetime('now') WHERE id=?", (board_id,))
        conn.commit()
        conn.close()
        return {"status": "saved", "board_id": board_id, "post_id": post_id}
    except Exception as e:
        conn.close()
        return {"error": str(e)}


def remove_post_from_board(board_id: int, post_id: str) -> bool:
    init_board_tables()
    conn = get_connection()
    conn.execute("DELETE FROM board_posts WHERE board_id=? AND post_id=?", (board_id, post_id))
    conn.commit()
    conn.close()
    return True


if __name__ == "__main__":
    init_board_tables()
    b = create_board("Swipe File", "Top-performing posts for inspiration")
    print(f"Created: {b}")
    boards = list_boards()
    print(f"Boards: {len(boards)}")
