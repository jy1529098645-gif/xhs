"""First-run login: open xhs and let the user scan the QR with their account.

Cookies are persisted automatically because DrissionPage uses the profile dir
configured in browser.py. We just need to verify the session works.
"""
import time

from loguru import logger

from . import config
from .browser import Browser


LOGIN_URL = f"{config.XHS_HOST}/explore"


def is_logged_in(b: Browser) -> bool:
    """Heuristic: the side menu shows a user avatar / username when logged in."""
    try:
        # 'side-bar-component .user' is the logged-in profile entry
        el = b.page.ele("css:.side-bar .user, .side-bar-component .user", timeout=3)
        if el:
            return True
        # fallback: presence of any avatar img in side nav
        el = b.page.ele("css:.side-bar img.user-avatar", timeout=1)
        return el is not None
    except Exception:
        return False


def run_login(timeout_seconds: int = 300) -> bool:
    from .browser import browser
    with browser(headless=False) as b:
        b.goto(LOGIN_URL)
        if is_logged_in(b):
            logger.info("Already logged in.")
            return True
        logger.info(
            "Please scan the QR in the opened Chrome window with the xhs app. "
            "I'll wait up to {}s...", timeout_seconds
        )
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            time.sleep(3)
            if is_logged_in(b):
                logger.success("Login detected. Cookies will persist in the profile dir.")
                # nudge a couple navigations so xhs writes all its cookies
                b.goto(f"{config.XHS_HOST}/explore")
                time.sleep(2)
                return True
        logger.error("Login timed out.")
        return False
