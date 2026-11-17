# P33 阶段一:technology 真实指标补齐(2026-11-16)

- 报告生成时间:2026-11-16T22:42:23
- 证据文件:`03_intelligence\metrics_public\technology-20261116T222204-b1.json`(逐篇结果台账 + 逐字段前后值)
- 写前备份:`11_backups\p33-before-metrics-20261116-222203.zip`(6,832,553 字节,zip 完整性校验:OK)
- 本轮范围:只做指标获取、回流与必要测试;未动写作、面板、调度器、Strata、爬虫安全模块;未加表、未加字段。

## 一、确认的问题与根因

1. **P25 那 4 次 404 的根因已查实:文章已被头条删除/下架,不是抓取方式问题。**
   取证(`07_tmp/p33/forensic_b.py`,2026-11-16 22:00-22:01,用同一浏览器会话做对照组):
   - 4 条目标 URL 全部返回 HTTP 404,且返回的是头条真实 404 页(正文含「抱歉,你访问的内容不存在」),内嵌 RENDER_DATA 里没有本文章计数;
   - 同一时刻、同一会话抓的 2 条对照文章返回 200 并成功取到计数 ⇒ 不是封 IP、不是限速、不是全站故障;
   - robots 对 `www.toutiao.com/article/...` 放行(18 条规则,`allow_all=False`,该路径允许)⇒ 不是来源策略挡的;
   - 换 URL 形态也不行:`/a<id>/` → 301 → `/article/<id>/` → 404;`m.toutiao.com/a<id>/` → 302 → `www.../article/<id>/?&source=m_redirect` → 404;`/group/<id>/` robots 不允许 ⇒ 按规矩一次请求都不发。
   - 这 4 条正文是 2026-11-13 抓到的(`fetched_at` 01:32–02:02,`text_length` 1480–4077),但 11-12/11-13/11-14/11-15 的日志里**没有**这 4 条成功取到计数的记录,所以「早前能抓到指标」没有证据,只有「早前抓到了正文」。
2. **报告只有计数没有台账,是上一轮无法定位根因的直接原因。**`fetch_errors: 4` 这种数字回答不了「哪一篇、什么状态、为什么」。本轮补上逐篇结果台账 + 逐字段前后值清单,问题才变得可复核。

## 二、实际修改的文件

| 文件 | 改了什么 |
| --- | --- |
| `ttxt/crawler/metrics_pages.py` | 每条候选输出一条可解释结果(`metrics_ok`/`no_public_counts`/`article_gone`/`access_blocked`/`fetch_failed`/`robots_refused`)与 `outcome_counts`;404/410/451 归类为「文章已下架」并单独写 `metrics.public.article_gone` 日志;`favorites` 明确标为语义未取证 |
| `ttxt/analysis/metrics_import.py` | `write_metric_rows` 输出逐篇逐字段前后值清单 `changes`(`inserted`/`filled_from_null`/`value_changed`/`unchanged`/`null_kept`/`protected_by_credibility`/`stayed_null`) |
| `ttxt/__main__.py` | `metrics --fetch-public` 新增 `--evidence PATH`,把完整逐篇证据写成 JSON;终端只打摘要(默认不写任何文件,默认仍是 dry-run) |
| `10_tests/test_p33_metrics_ledger.py`(新增) | 12 条测试先写后改,锁住「每篇有可解释结果」「失效文章不产生指标行也不写 0」「前后值可追溯」「弱来源不覆盖强来源」 |

## 三、回填数量与失败分类(本轮生产写入:1 批,70 条候选)

| 结果 | 篇数 | 说明 |
| --- | --- | --- |
| metrics_ok | 66 | 真实计数已入库 |
| article_gone | 4 | 内容不存在,保持无指标行(不写 0) |
| access_blocked | 0 | 本轮 0 条 |
| fetch_failed | 0 | 本轮 0 条 |
| no_public_counts | 0 | 本轮 0 条 |
| robots_refused | 0 | 本轮 0 条 |

- 候选 70 条 = 台账 70 条(严格一致);抓取成功 66 条,抓取失败 4 条。
- 白名单外主机跳过 62 条(ifanr/sspai/ruanyifeng 等,公开页指标只认头条官方域名,不放宽)。
- 内嵌计数核对:归属正确 66 页,计数与文章 id 不匹配 0 页,无法归属 0 页,解析失败 0 页,合理性拒绝 0 页。
- 入库动作:新增 64 行,更新 2 行,快照 66 条;`row_errors` = 0。

### 指标覆盖前后对照(technology)

| 指标 | 写前非空 | 写后非空 | 写后为真 0 |
| --- | --- | --- | --- |
| reads | 101 | 165 | 1 |
| likes | 101 | 165 | 18 |
| favorites | 0 | 0 | 0 — 语义未取证,始终 NULL |
| comments | 101 | 165 | 59 |
| impressions | 101 | 165 | 0 |

- 有指标文章:101 → 165(类目共 231 篇);快照 115 → 181。
- 溯源:`data_source` = {'toutiao_public_article': 165},`credibility` = {'public_page': 165};最新 `captured_at` = 2026-11-16T22:22:07+08:00。
- 仍无指标的 66 篇按来源分类:{'ifanr_feed': 39, 'sspai_rss': 19, 'toutiao_tech': 4, 'ruanyifeng_atom': 4} —— 其中 ifanr/sspai/ruanyifeng 共 62 篇主机不在公开指标白名单内,toutiao_tech 剩 4 篇即已下架的 4 篇。
- `favorites` 没有任何一行被写入数值,**也没有一行被写成 0**;`likes=0`、`comments=0` 是平台页面上的真实 0,如实入库并可与 NULL 区分。

## 四、已下架文章明细(保持无指标,不伪造)

| article_id | 标题 | 来源 | 正文抓取时间 | 正文字数 | URL |
| --- | --- | --- | --- | --- | --- |
| 70 | 1900亿市值蒸发！黄仁勋遭美国国会问询，女儿接班计划或许要悬了 | toutiao_tech | 2026-11-13T01:32:23+08:00 | 2504 | https://www.toutiao.com/article/7706545549247300139/ |
| 149 | 黄仁勋还是被盯上了！表面上是查新加坡转卖，实则逼英伟达查下家 | toutiao_tech | 2026-11-13T01:37:26+08:00 | 2703 | https://www.toutiao.com/article/7706366260958724643/ |
| 156 | 大胆设想：华裔黄仁勋已辞职，能否邀请他来华，助力国产芯片？ | toutiao_tech | 2026-11-13T01:39:21+08:00 | 1480 | https://www.toutiao.com/article/7707306738043355648/ |
| 233 | 马斯克给黄仁勋留了个工位 | toutiao_tech | 2026-11-13T02:02:50+08:00 | 4077 | https://www.toutiao.com/article/7707135760947741234/ |

这 4 篇每轮都会重新进入「从未测得」候选窗口,每轮白跑 4 次请求。修它需要给文章加链接状态字段或跳过清单,属于加字段,**本轮按范围不做**,记为下一步。

## 五、逐篇变更清单(可追溯:article_id / 来源 / 抓取时间 / 字段前后值)

来源 `toutiao_public_article`;抓取时间 2026-11-16T22:22:07+08:00。
动作含义:`inserted` 新增;`value_changed` 原值→新值;`unchanged` 没变;`stayed_null` 本来就没有、依然没有(缺失不是 0);`protected_by_credibility` 来值可信度低被保住。

| article_id | 读 | 赞 | 评 | 曝 | 藏 | 动作 |
| --- | --- | --- | --- | --- | --- | --- |
| 349 | 16893 | 61 | 39 | 528328 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 348 | 4933 | 29 | 11 | 59559 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 347 | 52 | 3 | 0 | 4564 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 346 | 154 | 4 | 0 | 6405 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 345 | 6298 | 282 | 19 | 333464 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 344 | 22 | 0 | 0 | 3588 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 343 | 572 | 7 | 2 | 5833 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 342 | 2 | 0 | 0 | 619 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 341 | 9 | 0 | 0 | 1339 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 340 | 64 | 0 | 0 | 953 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 339 | 200 | 3 | 0 | 8151 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 338 | 60 | 0 | 0 | 2872 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 337 | 10507 | 34 | 28 | 155922 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 336 | 148 | 3 | 0 | 8932 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 335 | 2701 | 39 | 4 | 81557 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 334 | 1709 | 22 | 1 | 57075 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 333 | 461 | 3 | 2 | 9069 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 332 | 868 | 11 | 1 | 7422 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 331 | 6700 | 77 | 28 | 68201 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 330 | 156076 | 873 | 357 | 3447726 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 329 | 128 | 4 | 0 | 15668 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 328 | 204 | 1 | 0 | 5374 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 327 | 236 | 2 | 1 | 4869 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 326 | 7769 | 80 | 11 | 142228 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 325 | 812 | 10 | 0 | 14119 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 324 | 2566 | 40 | 2 | 50776 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 323 | 33 | 0 | 2 | 1514 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 322 | 129 | 2 | 0 | 3058 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 321 | 2798 | 11 | 3 | 110047 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 320 | 23660 | 120 | 139 | 371736 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 319 | 245 | 5 | 0 | 21911 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 318 | 0 | 0 | 0 | 183 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 317 | 8603 | 17 | 4 | 100225 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 316 | 481 | 7 | 1 | 15394 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 315 | 4080 | 103 | 4 | 155513 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 314 | 826 | 4 | 0 | 53943 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 313 | 161 | 0 | 1 | 9916 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 312 | 40 | 1 | 0 | 1017 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 311 | 1657 | 20 | 6 | 29685 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 42 | 1057 | 9 | 0 | 42507 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 41 | 120 | 3 | 0 | 4421 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 40 | 269 | 4 | 0 | 26619 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 39 | 287 | 4 | 0 | 28639 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 38 | 4720 | 43 | 40 | 381432 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 37 | 16706 | 123 | 72 | 385041 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 23 | 42 | 2 | 0 | 7064 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 22 | 11 | 0 | 0 | 2612 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 21 | 67430 | 200 | 76 | 1095008 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 20 | 27119 | 267 | 212 | 296792 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 19 | 83 | 0 | 0 | 4111 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 17 | 981 | 29 | 5 | 46414 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 16 | 468 | 5 | 3 | 58224 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 15 | 7165 | 48 | 7 | 90816 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 14 | 34389 | 413 | 44 | 612838 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 10 | 5926 | 57 | 18 | 229902 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 9 | 11304 | 160 | 5 | 140864 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 8 | 25001 | 715 | 5 | 2233431 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 7 | 1138 | 10 | 1 | 107349 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 6 | 224809 | 578 | 518 | 6620097 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 5 | 4877 | 12 | 0 | 221410 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 4 | 2515 | 33 | 2 | 60881 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 3 | 40568 | 249 | 255 | 729152 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 2 | 4243 | 40 | 3 | 128831 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 1 | 2689 | 29 | 17 | 171324 | NULL | reads:inserted;likes:inserted;comments:inserted;impressions:inserted |
| 292 | 8972→9796 | 437→475 | 47→51 | 112181→128099 | NULL | reads:value_changed;likes:value_changed;comments:value_changed;impressions:value_changed |
| 291 | 3162→3257 | 23 | 5→6 | 186739→190711 | NULL | reads:value_changed;comments:value_changed;impressions:value_changed |

完整前后值(含每个字段 old/new/action)在 `03_intelligence\metrics_public\technology-20261116T222204-b1.json` 的 `changes` 与 `outcomes` 里,可按 article_id 直接查。

## 六、下游是否真消费到了这些数据

- 爆款标签:`measured` 101 → 165,`missing_data` 105 → 66。
- 阈值标定落库(`articles_meta.viral_calibration`):样本 165 条真实测得,阈值 [0.0525, 0.0258, 0.0123],方法 `p90/p60/p25 of measured scores`,时间 2026-11-16T22:31:15+08:00;写前真实测得样本只有 101 条 ⇒ 阈值来源确实变新鲜了,但口径方法没改(仍是 p90/p60/p25,样本不足则拒绝改口径,绝不用合成样本拟合)。

关键词表现表(`keyword_performance`,22:31:32 重算,已入库):

| 关键词 | 采集到 | 优质文章 | 关键词得分 |
| --- | --- | --- | --- |
| 数据 | 99 | 99 | 0.8093 |
| 人工智能 | 22 | 22 | 0.4824 |
| 开源 | 12 | 11 | 0.4542 |
| 效率工具 | 0 | 0 | 0.0 |

`ttxt analyze --category technology` 终端实录(2026-11-16 22:31:32,爆款信号为即时计算值,未新增字段落库):

```json
{
 "status": "applied",
 "measured_samples": 165,
 "required": 30,
 "weight": 0.3,
 "calibration": "calibrated",
 "min_keyword_hits": 3,
 "keywords_with_signal": 3,
 "keywords_without_signal": 1,
 "details": {
  "人工智能": {
   "signal": 0.3636,
   "measured_hits": 22,
   "matched_articles": 22
  },
  "开源": {
   "signal": 0.4889,
   "measured_hits": 9,
   "matched_articles": 12
  },
  "数据": {
   "signal": 0.3642,
   "measured_hits": 95,
   "matched_articles": 99
  },
  "效率工具": {
   "signal": null,
   "measured_hits": 0,
   "matched_articles": 0,
   "reason": "命中的文章里没有真实测得表现的数据(缺失不等于 0 分)"
  }
 }
}
```

- 关键点:`效率工具` 的 signal 是 `null` 而不是 0,原因写的是「命中的文章里没有真实测得表现的数据(缺失不等于 0 分)」⇒ **关键词反哺没有因为缺数据而加分或扣分**;其余三个关键词的 signal 全部来自 165 条真实测得样本。
- 反哺入队:冷却中:距上次反哺入队还需 17.82 小时(窗口 24 小时),本次不入队;该来源累计第 3 次反哺尝试 —— 冷却窗口 24 小时未动,本轮不入队(`queued_job_id` = null),没有为了「跑通」而放宽冷却。
- 预测结算:已结算 19 条(此前实测回填),待结算 21 条,本轮 `settled=0`;核查原因:这 21 条预测对应的文章 tier 全是 `missing_data`(ifanr 11 / sspai 5 / ruanyifeng 1 / 已下架 4),没有真实分数就不结算,**不伪造 outcome**。

## 七、测试与体检

- 新增测试先红后绿:`10_tests/test_p33_metrics_ledger.py` 先跑 11 failed(缺 `outcomes`/`changes`/`--evidence`),实现后 12 条全过。
- 相关三件套:`test_p33_metrics_ledger.py` + `test_p21_public_metrics.py` + `test_p22_render_counters.py` = 68 passed。
- 全量回归:`590 passed`(本轮前基线 578,新增 12),0 failed,退出码 0。
- `ttxt doctor`:36 ok / 0 failed;WARN 仍是原有 5 条(demo 命名空间、history 谎报全文 1、demo 30 行来源页 URL、identity_suspect、9 条 evidence_links 缺 article_id),无新增 FAIL。

## 八、数据库实际变化与回滚

- 变化:`01_categories/technology/articles.db` 的 `article_metrics` +64 行(共 165 行)、`metric_snapshots` +66 条(共 181 条)、`viral_labels` 全量重算(231 行)、`articles_meta` 的 `viral_calibration` 键被新标定覆盖(旧标定在备份包的库文件里)、`keyword_performance` 4 行重算(22:31:32)。未新建表、未加字段、未动其他类目、未动写作与草稿表。
- 回滚位置与做法:停止指标采集(不再跑 `--fetch-public`);要回到写前状态,解压 `11_backups\p33-before-metrics-20261116-222203.zip` 里的 `01_categories/technology/articles.db`(连同 `-wal`/`-shm`),备份包内还含本轮改动的三个生产文件与 `00_control/config.json`,以及 `P33_baseline_technology.json`(写前基线数字)。代码回滚 = 回退本轮提交。
- 逐篇回退也可行:证据文件里每篇的 old/new/action 都在,可按 article_id 精确还原到写前值。

## 九、尚未解决的阻塞(未验证的写「未验证」)

1. 已下架文章反复进候选窗口(每轮 4 次白跑):需要「链接状态」字段或跳过清单,本轮范围禁止加字段 ⇒ **未做**。
2. ifanr/sspai/ruanyifeng 共 62 篇没有公开互动指标来源 ⇒ 这些文章的指标只能是 NULL,爆款判定继续按 `missing_data` 处理 ⇒ **本轮不改口径**。
3. `favorites`(收藏数)语义仍未取证,继续 NULL;`reads`/`impressions` 只在有内嵌 RENDER_DATA 且文章 id 归属已证明时才入库 ⇒ 未取证字段仍为 0 行,属预期。
4. 21 条预测未结算(缺真实指标)⇒ 不伪造 outcome;等这些来源出现可信指标来源才能结算。
5. 租约回收/整机重启后的自动恢复:本轮未涉及 ⇒ **未验证**。
6. 真人签署仍是 0/61,AI 裁定不能代替 ⇒ 见下表。

## 十、签署

| 项 | 结论 | 判定性质 | 真人签署 |
| --- | --- | --- | --- |
| 4 条 404 = 文章已下架(非抓取问题) | 成立(对照实验 + 真实 404 页 + robots 放行) | **AI 裁定,非真人结论** | 阿龙 |
| 66 篇指标为公开页真实值,非伪造 | 成立(逐页归属核对 + 前后值台账) | **AI 裁定,非真人结论** | 阿龙 |
| `favorites` 继续 NULL、无 0 值冒充 | 成立(0 行非空,0 行 0 值) | **AI 裁定,非真人结论** | 阿龙 |

> 上表「真人签署」列由本人(阿龙)于 2026-11-17 亲自填写为「阿龙」;AI 侧的裁定性质列保持不变,两类签署性质不混同。

## 十一、验收裁定记录(2026-11-17 追加,本人提供的原文照录)

下面这段由本人(阿龙)在 2026-11-17 提供,明确要求保存进项目验收报告。本文件如实照录,未增删一字;其中 AI 裁定与真人签署在性质上分开标注,本节不构成对任何未验证项的通过。

```markdown
# P33 阶段一技术验收裁定

**验收对象:** P33 阶段一——technology 类目真实公开指标补齐
**对应提交:** 698be523f6cee08c226c500291a45dba849531f9
**验收结论:** 有条件通过
**裁定性质:** AI 独立技术审查,非真人签署

经审查本轮提交的执行报告及证据说明,66 篇真实指标写入、4 篇文章 404 分类、数据库变更数量、测试结果和回滚依据具有相互对应的记录。

同意 P33 阶段一作为已完成的技术阶段进入下一阶段,但不得将本裁定解释为真人签署,也不得将尚未验证的租约回收、整机重启恢复、收藏数语义标记为已通过。

**后续必须处理:**

1. 解释 keyword_performance 得分与即时 signal 的不同计算口径。
2. 修正 451 与文章删除状态混用的问题,避免将法律限制误判为文章下架。
3. 对已下架文章建立可追踪的跳过机制,避免反复请求;修改前先列影响面、备份并补测试。

**AI 技术裁定人:** ChatGPT(GPT-6)
**裁定日期:** 2026-11-17
**真人签署人:** 阿龙(本人确认并签署)
```

本人的决定(同日):「P33 阶段一有条件通过,不推翻已经完成的工作,也不为了签署问题重做本轮。下一轮应直接进入 **P34:先修正指标口径说明和 451 分类,再处理 4 篇下架文章重复请求**;不碰 Strata,不新增无关功能。」

## 十二、口径更正指引(2026-11-17 追加,不改上文任何数字)

- 上文「六、下游是否真消费到了这些数据」中 `keyword_performance` 表的「关键词得分」是**综合得分**,与 `ttxt analyze` 打印的 **signal(爆款信号)不是同一个口径**:signal 只是综合得分里权重 0.3 的一个加数,两者不能互相替换、不能互相解释。完整公式与逐项复算证据见 P34 报告 `08_wiki/reports/p34-metrics-link-status-and-caliber-20261117.md`。
- 「451 也算 `article_gone`」这一分类在 P33 代码里确实存在,但**本轮 4 条实际返回的都是 HTTP 404,没有一条是 451** ⇒ 本节第四部分的结论不受该修正影响;分类本身已在 P34 修正(451 单列为法律/政策屏蔽,不再等同下架)。

