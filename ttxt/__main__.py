"""CLI 入口:python -m ttxt <命令>。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from . import __version__
from . import config as cfg
from . import db, doctor, housekeep, scheduler
from .analysis import analyze as analyzer
from .analysis import metrics_import, prediction
from .analysis import rag as analyzer_rag
from .crawler import pipeline as crawler_pipeline
from .crawler import runner as crawler_runner
from .crawler import sources as crawler_sources
from .crawler import store as crawler_store
from .crawler import worker as crawler_worker
from .dashboard import server as dashboard_server
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
    result = housekeep.run(clean=args.clean, temp_max_age_hours=args.temp_max_age_hours)
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
            results[category] = crawler_pipeline.run_pipeline(category)
        except Exception as exc:  # noqa: BLE001 - 类目级错误隔离
            results[category] = {"error": f"{type(exc).__name__}: {exc}"}
            ok = False
    print(json.dumps(results, ensure_ascii=False, indent=2))
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
            results[category] = analyzer.run_analysis(category)
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
    return 0


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
    if not args.import_file or not args.source:
        print("metrics 需要 --import FILE --source 已授权来源 id(或 --snapshot)")
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
    if args.daemon:
        scheduler.daemon(interval_s=args.interval_sec)
        return 0
    print(json.dumps(scheduler.status_report(), ensure_ascii=False, indent=2))
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
    house.set_defaults(func=cmd_housekeep)

    sub.add_parser("status", help="查看数据库与任务状态").set_defaults(func=cmd_status)
    sub.add_parser("sources", help="列出采集来源").set_defaults(func=cmd_sources)

    crawl = sub.add_parser("crawl", help="执行采集任务")
    crawl.add_argument("--source", required=True, help="来源 id")
    crawl.add_argument("--category", default=None, help="类目 slug(默认取来源配置)")
    crawl.add_argument("--limit", type=int, default=None, help="最多入库条目数")
    crawl.add_argument("--max-pages", type=int, default=None, help="最多翻页数(html 来源)")
    crawl.set_defaults(func=cmd_crawl)

    clean = sub.add_parser("clean", help="清洗、时间归一化与相似聚类")
    clean.add_argument("--category", default="all", help="类目 slug 或 all")
    clean.set_defaults(func=cmd_clean)

    analyze = sub.add_parser("analyze", help="关键词评分/爆款标签/知识资产/FTS5 索引")
    analyze.add_argument("--category", default="all", help="类目 slug 或 all")
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
    metrics.add_argument("--no-relabel", action="store_true", help="导入后不刷新爆款标签")
    metrics.set_defaults(func=cmd_metrics)

    predict = sub.add_parser("predict", help="预测闭环:基线预测/实测回填/准确率报告")
    predict.add_argument("action", choices=["predict", "settle", "report"])
    predict.add_argument("--category", required=True)
    predict.add_argument("--limit", type=int, default=None)
    predict.set_defaults(func=cmd_predict)

    schedule = sub.add_parser("schedule", help="本地调度器(只入队采集与本地分析,绝不写作/发布)")
    schedule.add_argument("--once", action="store_true", help="执行一轮到期任务后退出")
    schedule.add_argument("--daemon", action="store_true", help="常驻轮询")
    schedule.add_argument("--interval-sec", type=int, default=300)
    schedule.add_argument("--status", action="store_true", help="查看计划(默认)")
    schedule.set_defaults(func=cmd_schedule)

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
