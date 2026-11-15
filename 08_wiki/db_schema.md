# 数据库字典

## 1. system.db(路径 `00_control/system.db`)

| 表 | 作用 | 关键列 |
|---|---|---|
| schema_meta | 版本元数据 | key, value |
| categories | 类目注册(与 category_registry.json 同步) | slug, display_name, db_path, status, created_at |
| sources | 采集来源 | category, name, url, kind, enabled, robots_ok, rate_limit_s |
| crawl_jobs | 采集任务队列(持久化;attempts 每次启动 +1;失败未达上限自动重排 queued;stale running 由 recover 处理) | job_type, category, keyword, source, status, attempts, max_attempts, last_error, meta_json, started_at, finished_at |
| model_jobs | 模型任务串行队列(毫秒时间戳;租约 900s;stale running 由 recover 重排) | category, job_type, payload_json, status, attempts, last_error, result_json, started_at, finished_at |
| system_log | 系统事件日志(预留) | ts, level, module, event, message, meta_json |
| app_state | 键值状态 | key, value_json, updated_at |

SQLite 设置为 WAL、foreign_keys=ON、busy_timeout=30s。

## 2. articles.db(每类目一份,同构;当前 `p12-v1`)

> 2026-11-15 体检 §9.4 实测校正:本节长期写着「当前 `p10-v1`」,而代码 `SCHEMA_VERSION = "p12-v1"`
> (`ttxt/crawler/store.py:21`)、6 个生产库的 `articles_meta.schema_version` 也全是 `p12-v1`。
> 文档落后代码三整代 ⇒ 按规则 40 改成实测值,并把两个版本号彻底分清:
>
> | 字段 | 写在哪 | 当前取值 | 语义 |
> |---|---|---|---|
> | `articles_meta.schema_version` | 每库一行(单值) | `p12-v1` | 该库的**表结构/迁移代次**,由 `migrate_articles_db` 写入 |
> | `articles.processor_version` | 每一行文章 | `p2-v1`(`store.py:20`) | 该行的**抽取/清洗管线代次**,用来识别老管线产出的行 |
>
> 正文升级(`store.py:587-610`)改写 `content/excerpt/content_hash/completeness/processor_version`
> 并保留 `id / url_normalized / fetched_at / title`;`text_length`、`cleaned_content`、`cleaned_at`、
> `clean_version` **归清洗阶段所有**(`pipeline.py:29-31` 每次 `ttxt clean` 全量重算),所以升级后
> 这几列短暂描述的是旧正文,由采集入口当场跑的清洗管线补回(规则 31);`doctor.db.completeness`
> 与 `db.cleaned_coverage` 会核对标签与真实正文长度、覆盖率,不一致当场可见。
> 正文与哈希与长度同源于同一次抓取 ⇒ 升级不会让一行自相矛盾。

> P3 清洗/聚类列 + `article_clusters`;P4 分析表 + FTS5;P6 写作/评审/证据链表;P8 `prediction_records`;P10 `writing_history.status`(draft/passed/rejected)与写作 L4 门禁;P11 无 schema 变更,新增行为语义:指标导入白名单、`metric_snapshots` 写入通道、`prediction_records` 回填/知识资产门禁、`keywords.status` active↔gap 状态机。迁移幂等。

| 表 | 作用 | 关键列 |
|---|---|---|
| articles | 文章主表 | id, category, source, source_item_id, title, content, cleaned_content, excerpt, author, published_at, url, url_normalized, fetched_at, content_hash, text_length, simhash, cluster_id, completeness, credibility, processor_version, cleaned_at, clean_version |
| article_clusters | 相似聚类摘要 | cluster_id, category, representative_id, member_count, created_at, updated_at |
| keywords | 关键词库 | id, keyword, category, origin, status, created_at(UNIQUE keyword+category;status 状态机 active↔gap 由 `auto_feedback` 独占迁移) |
| keyword_performance | 关键词效果 | keyword_id, collected, quality_articles, avg_text_length, score, last_used_at, updated_at |
| viral_labels | 爆款标签 | article_id, label, confidence_tier, score, reason, features_json, labeled_at |
| article_metrics | 真实指标(仅 config 白名单来源可入库,未授权 `PermissionError`;允许 NULL,缺失≠0;负数/跨类目/全缺行拒绝) | article_id, reads, likes, favorites, comments, impressions, account_baseline, data_source, credibility, captured_at |
| metric_snapshots | 多时点指标 | article_id, captured_at, reads, likes, favorites, comments, impressions, data_source |
| knowledge_assets | 知识资产(可追溯) | category, kind, statement, evidence_json, support_count, confidence, updated_at |
| writing_history | 草稿版本(带状态) | draft_id, category, topic, version, content, model_id, status, created_at |
| evaluation_history | 评审记录(issues + critical) | draft_id, version, evaluator, criteria_json, score, issues_json, created_at |
| evidence_links | 事实证据链(quote 为来源原文句子,启发式) | draft_id, claim, article_id, quote, overlap, credibility, created_at |
| prediction_records | 预测与真实结果对照 | category, subject, predicted_score, basis, predicted_at, outcome_score, outcome_at, notes |
| articles_fts | FTS5 双字词索引 | article_id, title, body |
| articles_meta | 库级元数据 | key, value |

关键语义:

- 去重(C-2,2026-11-15 起):类目库内 **URL 即文章身份** —— `insert_items` 在 `BEGIN IMMEDIATE` 写事务内先查 `url_normalized`(走既有 `idx_articles_url_norm`,无 DDL)再插入,同库同 URL 不再产生第二行,重复如实记 `duplicates`;批内同 URL 也算重复;任一条异常整批回滚。`content_hash`(sha256(title+content))唯一索引仍是第二道闸;相似度:64 位 simhash + 汉明距离 ≤3(长度比 ≤2.5)。
- 例外:来源页/订阅地址(`page_url`,html 列表页把它的 URL 赋给每一条)不是文章身份,这类条目只按 `content_hash` 去重;调用 `store.insert_items` 必须传 `page_url=source.url`。
- 检索:FTS5 双字词;AND 优先,短查询(≤3 双字词)严格,长查询 AND 无结果时 OR 回退。
- 爆款标签:缺曝光 `unscored + missing_data`;互动字段任一缺失 `unscored + incomplete_data`(缺失 ≠ 0);全部有值才评分。
- 证据链:quote 为来源文章中与声明重叠度最高的句子(启发式,不等同事实核查)。
- 写作门禁:完整性(长度 ≥400、句末标点收尾、finish_reason≠length)+ score ≥0.6 + critical 为空才 `passed`;否则 `rejected`(仅导出到 rejected 目录)。
- 指标回流(P11):导入仅 `00_control/config.json → metrics.authorized_sources` 白名单来源;`--snapshot` 冻结当前指标进 `metric_snapshots` 并联动重打爆款标签。
- 预测闭环(P11):无 measured 基线拒绝预测(不凭空);`settle` 仅以 measured 标签回填 outcome;`report` 仅当已回填样本 ≥ `prediction.min_samples`(默认 5)才写 `knowledge_assets`,否则如实记录样本不足。
- 关键词反哺(P11):覆盖统计对全部关键词进行(含 gap);零覆盖 active → `gap` 并自动为缺口入队非 demo 来源(去重);gap 词重新有覆盖 → 恢复 active;每类目写 `03_intelligence/keyword_feedback/*.json` 证据。
- 正文完整性(C-1,2026-11-15 起):`completeness` 只有三档 —— `full`(可用正文 ≥400 字)/ `short`(1–399 字)/ `excerpt`(没有可用正文)。判定是**采集侧**事实,只看原始 `content`(去首尾空白后计数),不看标题、不看 `excerpt` 列;唯一定义在 `crawler/cleaner.completeness_of`(SQL 侧同口径表达式 `store.USABLE_LEN_SQL`),写入点 `store.insert_items` 逐行调用,清洗管线**绝不回头改写**它(清洗结果写在 `cleaned_content`/`text_length`,那是另一套口径)。DDL 默认值仍是 `DEFAULT 'full'`(本批不做 schema 迁移),因此兜底在体检:`ttxt doctor` 的 `db.completeness.<类目>` 会抓"标 full 但正文不足 400"(谎报)与"标 excerpt/short 但正文足够长"(低估),词表外的值 → FAIL。历史数据整改:`ttxt relabel-completeness --category all`(默认干跑)→ 出逐行 `id/from/to/usable_len` 报告 → `--apply` 真改(`BEGIN IMMEDIATE` + 逐行 CAS,行标签被他人动过就整笔回滚并报 `CompletenessConflict`)→ `--rollback <报告>` 精确复原。2026-11-15 已对生产 6 库回填 124 行(正式类目 81 + `demo` 43;`--category all` 只覆盖注册类目,`demo` 必须显式点名),回填后分布 full 133 / short 124 / excerpt 0,总行数 257 不变。
- 清洗覆盖率(C-4,2026-11-15 起):清洗列 `cleaned_content / text_length / simhash / cluster_id / cleaned_at / clean_version` **不在** `init_articles_db` 的基础建表语句里,由 `store.migrate_articles_db()` 追加,而只有 `pipeline.run_pipeline()` 会调它 —— 所以**从未清洗过的命名空间连这些列都不存在**(`PRAGMA table_info(articles)` 里没有 `cleaned_content`),不是"列在但值为 NULL"。现在清洗挂在采集入口上:`runner._execute_job` 插入之后当场跑管线,统计写进 `summary["pipeline"]` 与 `crawl_jobs.meta_json`(`cleaned / dates_normalized / clusters / near_duplicate_pairs / clean_version`),管线失败只记 `pipeline_error`(文章行保留、采集仍判 ok)。对账在体检:`ttxt doctor` 的 `db.cleaned_coverage` 按库统计"有清洗文本的行/总行数",完全没有清洗列、空库、覆盖率落后(逐个命名空间 `已清洗/行数`)都 WARN 并给补跑命令 `ttxt clean --category <类目>`;实测整改前 43/328(13%),手工全量 4.3 秒,整改后 328/328 PASS。`published_at` 会被管线归一化(采集侧列,属既定口径),清洗不得回写 `completeness`(见上一条)。
- 路径解析护栏(C-4 事故补法):`migrate_articles_db` 若从 `init_articles_db` 拿到非路径返回值(例如测试桩返回 `None`)必须当场 `TypeError` —— 否则 `sqlite3.connect(str(None))` 会在当前位置造出一个名为 `None` 的库文件。
- 保留原始 `content`,清洗结果写入 `cleaned_content`,不覆盖原始数据。

## 3. 隔离与备份

- 类目物理隔离:不同 `articles.db` 文件,禁止跨库 JOIN 或合并检索上下文。
- 命名空间互斥(C-3,2026-11-15):写入正式类目库之前,对其它命名空间做**只读**存在性查询(`url_normalized` + `content_hash`,分片 `IN`,绝不跨库 JOIN、绝不建库)。拦截权只属于 `00_control/category_registry.json` 里注册的类目;`demo` 这类未注册目录没有数据所有权 —— 它不能卡住正式类目的采集,但注册类目已持有的文章不得再写进 `demo`。被跨库拦下的条目如实计入 `duplicates`(绝不静默丢弃),归属写进任务 `meta_json.cross_db` 与 `crawl.cross_db_duplicate` 日志。
- 跨库对账:`ttxt doctor` 新增 `namespaces.registered` / `namespaces.disk`(有库未注册 → WARN)/ `db.cross_scan`(扫描失败 → WARN)/ `db.cross_overlap`(正式类目之间同一篇文章 → **FAIL**;只牵涉未注册命名空间 → WARN)。
- 已知残留风险:跨库检查与目标库写事务不在同一个原子域,两个类目同时采同一篇文章仍可能双双落库;兜底是 `doctor` 的 `db.cross_overlap` FAIL 对账,而不是假装跨文件原子。
- 演示来源(demo=True)只能写入 demo 命名空间;正式来源必须写入其声明类目。
- 备份:`scripts\backup.py <label>` → `11_backups`(代码 + 草稿 + 面板 + 角色/技能 + system.db + 各类目库),保留份数由 `00_control/config.json → disk.backup_keep` 决定(2026-11-15 起默认/配置为 **30**,`<=0` 表示全部保留);轮转每删一个 zip 都打 `backup.pruned` 日志,静默删除视为篡改历史。恢复见 `operations.md`。
- 连接参数:WAL、busy_timeout 30s、单写者原则;文章批量写入统一在显式 `BEGIN IMMEDIATE` 写事务内(检查与插入同锁,见 C-2)。
