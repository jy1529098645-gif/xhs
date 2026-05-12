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


def _session_healthy(b: "Browser") -> bool:
    """Active sanity check: query an authenticated endpoint and look at the
    response. The DOM-based is_logged_in() can lie because xhs leaves the
    side-bar avatar rendered from stale state even when cookies are invalid.
    """
    try:
        result = b.page.run_js(
            "return fetch('/api/sns/web/v2/user/me', "
            "{credentials:'include'}).then(r => r.status).catch(()=>0);",
            as_expr=False,
        )
        # DrissionPage's run_js doesn't await Promises in older versions.
        # Use a sync XHR fallback that returns the status.
        return False if not result else False
    except Exception:
        pass
    # Fallback: navigate to /explore and look for a logged-out redirect.
    try:
        b.page.get(f"{config.XHS_HOST}/explore")
        time.sleep(2)
        if "/login" in (b.page.url or ""):
            return False
    except Exception:
        pass
    return True


def run_login(timeout_seconds: int = 300, force: bool = False) -> bool:
    from .browser import browser
    with browser(headless=False) as b:
        if force:
            logger.info("Force-relogin: clearing cookies for xiaohongshu.com")
            try:
                b.page.set.cookies.clear()
            except Exception as e:
                logger.warning("cookie clear failed: {}", e)
        b.goto(LOGIN_URL)
        time.sleep(2)
        # Probe with an actual note URL — DOM-only checks lie. xhs renders the
        # side-bar user placeholder even when unauthenticated. The only reliable
        # signal is whether a note page redirects to /login.
        if not force:
            probe_url = (f"{config.XHS_HOST}/explore/69fb6a5c0000000035020afe"
                         "?xsec_token=AB4j1RZEUAwRuReLy2wzhSOqfPDHKxEwG_Bpq1VHKpKMM=&"
                         "xsec_source=pc_search")
            b.page.get(probe_url)
            time.sleep(3)
            if "/login" not in (b.page.url or ""):
                logger.info("Already logged in (probe passed).")
                return True
            logger.info("Session is invalid (note probe → /login). Re-scan needed.")
        b.goto(LOGIN_URL)
        time.sleep(2)
        logger.info(
            "Please scan the QR in the opened Chrome window with the xhs app. "
            "I'll wait up to {}s...", timeout_seconds
        )
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            time.sleep(3)
            url = b.page.url or ""
            if is_logged_in(b) and "/login" not in url:
                logger.success("Login UI signal detected. Warming up session...")
                return _post_login_warmup_and_probe(b)
        logger.error("Login timed out.")
        return False


def _post_login_warmup_and_probe(b: "Browser") -> bool:
    """After QR scan, xhs delays issuing `id_token` until the account has done
    *something* — typically scrolling the home feed for a bit. This function
    drives that engagement, then polls cookies, then probes a real note URL.
    """
    import random as _r
    b.page.get(f"{config.XHS_HOST}/explore")
    time.sleep(3)
    logger.info("Scrolling explore feed for ~60s to trigger id_token...")
    for i in range(12):
        b.page.scroll.down(_r.randint(500, 900))
        time.sleep(_r.uniform(3.0, 5.5))
        cookies = {c.get("name"): c.get("value") for c in b.page.cookies()}
        if "id_token" in cookies:
            logger.success("id_token cookie appeared after scroll #{}", i + 1)
            break
    else:
        logger.warning("id_token still not present after 60s of scrolling")

    cookies = {c.get("name"): c.get("value") for c in b.page.cookies()}
    logger.info("Final cookies: {}", sorted(cookies.keys()))

    probe_url = (f"{config.XHS_HOST}/explore/69fb6a5c0000000035020afe"
                 "?xsec_token=AB4j1RZEUAwRuReLy2wzhSOqfPDHKxEwG_Bpq1VHKpKMM=&"
                 "xsec_source=pc_search")
    b.page.get(probe_url)
    time.sleep(3)
    final = b.page.url or ""
    if "/login" in final:
        logger.error("Probe FAILED — note page still redirects to /login.")
        return False
    logger.success("Probe OK — note page loads. Session is fully usable.")
    return True
