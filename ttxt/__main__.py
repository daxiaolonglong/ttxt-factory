"""CLI 入口:python -m ttxt <命令>。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from . import __version__
from . import config as cfg
from . import db, doctor, housekeep
from .analysis import analyze as analyzer
from .analysis import rag as analyzer_rag
from .crawler import pipeline as crawler_pipeline
from .crawler import runner as crawler_runner
from .crawler import sources as crawler_sources
from .crawler import store as crawler_store
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
        con = db.connect(db.system_db_path())
        try:
            rows = con.execute(
                "SELECT id, source, category FROM crawl_jobs WHERE status='queued' ORDER BY id"
            ).fetchall()
        finally:
            con.close()
        for row in rows:
            try:
                summary = crawler_runner.crawl_source(row["source"], category=row["category"])
                report["rerun"].append(
                    {"job": row["id"], "source": row["source"], "ok": summary.get("ok")}
                )
            except Exception as exc:  # noqa: BLE001
                report["rerun"].append(
                    {"job": row["id"], "source": row["source"], "error": f"{type(exc).__name__}: {exc}"}
                )
    print(json.dumps(report, ensure_ascii=False, indent=2))
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
    recover.add_argument("--run", action="store_true", help="并重跑排队的采集任务")
    recover.set_defaults(func=cmd_recover)

    write = sub.add_parser("write", help="写作闭环(仅导出草稿,不发布)")
    write.add_argument("--category", required=True)
    write.add_argument("--topic", required=True)
    write.add_argument("--max-revisions", type=int, default=2)
    write.add_argument("--max-tokens", type=int, default=900)
    write.add_argument("--think", action="store_true", help="启用模型推理过程")
    write.set_defaults(func=cmd_write)

    sub.add_parser("selftest", help="运行 pytest 测试").set_defaults(func=cmd_selftest)

    serve = sub.add_parser("serve", help="启动本地 Web 面板(只读)")
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
