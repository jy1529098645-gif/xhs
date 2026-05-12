"""Account warmup: simulate organic browsing to lift a soft-ban.

Goal: get xhs's anti-fraud system to issue the missing id_token by making
the account look like a real user. Strategy:
  - Open /explore and scroll the home feed naturally (variable pace)
  - Visit a few creator profiles (NOT note detail pages — those are blocked)
  - Click 关注 on visible follow buttons (real users do this)
  - Hover/scroll back occasionally (mimics human re-reading)

Idempotent: follow actions are easily reversible, no destructive ops.
Best-effort: skips silently when a selector doesn't match.
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402

from crawler import config  # noqa: E402
from crawler.browser import browser  # noqa: E402


def slow_scroll(page, max_steps: int = 5):
    """Scroll down a handful of times with realistic pauses, sometimes
    scrolling back up briefly (humans re-read what scrolled past)."""
    for _ in range(random.randint(2, max_steps)):
        page.scroll.down(random.randint(450, 900))
        time.sleep(random.uniform(2.5, 5.0))
        if random.random() < 0.25:
            page.scroll.up(random.randint(100, 300))
            time.sleep(random.uniform(1.0, 2.0))
            page.scroll.down(random.randint(150, 400))
            time.sleep(random.uniform(1.0, 2.5))


def try_find_creator_links(page) -> list:
    """xhs class names are obfuscated; try several reasonable selectors."""
    candidates: list = []
    for sel in (
        "css:a[href*='/user/profile/']",
        "css:.author-wrapper a",
        "css:.user-name",
    ):
        try:
            els = page.eles(sel, timeout=1)
            if els:
                candidates.extend(els)
        except Exception:
            continue
    # Dedupe by href
    seen = set()
    out = []
    for e in candidates:
        try:
            h = e.attr("href") or ""
        except Exception:
            continue
        if "/user/profile/" in h and h not in seen:
            seen.add(h)
            out.append(e)
    return out


def click_follow_on_profile(page) -> bool:
    """Attempt to click a follow button on the current user-profile page.
    Returns True if a button was clicked.
    """
    # The follow CTA text varies: 关注 / + 关注 / Follow
    for selector in (
        "css:.follow-btn",
        "css:button.follow",
        "css:.user-page-info .follow",
    ):
        try:
            el = page.ele(selector, timeout=2)
            if el:
                el.click()
                return True
        except Exception:
            pass
    # Last resort: text matcher
    try:
        el = page.ele("text=关注", timeout=2)
        if el:
            # Skip if it's actually the 已关注 / 互相关注 state
            txt = (el.text or "").strip()
            if txt in ("关注", "+ 关注", "+关注"):
                el.click()
                return True
    except Exception:
        pass
    return False


def main(target_follows: int = 5, total_minutes: int = 12):
    deadline = time.time() + total_minutes * 60
    with browser(headless=False) as b:
        logger.info("Opening xhs explore feed...")
        if not b.goto(f"{config.XHS_HOST}/explore"):
            logger.error("Couldn't open explore"); return
        time.sleep(random.uniform(4, 7))

        cookies = {c.get("name") for c in b.page.cookies()}
        if "web_session" not in cookies:
            logger.error("Not logged in (no web_session cookie). Run login first.")
            return
        logger.info("Cookies present: {}", sorted(cookies))

        followed = 0
        scroll_rounds = 0
        page = b.page

        while time.time() < deadline:
            scroll_rounds += 1
            logger.info("Round #{} scroll", scroll_rounds)
            slow_scroll(page, max_steps=random.randint(3, 6))

            # Every few rounds, visit a creator profile + try to follow
            if followed < target_follows and scroll_rounds % random.randint(2, 4) == 0:
                links = try_find_creator_links(page)
                random.shuffle(links)
                if not links:
                    logger.info("No creator links found this round; keep scrolling")
                    continue
                target = links[0]
                href = target.attr("href")
                logger.info("Visiting creator: {}", href[:80] if href else "?")
                try:
                    target.click()
                except Exception as e:
                    logger.debug("creator click failed: {}", e); continue
                time.sleep(random.uniform(4, 7))
                # Scroll a bit on profile (real users look at posts)
                slow_scroll(page, max_steps=random.randint(2, 4))
                if click_follow_on_profile(page):
                    followed += 1
                    logger.success("Followed creator (#{})", followed)
                    time.sleep(random.uniform(3, 6))
                else:
                    logger.info("No follow button matched (may already follow, or selector miss)")
                # Back to feed
                b.goto(f"{config.XHS_HOST}/explore")
                time.sleep(random.uniform(4, 7))

            # Occasional longer pause (humans don't scroll constantly)
            if random.random() < 0.2:
                pause = random.uniform(15, 35)
                logger.info("Idle pause {:.0f}s (simulating reading)", pause)
                time.sleep(pause)

        logger.success("Warmup complete: {} follows over {:.0f} min, {} scroll rounds",
                       followed, total_minutes, scroll_rounds)


if __name__ == "__main__":
    main()
