from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
IMAGES_DIR = DATA_DIR / "images"
EXPORTS_DIR = DATA_DIR / "exports"
STATE_DIR = ROOT / "state"
BROWSER_PROFILE_DIR = STATE_DIR / "profile"
DB_PATH = DATA_DIR / "xhs.db"
LOG_PATH = ROOT / "crawler.log"

for d in (DATA_DIR, IMAGES_DIR, EXPORTS_DIR, STATE_DIR, BROWSER_PROFILE_DIR):
    d.mkdir(parents=True, exist_ok=True)


def get_profile_dir(worker_id: int = 0) -> Path:
    """Per-worker Chrome profile dir. Worker 0 = default profile."""
    if worker_id == 0:
        return BROWSER_PROFILE_DIR
    d = STATE_DIR / f"profile-w{worker_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


PROXY_FILE = STATE_DIR / "proxies.txt"                  # crawler reads (typically 127.0.0.1 local-chain ports)
UPSTREAM_PROXY_FILE = STATE_DIR / "proxies_upstream.txt"  # proxy_chain.py reads (real Decodo URLs with auth)


def _read_proxy_file(path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def load_proxies() -> list[str]:
    """Proxies the crawler uses for browser connections.

    Returns local 127.0.0.1 endpoints when proxy_chain.py is running.
    Returns auth-bearing upstream URLs only if the upstream file is set
    *and* the local-chain file isn't (mostly for direct-auth experiments).
    """
    return _read_proxy_file(PROXY_FILE)


def load_upstream_proxies() -> list[str]:
    """Upstream proxies (with auth) that proxy_chain.py forwards to."""
    return _read_proxy_file(UPSTREAM_PROXY_FILE)


def get_proxy_for_worker(worker_id: int) -> str | None:
    """Returns the proxy URL this worker should use, or None for direct."""
    proxies = load_proxies()
    if not proxies:
        return None
    return proxies[worker_id % len(proxies)]

XHS_HOST = "https://www.xiaohongshu.com"

# Pacing — tuned conservatively. Raise at your own risk.
DELAY_MIN_SEC = 5.0
DELAY_MAX_SEC = 12.0
BATCH_SIZE = 20
BATCH_PAUSE_MIN_SEC = 30.0
BATCH_PAUSE_MAX_SEC = 60.0
RISK_PAUSE_SEC = 300.0

# Network listener — endpoints we care about. Matched as substrings of the URL path.
API_PATTERNS = {
    "feed": "/api/sns/web/v1/feed",
    "comment_page": "/api/sns/web/v2/comment/page",
    "comment_sub": "/api/sns/web/v2/comment/sub/page",
    "search_notes": "/api/sns/web/v1/search/notes",
    "user_posted": "/api/sns/web/v1/user_posted",
    "user_other": "/api/sns/web/v1/user/otherinfo",
    "homefeed": "/api/sns/web/v1/homefeed",
}
LISTEN_PREFIX = "xiaohongshu.com/api/sns/web"
