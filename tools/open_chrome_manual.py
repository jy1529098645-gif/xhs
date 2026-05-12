"""Open the crawler's Chrome and just keep it alive for manual driving.

No automation, no scrolling, no probing. The user does whatever they want
in the window (log in, scroll, click around, anything). We log the cookie
state every 30 seconds so you can see when (or if) `id_token` shows up.

Press Ctrl+C in the terminal (or close the window) to exit.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crawler import config  # noqa: E402
from crawler.browser import browser  # noqa: E402


WATCH_INTERVAL = 30   # seconds between cookie reports
TOTAL_MINUTES = 60    # keep open at most this long


def main():
    deadline = time.time() + TOTAL_MINUTES * 60
    print(f"[manual] opening Chrome with the crawler's profile; will stay alive "
          f"up to {TOTAL_MINUTES} minutes. Do whatever you want in the window.",
          flush=True)
    with browser(headless=False) as b:
        b.page.get(f"{config.XHS_HOST}/explore")
        print("[manual] loaded /explore. Cookie watcher starts now.\n", flush=True)
        last_state = None
        while time.time() < deadline:
            time.sleep(WATCH_INTERVAL)
            try:
                cookies = sorted({c.get("name") for c in b.page.cookies()})
            except Exception as e:
                print(f"[manual] cookie read failed: {e}", flush=True)
                continue
            state = tuple(cookies)
            has_id_token = "id_token" in cookies
            has_session = "web_session" in cookies
            marker = ("ID_TOKEN" if has_id_token
                      else "no-id_token (web_session={})".format(has_session))
            url = (b.page.url or "")[:80]
            if state != last_state:
                print(f"[manual] {time.strftime('%H:%M:%S')} "
                      f"url={url}\n         cookies({len(cookies)}): {cookies}\n         "
                      f"→ {marker}", flush=True)
                last_state = state
            else:
                print(f"[manual] {time.strftime('%H:%M:%S')} no change "
                      f"({len(cookies)} cookies, {marker}, url ends ...{url[-40:]})",
                      flush=True)
        print("[manual] time limit reached, closing.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[manual] stopped by user", flush=True)
