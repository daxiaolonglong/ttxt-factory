# 数据库字典

## 1. system.db(P1 已建,路径 `00_control/system.db`)

| 表 | 作用 | 关键列 |
|---|---|---|
| schema_meta | 版本元数据 | key, value |
| categories | 类目注册(与 category_registry.json 同步) | slug, display_name, db_path, status, created_at |
| sources | 采集来源 | category, name, url, kind, enabled, robots_ok, rate_limit_s |
| crawl_jobs | 采集任务队列(持久化,断点续采依据) | job_type, category, keyword, source, status, attempts, max_attempts, last_error, meta_json |
| model_jobs | 模型任务串行队列 | category, job_type, payload_json, status, attempts, last_error, result_json |
| system_log | 系统事件日志(与 JSONL 双写预留) | ts, level, module, event, message, meta_json |
| app_state | 键值状态 | key, value_json, updated_at |

SQLite 设置为 WAL、foreign_keys=ON、busy_timeout=30s。

## 2. articles.db(P2 建 v1 → P3 v2 → P4 v3 → P6 v4;每类目一份,同构)

> 当前版本 `p8-v1`。P3 新增清洗/聚类列与 `article_clusters`;P4 新增分析表与 FTS5 索引;P6 新增写作/评审/证据链表;P8 补全预测对照表。迁移幂等。

| 表 | 作用 | 关键列 |
|---|---|---|
| articles | 文章主表 | id, category, source, source_item_id, title, content, cleaned_content, excerpt, author, published_at, url, url_normalized, fetched_at, content_hash, text_length, simhash, cluster_id, completeness, credibility, processor_version, cleaned_at, clean_version |
| article_clusters | 相似聚类摘要 | cluster_id, category, representative_id, member_count, created_at, updated_at |
| keywords | 关键词库 | id, keyword, category, origin, status, created_at(UNIQUE keyword+category) |
| keyword_performance | 关键词效果 | keyword_id, collected, quality_articles, avg_text_length, score, last_used_at, updated_at |
| viral_labels | 爆款标签 | article_id, label, confidence_tier, score, reason, features_json, labeled_at |
| article_metrics | 真实指标(仅授权来源) | article_id, reads, likes, favorites, comments, impressions, account_baseline, data_source, credibility, captured_at |
| metric_snapshots | 多时点指标 | article_id, captured_at, reads, likes, favorites, comments, impressions, data_source |
| knowledge_assets | 知识资产(可追溯) | category, kind, statement, evidence_json, support_count, confidence, updated_at |
| writing_history | 草稿版本 | draft_id, category, topic, version, content, model_id, created_at |
| evaluation_history | 评审记录 | draft_id, version, evaluator, criteria_json, score, issues_json, created_at |
| evidence_links | 事实证据链 | draft_id, claim, article_id, quote, overlap, credibility, created_at |
| prediction_records | 预测与真实结果对照 | category, subject, predicted_score, basis, predicted_at, outcome_score, outcome_at, notes |
| articles_fts | FTS5 双字词索引 | article_id, title, body |
| articles_meta | 库级元数据 | key, value |

- 去重:内容 sha256 唯一索引(主);URL 规范化索引(辅);相似度:64 位 simhash + 汉明距离 ≤3(长度比 ≤2.5 约束)。
- 检索:FTS5 双字词索引;AND 优先,短查询(≤3 双字词)严格,长查询 AND 无结果时 OR 回退。
- 缺曝光数据时爆款标签强制 `unscored + missing_data`,不得臆测。
- 证据链:声明 vs 来源文章双字词覆盖率 ≥0.2 记 supported,否则 weak;无来源记 no_source(启发式,不等同事实核查)。
- 采集任务状态存于 system.db.crawl_jobs;模型任务存于 system.db.model_jobs。
- 保留原始 `content`,清洗结果写入 `cleaned_content`,不覆盖原始数据。
| article_metrics | 真实指标(仅授权来源) | article_id, reads, likes, favorites, comments, impressions, account_baseline, data_source, captured_at |
| metric_snapshots | 同一文章多时点表现 | article_id, captured_at, reads, likes, favorites, comments, impressions |
| keywords | 关键词库 | keyword, category, origin, created_at, status |
| keyword_performance | 关键词效果评分 | keyword_id, collected, quality_articles, avg_score, last_used_at |
| article_clusters | 相似文章聚类 | cluster_id, article_id, simhash, member_count, representative_article_id |
| viral_labels | 爆款标签(四级 + 置信度) | article_id, label(verified_viral/potential/normal/low_quality/spam), confidence_tier, reason, labeled_at |
| prediction_records | 预测记录 | article_id/topic, predicted_score, basis, predicted_at, outcome_score, outcome_at |
| writing_history | 写作版本 | draft_id, category, topic, version, content, model_id, created_at |
| evaluation_history | 评审记录 | draft_id, evaluator, criteria_json, score, issues_json, created_at |
| evidence_links | 事实证据链 | claim_id, draft_id, article_id, quote, char_start, char_end, source_url, credibility |
| knowledge_assets | 规律/反规律知识资产 | id, category, kind, statement, evidence_json, support_count, confidence, updated_at |
| rag_index | FTS5 双字词索引(外部内容表) | content_rowid, title, body |

## 3. 隔离与备份

- 类目物理隔离:不同 `articles.db` 文件,禁止跨库 JOIN 或合并检索上下文。
- 备份:阶段前 zip 快照 + SQLite `.backup` 至 `11_backups`,保留 7 份。
- 连接参数:WAL、busy_timeout 30s、单写者原则。
