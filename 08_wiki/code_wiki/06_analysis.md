# 06 · 情报子包 ttxt/analysis/（9 个文件）

## 6.1 analyze.py — P4 分析编排（57 行）

- `_timed(stages, name, fn, *args)`（L16）：分段计时（T-17 基线要求，回执含 `stage_seconds`）。
- **`run_analysis(category, db_path=None, system_db=None, sources_path=None, evidence_dir=None, full=False) -> dict`**（L24）：固定顺序
  1. `store.migrate_articles_db`
  2. `keywords.seed_keywords` → `keywords.score_keywords` → `keywords.auto_feedback`（反哺回灌 crawl_jobs）
  3. `viral.label_category`
  4. `knowledge.build_assets`
  5. 索引：默认 `rag.sync_index`（增量），`full=True` 走 `rag.rebuild_index`（L51-54）

## 6.2 keywords.py — 关键词评分 + 反馈状态机

- 常量：`DEMO_SEEDS`（L13）；`VIRAL_WEIGHT=0.30`（L25，关键词分中实测爆款信号的混合权重）；`MIN_KEYWORD_MEASURED_HITS=3`（L26）；`LABEL_VALUE`（L27，label→分值映射）；`VIRAL_STATUS_APPLIED/INSUFFICIENT/NO_HITS`（L33-35）；`DEFAULT_FEEDBACK_COOLDOWN_HOURS=24.0`（L40）。
- `seed_keywords`（L60）：向 `keywords` 表幂等播种（registry 的 seed_keywords / demo 种子词）。
- `_measured_label_map`（L87）：**只取 `viral_labels.confidence_tier='measured'`** 的标签——未实测不参与加权。
- `score_keywords`（L120）：关键词×文章关联打分写 `keyword_performance`（INSERT L187，含 collected 计数、分数、爆款信号状态）。
- **`auto_feedback(category, db_path, system_db, sources_path, evidence_dir)`**（L240）：状态机——采集零覆盖的词 `status='gap'`（L271）→ 一批 gap 词回 `active`（L281）+ 24h 冷却门（`_cooldown_remaining_hours` L43）→ 调 `crawler.keyword_crawl.plan_and_enqueue` ⇒ 完成"指标缺口 → 关键词 → 再采集"闭环。

## 6.3 viral.py — 爆款标签与阈值标定（实测/未实测分层）

- 标签五值：`verified_viral / potential / normal / low_quality / unscored`（L16-20）。
- 置信层级四值：`measured / no_impressions / incomplete_data / missing_data`（L22-25）——**标签值与数据可信度分层，是本项目"诚实口径"的核心设计**。
- 标定门：`MIN_CALIBRATION_SAMPLE=30`（L28，样本不足不产出分位阈值）；`CALIBRATION_META_KEY="viral_calibration"`（存 pipeline_state_meta）；`DEFAULT_THRESHOLDS=(0.15, 0.08, 0.02)`（L30，未校准时明示为设计假定）。
- 打分口径：核心互动字段 `("reads","likes","comments")`（L36）；权重 `READ 0.40 / FAVORITE 0.25 / LIKE 0.20 / COMMENT 0.15`（L37-40）。
- `calibrate`（L56）：够 30 样本才写阈值元数据；`compute_features`（L110，标题特征 has_question/has_number 等，prediction 分桶依据）；`score_with_metrics`（L121，互动率归一——分母缺 impressions 时用派生口径 ⇒ tier 降级）；`label_category`（L167，全量重打：DELETE viral_labels 后逐篇重插）。

## 6.4 metrics_import.py — 外部指标导入（白名单收口，唯一写入口径）

- `load_authorized_sources()`（L42）：读 `config.metrics.authorized_sources`（4 个授权来源：toutiao_admin_export/manual_verified/toutiao_public_article/legacy_factory_search_ssr）+ `credibility_rank`（verified 5 … unverified 1）；**未列入白名单的行一律拒收（PermissionError）**。
- `_find_article`（L102）：按归一 URL **精确匹配**锚定文章，匹配不到整行拒收，绝不模糊挂接。
- **`write_metric_rows(...)`**（L118，核心写入器）：校验通过后 `INSERT INTO article_metrics`（L239）+ `INSERT INTO metric_snapshots`（L260）。线上回收（crawler/metrics_pages）与人工离线导入、旧库迁移（legacy/migrate）都经此单一入口。
- `import_metrics`（L293，文件级封装 json/csv）；`snapshot_current_metrics`（L313）；`relabel_after_import`（L347，导入后触发 viral.label_category 重标——数据→标签即时联动）。

## 6.5 prediction.py — B2 预测闭环

- `DEFAULT_MIN_SAMPLES=5`（L20）；`_bucket`（L27，question/number/plain 三分桶）；`_measured_baselines`（L35，仅取 viral_labels measured 层实测分）。
- `predict_articles`（L51）：给未实测文章登记基线预测（桶均值），写 `prediction_records(category, subject='article:<id>', predicted_score, basis_json, predicted_at)`；诚实性门：**无任何 measured 样本直接拒绝预测**；P24 修正——候选条件 `v.article_id IS NULL OR tier<>'measured' OR score IS NULL` 且"已有预测记录"下推 SQL 反连接（L76-93），保证逐轮推进不重挑同批。
- `settle_predictions`（L129）：outcome 只从 viral_labels measured 层回填（L148）。
- `prediction_report`（L158）：MAE/mean_bias；样本 < `config.prediction.min_samples`（默认 5）只报告不落库；达标才 `DELETE+INSERT knowledge_assets(kind='prediction_accuracy', confidence='measured')`（L190-195）。

## 6.6 rag.py — FTS5 中文检索与索引维护

- `INDEX_CHAR_LIMIT=8000`（L24）；`bigrams`（L27，复用 `crawler.simhash.tokenize`）。
- **`rebuild_index`**（L52）：整库重建用 **DROP TABLE + CREATE**（L74-75，非 DELETE——FTS5 墓碑收不回，实测 811MB 教训/规则 61），显式 `rowid=articles.id`（L77），手动 `BEGIN IMMEDIATE/COMMIT` 原子回滚；成功后盖章 `pipeline_state_meta.fts_rowid_aligned='1'`、`last_full_index_at`、`fts_delta_ops_since_full=0`。
- **`sync_index(category, db_path, changed_ids, removed_ids, force_full)`**（L94）：增量五前提——对齐章存在、两张表都在、pipeline_state 覆盖全部文章行、单轮变更 < `fts_delta_full_rebuild_at_changed_rows`(2000)、累计墓碑 < `fts_delta_ops_budget`(20000)、delta_enabled 且未 force_full——任一不满足即整库重建；changed/removed 未显式传入时由台账 vs articles 反连接推导（L133-152）；增量走单写事务先删后插（L186-200）。
- `search(category, query, limit=10)`（L221）：查询词 tokenize 去重取前 12，bm25 排序；短查询严格 AND，>3 token 才 OR 回退（L251-254）。

## 6.7 knowledge.py — 知识资产沉淀

- `build_assets(category)`（L12）：统计 articles 总数/平均长度/来源分布/簇/关键词及 `keyword_performance.collected=0` 计数；`DELETE knowledge_assets(kind IN ('corpus_stats','keyword_gap'))`（L26）后重写两条 measured 资产（L34/48），证据 JSON + support_count 可追溯。

## 6.8 source_identity.py — 来源身份与"第二家独立媒体"判定（纯函数，不写库）

- 身份枚举：`OWN / REPRINT / UNKNOWN / CONFLICT / AGGREGATOR`（L20-24）；资格集 ELIGIBLE=(OWN,REPRINT)。
- 词典：`AGENCY_CANON`（机构别名→规范 id）、`SOURCE_PUBLISHER`（注册来源→机构；toutiao_* 为聚合平台）、`_DATELINE_PATTERNS`（L53，电头正则：中新网/新华社/人民日报/央视等）、`_MARKER_RE`（L62，页头「来源:xxx」）。
- 阈值：`MIN_EVENT_SCORE=0.30`（同一事件判定）、`CANDIDATE_FLOOR=0.12`（低于不列出；中间带进被拒清单+理由）。
- `classify(marker, dateline, own_agency="chinanews")`（L121）：**冲突时电头优先、域名让路**，标 CONFLICT；`independent(a,b)`（L144，宁可判否）；`event_score`（L159，标题双字 gram 的 max(jaccard, containment×0.9)）；`find_pairs(items, identities)`（L176，同域名拒、身份不独立拒、相似度不足拒）；`inventory_row`（L209，落盘清单含判定依据 evidence）。
