# P12 批次3证据报告:素材质量与正式来源白名单

生成时间:2026-11-12 23:25(+08)。所有数字来自真实运行与只读审计,非演示数据;凡缺失字段一律记 NULL,不回填。

## 1. 本批目标与结论

- 为已注册类目建立「正式来源白名单」:每个来源必须 **robots 允许 + feed/页面可解析 + 条目新鲜度实测** 三关全过才注册。
- 素材入库必须携带:原始 URL、作者、发布时间、抓取时间、正文证据(content + content_hash + completeness)。缺失的如实为 NULL。
- 演示来源与正式素材隔离(类目路由守卫已有,本批复核)。
- 结论:**technology / finance / health 获得正式白名单来源并真实入库;history / social 当前没有任何合格候选源,如实留空**(探测证据见 §3)。

## 2. 新注册来源(全部真实采集验证)

| 来源 | 类目 | kind | 配额 max_articles | 注册依据(2026-11-12 实测) |
|---|---|---|---|---|
| eastmoney_rss | finance | rss | 30 | robots 允许,HTTP 200,feed 96 条全部当日发布(06:07–22:30),全文约 11 KB/条 |
| tmtpost_rss | finance | rss | 20 | robots 允许,HTTP 200,17 条当日发布,摘要约 260 字,中英双语 |
| ifanr_feed | technology | rss | 20 | robots 允许,HTTP 200,20 条(11-10→11-12),短摘要 |
| who_cn_news | health | rss | 25 | robots 允许,HTTP 200,25 条(09-15→11-10) |

RSS 配额封顶是本批新增的治理(`runner.py`):此前 RSS 分支不截断全量 feed,东方财富单源一次就有 96 条全文(约 800 KB),会瞬间灌满类目库并扭曲爆款统计。现在未显式传 limit 时按来源 `max_articles` 封顶,并把 `feed_total`(feed 全量)写入观测——**不谎报截断**。

## 3. 候选探测完整台账(拒绝的必须留证据)

探测脚本 `99_temp/p12b3_probe_sources.py`、`p12b3_fresh_check.py`(gitignored);原始结果已固化为:
- [p12b3-probe-results.json](08_wiki/reports/p12b3-probe-results.json)
- [p12b3-fresh-results.json](08_wiki/reports/p12b3-fresh-results.json)

| 候选 | 类目 | 判定 | 证据 |
|---|---|---|---|
| ftchinese_news | finance | **robots_denied** | robots.txt 禁止,不得注册 |
| gmw_health | health | **robots_denied** | 同上 |
| geekpark_rss | technology | **robots_denied** | 同上 |
| 36kr_feed | finance | not_feed | HTTP 200 但正文非 XML feed(25 KB HTML) |
| thepaper | social | not_feed | 同上(57 KB HTML) |
| zaobao_china | social | not_feed | HTTP 200,非可解析 feed |
| chnmuseum | history | error | HTTP 404(终态,不重试) |
| dpm_org | history | error | HTTP 404(终态,不重试) |
| chinanews_scroll | social | error | HTTP 404(终态,不重试) |
| dxy_rss | health | error | 站点重定向到 127.0.0.1,curl SSRF 防护拒绝(3 次重试后终态) |
| people_health | health | 陈旧不注册 | 100 条全部 2025-05-30→06-05,距今约 17 个月(存档 feed) |
| xinhuanet_pol | social | 陈旧不注册 | 300 条 published_at 全 NULL,样本 URL 为 2022-12 存档 |

**history / social 无合格源**是实测结论而非疏忽;后续批次将继续探测候选(优先历史学会/博物馆类可解析 feed 与地方社会新闻 feed)。

## 4. 真实 Worker 执行记录(owner=`p12b3-whitelist` / `p12b3-retry26`)

| job | 来源 | 结果 | feed_total | 入库 | 耗时 |
|---|---|---|---|---|---|
| 23 | eastmoney_rss | done | 96 | 30(封顶生效) | 1.77 s |
| 24 | tmtpost_rss | done | 17 | 17 | 0.75 s |
| 25 | ifanr_feed | done | 20 | 20 | 1.06 s |
| 26 | who_cn_news | 首轮 failed → 重试 done | 25 | 25 | 1.16 s |

Worker 报告 `done:4`,stop_reason=`max_jobs`,全程无模型调用(`model_jobs` 26→26 不变;Strata `/health` 前后一致,模型 `qwen3.8-flash-next-iq3_s`,GPU 未受影响)。

## 5. 真实缺陷与修复:robots_unknown 被钉死终态

job 26 首轮 `robots.txt 无法确认(5xx/网络异常)` → 代码用 `finish_crawl_job("failed")` 直接终态化,但 `attempts=1/3`,任务永远滞留 `failed`——**transient 错误必须走可重试通道**。

- 修复:`runner.py` robots_unknown 分支改走 `store.fail_crawl_job`(attempts<max → 回 queued;达上限 → failed),保留 fail-safe(unknown 一律不放行抓取)。
- 回归测试 `test_robots_unknown_job_requeued_not_terminal`:首轮回 queued、attempts 计数正确、达 3 次上限落终态、全程零抓取。
- 真实验证:`requeue_failed_crawl_jobs` 重排 job 26 → Worker 重试成功(25/25 入库)。这同时证实首轮失败确为瞬时故障(同 URL 第二次 robots 抓取即成功)。

## 6. 字段完整性审计(新入库条目,只读抽查)

| 来源 | 条数 | url | 作者 | 发布时间 | 抓取时间 | 正文长度 |
|---|---|---|---|---|---|---|
| eastmoney_rss | 30 | 30/30 | **0/30(feed 未提供,NULL)** | 30/30 | 30/30 | 310–26844 |
| tmtpost_rss | 17 | 17/17 | **0/17(NULL)** | 17/17 | 17/17 | 13–261 |
| ifanr_feed | 20 | 20/20 | **0/20(NULL)** | 20/20 | 20/20 | 57–138 |
| who_cn_news | 25 | 25/25 | **0/25(NULL)** | 25/25(09-15→11-10) | 25/25 | 35–142(摘要) |

「原始 URL/作者/发布时间/抓取时间/证据」映射:`url` / `author` / `published_at` / `fetched_at` / `content+content_hash+completeness`。RSS 源普遍无作者——**如实 NULL,不回填**;批次4 的逐事实 URL 绑定将以此为基础。

## 7. 类目库终态(只读审计)

| 类目 | 条数 | 构成 |
|---|---|---|
| technology | 62 | toutiao_tech 29 + ifanr 20 + sspai 10 + ruanyifeng 3 |
| finance | 47 | eastmoney 30 + tmtpost 17(首次有正式数据) |
| health | 25 | who_cn_news 25(首次有正式数据) |
| history | 0 | 无合格源(§3) |
| social | 0 | 无合格源(§3) |
| demo(隔离命名空间) | 45 | quotes 30 + sspai 12 + ruanyifeng 3;历史遗留保留不删 |

`crawl_jobs` 台账:26 条 = done 19 + aborted 7(历史终态记录),queued/running/failed 均 0。`credibility` 全部 `unverified`——这是批次5 可信度分层的输入,本报告不谎称已核查。

## 8. 测试

`python -m pytest 10_tests -q` → **136 passed in 20.31 s**(批次3新增 2 个:RSS 封顶 `test_rss_source_max_articles_caps_full_feed`、robots_unknown 重排回归)。

## 9. 已知偏差(如实记录,未在本批处理)

- `01_categories/demo` 是未注册类目命名空间(45 条历史数据)。路由守卫已阻止演示源写入正式类目(jobs 18/19 aborted 即守卫证据),但 `analyze/clean` CLI 会把 demo 当类目处理;正式素材与演示数据的消费隔离将在批次4 写作取材入口强制。
- `07_tmp` 目录不在注册目录树中(历史遗留);控制库存在历史遗留的空 `sources` 表。均保留不删,仅记录。
- 09_logs 的 JSONL 混有测试桩(`fake_html`)条目,日志取证必须按测试运行排除。

## 10. 边界声明

- 本批未触碰 Strata(不重启/不停机/不换模型),未启动任何自动采集常驻进程,无发布动作。
- 现有证据链(FTS/simhash)是启发式相关性,不是事实核查;批次4 以逐事实 URL+原文连续片段绑定替换。
- 修改文件:`ttxt/crawler/runner.py`(RSS 配额封顶 + robots_unknown 重排)、`02_crawler/sources.json`(新增 4 源,更新 notes)、`10_tests/test_p12_crawler_hardening.py`(+2 测试)。取证脚本在 `99_temp/`(gitignored),原始探测 JSON 已固化进本报告目录。
