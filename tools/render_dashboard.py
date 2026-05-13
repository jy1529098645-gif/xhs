"""Render a self-contained HTML dashboard from xhs.db.

v2 — optimized for 10K+ notes:
  - virtual scrolling (only render visible cards, IntersectionObserver-driven)
  - pre-computed search blob + engagement rank
  - debounced search input (300ms)
  - native lazy image loading on card thumbnails
  - comments loaded on-demand (kept in DATA but not rendered until modal opens)

No server required. Open data/exports/dashboard.html in a browser.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from crawler import config


def _https(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def _build_payload() -> dict:
    db = sqlite3.connect(str(config.DB_PATH))
    db.row_factory = sqlite3.Row

    notes_rows = list(db.execute(
        """SELECT note_id, xsec_token, url, type, title, body,
                  author_id, author_nickname, publish_time_ms,
                  ip_location, liked_count, collected_count,
                  comment_count, share_count, image_count,
                  video_url, video_duration_ms,
                  tags_json, at_users_json, crawled_at
           FROM notes"""
    ))

    images_by_note: dict[str, list[dict]] = {}
    for r in db.execute(
        "SELECT note_id, idx, url, width, height "
        "FROM images ORDER BY note_id, idx"
    ):
        images_by_note.setdefault(r["note_id"], []).append({
            "url": _https(r["url"]),
            "w": r["width"],
            "h": r["height"],
        })

    comments_by_note: dict[str, list[dict]] = {}
    for r in db.execute(
        "SELECT comment_id, note_id, parent_id, user_id, nickname, content, "
        "like_count, sub_comment_count, publish_time_ms, ip_location "
        "FROM comments ORDER BY note_id, "
        "CASE WHEN parent_id IS NULL THEN 0 ELSE 1 END, like_count DESC"
    ):
        comments_by_note.setdefault(r["note_id"], []).append({
            "comment_id": r["comment_id"],
            "parent_id": r["parent_id"],
            "nickname": r["nickname"],
            "content": r["content"],
            "like_count": r["like_count"],
            "sub_comment_count": r["sub_comment_count"],
            "publish_time_ms": r["publish_time_ms"],
            "ip_location": r["ip_location"],
        })

    cards: list[dict] = []     # slim per-note for the grid (no body/images/comments)
    full: dict[str, dict] = {} # heavy per-note lookup, fetched only when modal opens

    total_comments = 0
    total_images = 0
    for r in notes_rows:
        n = dict(r)
        try:
            tags = json.loads(n.pop("tags_json") or "[]")
        except Exception:
            tags = []
        n.pop("at_users_json", None)
        imgs = images_by_note.get(n["note_id"], [])
        coms = comments_by_note.get(n["note_id"], [])
        total_images += len(imgs)
        total_comments += len(coms)

        body = n.get("body") or ""
        body_preview = body[:200]

        # Pre-computed engagement rank
        engagement = ((n.get("liked_count") or 0)
                      + (n.get("collected_count") or 0)
                      + (n.get("comment_count") or 0) * 2
                      + (n.get("share_count") or 0) * 3)

        # SLIM card payload — search at runtime over title + bp + an + tg.
        # Skipping pre-computed _s saves ~5MB on payload.
        cards.append({
            "id": n["note_id"],
            "title": n.get("title") or "",
            "bp": body_preview,
            "an": n.get("author_nickname") or "",
            "ip": n.get("ip_location"),
            "ty": n.get("type"),
            "th": imgs[0]["url"] if imgs else None,
            "lc": n.get("liked_count") or 0,
            "cc": n.get("collected_count") or 0,
            "mc": n.get("comment_count") or 0,
            "sc": n.get("share_count") or 0,
            "pt": n.get("publish_time_ms"),
            "ct": n.get("crawled_at"),
            "tg": tags[:5],
            "_e": engagement,
        })

        # HEAVY lookup (only accessed by modal click — never iterated)
        full[n["note_id"]] = {
            "body": body,
            "url": n.get("url"),
            "tags_full": tags,
            "images": imgs,
            "comments": coms,
        }

    return {
        "generated_at": int(time.time() * 1000),
        "stats": {
            "notes": len(cards),
            "comments": total_comments,
            "images": total_images,
        },
        "cards": cards,
        "full": full,
    }


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>xhs crawl dashboard</title>
<style>
  :root {
    --bg: #fafafa; --panel: #ffffff; --border: #e5e5e7;
    --fg: #1d1d1f; --muted: #6e6e73; --accent: #ff2741;
    --tag-bg: #f2f2f7; --shadow: 0 1px 3px rgba(0,0,0,.04);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px; background: var(--bg); color: var(--fg);
    font-family: -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
    font-size: 14px; line-height: 1.5;
  }
  header { max-width: 1400px; margin: 0 auto 20px; }
  h1 { margin: 0 0 4px; font-size: 22px; font-weight: 600; }
  .stats { color: var(--muted); font-size: 13px; }
  .controls {
    max-width: 1400px; margin: 0 auto 20px; display: flex; gap: 12px;
    flex-wrap: wrap; align-items: center;
    position: sticky; top: 0; background: var(--bg); padding: 12px 0; z-index: 10;
  }
  .controls input, .controls select {
    padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px;
    background: var(--panel); font-size: 13px; color: var(--fg); outline: none;
  }
  .controls input { flex: 1; min-width: 200px; max-width: 320px; }
  .controls input:focus, .controls select:focus { border-color: var(--accent); }
  .count-chip { color: var(--muted); font-size: 12px; }
  .loading-chip { color: var(--accent); font-size: 12px; display: none; }
  .loading-chip.show { display: inline; }
  main {
    max-width: 1400px; margin: 0 auto;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 16px;
  }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    overflow: hidden; cursor: pointer; transition: transform .15s, box-shadow .15s;
    display: flex; flex-direction: column;
  }
  .card:hover { transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,.08); }
  .thumb-wrap { position: relative; width: 100%; aspect-ratio: 3/4; overflow: hidden; background: #f0f0f3; }
  .thumb-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .thumb-wrap .placeholder {
    width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
    color: var(--muted); font-size: 12px;
  }
  .video-badge {
    position: absolute; top: 8px; right: 8px; padding: 2px 6px;
    background: rgba(0,0,0,.6); color: #fff; font-size: 10px; border-radius: 4px;
  }
  .body-wrap { padding: 12px; flex: 1; display: flex; flex-direction: column; gap: 8px; }
  .title { font-size: 14px; font-weight: 600; line-height: 1.35;
           display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
           overflow: hidden; }
  .body {
    color: var(--muted); font-size: 12px;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .meta { display: flex; gap: 10px; flex-wrap: wrap; font-size: 11px; color: var(--muted); }
  .metric b { color: var(--fg); font-weight: 600; }
  .tags { display: flex; flex-wrap: wrap; gap: 4px; }
  .tag { font-size: 10px; padding: 2px 6px; border-radius: 4px;
         background: var(--tag-bg); color: var(--muted); }
  .author-row { font-size: 11px; color: var(--muted); }

  #sentinel { grid-column: 1/-1; height: 80px; display: flex;
              align-items: center; justify-content: center; color: var(--muted); font-size: 12px; }

  /* Modal */
  .modal-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.6); z-index: 100;
    display: none; align-items: flex-start; justify-content: center; padding: 40px 20px;
    overflow-y: auto;
  }
  .modal-overlay.open { display: flex; }
  .modal {
    background: var(--panel); border-radius: 16px; max-width: 900px; width: 100%;
    padding: 24px; box-shadow: 0 20px 60px rgba(0,0,0,.3); position: relative;
  }
  .modal-close {
    position: absolute; top: 16px; right: 16px; width: 32px; height: 32px;
    border: none; background: var(--tag-bg); border-radius: 50%; cursor: pointer;
    font-size: 16px; color: var(--muted);
  }
  .modal h2 { margin: 0 0 8px; font-size: 18px; }
  .modal .meta-row { color: var(--muted); font-size: 12px; margin-bottom: 12px;
                     display: flex; gap: 16px; flex-wrap: wrap; }
  .modal .metric-row {
    display: flex; gap: 16px; padding: 12px 16px; background: var(--tag-bg);
    border-radius: 10px; margin: 12px 0; font-size: 13px;
  }
  .modal .full-body { white-space: pre-wrap; line-height: 1.6; margin: 16px 0; }
  .modal .img-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 8px; margin: 16px 0;
  }
  .modal .img-grid img {
    width: 100%; aspect-ratio: 3/4; object-fit: cover; border-radius: 8px;
    cursor: pointer; transition: transform .15s;
  }
  .modal .img-grid img:hover { transform: scale(1.02); }
  .modal .comments-section h3 {
    margin: 24px 0 8px; font-size: 14px; padding-bottom: 8px; border-bottom: 1px solid var(--border);
  }
  .comment {
    padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px;
  }
  .comment.reply { margin-left: 28px; border-left: 2px solid var(--tag-bg);
                   padding-left: 12px; border-bottom: none; }
  .comment .c-head {
    color: var(--muted); font-size: 11px; margin-bottom: 4px;
    display: flex; gap: 8px; flex-wrap: wrap;
  }
  .comment .c-head .nick { color: var(--fg); font-weight: 500; }
  .comment .c-body { line-height: 1.5; white-space: pre-wrap; }
  .comment .c-likes { color: var(--accent); font-size: 11px; }
  .modal a.original {
    display: inline-block; padding: 4px 10px; background: var(--accent); color: white;
    border-radius: 6px; text-decoration: none; font-size: 12px;
  }
  .lightbox {
    position: fixed; inset: 0; background: rgba(0,0,0,.92); z-index: 200;
    display: none; align-items: center; justify-content: center; cursor: zoom-out;
  }
  .lightbox.open { display: flex; }
  .lightbox img { max-width: 95%; max-height: 95%; object-fit: contain; }
</style>
</head>
<body>
<header>
  <h1>小红书爬取数据看板</h1>
  <div class="stats" id="stats"></div>
</header>
<div class="controls">
  <input id="search" type="text" placeholder="搜索 标题/正文/作者/tag…" />
  <select id="sort">
    <option value="_e">综合互动量降序</option>
    <option value="liked_count">点赞降序</option>
    <option value="collected_count">收藏降序</option>
    <option value="comment_count">评论数降序</option>
    <option value="share_count">分享降序</option>
    <option value="publish_time_ms">发布时间倒序</option>
    <option value="crawled_at">抓取时间倒序</option>
  </select>
  <span class="count-chip" id="count-chip"></span>
  <span class="loading-chip" id="loading-chip">加载中…</span>
</div>
<main id="grid"></main>
<div id="sentinel"></div>

<div class="modal-overlay" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="modal" id="modal-body"></div>
</div>
<div class="lightbox" id="lightbox" onclick="this.classList.remove('open')"><img id="lightbox-img"></div>

<script>
const DATA = __DATA__;

const PAGE_SIZE = 60;
let currentList = [];     // currently filtered + sorted full list (refs to notes)
let renderedCount = 0;    // how many cards already in DOM
let pendingFilterTimer = null;

const fmt = (n) => {
  if (n == null) return '-';
  if (n >= 10000) return (n / 10000).toFixed(1) + '万';
  return String(n);
};
const escapeHtml = (s) => {
  if (!s) return '';
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
};
const tsFmt = (ms) => {
  if (!ms) return '';
  return new Date(ms).toLocaleString('zh-CN', {hour12: false});
};

const grid = document.getElementById('grid');
const search = document.getElementById('search');
const sortSel = document.getElementById('sort');
const countChip = document.getElementById('count-chip');
const loadingChip = document.getElementById('loading-chip');
const sentinel = document.getElementById('sentinel');

document.getElementById('stats').textContent =
  `共 ${DATA.stats.notes} 篇笔记 · ${DATA.stats.comments} 条评论 · ${DATA.stats.images} 张图片 · `
  + `生成于 ${new Date(DATA.generated_at).toLocaleString('zh-CN', {hour12:false})}`;

function cardHTML(n) {
  const thumb = n.th
    ? `<img src="${n.th}" loading="lazy" alt="" referrerpolicy="no-referrer">`
    : `<div class="placeholder">无图</div>`;
  const tags = (n.tg && n.tg.length)
    ? `<div class="tags">${n.tg.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('')}</div>`
    : '';
  return `
    <div class="card" data-id="${n.id}">
      <div class="thumb-wrap">
        ${thumb}
        ${n.ty === 'video' ? '<span class="video-badge">视频</span>' : ''}
      </div>
      <div class="body-wrap">
        ${n.title ? `<div class="title">${escapeHtml(n.title)}</div>` : ''}
        <div class="body">${escapeHtml(n.bp)}</div>
        <div class="meta">
          <span class="metric">❤ <b>${fmt(n.lc)}</b></span>
          <span class="metric">⭐ <b>${fmt(n.cc)}</b></span>
          <span class="metric">💬 <b>${fmt(n.mc)}</b></span>
          <span class="metric">📤 <b>${fmt(n.sc)}</b></span>
        </div>
        <div class="author-row">@${escapeHtml(n.an || '?')}${
          n.ip ? ' · 📍' + escapeHtml(n.ip) : ''
        }</div>
        ${tags}
      </div>
    </div>
  `;
}

function renderPage() {
  // Append next PAGE_SIZE items
  const end = Math.min(renderedCount + PAGE_SIZE, currentList.length);
  const chunk = currentList.slice(renderedCount, end);
  if (chunk.length === 0) return;
  const frag = document.createElement('div');
  frag.innerHTML = chunk.map(cardHTML).join('');
  // Move all children of frag into grid (faster than re-setting innerHTML)
  while (frag.firstChild) grid.appendChild(frag.firstChild);
  renderedCount = end;
  sentinel.textContent = (renderedCount < currentList.length)
    ? `下滑加载更多… (已显示 ${renderedCount} / ${currentList.length})`
    : `— 已显示全部 ${currentList.length} —`;
}

// Map UI sort key → card field key
const SORT_KEY_MAP = {
  'liked_count': 'lc', 'collected_count': 'cc',
  'comment_count': 'mc', 'share_count': 'sc',
  'publish_time_ms': 'pt', 'crawled_at': 'ct',
  '_e': '_e',
};

function applyFilter() {
  loadingChip.classList.add('show');
  setTimeout(() => {
    const q = search.value.trim().toLowerCase();
    const sortField = SORT_KEY_MAP[sortSel.value] || '_e';
    let list = DATA.cards;
    if (q) {
      list = list.filter(n => {
        // Search across title + body preview + author + tags
        if (n.title && n.title.toLowerCase().indexOf(q) !== -1) return true;
        if (n.bp && n.bp.toLowerCase().indexOf(q) !== -1) return true;
        if (n.an && n.an.toLowerCase().indexOf(q) !== -1) return true;
        if (n.tg) for (const t of n.tg) if (t.toLowerCase().indexOf(q) !== -1) return true;
        return false;
      });
    }
    list = list.slice().sort((a,b) => (b[sortField] || 0) - (a[sortField] || 0));
    currentList = list;
    renderedCount = 0;
    grid.innerHTML = '';
    countChip.textContent = `匹配 ${list.length} 篇`;
    renderPage();
    loadingChip.classList.remove('show');
  }, 0);
}

// Debounce search input — 300ms
search.addEventListener('input', () => {
  clearTimeout(pendingFilterTimer);
  pendingFilterTimer = setTimeout(applyFilter, 300);
});
sortSel.addEventListener('change', applyFilter);

// Infinite scroll via IntersectionObserver
const io = new IntersectionObserver(entries => {
  if (entries[0].isIntersecting) renderPage();
}, {rootMargin: '400px'});
io.observe(sentinel);

// Modal — delegate card click
grid.addEventListener('click', e => {
  const card = e.target.closest('.card');
  if (!card) return;
  openModal(card.dataset.id);
});

function openModal(noteId) {
  const c = DATA.cards.find(x => x.id === noteId);
  const f = DATA.full[noteId];
  if (!c) return;
  const fullData = f || {body: c.bp, images: [], comments: [], tags_full: c.tg || []};
  const xhsUrl = fullData.url || `https://www.xiaohongshu.com/explore/${noteId}`;
  const tagsToShow = fullData.tags_full && fullData.tags_full.length ? fullData.tags_full : (c.tg || []);
  document.getElementById('modal-body').innerHTML = `
    <button class="modal-close" onclick="closeModal()">×</button>
    <h2>${escapeHtml(c.title || '(无标题)')}</h2>
    <div class="meta-row">
      <span>@${escapeHtml(c.an || '?')}</span>
      ${c.ip ? `<span>📍${escapeHtml(c.ip)}</span>` : ''}
      ${c.pt ? `<span>发布于 ${tsFmt(c.pt)}</span>` : ''}
      <a class="original" href="${xhsUrl}" target="_blank">↗ 原帖</a>
    </div>
    <div class="metric-row">
      <span>❤ 点赞 <b>${fmt(c.lc)}</b></span>
      <span>⭐ 收藏 <b>${fmt(c.cc)}</b></span>
      <span>💬 评论 <b>${fmt(c.mc)}</b></span>
      <span>📤 分享 <b>${fmt(c.sc)}</b></span>
    </div>
    ${tagsToShow.length ? `<div class="tags">${
      tagsToShow.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('')
    }</div>` : ''}
    <div class="full-body">${escapeHtml(fullData.body || '')}</div>
    ${fullData.images && fullData.images.length ? `<div class="img-grid">${
      fullData.images.map(img =>
        `<img src="${img.url}" loading="lazy" referrerpolicy="no-referrer" onclick="showLightbox('${img.url}')">`
      ).join('')
    }</div>` : ''}
    <div class="comments-section">
      <h3>评论 ${(fullData.comments || []).length} 条（已抓取）</h3>
      ${(fullData.comments || []).map(renderComment).join('') || '<div style="color:var(--muted)">无评论</div>'}
    </div>
  `;
  document.getElementById('modal').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function renderComment(c) {
  return `
    <div class="comment ${c.parent_id ? 'reply' : ''}">
      <div class="c-head">
        <span class="nick">@${escapeHtml(c.nickname || '?')}</span>
        ${c.ip_location ? `<span>📍${escapeHtml(c.ip_location)}</span>` : ''}
        ${c.publish_time_ms ? `<span>${tsFmt(c.publish_time_ms)}</span>` : ''}
        <span class="c-likes">❤${fmt(c.like_count)}</span>
      </div>
      <div class="c-body">${escapeHtml(c.content || '')}</div>
    </div>
  `;
}

function closeModal() {
  document.getElementById('modal').classList.remove('open');
  document.body.style.overflow = '';
}

function showLightbox(src) {
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox').classList.add('open');
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeModal();
    document.getElementById('lightbox').classList.remove('open');
  }
});

// Initial render
applyFilter();
</script>
</body>
</html>
"""


def render() -> Path:
    payload = _build_payload()
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    root_out = config.EXPORTS_DIR.parent.parent / "index.html"
    exports_out = config.EXPORTS_DIR / "dashboard.html"
    root_out.write_text(html, encoding="utf-8")
    exports_out.write_text(html, encoding="utf-8")
    return root_out


if __name__ == "__main__":
    p = render()
    print(f"Wrote {p}")
    print(f"Open in browser: file:///{p.as_posix()}")
