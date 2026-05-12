"""Build a single self-contained Markdown package ready to drag into another
AI chat (Claude / ChatGPT). Contains the project brief, dataset summary and
all crawled notes with their top comments.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crawler.config import DB_PATH, EXPORTS_DIR  # noqa: E402


HEADER = """\
# 小红书爆款内容竞争分析任务（输入数据包）

> 这是给你（接收这份文件的 AI 助手）的任务说明 + 原始数据。请仔细读完任务说明再开始分析。

---

## 我做的产品（背景）

**AcademiCats** —— 一款 AI 驱动的学术研究工作台。核心用户群：

- **研究生/博士生**：写文献综述、论文初稿、答辩稿，做文献调研
- **本科毕业生**：开题报告、毕业论文
- **留学申请者**：Personal Statement、SOP、CV
- **科研工作者**：跨学科文献检索、合成

产品差异化：
- **多智能体论文 Review 流水线**（5 阶段：结构/论证/证据/语言/原创性）
- **17 个可组合 Mod**（搜索/写作 Lab/文献综述/PS 生成/查重降 AI 率/引用格式...）
- **14 个学术源并行检索**（Semantic Scholar / OpenAlex / arXiv / 知网...）
- **新增：Personal Statement & Resume 一键生成**（直接对标留学申请人群）

公司目前阶段：v1.7.2 Beta，准备 D0 公开测试，需要从小红书做用户获取冷启动。

---

## 我让你做什么（任务清单）

基于下面的 **155 篇小红书爆款笔记 + 1497 条评论**，输出以下 6 部分：

### 1. 爆款规律提炼
- 标题公式（数字党 / 痛点开头 / 反差 / 干货宣告 ...）
- 正文结构（开头钩子 / 干货密度 / 列表分点 / 故事性 ...）
- 视觉风格猜测（封面文案密度 / 配色 / 是否截图风 / 手写感）
- Tag 组合规律（高互动笔记 vs 低互动的 tag 差异）

### 2. 用户真实痛点 & 身份画像
从 1497 条评论里提炼：
- 高频痛点关键词（"导师"/"查重"/"AI率"/"还有 3 天"...）
- 用户身份（本/硕/博/在职/海归，从用语和场景判断）
- 经典求助 pattern（"求指令""怎么开通""能不能私聊"...）
- 评论里暴露的工具竞品（DeepSeek / Gemini / GPT / 文优小助 / 知网 ...）

### 3. 起号计划草案（90 天路线图）
为我新建一个小红书账号，从 0 → 1w 粉的路线。包括：
- **账号定位**：人设（学姐/导师/工具评测博主/陪写）+ 一句话标签
- **内容矩阵**：3 类支柱内容的比例和示例（如 5:3:2）
- **发布节奏**：周更几次 / 黄金时段
- **关键里程碑**：粉丝数 → 内容类型转变节点
- **流量入口策略**：搜索 SEO 关键词布局 + 话题挂载

### 4. 可量产内容模板（3-5 个）
每个模板包含：
- 标题公式（带变量占位符）
- 正文骨架（带段落功能注释）
- 封面建议（文案 + 风格）
- 适用产品 Mod 场景（如「这个模板适合宣传 Writing Lab 的文献综述能力」）
- 预期互动量级和原因

### 5. 反面教材
从样本里挑出 5-10 篇相对低互动的，分析"为什么没爆"，作为不该做的清单。

### 6. 这份数据的缺口
诚实告诉我样本偏差：
- 关键词覆盖盲区
- 时间维度缺失（都是热门，缺新发布趋势？）
- 用户类型分布偏差
- 还应该补抓哪些维度（如视频笔记、长尾号、特定地区）

---

## 数据规格

- **来源**：小红书 PC Web，关键词 + 话题搜索（按热度排序）
- **采集时间**：2026-05-12 凌晨
- **关键词**：AI写论文、文献综述、论文润色、开题报告、论文写作（每词约 20-40 篇）
- **字段完整度**：标题/正文/作者昵称/IP 属地/点赞/收藏/评论/分享/Tag/评论原文/评论 IP 全部完整
- **未采集**：浏览量（小红书 PC 端不公开）、私密笔记、视频笔记下载

---

## 数据集摘要

"""

TASK_FOOTER = """

---

## 输出要求

- 请按上面 6 部分顺序输出
- 不需要重述任务说明
- 用具体例子支撑结论（引用具体笔记或评论）
- 数据有限就实话实说，别编
- 起号计划部分要可执行，不要套话

"""


def _short(s: str, n: int) -> str:
    if not s:
        return ""
    s = s.replace("\r", "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _format_count(n: int | None) -> str:
    if n is None or n == 0:
        return "0"
    if n >= 10000:
        return f"{n / 10000:.1f}w"
    return str(n)


def build() -> Path:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    notes = list(db.execute(
        """SELECT note_id, type, title, body, author_nickname, ip_location,
                  publish_time_ms, liked_count, collected_count,
                  comment_count, share_count, image_count, tags_json
           FROM notes
           WHERE liked_count IS NOT NULL
           ORDER BY (COALESCE(liked_count,0)+COALESCE(collected_count,0)*2
                    +COALESCE(comment_count,0)*3+COALESCE(share_count,0)*5) DESC"""
    ))

    comments_by_note: dict[str, list[sqlite3.Row]] = {}
    for r in db.execute(
        "SELECT note_id, parent_id, nickname, content, like_count, ip_location "
        "FROM comments ORDER BY note_id, like_count DESC NULLS LAST"
    ):
        comments_by_note.setdefault(r["note_id"], []).append(r)

    # ---------------- summary table ----------------
    n_notes = len(notes)
    n_comments = sum(len(v) for v in comments_by_note.values())
    likes = [n["liked_count"] for n in notes if n["liked_count"]]
    collects = [n["collected_count"] for n in notes if n["collected_count"]]
    coms = [n["comment_count"] for n in notes if n["comment_count"]]

    def stats(xs):
        if not xs:
            return "-"
        xs = sorted(xs)
        median = xs[len(xs) // 2]
        return (f"min={xs[0]} | p50={median} | "
                f"p90={xs[int(len(xs) * 0.9)]} | max={xs[-1]} | avg={sum(xs)//len(xs)}")

    summary = []
    summary.append(f"- 笔记数：{n_notes}\n")
    summary.append(f"- 评论数：{n_comments}\n")
    summary.append(f"- 点赞分布：{stats(likes)}\n")
    summary.append(f"- 收藏分布：{stats(collects)}\n")
    summary.append(f"- 评论数分布：{stats(coms)}\n\n")

    summary.append("**关键词来源（已采集）**：\n\n")
    summary.append("| 关键词 | 抓到笔记 |\n|---|---|\n")
    by_kw = list(db.execute(
        "SELECT source_value, COUNT(*) c FROM discover_queue "
        "WHERE status='done' GROUP BY source_value ORDER BY c DESC"
    ))
    for r in by_kw:
        summary.append(f"| {r['source_value']} | {r['c']} |\n")
    summary.append("\n")

    summary.append("**IP 属地分布 Top 10**（采集到 IP 的笔记中）：\n\n")
    summary.append("| 属地 | 笔记数 |\n|---|---|\n")
    for r in db.execute(
        "SELECT ip_location, COUNT(*) c FROM notes "
        "WHERE ip_location IS NOT NULL GROUP BY ip_location "
        "ORDER BY c DESC LIMIT 10"
    ):
        summary.append(f"| {r['ip_location']} | {r['c']} |\n")
    summary.append("\n")

    # ---------------- per-note section ----------------
    body_parts: list[str] = []
    body_parts.append("## 笔记数据（按综合互动量降序）\n\n")
    body_parts.append("---\n\n")

    for rank, n in enumerate(notes, 1):
        title = n["title"] or "（无标题）"
        tags = []
        try:
            tags = json.loads(n["tags_json"] or "[]")
        except Exception:
            pass
        body = (n["body"] or "").strip()
        # Cap body at 800 chars to keep package compact while preserving signal
        body_short = _short(body, 800)

        ts_str = ""
        if n["publish_time_ms"]:
            ts_str = time.strftime(
                "%Y-%m-%d", time.localtime(n["publish_time_ms"] / 1000)
            )

        body_parts.append(
            f"### #{rank}  {title}\n"
            f"- **互动**：❤ {_format_count(n['liked_count'])} "
            f" / ⭐ {_format_count(n['collected_count'])} "
            f" / 💬 {_format_count(n['comment_count'])} "
            f" / 📤 {_format_count(n['share_count'])} "
            f" / 🖼 {n['image_count'] or 0} 张\n"
            f"- **作者**：@{n['author_nickname'] or '?'} "
            f"  | IP：{n['ip_location'] or '?'} "
            f" | 发布：{ts_str or '?'} "
            f" | 类型：{n['type'] or '?'}\n"
            f"- **Tags**：{', '.join(tags) if tags else '（无）'}\n"
            f"- **正文**：\n\n> {body_short.replace(chr(10), chr(10) + '> ')}\n\n"
        )

        # Top comments
        cs = comments_by_note.get(n["note_id"], [])
        # Skip empty / placeholder comments
        cs = [c for c in cs if c["content"] and c["content"].strip()]
        if cs:
            body_parts.append("**Top 评论**（按点赞降序，最多 15 条）：\n")
            for c in cs[:15]:
                tag = "↳" if c["parent_id"] else "•"
                cnt = _short(c["content"], 220).replace("\n", " ")
                body_parts.append(
                    f"- {tag} @{c['nickname'] or '?'} "
                    f"(❤{c['like_count'] or 0}, IP:{c['ip_location'] or '?'}) — {cnt}\n"
                )
            body_parts.append("\n")

        body_parts.append("---\n\n")

    # ---------------- assemble ----------------
    out = EXPORTS_DIR / "xhs_analysis_package.md"
    with out.open("w", encoding="utf-8") as f:
        f.write(HEADER)
        f.writelines(summary)
        f.write("\n")
        f.writelines(body_parts)
        f.write(TASK_FOOTER)

    return out


if __name__ == "__main__":
    p = build()
    size_kb = p.stat().st_size / 1024
    print(f"wrote {p} ({size_kb:.0f} KB)")
