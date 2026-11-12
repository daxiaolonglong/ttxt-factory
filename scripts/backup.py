"""阶段备份:SQLite .backup + 源码 zip 快照,保留 N 份(默认 7)。"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ttxt import config as cfg
from ttxt.logging import get_logger

CODE_ITEMS = [
    "ttxt",
    "scripts",
    "10_tests",
    "07_rules",
    "08_wiki",
    "05_dashboard",
    "06_agents",
    "04_writing",
    ".dsh",
    "README.md",
    "requirements.txt",
    "requirements-dev.txt",
    "00_control/config.json",
    "00_control/category_registry.json",
]


def _db_destination(stage: Path, db_path: Path) -> Path:
    try:
        rel = db_path.relative_to(cfg.CATEGORIES_DIR)
        return stage / "db" / rel
    except ValueError:
        return stage / "db" / db_path.name


def snapshot(label: str, db_paths: list[Path]) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{label}-{ts}"
    stage = cfg.BACKUP_DIR / name
    stage.mkdir(parents=True, exist_ok=True)

    for db_path in db_paths:
        if not db_path.exists():
            continue
        dest = _db_destination(stage, db_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(db_path)
        try:
            dst = sqlite3.connect(dest)
            try:
                with dst:
                    src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

    for item in CODE_ITEMS:
        src = cfg.PROJECT_ROOT / item
        dst = stage / item
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    zip_path = cfg.BACKUP_DIR / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in stage.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(stage))
    shutil.rmtree(stage)

    keep = int(cfg.load_config().get("disk", {}).get("backup_keep", 7))
    zips = sorted(cfg.BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    for old in zips[:-keep]:
        old.unlink()

    get_logger("backup").info("backup.created", str(zip_path), size_bytes=zip_path.stat().st_size)
    return zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backup.py")
    parser.add_argument("label", nargs="?", default="snapshot")
    args = parser.parse_args(argv)
    dbs = [cfg.CONTROL_DIR / "system.db", *sorted(cfg.CATEGORIES_DIR.glob("*/articles.db"))]
    path = snapshot(args.label, dbs)
    print(f"backup: {path} ({path.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
