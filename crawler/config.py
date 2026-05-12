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
