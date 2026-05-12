"""Note detail + comments + image download."""
import time
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from . import config, db
from .browser import Browser
from .parse import (parse_feed_note, parse_initial_state,
                    parse_comments, has_more_comments)


def note_url(note_id: str, xsec_token: Optional[str], xsec_source: Optional[str]) -> str:
    qs = []
    if xsec_token:
        qs.append(f"xsec_token={xsec_token}")
    qs.append(f"xsec_source={xsec_source or 'pc_search'}")
    return f"{config.XHS_HOST}/explore/{note_id}?{'&'.join(qs)}"


def scrape_note(b: Browser, note_id: str, xsec_token: Optional[str],
                xsec_source: Optional[str], comment_pages: int = 5,
                download_images: bool = True) -> bool:
    """Scrape one note's detail + comments. Returns True on success."""
    b.start_listen()
    url = note_url(note_id, xsec_token, xsec_source)
    logger.info("Note {}: {}", note_id, url)
    if not b.goto(url):
        return False

    # Let SSR finish hydrating
    time.sleep(2)

    # PRIMARY PATH — read window.__INITIAL_STATE__ which xhs SSRs into the page.
    # This is reliable even when no /feed API call fires.
    note = images = ssr_comments = None
    raw_state = b.read_initial_state()
    if raw_state:
        parsed = parse_initial_state(raw_state, note_id)
        if parsed:
            note, images, ssr_comments = parsed

    # FALLBACK — listener (for older xhs builds that still hit /feed)
    feed_json_for_storage: dict = raw_state or {}
    if not note:
        packets = b.collect_packets(timeout=6.0)
        feed_pkt = next((p for p in packets if Browser.packet_kind(p) == "feed"), None)
        if feed_pkt:
            feed_json = Browser.packet_json(feed_pkt)
            if feed_json:
                parsed = parse_feed_note(feed_json, note_id)
                if parsed:
                    note, images = parsed
                    ssr_comments = []
                    feed_json_for_storage = feed_json

    if not note:
        logger.warning("No note data extracted for {}", note_id)
        return False

    note["url"] = url
    note["note_id"] = note_id
    if xsec_token and not note.get("xsec_token"):
        note["xsec_token"] = xsec_token

    db.upsert_note(note, feed_json_for_storage)
    db.upsert_images(note_id, images or [])

    # Trigger comment area into view to load more comments via API
    try:
        ce = b.page.ele("css:.comment-container, .comments-container, #commentpop, "
                        ".note-scroller, .interaction-container",
                        timeout=2)
        if ce:
            ce.scroll.to_see()
    except Exception:
        pass
    time.sleep(1.5)

    # Comments: SSR-embedded list + any /comment/page network calls so far
    initial_comments = list(ssr_comments or [])
    packets = b.collect_packets(timeout=4.0)
    last_cursor: Optional[str] = None
    has_more = False
    for p in packets:
        kind = Browser.packet_kind(p)
        if kind == "comment_page":
            j = Browser.packet_json(p)
            if j:
                initial_comments.extend(parse_comments(j, note_id))
                hm, cur = has_more_comments(j)
                has_more, last_cursor = hm, cur
        elif kind == "comment_sub":
            j = Browser.packet_json(p)
            if j:
                initial_comments.extend(parse_comments(j, note_id))
    if initial_comments:
        db.upsert_comments(initial_comments)
        logger.info("  {} initial comments", len(initial_comments))

    # Scroll for more comment pages
    pages_loaded = 1 if initial_comments else 0
    while has_more and pages_loaded < comment_pages:
        b.page.scroll.down(1200)
        time.sleep(1.5)
        new_pkts = b.collect_packets(timeout=4.0)
        got_any = False
        for p in new_pkts:
            kind = Browser.packet_kind(p)
            if kind in ("comment_page", "comment_sub"):
                j = Browser.packet_json(p)
                if j:
                    comms = parse_comments(j, note_id)
                    if comms:
                        db.upsert_comments(comms)
                        got_any = True
                    if kind == "comment_page":
                        has_more, last_cursor = has_more_comments(j)
        if not got_any:
            break
        pages_loaded += 1

    if download_images and images:
        cookies = _cookies_dict(b)
        _download_images(note_id, images, cookies)

    return True


def _download_images(note_id: str, images: list[dict], cookies: dict) -> None:
    target_dir = config.IMAGES_DIR / note_id
    target_dir.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
        "Referer": config.XHS_HOST + "/",
    }
    with httpx.Client(http2=False, follow_redirects=True, timeout=30.0,
                      headers=headers, cookies=cookies) as client:
        for i, img in enumerate(images):
            url = img.get("url")
            if not url:
                continue
            ext = _guess_ext(url)
            out = target_dir / f"{i:02d}{ext}"
            if out.exists() and out.stat().st_size > 0:
                db.set_image_local(note_id, i, str(out))
                continue
            try:
                r = client.get(url)
                if r.status_code == 200 and r.content:
                    out.write_bytes(r.content)
                    db.set_image_local(note_id, i, str(out))
                else:
                    logger.warning("img {} status {}", url, r.status_code)
            except Exception as e:
                logger.warning("img {} download fail: {}", url, e)


def _guess_ext(url: str) -> str:
    lower = url.lower().split("?", 1)[0]
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if lower.endswith(ext):
            return ext
    return ".jpg"


def _cookies_dict(b: Browser) -> dict:
    try:
        cookies = b.page.cookies()
        return {c["name"]: c["value"] for c in cookies if "name" in c and "value" in c}
    except Exception:
        return {}


def run_detail_batch(comment_pages: int = 5, retry_errors: bool = False,
                     max_notes: Optional[int] = None,
                     download_images: bool = True) -> dict:
    """Pull pending entries from the queue and scrape each."""
    from .browser import browser
    counts = {"ok": 0, "fail": 0, "skipped": 0}
    batch_idx = 0
    with browser() as b:
        while True:
            batch = db.pending_queue(limit=config.BATCH_SIZE, retry_errors=retry_errors)
            if not batch:
                break
            for row in batch:
                nid = row["note_id"]
                try:
                    ok = scrape_note(b, nid, row["xsec_token"], row["xsec_source"],
                                     comment_pages=comment_pages,
                                     download_images=download_images)
                    if ok:
                        db.mark_queue(nid, "done")
                        counts["ok"] += 1
                    else:
                        db.mark_queue(nid, "error", "no_data")
                        counts["fail"] += 1
                except Exception as e:
                    logger.exception("scrape_note crashed for {}", nid)
                    db.mark_queue(nid, "error", str(e))
                    counts["fail"] += 1
                b.sleep_between_notes()
                if max_notes and (counts["ok"] + counts["fail"]) >= max_notes:
                    return counts
            batch_idx += 1
            b.sleep_between_batches()
    return counts
