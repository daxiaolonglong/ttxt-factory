# P24(第 7 轮,2026-11-16):指标回流扩量 + 预测闭环首次真跑 + 两处口径根因

范围:全部在 `D:\ttxt`。取证件:`07_tmp/r27_metrics_expand/`。备份:`11_backups/P24-pre-metrics-20261116-050544.zip`(4,866,663 字节,6 类目库 + `system.db` 用 SQLite `Connection.backup()` 在线备份)。

## 一、先取证再定口径:上一轮待办有一条是空话

P23 报告 §九第 1 项写的是「`ttxt metrics --fetch-public` 刷 finance / health / social / history」。
本轮先用只读探针 `host_inventory.py` 数了各类目文章的**主机分布**(`host_inventory_out.txt`):

| 类目 | 行数 | 文章主机分布 | 头条文章数 |
|---|---|---|---|
| technology | 168 | toutiao.com **121** / ifanr.com 28 / sspai.com 15 / ruanyifeng.com 4 | 121 |
| finance | 96 | eastmoney.com 60 / tmtpost.com 36 | **0** |
| social | 33 | chinanews.com.cn 33 | **0** |
| history | 20 | chinanews.com.cn 20 | **0** |
| health | 25 | who.int 25 | **0** |
| demo | 32 | quotes.toscrape.com 30 / sspai.com 2 | **0** |

`metrics_pages._candidates` 只用 `host_allowed(url)` 选候选(目前仅 `www.toutiao.com`)。
所以给那 4 个类目跑 `--fetch-public` 的结果必然是 `candidates: 0 / host_skipped: N`(结构见 `ttxt/crawler/metrics_pages.py:308-334`)。
**不是能力缺失,是那 4 个类目库里根本没有头条文章。** 缺指标的正确根因链是:
「类目里没有带公开计数的来源 → 没有可测文章 → 没有 measured 样本 → 不能标定 → 100% `missing_data`」。
修法在第六节(注册头条 finance / history 频道来源),不在"再刷一次指标命令"。
已在 P23 §九与 P17 §十四 原文下面加了带日期的更正说明(不改写历史账)。

## 二、technology 指标回流:预备 → 三批入库

预检 dry-run(`dryrun_batch3.txt`,exit 0):candidates 20 / host_skipped 14 / fetched 19 / fetch_errors 1 / **render_ok 19 / render_mismatch 0 / render_unattributed 0 / render_errors 0 / sanity_rejected 0**,19 页全部带真实计数。

| 批次(均 `--apply`) | 候选 | host_skipped | 抓到 | 抓取失败 | render_ok | 写入 | 更新 | 快照 | 由 NULL 填上 |
|---|---|---|---|---|---|---|---|---|---|
| batch3(`apply_batch3.txt`) | 20 | 14 | 19 | 1 | 19 | 19 | 0 | 19 | 76 |
| batch4(`apply_batch4.txt`) | 20 | 14 | 18 | 2 | 18 | 18 | 0 | 18 | 72 |
| batch5(`apply_batch5.txt`) | 20 | 14 | 16 | 4 | 16 | 16 | 0 | 16 | 64 |
| 合计 | 60 | 42 | 53 | 7 | 53 | **53** | 0 | **53** | 212 |

`render_mismatch / render_unattributed / sanity_rejected / row_errors` **全部 0**:每条计数都靠 URL 里的 `data.itemId` 对上文章,没有张冠李戴,也没有"展现量 < 阅读量"的物理不可能值被采信。
`host_skipped` 每批都是 14 —— 与第一节一致(非头条主机不猜指标)。

technology 现状(`post_p24_state.json`):`article_metrics` 32 → **85** 行,reads/likes/comments/impressions 各 85 非空,**favorites 0 非空**;`metric_snapshots` 46 → **99**;reads 1–154,282,impressions 最大 11,159,267。

## 三、标定与标签分布(样本从 32 涨到 85)

| 时点 | measured 样本 | 阈值(verified_viral / potential / normal 下限) |
|---|---|---|
| 11-15 17:56(第 6 轮) | 32 | 0.0567 / 0.0247 / 0.0113 |
| 11-16 05:58(`calibrate_69.txt`) | 69 | 0.0462 / 0.0235 / 0.0108 |
| 11-16 07:15(`calibrate_85.txt`) | **85** | **0.0551 / 0.0261 / 0.0123** |

样本翻倍后阈值先降后回升,说明前 30 多个样本把分布拉偏了一点;方法不变(p90/p60/p25,`MIN_CALIBRATION_SAMPLE=30`)。
标签分布:unscored 83(=没有指标的 83 行,`missing_data`)、normal 30、potential 25、low_quality 21、**verified_viral 9**(第 6 轮是 3)。
`confidence_tier`:measured 85 / missing_data 83 —— 没指标的行只打 `unscored/missing_data`,不猜分(规则 43)。

## 四、根因:`prediction_records` 恒为 0 不是"没跑",是候选条件写错

实跑证据(修之前,`predict1.txt`):`recorded: 0 / already_has_prediction: 0 / skipped_no_baseline: 0`,但 `baseline_buckets` 里明明有 69 个 measured 样本。空转。

`ttxt/analysis/prediction.py` 旧候选 SQL:`WHERE v.article_id IS NULL`(只挑**完全没有标签行**的文章)。
而生产里每个采集入口跑完都会 analyze/relabel ⇒ **每篇**文章都有 `viral_labels` 行(没指标的记 `unscored/missing_data`)⇒ 候选永远为空。
第二个缺陷:`LIMIT` 在"是否已有预测记录"的判断**之前**生效 ⇒ 即便有候选,每轮也会重复挑同一批(和 P22 指标候选轮转同一类根因)。

修法(同一文件,注释里写明了根因):
- 候选 = **未实测**:`v.article_id IS NULL OR v.confidence_tier <> 'measured' OR v.score IS NULL`;
- 已实测的文章**不许**预测(它已经有真值,预测它是自欺);
- "已有预测记录"下推成 SQL 反连接(`LEFT JOIN prediction_records pr ... WHERE pr.id IS NULL`),`LIMIT` 只消耗新候选 ⇒ 逐轮往前推进;
- `ORDER BY a.id DESC` 保证顺序确定;`already_has_prediction` 改为按"未实测且已有预测"计数(旧语义保持)。

红→绿(`test_p24_prediction_candidates.py`,5 项):改前 **4 failed / 1 passed**(`p24_red.txt`:`assert 0 == 2`、`assert (0,0,0) == (1,1,1)`);改后连同 P11 旧预测测试 **24 passed**(`p24_green.txt`),旧断言(`already_has_prediction == 2`、幂等、`min_samples` 门禁)一条没破。

## 五、预测闭环第一次真跑:40 条预测 → 16 条回填 → 准确率进知识资产

顺序与凭据:
1. `predict predict --category technology --limit 40` → **recorded 40**(`predict2.txt`,修前是 0);
2. `settle` → settled 0 / pending 40(还没有这批文章的真值,不许回填);
3. batch5 实测其中 16 篇 → `settle` → **settled 16 / pending 24**(`settle3.txt`);
4. `--calibrate`(样本 85)→ `settle` → settled 0(分数与阈值无关,已回填的不重算);
5. `report` → **settled_samples 16 / MAE 0.0168 / mean_bias −0.0116 / asset_written true**(`report3.txt`)。

独立 SQL 复核(不同代码路径):`prediction_records` 40 行、`outcome_score` 非空 16 行、`AVG(ABS(predicted_score - outcome_score)) = 0.0168` —— 与报告一致(`post_p24_state.json`)。
写入的知识资产:`kind='prediction_accuracy'`、`confidence='measured'`、`statement=「类目 technology 预测校准:已回填 16 条,平均绝对误差 0.017,平均偏差 -0.012(正=系统性高估)」`。
含义:桶均值基线**系统性略低估**爆款分;16 个样本还少,只做参考,不当结论(规则 21)。

## 六、其余类目怎么拿到头条指标:频道取证(只读,不落库)

静态 Fetcher 探 6 个频道 slug(`probe_channels_out.txt`):

| 频道 URL | 状态 | 落地 URL | 静态 HTML 里的文章链接 |
|---|---|---|---|
| `/ch/news_finance/` | 200 | `/?channel=finance&source=ch` | 0(4,827 字节 JS 外壳) |
| `/ch/news_history/` | 200 | `/?channel=history&source=ch` | 0(同上) |
| `/ch/news_health/` | 200 | `/?channel=&source=ch`(频道名为空 ⇒ slug 不存在) | 0 |
| `/ch/news_economics/` | 404 | — | 0 |
| `/ch/news_society/` | 404 | — | 0 |
| `/ch/news_culture/` | 404 | — | 0 |

robots:六个 URL 全部**允许**(`robots_unknown: false`)。静态抓不到 ≠ 抓不到:`toutiao_tech` 本来就是 `render: browser`。
于是用**生产适配器**再探(`probe_channels_browser_out.txt`:`fetcher.browser_session()` + `toutiao.collect(limit=3)`,复刻 `runner.py:281-292` 的真实路径):

| 类目 | 频道 | 页数 | 逐篇链接 | 产出文章 | robots 跳过 | 错误 | 正文长度 | 发布时间样本 |
|---|---|---|---|---|---|---|---|---|
| finance | `news_finance` | 4 | 17 | 3 | 0 | 0 | 1450 / 1534 / 2377 | 2026-11-16 07:15、11-15、11-14 |
| history | `news_history` | 4 | 17 | 3 | 0 | 0 | 1562 / 2692 / 2985 | 2026-11-16 06:26、11-06、09-21 |

⇒ **第 8 轮注册 `toutiao_finance` / `toutiao_history` 两个来源的依据就是这两行**(有逐篇链接、正文够用、当天发布、robots 放行、不登录)。
social / health 的频道 slug 不存在,需要先渲染首页导航拿到真实 slug,再同样取证;不许凭猜测写进 `02_crawler/sources.json`。

## 七、`favorites`(收藏)仍然 NULL:语义仍未证实

`itemCounter` 里除 readCount/diggCount/commentCount/showCount 外还有 shareCount 54 / repinCount 196 / forwardCount 3 / buryCount 0 / danmuCount 0(文章 7706348155046068771,dry-run 原样)。
把 `repinCount` 当"收藏"是猜:探针 `probe_counter_labels_out.txt` 想找到页面上显示这些数字的中文标签,但**这次抓取同一 URL 返回的是没有 `RENDER_DATA` 的外壳**(HTML 里 `RENDER_DATA` 出现 0 次、`收藏` 0 次、计数元素 0 处),配对取证没做成。
⇒ 结论保持 P22 判定:**只有已证实语义的 4 个计数入库**,`favorites` 继续 NULL,`viral.score` 缺收藏权重就按缺项归一化并在 `reason` 里写明。真正能补上这条的是头条后台导出(`ttxt metrics --import FILE --source toutiao_admin_export`,等用户提供文件)。

顺带一条实测事实,**同一 URL 两次抓取内容不同**:dry-run 里这个 URL 有完整计数(reads 54308 / impressions 741337),十几分钟后探针抓同一 URL 拿到 0 计数外壳。这解释了 batch5 的 4 次抓取失败,也正是 `metric_snapshots` 时序不可覆盖的价值:单页单次抓取不可复现,不能当历史真值。

## 八、测试护栏的误报根因(conftest)

全量跑出现 `411 passed, 1 error`(`p24_full_suite.txt`):teardown 断言 `测试改动了生产状态: ['wal_sidecars']`。
真实原因与测试无关:会话**开始前**生产目录里就有 14 个 `-wal/-shm`(我的只读巡检脚本用 `mode=ro` 连库,只读连接不能 checkpoint,SQLite 会留下边车,下一次可写连接才收掉)。旧断言把边车**列表**纳入逐字节比较 ⇒ 外部只读巡检就会误报。
`10_tests/conftest.py` 改为两条精确判定:①受保护文件(6 库 + `system.db` + 配置/注册表/`scheduler_state`/`worker.lock` + 全部日志 + `model_gate`)sha256/行数必须一致且不许缺失;②会话期间**新增**的边车才算违规(配合原有 `session_db_opens` 连接白名单,`test_p15_test_isolation.py` 仍在测)。
红→绿:边车存在时重跑 → **411 passed / 109.69 s / exit 0**(`p24_full_suite2.txt`,上面列了 14 个边车)。

## 九、验收账

- 全量测试:**411 passed,0 failed,0 error,exit 0,109.69 s**(`p24_full_suite2.txt`;第 6 轮基线 406)。
- `ttxt doctor`:**33 ok / 0 failed / 5 WARN**(`doctor_after_p24.txt`),WARN 与第 6 轮同一批(demo 未注册、demo 30 行来源页 URL、9 条 `evidence_links.article_id` 为空、近 24h `crawler.insert_judgements` 里 social/chinanews_society 的 3 条历史塌陷)。
- `db.cleaned_coverage`:374/374。磁盘:`root_used=6.315GB / 预算 20GB`,`07_tmp=0.571GB / 限额 2GB`,未登记 0.001GB。
- 队列:`done 63 / aborted 7`,新增 0(本轮只做指标与预测,没有新采集任务)。
- 备份:本轮开始前 `P24-pre-metrics-20261116-050544.zip`;基线快照 `pre_metrics_state-20261116-050544.json`;终态快照 `post_p24_state.json`。

## 十、诚实说明(没做什么、不确定什么)

1. `fetch_errors` 1→2→4 **没有逐 URL 取证**,只证明"同一 URL 会被返回不同外壳";没有查是不是限流、是不是需要 cookie。不许写成已查明。
2. MAE 0.0168 只有 **16** 个回填样本,且预测基线只是"同类目同标题形态桶均值",不是模型预测。
3. `favorites` 全 NULL;其余 5 个类目 `article_metrics` **仍是 0**,`prediction_records` 仍是 0(它们没有可测样本,`predict` 会如实拒绝)。
4. 频道取证 limit=3,只证明"能拿到逐篇链接与正文",没有测整频道产量,也没测翻页/无限滚动。
5. 没有跑 Worker 常驻、没有开 `ttxt schedule --once`(仍需用户批准)、没有清理 `07_tmp`、没有改表结构(9 条空 `article_id` 仍在等 DDL 批准)。
6. `00_control/worker.lock` 里仍是 11-15 的陈旧 PID,按上一轮决定不动。

## 十一、第 8 轮待办(优先级从上到下)

1. **注册 `toutiao_finance` / `toutiao_history`**(依据第六节两行取证),`enabled: true` 前必须逐 URL robots 复核 + 限速 2s + `render: browser`;采集后 `analyze` → 攒 ≥30 measured → `--calibrate` → `predict/settle/report`,把预测闭环从 technology 复制过去。
2. **social / health 的头条频道**:渲染首页导航取真实 slug(不许猜 slug),同样走"静态 → 生产适配器"两级取证。
3. **R2-5 `ttxt crawl-enrich --category`**:health 25 行全 `short`(avg 119.4 字),等第 1 项落地后一起补。
4. **调度器上线**:`ttxt schedule --once` 先跑单轮;Worker 常驻需用户批准(`scheduler_state.json` 冻结在 11-12 19:57,`crawl_enqueue_4h` 已错过约 18 轮)。
5. C-5 可信度分档;9 条 `evidence_links.article_id` 为空(需 DDL 批准);`_element_text` 标题缺陷 + `PROCESSOR_VERSION` bump(规则 24 先红后绿)。
6. **关键词反哺读爆款标签**:`analysis/keywords.py` 目前不读 `viral_labels`,爆款结论没有回到选词(用户目标 2 的关键缺口)。
7. `07_tmp` 已 0.571 GB(限额 2 GB):清理必须先出清单交用户批准,不许自动删(规则 29/43)。

## 十二、凭据位置

`07_tmp/r27_metrics_expand/`:`host_inventory.py`+`host_inventory_out.txt`、`pre_p24_backup_and_state.py`、`pre_metrics_receipt.txt`、`pre_metrics_state-20261116-050544.json`、`dryrun_batch3.txt`、`apply_batch3/4/5.txt`、`calibrate_69.txt`、`calibrate_85.txt`、`predict1/2.txt`、`settle1/2/3/4.txt`、`report1/2/3.txt`、`post_p24_state.py`+`post_p24_state.json`+`post_p24_state_out.txt`、`probe_counter_labels.py`+`probe_counter_labels_out.txt`、`probe_toutiao_channels.py`+`probe_channels_out.txt`、`probe_toutiao_channels_browser.py`+`probe_channels_browser_out.txt`、`p24_red.txt`、`p24_green.txt`、`p24_full_suite.txt`(误报红)、`p24_full_suite2.txt`(411 绿)、`doctor_after_p24.txt`。
`07_tmp` 只测量、不自动删(规则 43)。
