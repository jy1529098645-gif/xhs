"""Test whether xhs serves note data to fully anonymous (no cookies) users.

If yes — we have a path that doesn't depend on accounts at all.
If no — anonymous redirects to /login same as soft-banned accounts, no shortcut.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crawler import config  # noqa: E402
from crawler.browser import browser  # noqa: E402


TEST_NOTES = [
    # (note_id, xsec_token) — picked from earlier successful scrapes
    ("69fb6a5c0000000035020afe", "AB4j1RZEUAwRuReLy2wzhSOqfPDHKxEwG_Bpq1VHKpKMM="),
    ("69c24ecc000000001f002e6c", "ABwWw34eNU7fcjO4fBr_aM_vWOiZzn_dF62ijoOOFu9fs="),
    ("69e78d620000000021012b9b", "AB44qZaOElWOjir_9rfHouetIDs-gN4MHTZW0P2Os71mk="),
]


def main():
    with browser(headless=False) as b:
        # Hard clear ALL cookies for xhs
        try:
            b.page.set.cookies.clear()
            print("[anon] cleared all cookies")
        except Exception as e:
            print(f"[anon] clear failed: {e}")

        # First visit any xhs page to be on the right origin, then re-clear
        b.page.get(f"{config.XHS_HOST}/explore")
        time.sleep(2)
        try:
            b.page.set.cookies.clear()
        except Exception:
            pass

        cookies = {c.get("name") for c in b.page.cookies()}
        print(f"[anon] initial cookies: {sorted(cookies)}")

        for note_id, token in TEST_NOTES:
            url = (f"{config.XHS_HOST}/explore/{note_id}"
                   f"?xsec_token={token}&xsec_source=pc_search")
            print(f"\n[anon] testing {note_id}")
            b.page.get(url)
            time.sleep(4)
            final = b.page.url or ""
            print(f"       final URL: {final[:120]}")
            if "/login" in final:
                print("       → REDIRECTED TO /login (anonymous also blocked)")
                continue
            # See if __INITIAL_STATE__ has note data
            state = b.read_initial_state()
            if not state:
                print("       → no __INITIAL_STATE__ retrievable")
                continue
            ndm = (state.get("note") or {}).get("noteDetailMap") or {}
            if note_id not in ndm:
                print(f"       → state present but noteDetailMap empty "
                      f"(keys: {list(ndm.keys())[:3]})")
                continue
            note = ndm[note_id].get("note") or {}
            interact = note.get("interactInfo") or note.get("interact_info") or {}
            print(f"       => DATA AVAILABLE")
            print(f"       title:        {note.get('title')!r}")
            print(f"       desc length:  {len(note.get('desc') or '')}")
            print(f"       images:       {len(note.get('imageList') or [])}")
            print(f"       liked_count:  {interact.get('likedCount') or interact.get('liked_count')}")
            print(f"       comments:     {interact.get('commentCount') or interact.get('comment_count')}")

        # After the visits, see what cookies xhs set
        cookies_after = sorted({c.get("name") for c in b.page.cookies()})
        print(f"\n[anon] cookies after visits: {cookies_after}")
        print("\n[anon] leaving browser open 20s for visual inspection...")
        time.sleep(20)


if __name__ == "__main__":
    main()
