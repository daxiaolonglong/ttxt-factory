"""环境体检:目录、配置、数据库、磁盘预算、Strata 只读状态。"""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Any

from . import config as cfg
from . import db, housekeep


def _strata_health(config: dict[str, Any]) -> dict[str, Any]:
    base = str(config.get("strata", {}).get("base_url", "http://127.0.0.1:8080")).rstrip("/")
    path = str(config.get("strata", {}).get("health_path", "/health"))
    try:
        with urllib.request.urlopen(base + path, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"ok": True, "data": payload}
    except Exception as exc:  # noqa: BLE001 - 健康检查失败不应中断体检
        return {"ok": False, "error": str(exc)}


def run_doctor() -> int:
    config = cfg.load_config()
    results: list[tuple[str, str, str]] = []  # (level, name, detail)

    def check(name: str, ok: bool, detail: str) -> None:
        results.append(("PASS" if ok else "FAIL", name, detail))

    # 1. 项目根与目录
    check("project.root", cfg.PROJECT_ROOT == Path("D:/ttxt"), str(cfg.PROJECT_ROOT))
    missing = [str(d) for d in cfg.REQUIRED_DIRS if not d.exists()]
    check("project.dirs", not missing, "all present" if not missing else f"missing: {missing}")

    # 2. 配置与注册表
    try:
        check("config.load", config.get("version") == 1, f"version={config.get('version')}")
    except Exception as exc:  # noqa: BLE001
        check("config.load", False, str(exc))
    try:
        registry = cfg.load_registry()
        slugs = [item["slug"] for item in registry.get("categories", [])]
        check("registry.categories", len(slugs) >= 1, ",".join(slugs))
    except Exception as exc:  # noqa: BLE001
        check("registry.categories", False, str(exc))

    # 3. 路径全部位于项目内
    try:
        bad = cfg.paths_within_project()
        check("paths.within_project", not bad, "ok" if not bad else f"outside: {bad}")
    except Exception as exc:  # noqa: BLE001
        check("paths.within_project", False, str(exc))

    # 4. system.db 完整性
    db_path = db.system_db_path()
    if not db_path.exists():
        check("db.system", False, f"missing: {db_path}")
    else:
        try:
            con = sqlite3.connect(db_path)
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            count = con.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            con.close()
            check("db.system", integrity == "ok" and count >= 1, f"integrity={integrity}, categories={count}")
        except Exception as exc:  # noqa: BLE001
            check("db.system", False, str(exc))

    # 5. 磁盘预算
    report = housekeep.report()
    check(
        "disk.free",
        bool(report["free_ok"]),
        f"free={report['free_gb']}GB min={report['min_free_gb']}GB",
    )
    check(
        "disk.budget",
        bool(report["within_budget"]),
        f"used={report['project_total_gb']}GB budget={report['budget_gb']}GB",
    )

    # 6. Venv
    venv_python = cfg.VENV_DIR / "Scripts" / "python.exe"
    check("venv.python", venv_python.exists(), str(venv_python))

    # 7. Strata 状态(只读;P1 不要求模型在线)
    strata = _strata_health(config)
    if strata["ok"]:
        data = strata["data"]
        check(
            "strata.health",
            True,
            f"model={data.get('model')} context={data.get('max_context')} loaded={data.get('loaded')}",
        )
    else:
        results.append(("WARN", "strata.health", f"not reachable: {strata['error']}"))

    # 输出
    for level, name, detail in results:
        print(f"[{level}] {name}: {detail}")
    failures = [item for item in results if item[0] == "FAIL"]
    print(f"\nsummary: {len(results) - len(failures)} ok, {len(failures)} failed")
    return 0 if not failures else 1
