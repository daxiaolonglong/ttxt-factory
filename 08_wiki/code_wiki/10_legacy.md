# 10 · 旧资产迁移 ttxt/legacy/（3 个文件）

旧资产库（`资产库/01_语料库/factory.db`，约 53,678 行历史语料+真实计数）接入新体系的**唯一入口**：只读访问 + 分批迁移。

## 10.1 factory.py — 旧库只读访问层（约 145 行）

三硬口径（docstring）：资产库一律 `mode=ro`（规则 33/43）；每连接 `PRAGMA temp_store=MEMORY`（只读库排序落盘坑）；`read_count=0` 是缺失不是 0，`collect_count` 语义未证实**永不改名**（规则 41）。

- `LEGACY_METRIC_MAP`（L26）：只映射语义证实三列——`read_count→reads`、`digg_count→likes`、`comment_count→comments`；`UNPROVEN_LEGACY_COLUMNS`（L28，collect_count/hot_score/hot_level/tier_v2/is_elite/is_viral 只作证据）。
- `open_read_only(path)`（L50，URI `?mode=ro`，写操作当场报错）；`article_count`（L60）；`iter_articles(con, limit)`（L64，流式产出不整表进内存）。
- `to_item(record, min_body_chars)`（L75）：复用采集侧 `clean_article/normalize_text`，拒绝原因可计数：`no_title/no_body/body_too_short/bad_url`。
- `metrics_row(record, zero_as_missing)`（L119）：0 按缺失（NULL）、全空返回 None（不生成空指标行）。

## 10.2 migrate.py — 迁移主流程（约 495 行）

写入侧**复用现成路径**：文章走 `crawler.store.insert_items`（URL+content_hash 双去重 + 跨类目守卫）、指标走 `analysis.metrics_import.write_metric_rows`（白名单、缺失≠0）。

- `MigrationPolicy`（L44）：`source_id`（默认 legacy_factory_search_ssr）/ **`category_map` 默认空表**——映射只能写在 `00_control/legacy_migration.json`，代码不许猜类目；`min_body_chars=200`、`zero_as_missing_fields`、`max_rows_per_category`、`legacy_db`。
- `load_policy(path)`（L62）：文件不存在返回空映射策略（=什么都不迁）；slug 非法（`store.SLUG_RE`）当场 ValueError（规则 29）。
- **`run(policy, *, apply=False, batch_size=500, limit=None, body_cap=8000, backup_dir=None, receipt_path=None, ...) -> dict`**（L189）：
  - **dry-run（默认）**：只读数不写一个字节；`target_path()`（L213）"只解析、不创建"命名空间；预演统计 `would_insert / would_collide_same_namespace / would_collide_within_migration`（`_IdentityIndex` L115 只读预演专用）。
  - **apply=True**：分批导入；**备份-before-write**——每类目每批首次写入前 `backup_category_db()`（L483，sqlite3 在线备份 API，WAL 下一致；备份在补 schema **之前**，拿"迁移开始前"那份，文件名 `{category}-before-{stamp}.db`）；每批后 `reconcile {before, after, delta}` 计数对账（L370）；`_novel_metric_rows`（L138）过滤"同来源且数值一字不差"的指标行（重跑不追加假快照），孤儿指标单独计数不静默丢弃；category_map 为空直接记 errors 拒迁（L259-262）。
  - apply 后当场 `rag.rebuild_index`（FTS 立即可见，L444-449）；simhash 聚类不跑（O(n²)，回执如实写明 P26 待办）。
  - **旧库完整性断言**：迁移前后 factory.db sha256 不等 ⇒ `RuntimeError`（L455-459，规则 33）。
  - 回执可写 `receipt_path` JSON（默认 `09_logs/legacy_migrate/`）；全程流式内存有界。
- `LEGACY_BODY_CAP=8000`（L39）：旧采集截断上限，回执必须如实报。

## 10.3 使用

```powershell
ttxt legacy-migrate                          # 只读预演（默认）
ttxt legacy-migrate --apply                  # 需先在 00_control/legacy_migration.json 配好 category_map
ttxt legacy-migrate --policy 00_control/legacy_migration.json --batch-size 500 --limit 1000
```

对应测试：`10_tests/test_p37_legacy_migration.py`、`test_p37_read_only_temp_store.py`。
