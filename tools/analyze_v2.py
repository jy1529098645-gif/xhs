"""Generate a comprehensive insight report from the 10K-note DB.

Output: data/exports/INSIGHT_REPORT.md
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "xhs.db"
OUT = ROOT / "data" / "exports" / "INSIGHT_REPORT.md"

con = sqlite3.connect(str(DB_PATH))
con.row_factory = sqlite3.Row


def fetch(sql, *args):
    return con.execute(sql, args).fetchall()


def fetch_one(sql, *args):
    return con.execute(sql, args).fetchone()


# ============================================================
# 1. Headline numbers
# ============================================================
total = fetch_one("SELECT COUNT(*) FROM notes")[0]
total_likes = fetch_one("SELECT SUM(liked_count) FROM notes")[0] or 0
total_collects = fetch_one("SELECT SUM(collected_count) FROM notes")[0] or 0
total_comments = fetch_one("SELECT SUM(comment_count) FROM notes")[0] or 0
total_shares = fetch_one("SELECT SUM(share_count) FROM notes")[0] or 0
avg_likes = total_likes / total if total else 0
median_likes = fetch_one("""SELECT liked_count FROM notes ORDER BY liked_count
    LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM notes)""")[0]
notes_w_body = fetch_one("SELECT COUNT(*) FROM notes WHERE LENGTH(body) > 50")[0]
notes_w_topics = fetch_one("SELECT COUNT(*) FROM notes WHERE tags_json != '[]' AND tags_json IS NOT NULL")[0]
distinct_authors = fetch_one("SELECT COUNT(DISTINCT author_nickname) FROM notes WHERE author_nickname != ''")[0]


# ============================================================
# 2. Engagement distribution
# ============================================================
buckets = [
    ("megahit (≥10K likes)", "liked_count >= 10000"),
    ("viral (5K-10K)",       "liked_count >= 5000 AND liked_count < 10000"),
    ("hit (1K-5K)",          "liked_count >= 1000 AND liked_count < 5000"),
    ("warm (100-1K)",        "liked_count >= 100  AND liked_count < 1000"),
    ("cold (<100)",          "liked_count < 100"),
]
engagement_dist = []
for label, cond in buckets:
    n = fetch_one(f"SELECT COUNT(*) FROM notes WHERE {cond}")[0]
    engagement_dist.append((label, n))


# ============================================================
# 3. Top notes by likes
# ============================================================
top_likes = fetch("""
    SELECT title, author_nickname, liked_count, collected_count, comment_count, tags_json, body
    FROM notes WHERE title IS NOT NULL AND title != ''
    ORDER BY liked_count DESC LIMIT 50
""")


# ============================================================
# 4. Top notes by comments (discussion drivers)
# ============================================================
top_comments = fetch("""
    SELECT title, author_nickname, liked_count, comment_count, tags_json
    FROM notes WHERE title IS NOT NULL AND title != ''
    ORDER BY comment_count DESC LIMIT 30
""")


# ============================================================
# 5. Tag frequency analysis
# ============================================================
tag_counter = Counter()
tag_likes = defaultdict(int)
tag_comments = defaultdict(int)
for row in fetch("SELECT tags_json, liked_count, comment_count FROM notes WHERE tags_json != '[]'"):
    try:
        tags = json.loads(row["tags_json"])
    except Exception:
        continue
    for t in tags:
        if not t.strip():
            continue
        tag_counter[t] += 1
        tag_likes[t] += row["liked_count"] or 0
        tag_comments[t] += row["comment_count"] or 0

top_tags = tag_counter.most_common(50)
top_tags_by_avg_likes = sorted(
    [(t, n, tag_likes[t] / n) for t, n in tag_counter.items() if n >= 20],
    key=lambda x: x[2], reverse=True
)[:30]


# ============================================================
# 6. Title pattern analysis
# ============================================================
title_patterns = {
    "数字开头 (1天/3步/5招)": r"^\d+",
    "数字+量词 (一天/三天/五天)": r"^[一二三四五六七八九十百千万]\s*[天日年时小时章节遍篇步招件个种]",
    "Emoji 开头": r"^[\U0001F300-\U0001FAFF☀-➿]",
    "问句开头 (怎么/如何/为什么)": r"^(怎么|如何|为什么|有没有|是不是)",
    "祈使 (建议/教你/告诉你)": r"(建议|教你|告诉你|手把手)",
    "AI 工具品牌 (ChatGPT/DeepSeek/Claude/Gemini)": r"(ChatGPT|DeepSeek|Claude|Gemini|Kimi|GPT|豆包|文心一言|通义)",
    "情绪/痛点 (终于/绝了/无敌/救命)": r"(终于|绝了|无敌|救命|崩溃|破防|完蛋|废了|哭了)",
    "速度宣告 (一天/两小时/十分钟)": r"(一天|两小时|半小时|十分钟|十几秒|几秒)",
    "数量宣告 (一万字/四篇/十篇)": r"(\d+万字|\d+篇|\d+\s*个)",
}
pattern_stats = {}
for label, regex in title_patterns.items():
    p = re.compile(regex)
    rows = fetch("SELECT title, liked_count FROM notes WHERE title IS NOT NULL AND title != ''")
    matched = [r for r in rows if p.search(r["title"] or "")]
    if matched:
        avg = sum(r["liked_count"] or 0 for r in matched) / len(matched)
        pattern_stats[label] = (len(matched), avg, sum(r["liked_count"] or 0 for r in matched))


# ============================================================
# 7. Title length analysis
# ============================================================
length_buckets = [
    ("<10字", "LENGTH(title) < 10"),
    ("10-20", "LENGTH(title) >= 10 AND LENGTH(title) < 20"),
    ("20-30", "LENGTH(title) >= 20 AND LENGTH(title) < 30"),
    ("30-40", "LENGTH(title) >= 30 AND LENGTH(title) < 40"),
    ("40+",    "LENGTH(title) >= 40"),
]
length_stats = []
for label, cond in length_buckets:
    n = fetch_one(f"SELECT COUNT(*) FROM notes WHERE title IS NOT NULL AND {cond}")[0]
    avg_l = fetch_one(f"SELECT AVG(liked_count) FROM notes WHERE title IS NOT NULL AND {cond}")[0] or 0
    length_stats.append((label, n, avg_l))


# ============================================================
# 8. Time-of-day & day-of-week
# ============================================================
hour_stats = defaultdict(lambda: [0, 0])  # hour → [count, sum_likes]
dow_stats = defaultdict(lambda: [0, 0])
import time as _t
for row in fetch("SELECT publish_time_ms, liked_count FROM notes WHERE publish_time_ms > 0"):
    t = _t.localtime(row["publish_time_ms"] / 1000)
    hour_stats[t.tm_hour][0] += 1
    hour_stats[t.tm_hour][1] += row["liked_count"] or 0
    dow_stats[t.tm_wday][0] += 1
    dow_stats[t.tm_wday][1] += row["liked_count"] or 0


# ============================================================
# 9. Author concentration
# ============================================================
top_authors = fetch("""
    SELECT author_nickname, COUNT(*) AS n,
           SUM(liked_count) AS total_likes,
           SUM(comment_count) AS total_com,
           AVG(liked_count) AS avg_likes
    FROM notes WHERE author_nickname != ''
    GROUP BY author_nickname HAVING n >= 3
    ORDER BY total_likes DESC LIMIT 50
""")


# ============================================================
# 10. Year distribution
# ============================================================
year_dist = defaultdict(int)
for row in fetch("SELECT publish_time_ms FROM notes WHERE publish_time_ms > 0"):
    y = _t.localtime(row["publish_time_ms"] / 1000).tm_year
    year_dist[y] += 1


# ============================================================
# 11. Body length vs engagement
# ============================================================
body_buckets = [
    ("<50字 (短)", 0, 50),
    ("50-200 (短中)", 50, 200),
    ("200-500 (中)", 200, 500),
    ("500-1000 (长)", 500, 1000),
    ("1000-2000 (长文)", 1000, 2000),
    ("2000+ (深度)", 2000, 99999),
]
body_stats = []
for label, lo, hi in body_buckets:
    n = fetch_one(f"SELECT COUNT(*) FROM notes WHERE LENGTH(body) >= {lo} AND LENGTH(body) < {hi}")[0]
    avg = fetch_one(f"SELECT AVG(liked_count) FROM notes WHERE LENGTH(body) >= {lo} AND LENGTH(body) < {hi}")[0] or 0
    body_stats.append((label, n, avg))


# ============================================================
# 12. AI tools mentioned (competitive intel)
# ============================================================
ai_tools = {
    "ChatGPT": r"ChatGPT|GPT-?[34]|GPT",
    "DeepSeek": r"DeepSeek|deepseek|DS\b",
    "Claude": r"Claude|claude",
    "Gemini": r"Gemini|gemini",
    "Kimi": r"Kimi|kimi",
    "豆包": r"豆包",
    "文心一言/百度": r"文心一言|百度AI",
    "通义/千问": r"通义|千问",
    "讯飞/星火": r"讯飞|星火",
    "Notion AI": r"Notion AI|Notion",
    "秘塔": r"秘塔",
    "Perplexity": r"Perplexity",
    "知网": r"知网",
    "Zotero": r"Zotero",
    "EndNote": r"EndNote",
}
ai_mentions = {}
for tool, patt in ai_tools.items():
    p = re.compile(patt, re.IGNORECASE)
    rows = fetch("SELECT title, body, liked_count FROM notes")
    n_titles = sum(1 for r in rows if p.search(r["title"] or ""))
    n_body = sum(1 for r in rows if p.search(r["body"] or ""))
    likes_total = sum(r["liked_count"] or 0 for r in rows if p.search((r["title"] or "") + " " + (r["body"] or "")))
    ai_mentions[tool] = (n_titles, n_body, likes_total)


# ============================================================
# 13. "Pain point" keyword frequency in body
# ============================================================
pain_keywords = ["查重", "降重", "降AI率", "AI检测", "导师", "ddl", "DDL", "焦虑",
                 "崩溃", "救命", "拖延", "瓶颈", "卡住", "改不完", "通宵", "熬夜",
                 "不会写", "没思路", "选题", "开题", "答辩"]
pain_stats = {}
for kw in pain_keywords:
    rows = fetch("SELECT liked_count FROM notes WHERE body LIKE ?", f"%{kw}%")
    if rows:
        pain_stats[kw] = (len(rows), sum(r["liked_count"] or 0 for r in rows) / len(rows))


# ============================================================
# Write output
# ============================================================
lines = []
W = lines.append

W("# 📊 AcademiCats 小红书数据库洞察报告 v2")
W("")
W(f"> 基于 **{total:,}** 条小红书 note 数据 (Excel ETL + 之前爬取)")
W(f"> 生成时间：{_t.strftime('%Y-%m-%d %H:%M', _t.localtime())}")
W(f"> 数据源：`data/xhs.db` (drain 还在跑，部分 raw_json/图片暂缺，不影响本报告)")
W("")
W("---")
W("")
W("## 1. 数据规模总览")
W("")
W(f"- **总 notes**: {total:,}")
W(f"- **总点赞**: {total_likes:,} (平均 {avg_likes:,.0f} / 中位数 {median_likes})")
W(f"- **总收藏**: {total_collects:,}")
W(f"- **总评论**: {total_comments:,}")
W(f"- **总分享**: {total_shares:,}")
W(f"- **含实质 body (>50 字)**: {notes_w_body:,} ({100*notes_w_body/total:.0f}%)")
W(f"- **含 hashtag**: {notes_w_topics:,} ({100*notes_w_topics/total:.0f}%)")
W(f"- **不同作者**: {distinct_authors:,}")
W("")
W("### 互动量级分布")
W("")
W("| 等级 | 数量 | 占比 |")
W("|---|---:|---:|")
for label, n in engagement_dist:
    W(f"| {label} | {n:,} | {100*n/total:.1f}% |")
W("")

W("---")
W("")
W("## 2. Top 50 爆款 note（按点赞）")
W("")
W("| # | 标题 | 作者 | 赞 | 收藏 | 评论 |")
W("|---:|---|---|---:|---:|---:|")
for i, r in enumerate(top_likes, 1):
    title = (r["title"] or "")[:50].replace("|", "\\|").replace("\n", " ")
    author = (r["author_nickname"] or "")[:15]
    W(f"| {i} | {title} | {author} | {r['liked_count']:,} | {r['collected_count']:,} | {r['comment_count']:,} |")
W("")

W("---")
W("")
W("## 3. Top 30 讨论驱动 note（按评论）")
W("")
W("| # | 标题 | 作者 | 评论 | 赞 |")
W("|---:|---|---|---:|---:|")
for i, r in enumerate(top_comments, 1):
    title = (r["title"] or "")[:50].replace("|", "\\|").replace("\n", " ")
    W(f"| {i} | {title} | {(r['author_nickname'] or '')[:15]} | {r['comment_count']:,} | {r['liked_count']:,} |")
W("")

W("---")
W("")
W("## 4. Hashtag 热度 Top 50")
W("")
W("| # | tag | 用过次数 |")
W("|---:|---|---:|")
for i, (tag, n) in enumerate(top_tags, 1):
    W(f"| {i} | #{tag} | {n} |")
W("")

W("### Top 30 高 ROI tag（按平均互动，最少 20 次出现）")
W("")
W("| # | tag | 次数 | 平均赞 |")
W("|---:|---|---:|---:|")
for i, (tag, n, avg) in enumerate(top_tags_by_avg_likes, 1):
    W(f"| {i} | #{tag} | {n} | {avg:,.0f} |")
W("")

W("---")
W("")
W("## 5. 标题 pattern 分析")
W("")
W("| 模式 | 命中数 | 平均赞 | 总赞 |")
W("|---|---:|---:|---:|")
for label, (n, avg, tot) in sorted(pattern_stats.items(), key=lambda x: x[1][1], reverse=True):
    W(f"| {label} | {n:,} | {avg:,.0f} | {tot:,} |")
W("")

W("### 标题长度 vs 互动")
W("")
W("| 长度 | 数量 | 平均赞 |")
W("|---|---:|---:|")
for label, n, avg in length_stats:
    W(f"| {label} | {n:,} | {avg:,.0f} |")
W("")

W("---")
W("")
W("## 6. 发布时间分布")
W("")
W("### 一天 24h（按小时）")
W("")
W("| 小时 | 发帖数 | 平均赞 |")
W("|---|---:|---:|")
for h in sorted(hour_stats.keys()):
    n, s = hour_stats[h]
    W(f"| {h:02d}:00 | {n:,} | {s/max(n,1):,.0f} |")
W("")
W("### 一周 7 天")
W("")
W("| 周 | 发帖数 | 平均赞 |")
W("|---|---:|---:|")
dow_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
for d in range(7):
    n, s = dow_stats[d]
    W(f"| {dow_names[d]} | {n:,} | {s/max(n,1):,.0f} |")
W("")

W("---")
W("")
W("## 7. 年份分布")
W("")
W("| 年 | 发帖数 |")
W("|---|---:|")
for y in sorted(year_dist.keys()):
    W(f"| {y} | {year_dist[y]:,} |")
W("")

W("---")
W("")
W("## 8. 内容长度 vs 互动")
W("")
W("| body 长度 | 数量 | 平均赞 |")
W("|---|---:|---:|")
for label, n, avg in body_stats:
    W(f"| {label} | {n:,} | {avg:,.0f} |")
W("")

W("---")
W("")
W("## 9. AI 工具竞争格局（在 note 里被提到次数）")
W("")
W("| 工具 | 标题命中 | 正文命中 | 累计赞 |")
W("|---|---:|---:|---:|")
for tool, (nt, nb, tot) in sorted(ai_mentions.items(), key=lambda x: x[1][2], reverse=True):
    W(f"| {tool} | {nt:,} | {nb:,} | {tot:,} |")
W("")

W("---")
W("")
W("## 10. 用户痛点关键词（出现在正文里）")
W("")
W("| 关键词 | 含此词的 note 数 | 平均赞 |")
W("|---|---:|---:|")
for kw, (n, avg) in sorted(pain_stats.items(), key=lambda x: x[1][0], reverse=True):
    W(f"| {kw} | {n:,} | {avg:,.0f} |")
W("")

W("---")
W("")
W("## 11. KOL 矩阵（Top 50 作者，按累计赞）")
W("")
W("| # | 作者 | 帖子数 | 累计赞 | 累计评论 | 平均赞 |")
W("|---:|---|---:|---:|---:|---:|")
for i, r in enumerate(top_authors, 1):
    W(f"| {i} | {(r['author_nickname'] or '')[:18]} | {r['n']} | {r['total_likes']:,} | {r['total_com']:,} | {r['avg_likes']:,.0f} |")
W("")

W("---")
W("")
W("## 12. 接下来如何用这份数据")
W("")
W("**对 AcademiCats 产品决策**：")
W("- 看 §9 AI 工具竞争 → 看用户最爱用的是哪家，差异化定位")
W("- 看 §10 痛点关键词 → 高频痛点（查重 / 降AI率 / 导师 / DDL）= 必做功能")
W("- 看 §3 讨论驱动 note → 评论多的话题 = 用户最想问的，做成 demo 场景")
W("")
W("**对 AcademiCats 起号**：")
W("- §5 标题 pattern → 模仿高 ROI 模式（数字/速度宣告/AI 品牌）")
W("- §4 high-ROI tag → 起号必带这些 hashtag")
W("- §6 发布时间 → 在平均赞最高的时段发")
W("- §11 KOL 矩阵 → 学习 top 50 的内容结构")
W("")
W("---")
W("")
W("*报告由 `tools/analyze_v2.py` 生成。再次跑就更新最新数据。*")

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"\nReport written: {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")
con.close()
