"""Import xhs cookies from the user's regular Chrome profile into the
DrissionPage browser profile, bypassing the QR-login path which xhs refuses
to fully authorize for our session.

REQUIREMENTS BEFORE RUNNING:
  1. Close ALL Chrome windows (including any background processes), otherwise
     Chrome holds an exclusive lock on its cookies DB and we can't read it.
  2. The personal Chrome must already have an active xhs login (visit
     https://www.xiaohongshu.com/explore in your normal browser to confirm).

WHAT IT DOES:
  - Reads cookies from C:\\Users\\<user>\\AppData\\Local\\Google\\Chrome\\
    User Data\\Default\\Network\\Cookies (the Chrome encrypted SQLite DB).
  - Decrypts values using the user's Windows DPAPI key from Local State.
  - Filters for xiaohongshu.com cookies.
  - Injects them into the DrissionPage profile by spinning up the crawler's
    Chrome briefly, calling page.set.cookies() and saving the profile.

After running this, verify with: python -m crawler.main login (no --force).
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# We need to import pywin32 and pycryptodome for decryption on Windows.
# Try to install them on first run if missing.
def _ensure_deps():
    missing = []
    try:
        import win32crypt  # noqa: F401
    except ImportError:
        missing.append("pypiwin32")
    try:
        from Crypto.Cipher import AES  # noqa: F401
    except ImportError:
        missing.append("pycryptodome")
    if missing:
        print(f"[cookies] installing required deps: {missing}")
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", *missing]
        )


_ensure_deps()
import win32crypt  # noqa: E402
from Crypto.Cipher import AES  # noqa: E402


CHROME_DIR = Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"


def get_master_key() -> bytes:
    """Read the AES-GCM master key from Local State and decrypt with DPAPI."""
    local_state_path = CHROME_DIR / "Local State"
    state = json.loads(local_state_path.read_text(encoding="utf-8"))
    encrypted = base64.b64decode(state["os_crypt"]["encrypted_key"])
    # Strip DPAPI prefix
    encrypted = encrypted[5:]
    return win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1]


def decrypt_value(encrypted_value: bytes, master_key: bytes) -> str:
    """Decrypt one Chrome v10+ cookie value (AES-GCM)."""
    if encrypted_value[:3] in (b"v10", b"v11"):
        try:
            nonce = encrypted_value[3:15]
            ciphertext_tag = encrypted_value[15:]
            ciphertext = ciphertext_tag[:-16]
            tag = ciphertext_tag[-16:]
            cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
            plaintext = cipher.decrypt_and_verify(ciphertext, tag)
            return plaintext.decode("utf-8", errors="replace")
        except Exception as e:
            return f"<decrypt err: {e}>"
    # Pre-v10 used DPAPI on the value directly
    try:
        return win32crypt.CryptUnprotectData(
            encrypted_value, None, None, None, 0
        )[1].decode("utf-8", errors="replace")
    except Exception as e:
        return f"<decrypt err: {e}>"


def extract_xhs_cookies(profile: str = "Default") -> list[dict]:
    """Return a list of dicts: name / value / domain / path / expires / secure /
    httpOnly, ready to feed into DrissionPage's set.cookies()."""
    cookies_db = CHROME_DIR / profile / "Network" / "Cookies"
    if not cookies_db.exists():
        raise FileNotFoundError(f"cookies db not found: {cookies_db}")
    # Copy to a temp file so we don't fight Chrome's lock (even though we asked
    # the user to close Chrome, sometimes background services hold it briefly).
    tmp = Path(tempfile.mkstemp(suffix=".db")[1])
    shutil.copyfile(cookies_db, tmp)
    try:
        master = get_master_key()
        con = sqlite3.connect(str(tmp))
        rows = con.execute(
            "SELECT host_key, name, encrypted_value, path, expires_utc, "
            "is_secure, is_httponly "
            "FROM cookies WHERE host_key LIKE '%xiaohongshu%' "
            "   OR host_key LIKE '%xhs%'"
        ).fetchall()
        con.close()
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass
    out = []
    for host, name, enc, path, expires, secure, httponly in rows:
        value = decrypt_value(enc, master)
        if value.startswith("<decrypt err"):
            print(f"[cookies] skipped {name}: {value}")
            continue
        # Chrome stores expires as microseconds since 1601; convert to seconds since 1970
        if expires:
            exp_unix = int(expires / 1_000_000 - 11644473600)
        else:
            exp_unix = -1
        out.append({
            "name": name,
            "value": value,
            "domain": host,
            "path": path,
            "expires": exp_unix,
            "secure": bool(secure),
            "httpOnly": bool(httponly),
        })
    return out


def push_to_drission(cookies: list[dict]) -> bool:
    """Open our DrissionPage browser and inject the cookies, then verify."""
    from crawler import config
    from crawler.browser import browser

    print(f"[cookies] injecting {len(cookies)} cookies into the crawler profile")
    with browser(headless=False) as b:
        # Must visit the target domain at least once before setting cookies
        # (Chrome rejects cookie sets without an active document on that host).
        b.page.get(config.XHS_HOST + "/")
        time.sleep(2)
        for c in cookies:
            try:
                b.page.set.cookies({
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c["path"] or "/",
                    "secure": c["secure"],
                    "httpOnly": c["httpOnly"],
                })
            except Exception as e:
                print(f"[cookies] failed to inject {c['name']}: {e}")
        # Reload and probe a note page
        probe_url = (f"{config.XHS_HOST}/explore/69fb6a5c0000000035020afe"
                     "?xsec_token=AB4j1RZEUAwRuReLy2wzhSOqfPDHKxEwG_Bpq1VHKpKMM=&"
                     "xsec_source=pc_search")
        b.page.get(probe_url)
        time.sleep(3)
        final = b.page.url or ""
        if "/login" in final:
            print(f"[cookies] PROBE FAILED — still redirected to /login")
            print(f"          final URL: {final}")
            return False
        print("[cookies] PROBE OK — note page loads cleanly!")
        names = {c["name"] for c in b.page.cookies()}
        important = {"web_session", "id_token"} & names
        print(f"[cookies] now have {len(names)} cookies; important present: {sorted(important)}")
        return True


def main():
    print(f"[cookies] reading from {CHROME_DIR}")
    cookies = extract_xhs_cookies()
    print(f"[cookies] found {len(cookies)} xhs cookies in normal Chrome profile")
    names = sorted({c["name"] for c in cookies})
    print(f"[cookies] cookie names: {names}")
    if "web_session" not in names:
        print("[cookies] WARN: no 'web_session' — your normal Chrome may not be "
              "logged into xhs. Visit https://www.xiaohongshu.com/explore "
              "in your normal Chrome and confirm you're logged in, then close "
              "Chrome and re-run this.")
        return 1
    ok = push_to_drission(cookies)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
