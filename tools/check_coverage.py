"""Show what keywords have been scraped and how many notes per source."""
import sqlite3
con = sqlite3.connect('data/xhs.db')
print("=== Sources covered (discover_queue.source_value) ===")
rows = con.execute("""
    SELECT source_value, COUNT(*) AS total,
           SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
           SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS err,
           SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending
    FROM discover_queue
    WHERE source_type='search'
    GROUP BY source_value ORDER BY total DESC
""").fetchall()
for r in rows:
    print(f"  {r[0]:30s}  total={r[1]:>4}  done={r[2]:>4}  err={r[3]:>3}  pending={r[4]:>3}")
total_notes = con.execute('SELECT COUNT(*) FROM notes').fetchone()[0]
pending = con.execute("SELECT COUNT(*) FROM discover_queue WHERE status='pending'").fetchone()[0]
errored = con.execute("SELECT COUNT(*) FROM discover_queue WHERE status='error'").fetchone()[0]
print(f"\nTotal notes in DB: {total_notes}")
print(f"Total pending in queue: {pending}")
print(f"Total errors in queue: {errored}")
con.close()
