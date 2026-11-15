"""备份轮转的护栏:轮转可以删,但**必须逐条留痕**;``backup_keep<=0`` 一律不删。

事故背景(2026-11-15,C-1 整改前):复用 ``scripts/backup.py`` 的 ``snapshot()`` 时以为
轮转在 ``main()`` 里,实际轮转就在 ``snapshot()`` 内部;``backup_keep=7`` 当场静默删掉
两个最老快照,日志里没有任何痕迹。这里把两条口径钉死:

1. 每个被删的 zip 都要打 ``backup.pruned`` 日志,且日志里必须带上被删文件的路径;
2. ``backup_keep<=0`` = 全部保留,不轮转;配置缺键时默认要宽(30),不得退化成"删到只剩 7 份"。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(r"D:\ttxt")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Recorder:
    """假 logger:把 info/warn 的 event、位置参数、关键字参数都原样记下来。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def info(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.events.append({"level": "info", "event": event, "args": args, "kwargs": kwargs})

    def warn(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.events.append({"level": "warn", "event": event, "args": args, "kwargs": kwargs})

    def by_name(self, name: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["event"] == name]


def _load_backup(monkeypatch, tmp_path: Path, keep: int | None) -> tuple[Any, Any, _Recorder]:
    spec = importlib.util.spec_from_file_location("ttxt_backup_script", ROOT / "scripts" / "backup.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from ttxt import config as cfg

    backup_dir = tmp_path / "11_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfg, "BACKUP_DIR", backup_dir)
    disk: dict[str, Any] = {} if keep is None else {"backup_keep": keep}
    monkeypatch.setattr(cfg, "load_config", lambda: {"disk": disk})
    monkeypatch.setattr(module, "CODE_ITEMS", [])  # 不复制源码树:测试只关心轮转语义
    recorder = _Recorder()
    monkeypatch.setattr(module, "get_logger", lambda name: recorder)
    return module, cfg, recorder


def _seed_zips(backup_dir: Path, count: int) -> list[Path]:
    """造若干"历史快照",mtime 严格递增,保证轮转的先后顺序可判定。"""
    paths: list[Path] = []
    for index in range(count):
        path = backup_dir / f"old-{index}.zip"
        path.write_bytes(b"PK\x03\x04placeholder")
        stamp = 1_700_000_000 + index * 3600
        os.utime(path, (stamp, stamp))
        paths.append(path)
    return paths


def test_rotation_prunes_oldest_and_logs_every_deletion(monkeypatch, tmp_path: Path) -> None:
    module, cfg, recorder = _load_backup(monkeypatch, tmp_path, keep=2)
    seeded = _seed_zips(cfg.BACKUP_DIR, 4)

    zip_path = module.snapshot("keep-two", [])

    assert zip_path.is_file() and "keep-two" in zip_path.name
    remaining = sorted(p.name for p in cfg.BACKUP_DIR.glob("*.zip"))
    assert len(remaining) == 2, remaining
    assert zip_path.name in remaining, remaining

    pruned = recorder.by_name("backup.pruned")
    assert len(pruned) == 3, recorder.events  # 4 个历史 + 1 个新快照,keep=2 → 删 3 个
    logged = {event["args"][0] for event in pruned if event["args"]}
    assert logged == {str(path) for path in seeded if path.name not in remaining}, logged
    assert all(event["kwargs"].get("keep") == 2 for event in pruned), pruned
    gone = [path for path in seeded if path.name not in remaining]
    assert gone and all(not path.exists() for path in gone)


def test_keep_zero_keeps_every_snapshot(monkeypatch, tmp_path: Path) -> None:
    module, cfg, recorder = _load_backup(monkeypatch, tmp_path, keep=0)
    seeded = _seed_zips(cfg.BACKUP_DIR, 4)

    module.snapshot("keep-none", [])

    assert all(path.exists() for path in seeded)
    assert len(list(cfg.BACKUP_DIR.glob("*.zip"))) == 5
    assert len(recorder.by_name("backup.rotation_disabled")) == 1
    assert not recorder.by_name("backup.pruned")


def test_missing_config_key_defaults_to_generous_keep(monkeypatch, tmp_path: Path) -> None:
    """配置里没有 ``backup_keep`` 时不得退化成"删到只剩 7 份",默认要宽(30)。"""
    module, cfg, recorder = _load_backup(monkeypatch, tmp_path, keep=None)
    seeded = _seed_zips(cfg.BACKUP_DIR, 9)

    module.snapshot("default-keep", [])

    assert all(path.exists() for path in seeded)
    assert not recorder.by_name("backup.pruned")
