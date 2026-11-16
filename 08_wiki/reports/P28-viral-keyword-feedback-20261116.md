# P28 报告:真实爆款表现喂回关键词评分(2026-11-16,第 9 轮)

一句话:**采集回来的文章到底爆没爆,以前不参与"下一轮该搜什么词"的决定;现在参与了,而且只在有真实数据时才生效。**

## 1. 实测缺口(不是猜的)

`ttxt/analysis/keywords.py:score_keywords()` 的评分只看两件事:**这个关键词覆盖了几篇文章**(`collected` 归一)和**这些文章正文合格不合格**(`quality/collected`)。它**一次都没有读 `viral_labels` / `article_metrics`**。

后果用两条断言就能钉死:同一个关键词、同样采到 4 篇、同样正文合格 —— 4 篇是**验证爆款**和 4 篇是**真低质**,`keyword_performance.score` 一分不差。红凭据:`07_tmp/p28_viral_signal/red_evidence.txt`,7 条断言全部 `KeyError: 'viral'`(旧代码根本没有这个回执字段),证明评分链路里没有爆款这一项。

这正是本人定的主线里断掉的那一环:`爆款表现 → 关键词评分 → 关键词采集`。P21/P22 把互动指标抓回来了,P12 把指标打成了四级标签,P19/P26 让关键词能驱动采集 —— 中间"标签喂回评分"没人接。

## 2. 口径(我定的,写下来供复核)

融合在**既有** `keyword_performance.score` 上,不另建第二套评分体系:

```
score = (1 - W) * base + W * signal      W = VIRAL_WEIGHT = 0.30
base  = 0.6 * collected/max_collected + 0.4 * quality/collected   (旧公式,一字未改)
signal= 该关键词命中的文章里,真实测得样本的标签均值
```

标签→数值(与 `ttxt/analysis/viral.py` 同一套词表,不另造):验证爆款 1.0 / 潜力 0.6 / 普通 0.2 / 低质 0.0。

四道闸门,任何一道不过就**不发加分**:

| 闸门 | 规则 | 为什么 |
|---|---|---|
| 数据可信度 | 只认 `confidence_tier='measured'` 且 `score IS NOT NULL` | `unscored/missing_data/incomplete_data/no_impressions` 没有真实表现数据 |
| 缺失 ≠ 0 | 上面那些行**不进分母**,更不当 0 分 | 规则 6:不能凭空给关键词扣钱,也不能凭空加分 |
| 类目门槛 | 类目真实测得样本 < `viral.MIN_CALIBRATION_SAMPLE`(30)⇒ 阈值仍是设计假定(0.15/0.08/0.02),`status=insufficient_sample`,爆款项**不参与**评分 | 样本不足宣称"已标定"就是自欺(规则 44) |
| 词级门槛 | 单个关键词真实测得命中 < `MIN_KEYWORD_MEASURED_HITS`(3)⇒ 不加权,`reason` 写清原因 | 一两篇文章说明不了这个词的选词价值 |

无信号时 `score` **必须逐位等于旧公式**(既不虚构加分,也不因缺数据扣分),这条有测试钉住。

回执新增 `keywords.viral`:`status`(applied / insufficient_sample / no_measured_hits)、`measured_samples`、`required=30`、`weight`(0.3 或 0.0)、`calibration`(calibrated / default_thresholds)、`min_keyword_hits`、`keywords_with_signal`、以及逐词 `details.<词>.{signal,measured_hits,matched_articles,reason}`。

**本轮不做的事**:没有任何 DDL、没有新表新列(逐词信号只活在回执与证据 JSON 里)、不动 `knowledge_assets` / 资产库、不动 `gap` 判定(只看 `collected`)、不动入队去重 / 24 小时冷却 / 来源限速 / robots 门禁。

## 3. 测试(先红后绿)

`10_tests/test_p28_viral_keyword_signal.py`,7 项,全部在 `07_tmp/pytest_basetemp` 的临时库里跑,连一次生产库都没有:

1. `test_measured_viral_performance_changes_the_score` —— 同一个库里两个词,覆盖数与正文合格数**完全相同**,只有真实爆款标签不同 ⇒ 分数必须分开(0.91 vs 0.70)。
2. `test_real_low_quality_articles_drag_the_score_down` —— 真低质不能当爆款,低质词分数要**低于**无数据词。
3. `test_unscored_labels_are_not_counted_as_zero` —— 4 篇命中文章全是 `unscored/missing_data/score=NULL` ⇒ `measured_hits=0`、`signal=None`、分数逐位 1.0,`measured_samples` 只数 30 条真测得。
4. `test_category_below_calibration_sample_must_not_claim_calibration` —— 29 条真实测得 ⇒ `insufficient_sample`、不加权、`reason` 含「不宣称已标定」。
5. `test_keyword_with_too_few_measured_hits_gets_no_signal` —— 类目门槛过了但单词只有 1 篇真实命中 ⇒ 不加权。
6. `test_categories_do_not_pollute_each_other` —— 同样文章摆进两个库,只有打了标签那个库的分数动;隔壁类目**不贡献**样本也不拉分数。
7. `test_gap_and_feedback_candidates_unchanged_by_new_score` —— 新分数下 `zero_coverage` / `feedback_candidates` / gap 状态与改前一致(回路其他环节没被带跑)。

- 红:`07_tmp/p28_viral_signal/red_evidence.txt`(7 failed,`KeyError: 'viral'`)
- 绿:`07_tmp/p28_viral_signal/green_evidence.txt`(**7 passed / 1.22 s**)
- 全量:`07_tmp/p28_viral_signal/full_suite.txt`(**442 passed / 113.20 s**,改前 435 + 本轮 7 项)
- 中途有一次全量跑出 **1 error**,是我并发跑只读巡检造成的(见 §6),收净后重跑才拿到 442 passed。

## 4. 生产读数(只有 technology 真的够格)

`scripts/p28_viral_sample_probe.py`(只读,`07_tmp/p28_viral_signal/prod_measured_probe.txt`):

| 类目 | 真实测得(measured 且 score 非空) | 过 30 门槛 | 标签分布 | 关键词数 |
|---|---|---|---|---|
| technology | **101** | 是 | normal 35 / potential 30 / low_quality 26 / verified_viral 10 | 4 |
| finance | 10 | 否 | 同上四类混 | 4 |
| history | 10 | 否 | normal 6 / low_quality 3 / potential 1 | 3 |
| social | 0 | 否 | 全是 `missing_data` | 3 |
| health | 0 | 否 | 全是 `missing_data` | 3 |
| demo | 0 | 否 | 全是 `missing_data` | 4 |

⇒ **今天这套加分只在 technology 生效**,其余 5 类逐位保持旧分数并如实报 `insufficient_sample` / `no_measured_hits`。报告里不许写"闭环已全部生效"。

## 5. 真实数据干跑与落库

干跑连的是 `Connection.backup()` 复制出来的副本 `07_tmp/p28_viral_signal/technology_copy.db`(`scripts/p28_technology_dryrun.py` → `technology_dryrun.txt`),不碰生产库:

| 关键词 | collected | 改前 score | 改后 score | signal / 真实命中 |
|---|---|---|---|---|
| 数据 | 81 | 1.0 | **0.8082** | 0.3607 / 56 篇 |
| 开源 | 11 | 0.4451 | **0.4716** | 0.5333 / 6 篇 |
| 人工智能 | 17 | 0.5259 | **0.4554** | 0.2909 / 11 篇 |
| 效率工具(gap) | 0 | 0.0 | 0.0 | None / 0 篇 |

`ttxt analyze --category all` 已落库,`prod_analyze_all.txt` 里各类目 `keywords.viral.status`:technology `applied`(calibrated)、finance/history `insufficient_sample`(各 10 条)、social/health/demo `no_measured_hits`。`zero_coverage` 与 `feedback_candidates` 与改前一致(technology 仍是 `效率工具`,health 1 个)。

选词端复核(`ttxt crawl-plan --category technology` 干跑,`crawl_plan_technology.txt`):计划里的关键词分数排序变成 **数据 0.8082 > 开源 0.4716 > 人工智能 0.4554 > 效率工具(gap)** —— 也就是说"采到的文章真的表现好的词",下一轮会被优先再采。**开源从第三位升到第二位**,它的真实爆款信号 0.5333 是四个词里最高的。

体检:`ttxt doctor` **34 ok / 0 failed**(`07_tmp/p28_viral_signal/doctor_after.txt`),磁盘 free 96.8 GB / used 7.015 GB(限额 20 GB)。

## 6. 事故如实记录(护栏抓到了我)

本轮我把"只读巡检生产库"和"全量测试"**并发**跑。只读连接(`mode=ro`)打不开 checkpoint,SQLite 在生产目录留下 6 对 `-wal/-shm`,`conftest.production_state_untouched` 当场判 **1 error**(`test_p9_toutiao.py`)。

判定:**护栏是对的,违规的是我**。处置:`scripts/p28_wal_sidecar_cleanup.py` 逐库 `PRAGMA wal_checkpoint(TRUNCATE)` + `PRAGMA quick_check`,全部 `ok`,边车 WAL **均为 0 字节**(没有未落盘内容),行数 **32/106/25/30/33/186 = 412** 与巡检前后一致(`07_tmp/p28_viral_signal/wal_cleanup.txt`)。

已写进 **规则 48**:跑测试期间禁止连生产库,要看生产数据先复制到 `07_tmp` 再连。

## 7. 审计性的实话(不许夸大)

因为没有 DDL,逐词的爆款信号**只存在于回执与 `03_intelligence/keyword_feedback/*.json` 证据里**,`keyword_performance` 表里没有"signal / measured_hits"这两列。也就是说:事后想知道某个分数是怎么算出来的,得看当轮 analyze 的回执与证据文件,不能只查表。这一条与"本轮不做 DDL"的约束是取舍关系,已登记为下一轮候选(加列即可,不需要新表)。

## 8. 下一步(我接着做,不等人)

1. **把 finance/history 的 measured 样本从 10 提到 ≥30**(各差 20 条)——走 `ttxt metrics --fetch-public` 的候选轮转(P22/P24 已修好候选口径),每轮点名这两个类目。完成标准:`prod_measured_probe` 里两行 ≥30,且 `analyze` 报 `status=applied`。
2. social/health 各 0 条 measured:先解决语料侧(health 25 行全 `short`,要 `crawl-enrich`),没有可测对象时**不许**凑数。
3. `keyword_performance` 加 `signal/measured_hits` 两列(下轮,单独走 DDL 迁移 + 备份 + 回滚取证)。
4. P26 的 24 小时冷却在生产上第一次看到"冷却中"文案,等 job 76/77 排干后取数。
5. 黄金门禁仍差:人工裁定 **0/61**、A/B 级独立证据 **17/61 = 28% < 50%**、61 张卡只覆盖 **10 篇 / 3 域名** —— 与本轮无关,继续按 P27 的规矩推进(15 行等本人逐行确认,我不会替他们签字)。
