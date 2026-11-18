# 03 · 核心模块（ttxt 包顶层）

## 3.1 config.py — 路径与配置底座

仅标准库（json/datetime/pathlib）。**config.json 路径硬编码，无环境变量覆盖机制**。

- 常量（L10–69）：`PROJECT_ROOT = Path("D:/ttxt")`（硬编码根）；`CONTROL_DIR`、`CONFIG_PATH`、`REGISTRY_PATH`、`CATEGORIES_DIR`、`CRAWLER_DIR`…`TEMP_DIR` 共 12 个编号目录常量；`SCRATCH_DIR=07_tmp`、`ASSET_DIR=资产库`（P23 登记为"计量桶"：只统计告警、不自动删）；`VENV_DIR`、`PIP_CACHE_DIR`。
- `REQUIRED_DIRS`：init 必建的 29 目录清单（含 `00_control/model_gate`、`02_crawler/{spiders,extractors,cleaners,schedulers,checkpoints,.browser}`）。
- `CATEGORY_SUBDIRS = ("", "rag", "keywords", "metrics")`：每类目下的隔离子目录。
- `PIPELINE_DEFAULTS`（L83）：`delta_enabled=True`、`fts_delta_full_rebuild_at_changed_rows=2000`、`fts_delta_ops_budget=20000`、`cluster_bucket_report_over=500`。
- 函数：
  - `load_json(path)` / `load_config()`（L72-78）
  - `pipeline_settings()`（L91）：config.pipeline 段按词表合并默认，读失败回落默认——增量化不是可用性依赖。
  - `load_registry()`（L109）：读 category_registry.json。
  - `resolve_path(value)`（L113）：相对路径按 PROJECT_ROOT 解析。
  - `category_db_path(category)`（L120）：`01_categories/<slug>/articles.db`。
  - `ensure_dirs()`（L125）：建 REQUIRED_DIRS + 注册类目子目录，返回新建列表。
  - `paths_within_project()`（L143）：config.paths 中解析到项目根之外的项（应为空，doctor 检查用）。
  - `now_iso()` / `now_iso_ms()`（L155/159）：本地时区 ISO 时间戳。

## 3.2 db.py — system.db 访问层

- `SCHEMA_VERSION = 1`；`WAL_SIZE_LIMIT_BYTES = 64MiB`（L19，注释引 2026-11-17 实测 social/articles.db-wal 826MB 教训）。
- `SYSTEM_SCHEMA`（L21-91）7 表 + 2 索引：`schema_meta`、`categories`（slug UNIQUE）、`sources`、`crawl_jobs`（job_type/category/keyword/source/status/attempts/max_attempts=3/owner/lease_expires_at/meta_json）、`model_jobs`、`system_log`、`app_state`；索引 `idx_crawl_jobs_status`、`idx_model_jobs_status`。表结构详见 [11_data_layer.md](11_data_layer.md)。
- `system_db_path()`（L94）。
- `connect(path)`（L98）：timeout=30s、row_factory=Row、PRAGMA `journal_mode=WAL` / `journal_size_limit=64MB` / `foreign_keys=ON` / `busy_timeout=30000` / `temp_store=MEMORY`；并调用 `ensure_crawl_job_columns`。
- `ensure_crawl_job_columns(con)`（L114）：P12 幂等迁移——凡存在 crawl_jobs 表即补 `owner`、`lease_expires_at` 列（绝不重建/丢数据）；放在 connect 内保证任何入口不会撞 `no such column`。
- `init_system_db(path=None)`（L137）：幂等建表、写 schema_version、按 registry UPSERT 种入 categories。

## 3.3 logging.py — 结构化 JSONL 日志

- `log_path()`（L26）：`09_logs/ttxt-YYYYMMDD.jsonl`（按日一文件）。
- `_max_mb()`（L15）：缓存读取 `config.disk.log_max_mb`（默认 10MB）。
- `_rotate_if_needed`（L30）：超限改名 `<name>.<HHMMSS>.bak`。
- `log_event(level, module, event, message="", **meta)`（L37）：一行 JSON，字段 `ts/level/module/event/message[/meta]`。**kwargs 全部装进 `meta` 子字典**——doctor.insert_judgement_rows 读日志依赖这一层级。
- `Logger`（L60）+ `get_logger(module)`（L74）：`info/warn/error(event, message, **meta)`。

## 3.4 agents.py — 六角色文件

- `ROLE_RE = ^[a-z][a-z0-9_\-]{1,32}$`（角色名白名单，防路径注入）。
- `ROLE_DESCRIPTIONS`：`orchestrator / collector / data_engineer / viral_researcher / writer_reviewer / maintainer`。
- `list_roles()`（L23）：glob `06_agents/*.md`。
- `read_role(name)`（L30）：正则校验后读取，非法名 ValueError。
- `role_context(name)`（L39）：角色正文 + `07_rules/CORE_RULES.md` + 固定"类目隔离"声明 → 调度上下文。
- `export_skills(root=None)`（L48）：导出为 DSH 项目级技能 `.dsh/skills/ttxt-<name>/SKILL.md`（不写全局配置）。

## 3.5 doctor.py — 环境体检（约 30 检查项）

入口 `run_doctor() -> int`（L693）：顺序跑全部检查组，输出 `[PASS|WARN|FAIL] name: detail`；**任何 FAIL 返回码 1，WARN 不影响**；每个体检段都包 try/except（单段异常不得崩掉整份体检）。

| 检查组 | 位置 | 语义要点 |
|---|---|---|
| `project.root/dirs` | run_doctor:701 | 根==D:/ttxt；REQUIRED_DIRS 全存在否则 FAIL |
| `config.load` / `registry.categories` | :707 | config.version==1；至少 1 个 slug |
| `paths.within_project` | :719 | config.paths 全部在项目内 |
| `db.system` | :725 | `PRAGMA integrity_check`=="ok" 且 categories≥1 |
| `namespaces.registered/disk`、`db.cross_scan` | namespace_checks:178 | 磁盘有库未注册→WARN |
| `db.cross_overlap` | :139 | 两个**注册类目**持有同一篇文章→FAIL；仅涉未注册命名空间→WARN |
| `db.completeness.<ns>` | :297/:234 | 谎报 full / 低估 excerpt→WARN；词表外取值→FAIL |
| `db.cleaned_coverage` | :515 | 无 cleaned_content 列或覆盖率<100%→WARN |
| `db.fts_coverage` | :565 | articles_fts 行数 < articles 行数→WARN |
| `db.duplicate_rows` / `db.identity_suspect` | :390（用 crawler.dedupe.summarize） | 历史重复→FAIL；同 URL 多不同文章→WARN（不得删） |
| `db.url_identity` | :445 | 来源页地址被当文章地址：责任来源仍 enabled→FAIL，仅停用→WARN |
| `db.orphan_references` | :337（`REFERENCE_TABLES=("viral_labels","article_metrics","metric_snapshots","evidence_links")`） | 悬空 article_id→FAIL |
| `crawler.stack.*` | crawler.stackcheck.doctor_checks | 抓取库参数词表离线对账（P15 防版本漂移） |
| `crawler.insert_judgements` | :631/:622 | 近 24h 日志中身份塌陷等事件计数（identity_conflict/page_url_collapse/body_kept_existing/body_upgraded/no_item_link） |
| `disk.free/budget/root_budget/scratch` | :758-791（数据来自 housekeep.report） | 剩余空间/桶预算/根实测预算/scratch 限额，超限 FAIL（P38） |
| `db.wal_sidecars` | :794 | 超阈值 -wal→FAIL，给出 `housekeep --checkpoint-wals` 命令 |
| `disk.accounting` | :810 | 未进桶根目录 >0.25GB→WARN |
| `ops.supervisor*` / `ops.scheduler*` | supervisor_checks:28 / :79-111 | 无常驻托管→FAIL；心跳超 `heartbeat_fresh_s`→FAIL；jobs 含禁用 kind→FAIL；启用任务超 2×interval 未跑→FAIL |
| `venv.python` | :845 | `.venv/Scripts/python.exe` 存在 |
| `strata.health` / `strata.model_drift` | :608 | GET base_url+health_path；不可达→WARN（模型不要求在线）；served model≠配置→WARN |

## 3.6 housekeep.py — 磁盘预算与受控清理

- `MAJOR_PATHS`（L21）：磁盘计量桶字典（control/categories/…/venv/pip_cache/tools/git/secrets）。
- 默认限额（读 `config.disk`）：`min_free_gb=5`、`temp_max_gb=2`、`log_max_mb=10`、`log_keep=7`、`pip_cache_max_gb=1`、`browser_max_gb=3`、`db_max_mb={system:200, article:2048}`、`wal_max_mb=64`、`project_budget_gb=20`、`scratch_max_gb=2`。
- 函数：
  - `dir_size / free_space_gb / check_free_space`（L50-72）
  - `clean_temp(max_age_hours=24)`（L75）：清 99_temp 超 TTL 文件；超限按最旧优先删到限额内
  - `rotate_logs`（L127）：jsonl 超限改名归档；归档超 keep 删最旧
  - `_prune_dir`（L157）：按最旧优先删到限额（被占用跳过）
  - `clean_pip_cache / clean_browser_cache`（L193/202）
  - `db_size_warnings`（L211）/ `db_wal_warnings`（L250）：只告警不删数据
  - `checkpoint_db_wals(dry_run=False)`（L270）：对超阈值库执行 `PRAGMA wal_checkpoint(TRUNCATE)`——只合并不删数据；被占用记 `skipped_busy`
  - `report()`（L323）：各桶字节数 + `pytest-of-*` 统计 + 根目录逐子项核算（未进桶目录列前 10）、`within_budget`（桶口径）与 `within_root_budget`（根实测口径）双口径、`caps/caps_status`
  - `prune_pytest_dirs(keep=2)`（L412）：清项目根 `pytest-of-*`
  - `reclaim_scratch_runs(dry_run=True, keep_runs=1, live_grace_minutes=30)`（L514）：回收 `07_tmp/pytest_basetemp/pytest-<N>` 与 `07_tmp/pytest_trim` 老缓存；安全机制：`_inside_scratch`（只允许删 07_tmp 之下、符号链接解析后判定）、`_protected_scratch_name`（reclaim-*/pytest_trim 整目录删禁）、`_reclaim_path`（先改名 `.reclaiming` 再删，占用则改名回去）、apply 写回执台账 `07_tmp/reclaim-<stamp>/ledger.json`；P38 按字节不按份数（单轮 basetemp 实测 28GB）
- 编排入口 `run(...)`（L667）：先 `report()`；reclaim 默认 dry-run；`clean=True` 时依次 clean_temp → rotate_logs → clean_pip_cache → clean_browser_cache → prune_pytest_dirs。
