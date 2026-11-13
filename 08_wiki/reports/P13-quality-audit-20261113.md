# P13 质量闭环只读审计报告（2026-11-13）

生成时间：2026-11-13 20:4x（Asia/Shanghai）
执行方式：**只读**。所有数据库连接均为 `sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)`；本轮未执行任何 INSERT/UPDATE/DELETE/DDL，未创建临时表或索引，未启动迁移，未导入资产库，未修改既有 Git 提交。Strata 未被停止/重启/切换模型，只读访问仅限 `/health`、`/v1/models`（由被测代码自身探测，无停服操作）。
本轮边界：**资产冻结保护继续有效，P13 数据迁移不启动**；U-1/U-2 补测保持暂停。

## 结论摘要

| # | 工作流 | 判定 | 关键实测数字 |
|---|---|---|---|
| ① | 本地基线核对 | **CONFIRMED** | HEAD = 远端 `0b5893f`，`origin/main...HEAD` = `0 0`，工作区干净；本地复跑 **174 passed / 53.17s / exit 0**；最新备份晚于最后一次数据写入 |
| ② | L4 测试与人工裁定证据 | **不能宣称通过（NOT_AVAILABLE）** | 黄金集 v2 卡片 61，**已人工裁定 0**；闸门 3 项全失败；harness exit 1 |
| ③ | Q4→核验→Q3 串行盲测 | **串行 CONFIRMED / 盲测 NOT PASSED** | 70 个模型任务 **0 区间重叠**；生成与评审 **70/70 同模型**；跨模型评审实例 **0** |
| ④ | 跨类目重复 / 正文完整性 / 来源追溯 | **CONFIRMED（缺口定位完成）** | 跨库重复仅 technology↔demo **13**；短正文 **75/212 = 35.38%** 且全部标 `full`；未清洗/未 simhash **202/212 = 95.28%** |
| ⑤ | 最小必要代码修改影响面清单 | **已产出（未改动任何代码）** | 8 项候选改动：3 项纯代码、5 项需数据库写入批准 |

---

## ① 基线核对（CONFIRMED）

- `tools\git\cmd\git.exe rev-parse HEAD` → `0b5893f91e285ab69335959d99faa95c5f8d86a5`（"docs: P13 资产审计/映射/流水线方案与每日质检报告；资产库 4.9GB 入忽略"，2026-11-13 19:25:23 +0800）。
- `git ls-remote --heads origin` → `refs/heads/main` = 同一 SHA；`git rev-list --left-right --count origin/main...HEAD` → `0 0`（无未推送、无落后）。
- `git status --porcelain` → 0 行（工作区干净；`07_tmp/`、`资产库/`、`*.db` 均在忽略范围：`.gitignore:19/:28/:34`；被跟踪的 `.db` 文件数 0）。
- **本地测试复跑（真实执行）**：`.venv\Scripts\python.exe -X utf8 -m pytest 10_tests -q --basetemp=D:\ttxt\07_tmp\pytest_basetemp` → **174 passed in 53.17s，exit 0**（P12 batch5 记录为 161，此后 +13）。
- 备份（`11_backups`，7 份 zip）：最新 `P13-batch0-baseline-20261113-085637.zip`（2,116,611 B，11-13 08:56）。各库最后写入时间 technology 02:39 / finance 02:23 / health 01:35 / demo 00:36 / social·history 11-12 19:57 / system.db 08:51 → **最新备份覆盖全部数据写入**（CONFIRMED）。
- 数据规模（只读）：technology 137、finance 50、health 25、social 0、history 0（注册 5 类目合计 **212**）；未注册命名空间 `01_categories\demo` 45 → 物理行合计 **257**。

**诚实性更正**：已提交的 `08_wiki/reports/P13-baseline-recheck-20261113.md` §J5 写有「本轮未复跑测试」，该表述现已被本轮实测推翻（174 passed）。该文件已在 `022b91d` 提交，更正需新增提交（改写历史未获授权，故不做），**待你批准**。

## ② L4 写作质量：测试与人工裁定证据

**测试层事实（CONFIRMED）**：`10_tests\` 18 个测试文件；与 L4 直接相关的有 `test_p13_golden_v2.py`（断言：无人工裁定时 `quality_claim=NOT_AVAILABLE`；仅在满足全部闸门的 fixture 下才 AVAILABLE）、`test_p12_fact_verifier.py`（16 项，含引句逐字一致、扰动负例、`no_source`→critical）、`test_p10_quality_gate.py`、`test_p6_writing.py`。全部通过。

**证据层事实（CONFIRMED）**：
- `10_tests\golden\golden_set_v2.jsonl` = **61 卡片**，`human_adjudicated=true` **0 张**，`human_verdict` 非空 **0 张**，`human_confirms_tier`/`human_fact_support` 全空。
- 分布：类目 technology 25 / finance 19 / health 17；`source_tier` C 44 / A 17；`kind` positive 10、negative_perturbed 16、negative_fabricated_causal 10、opinion_attributed 10、negative_wrong_entity 9、unrelated 6；`source_text` 61/61 非空（引证可复核）。
- 闸门（`scripts\golden_v2_harness.py`，MIN_HUMAN_CASES=50、finance/health/technology 各 ≥10、A/B 级独立证据占比 ≥0.5）实测失败项与已提交状态文件完全一致：
  1) 已人工裁定 **0 < 50**；2) 类目有效卡片不足 `['finance','health','technology']`（各需 ≥10）；3) A/B 级独立证据占比 **None < 0.5**。harness **exit 1**。
- 「机器一致性 vs 构造标签」一致率 **55/61 = 0.902**（v1 为 52/57）。状态文件已明确注明：**这不是质量分数**。
- 卡片供给不是瓶颈：三类目卡片数（25/19/17）均已 ≥10，**唯一瓶颈是人工裁定工作量**（需 ≥50 条裁定，且每类目 ≥10）。

**判定**：L4「写作质量达标」**不可宣称通过**；现有证据只能证明 *确定性一致性核验*（代码级、可复算），不能证明 *事实正确性*（缺人工真值）。AUTO_PASSED 与 HUMAN_APPROVED 在库里也未被区分（见 ③）。

**需要你的动作（不依赖迁移、不写库）**：对 v2 卡片给出裁定，格式 `v2xNN: 通过` / `v2xNN: 驳回` / `v2xNN: 证据级=B`。收到后由我把裁定写入卡片文件（文本文件写入，不触碰资产库），再复跑 harness 出真值结论。

## ③ Q4→核验→Q3 串行盲测核查

**串行（调用级）CONFIRMED**——两条独立证据链：
- 代码：`ttxt\model_gate\gate.py` 文件锁（`O_CREAT|O_EXCL`，:66；持锁内 ≤10 job，:154-160），写作每次模型调用都经 `ttxt\writing\workflow.py:45 gate.submit_and_run`（生产入口唯一，无旁路 `client.chat`）；`model_gate\queue.py` 用 `BEGIN IMMEDIATE + UPDATE WHERE status='queued'` 行认领。
- 数据：`00_control\system.db.model_jobs` **70 行全部 done**，70/70 可测时间区间，**重叠区间 0**（我独立复算，非引用）。时间跨度 2026-11-12T12:37:51 → 2026-11-13T01:04:05。

**「盲评」不存在（CONFIRMED，判为 NOT PASSED）**：
- `result_json.model`：62 次 `qwen3.8-flash-next-iq3_s`、8 次 `qwen3.8-flash-next-unsloth-ud-q4_k_xl`；`job_type` writing 64 / chat 6。同批次内生成与评审同模型，**跨模型评审实例 0**（含 11-12 的 Q4 批次 job19–26）。
- `ttxt\writing\workflow.py:349` 硬编码 `"same_model_review": True` 并附独立性说明（这是诚实的自我声明，不是缺陷掩盖）。
- 落库无独立身份：`evaluation_history.evaluator` 恒 `model_review`（13/13），表内无 reviewer/model 列；`writing_history.model_id` **NULL 13/13**；`status` 仅 `passed`/`rejected`，**无 AUTO_PASSED / HUMAN_APPROVED 分离**。
- 「核验夹在生成与评审之间」= **SUPPORTED（推断）**：`verifier.py` 无日志/计时，`evidence_links.created_at` 只在轮末落库，核验阶段无独立时间戳。
- 核验实现是**词面级一致性**，不是事实核查：`bind_quote` 词面重叠 ≥0.34（`QUOTE_MIN_OVERLAP`，verifier.py:32）、引句上限 220 字；numbers/dates/persons/orgs 仅 `norm(x) in norm(source)` 子串包含（:172-176）；因果仅验连接词与绑定引句共现、**不验方向**（:178-186）。改写即可绕过词面匹配。
- 通过率口径（避免混淆）：版本级 `writing_history` 13 行 = passed 4 / rejected 9；轮次级 2/3（materials>0 的 3 轮）= 全样本 2/5；`evidence_links` 合计 19 条（technology 3 / finance 8 / health 8）；人工复核记录 **0**。

**闸门自身的两个真实风险（代码级，未测）**：`gate.py:55` 等待 timeout 600s < stale 1800s（:80 需 age>1800 **且** pid 失活才回收）→ 进程被硬杀后，等待方会在回收前 TimeoutError；pid 复用则 `_pid_alive` 恒真 → 锁可能永不回收；`release()` 只删自身 pid，否则仅 warn（:91-98），无续租心跳。测试只用同进程 threading + tmp 锁，**跨进程并发从未被度量**；生产 `default_model_call → gate` 路径无测试（测试注入 `_fake_model` 绕过闸门）；`test_p5_model_gate.py:111` 直连 `client.chat` 本身即旁路示例。

**结论**：`Q4 → 核验 → Q3 串行` 在「调用串行」意义上 **真实通过**；在「异模型盲测」意义上 **从未发生**。任何「盲测通过」的表述都必须撤回。

## ④ 跨类目重复 / 正文完整性 / 来源追溯（全部只读定位）

跨库重复（`07_tmp\xdb_dup_probe.py`）：
- 唯一有交集的类目对 = **technology ↔ demo**：`url_intersection 13`、`content_hash_intersection 13`、`simhash_intersection 0`（因 technology 侧 simhash 几乎全空）；来源 `sspai_rss 10 + ruanyifeng_atom 3`。其余所有类目对交集均为 0。→ 与 `quality-20261113.md` 的「13 篇」完全一致（CONFIRMED）。性质：`demo` 是未注册测试命名空间，属**命名空间卫生问题**，不是生产类目之间泄漏。
- 库内重复 URL（无唯一约束）：technology 1 个 URL → 3 行（多余 2）、finance 1 → 2、demo 1 → 30（quotes.toscrape 同页反复插入）。`content_hash` 重复 **0**（每库均有 UNIQUE 索引 `idx_articles_content_hash`）→ **去重只在 content_hash 层，`url_normalized` 无约束**。

正文完整性（CONFIRMED，最严重缺口）：
- `content` 长度 <200 字：technology **33/137 = 24.09%**、finance **17/50 = 34.0%**、health **25/25 = 100%**（最长 142、均值 91.7）、demo 41/45 = 91.11%；注册类目合计 **75/212 = 35.38%**（与日报 35.4% 吻合）。
- 但这 257 行 **全部** `completeness='full'`，包括全部 75 条短文本。根因是代码级：`ttxt\crawler\store.py:270` 写入 `"full" if item.content else "excerpt"` —— 该字段只表示"content 非空"，**没有任何完整性判定语义**（CONFIRMED）。
- `content == excerpt`：technology 28、finance 17、health 25、demo 35（合计 105/257）→ **摘要冒充正文**，health 最严重（25/25）。
- 新发现（口径澄清）：`text_length` 恒等于 `LENGTH(cleaned_content)`（257/257 相等，对 `LENGTH(content)` 相等数 0），而清洗从未运行的行其值为 NULL/0（technology 127 / finance 50 / health 25 / demo 12）。因此**任何用 `text_length` 衡量正文长度的结论都不可信**，必须用 `LENGTH(content)`。

清洗与近重复检测滞后（CONFIRMED）：
- `cleaned_content` 缺失、`simhash` NULL、`cluster_id` NULL、`text_length` 空 **四列同数**：technology 127/137、finance 50/50、health 25/25、demo 12/45 → 注册合计 **202/212 = 95.28%** 从未跑过 `ttxt\crawler\pipeline.py`（清洗→时间归一→simhash 聚类，写点在 `clusters.py:77`）。→ **近重复/相似聚类事实上不存在**，跨库 13 篇的 simhash 交集为 0 也由此解释。
- `credibility` = `unverified` **257/257** → 来源可信度分级从未入库（黄金集里的 A/B/C 只存在于卡片文件，不在库）。
- 来源追溯字段完好（正面结论）：`url`/`url_normalized`/`source`/`fetched_at`/`content_hash` 空值 **0**；`fetched_at` 区间 technology 11-12 13:00:47 → 11-13 02:39:02、finance 11-12 23:20:01 → 11-13 01:35:39、health 全部 11-12 23:22:44、demo 11-12 12:30:04 → 22:11:30（+08:00）。
- 关键词覆盖：注册类目 5 个但仅 9 个来源类目活跃、social/history **0 行**；爆款指标 222 条缺真实指标（沿用 P12 审计，未变更）。

## ⑤ 最小必要代码修改影响面清单（**未改动任何代码；每项都需你单独批准**）

行数均为预估，实施时以实测 Diff 为准；「需 DB 写」= 当前冻结期禁止。

| # | 位置（证据） | 现状 | 最小改动 | 需 DB 写 | 影响面 | 回滚 |
|---|---|---|---|---|---|---|
| C-1 | `ttxt\crawler\store.py:270` | `completeness` 只表示非空，75 条 <200 字仍标 `full` | 按实测长度写入 `full/short/excerpt`（阈值待 M-7），长度取 `LENGTH(content)` | 回填 212 行需批准 | 写 1 行；新增 1 常量；`doctor.py` 加覆盖率检查；新测试 1 个；Wiki：P12/P13 报告字段说明 | 备份 zip 已存（08:56）可回退 |
| C-2 | `store.py:249-278`（`INSERT OR IGNORE` + 无 URL 约束） | 同 URL 可插入多行（technology +2、demo +29） | **零 schema 方案**：插入前 `SELECT ... WHERE url_normalized=?` 预检，命中计 `duplicates` | 否 | 写 ~6 行；`test_p2_storage.py` 补 2 例（同 URL 不同内容 / 不同 URL 同内容） | 纯代码，git revert |
| C-2b | 同上 + 各库 `articles` 表 | 若要在 DB 层加 `UNIQUE(url_normalized)` | 需先清 32 条重复行 + DDL 迁移 + `SCHEMA_VERSION` 递增 | **是（禁止中）** | 5 库 DDL、迁移脚本、回填、测试、Wiki schema 章节 | 恢复备份 zip |
| C-3 | 类目隔离设计（物理分库）+ 13 篇 technology↔demo | 跨库重复无人检测 | `doctor.py` 增「跨库 url/hash 交集」检查项 + 采集入队前跨库存在性只读查询（命中则跳过并记 `duplicates`） | 否（只读查询） | ~35 行（1 helper + 1 doctor 项 + 调用点 1）；测试用 tmp 双库 fixture；Wiki 类目隔离章节 | git revert |
| C-4 | `ttxt\crawler\pipeline.py` / `clusters.py:77` | 95.28% 未清洗/无 simhash/无聚类 | **无需改代码**，只需运行 `ttxt pipeline`（或加 `doctor` 覆盖率告警） | **是（写库，禁止中）** | 代码 0；只加告警则 ~10 行 | 备份 zip |
| C-5 | `credibility` 257/257 `unverified` | 来源分级从未入库 | 来源表→tier 映射并在插入时写入（映射待 M-8） | 回填需批准 | ~15 行 + 映射常量 + 测试 2 例；`golden_v2_harness` 可改读库值 | 备份 zip |
| C-6 | `workflow.py:349`、`evaluation_history`（13/13 `model_review`）、`writing_history.model_id` NULL 13/13 | 自评自过 + 无模型身份 + 无 AUTO/HUMAN 分离 | 评审元数据（served_model、reviewer 标识）落库；`status` 增加 `auto_passed` 与 `human_approved` 两级 | 需 DDL + 回填（**禁止中**） | ~25 行代码 + 迁移 + 测试 3 例 + Wiki L4 章节；真正异模型盲评需第二个 served model，与你「不增加模型」的决定冲突 → 现方案保留 `same_model_review=True` 声明，把真值交由 ②的人工裁定 | 备份 zip |
| C-7 | `gate.py:55,80,91-98` | timeout 600 < stale 1800；pid 复用可致锁永不回收；无续租 | 参数化（`stale_s ≤ wait_timeout`）+ 持锁心跳续租 + 回收日志 | 否 | ~20 行；新测试：`stale>timeout` 与 pid 复用场景、跨进程 subprocess 双进程 | git revert |
| C-8 | `10_tests\conftest.py`（仅 6 行 sys.path） | 测试与生产共享闸门锁（`test_p12_worker_lease.py` 已自认）；`test_p7_dashboard.py:57-70` 直连生产库只读 COUNT | conftest 加 fixture：tmp `gate_lock` + tmp `system.db`；dashboard 测试改 fixture 库 | 否 | 仅测试 ~20 行，生产代码 0 | git revert |

**优先级建议（按你的 P0/P1）**：C-7/C-8（P0 串行可信度，纯代码零风险）→ C-2/C-3（P1 隔离与去重，纯代码零风险）→ C-1（P1 完整性语义，需 M-7 阈值 + 回填批准）→ C-5/C-4（需 M-8/M-9 与解冻）→ C-6（需 schema 决策，且与「不加模型」绑定）。全部 8 项均**不需要**新系统、新 Agent、新模型或 CloakBrowser。

## 需你裁定的决策点

- **M-7** `completeness` 判定阈值：`short`/`excerpt`/`full` 的长度界限（建议 <200=excerpt、200–800=short、>800=full），以及是否回填历史 212 行。
- **M-8** 来源可信度 tier 映射表（谁定 A/B/C，是否沿用黄金集的 17 条 A 级来源）。
- **M-9** 是否解冻一次 `ttxt pipeline` 运行（清洗+simhash+聚类回填；写库，须先出备份 + Diff + 实测日志）。
- **M-10** v2 黄金集 61 卡片的人工裁定排期（≥50 条即可过闸门，每类目 ≥10）。
- **M-11** 是否批准修正已提交报告中的「本轮未复跑测试」表述（新增提交，不改历史）。

## 复算与证据文件（只读产物均在 `07_tmp`，未进 Git）

- `07_tmp\xdb_dup_probe.py` / `xdb_dup_probe.json`（跨库、库内重复、完整性、追溯、任务终态全量测量）
- `07_tmp\clean_cov_probe.py`（清洗/simhash/聚类覆盖四列同数）
- `07_tmp\txtlen_probe.py`（`text_length` 语义 = `LENGTH(cleaned_content)`）
- `07_tmp\q4q3_verify.py` / `q4q3_verify.json`（70 任务 0 重叠、模型分布、13/13 evaluator、19 条 evidence_links）
- 复跑测试：`.venv\Scripts\python.exe -X utf8 -m pytest 10_tests -q --basetemp=D:\ttxt\07_tmp\pytest_basetemp` → 174 passed
- 黄金集闸门：`.venv\Scripts\python.exe -X utf8 scripts\golden_v2_harness.py`（当前 exit 1，符合预期）
