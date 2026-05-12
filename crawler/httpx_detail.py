"""High-speed note detail scraper using direct HTTP (no browser).

Why this exists: xhs serves the full note data in the SSR HTML's
`window.__INITIAL_STATE__`. Chrome's anti-bot JS then runs and may redirect
to /login based on fingerprint detection. By using plain HTTP we get the
SSR data WITHOUT the JS-based bounce.

Tradeoffs:
  + 50-100x faster than Chrome (no JS, no rendering, ~50KB/s vs ~30s/note)
  + Anonymously — no account, no risk
  + Easily parallelizable to dozens of concurrent requests
  - Comments are not in SSR (xhs loads them later via signed API).
    We capture only what's embedded in __INITIAL_STATE__.comments.list.
    For the in-feed note vault we already have 1497 comments from the
    DrissionPage path — those stay.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Optional

import httpx
from loguru import logger

from . import config, db
from .parse import parse_initial_state


_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", re.DOTALL
)
_UNDEFINED_RE = re.compile(r":\s*undefined([,}\]])")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _extract_state(html: str) -> Optional[dict]:
    m = _STATE_RE.search(html)
    if not m:
        return None
    js = _UNDEFINED_RE.sub(r":null\1", m.group(1))
    try:
        return json.loads(js)
    except Exception:
        return None


def _note_url(nid: str, xsec_token: Optional[str], xsec_source: Optional[str]) -> str:
    qs = []
    if xsec_token:
        qs.append(f"xsec_token={xsec_token}")
    qs.append(f"xsec_source={xsec_source or 'pc_search'}")
    return f"{config.XHS_HOST}/explore/{nid}?{'&'.join(qs)}"


async def _scrape_one(client: httpx.AsyncClient, note_id: str,
                      xsec_token: Optional[str], xsec_source: Optional[str],
                      download_images: bool) -> tuple[str, bool, str]:
    url = _note_url(note_id, xsec_token, xsec_source)
    try:
        r = await client.get(url, headers={"User-Agent": UA, "Referer": config.XHS_HOST + "/"})
    except Exception as e:
        return note_id, False, f"http_error:{type(e).__name__}:{e}"
    if r.status_code != 200:
        return note_id, False, f"status:{r.status_code}"
    if "/login" in str(r.url):
        return note_id, False, "redirected_to_login"
    state = _extract_state(r.text)
    if not state:
        return note_id, False, "no_initial_state"

    parsed = parse_initial_state(state, note_id)
    if not parsed:
        return note_id, False, "no_note_in_state"
    note, images, ssr_comments = parsed
    note["url"] = url
    note["note_id"] = note_id
    if xsec_token and not note.get("xsec_token"):
        note["xsec_token"] = xsec_token

    db.upsert_note(note, state)
    db.upsert_images(note_id, images or [])
    if ssr_comments:
        db.upsert_comments(ssr_comments)

    if download_images and images:
        await _download_images(client, note_id, images)

    return note_id, True, f"ok ({len(ssr_comments or [])} ssr comments)"


async def _download_images(client: httpx.AsyncClient, note_id: str, imgs: list[dict]):
    from pathlib import Path
    target_dir = config.IMAGES_DIR / note_id
    target_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": UA, "Referer": config.XHS_HOST + "/"}
    for i, img in enumerate(imgs):
        url = img.get("url")
        if not url:
            continue
        ext = _guess_ext(url)
        out = target_dir / f"{i:02d}{ext}"
        if out.exists() and out.stat().st_size > 0:
            db.set_image_local(note_id, i, str(out))
            continue
        try:
            r = await client.get(url.replace("http://", "https://"), headers=headers)
            if r.status_code == 200 and r.content:
                out.write_bytes(r.content)
                db.set_image_local(note_id, i, str(out))
        except Exception:
            pass


def _guess_ext(url: str) -> str:
    lower = url.lower().split("?", 1)[0]
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if lower.endswith(ext):
            return ext
    return ".jpg"


async def run_http_drain(concurrency: int = 10, retry_errors: bool = False,
                         max_notes: Optional[int] = None,
                         download_images: bool = True) -> dict:
    proxies = config.load_proxies() or [None]
    counts = {"ok": 0, "fail": 0, "skipped": 0}
    logger.info("HTTP drain starting: concurrency={}, {} proxies",
                concurrency, len([p for p in proxies if p]))

    # Build N httpx clients, each with its own proxy (round-robin).
    # Disable keep-alive: each request opens a fresh TCP to the local proxy,
    # which opens a fresh TCP to Decodo, which rotates to a fresh IP. With
    # keep-alive on, multiple requests would share one upstream connection and
    # hence one IP — exactly what triggers xhs's per-IP rate limit.
    async def make_client(idx: int) -> httpx.AsyncClient:
        proxy = proxies[idx % len(proxies)]
        return httpx.AsyncClient(
            proxy=proxy, http2=False, follow_redirects=False,
            timeout=httpx.Timeout(20.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=0,
                                max_connections=10),
            headers={"Connection": "close"},
        )

    sem = asyncio.Semaphore(concurrency)
    clients = [await make_client(i) for i in range(concurrency)]

    rate_limited_streak = {"n": 0}  # counter to slow down if many 302s

    async def work(idx: int, row):
        async with sem:
            client = clients[idx % concurrency]
            nid = row["note_id"]
            try:
                _, ok, reason = await _scrape_one(
                    client, nid, row["xsec_token"], row["xsec_source"],
                    download_images=download_images,
                )
                if ok:
                    db.mark_queue(nid, "done")
                    counts["ok"] += 1
                    rate_limited_streak["n"] = max(0, rate_limited_streak["n"] - 1)
                    if counts["ok"] % 10 == 0:
                        logger.info("http_drain: {} done, {} fail",
                                    counts["ok"], counts["fail"])
                else:
                    if reason == "status:302":
                        # xhs IP throttle. Release back to pending so we'll
                        # retry after the IP pool rotates more.
                        db.release_claim(nid, "rate_limited")
                        rate_limited_streak["n"] += 1
                    else:
                        db.mark_queue(nid, "error", reason)
                        counts["fail"] += 1
            except Exception as e:
                db.mark_queue(nid, "error", f"crash:{type(e).__name__}")
                counts["fail"] += 1
            # Adaptive backoff: if we've seen many recent 302s, sleep longer
            base = 1.5 if rate_limited_streak["n"] < 5 else 6.0
            await asyncio.sleep(random.uniform(base, base * 2))

    try:
        idx = 0
        while True:
            batch = db.claim_queue_items(limit=concurrency * 2, retry_errors=retry_errors)
            if not batch:
                break
            tasks = [asyncio.create_task(work(idx + i, r)) for i, r in enumerate(batch)]
            await asyncio.gather(*tasks, return_exceptions=True)
            idx += len(batch)
            if max_notes and counts["ok"] >= max_notes:
                break
    finally:
        for c in clients:
            await c.aclose()
    return counts


def run_http_drain_sync(**kwargs) -> dict:
    return asyncio.run(run_http_drain(**kwargs))
