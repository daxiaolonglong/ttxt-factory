# P13-A 旧→新资产映射设计（P13-asset-mapping.md）

生成时间：2026-11-13（P13-A 只读审计轮次内）
配套：`08_wiki/reports/P13-asset-audit.md`（测量证据）、`08_wiki/reports/P13-asset-audit.json`（机器可读汇总）
证据标签：**CONFIRMED**＝本轮只读探针直接测得；**SUPPORTED**＝人工声明/间接推断；**UNKNOWN**＝本轮未测得。

> 本文只做映射与迁移设计，**不导入任何数据、不修改任何业务代码、不动任何已有库**。
> 所有 DECISION 需人工批准后才执行（见 §11 未决依赖）。

---

## 1. 映射原则（先定规则，再列资产）

| 编号 | 原则 | 依据 |
|---|---|---|
| P-1 | 原始资产只读，永不改写；所有新数据只进 `D:\ttxt\01_categories\<cat>\articles.db` 与 `00_control\system.db` | 用户红线；本轮资产库零写入（CONFIRMED） |
| P-2 | 一个 `article_uid` 一条文章记录；重复来源只增加"来源副本记录"，不新增文章行 | gid 跨源冗余率 64.06%（CONFIRMED） |
| P-3 | 正文按"最长非空"择优，来源优先级：`corpus_tt/clean/info/scored` ＞ `corpus_archive` 分片 ＞ `factory.db.article_bodies`；等长时取 `body_time` 较新者 | `article_bodies` 硬截断 8,000 字符（CONFIRMED），JSON/归档最长 53,148/66,364 |
| P-4 | 机器预测（`tier_v2/hot_level/viral_level/is_elite/is_viral/keyword_score`）与真实表现（`read_count` 等）不得进入同一语义层 | 54 次模型运行 accuracy 全部低于多数类基线（CONFIRMED） |
| P-5 | 不做物理删除去重；用 `simhash + cluster_id + article_clusters` 标记近重复，保留可回退 | 新库已有这些列（CONFIRMED） |
| P-6 | 每条导入必须带 `source_asset_id`（路径@sha256 前 12 位）与 `import_batch_id` | 资产清单 13,378 行含 sha256（CONFIRMED） |
| P-7 | 导入一律先 `dry_run`，账本与统计被人工接受后才 `commit` | R-1 |

---

## 2. 资产级映射总表

处置码：**REUSE**（优先复用）/ **RECOMPUTE**（复用但需重算口径）/ **QUARANTINE**（隔离评估，不入生产）/ **EXCLUDE**（不迁移，只登记）

| 旧资产（相对 `D:\ttxt\资产库`） | 规模（CONFIRMED） | 处置 | 新目标 | 主键/去重规则 | 关联风险 |
|---|---|---|---|---|---|
| `01_语料库/corpus_tt.json` | 409,794,942 B；58,931 条；唯一 gid 58,931；正文非空 40,794；唯一正文 hash 40,342 | **REUSE** | `articles`（正文/标题/作者/url/published_at/fetched_at）＋ `metric_snapshots` | `article_uid=legacy_tt_g:<gid>`；文件内 0 重复 gid | R-3 R-4 R-10 |
| `01_语料库/corpus_clean.json` | 415,432,643 B；49,594 条；唯一 hash 49,063；正文<200 字符 9,080 | **REUSE**（补字段） | `articles.primary_type 候选→M-1`、`completeness` | 与 tt 的 gid 交集 49,584；clean 独有 10 条 gid 需单独入账 | R-3 R-4 |
| `01_语料库/corpus_info.json` | 198,727,562 B；28,511 条；正文<200 字符 8,801（30.9%） | **RECOMPUTE** | 只作 `completeness='summary_only'` 与 `primary_type` 证据；**不作取材池** | `info ⊂ clean`（28,511 全在 clean 内） | R-3 |
| `01_语料库/corpus_scored_v2_1.json` | 106,007,916 B；15,527 条；正文<200 字符 151 | **REUSE** | 机器评分字段 → `viral_labels`(需 `origin/model_version`) | `scored ⊂ clean`；与 info 交集 **0** | R-5 |
| `01_语料库/corpus_archive/*.json`（7,386 分片） | 1,290,843,352 B；记录 291,205；唯一 gid 291,088；唯一正文 hash 113,155；正文长度 **p50=0** | **REUSE（作 gid 清单＋正文补全）** | `articles`(补缺正文) ＋ 导入账本 | 分片内 117 个重复 gid 按 gid 合并；与 `archived_gids.jsonl`（291,109 行/291,088 唯一）对齐 | R-11 U-4 |
| `01_语料库/corpus_archive/archived_gids.jsonl` | 291,109 行；21 重复行 | **REUSE** | 账本/候选清单（不入 `articles`） | 唯一 gid | R-11 |
| `01_语料库/factory.db` → `articles` | 53,678 行；唯一 gid/url 53,678；`primary_type` 只有真人故事/赚钱案例；`publish_time` 2014-05-13→2026-11-06 | **REUSE（元数据）＋QUARANTINE（标签列）** | `articles` 元数据；`tier_v2/hot_level/is_elite/is_viral` → `viral_labels` | 与 `corpus_info` 交集 **0**；与 tt 交集 15,525 | R-5 R-10 |
| `01_语料库/factory.db` → `article_bodies` | 190,555 行；唯一 hash 188,116；正文 p99=p100=**8,000**；**孤正文 136,877** | **REUSE（正文补全）＋待决 M-7** | 补 `articles.content`；孤正文暂存 staging 表 | 最长优先（P-3）；等长取新 `body_time` | R-2 M-7 |
| `01_语料库/factory.db` → `keywords` | 28,171 行 / 17 列；`word` 唯一 | **RECOMPUTE** | `keywords(origin='legacy')` ＋ `keyword_performance` | `word` 去重；`state→status` 词表映射（§9） | R-5 |
| `01_语料库/factory.db` → `model_runs` | 54 行 | **QUARANTINE（登记）** | `prediction_records.basis` 文本引用 / 审计账本，不入生产 | version 唯一 | R-5 M-3 |
| `01_语料库/factory.db` → `tasks/events/profiles/health_last/health_history/pipeline_runs` | 453,579 / 15,189 / 6,577 / 6,577 / 97,697 / 3,095 行 | **EXCLUDE** | 仅登记为旧运行遥测 | — | — |
| `corpus_tt.json` 的 4 个 sidecar（`meta/round_state/write_health/task_cursor`） | 合计 1,209 B | **EXCLUDE→证据保留** | 只作为旧流水线状态证据（已入 `audit_articles.json`） | — | — |
| `02_关键词/kw_roi_history.jsonl` | 170,917,333 B、37 日快照 / 765,554 观测（资产库独有） | **RECOMPUTE**（§9 CONFIRMED：公式已复现，但 96.4% roi=0 且与真实表现无关 → 不进 `score`） | 新表 `keyword_observations`（M-11）；未批前先做文件级登记 | (keyword, ts) | §9 D11/D13 |
| `02_关键词/关键词/*` | 递归 57 文件 / 107,503,941 B（直接子项口径 40 文件 / 106,010,967 B）；`kw_typed_candidates.jsonl` 480,627 B 与备份同名不同内容 | **REUSE（清洗后）＋ RECOMPUTE 分值** | `keywords` 候选（并集 48,732，先清重复行与碎片）；`level/tier` 需重跑 | 清洗后 `word` 去重 | M-13、§9 D8/D15 |
| `02_关键词/选题/*` ≡ `05_V4.5数据资产分层/选题_含经验卡/*` | 递归 158 文件 / 59,617,353 B（直接子项口径 29 文件 / 57,707,687 B；逐文件同 SHA） | **只导一次** | `knowledge_assets` / `keywords` | 两个路径登记为同一 `source_asset_id` 别名 | §2 审计、M-6 |
| `03_RAG/rag/kb_tf.json` / `kb_index.json` / `kb_docs.json` | 102,056,310 / 22,377,798 / 17,318,899 B（**资产库独有**，备份是上一代）。实测：kb_docs 7,177 对象 / 7,176 唯一 gid（1 个畸形 id）；kb_index `total_docs=7,177`、df=idf=530,761、`built_at=2026-11-07 21:31:18`；kb_tf 7,177 段 / 8,556,773 term-tf 对 | **EXCLUDE 索引**（不搬）＋ kb_docs 元数据 **RECOMPUTE** | 新系统 FTS5（bigram）为准；旧 kb_* 只作对照 | **`kb_index`/`kb_tf` 内无 gid，与 kb_docs 仅位置对应（D-R6）**；`body_preview` 只有 447–500 字符，正文必须回语料 join | D-R6/R7、R-7 |
| `03_RAG/训练数据/*.jsonl`（7 文件） | 12,096,977 B；其中 3 份 SHA 完全相同（3,293,240 B / 各 507 行 / 并集仅 **395 gid**） | **QUARANTINE（去重后只算 1 份）** | `prediction_records` 训练集登记（不接生产） | sha 去重；内含 2026-08-22 冻结的平台计数，**不回填 `article_metrics`** | U-5、D-R10、M-16 |
| `03_RAG/rag/rag_vs_baseline_r1..r5/articles`（700 文件） | 多为小文件；r1/r2 的 `scores/gen_progress/score_errors/score_progress` 均 **0 字节** | **QUARANTINE** | 评审历史证据 | — | M-6 |
| `04_RAG反向评审/*`（25 文件） | 10,146,088 B；13 个 jsonl 合计 **19,394 行**（rag_judge_scores 5,416 / card_hit 3,361 / rag_judge_gid_map 3,101 / unsupported_gate 2,504…），最新 mtime 2026-11-07 21:06:53 | **QUARANTINE** | 反向评审证据 → 未来 `evaluation_history` ＋ L4 规则候选 | 判定为机器评审，与人工判定分离 | M-6、§10.7 |
| `01_语料库/experience_memory.jsonl`（＝ `05_V4.5数据资产分层/experience_memory.jsonl`，同 SHA，各 1,129,445 B） | **648 行 / CONFIRMED**（README「648 条经验记忆」一致，§10.9） | **REUSE（清洗后，只导一次）** | `knowledge_assets`（`kind` 待定） | 行指纹 | M-6 |
| `02_关键词/选题/experience_cards.jsonl` 5,498,484 B ＋ `.json` 3,774,604 B ≡ `05_V4.5数据资产分层/选题_含经验卡/` 同名两份（跨层 SHA 相同，D-R12 7.3 MB 冗余） | jsonl **2,443 行 / 1,597 唯一 gid**；json 声明 1,597 但数组实长 **1,586**（D-R1） | **只导一次＋强制清洗**（按 gid 取最完整版本） | `knowledge_assets` | 卡片 payload 指纹（143 行重复） | M-6、D-R1/R2/R12 |
| `…/card_embeddings_bge_m3.json` | 32,377,041 B × 2（同 SHA） | **EXCLUDE**（不导入） | 向量索引在新链路重建；不引入新模型（红线） | — | 约束 |
| `05_V4.5数据资产分层/models/elite_nb_current.json` | 48,057,512 B（备份同 SHA） | **QUARANTINE** | 隔离评估，只读引用；不加载、不接生产 | — | R-5 U-3 |
| `01_语料库/corpus_archive/runtime/*.lock` | 4,616 个 0 字节 `.lock`（全库 `.lock` 4,618 个） | **EXCLUDE** | 陈旧锁文件，只登记 | — | M-10 |
| `*.bak*` / `*.corrupt_*` / `*.encbak` / `*.before_r204` | `.bak_20261021_084607_force` 2,603,047 B、`.corrupt_20260920_123325` 623,160 B 等 | **EXCLUDE** | 只登记为历史事故证据 | — | — |
| `C:\_KEEP_BACKUP\cluster_models`（58 个快照） | 2,228,719,658 B（备份独有） | **QUARANTINE（只读）** | 隔离评估材料；不在 C 盘写任何文件 | — | R-12 M-4 |
| `C:\_KEEP_BACKUP\toutiao_ai_数据`（3,006 文件，含 `筛选/training_data_clean_filtered.json` 55,560,135 B） | 200,368,372 B（备份独有） | **QUARANTINE→待 M-4** | 训练集/筛选证据 | — | M-4 U-5 |
| `C:\_KEEP_BACKUP\factory.db` | 1,749,405,696 B | **EXCLUDE（重复）** | 与资产库副本内容等价（唯一差异是 `pipeline_runs` 一行 rowid 位置，内容相同） | — | CONFIRMED |

---

## 3. 字段级映射（列到列）

### 3.1 文章主表

| 旧列（来源） | 新列 | 转换规则 | 标签 |
|---|---|---|---|
| `gid` | `source_item_id` ＋（新增）`article_uid` | `legacy_tt_g:<gid>` | CONFIRMED 缺口 |
| `url` | `url` / `url_normalized` | 原样；缺 url 的 14 条 tt 记录 → `url=''` 且 `completeness` 降级 | CONFIRMED |
| `title` / `full_title` | `title` / `excerpt` | `title` 取 `title`；`full_title`（150 字符拼接）只能进 `excerpt` | CONFIRMED（tt `full_title` 覆盖 40,794） |
| `body` | `content` | P-3 择优；`len==8000` 标 `truncated_8k` | CONFIRMED |
| （清洗后正文） | `cleaned_content` | 迁移期留 NULL，由新 analyze 链路重生成（P1） | CONFIRMED |
| `body_time` | `fetched_at` | ISO 直接映射 | CONFIRMED |
| `publish_time`（epoch） | `published_at`（ISO） | 合法性校验：`> 2010-01-01`，否则置 NULL 并记 `skip_reasons_json` | CONFIRMED（库内最早 2014-05-13） |
| `author` | `author` | tt 唯一作者 30,673 | CONFIRMED |
| — | `text_length` | `len(content)` | CONFIRMED |
| — | `content_hash` | 新系统口径重算；审计归一化 hash 存 `import_batches.skip_reasons_json` 之外的账本扩展列 | CONFIRMED |
| — | `simhash` / `cluster_id` | 导入时计算（复用现有 simhash 能力，不新增算法） | 设计 |
| — | `processor_version` / `clean_version` | `legacy-import-v1` | 设计 |
| — | `completeness` | `full` / `summary_only` / `truncated_8k` / `no_body` | CONFIRMED 依据 §6/§8 |
| — | `credibility` | 一律 `unverified`（旧资产无人工核验） | CONFIRMED |

### 3.2 真实表现（只进快照表）

| 旧列 | 新列 | 规则 | 实测非零（CONFIRMED） |
|---|---|---|---|
| `read_count` | `metric_snapshots.reads` | `data_source='legacy_snapshot:<asset_sha12>'`、`captured_at=body_time` | tt 33,865 / clean 29,830 / info 18,257 / scored 8,614 / db 33,708 |
| `digg_count` | `likes` | 同上 | tt 18,351 |
| `collect_count` | `favorites` | 同上 | tt 16,902 |
| `comment_count` | `comments` | 同上 | tt 9,791 |
| — | `impressions` | 旧资产无此字段 → NULL（禁止推算） | — |
| — | `article_metrics` | **禁止写入**（当前值语义） | 现 0 行 |

### 3.3 机器预测（须先加来源列）

| 旧列 | 新列（`viral_labels`） | 规则 |
|---|---|---|
| `viral_level`（tt 41,468 条：普通 40,342/普通优质 969/潜力爆款 133/超级爆款 13/爆款 11） | `label`＋`reason` | 需新增 `origin='legacy_model'` ＋ `model_version`（notes recipe 原文哈希） |
| `hot_level`（db：低热 29,186/普通 14,746/热门 6,340/爆款 3,406） | `label` | 同上 |
| `tier_v2`（A/B/A+/过滤） | `confidence_tier` | 同上 |
| `is_elite=1`（6,128）、`is_viral=1`（37） | `score`/`features_json` | 只作特征证据 |
| `model_runs.notes` | `features_json.model_recipe` | 保留原字符串 |

前置：**`viral_labels` 现在没有 `origin/model_version` 列**（现列：`article_id,label,confidence_tier,score,reason,features_json,labeled_at`）→ 未加列前不得导入任何机器标签。

### 3.4 关键词（现库 DDL 与取值已 CONFIRMED，证据 `07_tmp/kw_ddl.py`、`07_tmp/kw_status.py`）

现库 DDL（`01_categories/<cat>/articles.db`，5 库一致）：

```sql
CREATE TABLE keywords (
  id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL, category TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'seed', status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL, UNIQUE(keyword, category));
CREATE TABLE keyword_performance (
  keyword_id INTEGER PRIMARY KEY, collected INTEGER NOT NULL DEFAULT 0,
  quality_articles INTEGER NOT NULL DEFAULT 0, avg_text_length REAL, score REAL,
  last_used_at TEXT, updated_at TEXT NOT NULL, FOREIGN KEY(keyword_id) REFERENCES keywords(id));
```

实测（CONFIRMED）：现库共 **17 词**（technology 4 / finance 4 / social 3 / history 3 / health 3），`origin` 全为 `seed`，`status` 只出现 **active 9 / gap 8**，`keyword_performance` 也是 17 行（与 `keywords` 1:1）。三条硬结论：

1. `status` **没有 CHECK 约束**（自由文本）→ 可写入新词表值而不需改表；但代码若按 `active` 过滤，未知值必须按"非活跃"安全处理。
2. `keyword_performance` **一词一行、无时间维度、无 `origin/model_version`** → 旧 ROI 历史的 **37 个日快照 / 765,554 条观测无处存放**，需新表（M-11）。
3. `score REAL` **无来源列** → legacy 机器分值只能靠导入账本标记来源（M-12），不得当作人工评分。

| 旧列 / 旧资产（`07_tmp/audit_keywords.json`） | 新列 / 处置 | 说明 |
|---|---|---|
| `word` | `keyword` | 文本清单先清洗：去重复行（story 1,911 / discovered 1,636 / blacklist 263）、去碎片 |
| — | `category`（人工映射，未决前 `unassigned`，见 M-1） | 旧词表无类目维度 |
| — | `origin='legacy'` | 与现库 17 个 `seed` 词并存，不覆盖（`UNIQUE(keyword,category)` 决定重复词只留一条） |
| `state`（SLEEP 697,268 / ACTIVE 421,749 / DEAD 100,959 / NEW 23,190 / WEAK 2,465 / TESTING 25 观测） | `status` 归一化草案：**ACTIVE→active；NEW/TESTING→testing；WEAK/SLEEP→dormant；DEAD→retired**；`gap` 保留给"知识缺口"种子词 | 新增值需批准（M-13）；原值逐条写导入账本，不改 `keywords` 表结构 |
| `synced_at` | `created_at` | 真实导入时间另存账本（B-AS-7） |
| `search_count` | `keyword_performance.collected` | 只取"最新一天"快照值（14,571 词），历史不进此表 |
| `quality_avg` | `avg_text_length` | 口径旁证，不参与评分 |
| `success_count / empty_count / consecutive_fail` | 复用为采集健康度（需 M-11 新表承载历史） | 属机器运行指标 |
| `keyword_score / hot_avg / elite_count / hot_count`、`roi`、`lc.score` | **不写入 `score`**，先隔离 | §9 已证 `roi` 96.4% 为 0、与真实表现无关；`lc.score` 公式 UNKNOWN；机器分值必须带来源（M-12） |
| `parent_keyword` | 新表无父子列 → 建议 `keywords.parent_keyword`（待批准） | 影响词族复用 |
| 关键词↔文章引用（`kw_referenced_gids.jsonl` 5,844 gid） | 进 `evidence_links`（`kind='keyword_hit'`，待 M-14 批准） | 覆盖率仅 1.66%（5,844 / 351,843） |
| 词来源清单（`union_of_all_text_lists` 48,732；其中 21,218 从未进 ROI 历史） | 记入导入账本的 `source_asset_id` | 旧"库"是互不相交的多个小池（base∩story 仅 259），无单一权威清单 |

### 3.5 知识资产与经验卡（§10 实测后定稿）

`knowledge_assets(id,category,kind,statement,evidence_json,support_count,confidence,updated_at)`。字段级映射（实测字段名来自 `07_tmp/audit_rag_experience.json`）：

| 旧字段（来源文件） | 新落点 | 判定依据 |
|---|---|---|
| `experience_cards.jsonl` 经验体（2,443 行 → 唯一 payload 2,300 / 唯一 gid 1,597） | `statement` ＋ `evidence_json.article_uids[]` | 追加日志（D-R2）：同 gid 846 行重复、143 行经验体重复，**必须按 gid 取最完整版本**才等于"卡数" |
| 卡片 `level`（A+ 227 / A 612 / B 768 / **null 836**）、`reality_score`（n=1,607，60.0–92.5，均 71.13）、`career_*`（非空 449） | **只进 `prediction_records`／`confidence` 旁证，不进 `statement`** | 全部机器生成（D-R11：与训练标签 8 类词表不可互换） |
| 卡片 `schema` v2 1,643 / null 800、66 行带 `error` | 清洗规则：null schema 与 `error` 行隔离待人工 | 两代格式混写（D-R2） |
| `experience_memory.jsonl` **648 行 / 648 唯一 id / 624 唯一 topic**，`type` = trend_analysis 226 / career_transition 206 / business_case 132 / tool_tutorial 72 / opinion 12 | `statement` 候选 ＋ `kind` 映射（**待人工定义枚举**，M-6） | README「648 条经验记忆」CONFIRMED 一致（§10.9） |
| `kb_docs.json`（7,177 对象 / 7,176 唯一 gid）：`gid / title / keyword / primary_type / tier_v2（A+ 6,676 / A 500）/ signals / structured / training_quality_v2 / hot_score / hot_level / v45 / body_preview` | `gid`→`evidence_json.article_uids[]`；`tier_v2 / training_quality_v2 / hot_score / hot_level`→机器预测；`title/keyword`→旁证 | `v45` 抽样为 `{}`、`body_preview` 只有 447–500 字符（截断设计，**禁止当正文**）；1 个畸形 gid `3733195404`（D-R7） |
| `kb_docs.json` 互动计数与率值 | **`metric_snapshots` 候选**（真实表现快照，生成于 2026-11-07 21:31），不写 `article_metrics` 现值 | 属"真实表现"这一类数据，但已过时 6 天且口径未知 |
| `03_RAG/训练数据/*.jsonl`：`label`（8 类如 `A+_低热`）、`task_type=generate`、`source=elite_pool`、`rag_meta{7 子字段}`、`read/digg/comment/collect_count` | 只登记进 `prediction_records` ＋ 隔离区，**不接生产、不回填指标** | 机器标签、无人工评审（D-R10）；平台计数为 2026-08-22 冻结 |
| `04_RAG反向评审` 13 个 gate 文件（19,394 行） | `evaluation_history` ＋ 未来 L4 门禁规则候选 | 判定为机器评审（`llm_scores`/`rag_judge_*`），与人工判定分离（B-AS-8） |

四条硬约束（§10.2／§10.3／§10.10）：

1. `evidence_json.article_uids[]` **必须非空**才允许入 `knowledge_assets`。RAG 侧可反查 gid 并集只有 **8,291 / 351,843 = 2.356%**（kb_docs 7,176、卡 1,597、反向评审 2,437、训练 395；孤正文 0 个）→ 多数旧卡无法给出正文级证据，是否降级为"待补证据"由人工决定（M-6／M-15）。
2. `kind` 枚举仍需人工定义（M-6）；`credibility` 在人工确认前只能是 `machine_generated`（实测未发现任何 reviewer／审核／署名字段，"未见"≠"未做"，见 §10.11）。
3. **旧 RAG 索引一律不迁移**：`kb_index.json` / `kb_tf.json` 无文档主键、纯位置对齐（D-R6），`card_embeddings_bge_m3.json`（1,583 向量，2026-09-27）落后卡一代（D-R5）→ 检索由新系统 FTS5 bigram 重建，旧文件只登记为证据。
4. **卡片"数量"必须分层报告**：行数 2,443 ≠ 唯一 gid 1,597 ≠ 声明 total 1,597 ≠ 数组实长 1,586；验收等式（B-AS-8）以清洗后唯一 gid 为准。

---

## 4. 身份、去重与类目决议

1. **`article_uid` 唯一性**：跨源 gid 并集 **351,843**（CONFIRMED，gid 行 978,972 → 唯一 351,843，冗余率 64.06%）。导入后 `articles` 行数上限＝该并集减去排除项（UNKNOWN，取决于 M-1/M-7/M-8）。
2. **正文近重复**：归一化正文 hash 行 434,236 → 唯一 193,452（冗余 55.45%）。同一 hash 只保留一条"代表文章"进取材池，其余标 `cluster_id`（P-5）。
3. **url 不作身份**：url 行 206,201 → 唯一 97,091（冗余 52.91%）；tt 有 14 条记录无 url。
4. **零交集提醒**（防错分与"看似重复"）：`corpus_info ∩ corpus_scored_v2_1 = 0`、`factory.db.articles ∩ corpus_info = 0` → 派生层按内容类型分流，不是同一评分阶段的两份副本。
5. **未进派生层的文章**：`corpus_clean` 有 **5,556** 条（工具推荐 4,035 ＋ 工具教程 1,521）从未进入 info/scored 任一派生层 → 类目映射时必须单独处理（M-1）。
6. **类目映射草案**（不可执行，等 M-1）：`真人故事 / 赚钱案例 / 工具教程 / 工具推荐 / 泛资讯 / 行业分析` 6 类 → `technology/finance/social/history/health` 5 类为**多对多**；无法归类比例 **UNKNOWN**。

---

## 5. 导入批次计划（顺序、体量、验收等式）

| 批次 | 内容 | 读入行数（CONFIRMED） | 目标 | 验收等式（dry_run 必须成立） |
|---|---|---:|---|---|
| B-AS-1 | `corpus_tt.json` 元数据＋正文 | 58,931 | staging→`articles` | `rows_read=58931`；`rows_written ≤ 58931`；`rows_skipped` 全部有 reason |
| B-AS-2 | `corpus_archive` 分片＋`archived_gids.jsonl` | 291,205 | 补正文、补 uid | uid 并集与 351,843 一致（不含排除项） |
| B-AS-3 | `factory.db.article_bodies` | 190,555 | 正文补全 | 更新数 ＝ `min(缺正文 uid, 有正文 gid)`；不新增 uid（除非 M-7 批准） |
| B-AS-4 | `factory.db.articles` | 53,678 | 元数据补全 | uid 命中数 = 与 tt 交集 15,525 ＋ 其他源命中数 |
| B-AS-5 | `corpus_clean / info / scored_v2_1` | 49,594 / 28,511 / 15,527 | 字段补全（类型、完整性证据） | 不新增 uid；例外＝clean 独有 **5,556** 条（工具推荐 4,035＋工具教程 1,521）从未进派生层，须先经 M-1 决议 |
| B-AS-6 | `metric_snapshots`（读/digg/collect/comment 快照） | 各源非零合计（见 §3.2） | 只进快照表 | `article_metrics` 仍为 0 行 |
| B-AS-7 | 关键词：`factory.db.keywords` 与清洗后的文本清单并集 | 28,171 行 / 并集 48,732 唯一词（CONFIRMED） | `keywords`＋`keyword_performance` | 不覆盖现库 17 个 `seed` 词（active 9 / gap 8）；`origin='legacy'`；清洗后唯一词数 == 写入数；每词账本记 `source_asset_id` |
| B-AS-8 | 经验卡 / 记忆 / RAG 文本 | 卡 jsonl 2,443 行 → 唯一 gid 1,597；记忆 648 行；kb_docs 7,176 gid；反向评审 2,437 gid（并集 8,291，2.356%）（CONFIRMED） | `knowledge_assets`＋`metric_snapshots`（kb_docs 计数）＋`evaluation_history` | 导入行数 == 清洗后唯一 gid；每条 `evidence_json.article_uids[]` 非空且 gid 必须在 `articles` 中存在（缺失率阈值由人工定，M-15）；`credibility` 只能是 `machine_generated` |
| B-AS-9 | 模型 | 54 条运行记录 | 登记（不加载） | `prediction_records` 不写机器预测为 outcome |
| B-AS-10 | 收尾 | — | FTS 与索引 | `articles_fts` 行数 == `articles` 行数；无悬空 FTS 行 |

**回滚**（三层，见 `P13-asset-audit.md` §12.6）：行级 `import_batch_id` 删除 ＋ FTS rebuild；文件级 `VACUUM INTO` 副本还原；批次前置 `dry_run`。

**导入后体量预估**：正文文本合计约 0.8 GB（420,735,040 ＋ 280,872,293 ＋ 106,007,916 字符），加索引后类目库约 1–1.5 GB 量级（**UNKNOWN**，按 dry_run 实测替换）；当前类目库合计 3,584,000 B（CONFIRMED）。磁盘预算 20 GB 内可容纳（D 盘 free 103.93 GB @14:17）。

---

## 6. 禁止事项（迁移期红线清单）

1. 禁止把 `tier_v2 / hot_level / viral_level / is_elite / is_viral / keyword_score` 当真实爆款标签或写进 `article_metrics`。
2. 禁止用 `full_title` 覆盖 `title`；禁止把 0 字节/摘要当正文进入取材池。
3. 禁止用旧 `kb_tf/kb_index` 直接替代新 FTS（索引必须在新链路重建，且不引入新模型）。
4. 禁止加载 `elite_nb_current.json` 到生产；模型只做隔离评估。
5. 禁止在 `C:\_KEEP_BACKUP` 内写任何文件；禁止第二份语料拷贝；临时件只在 `07_tmp`。
6. 禁止跳过 `dry_run`；禁止无 `source_asset_id` 的写入。
7. 禁止 `git add -A`（资产库未被 ignore）。
8. 禁止调用 Strata / 启动爬虫 / 启动 Worker / 新增模型或浏览器框架。

---

## 7. 未决依赖对照表（阻塞项）

| 依赖 | 阻塞的映射/批次 | 状态 |
|---|---|---|
| M-1 类目映射表 | §3.1 `category`、§4.6、B-AS-1…5 的类目落库 | 待人工 |
| M-3 `deployed` vs `manual_deploy` 矛盾 | B-AS-9 登记口径 | 待人工 |
| M-4 备份独有资产处置 | B-AS-9、模型隔离区 | 待人工 |
| M-6 经验卡是否含人工判定 | §3.5、B-AS-8 | §10 已实测：卡／记忆／训练数据均**未发现任何 reviewer／审核／署名字段**（CONFIRMED）；"是否真的经过人工判定"与 `kind` 枚举仍待人工 |
| M-7 孤正文 136,877 回收 | B-AS-3 | 待人工 |
| M-8 8,000 截断正文替换 | B-AS-3、P-3 | 待人工 |
| M-9 未注册 `demo` 库处置 | 全批次安全边界 | 待人工 |
| M-10 `corpus_archive/runtime/` 4,616 个 0 字节 `.lock` 是否视为陈旧、由谁清理 | 归档批次 B-AS-2 的读取噪音 | 待人工 |
| M-11 新表 `keyword_observations`（承载 37 个 ROI 日快照 / 765,554 观测；现 `keyword_performance` 一词一行、无时间维度） | §3.4、B-AS-7、关键词反馈回流（模块⑦） | 待批准建表 |
| M-12 机器分值的来源列（`keyword_performance.score`、`viral_labels` 均无 `origin/model_version`） | §3.3、§3.4、B-AS-7、B-AS-9；不加列则只能靠导入账本标记 | 待批准建列 |
| M-13 关键词 `status` 词表（现只有 active 9 / gap 8，无 CHECK；草案新增 testing / dormant / retired） | §3.4、B-AS-7、采集调度读取关键词状态 | 待批准词表 |
| M-14 关键词↔文章引用覆盖率仅 1.66%（5,844 / 351,843）：是历史事实的全部，还是允许导入后重算 | `evidence_links`、模块⑦回流可信度 | 待人工 |
| M-15 旧卡/记忆/RAG 文本只有 2.356% 可反查正文：允许以"待补证据"身份入 `knowledge_assets`，还是只导可反查部分 | B-AS-8 验收等式、`knowledge_assets` 覆盖率 | 待人工 |
| M-16 `03_RAG/训练数据` 内 2026-08-22 平台计数快照：登记为历史参考（`metric_snapshots`）还是完全隔离 | B-AS-6／B-AS-9、真实表现口径 | 待人工 |
| M-17 污染与存根文件（`tpl_polluted.jsonl.bak`、`shadow/` 124 个 md、35 个 0 字节打分文件、`case_assets/` 断链）保留为证据还是登记废弃 | 资产登记簿完整性、导入噪音 | 待人工 |
| U-1 跨源有效文章总数 | 验收等式与体量预估 | 待补测（30–40 min） |
| U-2 `is_junk/confidence/signals` 分布 | 质量分层 L1/L2 权重 | 待补测（约 5 min） |
| U-3 模型内部结构 | 隔离评估方案 | 待专项 |
| U-5 `trainset_v1_<hash>` 快照构成 | B-AS-9 | 待专项 |

---

## 8. 结论

旧资产里**真正可复用的是文章正文与元数据（按 gid 归一后 351,843 个候选身份）**，其次是需要重算口径的关键词资产；**RAG 索引与旧模型属于"重建/隔离"而非"搬运"**。新系统缺 3 个承接能力（`article_uid`、`source_asset_id`＋`import_batches` 账本、`viral_labels.origin/model_version`），这三项是任何导入的前置条件；缺真实表现数据（`article_metrics/metric_snapshots/prediction_records` 全 0 行）意味着"爆款反馈闭环"（P3）在数据接入前无法启动，但不影响 P0–P2。
