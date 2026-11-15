"""环境体检:目录、配置、数据库、磁盘预算、Strata 只读状态。"""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Any

from . import config as cfg
from . import db, housekeep
from .crawler import stackcheck, store


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


def _completeness_one(name: str) -> tuple[str, str]:
    """单个命名空间的正文完整性口径(只读)。返回 ``(level, detail)``。

    口径与写入侧完全一致(``cleaner.completeness_of_length`` + ``store.USABLE_LEN_SQL``):

    - ``full`` 但可用正文 < 阈值 → 谎报全文(下游按“全文语料”筛选会被摘要污染)→ WARN;
    - ``excerpt``/``short`` 但可用正文 ≥ 阈值 → 低估(明明有全文却当摘要)→ WARN;
    - ``excerpt`` 但有 1 ~ 阈值之间的零星字符 → 容忍(Python 与 SQL 的空白口径差几个字符,
      由 ``store.relabel_completeness`` 整改时统一,不因此报警);
    - 取值不在词表内 → FAIL:这个词已经没有可信含义,必须先修数据再让下游使用。
    """
    path = store.articles_db_path(name)
    threshold = store.COMPLETENESS_THRESHOLD
    values = store.COMPLETENESS_VALUES
    placeholders = ",".join("?" * len(values))
    con = store.read_only_connect(path)
    try:
        counts = {str(row[0]): int(row[1]) for row in con.execute(
            "SELECT completeness, COUNT(*) AS n FROM articles GROUP BY completeness"
        )}
        stats = con.execute(
            "SELECT"
            f"  SUM(CASE WHEN completeness = ? AND {store.USABLE_LEN_SQL} < ? THEN 1 ELSE 0 END),"
            f"  SUM(CASE WHEN completeness IN ('excerpt', 'short') AND {store.USABLE_LEN_SQL} >= ? THEN 1 ELSE 0 END),"
            f"  SUM(CASE WHEN completeness NOT IN ({placeholders}) THEN 1 ELSE 0 END)"
            " FROM articles",
            ("full", threshold, threshold, *values),
        ).fetchone()
        overclaimed, underclaimed, unknown = (int(value or 0) for value in stats)
        unknown_values: list[str] = []
        if unknown:
            unknown_values = sorted(
                str(row[0])
                for row in con.execute(
                    f"SELECT DISTINCT completeness FROM articles WHERE completeness NOT IN ({placeholders})",
                    values,
                )
            )
    finally:
        con.close()

    detail = store.distribution_text(counts)
    problems: list[str] = []
    if overclaimed:
        problems.append(f"谎报全文={overclaimed}(标 full 但可用正文 <{threshold} 字)")
    if underclaimed:
        problems.append(f"低估正文={underclaimed}(标 excerpt/short 但可用正文 ≥{threshold} 字)")
    if unknown:
        problems.append(f"词表外取值={unknown}: {unknown_values}")
    if unknown:
        return (
            "FAIL",
            f"{detail}; {'; '.join(problems)}; completeness 已失去可信含义,下游不得按它筛选,先整改再使用",
        )
    if problems:
        return (
            "WARN",
            f"{detail}; {'; '.join(problems)}; "
            f"整改:`ttxt relabel-completeness --category {name} --out <报告> ` 先干跑,确认后再 --apply",
        )
    return ("PASS", f"{detail}; 标签与真实正文长度一致(阈值 {threshold})")


def completeness_checks() -> list[tuple[str, str, str]]:
    """逐命名空间的正文完整性体检(C-1)。返回 ``(level, name, detail)``。

    历史缺陷实测:6 个库 257 行**全部**标 ``full``,其中 124 行可用正文不足 400 字
    (普查 ``07_tmp/c1_20261115/census_c1.json``)。DDL 默认值也是 ``'full'``,
    任何绕开 ``insert_items`` 的写入都会谎报,所以这里必须天天喊,而不是改一次数据就完事。
    """
    results: list[tuple[str, str, str]] = []
    for name in store.namespaces_on_disk():
        try:
            level, detail = _completeness_one(name)
        except Exception as exc:  # noqa: BLE001 - 单个库读失败不得毁掉整份体检
            level, detail = "WARN", f"读失败,无法判定 completeness:{type(exc).__name__}: {exc}"
        results.append((level, f"db.completeness.{name}", detail))
    try:
        results.append(_cleaned_coverage_row())
    except Exception as exc:  # noqa: BLE001 - 覆盖率统计失败不得毁掉整段完整性体检
        results.append(("WARN", "db.cleaned_coverage", f"读失败,无法统计清洗覆盖率:{type(exc).__name__}: {exc}"))
    return results


def _cleaned_coverage_row() -> tuple[str, str, str]:
    """清洗覆盖率(C-4)。返回 ``(level, name, detail)``。

    实测根因:P15 恢复真实采集后普查 6 个命名空间 328 行,只有 43 行有 ``cleaned_content``
    (13%,``07_tmp/p15_crawler_verify_20261115/pre_clean_proof.json``)。清洗本身不慢
    (全量 328 行实测 4.3 秒),慢的是**从来没人跑**——采集入口不调用管线。
    覆盖率低于 100% 必须喊出来:派生字段落后于采集,下游写作取材会取不到清洗文本,
    但从文章行数上完全看不出来。
    """
    lagging: list[str] = []
    unmerged: list[str] = []
    total_rows = total_cleaned = 0
    for name in store.namespaces_on_disk():
        con = store.read_only_connect(store.articles_db_path(name))
        try:
            columns = {str(row[1]) for row in con.execute("PRAGMA table_info(articles)")}
            if "cleaned_content" not in columns:
                # 基线 DDL 里没有清洗列,只有清洗管线跑过才由 migrate 补上;
                # 也就是说这个库从来没被清洗过 —— 比"覆盖率低"更严重,要单独点名。
                unmerged.append(name)
                continue
            rows, cleaned = con.execute(
                "SELECT COUNT(*), SUM(CASE WHEN cleaned_content IS NOT NULL THEN 1 ELSE 0 END) FROM articles"
            ).fetchone()
        finally:
            con.close()
        rows, cleaned = int(rows or 0), int(cleaned or 0)
        total_rows += rows
        total_cleaned += cleaned
        if rows and cleaned < rows:
            lagging.append(f"{name} {cleaned}/{rows}")
    if unmerged:
        return (
            "WARN",
            "db.cleaned_coverage",
            f"完全没有清洗列(清洗管线从未跑过):{', '.join(unmerged)};"
            f"补跑:`ttxt clean --category {unmerged[0]}`(采集入口已内置管线,出现这种库要查是不是绕过入口)",
        )
    if not total_rows:
        return ("WARN", "db.cleaned_coverage", "库里没有文章,清洗覆盖率无从谈起")
    if lagging:
        return (
            "WARN",
            "db.cleaned_coverage",
            f"{total_cleaned}/{total_rows} 行有清洗文本;未覆盖:{', '.join(lagging)};"
            "补跑:`ttxt clean --category <类目>`(采集入口已内置管线,出现落后要查是不是绕过入口)",
        )
    return ("PASS", "db.cleaned_coverage", f"{total_cleaned}/{total_rows} 行全部有清洗文本")


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

    # 4c. 正文完整性口径(C-1:completeness 谎报会让摘要冒充全文进入语料与写作取材)
    try:
        results.extend(completeness_checks())
    except Exception as exc:  # noqa: BLE001 - 口径体检异常不得让整份体检崩掉
        check("db.completeness", False, f"{type(exc).__name__}: {exc}")

    # 4d. 采集栈自检(P15:第三方抓取库的参数词表一旦漂移,故障会在长时间采集中途
    #     才出现,并且伪装成"网络失败";这里离线把实参与库声明的词表对一遍)
    try:
        results.extend(stackcheck.doctor_checks())
    except Exception as exc:  # noqa: BLE001 - 采集栈体检异常不得让整份体检崩掉
        check("crawler.stack", False, f"{type(exc).__name__}: {exc}")

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
