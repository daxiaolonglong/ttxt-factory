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


def load_registry() -> dict[str, Any]:
    return load_json(REGISTRY_PATH)


def resolve_path(value: str | Path) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


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
