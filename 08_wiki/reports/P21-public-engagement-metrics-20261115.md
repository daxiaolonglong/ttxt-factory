# P21:头条公开互动指标入库(第 4 轮完成取证,2026-11-15 16:3x–17:0x)

交接页 §十一 的第 4 轮待办第 1 项(R2-4)本轮完成。新增规则 **41**。

> **⚠️ 更正(2026-11-15,同日 P22)**:本报告里「**收藏 / 展现量公开页根本没有**」这一条**判错了**。
> P21 只探了渲染后的可见 DOM,没查页面内嵌的结构化 JSON。P22 实测:公开文章页内嵌
> `<script id="RENDER_DATA">`(percent-encoded JSON)带本文章的 `readCount`(阅读)与 `showCount`(展现),
> 且 `diggCount/commentCount` 与本报告核验的工具条数值逐位相同(文章 292:437 / 47)。
> ⇒ **阅读与展现量现在从公开页真值入库**,`no_impressions` 卡死的判断随之作废;只有**收藏**仍然不可得。
> 取证、实现与真实数据前后对比见 `08_wiki/reports/P22-render-data-metrics-20261115.md`,规则 41 已同步更正、新增规则 42。
> 本报告其余结论(工具条/评论区归属、卡片数不得归属本文章、可信度分级合并、默认 dry-run)仍然有效。

## 一、为什么要做(根因)

接手时**六个类目的 `article_metrics` 和 `metric_snapshots` 全是 0 行**,`viral_labels` 全部 `missing_data` / `unscored`
(证据:`07_tmp/r24_metrics_probe/inventory.json`)。
⇒ 用户目标里"把头条的收藏/阅读/点赞/其他数据记进数据库、模型学会区分爆款和垃圾、再把关键词送回采集"这一整条链路
**没有任何互动信号可用**:爆款判定不出、阈值无法标定(`calibration.sample = 0`)、写出来的稿子无法用真实数据结算准确率。

## 二、先做实测,不猜(探测证据都在 `07_tmp/r24_metrics_probe/`)

| 探测 | 实测读数 | 结论 |
| --- | --- | --- |
| probe1(`probe1.json` / `probe1_run.txt`) | robots.txt **允许** `/article/`;纯 HTTP 拿到的 3 个 URL 页面**一模一样 72,914 字符**,是 JSVMP 空壳 | 纯 HTTP 在头条**不可能**拿到真实数字;必须正常渲染 |
| probe2(`probe2_render.json` / `probe2_run.txt`) | 浏览器渲染:文章 1 → 200 / 3.81 s / 168,777 字符 / body 6,239 字 / 真标题;文章 2 → 200 / 2.59 s | 用项目已有 Scrapling 浏览器会话即可,不需要反检测工具(规则 38) |
| probe3(`probe3_selectors.json` / `probe3_run.txt`) | 本文章工具条 `div.detail-side-interaction` aria-label:文章 1 **点赞 437 / 47 评论**,文章 2 **点赞 23 / 5 评论**;同页另有"16万阅读"(`li.related-list-item p.info span`)、"1628评论"(`div.feed-card-footer-comment-cmp`)、评论行内"点赞7"(`ttp-comment-like` 下) | 只有工具条与评论区标题属于本文章;卡片数是**别的文章**;收藏/展现量公开页**根本没有** |
| inventory | 六类目 0 指标;technology 有 **112 行落在 `www.toutiao.com`** | technology 是最好的实跑对象 |

**诚实口径写死在代码里**:能测到的只有本文章的点赞 / 评论(阅读只在别人的卡片上出现且被"万"取整;收藏、展现公开页没有)。
所以 `reads / favorites / impressions` 对公开页**保持 NULL**,只能由后台导出 `toutiao_admin_export` 补 —— 不编造。

## 三、改了什么

| 文件 | 行数 | 内容 |
| --- | --- | --- |
| `ttxt/crawler/metrics_pages.py` | **新增 313 行** | 公开页采集器:`host_allowed`(config `metrics.public_pages.hosts` 白名单,仿冒/子域拒绝)、`parse_count_text`(支持 `万/w/k`、逗号;**垃圾文本 → None**)、`extract_public_metrics`(只认本文章工具条 + `#comment-area .title`,**卡片数绝不归属**)、`_candidates`(按 host 过滤,`host_skipped` 计数)、robots 逐 URL 门禁(不放行/未知 → 拒绝,绝不换 UA)、默认 dry-run、`PermissionError` 拦未授权 source、`collect_with_browser` 整次共用一个浏览器会话 |
| `ttxt/analysis/metrics_import.py` | 310 行(diff +136/−22) | 合并口径**唯一实现** `write_metric_rows`:`NULL 不覆盖已知值`、**可信度分级**(`verified 5 > self_reported 4 > public_page 3 > public_card 2 > unverified 1`,低不层高 → `protected_by_credibility`;同级新的非 NULL 胜 → `filled_from_null` / `value_changed` / `null_kept`)、整行全 NULL 拒绝、`credibility_rank` 读 config;`import_metrics` / `snapshot_current_metrics` / `relabel_after_import` 全部保留且口径不变 |
| `ttxt/__main__.py` | 744 行(+24) | `ttxt metrics --fetch-public [--limit N] [--apply] [--sleep S]`:默认 dry-run,只有 `--apply` 写库;`PermissionError` → exit 2 |
| `00_control/config.json` | 115 行(+16) | `metrics.authorized_sources` 增 `toutiao_public_article: public_page`;新增 `metrics.credibility_rank`、`metrics.public_pages`(hosts / limit_per_run 20 / sleep_s 2.0) |
| `10_tests/test_p21_public_metrics.py` | **新增 524 行 / 28 项** | 用 probe3 的**真实 DOM 片段**写测试(工具条 / 评论块 / 推荐卡片 / 文章头),不靠造出来的 HTML |
| `07_rules/CORE_RULES.md` | 57 行(+1) | 规则 41:归属正确 + 可信度分级合并 + 测不到留 NULL |

## 四、测试(实现前先失败,规则 24)

| 步骤 | 读数 | 回执 |
| --- | --- | --- |
| 先写测试、跑 | **exit 2 — `ImportError: cannot import name 'metrics_pages'`** | `07_tmp/r24_metrics_probe/p21_red.txt` |
| 实现后单文件 | **28 passed / 1.86 s / exit 0** | `07_tmp/r24_metrics_probe/p21_cli.txt` |
| **全量** | **367 passed / 67.62 s / exit 0**(接手基线 339;+28) | `07_tmp/r24_metrics_probe/p21_full_suite.txt` |

测试锁住的关键行为:卡片数字绝不归属本文章;评论块标题算本文章评论;`万`/逗号解析;真实 0 保持 0;非白名单 host `host_skipped`;
robots `unknown=True` → 0 次抓取;dry-run 不落库;公开页**不覆盖**后台导出(`protected_by_credibility=2`,credibility 仍 `self_reported`);
后台导出**补空**(`filled_from_null=2`);同级新的非 NULL 胜、NULL 保留;全 NULL 行拒绝;未授权来源 `PermissionError`;CLI 默认 dry-run。

## 五、真实数据前后对账(生产库,`technology`)

写库前先备份:**`07_tmp/r24_metrics_probe/backup_pre_p21/technology_articles_pre_p21.db`(4,874,240 字节,sqlite backup API)**

| 阶段 | 读数 |
| --- | --- |
| 之前(`state_before.json`) | articles 159 / article_metrics **0** / metric_snapshots **0** / tiers `{missing_data: 159}` / labels `{unscored: 159}` |
| dry-run limit 5(`p21_dryrun_tech.json`) | fetched 5 / fetch_errors 0 / **would_write 5**;292→437/47、291→23/5、290→3/0、289→236/1、288→78/6;reads/favorites/impressions 全 NULL |
| **apply limit 12**(`p21_apply_tech.json`,exit 0) | written **12** / snapshots **12** / filled_from_null **24** / protected 0 / null_kept 0 / robots_refused 0 / host_skipped 0 / fetch_errors 0 |
| 之后(`state_after_tech.json`) | article_metrics **12**(source 全 `toutiao_public_article`,credibility 全 `public_page`)/ with_likes 12 / **with_reads 0** / **with_impressions 0** / tiers `{missing_data: 147, no_impressions: 12}` / labels 仍 `{unscored: 159}` |
| 重跑 limit 2(`p21_apply_tech_rerun.json`) | **written 0 / updated 2 / snapshots 2** ⇒ 同一文章不重复行,时序累加 |
| 快照对账 | metrics 12 / snapshots **14** / 2 篇文章各有 2 次快照 / 快照 source 全 `toutiao_public_article` |
| `ttxt doctor` | **29 ok / 0 failed**,WARN 仍是原有 4 条(`namespaces.disk`、`db.identity_suspect`、`db.url_identity`、9 条空 `evidence_links`),**没有新增问题** |
| `ttxt analyze --category technology` | exit 0;feedback `gap_keywords: ["效率工具"]`、`queued_job_id: 54`(已有排队任务不重复入队)、证据文件 `03_intelligence/keyword_feedback/technology-feedback-20261115T170056+0800.json`;viral tiers `{missing_data 147, no_impressions 12}`、calibration `default_thresholds / sample 0` |

如实说明:`gap_keywords` 变化**不是**本轮指标带来的(`keywords.py` 的 gap 判定只看 `keyword_performance.collected`),
是 P20 关键词闸门把 `效率工具` 记成 0 覆盖导致的。指标带来的真实变化是**互动数与时间序列开始存在**、
分档从 `missing_data` 走到 `no_impressions`。

## 六、网络与磁盘纪律

- 本轮真实抓取共 **19 次文章页 + 2 次 robots**,每次间隔 2.0 s(config 可调),全部 200、0 错误。
- 磁盘净增:备份库 4.87 MB(取证用,不删)+ 生产库内 12 行指标(库文件字节数未变,WAL 内)+ 报告与回执。

## 七、边界与残留风险(如实)

1. **展现量 / 收藏仍缺**:`impressions` 只能来自头条后台导出 ⇒ 爆款判定停在 `no_impressions`,`labels` 仍 `unscored`,
   `calibration.sample = 0`。需要用户从创作后台导出 CSV/JSON,再用已就绪的 `ttxt metrics --import --source toutiao_admin_export` 入库(合并不会覆盖已测到的点赞/评论,只补空)。
2. **样本只有 12 行**(112 行可采):阈值标定门槛 30 行 ⇒ 下一轮继续 `--fetch-public --limit N` 扩样,或按 R2-4b 用卡片"XX万阅读"作 `public_card`(近似值,记 `source_page`)补 reads,并给"只有互动数没有展现"的文章加**互动率**评分路径(点赞+评论 ÷ 阅读,重新归一)。
3. **卡片阅读数是近似值**(被"万"取整)⇒ 若采纳必须单独定 `public_card` 可信度(已在 rank 里预留 2 档),不得与精确值同级。
4. 页面结构会变(头条改版)⇒ 选择器失效时 `no_metrics_skipped` 会涨;`parse_count_text` 对读不懂的文本返回 None,不会写假 0。
5. 未做:DDL 变更(0 项)、C-5 可信度分档、`_element_text` 标题缺陷 + `PROCESSOR_VERSION` bump、`pytest-of-yangl` 清理(需批准)。

## 八、回滚(只描述,不执行)

- 代码回滚:`git revert <P21 commit>`(本轮提交见交接页 §十二)。
- 数据回滚:把 `07_tmp/r24_metrics_probe/backup_pre_p21/technology_articles_pre_p21.db` 覆盖回 `01_categories/technology/articles.db`,
  或按 SQL 精确撤:删 `article_metrics` / `metric_snapshots` 里 `data_source='toutiao_public_article'` 的行,再 `ttxt analyze --category technology` 重建派生标签。
- 配置回滚:`00_control/config.json` 的 `metrics` 三段(authorized_sources / credibility_rank / public_pages)。
