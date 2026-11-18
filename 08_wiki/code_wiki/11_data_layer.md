# 11 · 数据层

> 完整列级定义见 [../db_schema.md](../db_schema.md)；本篇给出模型与约定。

## 11.1 两套 SQLite

### system.db（控制面，`00_control/system.db`，SCHEMA_VERSION=1）

| 表 | 职责 | 关键机制 |
|---|---|---|
| `schema_meta` | key-value 元数据 | schema_version |
| `categories` | 类目注册（slug UNIQUE/display_name/db_path/status） | init 时按 registry UPSERT 种入 |
| `sources` | 来源镜像（真相在 sources.json） | — |
| `crawl_jobs` | 采集任务队列 | job_type/category/keyword/source/status/attempts(max 3)/**owner/lease_expires_at**（P12 幂等补列）/meta_json；索引 idx_crawl_jobs_status |
| `model_jobs` | 模型任务持久队列 | payload_json/result_json/status FIFO；租约 900s 恢复 |
| `system_log` | 系统日志表（结构化日志主载体是 JSONL 文件） | — |
| `app_state` | key-value 应用状态 | — |

状态机（crawl_jobs）：`queued → running(claim+lease) → done / failed(重试<3 回 queued) / aborted(来源缺失/禁用/类目越界)`。

### articles.db（数据面，每类目一份 `01_categories/<slug>/articles.db`，SCHEMA_VERSION=`p12-v1`）

| 表 | 职责 | 写入方 |
|---|---|---|
| `articles` | 文章主表（url/url_normalized/content_hash/title/content/cleaned_content/text_length/completeness/clean_version/published_at/simhash/cluster_id/source_id/author…） | 仅 `store.insert_items`（含跨类目守卫） |
| `articles_fts` | FTS5 虚表（article_id UNINDEXED, title, body；rowid=articles.id） | `analysis.rag`（DROP+CREATE 重建 / 受预算增量） |
| `article_clusters` | SimHash 簇 | `crawler.clusters.cluster_category` |
| `pipeline_state` / `pipeline_state_meta` | 清洗增量台账（content_fp/simhash/text_len）与 kv（校准、fts 盖章、link_status、dispatch_state） | `crawler.pipeline` |
| `keywords` / `keyword_performance` | 关键词与评分（status: active/gap；反哺冷却） | `analysis.keywords` |
| `viral_labels` | 爆款标签 + confidence_tier（measured/no_impressions/incomplete_data/missing_data） | `analysis.viral` |
| `article_metrics` / `metric_snapshots` | 互动指标（缺失=NULL）与时间快照 | **仅 `analysis.metrics_import.write_metric_rows`** |
| `knowledge_assets` | 知识资产（corpus_stats/keyword_gap/prediction_accuracy，带证据 JSON 与 support_count） | `analysis.knowledge` / `analysis.prediction` |
| `prediction_records` | 预测-回填记录 | `analysis.prediction` |
| `writing_history` / `evaluation_history` / `evidence_links` | 写作版本账本（REJECTED/AUTO_PASSED/INDEPENDENT_REVIEW_PASSED）/评审记录/证据链 | `writing.workflow` |
| `articles_meta` | 类目库 kv | store |

## 11.2 去重与守卫键

- `articles.url_normalized`：`crawler.clean.normalize_url`（绝对化+剥跟踪参数）。
- `articles.content_hash`：`sha256(归一标题+归一正文)`。
- 跨类目守卫：insert 前扫其余全部命名空间（仅白名单列 `url_normalized/content_hash`，400 行分批 IN）；命中即 `cross_blocked` 跳过。doctor `db.cross_overlap` 事后对账：注册类目交集→FAIL。

## 11.3 文件与目录约定

| 路径 | 内容 | 生命周期 |
|---|---|---|
| `00_control/config.json` | 唯一运行配置（版本 1；strata/review/dashboard/paths/disk/categories/crawler/browser/model_gate/writing/metrics/prediction/pipeline/scheduler/supervisor 段） | 人工编辑 |
| `00_control/category_registry.json` | 5 类目注册（slug/display_name/status/seed_keywords）+ 隔离规则 | 人工编辑 |
| `00_control/legacy_migration.json` | 旧库→类目映射策略（缺省=不迁） | 人工编辑 |
| `00_control/scheduler_state.json`、`supervisor_state.json/.lock/.pid`、`supervisor_stop`、`model_gate/gate.lock` | 运行态/锁 | 程序维护 |
| `02_crawler/sources.json` | 12 来源注册表 | 人工编辑（含取证 notes） |
| `02_crawler/page_urls.json` | 页面 URL 台账（dedupe 用作"列表页不是文章身份"白名单） | 程序/人工 |
| `02_crawler/.browser/profile`（+ 之外 `profile.lock`） | 共享 Edge persistent profile | browser_lock 互斥 |
| `02_crawler/worker_stop` | Worker 优雅停标志 | `ttxt worker --stop` |
| `03_intelligence/metrics_public/`, `keyword_crawl/` | 每轮取证 JSON | 累积 |
| `04_writing/drafts/<slug>/` 与 `rejected/` | 成品/被拒稿 Markdown | 门禁落盘 |
| `09_logs/ttxt-YYYYMMDD.jsonl` | 结构化日志（ts/level/module/event/message/meta） | 轮转 keep 7 |
| `11_backups/<slug>-before-<stamp>.db` | 迁移前在线备份 | 备份保留 backup_keep=6 |
| `99_temp/` | 普通临时（TTL 24h/限额 2GB） | housekeep 清理 |
| `07_tmp/pytest_basetemp/pytest-<N>`, `pytest_trim/<slug>-*.db`, `reclaim-<stamp>/ledger.json` | 测试 scratch 与回收台账 | 只计量；reclaim 受控回收 |

## 11.4 SQLite 连接统一口径

`db.connect` / `store.read_only_connect` / `legacy.factory.open_read_only` 共同口径：WAL（写库）、`journal_size_limit=64MB`、`busy_timeout=30000`、`foreign_keys=ON`、`temp_store=MEMORY`（受限环境/只读库防 temp 溢写）、timeout=30s。-wal 边车超阈值由 doctor FAIL + `housekeep --checkpoint-wals`（TRUNCATE 合并）处理。
