"""CLI 入口:python -m ttxt <命令>。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from . import config as cfg
from . import db, doctor, housekeep, scheduler, supervisor
from .analysis import analyze as analyzer
from .analysis import metrics_import, prediction, viral
from .analysis import rag as analyzer_rag
from .crawler import dedupe as crawler_dedupe
from .crawler import keyword_crawl as crawler_keyword_crawl
from .crawler import metrics_pages as crawler_metrics_pages
from .crawler import pipeline as crawler_pipeline
from .crawler import runner as crawler_runner
from .crawler import sources as crawler_sources
from .crawler import store as crawler_store
from .crawler import worker as crawler_worker
from .dashboard import server as dashboard_server
from .legacy import migrate as legacy_migrate
from .model_gate import client as model_client
from .model_gate import gate as model_gate
from .model_gate import queue as model_queue
from .writing import workflow as writing_workflow
from . import agents as agents_module
from .logging import get_logger

LOG = get_logger("cli")


def cmd_init(_args: argparse.Namespace) -> int:
    created = cfg.ensure_dirs()
    path = db.init_system_db()
    LOG.info("cli.init", "project initialized", created_dirs=len(created), db=str(path))
    print(f"init ok: {path}")
    if created:
        print(f"created {len(created)} directories")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    return doctor.run_doctor()


def cmd_housekeep(args: argparse.Namespace) -> int:
    if getattr(args, "reclaim_apply", False) and not getattr(args, "reclaim_scratch", False):
        print("--reclaim-apply 必须与 --reclaim-scratch 同用(先 dry-run 看清清单再回收)")
        return 2
    result = housekeep.run(
        clean=args.clean,
        temp_max_age_hours=args.temp_max_age_hours,
        reclaim_scratch=getattr(args, "reclaim_scratch", False),
        reclaim_apply=getattr(args, "reclaim_apply", False),
        reclaim_keep_runs=getattr(args, "reclaim_keep_runs", 1),
        reclaim_keep_trim_generations=getattr(args, "reclaim_keep_trim", 1),
        checkpoint_wals=getattr(args, "checkpoint_wals", False),
        checkpoint_wals_dry_run=getattr(args, "checkpoint_wals_dry_run", False),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    db_path = db.system_db_path()
    payload: dict[str, object] = {"version": __version__, "db": str(db_path)}
    if not db_path.exists():
        payload["error"] = "system.db missing; run `ttxt init`"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    con = db.connect(db_path)
    try:
        payload["categories"] = [dict(row) for row in con.execute(
            "SELECT slug, display_name, status FROM categories ORDER BY slug"
        )]
        payload["crawl_jobs"] = {
            row["status"]: row["n"] for row in con.execute(
                "SELECT status, COUNT(*) AS n FROM crawl_jobs GROUP BY status"
            )
        }
        payload["model_jobs"] = {
            row["status"]: row["n"] for row in con.execute(
                "SELECT status, COUNT(*) AS n FROM model_jobs GROUP BY status"
            )
        }
    finally:
        con.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_selftest(_args: argparse.Namespace) -> int:
    python = cfg.VENV_DIR / "Scripts" / "python.exe"
    if not python.exists():
        python = sys.executable
    command = [str(python), "-m", "pytest", str(cfg.TESTS_DIR), "-q", "--color=no"]
    print("running: " + " ".join(command))
    return subprocess.run(command, cwd=str(cfg.PROJECT_ROOT)).returncode


def cmd_sources(_args: argparse.Namespace) -> int:
    for source in crawler_sources.load_sources():
        state = "enabled" if source.enabled else "disabled"
        print(f"{source.id}\t{state}\t{source.kind}\t{source.category}\t{source.url}")
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    summary = crawler_runner.crawl_source(
        args.source,
        category=args.category,
        limit=args.limit,
        max_pages=args.max_pages,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


def cmd_crawl_plan(args: argparse.Namespace) -> int:
    """P19:关键词 → 采集计划(默认干跑;``--apply`` 才入队,执行仍走 crawl-queue --run)。"""
    result = crawler_keyword_crawl.plan_and_enqueue(
        args.category,
        keywords=args.keyword or None,
        source_ids=args.source or None,
        keyword_limit=args.keyword_limit,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    if args.category == "all":
        categories = list(cfg.load_config().get("categories", []))
        if (cfg.CATEGORIES_DIR / "demo").exists():
            categories.append("demo")
    else:
        categories = [args.category]

    results: dict[str, object] = {}
    ok = True
    for category in categories:
        try:
            results[category] = crawler_pipeline.run_pipeline(category, full=getattr(args, "full", False))
        except Exception as exc:  # noqa: BLE001 - 类目级错误隔离
            results[category] = {"error": f"{type(exc).__name__}: {exc}"}
            ok = False
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_relabel_completeness(args: argparse.Namespace) -> int:
    """C-1:按真实正文长度如实改写 ``completeness``(默认干跑;``--apply`` 才写;``--rollback`` 回滚)。

    报告必须落文件:整改是一份逐行 ``from → to`` 的可回滚账本,不是“跑一遍就完”的修数脚本。
    ``--category all`` 只覆盖注册表里的正式类目;未注册命名空间(``demo``)要显式点名,
    与 C-3 的所有权口径一致。
    """
    if args.rollback:
        source = Path(args.rollback)
        if not source.is_file():
            print(f"回滚报告不存在:{source}")
            return 1
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"回滚报告读不出来:{exc}")
            return 1
        results: dict[str, object] = {}
        ok = True
        for category, report in (payload.get("results") or {}).items():
            changes = list(report.get("changes") or []) if isinstance(report, dict) else []
            if not changes:
                results[category] = {"category": category, "restored": 0}
                continue
            try:
                results[category] = crawler_store.restore_completeness(category, changes)
            except Exception as exc:  # noqa: BLE001 - 类目级错误隔离
                results[category] = {"error": f"{type(exc).__name__}: {exc}"}
                ok = False
        print(
            json.dumps(
                {"rollback_from": str(source), "restored_ok": ok, "results": results},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if ok else 1

    if args.category == "all":
        categories = sorted(crawler_store.registered_namespaces())
    else:
        categories = [args.category]

    results: dict[str, object] = {}
    ok = True
    for category in categories:
        try:
            results[category] = crawler_store.relabel_completeness(category, apply=args.apply)
        except Exception as exc:  # noqa: BLE001 - 类目级错误隔离
            results[category] = {"error": f"{type(exc).__name__}: {exc}"}
            ok = False
    payload: dict[str, object] = {
        "applied": bool(args.apply),
        "threshold": crawler_store.COMPLETENESS_THRESHOLD,
        "values": list(crawler_store.COMPLETENESS_VALUES),
        "results": results,
    }
    if args.out:
        target = Path(args.out)
        if not target.resolve().is_relative_to(cfg.PROJECT_ROOT):
            print(f"报告必须写在项目内:{target}")
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        payload["report_file"] = str(target)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_dedupe_history(args: argparse.Namespace) -> int:
    """C-7:清理历史遗留"同一篇文章存了两行"(默认干跑;``--apply`` 才写;``--rollback`` 按报告回滚)。

    报告必须落文件:删行是一份逐行账本(整行原文 + 被搬移/丢弃的引用),
    没有账本的删数据等于毁数据。跨命名空间的同一篇文章(``demo`` 里 P12 转正遗留的拷贝)
    默认一起处理,``--skip-cross`` 可以只看类目库内。
    """
    if args.rollback:
        source = Path(args.rollback)
        if not source.is_file():
            print(f"回滚报告不存在:{source}")
            return 1
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"回滚报告读不出来:{exc}")
            return 1
        try:
            result = crawler_dedupe.rollback(payload)
        except Exception as exc:  # noqa: BLE001 - 回滚失败必须整笔停住,不能部分恢复
            print(f"回滚失败:{type(exc).__name__}: {exc}")
            return 1
        print(json.dumps({"rollback_from": str(source), **result}, ensure_ascii=False, indent=2))
        return 0

    if args.category == "all":
        categories = sorted(crawler_store.registered_namespaces())
    else:
        categories = [args.category]

    results: dict[str, object] = {}
    ledger: list[dict[str, object]] = []
    identity_suspect: list[dict[str, object]] = []
    removed_total = 0
    planned_total = 0
    ok = True
    for category in categories:
        try:
            plan = crawler_dedupe.plan_category(category)
            planned_total += int(plan["removable"])
            for suspect in plan["identity_suspect"]:
                identity_suspect.append({"category": category, **suspect})
            if args.apply:
                applied = crawler_dedupe.apply_plan(plan)
                results[category] = {
                    key: applied[key]
                    for key in ("removed", "moved_references", "dropped_references", "fts_rows_removed", "db")
                }
                results[category]["duplicate_groups"] = plan["duplicate_groups"]
                ledger.extend(applied["ledger"])
                removed_total += int(applied["removed"])
            else:
                results[category] = {
                    "db": plan["db"],
                    "removed": 0,
                    "planned": int(plan["removable"]),
                    "duplicate_groups": plan["duplicate_groups"],
                }
        except Exception as exc:  # noqa: BLE001 - 类目级错误隔离
            results[category] = {"error": f"{type(exc).__name__}: {exc}"}
            ok = False

    cross: dict[str, object] = {"skipped": True}
    if not args.skip_cross:
        try:
            cross_plan = crawler_dedupe.plan_cross()
            planned_cross = sum(len(block["ids"]) for one in cross_plan["copies"] for block in one["removable"])
            planned_total += planned_cross
            if args.apply:
                cross_result = crawler_dedupe.apply_cross(cross_plan)
                cross = {
                    "removed": cross_result["removed"],
                    "applied_namespaces": cross_result["applied_namespaces"],
                    "dropped_references": cross_result["dropped_references"],
                    "ambiguous": cross_plan["ambiguous"],
                }
                ledger.extend(cross_result["ledger"])
                removed_total += int(cross_result["removed"])
            else:
                cross = {
                    "planned": planned_cross,
                    "copies": cross_plan["copies"],
                    "ambiguous": cross_plan["ambiguous"],
                }
        except Exception as exc:  # noqa: BLE001 - 跨库对账失败不得让类目结果消失
            cross = {"error": f"{type(exc).__name__}: {exc}"}
            ok = False

    payload: dict[str, object] = {
        "applied": bool(args.apply),
        "removed": removed_total,
        "planned": planned_total,
        "categories": categories,
        "results": results,
        "cross": cross,
        "identity_suspect": identity_suspect,
        "ledger": ledger,
        "note": "账本含整行原文与被搬移/丢弃的引用;--rollback <本报告> 可按原 id 逐行恢复。"
        "identity_suspect 里的行不是重复,不得删除。articles_fts 与 article_clusters 是派生索引,"
        "整改后跑 `ttxt clean --category <类目>` 与检索重建即可。",
    }
    if args.apply and not args.out:
        print("--apply 必须配 --out:删行的逐行账本必须落文件,否则没法回滚。")
        return 1
    if args.out:
        target = Path(args.out)
        if not target.resolve().is_relative_to(cfg.PROJECT_ROOT):
            print(f"报告必须写在项目内:{target}")
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        payload["report_file"] = str(target)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # 账本里含整行原文,全打到屏幕上会把人淹死;屏幕只给可核对的摘要,全文在文件里。
        summary = {key: value for key, value in payload.items() if key not in ("results", "cross", "ledger")}
        summary["results"] = {
            name: (
                {"error": block["error"]}
                if "error" in block
                else {
                    "db": block.get("db"),
                    "removed": block.get("removed"),
                    "planned": block.get("planned", 0),
                    "groups": len(block.get("duplicate_groups") or []),
                }
            )
            for name, block in results.items()
        }
        summary["cross"] = {
            key: (len(value) if isinstance(value, list) else value) for key, value in cross.items()
        }
        summary["ledger_rows"] = len(ledger)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_analyze(args: argparse.Namespace) -> int:
    if args.category == "all":
        categories = list(cfg.load_config().get("categories", []))
        if (cfg.CATEGORIES_DIR / "demo").exists():
            categories.append("demo")
    else:
        categories = [args.category]

    results: dict[str, object] = {}
    ok = True
    for category in categories:
        try:
            results[category] = analyzer.run_analysis(category, full=getattr(args, "full", False))
        except Exception as exc:  # noqa: BLE001 - 类目级错误隔离
            results[category] = {"error": f"{type(exc).__name__}: {exc}"}
            ok = False
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_search(args: argparse.Namespace) -> int:
    matches = analyzer_rag.search(args.category, args.query, limit=args.limit)
    print(
        json.dumps(
            {"category": args.category, "query": args.query, "matches": matches},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_model(args: argparse.Namespace) -> int:
    if args.ping:
        health = model_client.health()
        print(
            json.dumps(
                {"health": health, "models": model_client.models()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if health else 1
    if args.status:
        print(json.dumps(model_queue.stats(), ensure_ascii=False, indent=2))
        return 0
    if args.drain:
        print(json.dumps(model_gate.drain(), ensure_ascii=False, indent=2))
        return 0
    if args.ask:
        payload: dict = {"prompt": args.ask, "max_tokens": args.max_tokens}
        if args.think:
            payload["enable_thinking"] = True
        outcome = model_gate.submit_and_run(
            "chat",
            payload,
            category=args.category,
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0 if any(item["outcome"] == "done" for item in outcome["results"]) else 1
    print("usage: ttxt model --ping | --status | --drain | --ask <prompt> [--max-tokens N] [--category X]")
    return 1


def cmd_write(args: argparse.Namespace) -> int:
    # P12 批次4:消费者隔离 —— 正式写作只允许注册表中 active 的类目,
    # demo/未注册命名空间不得进入正式写作链(测试直接调用 workflow 不受限)。
    try:
        registry = cfg.load_registry()
        active = {
            item["slug"]
            for item in registry.get("categories", [])
            if item.get("status") == "active"
        }
    except Exception as exc:  # noqa: BLE001
        print(f"拒绝写作:无法读取类目注册表: {exc}", file=sys.stderr)
        return 1
    if args.category not in active:
        print(
            f"拒绝写作:类目 '{args.category}' 不在 category_registry 的 active 列表"
            f"({','.join(sorted(active))});demo/测试命名空间禁止消费正式素材",
            file=sys.stderr,
        )
        return 2
    result = writing_workflow.write_article(
        args.category,
        args.topic,
        max_revisions=args.max_revisions,
        max_tokens=args.max_tokens,
        enable_thinking=args.think,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # 取材门禁拦下 ≠ 写出来了:给非零码,调度与脚本才不会把"没烧模型"当成功
    return 3 if result.get("status") == "insufficient_materials" else 0


def cmd_agents(args: argparse.Namespace) -> int:
    if args.export_skills:
        created = agents_module.export_skills()
        print(json.dumps({"exported": created}, ensure_ascii=False, indent=2))
        return 0
    if args.show:
        print(agents_module.read_role(args.show))
        return 0
    if args.context:
        print(agents_module.role_context(args.context))
        return 0
    for role in agents_module.list_roles():
        print(f"{role['name']}\t{role['bytes']} bytes\t{role['path']}")
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    report: dict[str, object] = {
        "model_stale_requeued": model_queue.requeue_stale_running(),
        "crawl_stale_marked_failed": crawler_store.recover_stale_crawl_jobs(),
        "crawl_failed_requeued": crawler_store.requeue_failed_crawl_jobs(),
        "rerun": [],
    }
    if args.run:
        for summary in crawler_runner.run_queued(limit=100):
            report["rerun"].append(
                {
                    "job": summary.get("job_id"),
                    "source": summary.get("source"),
                    "ok": summary.get("ok"),
                    "error": summary.get("error"),
                }
            )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_crawl_queue(args: argparse.Namespace) -> int:
    if not args.run:
        jobs = crawler_store.list_queued_crawl_jobs(limit=args.limit, category=args.category)
        print(json.dumps({"queued": jobs, "note": "加 --run 才真正执行采集"}, ensure_ascii=False, indent=2))
        return 0
    results = crawler_runner.run_queued(limit=args.limit, category=args.category)
    print(json.dumps({"executed": len(results), "results": results}, ensure_ascii=False, indent=2))
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    if args.stop:
        path = crawler_worker.request_stop()
        print(f"已创建停止标志:{path}")
        return 0
    if args.clear_stop:
        path = crawler_worker.stop_path()
        if path.exists():
            path.unlink()
            print(f"已删除停止标志:{path}")
        else:
            print("停止标志不存在,无需清理")
        return 0
    try:
        report = crawler_worker.run_worker(
            owner=args.owner,
            max_jobs=args.max_jobs,
            max_runtime_s=args.max_runtime_s,
            idle_stop_s=args.idle_stop_s,
            category=args.category,
            lease_s=args.lease_s,
        )
    except crawler_worker.WorkerAlreadyRunning as exc:
        print(f"拒绝启动:{exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    if args.list_sources:
        print(json.dumps(metrics_import.load_authorized_sources(), ensure_ascii=False, indent=2))
        return 0
    if not args.category:
        print("metrics 需要 --category(或用 --list-sources 查看授权来源)")
        return 1
    if args.snapshot:
        report = metrics_import.snapshot_current_metrics(args.category)
        report["relabel"] = metrics_import.relabel_after_import(args.category)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.calibrate:
        # 标定统计的是"已测得"的标签行:先按当前指标把标签算出来,再标定;
        # 标定成功后阈值变了,必须再按新阈值重算一遍,否则库里留着旧口径的标签。
        # T-25:measured 与 measured_reads 是两个独立池,各自标定、各自落 meta key,
        # 绝不互相补样本或共用 p90/p60/p25。
        if not args.no_relabel:
            viral.label_category(args.category)
        report = viral.calibrate(args.category)
        report["reads"] = viral.calibrate_reads(args.category)
        if not args.no_relabel:
            report["relabel"] = viral.label_category(args.category)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] in ("calibrated", "insufficient_sample") else 1
    if args.fetch_public:
        conf = ((cfg.load_config().get("metrics", {}) or {}).get("public_pages", {}) or {})
        limit = args.limit if args.limit is not None else int(conf.get("limit_per_run", 20))
        sleep_s = args.sleep if args.sleep is not None else float(conf.get("sleep_s", 2.0))
        try:
            report = crawler_metrics_pages.collect_with_browser(
                args.category, limit=limit, apply=bool(args.apply), sleep_s=sleep_s,
                retry_gone=bool(args.retry_gone),
            )
        except PermissionError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
            return 2
        if args.apply and not args.no_relabel:
            report["relabel"] = metrics_import.relabel_after_import(args.category)
        if not args.apply:
            report["note"] = "dry-run:确认无误后加 --apply 才写库"
        if args.evidence:
            # 完整逐篇证据(结果台账 + 逐字段变更清单)落文件,stdout 只留摘要,
            # 这样生产写入的对账凭据不会因为终端截断而丢失。
            evidence_path = cfg.resolve_path(args.evidence)
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            report["evidence"] = str(evidence_path)
            evidence_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            summary = {key: value for key, value in report.items() if key not in ("pages", "outcomes", "changes")}
            summary["sample_pages"] = report.get("pages", [])[:10]
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        report["pages"] = report["pages"][:50]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if not args.import_file or not args.source:
        print("metrics 需要 --import FILE --source 已授权来源 id(或 --snapshot/--calibrate)")
        return 1
    try:
        report = metrics_import.import_metrics(args.category, args.import_file, args.source)
    except (PermissionError, FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    if not args.no_relabel:
        report["relabel"] = metrics_import.relabel_after_import(args.category)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["row_errors"] else 0


def cmd_legacy_migrate(args: argparse.Namespace) -> int:
    """旧资产迁移(账本 T-14):默认只读预演,加 ``--apply`` 才写类目库。"""
    try:
        policy = legacy_migrate.load_policy(cfg.resolve_path(args.policy) if args.policy else None)
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    stamp = cfg.now_iso().replace(":", "").replace("+", "")
    receipt_path = (
        cfg.resolve_path(args.receipt)
        if args.receipt
        else cfg.LOG_DIR / "legacy_migrate" / f"{stamp}-{'apply' if args.apply else 'dry_run'}.json"
    )
    backup_dir = cfg.resolve_path(args.backup_dir) if args.backup_dir else cfg.BACKUP_DIR
    try:
        report = legacy_migrate.run(
            policy,
            legacy_db=cfg.resolve_path(args.legacy_db) if args.legacy_db else None,
            apply=bool(args.apply),
            batch_size=args.batch_size,
            limit=args.limit,
            backup_dir=backup_dir,
            receipt_path=receipt_path,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    report["batches"] = report["batches"][:20]  # stdout 只留前 20 批,完整批次在回执文件里
    if not args.apply:
        report["note"] = "预演:未写一个字节。核对新增/碰撞/拒绝数后再加 --apply"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["errors"] and not report["index_errors"] else 1


def cmd_predict(args: argparse.Namespace) -> int:
    if args.action == "predict":
        report = prediction.predict_articles(args.category, limit=args.limit)
    elif args.action == "settle":
        report = prediction.settle_predictions(args.category)
    else:
        report = prediction.prediction_report(args.category)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    if args.once:
        report = scheduler.run_due()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.run_job:
        # 手动跑一个任务并照常记账:验证新接上的任务不必改间隔、也不必等时钟到期。
        # 状态不是 ok 就返回非 0,免得"失败"在命令行里看起来像成功。
        report = scheduler.run_specific(args.run_job)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ran"][0]["status"] == "ok" else 1
    if args.daemon:
        scheduler.daemon(interval_s=args.interval_sec)
        return 0
    print(json.dumps(scheduler.status_report(), ensure_ascii=False, indent=2))
    return 0


def cmd_supervisor(args: argparse.Namespace) -> int:
    """常驻托管入口:只看护 schedule --daemon 与 worker,写作/发布/模型不在此列。"""
    if args.stop:
        path = supervisor.request_stop()
        print(f"已创建停止标志:{path}")
        return 0
    if args.clear_stop:
        removed = supervisor.clear_stop()
        print("已删除停止标志,可重新 --ensure" if removed else "停止标志不存在,无需清理")
        return 0
    if args.ensure:
        report = supervisor.ensure()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.run:
        pol = supervisor.policy()
        try:
            report = supervisor.run_forever(
                scheduler_interval_s=args.scheduler_interval_sec
                if args.scheduler_interval_sec is not None else pol["scheduler_interval_s"],
                worker_owner=args.worker_owner or pol["worker_owner"],
                restart_delay_s=args.restart_delay_sec
                if args.restart_delay_sec is not None else pol["restart_delay_s"],
                max_restarts=args.max_restarts if args.max_restarts is not None else pol["max_restarts"],
                window_s=pol["window_s"],
                poll_s=args.poll_sec if args.poll_sec is not None else pol["poll_s"],
                heartbeat_s=pol["heartbeat_s"],
                run_for_s=args.run_for_sec,
            )
        except supervisor.SupervisorAlreadyRunning as exc:
            print(f"拒绝启动:{exc}")
            return 2
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    alive, info = supervisor.supervisor_running()
    info["running"] = alive
    info["stop_flag_present"] = supervisor.default_stop_path().exists()
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    dashboard_server.serve(args.host, args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ttxt", description="完美头条智能工厂 CLI")
    parser.add_argument("--version", action="version", version=f"ttxt {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化目录与 system.db").set_defaults(func=cmd_init)
    sub.add_parser("doctor", help="环境体检(含 Strata 只读检查)").set_defaults(func=cmd_doctor)

    house = sub.add_parser("housekeep", help="磁盘/限额报告与清理")
    house.add_argument("--clean", action="store_true", help="执行临时文件清理与日志轮转")
    house.add_argument("--temp-max-age-hours", type=float, default=24.0)
    house.add_argument("--reclaim-scratch", action="store_true", help="巡检 07_tmp 下的 pytest 运行目录(默认 dry-run)")
    house.add_argument("--reclaim-apply", action="store_true", help="实际回收运行目录(需与 --reclaim-scratch 同用)")
    house.add_argument("--reclaim-keep-runs", type=int, default=1, help="保留最近几轮运行目录,默认 1")
    house.add_argument("--reclaim-keep-trim", type=int, default=1, help="保留每类目几份裁剪副本缓存,默认 1(老份永远 cache-miss)")
    house.add_argument("--checkpoint-wals", action="store_true", help="把超阈值的 -wal 合并回主库并截断(只合并不删数据)")
    house.add_argument("--checkpoint-wals-dry-run", action="store_true", help="只列出将要检查点的 -wal,不执行")
    house.set_defaults(func=cmd_housekeep)

    sub.add_parser("status", help="查看数据库与任务状态").set_defaults(func=cmd_status)
    sub.add_parser("sources", help="列出采集来源").set_defaults(func=cmd_sources)

    crawl = sub.add_parser("crawl", help="执行采集任务")
    crawl.add_argument("--source", required=True, help="来源 id")
    crawl.add_argument("--category", default=None, help="类目 slug(默认取来源配置)")
    crawl.add_argument("--limit", type=int, default=None, help="最多入库条目数")
    crawl.add_argument("--max-pages", type=int, default=None, help="最多翻页数(html 来源)")
    crawl.set_defaults(func=cmd_crawl)

    plan = sub.add_parser(
        "crawl-plan",
        help="关键词驱动的采集计划(默认干跑;--apply 入队,执行仍走 crawl-queue --run)",
    )
    plan.add_argument("--category", required=True, help="类目 slug")
    plan.add_argument("--keyword", action="append", default=[], help="指定关键词(可重复;缺省取类目库里 gap/高分词)")
    plan.add_argument("--source", action="append", default=[], help="限定来源 id(可重复)")
    plan.add_argument("--keyword-limit", type=int, default=6, help="最多用几个关键词")
    plan.add_argument("--apply", action="store_true", help="把计划写入采集任务队列(默认只出计划)")
    plan.set_defaults(func=cmd_crawl_plan)

    clean = sub.add_parser("clean", help="清洗、时间归一化与相似聚类")
    clean.add_argument("--category", default="all", help="类目 slug 或 all")
    clean.add_argument(
        "--full",
        action="store_true",
        help="强制全量重跑(关闭指纹台账增量与 FTS 增量;增量对账不过时的规定回退通道)",
    )
    clean.set_defaults(func=cmd_clean)

    relabel = sub.add_parser(
        "relabel-completeness",
        help="按真实正文长度如实改写 articles.completeness(默认干跑,报告可回滚)",
    )
    relabel.add_argument("--category", default="all", help="类目 slug 或 all(=注册表里的正式类目)")
    relabel.add_argument("--apply", action="store_true", help="真正写入(默认只出计划,不改库)")
    relabel.add_argument("--out", default=None, help="把逐行 from→to 报告写到该 JSON 文件(项目内)")
    relabel.add_argument("--rollback", default=None, help="按 --out 产出的报告精确回滚")
    relabel.set_defaults(func=cmd_relabel_completeness)

    dedupe = sub.add_parser(
        "dedupe-history",
        help="清理历史遗留的同一篇文章多行(默认干跑,报告是逐行账本,可 --rollback)",
    )
    dedupe.add_argument("--category", default="all", help="类目 slug 或 all(=注册表里的正式类目)")
    dedupe.add_argument("--apply", action="store_true", help="真正删行(默认只出计划,不改库)")
    dedupe.add_argument("--out", default=None, help="把逐行账本写到该 JSON 文件(项目内)")
    dedupe.add_argument("--rollback", default=None, help="按 --out 产出的账本按原 id 逐行恢复")
    dedupe.add_argument("--skip-cross", action="store_true", help="只处理类目库内,跳过跨命名空间拷贝对账")
    dedupe.set_defaults(func=cmd_dedupe_history)

    analyze = sub.add_parser("analyze", help="关键词评分/爆款标签/知识资产/FTS5 索引")
    analyze.add_argument("--category", default="all", help="类目 slug 或 all")
    analyze.add_argument(
        "--full",
        action="store_true",
        help="检索索引强制整库重建(关闭 sync_index 增量;规定回退通道)",
    )
    analyze.set_defaults(func=cmd_analyze)

    search = sub.add_parser("search", help="类目内 RAG 检索")
    search.add_argument("--category", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(func=cmd_search)

    model = sub.add_parser("model", help="Strata 单模型串行闸门")
    model.add_argument("--ping", action="store_true", help="健康检查与模型列表")
    model.add_argument("--status", action="store_true", help="模型队列状态")
    model.add_argument("--drain", action="store_true", help="串行处理队列中所有任务")
    model.add_argument("--ask", default=None, help="提交并串行执行一次模型任务")
    model.add_argument("--max-tokens", type=int, default=256)
    model.add_argument("--category", default=None)
    model.add_argument("--think", action="store_true", help="启用模型推理过程(默认关闭)")
    model.set_defaults(func=cmd_model)

    recover = sub.add_parser("recover", help="崩溃恢复:重置 stale 任务、重排失败任务")
    recover.add_argument("--run", action="store_true", help="并消费排队的采集任务(串行)")
    recover.set_defaults(func=cmd_recover)

    queue = sub.add_parser("crawl-queue", help="查看/执行排队采集任务(面板与调度器只入队,由 Worker 或本入口消费)")
    queue.add_argument("--limit", type=int, default=5)
    queue.add_argument("--category", default=None)
    queue.add_argument("--run", action="store_true", help="真正执行(默认只列出)")
    queue.set_defaults(func=cmd_crawl_queue)

    worker = sub.add_parser("worker", help="受限采集 Worker:单实例消费 crawl 队列(不含模型/发布)")
    worker.add_argument("--owner", default="cli-worker", help="执行者标识,写入任务 owner 供审计")
    worker.add_argument("--max-jobs", type=int, default=1, help="执行 N 个任务后退出(0=不限)")
    worker.add_argument("--max-runtime-s", type=float, default=None, help="运行秒数预算,到点退出")
    worker.add_argument("--idle-stop-s", type=float, default=10.0, help="队列空闲该秒数后退出(0=不因空闲退出)")
    worker.add_argument("--category", default=None, help="只消费指定类目(默认不限)")
    worker.add_argument("--lease-s", type=float, default=900.0, help="任务租约秒数,超时视为进程中断")
    worker.add_argument("--stop", action="store_true", help="创建停止标志,让运行中的 Worker 优雅退出")
    worker.add_argument("--clear-stop", action="store_true", help="删除停止标志,允许再次启动")
    worker.set_defaults(func=cmd_worker)

    metrics = sub.add_parser("metrics", help="授权指标导入/快照(缺失≠0,未授权来源拒绝)")
    metrics.add_argument("--list-sources", action="store_true", help="列出授权指标来源")
    metrics.add_argument("--category", default=None)
    metrics.add_argument("--import", dest="import_file", default=None, help="CSV/JSON 指标文件")
    metrics.add_argument("--source", default=None, help="数据来源 id(须在白名单)")
    metrics.add_argument("--snapshot", action="store_true", help="为现有指标追加时序快照")
    metrics.add_argument(
        "--calibrate",
        action="store_true",
        help="从真实测得样本拟合爆款阈值(先按当前指标重算标签再标定,标定成功后按新阈值再重算;样本不足则拒绝改口径)",
    )
    metrics.add_argument("--fetch-public", action="store_true",
                         help="采集公开文章页指标(头条:阅读/点赞/评论/展现量,内嵌计数逐页核对文章 id;默认 dry-run)")
    metrics.add_argument("--limit", type=int, default=None, help="本轮最多采集多少篇(默认取 config)")
    metrics.add_argument("--sleep", type=float, default=None, help="两次抓取之间的间隔秒(默认取 config)")
    metrics.add_argument("--apply", action="store_true", help="真正写库(不加则只报告,不落库)")
    metrics.add_argument("--evidence", default=None,
                         help="把本轮完整逐篇证据(结果台账 + 逐字段前后值变更清单)写成 JSON 文件,便于事后对账与回滚")
    metrics.add_argument("--retry-gone", action="store_true",
                         help="忽略「已确认下架(404/410)」跳过台账,强制重新取证(事后核查用;"
                              "默认跳过这些文章,避免反复请求已删除的内容)")
    metrics.add_argument("--no-relabel", action="store_true", help="导入后不刷新爆款标签")
    metrics.set_defaults(func=cmd_metrics)

    legacy = sub.add_parser(
        "legacy-migrate",
        help="旧资产迁移(资产库/01_语料库/factory.db → 类目库):默认只读预演,加 --apply 才写库",
    )
    legacy.add_argument("--policy", default=None, help="迁移口径(默认 00_control/legacy_migration.json)")
    legacy.add_argument("--legacy-db", default=None, help="旧库路径(默认取口径里的 legacy_db)")
    legacy.add_argument("--apply", action="store_true", help="真正导入(不加=只预演,不写一个字节)")
    legacy.add_argument("--batch-size", type=int, default=500, help="每批写入行数(默认 500)")
    legacy.add_argument("--limit", type=int, default=None, help="只扫旧库前 N 行(冒烟用)")
    legacy.add_argument("--backup-dir", default=None, help="类目库备份目录(默认 11_backups)")
    legacy.add_argument("--receipt", default=None, help="回执 JSON 路径(默认 09_logs/legacy_migrate/)")
    legacy.set_defaults(func=cmd_legacy_migrate)

    predict = sub.add_parser("predict", help="预测闭环:基线预测/实测回填/准确率报告")
    predict.add_argument("action", choices=["predict", "settle", "report"])
    predict.add_argument("--category", required=True)
    predict.add_argument("--limit", type=int, default=None)
    predict.set_defaults(func=cmd_predict)

    schedule = sub.add_parser("schedule", help="本地调度器(只入队采集与本地分析,绝不写作/发布)")
    schedule.add_argument("--once", action="store_true", help="执行一轮到期任务后退出")
    schedule.add_argument("--daemon", action="store_true", help="常驻轮询")
    schedule.add_argument("--run-job", metavar="JOB_ID", help="手动执行指定任务并记账(不等到期;状态非 ok 返回码非 0)")
    schedule.add_argument("--interval-sec", type=int, default=300)
    schedule.add_argument("--status", action="store_true", help="查看计划(默认)")
    schedule.set_defaults(func=cmd_schedule)

    sup_parser = sub.add_parser(
        "supervisor",
        help="常驻托管:看护 schedule --daemon 与 worker(单实例;绝不托管写作/发布/模型)",
    )
    sup_parser.add_argument("--run", action="store_true", help="前台常驻看护(通常由 --ensure 无窗口拉起)")
    sup_parser.add_argument(
        "--ensure",
        action="store_true",
        help="幂等保障:没在跑就无窗口拉起一份,已在跑则什么都不做(计划任务可反复调)",
    )
    sup_parser.add_argument("--status", action="store_true", help="查看托管状态(默认)")
    sup_parser.add_argument("--stop", action="store_true", help="创建停止标志,让托管优雅收尾")
    sup_parser.add_argument("--clear-stop", action="store_true", help="删除停止标志")
    sup_parser.add_argument("--scheduler-interval-sec", type=int, default=None,
                            help="调度轮询间隔秒(默认取 config.supervisor)")
    sup_parser.add_argument("--worker-owner", default=None, help="Worker 执行者标识(默认取 config.supervisor)")
    sup_parser.add_argument("--restart-delay-sec", type=float, default=None,
                            help="子进程退出后重启前的等待秒,退避下限(默认取 config.supervisor)")
    sup_parser.add_argument("--max-restarts", type=int, default=None,
                            help="窗口内允许的重启次数,超过则放弃并记录原因(默认取 config.supervisor)")
    sup_parser.add_argument("--poll-sec", type=float, default=None,
                            help="轮询子进程秒数(默认取 config.supervisor)")
    sup_parser.add_argument("--run-for-sec", type=float, default=None,
                            help="只看护 N 秒后收尾退出(验收演练用,默认不限)")
    sup_parser.set_defaults(func=cmd_supervisor)

    write = sub.add_parser("write", help="写作闭环(仅导出草稿,不发布)")
    write.add_argument("--category", required=True)
    write.add_argument("--topic", required=True)
    write.add_argument("--max-revisions", type=int, default=2)
    write.add_argument("--max-tokens", type=int, default=900)
    write.add_argument("--think", action="store_true", help="启用模型推理过程")
    write.set_defaults(func=cmd_write)

    sub.add_parser("selftest", help="运行 pytest 测试").set_defaults(func=cmd_selftest)

    serve = sub.add_parser("serve", help="启动本地 Web 面板(只读 + 本机任务下发)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=cmd_serve)

    agents = sub.add_parser("agents", help="六角色文件")
    agents.add_argument("--show", default=None, help="打印角色文件")
    agents.add_argument("--context", default=None, help="装载角色 + 核心规则的调度上下文")
    agents.add_argument("--export-skills", action="store_true", help="导出为项目级 DSH 技能")
    agents.set_defaults(func=cmd_agents)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
