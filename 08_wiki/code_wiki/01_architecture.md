# 01 · 整体架构

## 1. 分层视图

```
┌────────────────────────────────────────────────────────────────┐
│ 入口层   ttxt CLI (python -m ttxt)  |  Web 面板 (ttxt serve)    │
│          supervisor / scheduler 守护  |  scripts/*.ps1 巡检     │
├────────────────────────────────────────────────────────────────┤
│ 业务闭环层                                                     │
│   crawler(采集)  →  analysis(情报)  →  writing(写作+门禁)       │
│        ↑ 关键词反哺(keyword_crawl)      ↓                      │
│   metrics_pages(指标回流) ← model_gate ← 模型(Strata/DeepSeek) │
├────────────────────────────────────────────────────────────────┤
│ 治理层   doctor(体检) · housekeep(磁盘预算) · supervisor(托管)  │
│          scheduler(调度) · dedupe/url_safety/robots(守卫)       │
├────────────────────────────────────────────────────────────────┤
│ 数据层   system.db（控制面） + 每类目 articles.db（物理隔离）    │
│          JSON 配置/注册表 + JSONL 日志 + 证据文件               │
└────────────────────────────────────────────────────────────────┘
```

## 2. 主数据流（管线主链）

```
sources.json（12 个来源注册）
   │  scheduler: crawl_enqueue / dashboard: /api/tasks/crawl / CLI: crawl
   ▼
crawl_jobs 队列（system.db）──── worker.run_worker / runner.run_next 认领（lease 900s）
   ▼
fetcher.fetch_page（Scrapling StealthyFetcher HTTP-first；render=browser 时 patchright+Edge）
   │  前置：url_safety 逐跳 SSRF 校验 → robots.gate 放行闸门 → browser_lock 跨进程互斥
   ▼
extract / toutiao 适配器 → ExtractedItem（标题/正文/逐条链接/时间）
   ▼
store.insert_items（url_normalized + content_hash 双键去重 + 跨类目守卫 cross_blocked）
   ▼
01_categories/<slug>/articles.db（articles 表）
   ▼
crawler.pipeline.run_pipeline（清洗→时间归一→pipeline_state 台账→SimHash 聚类）
   ▼
analysis.analyze.run_analysis（seed→score→auto_feedback→viral→knowledge→FTS 索引）
   │        └ auto_feedback：gap 关键词 → keyword_crawl.plan_and_enqueue → 回到 crawl_jobs
   ▼
指标闭环：metrics_pages.collect_public_metrics（公开页回收）
   → analysis.metrics_import.write_metric_rows（授权白名单，唯一写入口径）
   → article_metrics + metric_snapshots → relabel_after_import → viral measured 层
   → analysis.prediction（predict/settle/report，≥5 实测样本才落知识资产）
   ▼
写作：writing.workflow.write_article（取材门禁 → 大纲 → 初稿 → claims 核验
       → 评审 → 修订≤2 → 独立评审 → 导出 04_writing/drafts/<类目>/）
```

## 3. 模块依赖图（import 关系）

核心原则：**config/db/logging 是无外部依赖的底座；子包之间尽量单向依赖，唯一受控反向依赖是 `crawler.metrics_pages → analysis.metrics_import`（指标写入收口），以及 `writing → analysis(crawler)` 的取材复用。**

```
                 ┌───────────────── 第三方 ─────────────────┐
                 │ scrapling(curl_cffi) · patchright/playwright │
                 │ browserforge · lxml · (Windows: msvcrt/ctypes) │
                 └───────▲───────────────────────────────────┘
                         │ 仅 crawler 子包使用
core: config ◄── db ◄── logging
   ▲          ▲
   │ 全部模块都依赖 config；db/logging 被广泛复用
   │
crawler ──► analysis（单向：analysis 读 crawler 的 store/simhash/cleaner）
crawler.metrics_pages ──► analysis.metrics_import（受控反向：指标唯一写入口）
analysis.keywords.auto_feedback ──► crawler.keyword_crawl（惰性 import，反哺入队）
writing ──► analysis.{rag,keywords,viral} + crawler.{cleaner,store} + model_gate.{client,gate}
dashboard ──► crawler.{store,sources(惰性),runner(惰性)} + analysis.analyze(惰性) + model_gate.{client,queue} + housekeep + scheduler(惰性)
doctor ──► crawler.{dedupe,stackcheck,store} + scheduler + supervisor + housekeep
supervisor ──► crawler.worker.stop_path（惰性）
scheduler ──► crawler.{runner,sources,store,fetcher,metrics_pages}(惰性) + analysis.{analyze,metrics_import}(惰性) + model_gate.queue(惰性)
legacy ──► crawler.{store,clean,cleaner,extract,timeparse} + analysis.{metrics_import,rag}
```

惰性 import（函数内 import）是刻意设计：打破循环依赖、缩短 CLI 启动路径（无第三方依赖的命令不触发 scrapling 导入）。

## 4. 关键设计决策

| # | 决策 | 落实位置 |
|---|---|---|
| 1 | **类目物理隔离**：每类目独立 articles.db 及 rag/keywords/metrics 子目录，禁止跨类目混合 | `category_registry.json`（isolation=physical）、`config.category_db_path`、`store.cross_namespace_hits` 写入守卫、`doctor` 的 `db.cross_overlap` 对账 |
| 2 | **模型调用只经 model_gate**：文件锁串行 + SQLite 持久队列，绝不启停模型 | `model_gate/gate.py`、`queue.py`；`writing.workflow.default_model_call` 唯一出口；scheduler/supervisor 禁 `model` 任务 |
| 3 | **写作/发布绝不被自动化触发**：scheduler `FORBIDDEN_KINDS`、supervisor `FORBIDDEN_CHILD_KINDS`、dashboard POST 守卫三层同立场 | `scheduler.py:26`、`supervisor.py:44-45`、`dashboard/server.py:_task_allowed` |
| 4 | **干跑默认、写库需 `--apply` 且删数据必须落账本可回滚** | CLI：relabel/dedupe/metrics fetch/legacy-migrate/crawl-plan；`dedupe.rollback`、`--out` 账本强制 |
| 5 | **访问控制永不绕过**：robots.txt 前置闸门（disallow 即一次请求不发）+ SSRF 逐跳校验 + 浏览器请求级守卫与 Service Worker 阻断 | `robots.gate`、`url_safety.walk`、`fetcher._install_request_guard` |
| 6 | **诚实指标口径**：缺失≠0（NULL）、来源未证实字段置 NULL、指标导入只认授权白名单+URL 精确锚定 | `metrics_import.write_metric_rows`、`metrics_pages.UNPROVEN_FIELDS`、`legacy.factory.metrics_row` |
| 7 | **写作门禁不烧模型**：取材不足直接 insufficient_materials（零模型调用）；独立评审 fail-closed | `writing.workflow._material_gate`、`review.api_key` 抛异常不返回空 |
| 8 | **磁盘有预算、清理有边界**：桶口径+根实测口径双预算；删除仅限 99_temp 与 07_tmp 受控对象，改名再删+回执台账 | `housekeep.report/reclaim_scratch_runs`、`doctor disk.*` 检查 |
| 9 | **FTS5 墓碑不可回收 ⇒ 增量有预算、超限整库重建（DROP+CREATE）** | `analysis/rag.py:sync_index/rebuild_index`、config.pipeline 阈值 |
| 10 | **单写者进程模型**：worker 单实例锁 + lease 认领；浏览器 profile 跨进程锁（OS 释放，不留死锁）；supervisor msvcrt 锁 | `crawler/worker.py`、`crawler/browser_lock.py`、`supervisor.acquire_lock` |

## 5. 控制面与数据面分离

- **控制面**（`00_control/`）：`system.db`（categories/sources/crawl_jobs/model_jobs/system_log/app_state）+ 各类锁与状态文件（`scheduler_state.json`、`supervisor.lock/.pid/_state.json/supervisor_stop`、`model_gate/gate.lock`）。
- **数据面**（`01_categories/`）：每类目一份 `articles.db`，全部业务表在其中；跨库对账只在 doctor/dedupe 的只读扫描中发生。
- **证据面**（`03_intelligence/`、`07_tmp/reclaim-*`、`09_logs/legacy_migrate/`）：一切"改数据"的动作留下可回放的文件证据。

## 6. 并发模型

- 常规写路径：**单写者**——worker 独占采集执行；supervisor 保证 schedule/worker 各只有一份（msvcrt 文件锁 + PID 探活）。
- 队列并发安全：`claim_next_crawl_job` / `model_queue.claim_next` 用 `BEGIN IMMEDIATE` + 条件 UPDATE 校验 rowcount，多进程也不会双认领。
- 浏览器互斥：crawl Worker 与 metrics_fetch 共用 `02_crawler/.browser/profile`，靠 `browser_lock`（msvcrt 区域锁，OS 自动释放）+ scheduler 的 `_wait_for_idle_queue` 让路策略。
- SQLite 全局口径：WAL、`busy_timeout=30000`、`journal_size_limit=64MB`（防 -wal 悬挂）、`temp_store=MEMORY`（防受限环境 temp 溢写）。
