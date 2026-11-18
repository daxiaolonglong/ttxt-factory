"""项目路径、配置与公共工具(P1)。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path("D:/ttxt")
CONTROL_DIR = PROJECT_ROOT / "00_control"
CONFIG_PATH = CONTROL_DIR / "config.json"
REGISTRY_PATH = CONTROL_DIR / "category_registry.json"
CATEGORIES_DIR = PROJECT_ROOT / "01_categories"
CRAWLER_DIR = PROJECT_ROOT / "02_crawler"
INTELLIGENCE_DIR = PROJECT_ROOT / "03_intelligence"
WRITING_DIR = PROJECT_ROOT / "04_writing"
DASHBOARD_DIR = PROJECT_ROOT / "05_dashboard"
AGENTS_DIR = PROJECT_ROOT / "06_agents"
RULES_DIR = PROJECT_ROOT / "07_rules"
WIKI_DIR = PROJECT_ROOT / "08_wiki"
LOG_DIR = PROJECT_ROOT / "09_logs"
TESTS_DIR = PROJECT_ROOT / "10_tests"
BACKUP_DIR = PROJECT_ROOT / "11_backups"
TEMP_DIR = PROJECT_ROOT / "99_temp"
# P23(2026-11-15 体检 §9.1 P1):`07_tmp` 才是测试与取证的真实落盘处 —— 规则 25/32 强制
# pytest basetemp 在 `07_tmp/pytest_basetemp`,每轮探针脚本也写在这里(实测 0.753 GB、
# 当日 +333 MB)。而磁盘桶口径里只有 `99_temp`(实测 3.4 MB),于是 temp 的 2 GB 限额和
# 24h TTL 从来没管到真正在涨的目录,`housekeep.report()` 的 1.301 GB 也远小于根目录实测
# 6.60 GB。这里登记 `07_tmp` 与 `资产库` 为**计量桶**:纳入统计与告警,不自动删除 ——
# `07_tmp` 里躺着往轮的账本与取证件,删不删由人决定(规则 29)。
SCRATCH_DIR = PROJECT_ROOT / "07_tmp"
ASSET_DIR = PROJECT_ROOT / "资产库"
VENV_DIR = PROJECT_ROOT / ".venv"
PIP_CACHE_DIR = PROJECT_ROOT / ".pip-cache"

REQUIRED_DIRS: list[Path] = [
    CONTROL_DIR,
    CONTROL_DIR / "model_gate",
    CATEGORIES_DIR,
    CRAWLER_DIR,
    CRAWLER_DIR / "spiders",
    CRAWLER_DIR / "extractors",
    CRAWLER_DIR / "cleaners",
    CRAWLER_DIR / "schedulers",
    CRAWLER_DIR / "checkpoints",
    CRAWLER_DIR / ".browser",
    INTELLIGENCE_DIR,
    INTELLIGENCE_DIR / "viral_scoring",
    INTELLIGENCE_DIR / "trends",
    INTELLIGENCE_DIR / "keyword_feedback",
    INTELLIGENCE_DIR / "knowledge",
    WRITING_DIR,
    WRITING_DIR / "prompts",
    WRITING_DIR / "drafts",
    WRITING_DIR / "evaluations",
    DASHBOARD_DIR,
    DASHBOARD_DIR / "web",
    DASHBOARD_DIR / "api",
    AGENTS_DIR,
    RULES_DIR,
    WIKI_DIR,
    LOG_DIR,
    TESTS_DIR,
    BACKUP_DIR,
    TEMP_DIR,
]

CATEGORY_SUBDIRS = ("", "rag", "keywords", "metrics")


def load_json(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_config() -> dict[str, Any]:
    return load_json(CONFIG_PATH)


# T-17(管线增量化):`pipeline` 配置段的代码级默认值。
# 配置里缺这个段(老 config)绝不能让管线崩:缺键一律回落到这里的默认值。
PIPELINE_DEFAULTS: dict[str, Any] = {
    "delta_enabled": True,
    "fts_delta_full_rebuild_at_changed_rows": 2000,
    "fts_delta_ops_budget": 20000,
    "cluster_bucket_report_over": 500,
}


def pipeline_settings() -> dict[str, Any]:
    """读 ``00_control/config.json`` 的 ``pipeline`` 段并合并默认值。

    只认词表内的键(未知键忽略),配置读失败按默认值走 —— 增量化是优化,
    不是可用性依赖:配置层出问题必须退到默认参数继续跑,而不是把 clean/analyze 卡死。
    """
    settings = dict(PIPELINE_DEFAULTS)
    try:
        section = load_config().get("pipeline") or {}
    except Exception:  # noqa: BLE001 - 缺段/坏文件都按默认值走
        section = {}
    if isinstance(section, dict):
        for key, value in section.items():
            if key in PIPELINE_DEFAULTS:
                settings[key] = value
    return settings


def load_registry() -> dict[str, Any]:
    return load_json(REGISTRY_PATH)


def resolve_path(value: str | Path) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def category_db_path(category: str) -> Path:
    """类目隔离库路径:01_categories/<slug>/articles.db。"""
    return CATEGORIES_DIR / category / "articles.db"


def ensure_dirs() -> list[str]:
    """创建全部必需目录与类目子目录,返回本次新建的目录列表。"""
    created: list[str] = []
    for d in REQUIRED_DIRS:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))
    config = load_config()
    for slug in config.get("categories", []):
        base = CATEGORIES_DIR / slug
        for sub in CATEGORY_SUBDIRS:
            target = base / sub if sub else base
            if not target.exists():
                target.mkdir(parents=True, exist_ok=True)
                created.append(str(target))
    return created


def paths_within_project() -> list[str]:
    """返回配置中解析到项目根之外的路径(应为空)。"""
    root = PROJECT_ROOT.resolve()
    bad: list[str] = []
    config = load_config()
    for key, value in config.get("paths", {}).items():
        p = resolve_path(value)
        if not p.is_relative_to(root):
            bad.append(f"{key}={p}")
    return bad


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def now_iso_ms() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
