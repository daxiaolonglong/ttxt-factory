"""环境体检:目录、配置、数据库、磁盘预算、Strata 只读状态。"""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Any

from . import config as cfg
from . import db, housekeep
from .crawler import store


def _namespace_identity(name: str) -> tuple[set[str], set[str]]:
    """只读取一个命名空间的 url_normalized / content_hash 全集(不写、不建、不改)。"""
    path = store.articles_db_path(name)
    con = store.read_only_connect(path)
    try:
        urls = {str(row[0]) for row in con.execute("SELECT DISTINCT url_normalized FROM articles")}
        hashes = {str(row[0]) for row in con.execute("SELECT DISTINCT content_hash FROM articles")}
    finally:
        con.close()
    return urls, hashes


def _namespace_identities() -> tuple[dict[str, tuple[set[str], set[str]]], dict[str, str]]:
    """读出每个命名空间的身份集合;读失败的命名空间单独报告,不毁掉整项体检。"""
    identities: dict[str, tuple[set[str], set[str]]] = {}
    errors: dict[str, str] = {}
    for name in store.namespaces_on_disk():
        try:
            identities[name] = _namespace_identity(name)
        except Exception as exc:  # noqa: BLE001
            errors[name] = f"{type(exc).__name__}: {exc}"
    return identities, errors


def namespace_overlap_rows(
    limit_per_pair: int = 20, identities: dict[str, tuple[set[str], set[str]]] | None = None
) -> list[dict[str, Any]]:
    """逐对命名空间的 url/hash 交集(C-3 对账;只读)。

    实测语料规模(千行级)下内存交足够;行数上万后应换成 SQL 分片或落一张对账表,
    不在这里做跨库 JOIN —— 那会破坏 ``08_wiki/db_schema.md`` §3 的类目物理隔离。

    只返回**有交集**的对;每对含 ``left/right/url_hits/hash_hits/registered_pair``
    以及样本(``url_samples/hash_samples``),便于人工核对。
    """
    names = store.namespaces_on_disk()
    registered = store.registered_namespaces()
    if identities is None:
        identities, _scan_errors = _namespace_identities()
    rows: list[dict[str, Any]] = []
    ordered = [name for name in names if name in identities]
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            left_urls, left_hashes = identities[left]
            right_urls, right_hashes = identities[right]
            url_hits = left_urls & right_urls
            hash_hits = left_hashes & right_hashes
            if not url_hits and not hash_hits:
                continue
            rows.append(
                {
                    "left": left,
                    "right": right,
                    "url_hits": len(url_hits),
                    "hash_hits": len(hash_hits),
                    "registered_pair": left in registered and right in registered,
                    "url_samples": sorted(url_hits)[:limit_per_pair],
                    "hash_samples": sorted(hash_hits)[:limit_per_pair],
                }
            )
    return rows


def namespace_checks() -> list[tuple[str, str, str]]:
    """命名空间卫生 + 跨库交集体检(C-3)。返回 ``(level, name, detail)``。

    - ``namespaces.disk``:磁盘上有库但未登记进注册表(如 ``demo``)→ WARN。
      测试命名空间不是正式语料,不能与正式类目平起平坐,必须显式可见。
    - ``db.cross_overlap``:两个**注册类目**持有同一篇文章 → FAIL(违反类目隔离);
      只涉及未注册命名空间的交集 → WARN(历史遗留,须清理但不阻断采集)。
    """
    results: list[tuple[str, str, str]] = []
    registered = store.registered_namespaces()
    on_disk = store.namespaces_on_disk()
    unregistered = sorted(set(on_disk) - registered)
    missing_dbs = sorted(registered - set(on_disk))
    _identities, scan_errors = _namespace_identities()
    if scan_errors:
        detail = "; ".join(f"{name}: {message}" for name, message in sorted(scan_errors.items()))
        results.append(("WARN", "db.cross_scan", f"命名空间库读失败,交集判定不完整: {detail}"))
    results.append(
        (
            "PASS" if registered else "FAIL",
            "namespaces.registered",
            f"registered={sorted(registered)}",
        )
    )
    if unregistered:
        results.append(
            (
                "WARN",
                "namespaces.disk",
                f"磁盘上有库但未注册: {unregistered};注册类目缺库: {missing_dbs or '无'}",
            )
        )
    else:
        detail = "all on-disk namespaces are registered"
        if missing_dbs:
            detail = f"{detail}; registered namespaces without articles.db: {missing_dbs}"
        results.append(("PASS", "namespaces.disk", detail))

    rows = namespace_overlap_rows()
    hard = [row for row in rows if row["registered_pair"]]
    soft = [row for row in rows if not row["registered_pair"]]
    if hard:
        summary = "; ".join(
            f"{row['left']}<->{row['right']} url={row['url_hits']} hash={row['hash_hits']}" for row in hard
        )
        results.append(("FAIL", "db.cross_overlap", f"正式类目之间存在同一篇文章: {summary}"))
    elif soft:
        summary = "; ".join(
            f"{row['left']}<->{row['right']} url={row['url_hits']} hash={row['hash_hits']}" for row in soft
        )
        results.append(("WARN", "db.cross_overlap", f"仅未注册命名空间与正式类目重叠: {summary}"))
    else:
        results.append(("PASS", "db.cross_overlap", f"namespaces={on_disk} 两两无交集"))
    return results


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

    # 4b. 命名空间卫生与跨库交集(C-3:类目物理隔离必须被对账,否则复制是静默的)
    try:
        results.extend(namespace_checks())
    except Exception as exc:  # noqa: BLE001 - 卫生检查异常不得让整份体检崩掉
        check("namespaces.checks", False, f"{type(exc).__name__}: {exc}")

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
        actual_model = data.get("model")
        expected_model = config.get("strata", {}).get("model")
        check(
            "strata.health",
            True,
            f"served_model={actual_model} context={data.get('max_context')} loaded={data.get('loaded')}",
        )
        if expected_model and expected_model != actual_model:
            # Strata 由用户管理,漂移不是故障;但报告若引用必须用 served 值,不得用配置值
            results.append(
                (
                    "WARN",
                    "strata.model_drift",
                    f"config expects '{expected_model}' but service serves '{actual_model}'"
                    " — 引用模型名时一律以 served 为准",
                )
            )
    else:
        results.append(("WARN", "strata.health", f"not reachable: {strata['error']}"))

    # 输出
    for level, name, detail in results:
        print(f"[{level}] {name}: {detail}")
    failures = [item for item in results if item[0] == "FAIL"]
    print(f"\nsummary: {len(results) - len(failures)} ok, {len(failures)} failed")
    return 0 if not failures else 1
