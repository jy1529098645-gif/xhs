"""Parsers that turn xhs API JSON into our normalized records.

xhs's response shape has shifted a few times; we walk defensively and tolerate
missing keys. The full raw blob is always stored in raw_json so we can re-parse.
"""
from typing import Any, Optional


def _g(d: Any, *path, default=None):
    cur = d
    for k in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(k, default if k == path[-1] else None)
        elif isinstance(cur, list) and isinstance(k, int) and 0 <= k < len(cur):
            cur = cur[k]
        else:
            return default
    return cur if cur is not None else default


def _gk(d: dict, *keys, default=None):
    """Get the first present key from a dict. Handles both snake_case (API)
    and camelCase (__INITIAL_STATE__) — xhs is inconsistent between them.
    """
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


_NOTE_ID_HEX = __import__("re").compile(r"^[0-9a-f]{24}$")


def parse_note_card(item: dict, source_type: str = "", source_value: str = "") -> Optional[dict]:
    """Parse a 'note card' from search/profile/topic feed → discovery row."""
    note_id = item.get("id") or item.get("note_id") or _g(item, "note_card", "note_id")
    if not note_id:
        return None
    # Real xhs note_ids are 24-char lowercase hex. Ad/placeholder slots in the
    # feed sometimes use UUIDs — those aren't notes and waste worker time.
    if not _NOTE_ID_HEX.match(str(note_id)):
        return None
    xsec_token = (item.get("xsec_token") or _g(item, "note_card", "xsec_token")
                  or item.get("xsecToken"))
    xsec_source = item.get("xsec_source") or "pc_search"
    return {
        "note_id": note_id,
        "xsec_token": xsec_token,
        "xsec_source": xsec_source,
        "source_type": source_type,
        "source_value": source_value,
    }


def parse_initial_state(state: dict, note_id: str) -> Optional[tuple[dict, list[dict], list[dict]]]:
    """Parse window.__INITIAL_STATE__ → (note_record, images, comments).

    xhs's SSR state shape (as of late 2025 / early 2026 web build):

        state.note.noteDetailMap[note_id] = {
            note: { ... same as feed item.note_card ... },
            comments: { list: [...], hasMore, cursor }
        }

    We walk defensively so layout shuffles don't break the whole pipeline.
    """
    note_section = state.get("note") or {}
    detail_map = note_section.get("noteDetailMap") or {}
    entry = detail_map.get(note_id)
    if not entry:
        # Sometimes the map is keyed by a slightly different id form; try first
        if detail_map:
            entry = next(iter(detail_map.values()))
    if not entry:
        return None

    nc = entry.get("note") or entry.get("noteCard") or entry
    if not nc:
        return None

    # Reuse parse_feed_note's logic by stuffing a fake feed shape
    fake_feed = {"data": {"items": [{"id": note_id, "note_card": nc,
                                       "xsec_token": nc.get("xsec_token")}]}}
    parsed = parse_feed_note(fake_feed, note_id)
    if not parsed:
        return None
    note, images = parsed

    # Comments: try a few keys xhs uses across versions
    comments_block = (entry.get("comments")
                      or entry.get("commentInfo")
                      or entry.get("comment")
                      or {})
    comment_items = (comments_block.get("list")
                     or comments_block.get("comments")
                     or [])
    parsed_comments: list[dict] = []
    for c in comment_items:
        parsed_comments.append(_one_comment(c, note_id, None))
        for sub in (c.get("sub_comments") or c.get("subComments") or []):
            parsed_comments.append(_one_comment(sub, note_id, c.get("id")))

    return note, images, parsed_comments


def parse_feed_note(feed_json: dict, note_id: str) -> Optional[tuple[dict, list[dict]]]:
    """Parse /feed response into (note_record, image_list).

    Returns None if the note is not in the response.
    """
    items = _g(feed_json, "data", "items") or []
    target = None
    for it in items:
        if it.get("id") == note_id or _g(it, "note_card", "note_id") == note_id:
            target = it
            break
    if target is None and items:
        target = items[0]
    if target is None:
        return None

    nc = target.get("note_card") or target
    interact = _gk(nc, "interact_info", "interactInfo", default={}) or {}
    user = nc.get("user") or {}

    note = {
        "note_id": _gk(nc, "note_id", "noteId") or target.get("id") or note_id,
        "xsec_token": (target.get("xsec_token") or target.get("xsecToken")
                       or _gk(nc, "xsec_token", "xsecToken")),
        "type": nc.get("type"),
        "title": nc.get("title") or "",
        "body": nc.get("desc") or "",
        "author_id": _gk(user, "user_id", "userId"),
        "author_nickname": _gk(user, "nickname", "nick_name", "nickName"),
        "publish_time_ms": _gk(nc, "time", "publish_time", "publishTime"),
        "last_update_ms": _gk(nc, "last_update_time", "lastUpdateTime"),
        "ip_location": _gk(nc, "ip_location", "ipLocation"),
        "liked_count": _to_int(_gk(interact, "liked_count", "likedCount")),
        "collected_count": _to_int(_gk(interact, "collected_count", "collectedCount")),
        "comment_count": _to_int(_gk(interact, "comment_count", "commentCount")),
        "share_count": _to_int(_gk(interact, "share_count", "shareCount")),
    }

    image_list = _gk(nc, "image_list", "imageList", default=[]) or []
    images = []
    for img in image_list:
        url = _pick_image_url(img)
        if url:
            images.append({
                "url": url,
                "width": img.get("width"),
                "height": img.get("height"),
            })
    note["image_count"] = len(images)

    # tags + @ users  (camelCase variants for __INITIAL_STATE__)
    tag_list = _gk(nc, "tag_list", "tagList", default=[]) or []
    note["tags"] = [t.get("name") for t in tag_list if t.get("name")]
    at_list = _gk(nc, "at_user_list", "atUserList", default=[]) or []
    note["at_users"] = [
        {"user_id": _gk(u, "user_id", "userId"),
         "nickname": _gk(u, "nickname", "nickName")} for u in at_list
    ]

    # video
    video = nc.get("video") or {}
    if video:
        stream = (_g(video, "media", "stream") or {})
        candidates = []
        for codec_key in ("h264", "h265", "av1"):
            candidates.extend(stream.get(codec_key) or [])
        if candidates:
            best = candidates[0]
            note["video_url"] = best.get("master_url") or best.get("backup_urls", [None])[0]
            note["video_duration_ms"] = _g(video, "capa", "duration")
        note["type"] = note.get("type") or "video"
    else:
        note["type"] = note.get("type") or "normal"

    return note, images


def _pick_image_url(img: dict) -> Optional[str]:
    info_list = _gk(img, "info_list", "infoList", default=[]) or []
    for scene in ("WB_DFT", "WB_PRV"):
        for info in info_list:
            if _gk(info, "image_scene", "imageScene") == scene and info.get("url"):
                return info["url"]
    for k in ("url_default", "urlDefault", "url_pre", "urlPre", "url"):
        if img.get(k):
            return img[k]
    if info_list:
        return info_list[0].get("url")
    return None


def parse_comments(json_body: dict, note_id: str, parent_id: Optional[str] = None) -> list[dict]:
    """Parse top-level or sub-comment page response."""
    out = []
    items = _g(json_body, "data", "comments") or _g(json_body, "data", "sub_comments") or []
    for c in items:
        out.append(_one_comment(c, note_id, parent_id))
        # eagerly capture inline sub_comments if xhs includes any
        for sub in c.get("sub_comments") or []:
            out.append(_one_comment(sub, note_id, c.get("id")))
    return out


def _one_comment(c: dict, note_id: str, parent_id: Optional[str]) -> dict:
    user = _gk(c, "user_info", "userInfo", default={}) or {}
    pics = []
    for p in c.get("pictures") or []:
        url = _pick_image_url(p) or _gk(p, "url_default", "urlDefault", "url")
        if url:
            pics.append({"url": url, "width": p.get("width"), "height": p.get("height")})
    target_comment = _gk(c, "target_comment", "targetComment", default={}) or {}
    return {
        "comment_id": c.get("id"),
        "note_id": note_id,
        "parent_id": parent_id or target_comment.get("id"),
        "user_id": _gk(user, "user_id", "userId"),
        "nickname": _gk(user, "nickname", "nickName"),
        "content": c.get("content"),
        "like_count": _to_int(_gk(c, "like_count", "likeCount")),
        "sub_comment_count": _to_int(_gk(c, "sub_comment_count", "subCommentCount")),
        "publish_time_ms": _gk(c, "create_time", "createTime"),
        "ip_location": _gk(c, "ip_location", "ipLocation"),
        "pictures": pics,
        "raw": c,
    }


def has_more_comments(json_body: dict) -> tuple[bool, Optional[str]]:
    data = json_body.get("data") or {}
    has_more = bool(_gk(data, "has_more", "hasMore"))
    cursor = data.get("cursor")
    return has_more, cursor


def parse_user_info(json_body: dict) -> Optional[dict]:
    data = json_body.get("data") or {}
    basic = data.get("basic_info") or data
    interactions = data.get("interactions") or []
    fans = follows = notes = 0
    for item in interactions:
        name = (item.get("name") or "").lower()
        c = _to_int(item.get("count"))
        if name in ("fans", "粉丝"):
            fans = c
        elif name in ("follows", "关注"):
            follows = c
        elif name in ("interaction", "获赞与收藏"):
            notes = c  # not strictly notes; xhs lumps likes+collects here
    return {
        "user_id": basic.get("red_id") and basic.get("user_id") or data.get("user_id"),
        "nickname": basic.get("nickname"),
        "red_id": basic.get("red_id"),
        "avatar": basic.get("imageb") or basic.get("images"),
        "description": basic.get("desc"),
        "gender": basic.get("gender"),
        "ip_location": basic.get("ip_location"),
        "fans_count": fans,
        "follows_count": follows,
        "interaction_count": notes,
    }


def _to_int(v) -> Optional[int]:
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        v = v.strip()
        # xhs formats like "1.2万" or "1,234"
        try:
            if v.endswith("万"):
                return int(float(v[:-1]) * 10000)
            if v.endswith("w"):
                return int(float(v[:-1]) * 10000)
            return int(v.replace(",", "").replace("+", ""))
        except (ValueError, TypeError):
            return None
    return None
