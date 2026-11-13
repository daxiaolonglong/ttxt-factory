# P13 批次0 本机基线核验报告(2026-11-13 上午)

- 执行人:DSH NEXT(本机 `D:\ttxt`)
- 性质:**只读取证 + 一次备份 + 一次完整测试运行**。除本报告与 `07_tmp/`、`99_temp/` 取证脚本外,未修改任何生产代码、数据库、配置。
- 时间基准:2026-11-13 08:47–08:58(Asia/Shanghai)
- 前置备份:`11_backups/P13-batch0-baseline-20261113-085637.zip`(2 116 611 字节,由 `scripts/backup.py` 生成)
- 本报告所有数字均为本轮实测,不是对 P12 报告回执的复述。凡引用 P12 报告处均标注「P12 回执」。

---

## 1. Git:本地与远端一致,工作树干净(实测)

| 检查 | 命令 | 实测结果 |
|---|---|---|
| 本地 HEAD | `tools\git\cmd\git.exe -C D:\ttxt status -b --porcelain=v1` | `## main...origin/main`,**无任何脏文件行** |
| 本地分支 | `.git/refs/heads/main` | `bef4d6385af40687f5fc80870f1ec6e965a5f874` |
| 上次 fetch 的远端跟踪引用 | `.git/refs/remotes/origin/main` | 同上 `bef4d638…` |
| 领先/落后 | `git rev-list --left-right --count origin/main...main` | `0 0` |
| **实时远端** | `git ls-remote <origin+token> main` | `bef4d6385af40687f5fc80870f1ec6e965a5f874` |

结论:本机 `main` = GitHub 远端 `main`(实时握手,非缓存),工作树无未提交改动。P12 五批提交(`7c1074d`/`ed326fc`/`90c68eb`/`a0234ea`/`39058be`)与清理提交 `bef4d63` 均在本机历史中,顺序与用户远端审查一致。

> 备注:本机 `git` 不在 PATH,取证通过 `tools/git/cmd/git.exe` 完成(README 已记录该约定)。

## 2. Strata 与模型基线:**当前只有一个服务模型**(实测,直接推翻「Q4/Q3 双模型」写法)

| 项 | 实测 |
|---|---|
| `GET /health` | 200 `{"status":"ok","model":"qwen3.8-flash-next-iq3_s","loaded":true,"max_context":147456,"api_key":false}` |
| `GET /v1/models` | **仅 1 个模型**:`qwen3.8-flash-next-iq3_s`,`status.value=loaded`,`n_ctx=147456` |
| 进程 | `strata` PID 26032,自 2026-11-12 19:38:59 运行;`strata-vision` PID 26644 |
| GPU | Tesla V100-SXM2-16GB,显存 **16001/16384 MiB**,利用率 0%(取证时刻无推理) |
| `ttxt doctor` | **10/10 通过**,`strata.health: served_model=qwen3.8-flash-next-iq3_s context=147456 loaded=True` |
| `model_jobs` | 70 条全部 `done`;`job_type`:writing 64 / chat 6;**时间窗重叠对数 = 0**(串行成立) |

裁定:README 第 11 行的模型描述**与当前实机一致,不需要改成 Q4/Q3**。所谓 Q4/Q3 是磁盘上的模型包,不是当前服务能力;`/v1/models` 只暴露一个已加载模型。
**连带结论:P13 计划里「同一素材 Q4/Q3 串行盲测」这一条目前不可执行** —— 它需要切换 Strata 模型,而「不动 Strata 的版本、参数、模型切换和服务生命周期」是 P13 硬约束。该项必须先由你明确解除该约束或提供另一台推理端,否则应从 P13 批次3 中删除,不能留在计划里假装可做。

## 3. 测试与体检(本轮亲自执行)

- `python -m pytest 10_tests -q --color=no` → **161 passed in 25.76s**(2026-11-13 08:56:40–08:57:07)。这是本轮实测,不再是 P12 回执。
- `ttxt doctor` → 10 ok, 0 failed(见 §2)。
- 注意:测试运行会写入共享日志 `09_logs/ttxt-20261113.jsonl`(demo 类目、pytest 临时 gate.lock)。测试与生产共用日志文件,长跑巡检时需注意区分来源;生产 `model_jobs` 未被测试污染(测试后 `MAX(id)` 仍为 70)。

## 4. 黄金集 v1:你的批评成立,且比报告口径更严重(实测)

`10_tests/golden/golden_set_v1.json` 实测:

| 项 | 实测值 | 说明 |
|---|---|---|
| 卡片总数 | **57** | `meta.card_count=57` |
| 卡片按类目 | technology **54** / finance **3** / health **0** | ⚠️ 与「科技 29、财经 25、健康 0」不是同一口径 |
| 语料文章数(`meta.corpus_articles_used`) | technology 29 / finance 25 / health 0 | 你表格里的 29/25/0 是**取材文章数**,不是卡片数 |
| 机器预裁与构造预期一致 | 52/57 | 仅规则吻合度 |
| **人工裁定** | `human_adjudicated=true` **0 条**;`human_verdict` 全为 `null` | 与你的判断一致 |
| 每卡来源数 | **全部 1 个**,且来源就是被引文章本身 | `evidence/sources` 计数:57 张卡均为 0 个独立来源 |
| 卡片字段 | `card_id, kind, expected_by_construction, category, article_id, source_url, claim, source_excerpt, machine{...}, note, human_adjudicated, human_verdict` | **无 `source_tier`、无 `corroborating_urls`、无 `fact_type`、无独立性字段** |
| 是否被测试消费 | **没有任何测试或 CLI 读取该 JSON** | 只有 `scripts/build_golden_set.py` 生成它;它目前不是「验收集」,只是一份候选清单 |

比口径问题更严重的是卡片质量本身(摘自 `08_wiki/reports/P12-batch4-golden-review.md`,机器结论均为 `verified`):

- `g002` 构造为 positive/verified:「现在18家大国企几乎全军覆没，痛心」
- `g003` positive/verified:「打脸了，72小时卖了10000台，酷派阔屏新机爆了」
- `g012` positive/verified:「兄弟们，今天是双 11 最后一天了，都买了些什么呀？」

这三条被判定 `verified` 的唯一原因是「原文里有这句话」。把这类卡片当正样本,训练/评测出来的是「规则能否匹配原文」,而不是「事实是否成立」。因此 **v1 的 52/57 不能外推为 L4 质量分数**,你的裁定正确。

## 5. 核验器四条不足的代码级确认(逐条落到行号)

`ttxt/writing/verifier.py` 的模块 docstring 自己已经写明这些边界(第 12–22 行),即代码作者与本审查结论一致:

| 你提的问题 | 代码证据 |
|---|---|
| ① 引句匹配靠词面重合 | `QUOTE_MIN_OVERLAP = 0.34`(第 32 行),`bind_quote` 用 `tokenize` 词集重合选最佳句(第 116 行起) |
| ② 数字/日期/人物/机构靠规则提取,会漏检误检 | `NUMBER_RE / CN_NUMBER_RE / DATE_RE / PERSON_RE / ORG_RE`(第 45–58 行);docstring 第 12–13 行承认「存在漏检与误检」 |
| ③ 因果只验证连接词共现 | `CAUSAL_RE`(第 59 行);docstring 第 13–14 行「因果仅验证连接词与绑定引句共现,不验证因果方向」 |
| ④ hedge 词把整句路由成观点,连数字也不再走事实门禁 | `OPINION_RE`(第 60–63 行)含「预计/或将/可能/或许/应该」;docstring 第 21–22 行承认「句中出现任一 hedge 词即整句路由到观点(即使含数字锚点)」 |
| **根级问题:没有来源可信度/独立性概念** | docstring 第 14 行:「规则不能证明事实为真,只能证明草稿陈述与来源文本一致;与来源本身是否为真无关。」`credibility` 只有 `verified/unverified/no_source/opinion`(第 16–20 行) |

**生产数据里的真实误检证据(finance/health 草稿):**
`health-20261113003349-32aa55` v1 的 critical 是
`事实未通过来源核验:利用可穿戴设备追踪数据……(orgs:是响应世卫组织)` —— 机构抽取把「**是响应世卫组织**」当成机构名,属于正则误切;该草稿最终 3 版都被拒(见 §6)。这说明核验器的规则缺陷会直接改变成稿结果,不只是「统计噪声」。

## 6. 真实写作记录:任务级 2/3、尝试级 2/5(实测 DB,非报告转述)

`writing_history` / `evaluation_history` / `evidence_links` 三类目实测:

| 类目 | 草稿(draft_id) | 版本数 | 终态 | 各版评分 | 证据可信度分布 |
|---|---|---|---|---|---|
| technology | `technology-20261113000422-5a990d` | 3 | **passed** | 0.65 / 0.65 / **0.95** | verified 3 |
| finance | `finance-20261113001849-dc9a4d` | 3 | rejected | 0.30 / 0.40 / 0.65 | no_source 4 |
| finance | `finance-20261113005130-cf7373` | 1 | **passed** | 0.95 | verified 4 |
| health | `health-20261113003349-32aa55` | 3 | rejected | 0.40 / 0.65 / 0.60 | no_source 5 |
| health | `health-20261113005555-e143da` | 3 | rejected | 0.85 / 0.85 / 0.85 | verified 2 / unverified 1 |

- **任务级(按选题)**:technology ✅ / finance ✅(第二次尝试)/ health ❌ → **2/3**
- **尝试级(按草稿)**:5 个草稿 2 个通过 → **2/5**
- 平均版本数:3.0(通过的两个分别为 3 版与 1 版)
- 拒绝原因分布:`no_source`(无来源断言)4+5 条占主导;`原文引句未绑定`+`orgs 误检`2 条;引用编号不一致 1 条

**两条必须写进 P13 的质量事实:**

1. `health-20261113005555-e143da` 连续三版评分 **0.85**(远高于 0.6 门槛),仍因「原文引句未绑定」被判 critical 而 rejected。即:**分数与事实门禁是两套判据,分数高不代表可发布**;这正是「模型分数通过 ≠ 真实事实通过」的实证。
2. technology 通过稿的 v1/v2 critical 记录显示,模型评审抓到了真实编造:「英伟达因内部治理危机导致股价剧烈波动…单日市值蒸发约1900亿美元」(因果无来源)、「苹果将于 2016 年永久关闭其支持社区」(时间线错乱)。**这两条如果被写进成稿,就是重大事实事故**;当前链路确实拦住了,但拦住依赖模型评审,而不是确定性核验器(核验器只报了 `causal:导致` 缺失)。

`workflow.py` 状态实测:只有 `passed` / `rejected`(`result["status"]`,`published: False` 固定),**没有任何人工确认状态位**。你说的 `AUTO_PASSED` / `HUMAN_APPROVED` 分离目前不存在,是待补的最小改动,不是新系统。

## 7. FTS 索引落后:根因是「只有全量重建、没有增量索引」(实测 + 代码)

按 `articles_fts.article_id` 对齐实测(不是 `rowid`,FTS 用独立列):

| 类目 | articles | 已索引 | 缺失 | 陈旧 id | 重复 |
|---|---|---|---|---|---|
| technology | 137 | 62 | **75** | 0 | 0 |
| finance | 50 | 47 | **3** | 0 | 0 |
| health | 25 | 25 | 0 | 0 | 0 |
| demo(测试库) | 45 | 45 | 0 | 0 | 0 |

- 缺失的 75 篇全部来自 `source=toutiao_tech`,id 区间 67…268,`published_at` 跨到 2026-11-13 02:00;finance 缺 3 篇(`eastmoney_rss` 1、`tmtpost_rss` 2,id 65/109/110)。
- **根因(代码级)**:`ttxt/analysis/rag.py` 只有 `rebuild_index()`(第 22–42 行:`DELETE FROM articles_fts` + 全量重插),`ttxt/crawler/store.py` 的 `insert_items()` 完全不写 FTS;唯一调用点是 `ttxt/analysis/analyze.py:31`(跑分析时顺带重建)与测试。所以**索引新鲜度=上次跑分析的时刻**,采集入库后若不跑分析,新文章永远搜不到。
- **对写作链路的直接影响(实测)**:在只读副本 `99_temp/p13_fts_probe/technology_copy.db` 上,用未索引文章的标题前 12 字检索:

| 查询 | 结果 | 目标文章是否被召回 |
|---|---|---|
| `美国全新X-70试验机亮`(id 257) | 10 条 | **False** |
| `大胆设想：华裔黄仁勋已辞`(id 156) | 7 条 | **False** |
| `黄仁勋离场！华人科技精英`(id 144) | 10 条 | **False** |
| `16GB+2TB！华为新`(id 72) | 7 条 | **False** |

  写作取材走 `rag.search`,所以 **technology 55% 的语料对写作者不可见**。这条欠账不是「检索质量略差」,而是 L4 素材面被截断 —— 与黄金集问题直接相关(取材面窄,候选事实也少)。

- 建议采纳你的方向:增量索引(insert/update 同事务写 FTS)+ 每类目一致性核验(`articles` vs `articles_fts` 计数与 id 集合),不要每次采集后无条件全库重建(technology 137 行全量重建虽快,但语义错误,且掩盖真实缺口)。

## 8. 磁盘:两个数字都不可复现,根因是浏览器档案缓存无界增长(实测)

本轮实测同一时刻的不同口径:

| 口径 | 占用 |
|---|---|
| 全项目所有文件(含 `.venv`/`.git`/`.pip-cache`) | **1 308 160 247 字节 = 1.218 GB**(与 `ttxt doctor` 的 `used=1.217GB` 一致) |
| 排除 `.venv` | 0.950 GB |
| 排除 `.venv`+`.git`+`.pip-cache` | 0.860 GB |
| 再排除 `09_logs`/`11_backups`/`99_temp`/`07_tmp` | 0.851 GB |

- **1.094 GB 无法用任何当前口径复现**;它与今日实测差约 124 MB。可复现的两个口径是 1.218 GB(全量,doctor 用)与 1.203 GB(`housekeep.MAJOR_PATHS` 白名单)。裁定:两个数差异 = **测量口径不同 + 测量时刻不同**,不是数据丢失。今后所有报告必须写「口径 + 排除项 + 时刻」。
- 真正的风险点:`02_crawler\.browser` = **697 240 656 字节(0.649 GB,占全项目 57%)**,其中 `component_crx_cache` 209 MB、`ProvenanceData` 177 MB、`Default\Cache\Cache_Data` 144 MB、`pw-artifacts` 40 MB。
- **观测缺口(代码级)**:`housekeep.clean_temp()` 只走 `99_temp`(第 63–80 行),浏览器档案不在 TTL/限额清理范围内;`MAJOR_PATHS` 只是把 `crawler` 计入统计。也就是说这块缓存会无界增长直到撞 20 GB 预算。P13 批次4 应加:浏览器缓存子目录的可清理白名单 + 限额告警(清理前须确认浏览器进程未占用;本轮未删除任何东西)。
- `housekeep` 已把 `pytest-of-*` 纳入统计并提供 `prune_pytest_dirs`(保留最近 2 个),本轮测试后自动清理 2 个,工作树保持干净。

## 9. 爆款预测:真实校准样本 = 0(实测确认)

各类目 `articles.db` 中 `article_metrics` 与 `metric_snapshots` **均为 0 行**(technology/finance/health/social/history 五个库都查了);`01_categories/*/metrics/` 目录为空。所以 P12 批次5「样本 0 如实回退 default」是真实状态,校准门槛(≥30 行授权真实指标、含曝光量、快照保留历史、按账号基线、按时间切分)仍是未闭环项。

## 10. 长跑与队列:新发现的两个小事实

- `crawl_jobs`:done 46 / aborted 7 / **queued 1**。`status='running'` 且租约已过期的任务 = 0(当前无悬挂执行)。
- **观测缺口**:queued 的 id 54(category=technology,`created_at=2026-11-13T08:51:42+08:00`,`owner=NULL`)在 `09_logs/ttxt-20261113.jsonl` 里**没有对应的入队日志条目**(08:50–08:51 无任何日志)。本轮我未执行任何采集/入队命令。代码侧 `store.py:311` 的 `INSERT INTO crawl_jobs(...)` 之后没有 `LOG.info`。结论:**入队路径缺日志埋点**,导致「谁在什么时候排了什么队」无法事后归因;P13 批次4 应补 `crawl.enqueued` 事件(带 job_id/source/category/actor),再谈 `run_next` / `run_job(job_id)` 语义区分。
- `model_jobs` 串行性:70 条、0 时间窗重叠(实测),与 P12 结论一致。

## 11. 文档事实错误(需修)

README 现状(第 66–79 行):阶段表只到 P11,**P12 五批未入表**;第 81 行仍写「第三批(多类目长跑、故障全矩阵、写作质量回归、磁盘压测)按计划未做」,与 P12 批次5 已做过 A–I 九轮长跑的事实冲突。第 11 行模型描述经本轮实测**正确**,无需改。

## 12. 批次0 裁定与下一批入口

**已核验通过(本机实测)**:Git 本地=远端、工作树干净;doctor 10/10;161 测试本轮亲测通过;模型队列串行、无并发;Strata 未被本轮任何操作触碰(取证时刻 GPU 0%、无推理、进程 PID/起始时间未变);备份已建立。

**已确认为 P13 必做(证据充分)**:
1. L4 黄金集重建(v1 无独立来源、无人工裁定、无 `source_tier`/`fact_type`/`corroborating_urls`,且没有任何测试消费它);
2. 核验器需要把「原文匹配 / 事实支持 / 来源可信度」拆成三个判据(现状只有第一个);
3. `AUTO_PASSED` 与 `HUMAN_APPROVED` 状态分离(当前只有 `passed`);
4. FTS 增量索引 + 一致性核验(75/137 缺失,已实测影响取材);
5. 浏览器缓存纳入限额与清理白名单(0.649 GB 无界);
6. 入队/关键状态变更缺日志埋点;
7. 磁盘口径统一定义(口径+排除项+时刻)。

**必须从 P13 计划里剔除或改条件**:Q4/Q3 双模型盲测(实机只有 1 个服务模型,切换模型与 P13 硬约束冲突)。

**未闭环(本轮无法完成)**:真实指标校准(样本 0,需你授权导出)、多日 soak(需连续运行)、人工裁定黄金集(需你逐条裁定)。

## 13. 回滚位置

- 备份:`11_backups/P13-batch0-baseline-20261113-085637.zip`(2 116 611 字节)
- 取证脚本(不入库,`.gitignore` 已排除 `07_tmp/`):`07_tmp/p13_b0_verify.py`、`p13_b0_verify2.py`、`p13_b0_verify3.py`、`p13_b0_verify4.py`
- 只读副本:`99_temp/p13_fts_probe/technology_copy.db`(FTS 取材实验用,可删)
- 本轮无数据库写入、无代码改动、无提交、无推送
