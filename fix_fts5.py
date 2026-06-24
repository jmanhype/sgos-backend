"""Fix corrupted FTS5 index by rebuilding from scratch."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "sgos.db"

conn = sqlite3.connect(str(DB_PATH))
conn.execute("PRAGMA journal_mode=WAL")

print("Dropping corrupted FTS5...")
conn.execute("DROP TABLE IF EXISTS posts_fts")
conn.commit()

print("Recreating FTS5...")
conn.execute("""CREATE VIRTUAL TABLE posts_fts USING fts5(
    title, content, subreddit, author,
    content=posts, content_rowid=rowid
)""")

print("Populating from posts table...")
conn.execute("""INSERT INTO posts_fts(rowid, title, content, subreddit, author)
    SELECT rowid, COALESCE(title,''), COALESCE(content,''), COALESCE(subreddit,''), COALESCE(author,'') FROM posts""")
conn.commit()

# Verify
count = conn.execute("SELECT COUNT(*) FROM posts_fts").fetchone()[0]
print(f"FTS5 rows: {count}")

rows = conn.execute("""SELECT p.title FROM posts_fts fts JOIN posts p ON p.rowid = fts.rowid
    WHERE posts_fts MATCH 'openai' LIMIT 3""").fetchall()
print(f"'openai' matches: {len(rows)}")
for r in rows:
    print(f"  {r[0][:70]}")

rows2 = conn.execute("""SELECT p.title FROM posts_fts fts JOIN posts p ON p.rowid = fts.rowid
    WHERE posts_fts MATCH 'grok' LIMIT 3""").fetchall()
print(f"'grok' matches: {len(rows2)}")
for r in rows2:
    print(f"  {r[0][:70]}")

conn.close()
print("\n✅ FTS5 rebuilt successfully!")
