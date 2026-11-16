# 本窗口进展与待办(2026-11-16,第 5–8 轮 / P22→P25)

> 由我在 2026-11-16 12:20 按用户要求「整理本窗口的每一个对话进展和待办 / 直接写出一份干净的清单」生成。
> 规则 39(先整理再开工)+ 规则 25/26(每条待办带"怎么算完成")。**数字都带来源**,没来源的一律标"待取证"。
> 用户自己的周报 `08_wiki/reports/P13-weekly-20261116.md` 是只读凭据,本文不重写、不改写。

---

## 一、已推送(远端 `main` = 本地 `HEAD` = `b8b01cf`,三端一致)

| 轮次 | commit | 内容 | 凭据 |
|---|---|---|---|
| 第 6 轮 P23 | `2719fa1` | 身份判定收口、被拒插入可见、磁盘口径闭合 | [P23 报告](P23-identity-and-visibility-20261116.md) |
| — | `032fdb6` | pytest basetemp 保留份数 10→4,自动回收逐条打日志(规则 17/27/29) | 提交信息 |
| 第 7 轮 P24 | `b8b01cf` | 指标回流扩量、预测闭环首次真跑、两处口径根因(含规则 44、README 实况更正、conftest WAL 修正、`prediction.py` 候选规则) | [P24 报告](P24-metrics-and-prediction-loop-20261116.md)、[db_schema](../db_schema.md) |

`git show --stat b8b01cf` = 9 文件 / +394 −16。检查点里说"规则 44、README 未提交"是**记忆偏差**,已按实况更正:它们在这一次提交里,已推送。

---

## 二、本窗口修掉的根因(7 条,都不是表面修补)

1. **预测闭环空转**:候选条件 `v.article_id IS NULL`,而采集入口每轮都给所有文章打标签 ⇒ 永远挑不出候选;`LIMIT` 还排在过滤前。现规则 = 未实测才预测、反连接已预测、先排序后 `LIMIT`。→ `ttxt/analysis/prediction.py`
2. **已实测的文章被重复预测**(自欺):已 measured 不许再预测。→ 同上 + `10_tests/test_p24_prediction_candidates.py`
3. **conftest WAL 误报**:把"边车文件存在"当违规,把外部只读打开的副产物算到测试头上。现规则 = 受保护文件 sha256/行数一致才算数,只新增边车不算违规。→ `10_tests/conftest.py`
4. **P23/P17 待办口径写错**(把已做的事列成待办):按实况加带日期更正块,不删原文。
5. **README 口径过时**(说只有 12 篇有指标):实况更正为 85 篇 measured、其余 5 类目 0 指标因为库里没有头条文章。
6. **入队没有日志**(排队动作无法事后归因):`store.create_crawl_job` 现在写 `crawl.enqueued`,带 `job_id/source/category/actor`,`actor` 分得出 dashboard / scheduler / keyword_plan / manual。→ 本轮 P25,已实现待提交
7. **FTS 索引滞后没人能发现**:新增体检项 `db.fts_coverage`,滞后就 WARN 并给出补跑命令。→ 本轮 P25 `ttxt/doctor.py`
   实测滞后(本窗口测得):全库 **354/374 行进索引**,`social 22/33`、`technology 159/168`(与 P13 §6 一致;凭据 `07_tmp/r28_channels/p25_obs_green.txt`)。
   根因:采集入口只自动跑**清洗管线**,索引只有 `analyze → rag.rebuild_index` 重建,而调度器自 11-12 19:57 冻结。

---

## 三、本轮(P25 / 第 8 轮)已完成、**尚未提交**

| 文件 | 状态 | 说明 |
|---|---|---|
| `02_crawler/sources.json` | 改 | 新增两个**已取证**头条频道:`toutiao_finance`→finance、`toutiao_history`→history(browser/extractor=toutiao/限 2.0s/`max_articles=10`);notes 写清两级取证与 robots。JSON 校验:`sources 12 enabled 11` |
| `10_tests/test_p25_toutiao_channels.py` | 新增 | 4 个测试,先红(4 failed)后绿 |
| `ttxt/crawler/store.py` | 改 | `crawl.enqueued` 埋点(根因 6) |
| `ttxt/doctor.py` | 改 | `_fts_coverage_row()` + 注册(根因 7) |
| `10_tests/test_p25_observability.py` | 新增 | 4 个测试(埋点 2 + 索引覆盖率 2),红(3 failed)→ 绿 |
| `03_intelligence/keyword_feedback/demo-feedback-…T082843+0800.json` | 未跟踪 | **用户经看板点的分析**产生的反哺证据,不删、按证据提交;取证见 `07_tmp/r28_channels/serve_and_demo_feedback_note.md` |

定向测试:`30 passed`(P25 两个文件 + `test_c4_clean_coverage.py` + `test_p12_worker_lease.py`)—— 凭据 `07_tmp/r28_channels/p25_obs_green3.txt`,exit 0。
新线索:看板(`ttxt serve`,PID 27156/27264,端口 8765)是**常驻写入口**,用户点一下就往队列/反哺文件写;job 71(08:28:54)与这份反哺文件(08:28:43)只差一秒,**入队归因目前只能是推断,非实证**——这正是根因 6 补的东西。

---

## 四、生产实况(来源都标出来)

| 项 | 数值 | 来源 |
|---|---|---|
| 文章总数 | 374(technology 168 / finance 96 / demo 32 / social 33 / health 25 / history 20) | P13 周报 §2 |
| 进检索索引 | 354/374(滞后 social −11、technology −9) | 本轮体检实测 |
| 实测指标文章 | 85(全在 technology);标定样本 85;阈值 p90/p60/p25 = 0.0551 / 0.0261 / 0.0123 | P24 报告 §2/§4 |
| 预测闭环 | 已预测 40,已回填 16,MAE 0.0168(score 口径,阈值无关) | P24 报告 §5 |
| 其余 5 类目指标 | 0 —— 库里没有头条来源的文章,等头条后台导出 | P24 报告 §3 |
| 采集队列 | done 63 / aborted 7 / **queued 1(job 71:technology toutiao_tech,08:28:54,attempts 0,无 owner/lease)** | P13 §6 |
| 调度器 | 自 2026-11-12 19:57:35 冻结未跑 | P13 §6 |
| 黄金核验集 v2 | 候选 61 / **人工裁定 0** / 质量结论 `NOT_AVAILABLE`(机检一致率 55/61=0.902 不等于质量达标) | P13 周报 §4、`P13-batch1-golden-v2-status.md`(用户 09:37:55 重跑) |
| 写作通过率 | 任务级 1/5=20%;尝试级 4/13=30.8%;拒因:引用错绑、编造因果、假时间线、产品幻觉 | P13 §5 |
| 磁盘 | MAJOR_PATHS 21 桶 6.665 GB;`07_tmp` 0.974/2 GB;游离 `D:\ttxt\pytest-of-yangl` 53,235,934 B;D: 剩余 97.10 GB | P13 §7 |
| 测试基线 | 411 passed / 107.49 s / exit 0(全量) | P13 §8;我这边 109.69 s |
| `scripts/learning.py` | 不存在(曾有文档提到,属口径错误) | P13 §8 |

---

## 五、待办(我自己定的顺序,每条带"怎么算完成")

### P25 本轮收尾(现在往下走)

1. **全量回归** —— `pytest 10_tests` 必须 exit 0,预期 **420 passed**(411 + 9 个新测试)。跑之前**不许**并发实采/Worker/指标,且看板若被点会触发生产实况护栏,那属真实并发写入,记录不硬扛。
2. **P25-pre 备份** —— 备份 6 个类目库 + `system.db`,带时间戳清单。完成定义:备份文件存在且大小记录进报告。
3. **排空队列 + 实采两个新频道** —— 先跑 job 71,再给 `toutiao_finance`/`toutiao_history` 各入队 3 次,用有界 Worker:`ttxt worker --owner p25 --max-jobs N --max-runtime-s … --idle-stop-s …`。完成定义:`queued=0`;finance/history 行数增加;日志里每条入队都能看见 `crawl.enqueued` 与 actor。
4. **指标回流** —— `ttxt metrics --category finance/history --fetch-public --limit … --apply`,报告 `render_ok/render_mismatch/render_unattributed/…` 全套键。完成定义:measured 增加、无 unattributed 增长。
5. **标定 + 预测** —— 若某类目 measured ≥ 30(`MIN_CALIBRATION_SAMPLE`)才 `--calibrate`;然后 predict / settle / report。完成定义:预测记录 > 0 且有回填 MAE;不达标就不标定,写清原因。
6. **补索引,关掉 FTS 滞后** —— `ttxt analyze --category social`、`--category technology`(顺带产出关键词反哺)。完成定义:体检 `db.fts_coverage` = PASS,`374/374`。
7. **文档与规则** —— 新增 P25 报告;P17 追加 §十六(本轮台账);规则 45:**看板是常驻写入口**,与测试并发算真实写入,须记账;`db_schema.md` 补入队埋点与新体检项。完成定义:四份文件都改完,数字与实测一致。
8. **提交 + 推送** —— `push.cmd` → 远端 `main` 前进。完成定义:三端 commit 一致。

### 下一轮起(第 9 轮往后,已排好)

9. `ttxt crawl-enrich --category` —— health 25 行全是 `short`(均值 119.4 字),先补全再谈写作。
10. `ttxt schedule --once` 起步,再考虑解冻常驻调度(先解决"冻结无人知"的可观测性)。
11. **C-5 可信度分层** + 9 行 `evidence_links.article_id` 为 NULL 的 DDL 收口(finance 4 / health 5)—— 已获用户授权。
12. **`ttxt/analysis/keywords.py` 不读 `viral_labels`** —— 爆款结论从来没喂回关键词选择,这是目标第 2 条(模型学会爆款/垃圾并反哺关键词)最关键的缺口。
13. `_element_text` 标题缺陷 + `PROCESSOR_VERSION` 提升(规则 24:先红后绿)。
14. **写作质量两条门禁**:`verifier.py` 观点路由过宽、无锚数字与引用窗口可绕过;拒因清单见 P13 §5。
15. social/health 的头条频道 slug **必须渲染导航取证**才能加(绝不猜 slug)。
16. `07_tmp` 与游离 `D:\ttxt\pytest-of-yangl`(53 MB)清理 —— 已授权,但必须先出**逐项清单**再删(规则 27/29)。
17. P13 遗留:批次2 `verifier.py` 两条门禁;批次3 Q4/Q3 盲测(Strata 单模型约束下暂不可做);批次6 连续跑测最早 11-19。

---

## 六、只有用户能做的事(1 项,我不能代做)

**黄金核验集 61 张卡的人工裁定,当前 0/61。** 质量结论一直是 `NOT_AVAILABLE`,门禁(裁定 ≥50、三类目各 ≥10、A/B ≥0.5)一天不过,一天就不许宣称"质量达标"。
我可以代做的是:把 61 张卡压成一张可打印/可勾选的裁定单(题目、来源、机器判定、待填 是/否/存疑),让这件事从"打开一堆文件"变成"照着一页勾"。要我做就说一声。

## 七、用户已给的授权(2026-11-16 上午/中午两次)

- 「https://github.com/daxiaolonglong/ttxt-factory/ 是我们自己的仓库,每一轮完成后直接推送」→ 每轮直接 push。
- 「所有要人工授权的,不管是以前的或者以后的,我今天给你授权」→ 覆盖:evidence_links DDL、Worker 常驻、调度器、`pytest-of-yangl` 与 `07_tmp` 清理、C-5 分层、`crawl.enqueued` 埋点。
- 我**仍然不自动做**的:不删旧日志、不删旧备份、不改写用户写的报告;任何删除都要先出逐项清单(规则 29/43)。授权不等于我停止留痕。

---

## 八、更新(2026-11-16 13:3x,P25 已推送;第 1–7 节原文不删只更正)

**已推送**:P25 全部内容(第 2 节第 6、7 两条从"待提交"变为已提交推送)。完整账本
[P25 报告](P25-two-new-channels-and-fts-index-20261116.md) + [P17 §十六](P17-session-handoff-20261115.md)。

| 更正/完成项 | 实况读数 |
|---|---|
| 本窗口根因第 2 条 P25 部分 | **7 条根因全部闭合**,新增第 8 条:`articles_fts` 只在 analyze 里建 ⇒ 当天采的文章搜不到;修法=管线末尾重建索引 + `indexed` 进回执;红(3 failed)→绿(46 passed) |
| 语料规模(13:34 收账最终读数) | 374 → **412 行**;technology **186** / finance 106 / history 30 / social 33 / health 25 / demo 32;FTS **412/412**、清洗 **412/412**(中途曾报 403,是 analyze 前的读数) |
| 队列 | 排干 71/72/73 与反哺出来的 74/75(`actor=keyword_feedback`);收账时 `done 68 / queued 2`(job 76/77)= 反哺循环的产物,故意不再排干 |
| **新缺陷(未修,列为下轮 ⓪)** | 关键词反哺入队是**无界循环**:同一 gap 词(`效率工具` / `睡眠`)今天入队 4 次(job 74/75 → 76/77),health 一支 `inserted 0 / duplicates 25` 纯白烧。验收:同一 gap 词 24h 内不重复入队 + 红→绿测试 |
| **第二处同源缺陷(已修)** | `runner.py:452-458` 往回执/`meta_json` 拷贝管线统计是**键白名单**,新增的 `indexed` 被静默丢掉 ⇒ 修了但账上看不见。红 `KeyError: 'indexed'` → 绿 11 passed |
| 测试 | 全量 **422 passed / exit 0 / 137.57 s**(P25 新增 11 项;`indexed` 断言加在既有测试里) |
| 新频道 | `toutiao_finance` / `toutiao_history` 已注册并排干队列(job 72/73,`actor=manual` 可证),各 +10 行 |
| 指标与标定 | technology measured 85 → **101**,重标定 0.0562/0.0272/0.0115,`verified_viral` **10**;finance/history 各 **10 条 < 30** ⇒ 仍不标定(如实) |
| 预测 | finance/history 各 `recorded 20`(pending);technology 回填 16 → **19**,MAE **0.018** / bias **−0.0127** |
| 体检 | `ttxt doctor` **34 ok / 0 failed / 5 WARN**,新增 `db.fts_coverage` PASS |
| 测试 | 全量 **422 passed / exit 0 / 138.26 s**(P25 新增 11 项);本文第 3 节"P25 应新增 9 项/总数 420"是**清单数错**,实际 8 项 / 419,后补 FTS 3 项 = 422 |
| 磁盘 | `07_tmp` **0.635 GB**(限额 2 GB)、根目录 6.43 GB / 20 GB、浏览器 profile 608 MB / 3 GB;`11_backups` 4 个 zip(保留份数 30) |
| 待办第 16 条尺寸更正 | `07_tmp` 实测 526 MB(不是 0.753 GB);另有 `C:\Users\yangl\AppData\Local\Temp\pytest-of-yangl` **50.8 MB** 在项目根之外(按规则 22 不动,等人点头) |

**第 9 轮(P26)待办以 P25 报告第十三节为准**,主线只有一条:**旧资产迁移**
(`资产库/01_语料库/factory.db` = 53,678 篇头条文章 / 1.7 万条有正文有真实互动 / 2.8 万条关键词战绩 / 旧模型 AUC 0.8803),
六步硬前置(红名单测试 → 只读映射表 → 身份碰撞预演 → 新鲜度隔离 → **管线增量化**(实测 295 行/秒,5 万行外推 3.0–3.7 分钟/任务)→ 分批导入对账)。
其余待办(404 根因、`keywords.py` 读 `viral_labels`、`crawl-enrich`、`schedule --once`、C-5 + DDL、清理清单)照旧。

**新增工具坑(规则 47)**:后台 pwsh 作业只要 stderr 有输出就回传 `exit code 1`,与被调程序真实退出码无关
⇒ `ttxt worker` / `ttxt metrics` 的 exit 1 不代表失败,判成败看回执 JSON、日志事件与库内读数。

## 九、14:00 补:黄金核验 61 张卡压成一张打勾裁定单(本人唯一卡住的那件事,已出纸)

| 项 | 实测 |
|---|---|
| 交付物 | `08_wiki/reports/P13-golden-adjudication-sheet.docx`(47,684 B)+ 同名 PDF(**769,243 B / 9 页 A4 横向**,双面打印 5 张纸)〈旧版数字;**14:5x 附录补上完整网址后为 49,104 B / 823,800 B / 11 页 / 双面 6 张**,见 §九·补〉 |
| 结构 | 61 行 × **3 个勾**:①机器说得对吗(对/不对→`human_verdict`)②原句撑得住吗(能/不能/说不清→`human_fact_support`)③证据级同意吗(同意/改字母→`human_confirms_tier`);附录按行号给来源机构、域名、独立性说明〈旧版附录**只有域名**→ 14:5x 已换成完整原始网址,本人复核要求〉 |
| 刻意不印 | `kind` 与 `expected_by_construction` —— 印了就等于把答案写在脸上,人工裁定被污染,`false_accept_rate` 失真 |
| 新工具 | `scripts/golden_v2_dump_flat.py`(项目 venv 跑,机器结论走 `golden_v2_harness::machine_result`,与度量器同源)→ `scripts/golden_v2_adjudication_sheet.py`(需带 python-docx 的解释器);两步重跑即可再生成 |
| 自查 | 主表 **65 行**(1 表头 + 3 分组 + 61 行),61 条 claim **全在、无截断**;PDF 签名 `%PDF-` ✓。**第一版渲染出 24 页**,根因:python-docx 只设 `autofit=False` 不生效,必须写 `tblLayout=fixed` + `tblGrid` 列宽 + 每个 `tc` 的 `tcW`(合并格按跨列合计)⇒ 修后 9 页 |
| 未完成的检查 | 内容与结构检查通过;**视觉检查未完成**——当前模型不接图像,预览图无法读,只核对了页数与文本完整性 |
| 仍卡着的门禁 | 61 行全勾完也还不达标:`A/B 级独立证据占比 17/61 = 28% < 50%`。这条是**我**的活(补真出处或降级卡片),不麻烦本人 |
| 回收路径 | 纸或照片交回 → 我写脚本按行号录回 `golden_set_v2.jsonl` 的 `human_*` 三字段(先写红测试:取值只允许 通过/驳回、supported/unsupported/needs_more_evidence、A/B/C/D),再跑 harness 出 `false_accept_rate` 与一致率 |

### 九·补(14:5x,本人复核后只改附录,别的内容一律不动)

本人复核结论:**先不打印**——附录只印了域名(`www.who.int` 这种),打不开原文就没法核对证据。本轮只修这一处:

| 项 | 实测 |
|---|---|
| 改了什么 | 附录第三列换成**完整原始文章网址**,按同一行号逐行给出(61 行都有)。网址全部取自卡片 `source_url` 字段;`scripts/golden_v2_dump_flat.py` 新增 `source_url` / `corroborating_urls` 两列,没有一条手写、猜测或拼出来的网址 |
| 缺网址的行 | **0 行**。61 张卡片都带完整网址,所以「原始网址缺失」这次一条都没触发(代码里这个分支在,查不到就印红字,不伪造) |
| 61 行裁定内容 | **没动**。主表仍是 65 行(1 表头 + 3 分组 + 61 行),claim 全在、机器短词不变、三个勾不变、行号不变。陷阱卡 `v2t13` 仍在:句子里「开售仅10天」/ 原句「开售仅3天」/ 机器那栏印「能核实」 |
| 就地纠正一处引用(规则 44) | 本人说的是「第 49 行」。按**印出来的行号**,这条陷阱卡在**第 13 行**;印出的第 49 行是 `v2h05`(世卫组织青少年活动量,句子与原句一致的正面卡)。照 49 行去找会找不到,故记下 |
| 页数 | **9 页 → 11 页**:网址列要 12.6cm、61 行网址要占 3 页。仍是 A4 横向,双面 **6 张纸**(末页单面)。中间一版 13 页,根因是独立性说明列只给 5.4cm,把 39–42 字挤成 4 行;放宽到 8.0cm + 7.5pt 后回到 11 页 |
| 版面核验 | 11 页全部 `MediaBox[0 0 841.89 595.30]` = 29.7×21.0 cm **横向,无纵向页**;PDF 内 `/Type /Page` 计数 11 = 渲染 `pageCount=11`;PDF 字节流里 **61/61 条网址都在**,`/URI` 对象 78 个、去重 10 个,与卡片里 10 篇不同来源文章吻合 ⇒ 电脑/手机上看 PDF 能直接点开原文 |
| 双面翻页不错行的核验 | 两张表都加了 `tblHeader`(跨页重复表头)与 `cantSplit`(整行不拆页,125 处);再用 LibreOffice 渲染图做像素比对:主表第 2–6 页表头行**逐像素相同(差 0.0)**,附录第 9–11 页顶部窄条**差 0.0** ⇒ 翻页后列名还在,一行不会被劈到两张纸上 |
| 仍未做的核验 | 依旧**没有肉眼看过版面**(当前模型不接图像)。依据是:固定列宽 + 页数 + MediaBox + 网址字节比对 + 表头像素比对,不是"看着没问题" |
| 顺带挖出一条实情 | 61 张卡片的网址去重后**只有 10 篇文章、3 个域名**(toutiao 4 篇、eastmoney 3 篇、who.int 3 篇);每篇被 5–7 张卡反复引用。这正是 `A/B 级独立证据只占 17/61 = 28%` 的直接原因:同一篇稿子不构成独立第二来源 |

## 十、14:20 起:第 9 轮 ⓪ 关键词反哺冷却窗口(红→绿,已修)

**根因(不是"排队太多",是状态放错了地方 + 没有窗口)**:`auto_feedback` 每轮看到 gap 词就给该类目的第一个正式来源入一条任务,
唯一的去重是"已有 queued/running 任务"。任务被 Worker 排干之后去重条件立刻失效 ⇒ 下一轮 `analyze` 又入一条。
health 那一支每次 `inserted 0 / duplicates 25`,gap 词 `睡眠` 永远补不上,却一天入队 4 次(job 74/75/76/77)。

**修法**:同一来源距上次反哺入队 **24 小时内不再入队**,只写证据;`cooldown_hours=0` 是运维逃生阀(紧急补采/测试)。

| 关键点 | 实测/结论 |
|---|---|
| 状态存哪 | 类目库 `articles_meta` 的 `dispatch_state` 键。**不能存 `crawl_jobs.meta_json`**:`finish_crawl_job`(`store.py:1034`)用执行回执**整体覆盖** meta_json,入队时写的 `dispatched_via` 在任务跑完那一刻就消失 —— 红测试里有一条断言专门钉住这件事 |
| 红名单先跑 | 第一批 `10_tests/test_p26_keyword_cooldown.py` 4 项,先跑 **3 failed / 1 passed**(`07_tmp/p26_cooldown/red_evidence.txt`);通过的那项是 p11 去重语义不被覆盖的回归钉。之后按验收口径("日志要能看出第几次尝试")补第 5 项,**再次先红**(`KeyError: 'attempt'`,`red_attempt_log.txt`)再修 |
| 红→绿之间修掉的第二个缺陷 | 冷却跳过时**没有把 attempts 计数写回**,第三次尝试仍记成 2 次。测试断言 `feedback_attempts == 3` 抓到它 ⇒ 尝试次数是"这个 gap 词采不动"的证据,不是入队的副产品 |
| 入队日志补一项 | `crawl.enqueued` 事件新增 `attempt` 字段(`store.create_crawl_job`),关键词反哺入队时 meta 带 `"attempt": N`;下次再排队积压,能直接看出"同一来源第几次反哺" |
| 全量回归 | 冷却主体完成后 **426 passed / exit 0 / 110.59 s**;补 `attempt` 日志后 **427 passed / exit 0 / 114.03 s**(`full_suite.txt`、`full_suite2.txt`);定向 51 项(冷却 + p25 可观测性 + p11 闭环 + p19 关键词采集)全绿 |
| 实盘体检 | `ttxt doctor` **34 ok / 0 failed / 5 WARN**(无新增 WARN);`analyze --category health` 落新证据文件 `03_intelligence/keyword_feedback/health-feedback-20261116T141917+0800.json` |
| 尚未在实盘触发 | 队列里 job 76/77 是**修复前**排的队,仍走 p11 去重通道(`note: 已有排队/运行任务,本次不重复入队`),所以生产上第一次看到"冷却中"文案要等这两条排干之后。不伪造这条证据 |

## 十一、15:0x 起:61 行裁定交回来了 —— 但交的是 **ChatGPT 辅助初审**,不是本人签字

本人把 61 行的三个勾打完了(自述用 ChatGPT 辅助,且**没有打开过任何一条原始网页**)。我的处置:**收下当筛选层,拒绝当门禁**。

| 关键点 | 实测/结论 |
|---|---|
| 落盘形式 | sidecar `10_tests/golden/adjudications/assist_draft_chatgpt_20261116.jsonl`,61 条:`tick1_machine/tick2_fact_support/tick3_tier` + `provenance="chatgpt_assisted_draft"` + `web_pages_opened=false` + `human_signature=false`,并同时记 `assist_row`(本人表格行号)与 `printed_row`(印出的行号) |
| **`golden_set_v2.jsonl` 一个字节没改** | `human_adjudicated` 仍 **False × 61**,`human_*` 仍 null ⇒ 门禁"人工裁定 ≥50 张"仍是 **0/61**,没被一份机器稿洗过去 |
| 护栏 | `scripts/golden_v2_assist_audit.py`:`assist` 模式只写 `assist_*`;`human` 模式要求每行 `provenance=human_signed` **且** `human_signature=true`,否则整份退回并报错。取值白名单(对/不对、能/不能/说不清、同意/改级、A–D)写死,拼错即 `DraftError` |
| 先红后绿 | `10_tests/test_p27_assist_draft.py` 8 项,先红 `ModuleNotFoundError: scripts.golden_v2_assist_audit`(`07_tmp/p27_assist/red_evidence.txt`,967 B)→ 实现后 **8 passed**(`green_evidence.txt`);全量 **435 passed / 118.93 s**(`full_suite.txt`) |
| 算术就地更正(规则 44) | 逐行表实际 **① 46 对 / 15 不对;② 10 能 / 51 不能;③ 61 同意**。本人汇总写的是 47/14 与 9/52,**各差 1**;以逐行表为准,测试 `test_draft_totals_are_exactly_these` 钉住这组数,以后改一个勾就会被发现 |
| 两套行号必须换算 | 本人按 财经 1–19 / 健康 20–36 / 科技 37–61 编号;印出的单子按 科技 1–25 / 财经 26–44 / 健康 45–61。锚点核对:本人「第 49 行」= 印出第 **13** 行 = `v2t13`;61 行里 ③ 勾的字母与卡片 `source_tier` **61/61 相同**,映射成立。裁定单已改成行号下印卡片编号小字,免得下次再错 |
| 自动挑出的 5 处自相矛盾 | R1(机器印「能核实」、②打「能」、①却打「不对」):印45/表20 `v2h01`。R2(机器印「对不上/没出处」、②打「不能」、①却打「不对」):印46 `v2h02`、印47 `v2h03`、印60 `v2h16`、印13 `v2t13` |
| **"第 49 行漏检"这个说法对不上印出的单子** | 那一行(印出第 13 行 `v2t13`)机器栏印的是 **「对不上」**,机器已经发现"工厂写 10 天 / 原句写 3 天";构造真值也是「对不上」。所以这行**不是漏检**,是初审把 ① 打错了。生产上有没有真漏检,要看写稿链路,不靠这张表下结论 |
| 15 个「不对」逐条查到底 | 15 行**全部落在 机器词 = 构造真值 的行上**(其中 10 行是 转述 类、4 行 对不上、1 行 能核实)。也就是说这 15 个"不对"是初审按自己对「转述」的理解打的,不是机器标错 |
| 循环性警告(不许当质量分) | 在这批卡上 `machine_result` 与 `expected_by_construction` **61/61 一致**——因为判定规则就是照这批卡写的。这个数字**不能**当机器的准确率,只能说明这批卡测不出规则外的错 |
| 初审唯一的正面信号 | 51 张构造负例(编造数字/错归属/因果预测/跑题等)里,初审把 **0 张**判成「原句能支持」;10 张正例里也只有那 1 处矛盾。即"接受假事实"这一项 = 0(但也因为它是照着文字表面判的) |
| 门禁照旧不动 | 人工裁定 **0/61**;A/B 级独立证据 **17/61 = 28% < 50%**;61 张卡的网址去重只有 **10 篇 / 3 域名** —— 这条是我这边的活,与打勾无关 |
| 下一步(要本人做的只有一件事) | 那 **15 行**(含 5 处矛盾)逐行确认。在对话里直接回就行(例:「13 改对」),回来才由 `--mode human` 写 `human_*`;其余 46 行不催,但也不冒充已核 |

顺带把裁定单首页写清楚(同一份 PDF 重出):**四个短词定义改成"机器怎么处置这句话",不是"这句话真假"**,并加一句「①只问短词贴不贴切」;行号下印卡片编号。重出后仍是 **11 页 / A4 横向 / 双面 6 张**,61/61 网址、61 个超链接、`tblHeader`+`cantSplit` 都在;渲染图比对:主表第 2–6 页表头行**差 0.0**、附录第 8–11 页表头行**差 0.00–0.01**。

## 十二、15:4x–16:0x 起:第 9 轮 P28 —— 真实爆款表现喂回关键词评分(红→绿,本轮已推)

本人主线是 `爆款表现 → 关键词评分 → 关键词采集`。实测断点在中间那一环:`score_keywords()` **一次都没读 `viral_labels`**,同一个词采到 4 篇验证爆款和 4 篇真低质,评分一分不差。红凭据 `07_tmp/p28_viral_signal/red_evidence.txt`(7 条断言全 `KeyError: 'viral'`)。

| 关键点 | 实测/结论 |
|---|---|
| 改法 | `score = 0.7*旧公式 + 0.3*真实爆款信号`(`VIRAL_WEIGHT=0.30`);信号 = 该词命中文章里**真实测得**样本的标签均值(验证爆款 1.0 / 潜力 0.6 / 普通 0.2 / 低质 0.0,词表沿用 `analysis/viral.py`)。旧公式一字未改,不加权时分数**逐位**等于改前 |
| 四道拒发闸门 | ①只认 `confidence_tier='measured'` 且 `score` 非空;②`unscored/missing_data/incomplete_data/no_impressions` 不进分母、**绝不按 0 分**(缺失 ≠ 0);③类目真实测得样本 <30 ⇒ `insufficient_sample` 不加权;④单词真实命中 <3 ⇒ 不加权 |
| 明确没做 | **零 DDL**、不动 `keyword_performance` 列、不动 `knowledge_assets`/资产库、`gap` 只看 `collected`、入队去重 / 24h 冷却 / 来源限速 / robots 一律没碰 |
| 测试 | `10_tests/test_p28_viral_keyword_signal.py` **7 项**(先红 `KeyError: 'viral'` → 绿 `7 passed / 1.22 s`);全量 **442 passed / 113.20 s**(改前 435 + 本轮 7);`ttxt doctor` **34 ok / 0 failed** |
| 生产读数(只读) | 过 30 门槛的**只有 technology**:101 条 measured(normal 35 / potential 30 / low_quality 26 / verified_viral 10)。finance 10、history 10、social 0、health 0、demo 0 ⇒ 这 5 类**评分逐位不变**并如实报 `insufficient_sample` / `no_measured_hits`,不许说成已闭环(`prod_measured_probe.txt`) |
| 真数据干跑(副本,不碰生产库) | 数据 1.0→**0.8082**(signal 0.3607 / 56 篇真实命中)、开源 0.4451→**0.4716**(0.5333 / 6)、人工智能 0.5259→**0.4554**(0.2909 / 11)、效率工具(gap)0→0。`ttxt crawl-plan --category technology` 干跑排序变成 **数据 > 开源 > 人工智能 > 效率工具**,开源升到第二位(`technology_dryrun.txt`、`crawl_plan_technology.txt`) |
| 已落库 | `ttxt analyze --category all` 跑完(`prod_analyze_all.txt`):technology `applied/calibrated`、finance+history `insufficient_sample`、social/health/demo `no_measured_hits`;`zero_coverage` 与反哺候选与改前一致 |
| 事故(护栏抓到我) | 只读巡检(`mode=ro`)与全量测试**并发** ⇒ 生产目录留 6 对 `-wal/-shm`,`conftest.production_state_untouched` 判 1 error。护栏正确、违规是我;`scripts/p28_wal_sidecar_cleanup.py` 逐库 `wal_checkpoint(TRUNCATE)`+`quick_check` 全 ok,边车 WAL 全 0 字节,行数 32/106/25/30/33/186 = **412 未变**,收净后重跑才是 442 passed。已写进 **规则 48**(测试期间禁止连生产库) |
| 实话(审计性) | 因为本轮不做 DDL,逐词的 `signal/measured_hits` 只在回执与 `03_intelligence/keyword_feedback/*.json` 证据里,查表查不到 —— 下一轮加两列补上 |
| 下一轮我的活 | finance/history 的 measured 从 10 提到 ≥30(各差 20 条,走 `metrics --fetch-public` 候选轮转);social/health 无可测对象先补语料不凑数;`keyword_performance` 加 `signal/measured_hits` 列走正规迁移+备份+回滚 |
| 门禁照旧 | 人工裁定仍 **0/61**、A/B 独立证据 **17/61 = 28%**、61 张卡只覆盖 10 篇 / 3 域名;本轮完全没动黄金集与 `human_*`,15 行仍等本人逐行确认 |

完整账本:`08_wiki/reports/P28-viral-keyword-feedback-20261116.md`。



