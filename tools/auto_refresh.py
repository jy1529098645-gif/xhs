"""Periodically re-render the dashboard and push to git when new notes arrive.

Designed to run alongside the crawler. Polls notes count every CHECK_INTERVAL
seconds; on a meaningful delta (>= MIN_DELTA new notes since last push), it:
  1. renders the dashboard
  2. git add / commit / push

Idempotent: safe to interrupt and restart. Detects "git push" failures and keeps
trying on the next tick (transient network errors don't block crawler progress).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crawler.config import DB_PATH  # noqa: E402

PYTHON = sys.executable
CHECK_INTERVAL = 60          # poll DB every minute
MIN_DELTA = 20               # push if at least N new notes since last push
MAX_INTERVAL = 30 * 60       # but also push at least every 30 min if anything new


def count_notes() -> int:
    try:
        db = sqlite3.connect(str(DB_PATH), timeout=5)
        n = db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        db.close()
        return n
    except sqlite3.OperationalError as e:
        # crawler might be mid-write; just retry next tick
        print(f"[autorefresh] db read transient err: {e}")
        return -1


def run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out.strip()


def render() -> bool:
    rc, out = run([PYTHON, "-m", "crawler.main", "dashboard"], cwd=ROOT)
    if rc != 0:
        print(f"[autorefresh] render fail rc={rc}: {out}")
        return False
    return True


def push(count: int) -> bool:
    rc, out = run(["git", "add", "index.html", "data/exports/dashboard.html"], cwd=ROOT)
    if rc != 0:
        print(f"[autorefresh] git add fail: {out}")
        return False
    rc, out = run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if rc == 0:
        # no staged changes — nothing to push
        return True
    rc, out = run(["git", "commit", "-m", f"refresh: {count} notes"], cwd=ROOT)
    if rc != 0:
        print(f"[autorefresh] commit fail: {out}")
        return False
    rc, out = run(["git", "push"], cwd=ROOT)
    if rc != 0:
        print(f"[autorefresh] push fail (will retry next tick): {out}")
        return False
    print(f"[autorefresh] pushed at {count} notes")
    return True


def main():
    last_pushed_count = count_notes()
    last_push_time = time.time()
    print(f"[autorefresh] starting at {last_pushed_count} notes; "
          f"will push every +{MIN_DELTA} notes or every {MAX_INTERVAL // 60}min")

    while True:
        time.sleep(CHECK_INTERVAL)
        n = count_notes()
        if n < 0 or n == last_pushed_count:
            continue
        elapsed = time.time() - last_push_time
        if (n - last_pushed_count) >= MIN_DELTA or elapsed >= MAX_INTERVAL:
            if render() and push(n):
                last_pushed_count = n
                last_push_time = time.time()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[autorefresh] stopped by user")
