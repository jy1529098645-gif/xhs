"""High-speed note detail scraper using curl_cffi (Chrome TLS impersonation).

Why this exists: xhs serves the full note data in the SSR HTML's
`window.__INITIAL_STATE__`. Plain httpx/requests get 302→error 300013 because
xhs sniffs JA3/JA4 TLS fingerprint and identifies non-browser clients. We use
curl_cffi with `chrome131` impersonation so xhs sees a real-Chrome-like
handshake and serves the actual note HTML.

Tradeoffs:
  + 50-100x faster than Chrome (no JS, no rendering)
  + Anonymous — no account, no risk
  + Easily parallelizable via thread pool
  - Comments not in SSR (xhs loads via signed API). We only capture
    __INITIAL_STATE__.comments.list (usually empty).
"""
from __future__ import annotations

import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from curl_cffi import requests as cffi_requests
from loguru import logger

from . import config, db
from .parse import parse_initial_state


_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", re.DOTALL
)
_UNDEFINED_RE = re.compile(r":\s*undefined([,}\]])")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

IMPERSONATE = "chrome131"


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


def _scrape_one(note_id: str, xsec_token: Optional[str],
                xsec_source: Optional[str],
                download_images: bool) -> tuple[str, bool, str]:
    url = _note_url(note_id, xsec_token, xsec_source)
    try:
        r = cffi_requests.get(
            url,
            impersonate=IMPERSONATE,
            timeout=25,
            allow_redirects=False,
            headers={"Referer": config.XHS_HOST + "/"},
        )
    except Exception as e:
        return note_id, False, f"http_error:{type(e).__name__}"
    if r.status_code != 200:
        if r.status_code in (301, 302):
            loc = r.headers.get("Location", "")
            if "/login" in loc or "error_code=300013" in loc:
                return note_id, False, "rate_limited"
            return note_id, False, f"redirect_to:{loc[:60]}"
        return note_id, False, f"status:{r.status_code}"
    if "/login" in r.text[:500] and "__INITIAL_STATE__" not in r.text:
        return note_id, False, "login_wall"
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
        _download_images(note_id, images)

    return note_id, True, f"ok ({len(ssr_comments or [])} ssr comments)"


def _download_images(note_id: str, imgs: list[dict]):
    target_dir = config.IMAGES_DIR / note_id
    target_dir.mkdir(parents=True, exist_ok=True)
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
            r = cffi_requests.get(
                url.replace("http://", "https://"),
                impersonate=IMPERSONATE,
                timeout=20,
                headers={"Referer": config.XHS_HOST + "/"},
            )
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


def run_http_drain(concurrency: int = 8, retry_errors: bool = False,
                   max_notes: Optional[int] = None,
                   download_images: bool = True) -> dict:
    counts = {"ok": 0, "fail": 0, "skipped": 0, "rate_limited": 0}
    logger.info("curl_cffi drain starting: concurrency={} impersonate={}",
                concurrency, IMPERSONATE)

    def task(row):
        nid = row["note_id"]
        try:
            _, ok, reason = _scrape_one(
                nid, row["xsec_token"], row["xsec_source"],
                download_images=download_images,
            )
        except Exception as e:
            db.mark_queue(nid, "error", f"crash:{type(e).__name__}")
            counts["fail"] += 1
            return
        if ok:
            db.mark_queue(nid, "done")
            counts["ok"] += 1
        elif reason == "rate_limited":
            db.release_claim(nid, "rate_limited")
            counts["rate_limited"] += 1
        else:
            db.mark_queue(nid, "error", reason)
            counts["fail"] += 1
        # gentle pacing per worker
        time.sleep(random.uniform(0.3, 1.0))

    consecutive_rate_limited = 0
    while True:
        batch = db.claim_queue_items(
            limit=concurrency * 3, retry_errors=retry_errors
        )
        if not batch:
            break
        before_ok = counts["ok"]
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            list(ex.map(task, batch))
        new_ok = counts["ok"] - before_ok
        logger.info("batch done: +{}ok / {}total  (cum: {}ok / {}fail / {}rl)",
                    new_ok, len(batch), counts["ok"], counts["fail"],
                    counts["rate_limited"])
        # adaptive backoff if rate limit creeps in
        if new_ok == 0:
            consecutive_rate_limited += 1
            if consecutive_rate_limited >= 2:
                logger.warning("2 batches w/ 0 success → 30s cooldown")
                time.sleep(30)
                consecutive_rate_limited = 0
        else:
            consecutive_rate_limited = 0
        if max_notes and counts["ok"] >= max_notes:
            break
    return counts


def run_http_drain_sync(**kwargs) -> dict:
    return run_http_drain(**kwargs)
