"""结构化 JSONL 日志(按日文件 + 超限轮转)。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as cfg

_MAX_MB_CACHE: float | None = None


def _max_mb() -> float:
    global _MAX_MB_CACHE
    if _MAX_MB_CACHE is None:
        try:
            value = float(cfg.load_config().get("disk", {}).get("log_max_mb", 10))
        except Exception:
            value = 10.0
        _MAX_MB_CACHE = value
    return _MAX_MB_CACHE


def log_path() -> Path:
    return cfg.LOG_DIR / f"ttxt-{datetime.now().strftime('%Y%m%d')}.jsonl"


def _rotate_if_needed(path: Path) -> None:
    limit = _max_mb() * 1024 * 1024
    if path.exists() and path.stat().st_size > limit:
        stamp = datetime.now().strftime("%H%M%S")
        path.rename(path.with_name(f"{path.name}.{stamp}.bak"))


def log_event(
    level: str,
    module: str,
    event: str,
    message: str = "",
    **meta: Any,
) -> None:
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = log_path()
    _rotate_if_needed(path)
    record: dict[str, Any] = {
        "ts": cfg.now_iso(),
        "level": level,
        "module": module,
        "event": event,
        "message": message,
    }
    if meta:
        record["meta"] = meta
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class Logger:
    def __init__(self, module: str) -> None:
        self.module = module

    def info(self, event: str, message: str = "", **meta: Any) -> None:
        log_event("info", self.module, event, message, **meta)

    def warn(self, event: str, message: str = "", **meta: Any) -> None:
        log_event("warn", self.module, event, message, **meta)

    def error(self, event: str, message: str = "", **meta: Any) -> None:
        log_event("error", self.module, event, message, **meta)


def get_logger(module: str) -> Logger:
    return Logger(module)
