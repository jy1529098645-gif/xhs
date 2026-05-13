"""One-shot snapshot of current DB state."""
import sqlite3
con = sqlite3.connect('data/xhs.db')

def q1(sql, *args):
    return con.execute(sql, args).fetchone()[0]
def q1_noarg(sql):
    return con.execute(sql).fetchone()[0]

print("=== NOTES ===")
print(f"total notes: {q1('SELECT COUNT(*) FROM notes')}")
print(f"notes with body > 100 chars: {q1('SELECT COUNT(*) FROM notes WHERE LENGTH(body) > 100')}")
print(f"video notes: {q1('SELECT COUNT(*) FROM notes WHERE type=?', 'video')}")
print(f"normal notes: {q1('SELECT COUNT(*) FROM notes WHERE type=?', 'normal')}")

print("\n=== COMMENTS ===")
print(f"total comments: {q1('SELECT COUNT(*) FROM comments')}")
print(f"notes with >=1 scraped comments: {q1('SELECT COUNT(DISTINCT note_id) FROM comments')}")

print("\n=== IMAGES ===")
print(f"total image rows: {q1('SELECT COUNT(*) FROM images')}")
print(f"locally downloaded: {q1('SELECT COUNT(*) FROM images WHERE local_path IS NOT NULL')}")

print("\n=== AUTHORS ===")
print(f"distinct authors from notes: {q1('SELECT COUNT(DISTINCT author_id) FROM notes WHERE author_id IS NOT NULL')}")

print("\n=== ENGAGEMENT ===")
print(f"sum likes: {q1('SELECT SUM(liked_count) FROM notes')}")
print(f"sum collects: {q1('SELECT SUM(collected_count) FROM notes')}")
print(f"sum comments-reported: {q1('SELECT SUM(comment_count) FROM notes')}")
top = con.execute('SELECT MAX(liked_count), title FROM notes').fetchone()
print(f"max likes: {top[0]}  title: {top[1]}")

print("\n=== KEYWORDS (search source) ===")
rows = con.execute("""
    SELECT source_value, COUNT(*) AS total,
           SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done
    FROM discover_queue WHERE source_type='search'
    GROUP BY source_value ORDER BY total DESC
""").fetchall()
for r in rows:
    print(f"  {r[0]}  total={r[1]}  done={r[2]}")

print("\n=== QUEUE STATUS ===")
for s, c in con.execute("SELECT status, COUNT(*) FROM discover_queue GROUP BY status").fetchall():
    print(f"  {s}: {c}")

con.close()
