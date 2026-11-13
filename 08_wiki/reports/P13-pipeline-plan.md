# P13-B 新流水线建设计划（规划稿 · 未开工）

生成时间：2026-11-13（Asia/Shanghai）
性质：**规划文档**。本文件不改变任何运行时代码、数据库或资产；所有"开工"动作都要先取得人工批准（见 §10 批准清单）。
依据：`08_wiki/reports/P13-asset-audit.md`（§0–§14）、`P13-asset-audit.json`、`P13-asset-mapping.md`。引用数字沿用其证据标签 **CONFIRMED / SUPPORTED / UNKNOWN**，未在本文重新测量。

---

## 1. 本文要解决的问题与不解决的问题

解决：把 P13-A 测得的旧资产，映射进"一套工厂、七个模块"的目标架构，给出**批次顺序、验收等式、回滚方式、待批准决策**。

不解决（明确排除）：
- 不在本文写/改业务代码；不做 H1（analyze 接线）等代码修改的实现细节设计——那属于 P1 批准后的小设计稿。
- 不做任何导入。P13-A 的结论是"只读盘点"，导入是 P13-B 批准后才启动的独立批次。
- 不把旧爆款预测模型的输出当真实爆款标签（R-5、D-R10、§8）。
- 不新增模型、Agent、浏览器框架；不改 Strata Engine v0.1.58；不重启任何服务。

三条红线在本文中的落地形式：
| 红线 | 计划里的硬约束 |
|---|---|
| 不乱加功能 | 每批次只允许触碰 §4 列出的 schema 与 §5 列出的动作；新增表/列必须对应一个已批准的 M-x 决策，否则不得建表 |
| 不破坏现有资产 | 旧资产一律只读（`mode=ro`，legacy `factory.db` 加 `immutable=1`）；新写入只进 `00_control/system.db` 与 `01_categories/<slug>/articles.db`，且必须走 `import_batches` + `dry_run`（R-1） |
| 不拿测试通过冒充生产质量 | 每批次的验收是**数据等式＋人工判定**，不是 pytest 绿灯；`AUTO_PASSED` 与 `HUMAN_APPROVED` 分列记录（§7.4） |

---

## 2. 目标架构：一套工厂、七个模块

```
①关键词智能中心 ──候选词──▶ ②Scrapling 采集引擎 ──原始正文──▶ ③分类数据库与语料中心
        ▲                                                            │
        │ 反馈：有效词/无效词/新词                                    ├─正文+哈希+FTS─▶ ④爆款研究与 RAG
        │                                                            │
        └──────────────⑦反馈回流◀────判定与表现◀────⑥L4 质量门禁 ◀──取材与稿件──⑤Strata 写作引擎
```

| 模块 | 职责（一句话） | 主要落点（新库表） | 禁止事项 |
|---|---|---|---|
| ①关键词智能中心 | 出词、评词、淘汰词 | `keywords`、`keyword_performance`、（待批）`keyword_observations` | 不得把旧 `roi`/`lc.score` 写进 `score`（§9：roi 96.36% 为 0、与命中相关性 0.085，CONFIRMED） |
| ②Scrapling 采集引擎 | 抓正文、robots、限速、断点续采 | `articles`（正文/`content_hash`/`source_url`/`captured_at`） | 绝不调用 Strata；不抢显存；不与 Worker 并发跑模型 |
| ③分类数据库与语料中心 | 类目隔离、身份与去重、FTS 索引 | `articles`、`articles_fts`（bigram）、`article_metrics`、`metric_snapshots` | 不覆盖现有 212 篇小样本（R-1）；不在 legacy `factory.db` 写 |
| ④爆款研究与 RAG | 检索取证、爆款特征、真实表现分析 | `knowledge_assets`、`prediction_records`、`article_metrics` | 不迁移旧 `kb_index/kb_tf`（D-R6）；不使用旧向量（D-R5）；不把旧预测当真实表现 |
| ⑤Strata 写作引擎 | 取材 → 写作 → 核验 → 盲评 | `drafts`、`revision_log`、`evidence_links` | Q4 → 核验 → Q3 **严格串行**；Q4/Q3 并发数必须为 0；不改动 Engine v0.1.58 |
| ⑥L4 质量门禁 | 事实/证据/重复/标题党等硬闸 | `gate_results`、`evaluation_history` | 门禁规则只能由人工批准后启用（旧 gate 文件是**规则候选**，§3） |
| ⑦反馈回流 | 把判定与真实表现回灌①②④ | `keyword_observations`（待批）、`evaluation_history` | 回灌只写"事实＋人工判定＋真实表现"三类，机器预测单独列 |

---

## 3. 旧资产 → 新模块落点（摘要，完整见 mapping §2/§3）

| 旧资产（实测规模） | 处置 | 进哪个模块 | 关键前置 |
|---|---|---|---|
| 四个语料 JSON ＋ `corpus_archive`（gid 并集 **351,843**、唯一正文 **193,452**，CONFIRMED） | REUSE（清洗后分批） | ③ → ④ | M-1 类目映射、M-7 孤正文、M-8 截断正文、U-1 有效文章口径补测 |
| `factory.db.articles/article_bodies`（53,678 / 190,555，正文 p99=p100=8,000 截断） | RECOMPUTE（正文优先取 JSON/归档长版） | ③ | M-8 |
| 关键词文本清单（并集 **48,732**，其中 21,218 从未进 ROI 历史）＋ `factory.db.keywords` 28,171 行 | REUSE 词面＋RECOMPUTE 分值 | ① | M-11/M-12/M-13/M-14 |
| `kw_roi_history.jsonl`（37 快照 / 765,554 观测） | 历史登记（不喂 `score`） | ①⑦ | M-11 |
| `experience_cards.jsonl`（2,443 行 / 1,597 唯一 gid）＋ `experience_memory.jsonl`（648 行） | REUSE（强制清洗，`credibility=machine_generated`） | ④ | M-6、M-15 |
| `kb_docs.json`（7,177 对象 / 7,176 gid） | 仅元数据候选，正文回语料 join | ④ | D-R6/R7 |
| `kb_index.json` / `kb_tf.json` / `card_embeddings_bge_m3.json` | **EXCLUDE 不迁移** | — | 已定（§10.10） |
| `04_RAG反向评审` 13 个 gate 文件（19,394 行） | 门禁**规则候选** | ⑥ | M-17、人工批准规则 |
| `03_RAG/训练数据`（同 SHA 三份 / 395 gid）＋ `elite_nb_current.json` ＋ `model_runs` 54 条 | QUARANTINE／隔离评估，不接生产 | ④（只登记） | M-4、M-16、U-3 |
| `C:\_KEEP_BACKUP`（16,133 文件 / 7,065,010,198 B；独有 2,472,370,090 B） | 只读登记，不参与导入 | 登记簿 | M-4 |

---

## 4. Schema 需求（全部需批准，未批不建）

新增表：

1. `import_batches(import_batch_id PK, kind, source_asset_id, started_at, finished_at, mode('dry_run'|'apply'), rows_planned, rows_written, rollback_sql_path, note)` — 一切写入的前置账本（R-1 的缓解载体）。
2. `source_assets(source_asset_id PK, path, sha256, bytes, mtime, role, disposition, notes)` — 由 `07_tmp/asset_manifest.jsonl`（13,378 行，CONFIRMED）派生，**登记不等于导入**。
3. （M-11）`keyword_observations(keyword, category, snapshot_date, search_count, new_count, quality_avg, roi_raw, delta_json, source_asset_id)` — 承载 37 个 ROI 日快照；`roi_raw` 明确标机器口径。
4. （M-14）`evidence_links(kind, article_uid, target_kind, target_id, note)` — 关键词命中（`kind='keyword_hit'`，5,844 gid）与写作取证共用。
5. （待议 A-1）`evaluation_history(round, gate_name, article_uid, verdict, reviewer_type('machine'|'human'), at)` — 旧 19,394 行 gate 记录只登记为 `reviewer_type='machine'`。

新增列（现有表）：

6. （M-12）`keyword_performance.score_origin, score_model_version`；`viral_labels.label_origin` — 现库分值无来源列（CONFIRMED）。
7. （M-13）`keywords.status` 词表归一：`active | gap | testing | dormant | retired`，并加 `CHECK`；旧词表映射 ACTIVE→active、NEW/TESTING→testing、WEAK/SLEEP→dormant、DEAD→retired（mapping §3.4）。
8. （A-2）`articles.source_asset_id`、`articles.credibility` 必填化（身份七要素齐备才算有效文章）。

不改的东西：旧 `资产库` 内任何文件与数据库；Strata Engine v0.1.58；`01_categories/demo/articles.db`（未注册，M-9 前先冻结不动）。

---

## 5. 导入批次与验收等式（P13-B 批准后才执行）

每批次三件套：**dry_run 计数 → apply → 等式校验**；任一等式不成立即按 `import_batch_id` 回滚。

| 批次 | 内容 | 前置批准 | 验收等式（可机器校验） | 回滚 |
|---|---|---|---|---|
| B-AS-0 | 建 `import_batches`/`source_assets`，登记 13,378 行清单 | A-3 | 登记行数 == 13,378 且 `sha256` 空值 == 0 | drop 两表（新库无历史数据，安全） |
| B-AS-1…5 | 按类目导入 `corpus_clean/info/scored_v2_1` 代表文章 | M-1、M-8、U-1 | 写入数 == dry_run 数；`articles` 中 7 要素空值 == 0；`articles_fts` 行数 == `articles` 行数、无悬空 FTS 行 | 按 batch_id 删除＋`VACUUM INTO` 前的快照副本 |
| B-AS-2 | `corpus_archive`（291,205 记录 / 正文 p50=0）只补正文缺失项 | M-7 | 补全数 == (正文空 gid 集 ∩ 归档非空正文 113,155)；不新增 `articles` 行 | 同上 |
| B-AS-3 | `factory.db.article_bodies` 长版正文替换 8,000 截断 | M-8 | 替换后 `length(body) >= 8000` 的行数 == 计划替换数；两套哈希不混用（§12.1 审计口径≠新口径） | 保留原正文列副本 |
| B-AS-6 | `metric_snapshots`（各源互动计数） | M-16 | `article_metrics` 仍为 0 行；快照行必须带 `as_of` 时间戳 | 删快照表行 |
| B-AS-7 | 关键词：清洗后文本清单并集 ∪ `factory.db.keywords` | M-11/M-12/M-13/M-14 | `keywords` 唯一词 == 清洗后唯一词数；现库 17 词（active 9 / gap 8）不被覆盖；`origin ∈ {seed, legacy}` | 按 origin='legacy' 删除 |
| B-AS-8 | 经验卡／记忆 → `knowledge_assets` | M-6/M-15 | 行数 == 清洗后唯一 gid（≤1,597）；`evidence_json.article_uids[]` 非空率 ≥ 人工阈值；gid 必须存在于 `articles`；`credibility` 只能是 `machine_generated` | 按 source_asset_id 删除 |
| B-AS-9 | 模型登记（54 条 `model_runs`、`elite_nb_current.json`） | M-4 | `prediction_records` 新增 0 行旧预测；推理调用 == 0 | 删登记行 |
| B-AS-10 | 索引与 FTS 收尾 | — | `articles_fts` == `articles`；每类目 DB 独立校验通过；0 悬空 | 重建 FTS（可重复） |

体量与磁盘预算：单次导入前的 dry_run 产物、临时索引、`VACUUM INTO` 快照都计入 **20 GB** 上限；当前 `07_tmp` 已占 **286.5 MiB**（CONFIRMED，2026-11-13 18:5x 实测 215 文件 / 300,391,405 B），D 盘可用 103.93 GB。**禁止生成第二份数百 MB 的语料副本**（P13-A 约束延续）。

---

## 6. 优先级顺序（与用户确认一致）

**P0 资产盘点与映射收口（当前）**
- 已完成：P13-A 三份交付＋§10 集成；一致性复核（见 §9 复核记录）。
- 剩余：U-1（跨源"正文≥200 字符"有效文章总数，30–40 min 流式补测）、U-2（`is_junk/confidence/signals` 分布，约 5 min）。**P0 完成判据 = U-1/U-2 有 CONFIRMED 数字＋人工确认 M-1/M-7/M-8 的口径。**
- 不做：任何导入、任何代码修改。

**P1 修 `analyze`**（用户明确"不要马上开始 H1 代码修改"）
- 先出一页小设计：`analyze` 接线点、输入输出契约、失败可见化。旧 `pipeline_runs` 3,095 行中 **ok=0 只有 48 次（1.55%）、ok=1 有 3,047 次（CONFIRMED，`07_tmp/pr_ok_dist.py`）**——但这不代表旧流水线质量合格：`ok` 只表示"跑完"，旧链路没有任何质量闸（§8：模型全部低于多数类基线仍继续运行）。
- 验收：新增测试覆盖"失败必须中断或显式记录"；不引入新依赖。

**P2 修取材**
- 取材必须带 `evidence_links` 且可反查 `articles`；空证据取材直接进 L4 拦截。
- 验收：取材样本 100% 有 `article_uid` 级证据；无证据率 == 0。

**P3 爆款反馈与经验学习**
- 只有 `HUMAN_APPROVED` 判定＋`article_metrics` 真实表现能进反馈；旧机器预测只作对照。
- 验收：反馈记录 100% 带 `reviewer_type`；模型输入不含旧 `roi`／旧标签。

---

## 7. 质量门禁（L1–L4）与判定分离

7.1 **L1 结构**：7 要素齐备、哈希可算、类目正确、时间格式统一 → 机器，可自动。
7.2 **L2 内容**：正文长度阈值、重复/近重复（`content_hash` 唯一 193,452 / 冗余 55.45%）、垃圾碎片（D8）→ 机器。
7.3 **L3 证据**：`evidence_links` 可反查；旧资产侧仅 **2.356%** 可反查 → 新写的稿件必须 100%。
7.4 **L4 语义/编辑**：事实、标题承诺、重复表述——旧 gate 文件（`unsupported_gate` 2,504 行、`repetition_gate` 1,924 行、`title_claim_gate` 1,174 行等）只能作为**规则候选**，逐条人工批准后才启用；启用后仍分 `AUTO_PASSED` 与 `HUMAN_APPROVED` 两列，不得互相顶替。
7.5 旧 gate 证据缺口：`rag_vs_baseline_r1..r5` 打分文件多为 0 字节、0 个 gid（D-R8，R-18）→ **不得**引用其"效果提升"数字作为任何验收依据。

---

## 8. 运行时与资源约束（硬门）

| 约束 | 具体执行 |
|---|---|
| Strata 独占 | 其他模型/引擎一律不得启动；Q4 与 Q3 **不并发**；串行队列由 `00_control/system.db` 的任务表实现（新库现有 `crawl_jobs` 54 行：done 46 / aborted 7 / queued 1；`model_jobs` 70 行全部 done，其中 writing 64 / chat 6，CONFIRMED §11） |
| 现有进程不动 | Strata PID 9136、`serve.server --engine strata` PID 14752、`ttxt serve --port 8765` PID 29724/30760（CONFIRMED 存活）：不重启、不 kill |
| 爬虫与写作隔离 | ② 永不调用 Strata；Worker 不做 GPU 任务；采集与写作不同进程、不同资源池 |
| VRAM | 任何批次脚本不得占用显存（审计期实测探针 0 GPU 调用） |
| 数据库 | 新库 `journal_mode=wal`＋FTS5 bigram（CONFIRMED）；旧库只读 `mode=ro`（legacy `factory.db` 加 `immutable=1`）；禁止覆盖任何 DB 文件 |
| 磁盘 | 20 GB 预算；临时件只放 `07_tmp`；批次结束即清理账（B-AS-10 之后） |
| 网络 | 不向 Strata 之外的推理服务发请求；不新增浏览器框架（Scrapling 0.5.1 / Playwright 1.65.1 已在 venv，不升级不新增） |

---

## 9. P13-A 复核记录（本轮已做，全量）

复核方法：脚本 `07_tmp/p13_review_check.py`（占位符扫描、编号连续性、跨文件数字出现次数、JSON 标签分布）＋逐节人工读。

发现并修正：
1. §1 README 对照表仍有"622 张经验卡 … 见 §9（进行中）｜待测" → 改为 §10 实测结论（622 口径失效 D-R3；648 CONFIRMED）。
2. §1 语料 452 条差异的措辞从"待 §6 确认"改为"差异 CONFIRMED、成因 UNKNOWN"（§6 主表已记录）。
3. §5 两个反常、§5 结尾"待 §6 复核"→ 改为事实 CONFIRMED／成因 UNKNOWN，并明确 **U-1 未测得，193,452 不可直接当可导入量**。
4. §9.9 处置表 `QUARANTINE→待 §10` 改为已定处置＋指向 §10.11 UNKNOWN；修复该行列错位。
5. §12.3 标题"（待 §9 补齐口径）"→ 定稿，并补入新库仅 **17 词（active 9 / gap 8）** 的实测缺口（`07_tmp/kw_status.py`）。
6. §0 临时件字节自相矛盾（239 MB < 288 MB）→ 统一为实测 **215 文件 / 300,391,405 B（286.5 MiB）**，`asset_index.db` 275.1 MiB；并补两行专项探针证据（kw 24 脚本→17 份 json；rag 9 步→7 份 json）。
7. §13 新增 R-15（索引无主键）、R-16（卡是追加日志）、R-17（陈旧平台计数混入）、R-18（RAG 效果无证据）。
8. `P13-asset-audit.json`：接入 §10（`rag_experience` 17 个键）、关键词实测状态、M-6 改写＋新增 M-11…M-17、R-15…R-18、`pending_probes` 标 done＋列明 3 个 known_gaps；并把内嵌探针的非图例 `label`（`analysis`、`lc.score_line37`、`CONFIRMED(gap)` 等）统一为四值图例，原值保留在 `label_raw`（新增 `label_normalization_note`）。重跑后 239,917 B，标签分布 CONFIRMED 88 / UNKNOWN 19 / SUPPORTED 2，PENDING 标签 0。
9. mapping：§2 行 47/48/50/51/52 换成实测数字（含 kb_* 三件套计数、训练数据 395 gid、卡 2,443/1,597/1,586、记忆 648/648/624）；§3.5 定稿为字段级映射＋四条硬约束；§7 补 M-15/M-16/M-17；M-6 改为"字段未发现（CONFIRMED）／是否真经人工判定仍 UNKNOWN"。
10. 路径纠错：经验卡在 `02_关键词/选题/` 与 `05_V4.5数据资产分层/选题_含经验卡/`（跨层 SHA 相同），`experience_memory.jsonl` 另有 `05_V4.5数据资产分层/` 副本 —— mapping §2 已按 manifest 实测路径改写。

复核后仍为 UNKNOWN（不掩盖，见 §10.11 与 `pending_probes.known_gaps`）：每轮 `rag_vs_baseline` 明细字段、目录级 mtime 汇总、缺失向量的 gid 逐一 diff、`qavg` 语义、计数器不等式违例成因、2026-11-02 关键词池塌陷原因、452 条正文差异成因。

遗留诚实债（单独文件）：`08_wiki/reports/P13-baseline-recheck-20261113.md` §J5 写"本轮未复跑测试"，与事实不符（pytest 实际退出码 0，只是未复跑全量套件）——需在批准提交前改正。

---

## 10. 开工前必须得到的批准（不批则不动）

| 编号 | 决策 | 阻塞的批次 |
|---|---|---|
| A-3 | 批准建 `import_batches` / `source_assets`（B-AS-0） | 全部 |
| M-1 | 旧类目 → 5 类目（technology/finance/social/history/health）映射表 | B-AS-1…5 |
| M-7 / M-8 | 孤正文回收与 8,000 截断替换口径 | B-AS-2/3 |
| M-11 / M-12 / M-13 / M-14 | 关键词三张新表/列与 status 词表、引用 `kind` | B-AS-7、模块⑦ |
| M-6 / M-15 | 经验卡 `kind` 枚举、人工判定、证据非空阈值 | B-AS-8 |
| M-4 / M-16 | 备份独有资产与平台计数快照处置 | B-AS-6/9 |
| M-9 | `demo/articles.db` 处置（冻结/删除/注册） | 安全边界 |
| M-17 | 污染与存根文件（`.bak`、`shadow/` 124 md、35 个 0 字节打分件、`case_assets/` 断链 D-R13）保留或登记废弃 | 登记簿 |
| A-1 | `evaluation_history` 是否建表 | 模块⑥⑦ |
| A-2 | `articles.credibility` / `source_asset_id` 必填化 | B-AS-1 |
| A-4 | 是否授权补测 U-1/U-2（需要再建临时索引，磁盘与时间预算） | P0 收口 |

---

## 11. 里程碑（估算，供批准用，不自动执行）

| 里程碑 | 内容 | 估算 |
|---|---|---|
| G-P0-a | U-1/U-2 补测（若批准 A-4） | 35–45 min 机器＋10 min 写结论 |
| G-P0-b | P13-A 三份交付＋baseline 报告改正后提交 | 20 min（提交需批准；`资产库` 未列入 .gitignore，禁止 `git add -A`，M-5） |
| G-P1 | `analyze` 接线小设计 → 批准 → 实现＋测试 | 设计 1 h；实现视批准范围 |
| G-P2 | 取材证据链改造 | 待 G-P1 后小设计 |
| G-B | B-AS-0…10 导入（若批准） | 每批 dry_run＋apply＋校验；总量取决于 U-1 |
| G-P3 | 反馈回灌（仅人工判定＋真实表现） | G-B 之后 |

---

## 12. 本计划的自我约束检查（防止越界）

- 本文件是**计划**，不是实现：未新增/修改业务代码、未改 DB、未动资产库（只读探针除外）。
- 每个批次都绑定：前置批准 → dry_run → 等式 → 回滚，缺一不得执行。
- 任何"新表"都必须对应 §4 里一条已批准的决策；否则视为"乱加功能"。
- 任何旧数字进入计划时都带证据标签；UNKNOWN 数字不得进入验收等式（U-1 未测得 → B-AS-1 的等式只能用 dry_run 实测数，不用预估总量）。
