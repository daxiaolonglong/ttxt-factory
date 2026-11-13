# P13-A — 旧头条资产只读审计与新工厂映射

生成时间：2026-11-13（Asia/Shanghai）
执行者：DSH 代理（只读审计模式）
证据标签：**CONFIRMED**＝本轮用脚本直接测得且可复算；**SUPPORTED**＝有一致证据但未直接复算／来自人工声明；**UNKNOWN**＝本轮无法判定。
本轮边界：`D:\ttxt\资产库` 与 `C:\_KEEP_BACKUP` 全程只读；新写入只发生在 `D:\ttxt\07_tmp`（探针与中间件）与 `D:\ttxt\08_wiki\reports`（本报告）。未启动爬虫、Worker、模型切换，未向 Strata 发请求，未修改任何数据库或资产文件。

---

## 0. 审计方法与只读保证（CONFIRMED）

| 项 | 做法 | 证据文件 |
|---|---|---|
| 文件级清单 | 逐文件 SHA256（分块流式，不把大文件整体读入内存） | `07_tmp/asset_manifest.jsonl`（13,378 行，坏行 0）、`07_tmp/keepbackup_manifest.jsonl` |
| 语料 JSON | 定位记录分隔符（`corpus_tt/corpus_archive` 为 CRLF，`corpus_clean/scored_v2_1/info` 为 LF）后逐记录流式解析，禁用整档载入 | `07_tmp/asset_probe_articles.py`、`07_tmp/audit_parts/*.json` |
| SQLite | `sqlite3.connect("file:...?mode=ro&immutable=1", uri=True)`；两份 `factory.db` 均无 `-wal/-shm/-journal`（journal_mode=delete、page_size=4096、UTF-8、user_version=0） | `07_tmp/keepbackup_factorydb.json`、`07_tmp/factorydb_diff.json` |
| 去重交叉 | 临时索引 `07_tmp/asset_index.db`（gid/正文归一化 hash/url × 来源），仅本轮新建，不影响资产 | `07_tmp/cross_source_joins.json` |
| 新系统对照 | `00_control/system.db` 与 `01_categories/<slug>/articles.db` 只读 schema 与行数普查 | `07_tmp/new_system_schema.json`、`07_tmp/new_system_contents.json` |
| 关键词专项 | 37 份 `kw_roi_history` 快照逐行流式（单行最长 6,330,982 B）、文本清单递归 union、引用 gid 抽取 | 24 个 `kw_probe_*.py` → 17 份 `07_tmp/kw_probe_*.json`、`07_tmp/audit_keywords.json`、`07_tmp/kw_referenced_gids.jsonl`（5,844 行） |
| RAG／经验卡／模型专项 | `kb_docs`/`kb_tf`/`kb_index` 分块流式、卡片逐行、13 个 gate 文件行数、`factory.db.model_runs` 只读全行 | 9 个 `rag_probe_*` 步骤（id 见汇总 `probes`）→ 7 份 `07_tmp/rag_probe_*.json`、`07_tmp/audit_rag_experience.json`、`07_tmp/{rag_doc,card,train}_referenced_gids.jsonl` |

资产库文件是否被写入的核验（CONFIRMED）：本轮结束时 `D:\ttxt\资产库` 内 mtime 最新的文件仍是 `README_资产清单.md`（2026-11-13 11:40:31），其余全部 ≤ 2026-11-07 21:34:26；本轮所有探针输出均落在 `07_tmp`。
另有 `D:\ttxt\07_tmp\asset_index.db`（275.1 MiB）与 `07_tmp/audit_parts/` 为本轮临时产物，计入 20 GB 预算；§10 收口后实测 `07_tmp` 合计 **215 个文件 / 300,391,405 B（286.5 MiB）**，其中 `asset_index.db` 为主体（275.1 MiB）；D 盘可用 103.93 GB。

**待人工确认（唯一写痕）**：`D:\ttxt\资产库\README_资产清单.md`（2,636 B，创建 2026-11-13 11:34:20、写入 11:40:31）位于只读资产目录内，内容以人工口吻描述 2026-11-07 的 D/E/F 盘清理决策。本轮上下文无法证明其作者；若非人工所写，需要确认是否为早期代理误写入资产目录。

---

## 1. 资产库文件级盘点（CONFIRMED）

总量：**13,378 个文件 / 4,879,356,851 B（4,879.4 MB）**；mtime 跨度 2026-08-14T04:19:25 → 2026-11-13T11:40:31。

按顶层目录：

| 目录 | 文件数 | 字节 |
|---|---:|---:|
| `01_语料库` | 12,014 | 4,241,707,364 |
| `02_关键词` | 216 | 338,038,627 |
| `03_RAG` | 960 | 158,643,101 |
| `05_V4.5数据资产分层` | 162 | 130,819,035 |
| `04_RAG反向评审` | 25 | 10,146,088 |
| 根目录（`README_资产清单.md`） | 1 | 2,636 |
| **合计** | **13,378** | **4,879,356,851** |

按字节最大的目录（前几名）：`01_语料库` 根 2,944,367,893（10 个文件）、`01_语料库/corpus_archive` 1,297,334,855（7,388 个文件）、`02_关键词` 根 170,917,333（1 个文件＝`kw_roi_history.jsonl`）、`03_RAG/rag` 142,422,260（92）、`02_关键词/关键词` 106,010,967（40）、`05_V4.5数据资产分层/models` 70,072,237（3）、`02_关键词/选题` 57,707,687（29）、`05_V4.5数据资产分层/选题_含经验卡` 57,707,687（29）。

按扩展名（部分）：`.json` 7,464 个 / 2,790,706,860 B；`.db` 1 个 / 1,749,405,696 B；`.jsonl` 76 个 / 327,018,748 B；`.md` 1,087 个 / 5,549,846 B；`.txt` 32 个 / 2,406,712 B；`.py` 82 个 / 358,990 B；**`.lock` 4,618 个 / 4,616 B（几乎全是 `01_语料库/corpus_archive/runtime/` 的陈旧锁文件）**；另有历史备份后缀 `.bak_20261021_084607_force` 2,603,047 B、`.corrupt_20260920_123325` 623,160 B、`.bak_r1594_20261011_230123` 485,553 B、`.bak` 2 个 141,384 B、`.encbak` 50,785 B、`.before_r204` 2 个 1,454 B。

**零字节文件 36 个**（CONFIRMED），其中包括 `03_RAG/rag/v45_rules.jsonl`（README 把它列为保留资产之一）、`02_关键词/关键词/saturated_keywords_latest.txt`，以及 `03_RAG/rag/rag_vs_baseline_r1..r5/*/{scores,gen_progress,score_errors,score_progress}.jsonl` 与 10 个 `.err`。含义：**部分被 README 记为"资产"的文件实际为空**，迁移时不能按文件名计数。

### 1.1 README 声明 vs 本轮实测（CONFIRMED 修正）

| README（`资产库/README_资产清单.md`）声明 | 本轮实测 | 判定 |
|---|---|---|
| `corpus_tt.json` 390.8 MB | 409,794,942 B ＝ 390.8 MiB | 一致（MiB/MB 表述差） |
| 58,931 条文章 | 58,931 个 gid（与 `.meta.json` total=58,931 一致） | 一致 |
| 含正文 40,794 条 | 正文非空且去空白后可哈希的记录 **40,342**（`hash_src` 内 `src=corpus_tt` 行数） | **差异 452 条：CONFIRMED 存在、成因 UNKNOWN**（§6 主表已记录该差；§6.1 另证 `body_status` 陈旧不可采信） |
| `corpus_archive\` 约 63 MB | **1,297,334,855 B（约 1.30 GB）／7,388 个文件** | **README 低估约 20 倍**（63 MB 只是分片 0001 之外的错觉） |
| `kw_roi_history.jsonl` 163 MB | 170,917,333 B ＝ 163.0 MiB | 一致 |
| `kb_docs` 16.5 MB / `kb_index` 21.3 MB / `kb_tf` 97 MB | 17,318,899 / 22,377,798 / 102,056,310 B | 一致（MiB） |
| 622 张经验卡 / 648 条经验记忆 | 卡：jsonl **2,443 行 / 1,597 唯一 gid**，json 声明 1,597 但数组实长 **1,586**；经验记忆 **648 行** | **622 口径失效**（D-R3，CONFIRMED，§10.4/§10.9）；648 条 **CONFIRMED 一致** |
| `elite_nb_current.json` 45.8 MB | 48,057,512 B ＝ 45.8 MiB | 一致 |
| factory.db 53,678 articles / 190,555 bodies / 10 表 | 53,678 / 190,555 / 10 表（只读 COUNT 复算） | 一致 |

---

## 2. 文件级重复与冗余（CONFIRMED）

- 内容完全相同的文件组：**165 组**；冗余字节 **67,353,745 B（约 67.4 MB）**。
- 冗余按目录归属：`02_关键词` ↔ `05_V4.5数据资产分层` **59,617,353 B**；`03_RAG` 内部 6,596,698 B；`01_语料库` ↔ `05_V4.5数据资产分层` 1,129,445 B；`01_语料库` 内部 10,249 B。
- **`02_关键词/选题/` 与 `05_V4.5数据资产分层/选题_含经验卡/` 是同一棵树的完整副本**：两边各 29 个文件、字节数完全相同（57,707,687 B），连 `shadow/`（124 文件 / 597,237 B）、`case_assets/C/`（1 文件 / 580,519 B）都逐一对齐。
- 单文件重复示例：`card_embeddings_bge_m3.json` 32,377,041 B ×2；`experience_cards.jsonl` 5,498,484 B ×2；`experience_cards.json` 3,774,604 B ×2；`topic_score.json` 5,376,408 B ×2；`kb_reality_scan.jsonl` 5,583,339 B ×2。
- **RAG 训练数据三份同体**（CONFIRMED，重要）：`03_RAG/训练数据/training_data.jsonl`、`training_data_panel.jsonl`、`training_data_rag400.jsonl` **SHA256 完全相同**（3,293,240 B）。README 把它们当成不同数据集列出，实际是同一份的三个名字。

> 结论：旧资产里"文件数"与"数据量"严重脱钩。任何按文件数/文件名的迁移统计都会虚增，必须以内容哈希与文章 ID 集合为准。

---

## 3. `C:\_KEEP_BACKUP` 只读比对（CONFIRMED）

备份规模：**16,133 个文件 / 7,065,010,198 B（7.07 GB）**；C 盘剩余 56,430,534,656 B。

| 备份顶层目录 | 文件数 | 字节 |
|---|---:|---:|
| `语料库` | 12,013 | 2,492,299,130 |
| `cluster_models` | 63 | 2,368,862,824 |
| 根 `factory.db` | 1 | 1,749,405,696 |
| `toutiao_ai_数据` | 3,006 | 200,368,372 |
| `toutiao_ai_rag` | 988 | 146,497,723 |
| `关键词` | 57 | 107,503,941 |
| `RAG反向评审_scripts` | 3 | 71,254 |
| `_tool` | 2 | 1,258 |

双向差异（以 SHA256 集合判定）：

- 资产库侧：**13,378 个文件中有 13,364 个能在备份里找到字节相同的对应文件**；只有 **14 个文件（2,472,370,090 B）在备份中没有同内容副本**。这 14 个是资产库独有的**更新版**：`factory.db`、`corpus_tt.json`＋其 4 个 sidecar（`.meta/.round_state/.write_health/.task_cursor`，共 1,209 B）、`kw_roi_history.jsonl` 170,917,333 B、`kb_tf.json` 102,056,310 B、`kb_index.json` 22,377,798 B、`kb_docs.json` 17,318,899 B、`kw_typed_candidates.jsonl` 480,627 B、`04_RAG反向评审/scripts` 2 个脚本（14,640 B）、`README_资产清单.md` 2,636 B。
- 备份侧：**2,911 个文件（4,648,626,619 B）在资产库中没有同内容副本**，即**资产库缺失的资产家族**：
  - `cluster_models`：**58 个文件 / 2,228,719,658 B**（按日期滚动到 `elite_nb_v20261105_0239.json` 等；`elite_nb_current.json` 与资产库同 SHA）——**全部历史模型快照只在备份里**。
  - `toutiao_ai_数据`：生成/清洗/筛选/合并/发布池/失败案例/负样本/点评/标题/审计等 **2,900+ 个文件**，其中 `筛选/training_data_clean_filtered.json` **55,560,135 B**（资产库没有此件）。
  - `toutiao_ai_rag` 旧版 `kb_tf/kb_docs/kb_index`（101,758,349 / 17,268,181 / 22,341,319 B，mtime 2026-11-06）、`语料库/corpus_tt.json` 旧版 409,792,552 B（mtime 2026-11-06T11:38）、`关键词/kw_typed_candidates.jsonl` 旧版 480,627 B（mtime 2026-11-06T10:52）。
- 同名同大小但内容不同：仅 **3 个**（`factory.db`、`关键词/kw_typed_candidates.jsonl`、`语料库/corpus_tt.json.task_cursor.json`）。
- 去重视角：资产库 8,564 个不同 SHA，备份 11,094 个，交集 8,550，**备份独有 2,544 个 SHA**。

> 含义：**资产库不是备份的超集**。`05_V4.5数据资产分层/models/` 只保留了 `elite_nb_current.json` 等 3 个文件（70 MB），而 **2.23 GB 的历史模型快照（58 个）只存在于 `C:\_KEEP_BACKUP\cluster_models`**；旧代生产流水线产物（`toutiao_ai_数据`，含 55 MB 训练筛选集）同样只在备份里。任何"旧资产已全部保留"的判断都不成立，必须在映射阶段明确这些资产是"复用/隔离/放弃"。

---

## 4. 两份 `factory.db` 是否同一数据（CONFIRMED）

| 项 | `D:\ttxt\资产库\01_语料库\factory.db` | `C:\_KEEP_BACKUP\factory.db` |
|---|---|---|
| 大小 | 1,749,405,696 B | 1,749,405,696 B |
| SHA256 | 不同 | 不同 |
| mtime | 2026-11-07T21:32:06 | 2026-11-07T21:29:34 |
| journal_mode / page_size / encoding / user_version | delete / 4096 / UTF-8 / 0 | 同 |
| 10 张表行数 | articles 53,678；article_bodies 190,555；events 15,189；health_history 97,697；health_last 6,577；keywords 28,171；model_runs 54；pipeline_runs 3,095；profiles 6,577；tasks 453,579 | **完全相同** |

逐行比对（`SELECT rowid,* ... ORDER BY rowid` 的规范化 SHA256，见 `07_tmp/factorydb_diff.json`）：

- **10 张表中的 9 张逐行完全一致**（articles、article_bodies、events、health_history、health_last、keywords、model_runs、profiles、tasks）。
- 唯一差异在 `pipeline_runs`：两边各 3,095 行，**3,094 个共同 rowid 的内容逐字节相同**；备份独有 rowid 3452，资产库独有 rowid 3453，而这两行的内容**完全相同**（`ts=2026-11-06T03:48:18.298Z, ok=1, clean=49594, scored=15527, elite=6128`），只是落在不同 rowid（资产库中 3451/3452 为空洞）。两副本 `pipeline_runs` 时间范围均为 2026-08-17T06:43:48.603Z → 2026-11-06T03:48:18.298Z。补充只读实测（`07_tmp/pr_ok_dist.py`）：`pipeline_runs` 列为 `ts/ok/clean/scored/elite/new_words`，`ok=1` 共 **3,047**、`ok=0` 共 **48**（1.55%，CONFIRMED）——但 `ok` 只表示"跑完一轮"，不代表质量合格（§8：模型全部低于多数类基线仍在继续运行）。

> 结论：**两份 factory.db 数据等价**（差异只是同一行落在不同 rowid／页布局），不存在"哪一份更权威"的分歧；哈希不同不代表内容不同。后续迁移只需选一份做输入，并记录其 SHA256 与 mtime。

---

## 5. 文章级交叉与去重：JSON 语料 × SQLite × 归档（CONFIRMED）

以 `gid`（头条文章 ID）、正文归一化 SHA256（去掉全部空白后哈希）、`url` 三个维度建临时索引（`07_tmp/asset_index.db`），来源标签：`corpus_tt`、`corpus_clean`、`corpus_scored_v2_1`、`corpus_info`、`db_articles`、`db_bodies`、`archive_gids`、`archive_shards`。

引用行（来源 × 记录）：

| 来源 | gid 行数 | 正文哈希行数 | url 行数 |
|---|---:|---:|---:|
| `archive_shards` | 291,088 | 113,155 | — |
| `archive_gids` | 291,088 | — | — |
| `db_bodies` | 190,555 | 188,116 | — |
| `corpus_tt` | 58,931 | 40,342 | — |
| `db_articles` | 53,678 | — | — |
| `corpus_clean` | 49,594 | 49,063 | — |
| `corpus_info` | 28,511 | 28,226 | — |
| `corpus_scored_v2_1` | 15,527 | 15,334 | — |
| **合计** | **978,972** | **434,236** | **206,201** |

去重结果（同一 gid / 同一归一化正文 / 同一 url 只计一次）：

| 维度 | 原始引用 | 唯一值 | 冗余 | 冗余率 |
|---|---:|---:|---:|---:|
| gid | 978,972 | **351,843** | 627,129 | **64.06%** |
| 正文归一化 SHA256 | 434,236 | **193,452** | 240,784 | **55.45%** |
| url | 206,201 | **97,091** | 109,110 | **52.92%** |

gid 出现来源数分布（351,843 个 gid）：只出现在 1 个来源 **10,776**；2 个来源 **152,414**；3 个 **106,931**；4 个 **66,147**；5 个 **15,498**；6 个 **42**；7 个 **35**。
正文归一化哈希出现来源数分布（193,452 个哈希）：1 个来源 35,390；2 个 109,328；3 个 14,775；4 个 33,930；5 个 29。
url 出现来源数分布（97,091 个 url）：1 个来源 47,512；2 个 5,559；3 个 28,509；4 个 15,511。

两两 gid 交集（CONFIRMED）：

| 来源对 | 共同 gid |
|---|---:|
| `archive_gids` ↔ `archive_shards` | 291,088（完全相同） |
| `archive_*` ↔ `db_bodies` | 139,075 |
| `archive_*` ↔ `db_articles` | 37,717 |
| `archive_*` ↔ `corpus_tt` | **149** |
| `archive_*` ↔ `corpus_clean` | 87 |
| `archive_*` ↔ `corpus_info` | 44 |
| `archive_*` ↔ `corpus_scored_v2_1` | 38 |
| `db_articles` ↔ `db_bodies` | 53,678（articles 全部有正文行） |
| `db_bodies` ↔ `corpus_tt` | 49,585 |
| `db_bodies` ↔ `corpus_clean` | 49,594（clean 全部有正文行） |
| `db_bodies` ↔ `corpus_info` | 28,511（info 全部有正文行） |
| `db_bodies` ↔ `corpus_scored_v2_1` | 15,527（scored 全部有正文行） |
| `corpus_tt` ↔ `corpus_clean` | 49,584（clean 几乎完全来自 tt；clean 有 10 个 gid 不在 tt） |
| `corpus_tt` ↔ `corpus_info` | 28,506 |
| `corpus_tt` ↔ `corpus_scored_v2_1` | 15,524 |
| `corpus_clean` ↔ `corpus_info` | 28,511（info ⊂ clean） |
| `corpus_clean` ↔ `corpus_scored_v2_1` | 15,527（scored ⊂ clean） |
| `db_articles` ↔ `corpus_clean` | 15,527 |
| `db_articles` ↔ `corpus_scored_v2_1` | 15,527 |
| **`db_articles` ↔ `corpus_info`** | **0** |
| **`corpus_info` ↔ `corpus_scored_v2_1`** | **0** |

需要解释的两个反常（**事实 CONFIRMED，成因 UNKNOWN，需人工确认**；§6/§7 已复核，仍未解释）：
1. `corpus_info` 与 `corpus_scored_v2_1` 的 gid 交集为 **0**，但两者都完全包含在 `corpus_clean` 内 —— 说明 `corpus_info`（信息层）与 `scored_v2_1`（评分层）覆盖的是**不同的文章子集**，评分链路只覆盖 clean 的 15,527/49,594 ≈ 31.3%。
2. `archive_*` 与 `corpus_tt` 只有 **149** 个 gid 相交，但归档集合 291,088 个 gid 与 `db_bodies` 相交 139,075 —— 归档主要对应数据库侧正文，而不是 `corpus_tt` 当前集合；两者对同一 gid 的"归档"语义不同，迁移时必须以 `gid` 为主键统一，不能按文件来源分层。

**唯一文章总体规模（迁移口径）**：35 万量级 gid（**351,843**）；有正文（归一化哈希可算）的唯一正文 **193,452** 篇。§6 已按"正文字段非空＋长度"复核分源数字（CONFIRMED）；但**跨来源去重后"正文≥200 字符"的有效文章总数仍未测得（U-1，UNKNOWN）**，导入批次验收必须先用 U-1 口径补测，不得直接用 193,452 当作可导入量。

## 6. 有效文章数量与正文完整性（CONFIRMED，逐流式解析）

四个语料 JSON 全部逐条解析成功，**malformed_records = 0**（解析器自动识别 CRLF/LF 记录分隔，未整档载入内存）。

| 来源 | 记录数 | 唯一 gid | gid 重复多余行 | 唯一 url | 正文非空记录 | 正文唯一（去空白 SHA256） | 文件内同正文多余行 | 正文 < 200 字符 | 正文含 HTML 标签 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `corpus_tt.json` | 58,931 | 58,931 | 0 | 58,917 | 40,794 | 40,342 | 452 | 236 | 0 |
| `corpus_clean.json` | 49,594 | 49,594 | 0 | 49,581 | 49,594 | 49,063 | 531 | 9,080 | 0 |
| `corpus_info.json` | 28,511 | 28,511 | 0 | 28,511 | 28,511 | 28,226 | 285 | 8,801 | 0 |
| `corpus_scored_v2_1.json` | 15,527 | 15,527 | 0 | 15,514 | 15,527 | 15,334 | 193 | 151 | 0 |

正文长度（字符，非空记录）：

| 来源 | 总字符 | 均值 | 中位 | p75 | p90 | p99 | 最大 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `corpus_tt` | 104,953,643 | 1,781 | 1,293 | 2,713 | 4,355 | 8,107 | 62,646 |
| `corpus_clean` | 106,007,916 | 2,138 | 1,645 | 3,049 | 4,641 | 8,627 | 62,646 |
| `corpus_info` | 45,605,058 | 1,600 | 1,035 | 2,245 | 3,940 | 8,107 | 62,646 |
| `corpus_scored_v2_1` | 43,490,383 | 2,801 | 2,416 | 3,668 | 4,978 | 8,828 | 53,148 |

标题长度：中位 29–30 字符，但 `corpus_tt/clean/info` 的 **p90 与 p95 都是 150**，最大 400 —— 说明约 10%–25% 的记录标题被写成 150 字符的 `full_title` 拼接（信息层尤其明显：info 的 p75=92）。这是清洗质量缺陷，导入时不能直接当标题用。

### 6.1 正文完整性判定：不能采信 `body_status`（CONFIRMED，重要）

| 来源 | `body_status=success` | `=empty` | `=failed` | `=pending` | 正文实际非空 | 差异 |
|---|---:|---:|---:|---:|---:|---|
| `corpus_tt` | 40,562 | 18,082 | 213 | 74 | 40,794 | 232 条状态非 success 却有正文 |
| `corpus_clean` | 40,502 | **8,879** | 213 | — | **49,594** | **8,879 条状态为 empty 却有正文** |
| `corpus_info` | 19,826 | 8,597 | 88 | — | 28,511 | 8,597 条同上 |
| `corpus_scored_v2_1` | 15,274 | 155 | 98 | — | 15,527 | 155 条同上 |

`corpus_clean` 有 **8,860 条带 `body_from` 字段**（正文由别的途径补齐）、`corpus_info` 有 8,589 条。结论：**`body_status` 是陈旧字段，与正文内容不一致**；迁移必须以"正文字段是否非空 + 长度阈值"判定，不能以状态字段计数（否则会把 8,879 篇有效正文误判为缺失，同时把摘要当正文的多算进来）。

### 6.2 摘要冒充正文（CONFIRMED，重要）

`corpus_info` 有 **8,801 条（30.9%）正文 < 200 字符**，`corpus_clean` 有 **9,080 条（18.3%）**，两者 p5/p10/p25 都落在 89–150 字符区间；而 `corpus_scored_v2_1` 只有 151 条（1.0%）。150 字符正是头条摘要的长度上限，因此**信息层语料里有约三成记录只有摘要、没有正文**。有效文章口径必须带长度阈值。

- 按"正文非空且 ≥ 200 字符"口径（本轮实测，单来源）：`corpus_tt` 40,558；`corpus_clean` 40,514；`corpus_info` 19,710；`corpus_scored_v2_1` 15,376。
- **跨来源去重后的"有效文章总数"目前 UNKNOWN**：`article_bodies`（190,555 行）与 `corpus_archive` 分片正文的长度分布尚未产出，且同一 gid 在不同来源的正文字数不同（补采导致）。给出边界：JSON 四档去重上界 58,931（tt 全集）、下界 ≥40,558；加上数据库/归档侧后必须重跑一次带长度索引的流式扫描才能定数（见 §13 待办 U-1）。

### 6.3 真实表现字段：存在但既稀疏又是快照（CONFIRMED）

| 来源 | 有该字段 | `read_count` 非零 | `digg_count` 非零 | `collect_count` 非零 | `comment_count` 非零 |
|---|---:|---:|---:|---:|---:|
| `corpus_tt` | 58,877 | 33,865（57.5%） | 18,351 | 16,902 | 9,791 |
| `corpus_clean` | 49,573 | 29,830（60.2%） | 16,518 | 15,493 | 8,791 |
| `corpus_info` | 28,507 | 18,257（64.0%） | 10,583 | 8,937 | 6,148 |
| `corpus_scored_v2_1` | 15,514 | 8,614（55.5%） | 4,415 | 4,818 | 1,915 |

另有机器派生字段（非真实表现）覆盖：`hot_value / interaction_rate / content_value / article_value / creator_bonus / creator_level / viral_level / title_bait` 在 tt 41,468 条、clean 34,836 条、info 19,818 条；`age_hours / read_velocity` 24,230 / 21,318 / 12,887 条；`tier_v2 / training_quality_v2` 15,525 / 15,527 条。
`viral_level` 分布（tt）：普通 40,342、普通优质 969、潜力爆款 133、超级爆款 13、爆款 11；clean：普通 33,853、普通优质 856、潜力爆款 110、爆款 10、超级爆款 7；scored：普通 10,869、普通优质 203、潜力爆款 27、爆款 3、超级爆款 2。
**这些是旧系统模型/规则给出的预测标签，不是头条官方真实表现**；按红线要求只能作为"机器预测"类别隔离保存，禁止当作爆款真值（见 §11 四类数据分离）。

时间跨度（CONFIRMED，关键约束）：四个语料的 `publish_time` 全部落在 **2026-10-10 → 2026-11-06/11-07**，正文抓取时间 2026-10-10 → 2026-11-06。**整个语料只覆盖约 4 周**，不存在跨季度样本；任何按时间切分的模型验证都只能在 4 周内做，且 `metrics` 是抓取时点快照（无最终值）。

采集模式单一（CONFIRMED，关键约束）：`source` 全为 `toutiao`、`source_mode` **全为 `hot_rank`**（tt 58,917 / clean 49,581 / info 28,511 / scored 15,514，另有 14 条缺字段）。**语料 100% 来自热榜**，没有关键词驱动或分类频道来源 → 旧语料对"非热榜长尾"完全没有覆盖，用它训练/评估爆款研究会有系统性偏差。

其他字段事实（CONFIRMED）：作者去重后 tt 30,673 / clean 25,001 / info 17,576 / scored 8,521；单一作者最多发文 931（tt 的「优雅奶酪6hWlg9」）、官方媒体号（新浪财经 470、环球网 350…）占比高；关键词字段去重后 tt 9,563 / clean 8,819 / info 6,542 / scored 4,812 个，高频词包含「别人赚钱案例案例」「为什么普通人拼命打工赚钱工具」这类**拼接式噪声关键词**。正文含 HTML 标签的记录为 **0**（清洗过）。

---

## 7. 五类目分布：旧类型体系 ≠ 新类目（CONFIRMED）

新系统类目（只读实测，见 `07_tmp/new_system_contents.json`）：

| 类目 | articles | articles_fts | 未入 FTS | viral_labels | keywords | knowledge_assets | evidence_links | writing/evaluation_history | article_clusters |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| technology | 137 | 62 | **75** | 114 | 4 | 2 | 3 | 3 / 3 | 10 |
| finance | 50 | 47 | 3 | 50 | 4 | 1 | 8 | 4 / 4 | 0 |
| social | 0 | 0 | 0 | 0 | 3 | 2 | 0 | 0 / 0 | 0 |
| history | 0 | 0 | 0 | 0 | 3 | 2 | 0 | 0 / 0 | 0 |
| health | 25 | 25 | 0 | 25 | 3 | 2 | 8 | 6 / 6 | 0 |
| **合计** | **212** | **134** | **78** | **189** | **17** | **9** | **19** | 13 / 13 | 10 |

（technology 另有 **4 条悬空 FTS 行**（FTS 里有、articles 里没有）。`article_metrics`、`metric_snapshots`、`prediction_records` **全部 0 行**；`viral_labels` 全部 `label='unscored'`、`confidence_tier='missing_data'`、score 为 NULL、理由「缺少真实表现指标(未接入授权数据)」。`system.db`：categories 5、crawl_jobs 54（done 46 / aborted 7 / queued 1）、model_jobs 70（writing 64 / chat 6）、`sources` 与 `system_log` **0 行**；类目创建时间 2026-11-12T12:24:09。）

旧语料的类型字段是**内容形态**而非主题类目（CONFIRMED，`primary_type` / `v2_type` 计数）：

| `corpus_clean` primary_type | 条数 | 落在哪个派生文件 |
|---|---:|---|
| 泛资讯 | 21,708 | `corpus_info` |
| 行业分析 | 6,803 | `corpus_info` |
| 真人故事 | 12,350 | `corpus_scored_v2_1` |
| 赚钱案例 | 3,177 | `corpus_scored_v2_1` |
| 工具推荐 | 4,035 | **两个派生文件都没有** |
| 工具教程 | 1,521 | **两个派生文件都没有** |
| 合计 | 49,594 | info 28,511 + scored 15,527 = 44,038；**5,556 条未进入任何派生层** |

`corpus_info` 的 v2_type 只有 泛资讯/行业分析，`corpus_scored_v2_1` 的 v2_type 只有 真人故事/赚钱案例 —— **info 层与 scored 层是按类型切的，不是按评分阶段切的**（这也解释了 §5 里两者 gid 交集为 0）。`tier_v2` 分布：A 7,521 / B 4,078 / A+ 2,645 / 过滤 1,283（合计 15,527，即只有 scored 层有 v2 质量分层）。

**结论与待人工确认（M-1）**：旧体系 6 类（泛资讯/行业分析/真人故事/赚钱案例/工具推荐/工具教程）与新体系 5 类（technology/finance/social/history/health）**不是同一种分类**：旧的是叙事形态，新的是主题领域。多对多映射无法自动判定，例如"真人故事×财经"同时含 social+finance 信号，"工具推荐/教程"多落在 technology/health。必须人工给出映射表（含"无法归类"的处置），本报告不做自动归类。

## 8. 归档侧与 `factory.db` 侧语料（CONFIRMED）

### 8.1 `01_语料库/corpus_archive`（README 说"约 63 MB"，实际 1.30 GB）

| 项 | 实测 |
|---|---:|
| 顶层文件数 | 7,388（其中分片文件 7,386，分片字节合计 **1,290,843,352**） |
| `runtime/` 文件数 | 4,616（几乎全是 0 字节 `.lock`） |
| `archived_gids.jsonl` | 291,109 行 → **唯一 gid 291,088**（重复行 21） |
| 分片内记录数 | **291,205**（唯一 gid 291,088 → 跨分片重复 gid 117 行） |
| 分片正文唯一（归一化 hash） | 113,155 |
| 分片正文字符合计 | 280,872,293（均值 964.5） |
| 分片正文长度 | **p50 = 0**、p75 = 1,512、p90 = 3,071、p95 = 4,214、p99 = 7,054、max 66,364 |

**归档里至少一半记录没有正文**（p50=0，非空正文只有 113,155 个唯一 hash）。归档的价值在于 gid 清单与元数据，不能当成"1.3 GB 正文资产"来估算复用收益。

### 8.2 `factory.db.articles`（53,678 行）

| 项 | 实测 |
|---|---|
| 行数 / 唯一 gid / 唯一 url | 53,678 / 53,678 / 53,678（无重复） |
| `primary_type` | 真人故事 41,057、赚钱案例 12,621（**只有这两类**） |
| `tier_v2` | A 24,864、B 15,669、A+ 8,199、过滤 4,946 |
| `hot_level` | 低热 29,186、普通 14,746、热门 6,340、**爆款 3,406** |
| `is_elite=1` | 6,128 |
| `is_viral=1` | **37** |
| `read_count` 非零 | 33,708 |
| `publish_time` 跨度 | **2014-05-13T14:48:22 → 2026-11-06T08:38:23**（12 年） |

注意：`factory.db.articles` 只覆盖"真人故事 / 赚钱案例"两类，与 `corpus_scored_v2_1` 同族；`corpus_info`（泛资讯/行业分析）**不在 articles 表里**。`publish_time` 最早到 2014 年，说明库内混有历史回填文章，与四个 JSON 语料的"仅 4 周"结论不冲突（不同代）。

### 8.3 `factory.db.article_bodies`（190,555 行）

| 项 | 实测 |
|---|---:|
| 行数 / 唯一 gid / 唯一正文 hash | 190,555 / 190,555 / **188,116**（库内同正文多余行 2,439） |
| 空正文行 | 0 |
| 正文 < 200 字符 | 17,455（9.2%） |
| 正文含 HTML 标签 | 0 |
| 正文字符合计 | 420,735,040（均值 2,208） |
| 正文长度分布 | p0 37 / p5 150 / p10 298 / p25 995 / **p50 1,804** / p75 3,011 / p90 4,556 / **p99 8,000 / p100 8,000** |

**`article_bodies` 的正文被硬截断在 8,000 字符**（p99 与最大值都恰好 8,000）。而 JSON 语料与归档的最大正文 53,148–66,364 字符。含义：**长文在数据库侧不完整**，若以数据库为导入主源会永久丢失 8,000 字符以上的尾部；JSON/归档侧保留更完整的版本。

正文与文章的对应关系（CONFIRMED）：`articles` 的每一行都有正文（articles_only = 0），但 **136,877 条正文没有对应的 articles 行（孤正文，元数据缺失）** —— 与归档 gid 集合（291,088）和 `articles`（53,678）的规模差一致。这批孤正文可以按 `gid` 从 `corpus_archive` / `corpus_tt` 反查元数据后回收，但**必须记录"元数据来自另一来源"**（见 §12 血缘要求）。

### 8.4 `factory.db.keywords`（28,171 行）

列：`word, source, synced_at, parent_keyword, state, search_count, success_count, empty_count, consecutive_fail, quality_avg, hot_avg, elite_count, hot_count, keyword_score, last_success, next_test_time, last_state_change`；首列 `word` 唯一（28,171 个）。ROI/效果字段齐全，但语义与计算口径需反推（§9 由关键词专项完成）。

### 8.5 `factory.db` 其他表（行数）

`events` 15,189、`health_history` 97,697、`health_last` 6,577、`profiles` 6,577、`tasks` 453,579、`pipeline_runs` 3,095、`model_runs` 54（见 §11）。除 `articles/article_bodies/keywords/model_runs` 外，其余表属于旧系统运行遥测，**不是文章资产**，迁移时应排除（见 §12）。

---

## 9. 关键词资产与 ROI 质量（CONFIRMED，专项 24 个只读探针）

证据：`07_tmp/audit_keywords.json`（155,263 B，**24 个只读探针**，2026-11-13T16:29:37 定稿）＋ `07_tmp/audit_keywords_notes.md`（37,136 B / 215 行人读版，2026-11-13T16:44:57）＋ `07_tmp/kw_probe_*`（探针脚本与逐条 JSON）＋ `07_tmp/kw_referenced_gids.jsonl`（116,871 B）。本节所有数字均可由上述 JSON 复算。

### 9.1 规模与口径（CONFIRMED，两个口径都成立）

| 口径 | `02_关键词` 直接子项 | 递归全量 |
|---|---|---|
| 文件数 | 顶层 1 文件 ＋ `关键词/` 40 ＋ `选题/` 29（§1 清单口径） | **216 文件 / 338,038,627 B** |
| 分区 | — | `(top)` 1 文件 170,917,333 B；`关键词/` **57 文件 / 107,503,941 B**；`选题/` **158 文件 / 59,617,353 B** |

口径差异的原因（CONFIRMED）：清单只统计直接子文件，递归口径包含 `_intake_backup/20261030_213936/`、`_intake_backup/20261031_101209/`、`_intake_backup/20260915_191045/`、`shadow/`、`case_assets/{A+,A,B,C,D}/` 等子目录。**这些 `_intake_backup/` 快照本身就是关键词清单的历史副本**（如 `story_keywords.txt` 12,216 / 9,460 / 4,366 词），不是独立资产。
`02_关键词` 内唯一 0 字节文件：`关键词/saturated_keywords_latest.txt`。清单交叉核对：216/216 文件的 bytes/sha 与 `asset_manifest.jsonl` 全部一致（`manifest_crosscheck_all_match=true`）。
最新 mtime 文件 `关键词/kw_typed_candidates.jsonl`（2026-11-07T21:34:26），最旧 `关键词/failure_keywords.txt`（2026-08-18T23:08:36）。

### 9.2 ROI 是什么：公式已被完全复现（CONFIRMED）

`02_关键词/kw_roi_history.jsonl`：170,917,333 B、**37 行＝37 个日快照**（一行一个 `{ts, n_words, words:{keyword:{…}}}`），UTF-8 严格通过、**0 行不可解析**、最长行 6,330,982 B；关键词观测 **765,554 条**，其中带 `roi` 键 **724,892 条**；时间跨度 `2026-09-26T09:10:07Z → 2026-11-07T13:07:10Z`，37 个不同日期跨 43 天，**缺 6 天**（10-17、10-19、10-20、10-22、10-23、11-06），文件内 ts 单调。

公式复现（`kw_probe_10_roi_final_formula`）：

```
roi == round( max(0.0, qavg * new_d / max(search_d, 1)) , 3 )
```

- 可测样本（`qavg/new_d/search_d` 都在）**265,607 条 → 精确匹配 265,607 条（100%）**；按小数位对比 round3 命中最高（265,607），说明存储就是 3 位小数。
- 不可测样本全部因为 `qavg` 为 null（459,285 条），且这些记录 `roi` 一律为 0 → **ROI 的 0 有相当一部分是"缺质量分"而不是"低回报"**。
- 确定性：26,181 组计数器元组完全相同的观测中，**0 组 ROI 不同** → ROI 是存储计数器的确定性函数，无隐藏输入。
- `search_d / new_d / elite_d` 与相邻快照的日间增量逐条相等（724,892/724,892 对，`kw_probe_06/08`）→ **delta 结构 CONFIRMED**；`dup_d / fails_d` 不存在于记录中。
- 但计数器自身不自洽（D4，CONFIRMED，765,554 条观测为分母）：`hits != unique + dup` 违例 **753,543 条（98.4%）**；`unique > hits` 违例 **14,330 条**；`elite > unique` 违例 **19,001 条**（即 `unique ≤ hits` 只在 751,224 条成立、`elite ≤ unique` 只在 746,553 条成立）。
- **ROI 不是平台真实表现**：ROI 文件按关键词字符串索引，**0 个 gid 形态键**（CONFIRMED）；是否有真实平台指标（阅读/曝光/CTR）曾经进入关键词管道 = **UNKNOWN**。`04_RAG反向评审/scripts/rag_gap_pipeline.py:233` 把该文件称为"用户需求"，但**没有生产代码存档**，写入者与调度 = UNKNOWN。

### 9.3 ROI 的区分力（CONFIRMED）：只有约 1% 的尾部有信号

`roi` 全观测（724,892）：min 0、**p1/p5/p25/p50/p75/p95 全为 0.0**、p99 23.235、max 1,167.571、mean 0.8911；**roi==0 共 698,513 条（96.4%）**，不同取值仅 15,580。最大快照（2026-11-01，28,427 词）里 roi 仍 **p95=0.0**、零值 27,535/28,020。
对照 `lc.score`（733,300 观测）：min 0、p25 35、p50 45、p75 66.7、p95 80.3、max 98、mean 50.37、无零值堆叠 → **`lc.score` 才有分布区分力，但其公式在存档代码中不存在（UNKNOWN）**。
相关性（CONFIRMED，`kw_probe_11`）：`roi vs hits` 0.085、`roi vs lc_score` 0.1445、`roi vs delta_sum` −0.049、`lc_score vs hits` 0.6099。`roi` 与 `lc.score` 完全相等的记录只有 **5 条**，不同 693,785 条 → 两个分数不是同一口径。
**判定**：旧"关键词 ROI"只能用于**尾部候选筛（约 1%）**，不能当作关键词质量的总体排序依据，更不能作为真实表现代理。

### 9.4 关键词全量与清单碎片化（CONFIRMED）

| 集合 | 唯一关键词 |
|---|---:|
| `kw_roi_history.jsonl` 全历史 | **28,922** |
| 其最新快照（2026-11-07） | 14,571 |
| 其最大快照（2026-11-01） | 28,427 |
| `kw_lifecycle_history.jsonl` | 29,656 |
| 所有文本清单并集（`story 14,896 / base 7,248 / tool 3,464 / discovered / blacklist / golden 713 / saturated_*.bak_r1594 18,035` 等） | **48,732** |

文本清单与 ROI 历史**严重不一致**（D7，CONFIRMED）：两者并集 **50,140** 个唯一关键词；**文本清单独有 21,218 个（43.5%）从未出现在 ROI 历史**，**ROI 历史独有 1,408 个不在任何文本清单**，交集 27,514。清单内部还有重复行（D15：`story_keywords.txt` 1,911、`discovered_keywords.txt` 1,636、`blacklist.txt` 263 条重复条目）与非词碎片（D8：5 字符尾部片段；blacklist 混入金融/营销词）。
池塌陷（D11，CONFIRMED）：ROI 关键词池 2026-11-01 的 28,427 → **2026-11-02 的 2,486** → 2026-11-07 的 14,571，**原因 UNKNOWN**（无日志/脚本解释）。

### 9.5 状态词表（CONFIRMED，实测词表，未假设）

读取键名：`state/status/tier/stage/lifecycle/verdict/decision/grade/level/bucket/next_state/prev_state/lc.state`。

- `state`：SLEEP 697,268 / ACTIVE 421,749 / DEAD 100,959 / NEW 23,190 / WEAK 2,465 / TESTING 25（来源：`kw_keyword_tree.json`、`kw_lifecycle_history.jsonl`、`kw_rank_daily.jsonl`、`kw_waste_daily.jsonl`）
- `lc.state`（ROI 文件内）：SLEEP 502,797 / ACTIVE 159,216 / DEAD 70,066 / WEAK 1,212 / NEW 9
- `level`：B 4,646 / A 3,814 / C 2,465 / A+ 1,393 / D 842 / null 836；`tier`：C 2,424 / B 1,389 / A 660 / D 435 / A+ 194
- 选题侧：`verdict` 直接通过 88 / 需修改 12；`grade` 普通AI文章 35 / ✅可发布案例 7 / 营销故事 4；`decision` watch 8 / quality_gap 6 / reject 2 / knowledge_gap 2 / auto_produce 2
→ 新系统 `keywords.status` 只有少量取值，旧词表需映射（SLEEP/DEAD/WEAK/TESTING → 待定义），且 `level/tier` 属机器评级，必须与人工判定分开存（见 §12.3、mapping §3.4）。

### 9.6 关键词↔文章可追溯性（CONFIRMED，很弱）

关键词→文章引用只出现在 `选题/` 侧，**5,844 个唯一 gid**（`07_tmp/kw_referenced_gids.jsonl`，5,844 行/5,844 唯一；1 个非 19 位数字为形态异常 D14）。出现位置计数：`kb_reality_scan.jsonl` 23,036、`experience_cards.jsonl` 2,443 ＋ `guard.provenance.gid` 1,478 ＋ `guard_detail.provenance.gid` 1,478、`experience_cards.json / case_memory_index.json / case_assets/*/list.json` 各 400（A+ 282）。
`kw_roi_history.jsonl` 本身**不含任何文章引用**（0 个 gid 形态键）。相对语料侧 351,843 个 gid（§5），关键词资产只能反查 **1.66%** 的文章。

### 9.7 陈旧度与"声称≠存在"（CONFIRMED）

探针时刻 2026-11-13T14:57:00：最新文件 mtime 距今 **5 天 17 小时**；文件内最新时间戳 `golden_keywords.json` 2026-11-07T21:09:03(+08) 距今 **5 天 17:47**；最早内部时间戳 `选题/topic_decision_matrix.json` 2026-08-23 08:44:52。
`golden_keywords.json` 含**负聚合值**（`total_new = -14,124`、单词 `search = -95`），且 `golden_new_n=0` 而 `golden_elite_n=713`（D12，CONFIRMED）→ 该文件不能用于任何"新增关键词"统计。
`README_资产清单.md` 声称的 `02_关键词/writing_keywords_5000.jsonl`（0.5 MB）**不存在**于 13,378 行清单，也不在 `02_关键词` 的 216 文件中（D10，CONFIRMED）→ README 的清单可信度降级（见 §14 M-2）。
归档代码读取路径为 `d:/gzml/PC/_kw_roi_history.jsonl`（带下划线），与盘上 `02_关键词/kw_roi_history.jsonl` 不一致（D9）；`saturation` 规则据归档代码注释"从来没有生效过"（OD-055，SUPPORTED），对应 `saturated_keywords_latest.txt` 0 字节而 `.bak_r1594_20261011_230123` 有 18,035 词（D3/D2）。

### 9.8 口径修正与被推翻的假设（诚实记录，CONFIRMED）

- 关键词专项早期探针（`kw_probe_05/06`）里命名为 `*_latest_snapshot` 的字段其实用了**累计集合**，不是最新快照：其 `keyword_distinct_in_latest_snapshot = 28,922` 实为全文件并集。正确口径来自 `kw_probe_18`：最新快照（第 37 行）14,571 词、最大快照（第 32 行）28,427 词。**本报告全部采用修正后口径**；修订记录见 `07_tmp/audit_keywords_notes.md` §10、§12.6。
- 早期还曾假设 `kw_roi_history.jsonl` 因单行过大而"截断/损坏"（`kw_probe_03` 的窗口切片造成假损坏）、假设 ROI 与 `lc.score` 线性相关（`kw_probe_07` 拟合失败）、假设 ROI 有钳位上界（`kw_probe_08/09` 未证实）——均已被推翻，失败过程保留在 notes §10。
- 清单两两重叠（`kw_probe_19`，465 对，82 对交集为 0）：**`base_keywords.txt ∩ story_keywords.txt` 仅 259 个交集**（7,248 / 14,896）、`story ∩ tool` 123、`base ∩ tool` 69、`saturated_keywords_20260822 ∩ discovered_keywords.txt` **0**；`golden_keywords.json` 的 713 词 **100% 都在 roi 历史**（与 story 交集 483、与 base 126、与 tool 25）。→ 旧关键词"库"实为**互不相交的多个小池**，不存在单一权威清单。

### 9.9 关键词侧结论：复用 / 重算 / 排除

| 资产 | 判定 | 理由 |
|---|---|---|
| `kw_roi_history.jsonl`（28,922 词 × 37 快照） | **RECOMPUTE** | 公式与 delta 已 CONFIRMED，但 96.4% 零值、计数器不等式违例、无平台指标来源 → 只保留"日快照＋delta"作历史，`roi` 不作为新 `score` 输入 |
| `关键词/kw_keyword_tree.json / kw_lifecycle_history.jsonl / kw_rank_daily.jsonl / kw_waste_daily.jsonl` | **REUSE（状态需重算）** | 状态词表可映射，`DEAD/SLEEP` 需按新采集健康度重算 |
| `关键词/{story,base,tool,discovered}_keywords.txt` ＋ `_intake_backup/` 快照 | **REUSE（去重后）** | 并集 48,732；重复行/碎片必须清洗（D8/D15），`_intake_backup/` 只登记不导入 |
| `关键词/golden_keywords.json`（713 词） | **REUSE 作候选，禁用其聚合数** | D12 负值 |
| `关键词/saturated_keywords_latest.txt`（0 B）与 `.bak_r1594`（18,035 词） | **排除当前件，隔离 .bak** | 规则从未生效（D2/D3） |
| `关键词/blacklist.txt` | **REUSE（需人工核）** | 混入金融/营销词（D8） |
| `关键词/kw_typed_candidates.jsonl` 480,627 B | **REUSE（与备份同名不同内容，需选版本）** | §4 |
| `关键词/rag_gap_pool.json`、`选题/topic_decision_matrix.json / topic_generation_review / reality_baseline / reality_calibration` | **QUARANTINE**（与 §10 侧同源，明细未逐一实测 → §10.11 UNKNOWN） | 含机器 `tier/verdict/grade/decision`，需与人工判定分离 |
| `选题/` 与 `05_V4.5/选题_含经验卡/` | **只导一次** | 逐文件同 SHA（§2） |
| `factory.db.keywords` 28,171 行 / 17 列 | **RECOMPUTE** | §8.4、mapping §3.4 |

关键词侧仍为 UNKNOWN 的 7 项：`qavg` 定义（null 487,304/765,554）；`hits/unique/dup/fails/elite` 的真实计数语义；写入这三个文件的程序与调度；真实平台指标是否曾进入管道；`lc.score` 公式；2026-11-02 池塌陷原因；0 字节 `saturated_keywords_latest.txt` 的成因。


---

## 10. RAG 索引与经验卡质量（CONFIRMED，专项 9 个只读探针，产物 `07_tmp/audit_rag_experience.json` 48,677 B / `_notes.md` 18,357 B）

### 10.1 规模与口径

| 资产 | 实测 | 标签 |
|---|---|---|
| `03_RAG/rag/kb_tf.json` | 102,056,310 B；**7,177** 条文档 tf 数组；**8,556,773** 个 词→tf 对；唯一词 **530,761** | CONFIRMED（`rag_probe_02c_kbtf.json`） |
| `03_RAG/rag/kb_index.json` | 22,377,798 B；`total_docs=7,177`；df 词条 **530,761** ＝ idf 词条 **530,761**；`built_at=2026-11-07 21:31:18` | CONFIRMED，与 kb_tf 唯一词**完全吻合** |
| `03_RAG/rag/kb_docs.json` | 17,318,899 B；**7,177** 个对象 / 唯一 gid **7,176**；`body_preview` 仅 **447–500 字符**；`tier_v2` A+ 6,676 / A 500 | CONFIRMED |
| `03_RAG/训练数据/*.jsonl`（3 份） | 各 3,293,240 B / 507 行，**SHA256 完全相同**（`b3302938b059…`）；并集唯一 gid **395** | CONFIRMED |
| `04_RAG反向评审/` | 25 文件 / 10,146,088 B；JSONL 合计 **19,394 行**；最新 mtime 2026-11-07T21:06:53 | CONFIRMED |
| `rag_vs_baseline_r1..r5/articles` | 700 文件（§1 清单口径）；正文中 **0** 个 gid 型数字串 | CONFIRMED（文件数）／gid 命中 CONFIRMED 为 0，但每轮文件明细 UNKNOWN（时间盒） |
| 0 字节文件 | `03_RAG`＋`04` 下 **35** 个；加上 `02_关键词/关键词/saturated_keywords_latest.txt` 正好等于 §1 的全库 **36** 个 | CONFIRMED（两处口径互相印证） |

三件套在**同一代**（mtime 2026-11-07 21:31:15 / :23 / :53，`built_at` 夹在中间），且文档数、唯一词数彼此吻合 → **内部自洽 CONFIRMED**；备份侧 `C:\_KEEP_BACKUP\toutiao_ai_rag\kb_*.json` 是**上一代**（17,268,181 / 22,341,319 / 101,758,349 B，mtime 2026-11-06）→ 资产库副本更新，CONFIRMED。

### 10.2 最严重缺陷：检索索引没有文档主键（D-R6，high）

`kb_index.json` 的全部键都是词条（term→df/idf），`kb_tf.json` 是**按数组位置对齐**的扁平 dict 序列，两者**都不含任何 gid**（CONFIRMED，`document_id_keys_present=false`、`gid_field_present=false`）。后果：`kb_docs ↔ kb_tf` 的对应关系完全依赖顺序，任何一次重排／增删都会**静默错位**且无法检测。探针过程本身即为证据：首版因尾部括号处理得到 **7,176**，修正后 **7,177**（0 解析错误）——一个只差 1 条的错误无法被文件自身发现。**结论：旧索引不迁移，只作对照**（新系统 FTS5 bigram 重建，见 §12.2 / R-7）。

`kb_docs.json` 另有 1 个不合规主键 `3733195404`（10 位，非 19 位 gid，D-R7；与 §9 关键词侧出现的同一个畸形 id 呼应）。

### 10.3 gid 可追溯性（分母＝§5 的 351,843 唯一 gid，全部命中、无孤儿）

| 集合 | 唯一 gid | 占 351,843 | 未命中 |
|---|---:|---:|---:|
| `kb_docs.json` | 7,176 | 2.04% | 0 |
| `experience_cards.jsonl` | 1,597 | 0.454% | 0 |
| `04_RAG反向评审`（文本数字串启发式，非正式 gid 字段） | 2,437 | 0.693% | 0 |
| `03_RAG/训练数据` | 395 | 0.112% | 0 |
| `rag_vs_baseline_r1..r5` 正文 | 0 | 0% | — |
| **RAG 侧并集** | **8,291** | **2.356%** | 0 |

两两交集：kb_docs∩卡 1,070、kb_docs∩反向评审 1,991、kb_docs∩训练 134、卡∩训练 157、卡∩反向评审 21、训练∩反向评审 9。结论（CONFIRMED）：**旧 RAG 只覆盖语料的 2.4%**，且"生成文章无法回溯取材"（700 篇正文里 0 个 gid）。这与 §9 关键词侧引用覆盖 1.66%（5,844 gid）一致地指向同一问题：**旧链路的知识层与语料层之间几乎没有可验证的外键**。

### 10.4 经验卡：这是追加日志，不是成品集（D-R1／D-R2，high）

| 指标 | `experience_cards.jsonl`（5,498,484 B） | `experience_cards.json`（3,774,604 B） |
|---|---|---|
| 行/对象数 | **2,443 行**（0 行坏 JSON） | 声明 `total=1,597`，但 `cards` 数组实长 **1,586**（**自相矛盾 D-R1**） |
| 唯一 gid | **1,597**（同 gid 重复 **846 行**） | 1,586 |
| 经验体去重 | 唯一 payload **2,300** → 重复 **143 行** | 重复 **108 行** |
| schema | v2 1,643 / **无 schema（旧代）800** | v2 1,577 / 无 9 |
| `level` | null **836** / B 768 / A 612 / A+ 227（**无 C/D**） | B 751 / A 608 / A+ 227 |
| 其他 | `career` 非空仅 449 行；`reality` n=1,607 ∈ [60.0, 92.5] 均值 71.13；**带 `error` 字段 66 行** | `fallback_from_rows=9`（`fallback_dropped_error`） |
| 人工评审字段 | **未发现任何**（reviewer/审核/署名，探针只检索字段名 → 判定 UNKNOWN） | 同 |

四份跨层副本（`02_关键词/选题` 与 `05_V4.5/选题_含经验卡` 各 2 个文件，SHA 相同，共 7.3 MB 冗余，D-R12）；同目录还留有 `tpl_polluted.jsonl.bak`（污染版本）。**复用必须清洗**：按 gid 取最完整版本、剔除 error 行与旧 schema 行、经验体去重 → 候选有效卡 ≈ 1,597 gid（CONFIRMED 上界），实际可用量 UNKNOWN（836 行 `level=null` 是否半成品，UNKNOWN）。

卡片的 `level` 词表与训练标签词表**不可互换**（D-R11）：训练标签是 `A+_低热 / A_爆款` 这类组合（8 类，A+_低热 160 最多），卡片只有 A+/A/B。

### 10.5 向量落后卡片一代（D-R5，high）

`card_embeddings_bge_m3.json` 32,377,041 B：`bge-m3:567m@11434-inprocess-gpu(hf+torch cuda)`，dim 全部 1024，向量 **1,583** 条（分隔符计数 CONFIRMED；流式探针 1,582 因首向量键被头部截断少 1，已在 notes 记录）。向量内部时间 **2026-09-27 19:25:06**，卡片 `generated_at=2026-10-04 11:07:52` → **向量早于卡 7 天且条数少于卡**（1,583 < 1,586 < 1,597）。缺失向量对应哪些 gid：**UNKNOWN**（时间盒内未逐一 diff）。处置：**EXCLUDE**——新工厂不新增嵌入模型，历史向量只登记为证据，不迁移、不加载。

### 10.6 训练数据：三份同 SHA，只覆盖 395 gid（D-R10）

507 行 / 字段 17 个（`task_type/title/label/source/gid/hot_score/read_count/digg_count/comment_count/collect_count/publish_time/instruction/rag_meta/output/part/total_parts/length_tokens`），`rag_meta` 7 个子字段（`rag_confidence/same_domain/has_income_fact/career_match/injected/retrieved/filtered`）；`task_type` 全为 generate、`source` 全为 elite_pool；**无任何人工评审字段**。三文件 507 行**完全互相共享**（唯一行 0）→ 去重后只有 **1 份**价值。

一个正向发现：该文件携带 `read_count / digg_count / comment_count / collect_count / publish_time`（2026-08-22 抓取时点的头条平台计数）——这是**真实表现（platform metrics）的快照**，与 `hot_score/label`（机器判定）必须分开登记；但它是 2026-08-22 的冻结值，**不得回填进 `article_metrics`**（B3 未满足前保持空，§12.2）。

### 10.7 反向评审 gate 与对比轮：可直接复用的是规则，不是分数

`04_RAG反向评审` 的 gate 文件行数（CONFIRMED）：`rag_judge_scores.jsonl` 5,416、`card_hit.jsonl` 3,361、`rag_judge_gid_map.jsonl` 3,101、`unsupported_gate.jsonl` 2,504、`repetition_gate.jsonl` 1,924、`title_claim_gate.jsonl` 1,174、`writer_arm.jsonl` 847、`llm_scores.jsonl` 591、`repetition_gate.probe-archive.jsonl` 122、`title_promise_gate.jsonl` 131、`wealth_gate.jsonl` 65、`llm_stance_cache.jsonl` 151、`wealth_observe_history.jsonl` 7。这些 gate（不支持声明 / 重复 / 标题承诺 / 财富叙事 / 卡命中）是 **⑥L4 质量门禁的候选规则来源**（设计输入，CONFIRMED 存在；规则有效性 UNKNOWN）。

对比轮 `rag_vs_baseline_r1..r5`：r1–r4 的 `scores.jsonl / gen_progress / score_errors / score_progress` 全为 **0 字节**（D-R8）→ **对比实验的打分结果没有落盘**，700 篇文章只是文本样本；`v45_rules.jsonl` 亦 0 字节；`case_assets/` 目录**全库 0 命中**（D-R13，引用它的流程已断链）；`shadow/` 是 124 个 md（不是卡）。旧脚本清单（`_clean_cards.py / _check_cards.py / _rag_vs_baseline.py / _r5_stats.py …`）证明这些资产的产出方式，但它们**不迁入新链路**（只登记为证据）。

### 10.8 四代资产时间轴不同步（CONFIRMED，内部时间戳）

`kb_docs/kb_index/kb_tf` 构建于 **2026-11-07 21:31** ＞ 经验卡 **2026-10-04 11:07** ＞ 卡向量 **2026-09-27 19:25** ＞ 训练数据/对比轮 **2026-08-22…09-26**。即：**索引比卡新一个月，向量比卡旧一周，训练集比卡旧六周**，且都早于/晚于语料最新代（§9 关键词最新 2026-11-07 21:34）。任何"用旧 RAG 直接检索新语料"的假设都不成立 → 必须重建（§12.2）。

### 10.9 README 声称 vs 实测

| 声称（README 2026-11-13 11:40 写痕） | 实测 | 判定 |
|---|---|---|
| 「622 张经验卡」 | json 1,586 / jsonl 唯一 gid 1,597 / 行 2,443 | **口径失效**（D-R3，CONFIRMED）；622 的来源是 2026-08-10 的 P3.1 报告「622 张＝kb_docs v45 `case_reality_score ≥ 60` 的 622 篇」，是**另一代 kb_docs（v45 全量 1,411 篇）**的口径 |
| 「648 条经验记忆」 | `experience_memory.jsonl` 行数 **648** | **CONFIRMED 一致**（本轮唯一与 README 完全吻合的声明） |

→ §1 的结论加强：README 的规模描述不可作为验收依据；但 `README_资产清单.md`（2,636 B，2026-11-13 11:34/11:40）的作者仍 UNKNOWN（M-2）。

### 10.10 处置建议（只读审计给出的方案，不执行）

| 资产 | 处置 | 理由（证据编号） |
|---|---|---|
| `kb_docs.json` | **REUSE（作元数据候选）＋ RECOMPUTE（正文一律回语料 join）** | 只有 447–500 字符 preview（10.1）；gid 命中 2.04% |
| `kb_index.json` / `kb_tf.json` | **EXCLUDE（不迁移索引）** | 无主键、位置对齐、静默错位（D-R6）；新 FTS5 bigram 重建 |
| `card_embeddings_bge_m3.json` | **EXCLUDE** | 落后一代、条数不足（D-R5）；新工厂不新增模型 |
| `experience_cards.jsonl/.json` | **REUSE（强制清洗后）→ `knowledge_assets`** | 日志非成品（D-R1/R2）；`credibility` 只能标"机器生成"直到 M-6 人工确认 |
| `experience_memory.jsonl` | **REUSE 候选** | 648 行 CONFIRMED；与卡去重后入 `knowledge_assets` |
| `03_RAG/训练数据/*.jsonl` | **QUARANTINE（去重成 1 份）＋ 其中的平台计数字段单独登记为陈旧真实表现** | 同 SHA 三份、仅 395 gid、无人工评审（D-R10） |
| `rag_vs_baseline_r1..r5` | **QUARANTINE** | 打分未落盘（D-R8）；正文无 gid 无法回溯 |
| `04_RAG反向评审` gate 文件 | **REUSE 作 L4 门禁规则候选** | 13 个 gate / 19,394 行 CONFIRMED；有效性 UNKNOWN |

### 10.11 本轮 UNKNOWN（含时间盒未测项，诚实记录）

1. 卡片与经验记忆是否经过**任何人工复核**：未发现相关字段名 → UNKNOWN（M-6 仍未决）。
2. 836 条 `level=null` 的行是半成品还是失败回填 → UNKNOWN。
3. `kb_tf ↔ kb_docs` 位置对齐是否 100% 成立（无主键只能抽样）→ 抽样一致、整体 UNKNOWN。
4. `rag_vs_baseline` 每轮的文件数／md 数／jsonl 行数明细：探针字段留空（时间盒用尽）→ UNKNOWN（文件总数 700 取自 §1 清单，CONFIRMED）。
5. 缺失向量对应的具体 gid 集合 → UNKNOWN。
6. `shadow/` 124 个 md 是否人工撰写 → UNKNOWN。
7. 各资产**目录级**最新 mtime 汇总（`staleness.group_newest_mtime`）本轮输出为 null → UNKNOWN；但关键内部时间戳 CONFIRMED（10.8），且 §1 全库 mtime 分布可用。

探针自纠过程（见 `audit_rag_experience_notes.md` 第 12 节）：kb_tf 首版少计 1 条（02c 修正为 7,177，0 解析错误）；向量首版首向量键被头部截断少计 1（分隔符重计数 1,583）；probe 05 被误启动两次存在竞写（输入相同、结果一致，以最后写入为准）。**这些错误都发生在计数层，未污染资产库**（全程 `mode=ro`）。


---

## 11. 爆款预测模型可信度：隔离评估，禁止接入生产（CONFIRMED）

数据来源：`factory.db.model_runs`（54 行，全部读出，见 `07_tmp/factory_model_runs.json`）与 `05_V4.5数据资产分层/models`、`C:\_KEEP_BACKUP\cluster_models`。

### 11.1 训练来源与标签定义（CONFIRMED，来自 `notes` 字段原文）

典型 recipe：

```
v6.3(pos=elite,C=0.10,thr=0.26,min_df=2,seeds=[7, 42, 2026],na=1000,nb=1000,nf=500)
inc_auc=0.924 inc_f1=0.4096 inc_p100=0.18 inc_ndcg=0.9436 keep_old
```

- **正类标签是 `pos=elite`**，即旧流水线自己筛出的"elite"标记，而不是头条真实爆款。`articles.is_elite=1` 共 6,128 行；`hot_level` 由旧规则给出（低热/普通/热门/爆款 3,406），`is_viral=1` 只有 **37** 行。**标签是被预测对象自己生成的**（循环标签），因此该模型学的是"复现旧清洗器的口味"，不是"预测真实爆款"。
- `trainset` 列 54 行给出 **54 个不同的训练集快照**（`trainset_v1_<hash>`），但**没有任何时间切分、留出集、真实表现字段的记录**；`na=1000,nb=1000,nf=500` 只是评估分桶规模。
- `deployed=1` 只有 **1 行**（最新版本 `v20261105_0239`，ts 2026-11-04T18:39:05Z，notes `manual_deploy`），但 notes 里出现 `manual_deploy` 的有 **8 行** → **部署标志与部署说明互相矛盾**，无法确认历史上哪些模型真正上过线（UNKNOWN，需人工确认）。
- notes 里大量 `keep_old` 决策与 `thr` 参数被反复扫描（thr 0.18–0.34、C 0.10–0.40），说明这是**阈值调参循环**，不是模型能力验证。

### 11.2 指标可信度（CONFIRMED，54 次运行）

| 指标 | min | mean | max |
|---|---:|---:|---:|
| accuracy | 0.4196 | 0.5221 | 0.6250 |
| precision | 0.1543 | 0.1925 | 0.5000 |
| recall | 0.8973 | 0.9368 | **1.0000**（3 次正好 1.0） |
| f1 | 0.2647 | 0.3175 | 0.6667 |
| auc | 0.8050 | 0.8386 | 0.9264 |
| 训练集正例比例 | 0.7027 | 0.7593 | **0.8631** |

**54 次运行的 accuracy 全部低于"多数类基线"**（正例占 70%–86%，随机全预测正类即可得到 0.70–0.86 的 accuracy，而模型只有 0.42–0.63）。`train_n` 只有 4,709–6,704 条，远小于语料规模（19 万正文），且正类占绝对多数；`recall=1.0` 出现在 3 次运行（另一次 accuracy 0.625 / precision 0.5 / f1 0.667 的整数比可疑，像只有极少样本的测试集）。

### 11.3 判定（SUPPORTED/UNKNOWN）

- **可复用价值**：AUC 0.80–0.93 说明它在"区分旧 elite / 非 elite"上有稳定的**排序**能力，可作为候选特征或离线对照（SUPPORTED）。
- **不可复用**：它不能给出真实爆款预测。把 `hot_level=爆款`、`viral_level=潜力爆款`、`tier_v2=A+`、`is_elite`、`is_viral` 当作真实爆款标签一律禁止（红线）；这些必须归入"机器预测"类别并保留其生成版本（旧 recipe 文本）作为出处（CONFIRMED 做法）。
- **模型文件本体**：`05_V4.5数据资产分层/models/elite_nb_current.json` 48,057,512 B（与备份 `cluster_models/elite_nb_current.json` 字节一致）；备份里还有 58 个历史快照（2.23 GB）。本轮**未加载模型文件**，因此无法确认其内部特征名、词表版本与 `na/nb/nf` 对应关系（UNKNOWN，需专项隔离评估）。
- 时间可行性：语料发布时间在 JSON 层只有 4 周、库内到 12 年，但**没有任何真实表现时间序列**（`metric_snapshots`=0 行），所以"预测效果回测"目前无法在新系统里做，必须先接入授权真实指标（B3）。

---

## 12. 旧→新资产映射设计（不改代码、不导数据，仅设计）

新系统实际列名已从 `07_tmp/new_system_schema.json` 逐列读出（CONFIRMED）。每类目库 22 张表；`articles` 列为：`id, category, source, source_item_id, title, content, excerpt, author, published_at, url, url_normalized, fetched_at, content_hash, completeness, credibility, processor_version, cleaned_content, text_length, simhash, cluster_id, cleaned_at, clean_version`。

### 12.1 身份与血缘字段（用户要求的 7 项逐一落地）

| 要求字段 | 新系统现状 | 旧资产来源（实测） | 处置建议 |
|---|---|---|---|
| `article_uid` | **无此列**；只有 `source_item_id`（TEXT，可空） | `gid`：归档 291,088 / 库正文 190,555 / tt 58,931 | 新增列 `article_uid TEXT NOT NULL UNIQUE`；旧资产生成规则 `legacy_tt_g:<gid>`，新链路 `n:<category>:<source>:<source_item_id>`。**不得由 url 生成**（tt 有 14 条缺 url，且 url 跨源冗余率 52.9%） |
| `source_url` | 已有 `url`、`url_normalized`（有 `idx_articles_url_norm`） | `url`（tt 58,917、clean 49,581、info 28,511、scored 15,514） | 直接映射；url 只作去重提示，不作主键 |
| `content_hash` | 已有 `content_hash NOT NULL` + `idx_articles_content_hash` | 本轮已算归一化 SHA256（跨源唯一 193,452） | 迁移时按**新系统口径**重算写入 `content_hash`；审计口径（去全部空白）另存到导入账本，两套哈希不可混用 |
| `category_id` | 已有 `articles.category` + `system.db.categories(id,slug,display_name,db_path,status,created_at)` | 旧 `primary_type` 6 类（叙事形态） | 语义不等价 → **M-1 人工映射表**，未决前不得写入类目库 |
| `captured_at` | 已有 `fetched_at NOT NULL`（抓取时间） | `body_time`（正文抓取时间，ISO，tt/clean/info/scored 全有） | `fetched_at ← body_time`；`published_at ← publish_time`（epoch→ISO）。注意库内 `publish_time` 最早 **2014-05-13**（历史回填），导入需做时间合法性校验 |
| `source_asset_id` | **无此列**，也没有导入批次账本 | 资产清单 `07_tmp/asset_manifest.jsonl`（13,378 行含 path/bytes/mtime/**sha256**） | 新增 `articles.source_asset_id`（值＝`<相对路径>@<sha256 前 12 位>`）＋ `articles.import_batch_id`；并新增 `import_batches` 表（见 12.5） |
| `credibility` | 已有 `credibility NOT NULL`（当前**全部 `unverified`**）；`completeness NOT NULL`（当前全部 `full`） | 旧资产无任何人工核验记录 | 迁移后 `credibility` 保持 `unverified`；`completeness` 改为枚举 `full / summary_only / truncated_8k / no_body`（依据 §6.2、§8.3 实测：摘要冒充正文 9,080/8,801 条、库内正文硬截断 8,000 字符） |

另外可用列：`simhash + cluster_id + article_clusters(cluster_id,category,representative_id,member_count)` 承接跨源近重复（本轮测得正文归一化重复 55.4%）；`clean_version / processor_version / cleaned_at / cleaned_content` 承接清洗版本，导入统一写 `processor_version='legacy-import-v1'`；`text_length` 承接正文长度（用于质量分层）。

### 12.2 四类数据必须分开存放（红线）

| 数据类别 | 旧资产字段（实测） | 新系统落点 | 明确禁止 |
|---|---|---|---|
| **原始事实** | `title, body, author(去重 30,673), publish_time, url, images/image_count` | `articles.title/content/cleaned_content/author/published_at/url/images→（无列，需另存）` | 把 `full_title`（150 字符拼接标题）覆盖 `title` |
| **人工标签** | **旧资产中不存在**（无任何字段代表人工判定；`is_junk / confidence / signals / secondary` 均为机器字段，覆盖 28,511/49,594 条） | 应落 `evaluation_history.evaluator='human:<id>'`（现有列可承载）＋建议新增 `human_verdicts` | 把机器字段当人工标签导入 |
| **机器预测** | `viral_level`(tt 41,468 条)、`hot_level`(库 53,678)、`tier_v2`(库 53,678)、`is_elite=6,128`、`is_viral=37`、`hot_value/interaction_rate/content_value/article_value/creator_bonus/creator_level/title_bait/age_hours/read_velocity`、`quality_avg/hot_avg/keyword_score` | `viral_labels(article_id,label,confidence_tier,score,reason,features_json,labeled_at)` ＋ `prediction_records(id,category,subject,predicted_score,basis,predicted_at,outcome_score,outcome_at,notes)` | **`viral_labels` 现在没有"人工/机器"来源列与模型版本列** → 必须先加 `origin` + `model_version`（否则与人工判定混表，违反四类分离）；禁止把这些值写进 `article_metrics` |
| **真实表现** | `read_count/digg_count/comment_count/collect_count`（非零率 55%–64%，且只是抓取时点快照） | **只进** `metric_snapshots(article_id,captured_at,reads,likes,favorites,comments,impressions,data_source)`，`data_source='legacy_snapshot:<asset_sha>'`、`credibility='unverified'`、`captured_at=body_time` | **禁止写 `article_metrics`**（它是"当前值"语义）；禁止用快照生成 `viral_labels.label`（现在 189 行全是 `unscored`/`missing_data`，这是正确状态，必须保持） |

### 12.3 关键词资产映射（§9 已定稿；完整列级映射见 mapping §3.4）

实测补充（§9）：新库 `keywords` 只有 **17 个词**（technology 4 / finance 4 / social 3 / history 3 / health 3，全部 `origin='seed'`；status：active 9 / gap 8），而旧资产有 48,732 个不同关键词文本、28,922 个出现在 ROI 历史中的词。缺口即 M-11/M-14 的决策对象。

| 旧 | 新 | 说明 |
|---|---|---|
| `factory.db.keywords` 28,171 行（`word, source, synced_at, parent_keyword, state, search_count, success_count, empty_count, consecutive_fail, quality_avg, hot_avg, elite_count, hot_count, keyword_score, last_success, next_test_time, last_state_change`） | `keywords(id,keyword,category,origin,status,created_at)` ＋ `keyword_performance(keyword_id,collected,quality_articles,avg_text_length,score,last_used_at,updated_at)` | 新表 `origin` 现值全为 `seed` → 导入写 `origin='legacy'`；`state`→`status` 词表需映射（§9 已探状态词表）；`keyword_score/quality_avg/hot_avg/elite_count/hot_count` 属**机器预测**，只能进 `keyword_performance.score` 且必须带来源标记 |
| `02_关键词/kw_roi_history.jsonl` 170,917,333 B（37 快照 / 765,554 观测，资产库独有） | **新系统无对应表** | §9 已定稿：公式 `roi=round(max(0,qavg*new_d/max(search_d,1)),3)` 265,607/265,607 复算成立，但 96.36% 为 0、与命中相关性仅 0.085 → 建议新增 `keyword_observations`（M-11）只作历史快照＋delta，**不喂给新 `score`**；是否导入待人工确认 |
| `02_关键词/选题/*`（与 `05_V4.5/选题_含经验卡` 完全同 SHA） | `knowledge_assets` / `keywords` | 只导一次，两个路径登记为同一 `source_asset_id` 的两个别名，避免重复计数（§2） |

### 12.4 RAG / 经验卡映射（§10 实测后定稿）

`knowledge_assets(id,category,kind,statement,evidence_json,support_count,confidence,updated_at)` 的候选来源与实测规模（CONFIRMED，§10）：`experience_cards.jsonl` 2,443 行 / 1,597 唯一 gid（清洗前）、`experience_cards.json` 1,586 对象、`experience_memory.jsonl` 648 行、`03_RAG/rag/kb_docs.json` 7,177 对象（但只有 447–500 字符 preview）。

三条硬约束（来自 §10.2／§10.3）：

1. `evidence_json.article_uids[]` **必须非空**才允许入库。旧资产里可反查的 gid 只有：卡 1,597、kb_docs 7,176、训练 395、反向评审 2,437（并集 8,291，占语料 2.356%）→ 预计**多数卡无法直接给出正文级证据**，需人工决定是否降级为"待补证据"（M-6／M-15）。
2. `kind` 枚举仍需人工定义（M-6）；`credibility` 在人工确认前只能是 `machine_generated`（实测未发现任何 reviewer/审核/署名字段）。
3. **旧 RAG 索引一律不迁移**：`kb_index.json` / `kb_tf.json` 无文档主键、纯位置对齐（D-R6），`card_embeddings_bge_m3.json` 落后卡一代（D-R5）→ 检索能力由新系统 FTS5 bigram 重建，旧文件只登记为证据（§10.10）。

`04_RAG反向评审` 的 13 个 gate 文件（19,394 行）作为 **⑥L4 质量门禁的规则候选**登记，不直接执行旧脚本。

### 12.5 写作追溯（每次写作可复盘）

现有能力（CONFIRMED）：`writing_history(draft_id,category,topic,version,content,model_id,created_at,status)`、`evaluation_history(id,draft_id,version,evaluator,criteria_json,score,issues_json,created_at)`、`evidence_links(id,draft_id,claim,article_id,quote,overlap,credibility,created_at,source_url,checks_json)`、`prediction_records`（`predicted_score` → 后续 `outcome_score/outcome_at` 回填真实表现）。

缺口（设计新增，轻量 4 张表，不改现有表）：

1. `draft_used_articles(draft_id, article_uid, role=材料/对照/否决依据, used_at)`
2. `draft_used_cards(draft_id, asset_id, kind, used_at)`
3. `draft_used_keywords(draft_id, keyword, origin, used_at)`（现有 `keyword_performance.last_used_at` 只留最后一次，不足以复盘）
4. `rejected_facts(draft_id, claim, article_uid, reason, reviewer, rejected_at)`（过渡期可用 `evidence_links.checks_json` 承载，避免立即改代码）

真实表现回流：`draft → 发布 → articles(新采集/回灌) → metric_snapshots → prediction_records.outcome_score`，只有 `data_source` 为授权真实指标时才允许回填（B3 未满足前保持空）。

### 12.6 导入批次账本与回滚（必须先于任何导入）

新增表 `import_batches(batch_id TEXT PK, source_asset_id TEXT, asset_sha256 TEXT, category TEXT, mode TEXT('dry_run'|'commit'), started_at, finished_at, rows_read INTEGER, rows_written INTEGER, rows_skipped INTEGER, skip_reasons_json TEXT, note TEXT)`；`articles.import_batch_id`、`articles.source_asset_id` 两列。

回滚三层：
1. **行级**：`DELETE FROM articles WHERE import_batch_id=?` ＋ 同批删除 `articles_fts` 对应行，随后 `INSERT INTO articles_fts(articles_fts) VALUES('rebuild')`（新库可写，类目库当前 118 KB–1.79 MB，成本可忽略）。
2. **文件级**：导入前 `VACUUM INTO '07_tmp/backup/<cat>/articles.db.<batch>.bak'`（对**新系统**库执行；资产库永远只读），回滚＝整库换回副本。
3. **批次前置**：所有导入默认 `mode='dry_run'` 只写账本与统计，人工确认后才 `commit`。

---

## 13. 迁移风险与回滚（含缓解与判据）

| 编号 | 风险（证据） | 缓解 / 回滚 |
|---|---|---|
| R-1 | **覆盖现有库**。新系统现有 212 篇文章、189 行 `unscored` 标签、17 个关键词、9 条知识资产，是唯一的"干净小样本"；一旦批量导入即被淹没 | 一律经 `import_batches` + `mode='dry_run'`（§12.6）；回滚＝按 `import_batch_id` 删除 ＋ `VACUUM INTO` 副本还原；**绝不**直接改资产库（只读，CONFIRMED 未写入） |
| R-2 | **正文硬截断**。`article_bodies` 正文 p99＝p100＝**8,000 字符**，而 JSON/归档正文最长 53,148 / 66,364 | 同一 gid 取"最长非空正文"，来源优先级：`corpus_tt/派生 JSON > corpus_archive 分片 > article_bodies`（仅当库内更长时取库内）；`len==8000` 标 `completeness='truncated_8k'`；写入 `source_asset_id` |
| R-3 | **摘要冒充正文**。`corpus_info` 8,801 条（30.9%）、`corpus_clean` 9,080 条（18.3%）、`article_bodies` 17,455 条正文 < 200 字符 | 标 `completeness='summary_only'`，L4 门禁禁止进入取材池；不得计入"有效文章" |
| R-4 | **`body_status` 陈旧**。`corpus_clean` 有 8,879 条状态为 `empty` 但正文非空（8,860 条带 `body_from`） | 完整性一律按正文内容＋长度判定，不采信状态字段；状态字段仅作旁证 |
| R-5 | **标签污染**。`viral_level / hot_level / tier_v2 / is_elite(6,128) / is_viral(37) / hot_value / read_velocity` 全是机器产物；54 次模型运行 accuracy 全部低于多数类基线（§11.2） | 先给 `viral_labels` 加 `origin/model_version` 再导；真实指标只进 `metric_snapshots`；`viral_labels.label` 保持 `unscored` 直到 B3 |
| R-6 | **类目错分**。旧 6 类是叙事形态，新 5 类是主题领域（§7） | M-1 未决前只做 dry_run 统计；social/history（当前 0 篇）不接受自动导入 |
| R-7 | **新系统 FTS 已不同步**：technology 137 篇 / FTS 62（75 未索引）＋4 条悬空 FTS 行；合计 78 未索引 | 属 P1「修 analyze」范围（待批准后修）；导入收尾必须 `INSERT INTO articles_fts(articles_fts) VALUES('rebuild')`；修复前不要用 FTS 计数验收导入 |
| R-8 | **磁盘与临时件**。项目 ≈1.31 GB ＋ 资产 4.88 GB ＝ 6.19 GB；`07_tmp` 当前 298,896,337 B（116 文件，主体是临时索引 `asset_index.db`）；正文文本合计约 0.8 GB（420,735,040 ＋ 280,872,293 ＋ 106,007,916 字符）→ 导入后类目库约 1–1.5 GB 量级 | 只写 `D:\ttxt`；禁止第二份语料拷贝；临时件只在 `07_tmp` 并在审计收尾时清理（保留清单与报告）；D 盘 free 103.93 GB（14:17 实测），但 11:56→12:50 曾减少约 7.9 GB，原因 UNKNOWN（U-6），导入前先复核 |
| R-9 | **Git 风险**。`D:\ttxt\资产库`（13,378 文件 / 4.88 GB）不在 `.gitignore` 内 | 绝不 `git add -A`；是否加 ignore 规则＝M-5（需批准） |
| R-10 | **时间覆盖窄**。四个 JSON 语料 `publish_time` 全在 2026-10-10 → 2026-11-07（约 4 周）；库内混有 2014-05-13 起的回填 | 模型/爆款评估不能声称跨期验证；回填 `publish_time` 需合法性校验，但不因此否定正文的检索价值 |
| R-11 | **归档收益被高估**。`corpus_archive` 1,297,334,855 B 中正文长度 **p50 = 0**，非空正文唯一 113,155；README 只写"约 63 MB"（低估） | 归档按"291,088 个 gid 清单＋部分正文补全源"估值，不按字节；分片内 117 个跨分片重复 gid 需按 gid 合并 |
| R-12 | **备份独有资产缺失**。`cluster_models` 58 个快照 / 2,228,719,658 B、`toutiao_ai_数据` 3,006 文件 / 200,368,372 B（含 `training_data_clean_filtered.json` 55,560,135 B）、旧代 `kb_*` 与旧代 `corpus_tt.json` 只在 `C:\_KEEP_BACKUP` | 若要隔离评估模型，从备份**只读**取；C 盘 free 56.43 GB，**不在 C 盘写任何文件**；处置决定＝M-4 |
| R-13 | **运行时争用**。当前存活进程：Strata 引擎 PID 9136 ＋ `serve.server --engine strata --port 8080` PID 14752、新系统 `ttxt serve --port 8765` PID 29724/30760（2026-11-12 19:50 起）；旧爬虫/Worker 无残留（CONFIRMED） | 迁移/审计脚本不得调用模型或 GPU；不与 Strata 并发；不重启任何服务 |
| R-15 | **旧检索索引不可迁移**：`kb_index.json` / `kb_tf.json` 内**没有任何文档主键**，与 `kb_docs` 只有位置对应（D-R6）；一旦按序号搬运就会永久静默错位 | 索引一律不迁移（§10.10），检索由新系统 FTS5 bigram 重建；`kb_docs` 若入 `knowledge_assets` 必须按 gid join 回语料 |
| R-16 | **经验卡是追加日志不是成品集**：2,443 行 / 1,597 唯一 gid / 143 行重复 payload / 836 行 `level` 为空 / 66 行带 `error`；`experience_cards.json` 声明 1,597 与数组实长 1,586 自相矛盾（D-R1/R2） | 导入前强制清洗（按 gid 取最完整版本）＋ `credibility='machine_generated'`＋人工判定（M-6）；两张表分别记录，禁止把行数当卡数 |
| R-17 | **陈旧真实表现混入风险**：`03_RAG/训练数据/*.jsonl`（同 SHA 三份 / 仅 395 gid）内含 `read_count / digg_count / comment_count / collect_count / publish_time` 的 2026-08-22 冻结快照（D-R10） | 一律 QUARANTINE，**不回填 `article_metrics`**；只作历史参考，登记时间戳（M-16） |
| R-18 | **"RAG 提升质量"缺证据**：`rag_vs_baseline_r1..r5` 的 `scores/gen_progress/score_errors/score_progress` 多为 0 字节（D-R8），5 轮对比文件里 **0 个 gid**（§10.7）→ 任何提升幅度都无法复算 | 新流水线的 A/B 必须由 L4 门禁＋人工判定重新产生，旧对比轮只登记为"未完成的尝试" |
| R-14 | **游离的 demo 库**。`01_categories/demo/articles.db`（397,312 B，articles 45、viral_labels 33、keywords 4，`schema_version=p2-v1`）**未注册**在 `system.db.categories`（仅 5 个） | 禁止纳入迁移；先人工处置（M-9） |

---

## 14. 需要人工确认的项（本轮不做决定）

| 编号 | 事项 | 本轮证据状态 |
|---|---|---|
| M-1 | 旧 6 类（泛资讯/行业分析/真人故事/赚钱案例/工具推荐/工具教程）→ 新 5 类（technology/finance/social/history/health）的映射表，含"无法归类"处置 | 分布已实测（§7）；语义不等价，需人工表 |
| M-2 | `资产库/README_资产清单.md`（2,636 B，2026-11-13 11:34 创建）是否人工所写——它是只读资产目录里唯一的今日写痕 | 文件与时间戳 CONFIRMED；作者 UNKNOWN |
| M-3 | `model_runs.deployed` 只有 1 行为 1，但 notes 里 `manual_deploy` 有 8 行 → 历史上哪些模型真正上线 | 计数 CONFIRMED；事实 UNKNOWN |
| M-4 | 备份独有资产（模型快照 2.23 GB、旧代生产产物 200 MB、`training_data_clean_filtered.json` 55.6 MB）处置：复用 / 隔离评估 / 放弃 | 规模 CONFIRMED |
| M-5 | 是否把 `D:\ttxt\资产库` 加入 `.gitignore`（需批准，影响 13,378 文件） | 未 ignore，CONFIRMED |
| M-6 | 经验卡 / 经验记忆是否含人工判定、能否作为"人工标签"入 `knowledge_assets` | §10 实测：卡 jsonl 2,443 行 / 1,597 gid，json 声明 1,597 vs 实长 1,586，**未发现任何人工评审字段**（字段名检索 CONFIRMED）；"是否经人工判定"本身仍 UNKNOWN；README「622 张」口径已失效（D-R3） |
| M-7 | **136,877 条孤正文**（有正文无 articles 行）是否回收元数据、按什么优先级回收 | 计数 CONFIRMED（§8.3） |
| M-8 | 8,000 字符截断正文是否一律用 JSON/归档长版替换（可能引入不同抓取时点的版本差异） | 截断 CONFIRMED；取舍待定 |
| M-9 | `01_categories/demo/articles.db`（45 篇，未注册）保留还是清理 | 实测 CONFIRMED |
| M-10 | `资产库/01_语料库/corpus_archive/runtime/` 4,616 个 0 字节 `.lock`（进程已不存在）是否视为陈旧、由谁清理 | 文件数 CONFIRMED；陈旧性 SUPPORTED |
| M-11 | 是否新建 `keyword_observations` 表承载旧 ROI 历史（37 个日快照 / 765,554 观测）；现 `keyword_performance` 一词一行、无时间维度 | §9 规模 CONFIRMED；DDL 实测 CONFIRMED（`07_tmp/kw_ddl.py`） |
| M-12 | 机器分值是否需要来源列（`keyword_performance.score`、`viral_labels` 都没有 `origin/model_version`）；不加列则只能靠导入账本标记 | 现库 DDL CONFIRMED；`roi` 96.4% 为 0 且与真实表现无关（§9） |
| M-13 | 关键词 `status` 词表：现库只有 `active`(9)/`gap`(8) 且无 CHECK 约束；草案新增 `testing / dormant / retired`（旧 SLEEP/DEAD/WEAK/NEW/TESTING/ACTIVE 的归一化） | 实测 CONFIRMED（`07_tmp/kw_status.py`）；新增值需批准 |
| M-14 | 关键词↔文章引用只覆盖 1.66%（5,844 / 351,843）：它是历史事实的全部，还是允许导入后重算 | 引用名单 CONFIRMED（`07_tmp/kw_referenced_gids.jsonl`） |
| M-15 | 旧卡/经验记忆在**只有 2.356% 可反查正文**的情况下，是否允许以"待补证据"身份进 `knowledge_assets`（否则几乎全部只能隔离） | §10.3 覆盖率 CONFIRMED |
| M-16 | 训练数据里 2026-08-22 抓取的 `read/digg/comment/collect_count`（陈旧真实表现快照）是登记为历史参考，还是完全隔离不入 `metric_snapshots` | 字段存在 CONFIRMED（§10.6） |
| M-17 | 污染与存根文件（`tpl_polluted.jsonl.bak`、`shadow/` 124 个 md、35 个 0 字节打分文件、`case_assets` 断链引用）保留为证据还是登记为废弃 | 存在性 CONFIRMED（§10.7）；作者与用途 UNKNOWN |
| U-1 | 跨来源去重后的"有效文章（正文非空且 ≥200 字符）"总数 | **UNKNOWN**：需一次带正文长度索引的流式扫描（约 30–40 分钟，临时件 ≤ 数百 MB），单来源数字已 CONFIRMED（§6.2） |
| U-2 | `is_junk / confidence / signals / secondary` 的取值分布（质量分层要用） | **UNKNOWN**：本轮探针未统计；只需再扫 `corpus_info.json`（198,727,562 B，约 5 分钟） |
| U-3 | `elite_nb_current.json`（48,057,512 B）内部特征名/词表版本/与 `na,nb,nf` 的对应 | **UNKNOWN**：本轮未加载模型文件（隔离评估要求） |
| U-4 | `corpus_archive` 分片内 117 个跨分片重复 gid 的具体形态（追加还是改写） | 计数 CONFIRMED；形态 UNKNOWN |
| U-5 | `trainset_v1_<hash>` 54 个训练集快照的构成与是否含真实表现字段 | 快照名 CONFIRMED；构成 UNKNOWN |
| U-6 | D 盘 11:56 → 12:50 可用空间减少约 7.9 GB 的外部写入来源（本轮 `07_tmp` 仅占 239 MB） | **UNKNOWN**；14:17 实测 free 103.93 GB |

### 本轮不做的事（边界自证）

未导入任何数据、未改动任何数据库或资产文件、未修改业务代码、未启动爬虫/Worker/模型、未向 Strata 发送请求、未新增模型/Agent/浏览器框架、未把旧模型预测当真实爆款标签、未以文件数量代替有效文章数量（§6/§12 已给出按 gid 与正文长度的口径）。P13-A 到此为止：**等待 M-1…M-10 与 U-1…U-6 的人工答复与批准，才进入批次 C（最小迁移设计）**。
