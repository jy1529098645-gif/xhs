"""CLI entry point.

Examples:
    python -m crawler.main login
    python -m crawler.main search --keywords "穿搭,夏季" --pages 20
    python -m crawler.main author --ids "abc,def" --max-per-user 200
    python -m crawler.main topic --names "通勤穿搭"  --pages 10
    python -m crawler.main urls --file targets.txt
    python -m crawler.main detail --comment-pages 5
    python -m crawler.main stats
    python -m crawler.main export --fmt json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from . import config


def _setup_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<level>{level: <7}</level> | {time:HH:mm:ss} | {message}")
    logger.add(config.LOG_PATH, level="DEBUG", rotation="20 MB", retention=5,
               encoding="utf-8")


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def cmd_login(args):
    from .login import run_login
    ok = run_login(timeout_seconds=args.timeout, force=args.force)
    sys.exit(0 if ok else 1)


def cmd_search(args):
    from .discover import discover_search
    n = discover_search(_split(args.keywords), pages=args.pages, sort=args.sort)
    logger.success("Queued {} new notes", n)
    if args.then_detail:
        cmd_detail(args)


def cmd_author(args):
    from .discover import discover_author
    n = discover_author(_split(args.ids), max_per_user=args.max_per_user)
    logger.success("Queued {} new notes", n)
    if args.then_detail:
        cmd_detail(args)


def cmd_topic(args):
    from .discover import discover_topic
    n = discover_topic(_split(args.names), pages=args.pages)
    logger.success("Queued {} new notes", n)
    if args.then_detail:
        cmd_detail(args)


def cmd_urls(args):
    from .discover import discover_urls
    n = discover_urls(Path(args.file))
    logger.success("Queued {} new notes", n)
    if args.then_detail:
        cmd_detail(args)


def cmd_detail(args):
    from .detail import run_detail_batch
    counts = run_detail_batch(
        comment_pages=args.comment_pages,
        retry_errors=args.retry_errors,
        max_notes=args.max_notes,
        download_images=not args.no_images,
        worker_id=getattr(args, "worker_id", 0),
    )
    logger.success("Detail batch done: {}", counts)


def cmd_stats(args):
    from .db import stats
    s = stats()
    for k, v in s.items():
        logger.info("{:>20}: {}", k, v)


def cmd_export(args):
    from .export import export_all
    paths = export_all(fmt=args.fmt)
    for p in paths:
        logger.success("wrote {}", p)


def cmd_dashboard(args):
    from tools.render_dashboard import render
    out = render()
    logger.success("Dashboard: {}", out)
    logger.info("Open in browser: file:///{}", out.as_posix())


def cmd_http_drain(args):
    from .httpx_detail import run_http_drain_sync
    counts = run_http_drain_sync(
        concurrency=args.concurrency,
        retry_errors=args.retry_errors,
        max_notes=args.max_notes,
        download_images=not args.no_images,
    )
    logger.success("HTTP drain done: {}", counts)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="crawler", description="xhs scraper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="open Chrome for QR-scan login")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--force", action="store_true",
                   help="clear cookies first and always prompt for QR rescan")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("search", help="discover via keyword search")
    p.add_argument("--keywords", required=True, help="comma-separated")
    p.add_argument("--pages", type=int, default=10)
    p.add_argument("--sort", default="general",
                   choices=["general", "time_descending", "popularity_descending"])
    _add_then_detail(p)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("author", help="discover via creator profile")
    p.add_argument("--ids", required=True, help="comma-separated user_ids")
    p.add_argument("--max-per-user", type=int, default=200)
    _add_then_detail(p)
    p.set_defaults(func=cmd_author)

    p = sub.add_parser("topic", help="discover via topic/tag")
    p.add_argument("--names", required=True, help="comma-separated topic names")
    p.add_argument("--pages", type=int, default=10)
    _add_then_detail(p)
    p.set_defaults(func=cmd_topic)

    p = sub.add_parser("urls", help="discover from a file of URLs")
    p.add_argument("--file", required=True)
    _add_then_detail(p)
    p.set_defaults(func=cmd_urls)

    p = sub.add_parser("detail", help="scrape pending queue entries (Chrome-based)")
    _add_detail_args(p)
    p.set_defaults(func=cmd_detail)

    p = sub.add_parser("http-drain",
                       help="fast HTTP drain: SSR HTML parsing, no Chrome, "
                            "high concurrency")
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument("--retry-errors", action="store_true")
    p.add_argument("--max-notes", type=int, default=None)
    p.add_argument("--no-images", action="store_true")
    p.set_defaults(func=cmd_http_drain)

    p = sub.add_parser("stats")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("export")
    p.add_argument("--fmt", choices=["json", "csv"], default="json")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("dashboard", help="render a self-contained HTML dashboard")
    p.set_defaults(func=cmd_dashboard)
    return ap


def _add_then_detail(p: argparse.ArgumentParser) -> None:
    p.add_argument("--then-detail", action="store_true",
                   help="after discovery, immediately scrape detail+comments")
    _add_detail_args(p, with_prefix=False)


def _add_detail_args(p: argparse.ArgumentParser, with_prefix: bool = True) -> None:
    p.add_argument("--comment-pages", type=int, default=5,
                   help="how many comment scroll-pages per note")
    p.add_argument("--retry-errors", action="store_true",
                   help="also re-attempt rows with status='error'")
    p.add_argument("--max-notes", type=int, default=None)
    p.add_argument("--no-images", action="store_true",
                   help="skip image downloads (still store URLs)")
    p.add_argument("--worker-id", type=int, default=0,
                   help="worker index for parallel runs (each uses its own Chrome profile)")


def main(argv=None):
    _setup_logging()
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
