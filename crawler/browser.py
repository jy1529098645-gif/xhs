"""DrissionPage wrapper: stealth Chrome with API network listener and pacing."""
import json
import random
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from DrissionPage import ChromiumOptions, ChromiumPage
from DrissionPage._units.listener import DataPacket
from loguru import logger

from . import config
from . import db


class Browser:
    """Wraps a ChromiumPage. Use as a singleton per process."""

    def __init__(self, headless: bool = False):
        opts = ChromiumOptions()
        opts.set_user_data_path(str(config.BROWSER_PROFILE_DIR))
        opts.set_argument("--lang=zh-CN")
        opts.set_argument("--disable-blink-features=AutomationControlled")
        opts.set_argument("--no-default-browser-check")
        opts.set_argument("--no-first-run")
        if headless:
            opts.headless(True)
        self.page = ChromiumPage(addr_or_opts=opts)
        self.page.set.window.size(1440, 900)
        self._listening = False

    # ----- network listener -----

    def start_listen(self) -> None:
        if not self._listening:
            self.page.listen.start(config.LISTEN_PREFIX)
            self._listening = True

    def stop_listen(self) -> None:
        if self._listening:
            self.page.listen.stop()
            self._listening = False

    def collect_packets(self, timeout: float = 8.0,
                        max_count: Optional[int] = None) -> list[DataPacket]:
        """Drain all packets that have arrived within `timeout` seconds of inactivity."""
        out: list[DataPacket] = []
        deadline = time.time() + timeout
        idle = 1.5  # seconds of no new packets before we stop early
        last = time.time()
        while time.time() < deadline:
            pkt = self.page.listen.wait(timeout=0.8, fit_count=False)
            if pkt:
                if isinstance(pkt, list):
                    out.extend(pkt)
                else:
                    out.append(pkt)
                last = time.time()
                if max_count and len(out) >= max_count:
                    break
            elif time.time() - last > idle and out:
                break
        return out

    @staticmethod
    def packet_kind(pkt: DataPacket) -> Optional[str]:
        url = pkt.url or ""
        for kind, path in config.API_PATTERNS.items():
            if path in url:
                return kind
        return None

    @staticmethod
    def packet_json(pkt: DataPacket) -> Optional[dict]:
        try:
            body = pkt.response.body
            if isinstance(body, dict):
                return body
            if isinstance(body, (bytes, bytearray)):
                body = body.decode("utf-8", errors="replace")
            if isinstance(body, str):
                return json.loads(body)
        except Exception as e:
            logger.warning("Failed to parse packet body: {}", e)
        return None

    # ----- pacing -----

    def sleep_between_notes(self) -> None:
        delay = random.uniform(config.DELAY_MIN_SEC, config.DELAY_MAX_SEC)
        logger.debug("Sleeping {:.1f}s", delay)
        time.sleep(delay)

    def sleep_between_batches(self) -> None:
        delay = random.uniform(config.BATCH_PAUSE_MIN_SEC, config.BATCH_PAUSE_MAX_SEC)
        logger.info("Batch pause: {:.0f}s", delay)
        time.sleep(delay)

    def risk_pause(self, reason: str = "") -> None:
        logger.warning("RISK pause for {}s: {}", config.RISK_PAUSE_SEC, reason)
        db.log_event("warning", "risk_pause", {"reason": reason,
                                                "seconds": config.RISK_PAUSE_SEC})
        time.sleep(config.RISK_PAUSE_SEC)

    # ----- low-level helpers -----

    def goto(self, url: str, retries: int = 2) -> bool:
        for attempt in range(retries + 1):
            try:
                self.page.get(url)
            except Exception as e:
                logger.warning("goto({}) attempt {} failed: {}", url, attempt, e)
                time.sleep(3 * (attempt + 1))
                continue
            if self._looks_blocked():
                # real risk control — pause once and bail; retrying immediately
                # would just hit the same overlay.
                self.risk_pause(f"blocked at {url}")
                return False
            return True
        return False

    def _looks_blocked(self) -> bool:
        """Conservative block detection — only fires on real risk-control signals.

        We DELIBERATELY do not scan page HTML, because xhs's normal pages contain
        the words 'verify' / 'captcha' in unrelated contexts (form placeholders,
        bundled JS strings, etc.). Looking at URL redirects, page title and a
        specific captcha overlay element is much more reliable.
        """
        try:
            url = self.page.url or ""
            if "/captcha" in url or "verifyType=" in url or "/web_login/captcha" in url:
                return True
            title = self.page.title or ""
            for m in ("访问受限", "环境异常", "请完成安全验证", "请完成验证", "异常访问"):
                if m in title:
                    return True
            # XHS / Aliyun slider captcha overlays
            if self.page.ele(
                "css:#captcha-verify-image, .nc_wrapper, #nc_1_wrapper, "
                ".captcha-wrap, .red-captcha",
                timeout=0.2,
            ):
                return True
        except Exception:
            return False
        return False

    # Safe-stringify: xhs hydrates __INITIAL_STATE__ into a Vue reactive Proxy
    # which is full of circular `dep` / `effect` references. Plain JSON.stringify
    # blows up; a replacer that drops repeat visits keeps the data we care about.
    _SAFE_STRINGIFY = """
    (function(){
        const root = window.__INITIAL_STATE__;
        if (!root) return null;
        const seen = new WeakSet();
        const SKIP_KEYS = new Set(['dep','effect','_effect','_vnode','__v_isRef',
            'subs','subsHead','flags','version','globalVersion']);
        return JSON.stringify(root, function(k, v){
            if (SKIP_KEYS.has(k)) return undefined;
            if (v !== null && typeof v === 'object'){
                if (seen.has(v)) return undefined;
                seen.add(v);
            }
            return v;
        });
    })()
    """

    def read_initial_state(self) -> Optional[dict]:
        """Pull window.__INITIAL_STATE__ from the page.

        xhs SSRs note data into this global, then Vue wraps it in reactive
        proxies. Plain JSON.stringify hits a circular reference; we use a
        replacer to skip Vue internals + repeat visits.
        """
        raw = None
        try:
            raw = self.page.run_js(f"return {self._SAFE_STRINGIFY};")
        except Exception as e:
            logger.debug("read_initial_state safe-stringify failed: {}", e)

        # Fallback: regex-extract the JSON literal from the SSR HTML.
        # xhs injects: <script>window.__INITIAL_STATE__={...}</script>
        if not raw or raw == "null":
            raw = self._extract_state_from_html()

        if not raw or raw == "null":
            return None
        try:
            return json.loads(raw)
        except Exception as e:
            logger.warning("read_initial_state JSON parse failed: {} (raw[:200]={})",
                           e, str(raw)[:200])
            return None

    def _extract_state_from_html(self) -> Optional[str]:
        try:
            html = self.page.html or ""
        except Exception:
            return None
        import re
        # The injected literal is JS-style (with `undefined`, single quotes, etc.).
        # We capture the balanced-ish object then sanitize.
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
                      html, re.DOTALL)
        if not m:
            return None
        js_obj = m.group(1)
        # xhs literally writes `undefined` as a value; replace with null so JSON parses.
        js_obj = re.sub(r":\s*undefined([,}\]])", r":null\1", js_obj)
        return js_obj

    def scroll_to_bottom(self, max_steps: int = 12, step_px: int = 800) -> None:
        for _ in range(max_steps):
            self.page.scroll.down(step_px)
            time.sleep(random.uniform(0.6, 1.2))

    def close(self) -> None:
        try:
            self.stop_listen()
        finally:
            self.page.quit()


@contextmanager
def browser(headless: bool = False) -> Iterator[Browser]:
    b = Browser(headless=headless)
    try:
        yield b
    finally:
        b.close()
