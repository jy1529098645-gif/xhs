"""Export tables to JSON/CSV under data/exports/."""
import csv
import json
import time
from pathlib import Path

from . import config, db


def export_all(fmt: str = "json") -> list[Path]:
    """fmt: 'json' | 'csv' — exports notes, comments, authors, images."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = config.EXPORTS_DIR / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for table in ("notes", "comments", "authors", "images"):
        path = out_dir / f"{table}.{fmt}"
        rows = list(db.conn().execute(f"SELECT * FROM {table}"))
        if fmt == "json":
            path.write_text(
                json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            if rows:
                with path.open("w", encoding="utf-8-sig", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                    w.writeheader()
                    for r in rows:
                        w.writerow(dict(r))
            else:
                path.write_text("", encoding="utf-8")
        paths.append(path)
    return paths
