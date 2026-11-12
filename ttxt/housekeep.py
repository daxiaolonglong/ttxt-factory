"""磁盘预算、限额巡检与临时清理。"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import config as cfg
from .logging import get_logger

GB = 1024 ** 3

LOG = get_logger("housekeep")

MAJOR_PATHS: dict[str, Path] = {
    "control": cfg.CONTROL_DIR,
    "categories": cfg.CATEGORIES_DIR,
    "crawler": cfg.CRAWLER_DIR,
    "intelligence": cfg.INTELLIGENCE_DIR,
    "writing": cfg.WRITING_DIR,
    "dashboard": cfg.DASHBOARD_DIR,
    "agents": cfg.AGENTS_DIR,
    "rules": cfg.RULES_DIR,
    "wiki": cfg.WIKI_DIR,
    "logs": cfg.LOG_DIR,
    "tests": cfg.TESTS_DIR,
    "backups": cfg.BACKUP_DIR,
    "temp": cfg.TEMP_DIR,
    "venv": cfg.VENV_DIR,
    "pip_cache": cfg.PIP_CACHE_DIR,
}


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def free_space_gb(path: Path = cfg.PROJECT_ROOT) -> float:
    usage = shutil.disk_usage(str(path))
    return usage.free / GB


def check_free_space(min_gb: float | None = None) -> tuple[bool, float]:
    if min_gb is None:
        min_gb = float(cfg.load_config().get("disk", {}).get("min_free_gb", 5))
    free = free_space_gb()
    return free >= min_gb, free


def clean_temp(max_age_hours: float = 24.0, max_gb: float | None = None) -> dict[str, Any]:
    """删除 99_temp 中超过 TTL 的文件;若总量超限,按最旧优先删除到限额内。"""
    if max_gb is None:
        max_gb = float(cfg.load_config().get("disk", {}).get("temp_max_gb", 2))
    limit = max_gb * GB
    now = time.time()
    max_age = max_age_hours * 3600
    deleted = freed = skipped = 0
    files: list[tuple[float, int, Path]] = []
    if cfg.TEMP_DIR.exists():
        for root, _dirs, names in os.walk(cfg.TEMP_DIR):
            for name in names:
                fp = Path(root) / name
                try:
                    st = fp.stat()
                except OSError:
                    continue
                if now - st.st_mtime > max_age:
                    try:
                        fp.unlink()
                        deleted += 1
                        freed += st.st_size
                    except OSError:
                        skipped += 1
                else:
                    files.append((st.st_mtime, st.st_size, fp))

    total = sum(size for _mtime, size, _fp in files)
    if total > limit:
        files.sort(key=lambda item: item[0])
        for _mtime, size, fp in files:
            if total <= limit:
                break
            try:
                fp.unlink()
                deleted += 1
                freed += size
                total -= size
            except OSError:
                skipped += 1

    result = {
        "deleted_files": deleted,
        "freed_bytes": freed,
        "skipped_locked": skipped,
        "temp_size_bytes": dir_size(cfg.TEMP_DIR),
        "temp_max_gb": max_gb,
    }
    LOG.info("housekeep.clean_temp", "temporary files cleaned", **result)
    return result


def rotate_logs(max_mb: float | None = None, keep: int | None = None) -> dict[str, Any]:
    """日志超过上限即改名归档;归档数量超过 keep 时删除最旧。"""
    if max_mb is None:
        max_mb = float(cfg.load_config().get("disk", {}).get("log_max_mb", 10))
    if keep is None:
        keep = int(cfg.load_config().get("disk", {}).get("log_keep", 7))
    archived = 0
    removed = 0
    if not cfg.LOG_DIR.exists():
        return {"archived": 0, "removed": 0, "log_size_bytes": 0}
    for path in sorted(cfg.LOG_DIR.iterdir()):
        if path.is_file() and path.suffix == ".jsonl" and path.stat().st_size > max_mb * 1024 * 1024:
            archive = path.with_name(f"{path.name}.{time.strftime('%Y%m%d%H%M%S')}.bak")
            try:
                path.rename(archive)
                archived += 1
            except OSError:
                continue
    backups = sorted(cfg.LOG_DIR.glob("*.bak"), key=lambda p: p.stat().st_mtime)
    for old in backups[: max(0, len(backups) - keep)]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            continue
    result = {"archived": archived, "removed": removed, "log_size_bytes": dir_size(cfg.LOG_DIR)}
    LOG.info("housekeep.rotate_logs", "logs rotated", **result)
    return result


def _prune_dir(directory: Path, limit_bytes: int) -> dict[str, Any]:
    """按最旧优先删除目录内文件直到低于限额;被占用文件跳过。"""
    deleted = freed = skipped = 0
    if not directory.exists():
        return {"deleted_files": 0, "freed_bytes": 0, "skipped_locked": 0, "size_bytes": 0, "limit_bytes": limit_bytes}
    files: list[tuple[float, int, Path]] = []
    for root, _dirs, names in os.walk(directory):
        for name in names:
            fp = Path(root) / name
            try:
                st = fp.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, fp))
    total = sum(size for _mtime, size, _fp in files)
    if total > limit_bytes:
        files.sort(key=lambda item: item[0])
        for _mtime, size, fp in files:
            if total <= limit_bytes:
                break
            try:
                fp.unlink()
                deleted += 1
                freed += size
                total -= size
            except OSError:
                skipped += 1
    return {
        "deleted_files": deleted,
        "freed_bytes": freed,
        "skipped_locked": skipped,
        "size_bytes": dir_size(directory),
        "limit_bytes": limit_bytes,
    }


def clean_pip_cache(max_gb: float | None = None) -> dict[str, Any]:
    if max_gb is None:
        max_gb = float(cfg.load_config().get("disk", {}).get("pip_cache_max_gb", 1))
    result = _prune_dir(cfg.PIP_CACHE_DIR, int(max_gb * GB))
    result["dir"] = str(cfg.PIP_CACHE_DIR)
    LOG.info("housekeep.clean_pip_cache", **result)
    return result


def clean_browser_cache(max_gb: float | None = None) -> dict[str, Any]:
    if max_gb is None:
        max_gb = float(cfg.load_config().get("disk", {}).get("browser_max_gb", 3))
    result = _prune_dir(cfg.CRAWLER_DIR / ".browser", int(max_gb * GB))
    result["dir"] = str(cfg.CRAWLER_DIR / ".browser")
    LOG.info("housekeep.clean_browser_cache", **result)
    return result


def db_size_warnings() -> list[dict[str, Any]]:
    """数据库超过配置上限时给出告警(不删除数据)。"""
    from ttxt.db import system_db_path

    caps = cfg.load_config().get("disk", {}).get("db_max_mb", {})
    warnings: list[dict[str, Any]] = []

    def check(path: Path, cap_mb: float, label: str) -> None:
        if path.exists() and path.stat().st_size > cap_mb * 1024 * 1024:
            warnings.append(
                {
                    "path": str(path),
                    "label": label,
                    "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
                    "cap_mb": cap_mb,
                }
            )

    check(system_db_path(), float(caps.get("system", 200)), "system")
    for db_path in sorted(cfg.CATEGORIES_DIR.glob("*/articles.db")):
        check(db_path, float(caps.get("article", 2048)), f"category {db_path.parent.name}")
    return warnings


def report() -> dict[str, Any]:
    config = cfg.load_config()
    disk_cfg = config.get("disk", {})
    sizes = {name: dir_size(path) for name, path in MAJOR_PATHS.items()}
    total = sum(sizes.values())
    free = free_space_gb()
    budget_gb = float(disk_cfg.get("project_budget_gb", 20))
    min_free = float(disk_cfg.get("min_free_gb", 5))
    return {
        "root": str(cfg.PROJECT_ROOT),
        "sizes_bytes": sizes,
        "project_total_gb": round(total / GB, 3),
        "budget_gb": budget_gb,
        "within_budget": total <= budget_gb * GB,
        "free_gb": round(free, 1),
        "min_free_gb": min_free,
        "free_ok": free >= min_free,
        "caps": {
            "temp_max_gb": disk_cfg.get("temp_max_gb"),
            "pip_cache_max_gb": disk_cfg.get("pip_cache_max_gb"),
            "log_max_mb": disk_cfg.get("log_max_mb"),
            "backup_keep": disk_cfg.get("backup_keep"),
            "browser_max_gb": disk_cfg.get("browser_max_gb"),
        },
        "caps_status": {
            "pip_cache_bytes": sizes["pip_cache"],
            "pip_cache_within": sizes["pip_cache"] <= float(disk_cfg.get("pip_cache_max_gb", 1)) * GB,
            "browser_bytes": dir_size(cfg.CRAWLER_DIR / ".browser"),
            "browser_within": dir_size(cfg.CRAWLER_DIR / ".browser")
            <= float(disk_cfg.get("browser_max_gb", 3)) * GB,
            "db_warnings": db_size_warnings(),
        },
    }


def run(clean: bool = False, temp_max_age_hours: float = 24.0) -> dict[str, Any]:
    result: dict[str, Any] = {"report": report()}
    if clean:
        result["clean_temp"] = clean_temp(max_age_hours=temp_max_age_hours)
        result["rotate_logs"] = rotate_logs()
        result["clean_pip_cache"] = clean_pip_cache()
        result["clean_browser_cache"] = clean_browser_cache()
    return result
