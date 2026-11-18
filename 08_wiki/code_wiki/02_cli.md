# 02 · CLI 入口（ttxt/__main__.py）

## 1. 结构

- 入口：`python -m ttxt`（或 `.venv` 激活后的 `ttxt`）。
- `build_parser()`（`__main__.py:710`）注册 21 个子命令；`main(argv)`（:926）走 `args.func(args)` 分发；`prog="ttxt"`、支持 `--version`。
- 每个子命令是一个 `cmd_*` 函数，自身只做参数校验与返回码，业务全部委托给对应模块。

## 2. 子命令全表

| 子命令 | 实现(行) | 委托 | 关键参数与语义 |
|---|---|---|---|
| `init` | cmd_init:37 | config.ensure_dirs + db.init_system_db | 建 29 个必需目录与 system.db |
| `doctor` | cmd_doctor:47 | doctor.run_doctor | 全量体检；任一 FAIL → 退出码 1 |
| `housekeep` | cmd_housekeep:51 | housekeep.run | `--clean`、`--temp-max-age-hours`、`--reclaim-scratch`（默认 dry-run）+`--reclaim-apply`（必须成对）、`--reclaim-keep-runs`、`--reclaim-keep-trim`、`--checkpoint-wals[-dry-run]` |
| `status` | cmd_status:69 | 直查 system.db | categories/crawl_jobs/model_jobs 按 status 计数 |
| `selftest` | cmd_selftest:97 | subprocess 起 pytest | 优先 `.venv\Scripts\python.exe`，跑 `10_tests` |
| `sources` | cmd_sources:106 | crawler.sources.load_sources | 列来源 id/state/kind/category/url |
| `crawl` | cmd_crawl:113 | crawler.runner.crawl_source | `--source`(必) `--category --limit --max-pages`；手工建 job 并同步执行 |
| `crawl-plan` | cmd_crawl_plan:124 | crawler.keyword_crawl.plan_and_enqueue | P19 关键词→采集计划；默认干跑，`--apply` 才入队；`--keyword`(可重复) `--source` `--keyword-limit`(默认6)；执行仍走 crawl-queue --run |
| `clean` | cmd_clean:137 | crawler.pipeline.run_pipeline | 清洗/时间归一/聚类；`--category all|slug`、`--full` 全量回退通道 |
| `relabel-completeness` | cmd_relabel_completeness:157 | store.relabel_completeness / restore_completeness | C-1 按真实正文长度改写 completeness；默认干跑；`--apply --out`(报告必须在项目内) `--rollback <报告>` |
| `dedupe-history` | cmd_dedupe_history:226 | crawler.dedupe.plan_category/apply_plan/plan_cross/apply_cross/rollback | C-7；`--apply` 必须配 `--out` 落逐行账本，否则拒执行；`--skip-cross`；identity_suspect 行不得删 |
| `analyze` | cmd_analyze:363 | analysis.analyze.run_analysis | 关键词评分/爆款标签/知识资产/FTS5；`--category all|slug --full` |
| `search` | cmd_search:383 | analysis.rag.search | 类目内检索 `--category --query --limit(10)` |
| `model` | cmd_model:395 | model_gate.{client,queue,gate} | `--ping --status --drain --ask <prompt> --max-tokens(256) --category --think` |
| `write` | cmd_write:427 | writing.workflow.write_article | 消费者隔离：类目必须在 registry active（demo/未注册拒写，返回码 2）；`--category --topic --max-revisions(2) --max-tokens(900) --think`；insufficient_materials 返回 3 |
| `agents` | cmd_agents:459 | agents 模块 | `--show --context --export-skills`，默认列出六角色 |
| `recover` | cmd_recover:475 | model_queue.requeue_stale_running + store.recover_stale_crawl_jobs/requeue_failed_crawl_jobs | `--run` 并消费队列(limit=100) |
| `crawl-queue` | cmd_crawl_queue:496 | store.list_queued_crawl_jobs / runner.run_queued | `--limit(5) --category --run` |
| `worker` | cmd_worker:506 | crawler.worker.run_worker/request_stop/stop_path | `--owner --max-jobs(1) --max-runtime-s --idle-stop-s(10) --category --lease-s(900) --stop --clear-stop`；WorkerAlreadyRunning→2 |
| `metrics` | cmd_metrics:535 | analysis.metrics_import / analysis.viral / crawler.metrics_pages | `--list-sources --category --import FILE --source(白名单) --snapshot --calibrate --fetch-public --limit --sleep --apply --evidence FILE --retry-gone --no-relabel`；未授权来源 PermissionError→2 |
| `legacy-migrate` | cmd_legacy_migrate:601 | legacy.migrate.run | 默认只读预演；`--policy --legacy-db --apply --batch-size(500) --limit --backup-dir --receipt`；stdout 只留前 20 批 |
| `predict` | cmd_predict:635 | analysis.prediction | 位置参数 `action ∈ {predict,settle,report}`；`--category --limit` |
| `schedule` | cmd_schedule:646 | scheduler | `--once --daemon --run-job JOB_ID（非 ok 退出码非 0）--interval-sec(300) --status`(默认) |
| `supervisor` | cmd_supervisor:664 | supervisor | `--run --ensure --status`(默认) `--stop --clear-stop --scheduler-interval-sec --worker-owner --restart-delay-sec --max-restarts --poll-sec --run-for-sec`（未指定取 policy） |
| `serve` | cmd_serve:705 | dashboard.server.serve | 默认 `127.0.0.1:8765` |

## 3. CLI 层治理口径（写代码时必须遵守）

1. **干跑默认**：relabel / dedupe / metrics fetch-public / legacy-migrate / crawl-plan / reclaim-scratch 全部默认只出计划，`--apply` 才写。
2. **删数据必须落账本**：`--apply` 不带 `--out`（项目内路径）直接拒绝（:327）；报告文件路径用 `is_relative_to(PROJECT_ROOT)` 校验（:216/:332）。
3. **消费者隔离**：`write` 只接受 category_registry 中 active 的 slug。
4. **类目级错误隔离**：`--category all` 遍历时单类目异常不带走其它类目（doctor/analyze/clean 同款写法）。
5. **返回码语义**：0 成功；1 doctor FAIL；2 隔离/并发拒绝（write 越界、WorkerAlreadyRunning、未授权指标来源）；3 素材不足。
