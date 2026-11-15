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
    # P23(体检 2026-11-15 §9.1):`07_tmp` 与 `资产库` 以前不在任何一个桶里 ⇒ 磁盘预算
    # 看不见真实根目录占用(housekeep 报 1.301 GB,根目录实测 6.60 GB),temp 的限额与
    # TTL 也永远管不到真正在涨的 `07_tmp`。登记为**计量桶**:只统计、只告警,不自动删 ——
    # `07_tmp` 里放着往轮账本与取证件,删不删由人决定(规则 29)。
    "scratch": cfg.SCRATCH_DIR,
    "assets": cfg.ASSET_DIR,
    "venv": cfg.VENV_DIR,
    "pip_cache": cfg.PIP_CACHE_DIR,
    "tools": cfg.PROJECT_ROOT / "tools",
    # 根目录口径要闭合:仓库自身的 .git 与本地凭据目录也占盘,不登记就永远算"未登记占用"
    "git": cfg.PROJECT_ROOT / ".git",
    "secrets": cfg.PROJECT_ROOT / ".secrets",
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
    # P12:pytest base temp 目录散落在项目根(pytest-of-*),必须纳入统计,杜绝预算盲区
    sizes["pytest_of"] = sum(
        dir_size(p) for p in sorted(cfg.PROJECT_ROOT.glob("pytest-of-*")) if p.is_dir()
    )
    total = sum(sizes.values())
    # P23:桶是**白名单**,白名单本身就是这次的盲区 —— 根目录里任何没登记的一级目录
    # 都不会出现在预算里(实测差值 5.3 GB 全是未登记目录)。这里把根目录逐子项核算一遍,
    # 凡是没进桶的按大小列出,让"看不见的大象"当场可见(只报告,不删除)。
    bucket_names = {path.name for path in MAJOR_PATHS.values()}
    entries: list[tuple[str, int]] = []
    root_scan_error = ""
    try:
        root_entries = sorted(cfg.PROJECT_ROOT.iterdir())
    except OSError as exc:
        root_entries = []
        root_scan_error = f"{type(exc).__name__}: {exc}"
    for entry in root_entries:
        try:
            if entry.is_dir():
                size = dir_size(entry)
            elif entry.is_file():
                size = entry.stat().st_size
            else:
                size = 0
        except OSError:
            size = 0
        entries.append((entry.name, size))
    unaccounted = sorted(
        (
            {"name": name, "bytes": size}
            for name, size in entries
            if name not in bucket_names and not name.startswith("pytest-of")
        ),
        key=lambda item: item["bytes"],
        reverse=True,
    )
    unaccounted_bytes = sum(item["bytes"] for item in unaccounted)
    root_total_bytes = sum(size for _name, size in entries)
    scratch_max_gb = float(disk_cfg.get("scratch_max_gb", 2))
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
        # P23:`project_total_gb` 仍是"已进桶"口径(保持既有面板/测试语义不变);
        # 新增按根目录实测的口径,两个数一起摆出来,口径差本身就是要暴露的问题。
        "root_total_bytes": root_total_bytes,
        "root_total_gb": round(root_total_bytes / GB, 3),
        "within_root_budget": root_total_bytes <= budget_gb * GB,
        "unaccounted_bytes": unaccounted_bytes,
        "unaccounted_dirs": unaccounted[:10],
        "root_scan_error": root_scan_error or None,
        "caps": {
            "temp_max_gb": disk_cfg.get("temp_max_gb"),
            "scratch_max_gb": disk_cfg.get("scratch_max_gb"),
            "pip_cache_max_gb": disk_cfg.get("pip_cache_max_gb"),
            "log_max_mb": disk_cfg.get("log_max_mb"),
            "backup_keep": disk_cfg.get("backup_keep"),
            "browser_max_gb": disk_cfg.get("browser_max_gb"),
        },
        "caps_status": {
            "pip_cache_bytes": sizes["pip_cache"],
            "pip_cache_within": sizes["pip_cache"] <= float(disk_cfg.get("pip_cache_max_gb", 1)) * GB,
            # P23:temp 桶(99_temp)与 scratch 桶(07_tmp)分开计量:以前只有 temp,
            # 而真正在涨的是 scratch ⇒ 限额形同虚设。scratch 超限只告警,不自动删。
            "temp_bytes": sizes["temp"],
            "temp_within": sizes["temp"] <= float(disk_cfg.get("temp_max_gb", 2)) * GB,
            "scratch_bytes": sizes["scratch"],
            "scratch_within": sizes["scratch"] <= scratch_max_gb * GB,
            "browser_bytes": dir_size(cfg.CRAWLER_DIR / ".browser"),
            "browser_within": dir_size(cfg.CRAWLER_DIR / ".browser")
            <= float(disk_cfg.get("browser_max_gb", 3)) * GB,
            "db_warnings": db_size_warnings(),
        },
    }


def prune_pytest_dirs(base: Path | None = None, keep: int = 2) -> dict[str, Any]:
    """P12:清理项目根下旧的 pytest base temp 目录(pytest-of-*),只保留最近 ``keep`` 个。

    这些目录是 pytest 自动创建的测试缓存,位于项目根、不在 99_temp 桶内,
    必须有 TTL 清理,否则磁盘预算统计会持续漂移。正被占用的目录跳过并记录。
    """
    root = Path(base) if base is not None else cfg.PROJECT_ROOT
    candidates = [p for p in root.glob("pytest-of-*") if p.is_dir()]
    candidates.sort(key=lambda p: p.stat().st_mtime)
    stale = candidates[: max(0, len(candidates) - max(0, int(keep)))]
    removed: list[str] = []
    skipped = 0
    for old in stale:
        try:
            shutil.rmtree(old)
            removed.append(str(old))
        except OSError:
            skipped += 1
    result = {"removed": len(removed), "skipped_locked": skipped}
    LOG.info("housekeep.prune_pytest_dirs", "pytest base temp dirs pruned", **result)
    return result


def run(clean: bool = False, temp_max_age_hours: float = 24.0) -> dict[str, Any]:
    result: dict[str, Any] = {"report": report()}
    if clean:
        result["clean_temp"] = clean_temp(max_age_hours=temp_max_age_hours)
        result["rotate_logs"] = rotate_logs()
        result["clean_pip_cache"] = clean_pip_cache()
        result["clean_browser_cache"] = clean_browser_cache()
        result["prune_pytest_dirs"] = prune_pytest_dirs()
    return result
