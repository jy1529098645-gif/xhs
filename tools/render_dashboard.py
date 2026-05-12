"""Render a self-contained HTML dashboard from xhs.db.

No server required. Open data/exports/dashboard.html in a browser.

Features:
- Searchable / sortable card grid of all crawled notes
- Click a card → modal with full body, all images, all comments (threaded)
- Metrics, tags, author, IP location, publish time
- Local images served via relative paths
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from crawler import config


def _https(url: str | None) -> str | None:
    """Force https on xhs CDN URLs — GitHub Pages serves https and would
    block mixed-content http:// images."""
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
        "SELECT note_id, idx, url, width, height, local_path "
        "FROM images ORDER BY note_id, idx"
    ):
        images_by_note.setdefault(r["note_id"], []).append({
            "idx": r["idx"],
            "url": _https(r["url"]),  # xhs CDN, works from any origin w/ no-referrer
            "width": r["width"],
            "height": r["height"],
        })

    comments_by_note: dict[str, list[dict]] = {}
    for r in db.execute(
        "SELECT comment_id, note_id, parent_id, user_id, nickname, content, "
        "like_count, sub_comment_count, publish_time_ms, ip_location, pictures_json "
        "FROM comments ORDER BY note_id, "
        "CASE WHEN parent_id IS NULL THEN 0 ELSE 1 END, like_count DESC"
    ):
        comments_by_note.setdefault(r["note_id"], []).append({
            "comment_id": r["comment_id"],
            "parent_id": r["parent_id"],
            "user_id": r["user_id"],
            "nickname": r["nickname"],
            "content": r["content"],
            "like_count": r["like_count"],
            "sub_comment_count": r["sub_comment_count"],
            "publish_time_ms": r["publish_time_ms"],
            "ip_location": r["ip_location"],
            "pictures": json.loads(r["pictures_json"] or "[]"),
        })

    notes: list[dict] = []
    for r in notes_rows:
        n = dict(r)
        try:
            n["tags"] = json.loads(n.pop("tags_json") or "[]")
        except Exception:
            n["tags"] = []
        try:
            n["at_users"] = json.loads(n.pop("at_users_json") or "[]")
        except Exception:
            n["at_users"] = []
        n["images"] = images_by_note.get(n["note_id"], [])
        n["comments"] = comments_by_note.get(n["note_id"], [])
        notes.append(n)

    return {
        "generated_at": int(time.time() * 1000),
        "stats": {
            "notes": len(notes),
            "comments": sum(len(n["comments"]) for n in notes),
            "images": sum(len(n["images"]) for n in notes),
        },
        "notes": notes,
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
  }
  .controls input, .controls select {
    padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px;
    background: var(--panel); font-size: 13px; color: var(--fg); outline: none;
  }
  .controls input { flex: 1; min-width: 200px; max-width: 320px; }
  .controls input:focus, .controls select:focus { border-color: var(--accent); }
  .count-chip { color: var(--muted); font-size: 12px; }
  main {
    max-width: 1400px; margin: 0 auto;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 16px;
  }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    overflow: hidden; cursor: pointer; transition: transform .15s, box-shadow .15s;
    display: flex; flex-direction: column;
  }
  .card:hover { transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,.08); }
  .card .thumb {
    width: 100%; aspect-ratio: 3/4; background: #f0f0f3 center/cover no-repeat;
    display: flex; align-items: center; justify-content: center;
    color: var(--muted); font-size: 12px;
  }
  .card .thumb.video::after { content: "▶"; position: absolute; font-size: 32px; color: white; }
  .thumb-wrap { position: relative; }
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
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
  }
  .meta { display: flex; gap: 10px; flex-wrap: wrap; font-size: 11px; color: var(--muted); }
  .metric { display: inline-flex; align-items: center; gap: 3px; }
  .metric b { color: var(--fg); font-weight: 600; }
  .tags { display: flex; flex-wrap: wrap; gap: 4px; }
  .tag {
    font-size: 10px; padding: 2px 6px; border-radius: 4px;
    background: var(--tag-bg); color: var(--muted);
  }
  .author-row { font-size: 11px; color: var(--muted); display: flex; gap: 8px; }
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
  <input id="search" type="text" placeholder="搜索 标题/正文/作者/评论..." />
  <select id="sort">
    <option value="engagement">综合互动量降序</option>
    <option value="liked_count">点赞降序</option>
    <option value="collected_count">收藏降序</option>
    <option value="comment_count">评论数降序</option>
    <option value="share_count">分享降序</option>
    <option value="publish_time_ms">发布时间倒序</option>
    <option value="crawled_at">抓取时间倒序</option>
  </select>
  <span class="count-chip" id="count-chip"></span>
</div>
<main id="grid"></main>

<div class="modal-overlay" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="modal" id="modal-body"></div>
</div>
<div class="lightbox" id="lightbox" onclick="this.classList.remove('open')"><img id="lightbox-img"></div>

<script>
const DATA = __DATA__;

const fmt = (n) => {
  if (n === null || n === undefined) return '-';
  if (n >= 10000) return (n / 10000).toFixed(1) + '万';
  return String(n);
};
const escapeHtml = (s) => {
  if (!s) return '';
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
};
const tsFmt = (ms) => {
  if (!ms) return '';
  const d = new Date(ms);
  return d.toLocaleString('zh-CN', {hour12: false});
};

const grid = document.getElementById('grid');
const search = document.getElementById('search');
const sortSel = document.getElementById('sort');
const countChip = document.getElementById('count-chip');

document.getElementById('stats').textContent =
  `共 ${DATA.stats.notes} 篇笔记 · ${DATA.stats.comments} 条评论 · ${DATA.stats.images} 张图片 · `
  + `生成于 ${new Date(DATA.generated_at).toLocaleString('zh-CN', {hour12:false})}`;

function rank(n, key) {
  if (key === 'engagement') {
    return (n.liked_count||0) + (n.collected_count||0) + (n.comment_count||0)*2 + (n.share_count||0)*3;
  }
  return n[key] || 0;
}

function matches(n, q) {
  if (!q) return true;
  q = q.toLowerCase();
  const hay = [n.title, n.body, n.author_nickname, ...(n.tags||[]),
               ...(n.comments||[]).map(c => c.content + ' ' + (c.nickname||''))]
              .filter(Boolean).join(' ').toLowerCase();
  return hay.includes(q);
}

function thumbStyle(n) {
  if (n.images && n.images.length && n.images[0].url) {
    return `background-image: url("${n.images[0].url}")`;
  }
  return '';
}

function render() {
  const q = search.value.trim();
  const sortKey = sortSel.value;
  const list = DATA.notes
    .filter(n => matches(n, q))
    .sort((a,b) => rank(b, sortKey) - rank(a, sortKey));
  countChip.textContent = `显示 ${list.length} 篇`;
  grid.innerHTML = list.map((n, i) => `
    <div class="card" data-idx="${i}" onclick="openModal('${n.note_id}')">
      <div class="thumb-wrap">
        <div class="thumb" style="${thumbStyle(n)}">
          ${(!n.images || !n.images.length || !n.images[0].url) ? '无图' : ''}
        </div>
        ${n.type === 'video' ? '<span class="video-badge">视频</span>' : ''}
      </div>
      <div class="body-wrap">
        ${n.title ? `<div class="title">${escapeHtml(n.title)}</div>` : ''}
        <div class="body">${escapeHtml(n.body || '')}</div>
        <div class="meta">
          <span class="metric">❤ <b>${fmt(n.liked_count)}</b></span>
          <span class="metric">⭐ <b>${fmt(n.collected_count)}</b></span>
          <span class="metric">💬 <b>${fmt(n.comment_count)}</b></span>
          <span class="metric">📤 <b>${fmt(n.share_count)}</b></span>
        </div>
        <div class="author-row">
          <span>@${escapeHtml(n.author_nickname || '?')}</span>
          ${n.ip_location ? `<span>📍${escapeHtml(n.ip_location)}</span>` : ''}
        </div>
        ${n.tags && n.tags.length ? `<div class="tags">${
          n.tags.slice(0,5).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('')
        }</div>` : ''}
      </div>
    </div>
  `).join('');
}

function openModal(noteId) {
  const n = DATA.notes.find(x => x.note_id === noteId);
  if (!n) return;
  const xhsUrl = n.url || `https://www.xiaohongshu.com/explore/${n.note_id}`;
  document.getElementById('modal-body').innerHTML = `
    <button class="modal-close" onclick="closeModal()">×</button>
    <h2>${escapeHtml(n.title || '(无标题)')}</h2>
    <div class="meta-row">
      <span>@${escapeHtml(n.author_nickname || '?')}</span>
      ${n.ip_location ? `<span>📍${escapeHtml(n.ip_location)}</span>` : ''}
      ${n.publish_time_ms ? `<span>发布于 ${tsFmt(n.publish_time_ms)}</span>` : ''}
      <a class="original" href="${xhsUrl}" target="_blank">↗ 原帖</a>
    </div>
    <div class="metric-row">
      <span>❤ 点赞 <b>${fmt(n.liked_count)}</b></span>
      <span>⭐ 收藏 <b>${fmt(n.collected_count)}</b></span>
      <span>💬 评论 <b>${fmt(n.comment_count)}</b></span>
      <span>📤 分享 <b>${fmt(n.share_count)}</b></span>
    </div>
    ${n.tags && n.tags.length ? `<div class="tags">${
      n.tags.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('')
    }</div>` : ''}
    <div class="full-body">${escapeHtml(n.body || '')}</div>
    ${n.images && n.images.length ? `<div class="img-grid">${
      n.images.map(img =>
        `<img src="${img.url}" loading="lazy" referrerpolicy="no-referrer" onclick="showLightbox('${img.url}')">`
      ).join('')
    }</div>` : ''}
    <div class="comments-section">
      <h3>评论 ${n.comments.length} 条（已抓取）</h3>
      ${n.comments.map(renderComment).join('') || '<div style="color:var(--muted)">无评论</div>'}
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

search.addEventListener('input', render);
sortSel.addEventListener('change', render);
render();
</script>
</body>
</html>
"""


def render() -> Path:
    payload = _build_payload()
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    # Write to repo root as index.html (so GitHub Pages serves it at /),
    # plus a copy at data/exports/ for local convenience.
    root_out = config.EXPORTS_DIR.parent.parent / "index.html"
    exports_out = config.EXPORTS_DIR / "dashboard.html"
    root_out.write_text(html, encoding="utf-8")
    exports_out.write_text(html, encoding="utf-8")
    return root_out


if __name__ == "__main__":
    p = render()
    print(f"Wrote {p}")
    print(f"Open in browser: file:///{p.as_posix()}")
