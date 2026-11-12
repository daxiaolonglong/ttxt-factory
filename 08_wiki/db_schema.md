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

## 2. articles.db(每类目一份,同构;当前 `p10-v1`)

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

- 去重:内容 sha256 唯一索引(主);URL 规范化索引(辅);相似度:64 位 simhash + 汉明距离 ≤3(长度比 ≤2.5)。
- 检索:FTS5 双字词;AND 优先,短查询(≤3 双字词)严格,长查询 AND 无结果时 OR 回退。
- 爆款标签:缺曝光 `unscored + missing_data`;互动字段任一缺失 `unscored + incomplete_data`(缺失 ≠ 0);全部有值才评分。
- 证据链:quote 为来源文章中与声明重叠度最高的句子(启发式,不等同事实核查)。
- 写作门禁:完整性(长度 ≥400、句末标点收尾、finish_reason≠length)+ score ≥0.6 + critical 为空才 `passed`;否则 `rejected`(仅导出到 rejected 目录)。
- 指标回流(P11):导入仅 `00_control/config.json → metrics.authorized_sources` 白名单来源;`--snapshot` 冻结当前指标进 `metric_snapshots` 并联动重打爆款标签。
- 预测闭环(P11):无 measured 基线拒绝预测(不凭空);`settle` 仅以 measured 标签回填 outcome;`report` 仅当已回填样本 ≥ `prediction.min_samples`(默认 5)才写 `knowledge_assets`,否则如实记录样本不足。
- 关键词反哺(P11):覆盖统计对全部关键词进行(含 gap);零覆盖 active → `gap` 并自动为缺口入队非 demo 来源(去重);gap 词重新有覆盖 → 恢复 active;每类目写 `03_intelligence/keyword_feedback/*.json` 证据。
- 保留原始 `content`,清洗结果写入 `cleaned_content`,不覆盖原始数据。

## 3. 隔离与备份

- 类目物理隔离:不同 `articles.db` 文件,禁止跨库 JOIN 或合并检索上下文。
- 演示来源(demo=True)只能写入 demo 命名空间;正式来源必须写入其声明类目。
- 备份:`scripts\backup.py <label>` → `11_backups`(代码 + 草稿 + 面板 + 角色/技能 + system.db + 各类目库),保留 7 份;恢复见 `operations.md`。
- 连接参数:WAL、busy_timeout 30s、单写者原则。
