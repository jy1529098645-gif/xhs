"""SQLite schema and DAO. Single source of truth for crawled data."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import DB_PATH

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    note_id            TEXT PRIMARY KEY,
    xsec_token         TEXT,
    url                TEXT,
    type               TEXT,                -- 'normal' | 'video'
    title              TEXT,
    body               TEXT,
    author_id          TEXT,
    author_nickname    TEXT,
    publish_time_ms    INTEGER,
    last_update_ms     INTEGER,
    ip_location        TEXT,
    liked_count        INTEGER,
    collected_count    INTEGER,
    comment_count      INTEGER,
    share_count        INTEGER,
    image_count        INTEGER,
    video_url          TEXT,
    video_duration_ms  INTEGER,
    tags_json          TEXT,
    at_users_json      TEXT,
    raw_json           TEXT,                -- full feed response, for forensics
    crawled_at         INTEGER NOT NULL,
    updated_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_author ON notes(author_id);
CREATE INDEX IF NOT EXISTS idx_notes_liked  ON notes(liked_count);

CREATE TABLE IF NOT EXISTS authors (
    user_id            TEXT PRIMARY KEY,
    nickname           TEXT,
    red_id             TEXT,
    avatar             TEXT,
    description        TEXT,
    gender             INTEGER,
    ip_location        TEXT,
    fans_count         INTEGER,
    follows_count      INTEGER,
    notes_count        INTEGER,
    interaction_count  INTEGER,
    raw_json           TEXT,
    crawled_at         INTEGER NOT NULL,
    updated_at         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
    comment_id         TEXT PRIMARY KEY,
    note_id            TEXT NOT NULL,
    parent_id          TEXT,                -- NULL for top-level
    user_id            TEXT,
    nickname           TEXT,
    content            TEXT,
    like_count         INTEGER,
    sub_comment_count  INTEGER,
    publish_time_ms    INTEGER,
    ip_location        TEXT,
    pictures_json      TEXT,
    raw_json           TEXT,
    crawled_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_note ON comments(note_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id);

CREATE TABLE IF NOT EXISTS images (
    note_id            TEXT NOT NULL,
    idx                INTEGER NOT NULL,
    url                TEXT NOT NULL,
    width              INTEGER,
    height             INTEGER,
    local_path         TEXT,
    downloaded_at      INTEGER,
    PRIMARY KEY (note_id, idx)
);

CREATE TABLE IF NOT EXISTS discover_queue (
    note_id            TEXT PRIMARY KEY,
    xsec_token         TEXT,
    xsec_source        TEXT,
    source_type        TEXT,                -- 'search' | 'author' | 'topic' | 'url'
    source_value       TEXT,                -- the keyword / user_id / topic / file
    status             TEXT NOT NULL DEFAULT 'pending',
                                            -- 'pending' | 'done' | 'error' | 'skipped'
    attempts           INTEGER NOT NULL DEFAULT 0,
    last_error         TEXT,
    discovered_at      INTEGER NOT NULL,
    last_attempt_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON discover_queue(status);

CREATE TABLE IF NOT EXISTS crawl_log (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 INTEGER NOT NULL,
    level              TEXT,
    event              TEXT,
    detail             TEXT
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def now_s() -> int:
    return int(time.time())


_CONN: Optional[sqlite3.Connection] = None


def conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        _CONN = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
        _CONN.row_factory = sqlite3.Row
        _CONN.execute("PRAGMA journal_mode=WAL")
        _CONN.execute("PRAGMA synchronous=NORMAL")
        _CONN.executescript(SCHEMA)
        _CONN.commit()
    return _CONN


@contextmanager
def tx():
    c = conn()
    with _LOCK:
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def upsert_queue_items(items: Iterable[dict]) -> int:
    """items: dicts with note_id, xsec_token, xsec_source, source_type, source_value."""
    n = 0
    with tx() as c:
        for it in items:
            cur = c.execute(
                """INSERT INTO discover_queue
                   (note_id, xsec_token, xsec_source, source_type, source_value,
                    status, attempts, discovered_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', 0, ?)
                   ON CONFLICT(note_id) DO UPDATE SET
                     xsec_token = COALESCE(excluded.xsec_token, discover_queue.xsec_token),
                     xsec_source = COALESCE(excluded.xsec_source, discover_queue.xsec_source)
                """,
                (it["note_id"], it.get("xsec_token"), it.get("xsec_source"),
                 it["source_type"], it.get("source_value"), now_s()),
            )
            if cur.rowcount:
                n += 1
    return n


def pending_queue(limit: int = 50, retry_errors: bool = False) -> list[sqlite3.Row]:
    statuses = ("pending", "error") if retry_errors else ("pending",)
    placeholders = ",".join("?" * len(statuses))
    with tx() as c:
        return list(c.execute(
            f"SELECT * FROM discover_queue WHERE status IN ({placeholders}) "
            f"ORDER BY discovered_at ASC LIMIT ?",
            (*statuses, limit),
        ))


MAX_ATTEMPTS = 6  # after this many fails (including transient retries), give up


def claim_queue_items(limit: int = 20, retry_errors: bool = False) -> list[sqlite3.Row]:
    """Atomically claim N pending items by marking them as 'in_progress'.

    Skips items with attempts >= MAX_ATTEMPTS so persistent-throttle items
    don't infinite-loop. They get marked 'error' on next mark_queue call.
    """
    statuses = ("pending", "error") if retry_errors else ("pending",)
    placeholders = ",".join("?" * len(statuses))
    with tx() as c:
        # Recover stuck items from dead workers
        c.execute(
            "UPDATE discover_queue SET status='pending' "
            "WHERE status='in_progress' AND last_attempt_at < ?",
            (now_s() - 3600,),
        )
        # Mark perpetual-fail items as gave_up so they're skipped
        c.execute(
            "UPDATE discover_queue SET status='error', last_error='max_attempts_reached' "
            "WHERE status='pending' AND attempts >= ?",
            (MAX_ATTEMPTS,),
        )
        # Atomic claim
        rows = c.execute(
            f"UPDATE discover_queue SET status='in_progress', last_attempt_at=? "
            f"WHERE note_id IN ("
            f"  SELECT note_id FROM discover_queue "
            f"  WHERE status IN ({placeholders}) AND attempts < ? "
            f"  ORDER BY discovered_at ASC LIMIT ?"
            f") RETURNING *",
            (now_s(), *statuses, MAX_ATTEMPTS, limit),
        ).fetchall()
        return list(rows)


def mark_queue(note_id: str, status: str, error: Optional[str] = None) -> None:
    with tx() as c:
        c.execute(
            "UPDATE discover_queue SET status=?, last_error=?, "
            "attempts=attempts+1, last_attempt_at=? WHERE note_id=?",
            (status, error, now_s(), note_id),
        )


def release_claim(note_id: str, reason: Optional[str] = None) -> None:
    """Return a claimed item to 'pending'.

    Used when we couldn't process the note for transient reasons (IP throttle,
    network error). Bumps attempts so persistently-failing items eventually
    get diverted (claim_queue_items has a default attempts cap).
    """
    with tx() as c:
        c.execute(
            "UPDATE discover_queue SET status='pending', last_error=?, "
            "attempts=attempts+1, last_attempt_at=? "
            "WHERE note_id=? AND status='in_progress'",
            (reason, now_s(), note_id),
        )


def upsert_note(note: dict, raw: dict) -> None:
    with tx() as c:
        c.execute(
            """INSERT INTO notes (
                note_id, xsec_token, url, type, title, body,
                author_id, author_nickname, publish_time_ms, last_update_ms, ip_location,
                liked_count, collected_count, comment_count, share_count,
                image_count, video_url, video_duration_ms, tags_json, at_users_json,
                raw_json, crawled_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(note_id) DO UPDATE SET
                 xsec_token = excluded.xsec_token,
                 url = excluded.url,
                 type = excluded.type,
                 title = excluded.title,
                 body = excluded.body,
                 author_id = excluded.author_id,
                 author_nickname = excluded.author_nickname,
                 publish_time_ms = excluded.publish_time_ms,
                 last_update_ms = excluded.last_update_ms,
                 ip_location = excluded.ip_location,
                 liked_count = excluded.liked_count,
                 collected_count = excluded.collected_count,
                 comment_count = excluded.comment_count,
                 share_count = excluded.share_count,
                 image_count = excluded.image_count,
                 video_url = excluded.video_url,
                 video_duration_ms = excluded.video_duration_ms,
                 tags_json = excluded.tags_json,
                 at_users_json = excluded.at_users_json,
                 raw_json = excluded.raw_json,
                 updated_at = excluded.updated_at
            """,
            (
                note["note_id"], note.get("xsec_token"), note.get("url"),
                note.get("type"), note.get("title"), note.get("body"),
                note.get("author_id"), note.get("author_nickname"),
                note.get("publish_time_ms"), note.get("last_update_ms"),
                note.get("ip_location"),
                note.get("liked_count"), note.get("collected_count"),
                note.get("comment_count"), note.get("share_count"),
                note.get("image_count"), note.get("video_url"),
                note.get("video_duration_ms"),
                json.dumps(note.get("tags") or [], ensure_ascii=False),
                json.dumps(note.get("at_users") or [], ensure_ascii=False),
                json.dumps(raw, ensure_ascii=False),
                now_ms(), now_ms(),
            ),
        )


def upsert_images(note_id: str, imgs: list[dict]) -> None:
    if not imgs:
        return
    with tx() as c:
        for i, img in enumerate(imgs):
            c.execute(
                """INSERT INTO images (note_id, idx, url, width, height, local_path, downloaded_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(note_id, idx) DO UPDATE SET
                     url=excluded.url, width=excluded.width, height=excluded.height,
                     local_path=COALESCE(excluded.local_path, images.local_path),
                     downloaded_at=COALESCE(excluded.downloaded_at, images.downloaded_at)
                """,
                (note_id, i, img.get("url"), img.get("width"), img.get("height"),
                 img.get("local_path"), img.get("downloaded_at")),
            )


def set_image_local(note_id: str, idx: int, local_path: str) -> None:
    with tx() as c:
        c.execute(
            "UPDATE images SET local_path=?, downloaded_at=? WHERE note_id=? AND idx=?",
            (local_path, now_s(), note_id, idx),
        )


def upsert_comments(comments: list[dict]) -> None:
    if not comments:
        return
    with tx() as c:
        for cm in comments:
            c.execute(
                """INSERT INTO comments (
                    comment_id, note_id, parent_id, user_id, nickname, content,
                    like_count, sub_comment_count, publish_time_ms, ip_location,
                    pictures_json, raw_json, crawled_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(comment_id) DO UPDATE SET
                     content=excluded.content,
                     like_count=excluded.like_count,
                     sub_comment_count=excluded.sub_comment_count,
                     pictures_json=excluded.pictures_json,
                     raw_json=excluded.raw_json
                """,
                (
                    cm["comment_id"], cm["note_id"], cm.get("parent_id"),
                    cm.get("user_id"), cm.get("nickname"), cm.get("content"),
                    cm.get("like_count"), cm.get("sub_comment_count"),
                    cm.get("publish_time_ms"), cm.get("ip_location"),
                    json.dumps(cm.get("pictures") or [], ensure_ascii=False),
                    json.dumps(cm.get("raw") or {}, ensure_ascii=False),
                    now_ms(),
                ),
            )


def upsert_author(a: dict, raw: dict) -> None:
    with tx() as c:
        c.execute(
            """INSERT INTO authors (
                user_id, nickname, red_id, avatar, description, gender, ip_location,
                fans_count, follows_count, notes_count, interaction_count,
                raw_json, crawled_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 nickname=excluded.nickname,
                 red_id=excluded.red_id,
                 avatar=excluded.avatar,
                 description=excluded.description,
                 gender=excluded.gender,
                 ip_location=excluded.ip_location,
                 fans_count=excluded.fans_count,
                 follows_count=excluded.follows_count,
                 notes_count=excluded.notes_count,
                 interaction_count=excluded.interaction_count,
                 raw_json=excluded.raw_json,
                 updated_at=excluded.updated_at
            """,
            (
                a["user_id"], a.get("nickname"), a.get("red_id"), a.get("avatar"),
                a.get("description"), a.get("gender"), a.get("ip_location"),
                a.get("fans_count"), a.get("follows_count"),
                a.get("notes_count"), a.get("interaction_count"),
                json.dumps(raw, ensure_ascii=False), now_ms(), now_ms(),
            ),
        )


def log_event(level: str, event: str, detail: Any = None) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO crawl_log (ts, level, event, detail) VALUES (?,?,?,?)",
            (now_s(), level, event,
             detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)),
        )


def note_status(note_id: str) -> Optional[str]:
    with tx() as c:
        row = c.execute("SELECT status FROM discover_queue WHERE note_id=?",
                        (note_id,)).fetchone()
        return row["status"] if row else None


def stats() -> dict:
    c = conn()
    out = {}
    for q in ("notes", "comments", "images", "authors", "discover_queue"):
        out[q] = c.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
    out["queue_pending"] = c.execute(
        "SELECT COUNT(*) FROM discover_queue WHERE status='pending'").fetchone()[0]
    out["queue_done"] = c.execute(
        "SELECT COUNT(*) FROM discover_queue WHERE status='done'").fetchone()[0]
    out["queue_error"] = c.execute(
        "SELECT COUNT(*) FROM discover_queue WHERE status='error'").fetchone()[0]
    return out
