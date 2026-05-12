"""Four ways to populate the discover_queue.

A. search    — for each keyword, scroll N pages of search results
B. author    — for each user_id, scroll their profile until no new notes
C. topic     — for each tag/topic name, scroll N pages
D. urls      — read note URLs from a file (must include xsec_token)
"""
import re
import time
import urllib.parse as up
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

from . import config, db
from .browser import Browser, browser
from .parse import parse_note_card


_NOTE_URL_RE = re.compile(
    r"xiaohongshu\.com/(?:explore|discovery/item)/([0-9a-f]+)(?:\?[^ ]*)?",
    re.IGNORECASE,
)
_XSEC_RE = re.compile(r"[?&]xsec_token=([^&]+)")
_XSRC_RE = re.compile(r"[?&]xsec_source=([^&]+)")


# ---------------- A: search ----------------

def discover_search(keywords: list[str], pages: int = 10,
                    sort: str = "general") -> int:
    """sort: general (default), time_descending, popularity_descending."""
    total = 0
    with browser() as b:
        for kw in keywords:
            count = _discover_search_one(b, kw, pages=pages, sort=sort)
            logger.success("[search] '{}': {} notes", kw, count)
            total += count
            b.sleep_between_batches()
    return total


def _discover_search_one(b: Browser, keyword: str, pages: int, sort: str) -> int:
    b.start_listen()
    q = up.quote(keyword)
    url = (f"{config.XHS_HOST}/search_result?keyword={q}"
           f"&source=web_explore_feed&type=51&sort={sort}")
    if not b.goto(url):
        return 0
    time.sleep(3)
    collected = 0
    seen_ids: set[str] = set()
    for page_i in range(pages):
        pkts = b.collect_packets(timeout=5.0)
        for p in pkts:
            if Browser.packet_kind(p) != "search_notes":
                continue
            j = Browser.packet_json(p)
            if not j:
                continue
            items = (j.get("data") or {}).get("items") or []
            cards = []
            for it in items:
                rec = parse_note_card(it, source_type="search", source_value=keyword)
                if rec and rec["note_id"] not in seen_ids:
                    cards.append(rec)
                    seen_ids.add(rec["note_id"])
            collected += db.upsert_queue_items(cards)

        # scroll for next page
        b.page.scroll.down(1500)
        time.sleep(1.5 + 0.5 * (page_i % 3))
        if _at_bottom(b):
            logger.info("[search] '{}' reached bottom at page {}", keyword, page_i + 1)
            break
    return collected


# ---------------- B: author ----------------

def discover_author(user_ids: list[str], max_per_user: int = 200) -> int:
    total = 0
    with browser() as b:
        for uid in user_ids:
            count = _discover_author_one(b, uid, max_notes=max_per_user)
            logger.success("[author] {}: {} notes", uid, count)
            total += count
            b.sleep_between_batches()
    return total


def _discover_author_one(b: Browser, user_id: str, max_notes: int) -> int:
    b.start_listen()
    url = f"{config.XHS_HOST}/user/profile/{user_id}"
    if not b.goto(url):
        return 0
    time.sleep(3)
    collected = 0
    seen_ids: set[str] = set()
    # author info from packets
    pkts = b.collect_packets(timeout=5.0)
    _capture_author_packets(pkts)
    _capture_user_posted(pkts, user_id, collected_ids=seen_ids, out_counter=lambda: None)
    while collected < max_notes:
        b.page.scroll.down(1500)
        time.sleep(1.5)
        new_pkts = b.collect_packets(timeout=4.0)
        _capture_author_packets(new_pkts)
        new_cards = []
        for p in new_pkts:
            if Browser.packet_kind(p) != "user_posted":
                continue
            j = Browser.packet_json(p)
            if not j:
                continue
            items = (j.get("data") or {}).get("notes") or []
            for it in items:
                rec = parse_note_card(it, source_type="author", source_value=user_id)
                if rec and rec["note_id"] not in seen_ids:
                    new_cards.append(rec)
                    seen_ids.add(rec["note_id"])
        if not new_cards:
            # no more notes
            if _at_bottom(b):
                break
            continue
        collected += db.upsert_queue_items(new_cards)
    return collected


def _capture_user_posted(pkts, user_id, collected_ids, out_counter):
    for p in pkts:
        if Browser.packet_kind(p) != "user_posted":
            continue
        j = Browser.packet_json(p)
        if not j:
            continue
        items = (j.get("data") or {}).get("notes") or []
        cards = []
        for it in items:
            rec = parse_note_card(it, source_type="author", source_value=user_id)
            if rec and rec["note_id"] not in collected_ids:
                cards.append(rec)
                collected_ids.add(rec["note_id"])
        db.upsert_queue_items(cards)


def _capture_author_packets(pkts) -> None:
    from .parse import parse_user_info
    for p in pkts:
        if Browser.packet_kind(p) != "user_other":
            continue
        j = Browser.packet_json(p)
        if not j:
            continue
        ui = parse_user_info(j)
        if ui and ui.get("user_id"):
            db.upsert_author(ui, j)


# ---------------- C: topic ----------------

def discover_topic(names: list[str], pages: int = 10) -> int:
    """Topic discovery via the search endpoint with type=hashtag.

    xhs's topic pages forward to the search feed under the hood; this is the
    most reliable cross-account route.
    """
    total = 0
    with browser() as b:
        for name in names:
            count = _discover_topic_one(b, name, pages=pages)
            logger.success("[topic] '{}': {} notes", name, count)
            total += count
            b.sleep_between_batches()
    return total


def _discover_topic_one(b: Browser, name: str, pages: int) -> int:
    b.start_listen()
    q = up.quote(name)
    # Hashtag-style search; xhs treats #name as a topic filter
    url = (f"{config.XHS_HOST}/search_result?keyword={q}"
           f"&source=web_note_detail_r10&type=54")  # 54 = topic-scoped
    if not b.goto(url):
        return 0
    time.sleep(3)
    collected = 0
    seen_ids: set[str] = set()
    for page_i in range(pages):
        pkts = b.collect_packets(timeout=5.0)
        for p in pkts:
            if Browser.packet_kind(p) != "search_notes":
                continue
            j = Browser.packet_json(p)
            if not j:
                continue
            items = (j.get("data") or {}).get("items") or []
            cards = []
            for it in items:
                rec = parse_note_card(it, source_type="topic", source_value=name)
                if rec and rec["note_id"] not in seen_ids:
                    cards.append(rec)
                    seen_ids.add(rec["note_id"])
            collected += db.upsert_queue_items(cards)
        b.page.scroll.down(1500)
        time.sleep(1.5)
        if _at_bottom(b):
            break
    return collected


# ---------------- D: URL list ----------------

def discover_urls(path: Path) -> int:
    """Read URLs from file (one per line). Extract note_id + xsec_token."""
    if not path.exists():
        raise FileNotFoundError(path)
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _NOTE_URL_RE.search(line)
        if not m:
            logger.warning("URL not recognized: {}", line)
            continue
        nid = m.group(1)
        xt = _XSEC_RE.search(line)
        xs = _XSRC_RE.search(line)
        items.append({
            "note_id": nid,
            "xsec_token": up.unquote(xt.group(1)) if xt else None,
            "xsec_source": up.unquote(xs.group(1)) if xs else None,
            "source_type": "url",
            "source_value": str(path.name),
        })
    n = db.upsert_queue_items(items)
    logger.success("[urls] queued {} from {}", n, path)
    return n


# ---------------- helpers ----------------

def _at_bottom(b: Browser) -> bool:
    try:
        ele = b.page.ele("text:已经到底啦", timeout=0.5) or b.page.ele(
            "text:没有更多", timeout=0.5)
        return ele is not None
    except Exception:
        return False
