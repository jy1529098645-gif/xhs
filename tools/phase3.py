"""Phase 3 expansion crawl.

Waits for the current detail crawl to fully drain the queue, then runs:
  - 55 long-tail keywords (留学申请深度 + 论文进阶 + 工具 + 生活/DDL + 职业)
  - 6 new topics
  - Step-2 keywords re-run with time_descending (catches recent posts)
  - Final detail pass over everything queued

Designed to be started while Step 2 is still running. It busy-waits on
discover_queue.status='pending' == 0, then sleeps 90s extra so the prior
crawler's Chrome process fully releases its profile lock.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crawler.config import DB_PATH  # noqa: E402

PYTHON = sys.executable

# Long-tail keywords — excludes anything already crawled in Step 1/2 to avoid no-ops.
NEW_KEYWORDS_POPULARITY = ",".join([
    # 留学申请 (the user's '留子' angle)
    "留学申请", "留学日常", "留学生活", "留学生",
    "SOP", "套磁", "选校", "推荐信",
    "雅思", "托福", "海外读研", "留学PhD", "DIY留学",
    "留学美国", "留学英国", "留学澳洲", "留学香港",
    "港硕", "英硕",
    # 论文进阶
    "论文初稿", "论文修改", "期刊投稿", "SCI", "核心期刊",
    "论文答辩", "答辩PPT", "文献管理", "文献查找",
    "知网查重", "论文查重", "学术不端",
    # 学术写作工具
    "AI检测", "降AI率",
    "Gemini写论文", "DeepSeek写论文", "豆包写论文",
    "学术英语", "Notion AI", "Zotero", "EndNote",
    # 学习生活 / 赶DDL
    "赶ddl", "期末复习", "期末周",
    "考研", "考研日常", "博士日常",
    "学习方法", "拖延症", "时间管理",
    # 学生职业
    "找工作", "秋招", "实习", "大学生活", "大四", "应届生",
])

# Step 2 keywords re-run sorted by recency to catch posts the popularity sort missed.
STEP2_KEYWORDS_FOR_TIME_RERUN = ",".join([
    "论文润色", "开题报告", "论文写作", "论文降重", "ChatGPT写论文",
    "科研工具", "文献阅读", "毕业论文", "留学PS", "研究生日常",
    "AI写论文", "文献综述",
])

NEW_TOPICS = ",".join([
    "留学日常", "考研", "学习方法",
    "博士日常", "大学生活", "期末复习",
])


def queue_pending() -> int:
    try:
        db = sqlite3.connect(str(DB_PATH), timeout=5)
        n = db.execute(
            "SELECT COUNT(*) FROM discover_queue WHERE status='pending'"
        ).fetchone()[0]
        db.close()
        return n
    except sqlite3.OperationalError as e:
        print(f"[phase3] db transient err: {e}", flush=True)
        return -1


def wait_for_step2_done():
    """Block until the queue has been empty for 2 consecutive minutes
    (means prior detail loop has exited; Chrome profile is free)."""
    stable_since: float | None = None
    while True:
        n = queue_pending()
        if n == 0:
            if stable_since is None:
                stable_since = time.time()
                print("[phase3] queue empty; stabilizing...", flush=True)
            elif time.time() - stable_since >= 120:
                print("[phase3] queue stably empty 120s → Step 2 done.", flush=True)
                return
        else:
            if stable_since is not None:
                print(f"[phase3] queue refilled to {n}; resetting wait", flush=True)
            stable_since = None
            if n > 0:
                print(f"[phase3] waiting: {n} pending", flush=True)
        time.sleep(60)


def run_step(*args: str) -> int:
    print(f"\n[phase3] === RUN: {' '.join(args)} ===", flush=True)
    p = subprocess.run([PYTHON, "-m", "crawler.main", *args], cwd=ROOT)
    print(f"[phase3] returned rc={p.returncode}", flush=True)
    return p.returncode


def main():
    print("[phase3] starting; will wait for Step 2 to fully complete first.",
          flush=True)
    wait_for_step2_done()
    print("[phase3] safe margin: sleeping 90s before launching Chrome.", flush=True)
    time.sleep(90)

    # 1. New long-tail keywords (popularity sort)
    run_step("search", "--keywords", NEW_KEYWORDS_POPULARITY,
             "--pages", "25", "--sort", "popularity_descending")

    # 2. New topics
    run_step("topic", "--names", NEW_TOPICS, "--pages", "25")

    # 3. Re-run Step 2 keywords by time_descending (catches recent fresh notes)
    run_step("search", "--keywords", STEP2_KEYWORDS_FOR_TIME_RERUN,
             "--pages", "25", "--sort", "time_descending")

    # 4. Drain everything that's now pending
    run_step("detail", "--comment-pages", "4")

    print("\n[phase3] === DONE ===", flush=True)


if __name__ == "__main__":
    main()
