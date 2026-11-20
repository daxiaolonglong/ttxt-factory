# R55 探针口径并回正式链路 + 关键词驱动小批量(2026-11-20)

> 状态:**清单已放行 → 已实施 → 已实测**(job 386,2026-11-20 14:2x;逐行 diff 既有行变化 0 行)。
> 放行范围:仅 §1 三项,不许扩;风险 B 走"跑前只读预检 + 批次排除已采 URL",**不接受就地升级既有行**;风险 C 照实报命中率。
> 前置:R54 收工 commit `c36e969`,探针 `scripts/r54_crawler_probe.py` 四阶段 PASS。

## 0. 摸底结论(诚实版,决定本轮改动量)

探针验证的四段"渲染列表→链接安全闸→限速抓正文→清洗去重入库"**已经在生产链路里**:
- `runner.crawl_one`(runner.py:273-288):`render=="browser"` 来源开 `fetcher.browser_session()`(跨进程 profile 锁 + SSRF 双层),`toutiao.collect` 内部就是 列表页→逐链接 `rules.allowed` robots 闸→`fetch_page(render=True, session)`→`time.sleep(delay)`;
- `delay = max(source.rate_limit_s=2.0, robots Crawl-delay)`(runner.py:268)——2 秒限速已是既有纪律;
- `keyword_crawl.plan_category`(P19)把类目库关键词(=gap 优先)变成带 `keyword_filter`/`url_override`(同 host 限定)的采集计划,`runner` 入库前跑 `extract.keyword_hit` 闸(`keyword_filtered_out` 计数,不静默丢);
- 入库一律 `store.insert_items`:BEGIN IMMEDIATE 单事务,URL 身份 + content_hash + 跨库归属三道闸,**只插不改**——唯一就地写是 P20 闸(同 URL+同标题+正文更长⇒就地升级,`bodies_upgraded`)。

头条搜索/百度新闻等检索 URL 全部 robots Disallow(P19 实测 07_tmp/p19_keyword_crawl),所以关键词驱动的**唯一合规形态** = 频道页采集 + `keyword_filter` 入库闸。

⇒ 本轮**不重写链路**,改动只有下面 3 项,其中既有代码只碰 1 处。

## 1. 影响面清单

| # | 文件 | 性质 | 改动内容 | 调用方 | 回滚 |
|---|---|---|---|---|---|
| 1 | `ttxt/crawler/toutiao.py:collect()` L120-121 | **包装(重试)**,唯一碰既有代码处 | `links = extract_listing(...)` 外套"首帧空列表⇒重取,≤3 次,每次间隔 3s"(R54 实测:13:43 一次 0 链接,13:44 起 16/15 条;探针同款已验证)。已有链接时行为零变化 | 仅 `runner.crawl_one` 经 `collect=module` 调它(toutiao_tech/toutiao_social) | git revert 单 hunk |
| 2 | `scripts/r55_batch.py` | **新增文件** | 小批量驱动:调 `keyword_crawl.plan_category("technology")` 出计划→入队(crawl_jobs.meta_json 带 `keyword_filter`)→`runner.run_queued(category="technology", limit=小)`;逐篇回执写 `99_temp/r55_batch/`。不复制任何采集/去重逻辑 | 无(独立运维脚本) | 删文件 |
| 3 | `08_wiki/reports/r55-pipeline-integration.md` + wiki/PROJECT_STATUS | 新增/追加 | 本报告 + 状态同步 | — | git revert |

**明确不改**:ttxt/crawler 其余全部(fetcher/store/runner/robots/dedupe/clean/extract/keyword_crawl)、`02_crawler/sources.json`(toutiao_tech 已启用)、`articles` 既有行与 schema、`资产库/**`、`00_control/config.json` 模型段、浏览器配置段。

**浏览器引擎决策(不改)**:生产浏览器路径 = Edge(`config.browser.executable_path=msedge.exe`),profile 跨进程锁、SSRF 双层、`--host-resolver-rules` 全部按 Edge 实测建立(P32/P36/P38 取证)。本轮不切——换内核=换指纹+换锁语义,风险最大、收益为零。R54 为对照装的 patchright 备用引擎**已在本轮摘除**(见 4.7):代码里"`executable_path` 置空即自动回退到自带 chromium"的机制保留,但项目内不再留 752.8MB 二进制;摘除后冒烟复验生产路径仍走 Edge(200 / 282,114 字节 / 23.9s)。

## 2. 为什么不能只留探针脚本

探针写 `r54_probe_articles` 旁表,绕过了生产闭环的全部五层:
1. `crawl_jobs` 台账(重试上限/审计/调度器可见);
2. `store.insert_items` 三道闸(探针只**读**去重判定,不拦截写入;生产才拦);
3. FTS/聚类/`pipeline_state` 增量(爆款判定的语料底座);
4. `keywords`/`keyword_performance` 反哺回路(关键词→采集→覆盖统计→再反哺);
5. `article_metrics`/指标回收(爆款反馈的数据来源)。
旁表没有这五层 = 采集到的东西进不了"爆款判定→关键词反哺"闭环,只是取证品。

## 3. 风险项(已按用户要求先停报)

- **风险 B(就地写)**:`insert_items` 的 P20 闸在"同 URL+同标题+正文更长"时会就地升级既有行(`bodies_upgraded`)。10 篇里若撞到今天已被调度器采过的文章,该既有行的 `content` 会被更长正文替换(保留 id/URL/fetched_at)。**这是既有闸门语义,不是新代码**;放宽=违规。处置:跑前对既有 904 行做只读指纹快照(baseline),跑后逐行 diff,若有 `bodies_upgraded` 如实上报逐行清单;其余既有行保证零变化。
- **风险 C(关键词命中率)**:`keyword_filter` 模式下频道页 15 条链接里标题命中关键词的可能只有几条,`keyword_filtered_out` 会高、实际入库可能不足 10 篇。处置:关键词取 gap 词"效率工具"+高分 active 词(库内 13 词,perf 已有);不足就如实报分布,不放宽闸、不挑词凑数。

## 4. 实测结果(job 386,2026-11-20 14:23,technology 类目)

放行范围核对:只落了 §1 三项 —— `toutiao.py` 首帧重取包装、新增 `scripts/r55_batch.py`(只驱动)、本报告 + 账本。**没有扩项**:fetcher/store/runner/robots/dedupe/clean/extract/keyword_crawl 一字节未改,`sources.json` 未改,去重实现没另写、没放宽任何一道闸。

### 4.1 跑前只读预检(风险 B 第二选项的落实)

| 预检项 | 内容 | 产物 |
|---|---|---|
| 指纹快照 | 904 既有行 × 16 列(文本列转 `_len`,不含正文原文) | `99_temp/r55_batch/precheck_baseline.json` |
| 当日已采 URL 名单(只读) | 178 行 `id/url/url_normalized/title/source/fetched_at`(`substr(fetched_at,1,10)=date('now')`,来自 job 314/332/350/368 等当日采集)+ 全库 904 条 `url_normalized` | `precheck_today_urls.json` |
| 并发写库者 | queued/running/failed 任务 = `[]`,否则直接 abort(退出码 3) | `precheck_pending_jobs.json` |
| 一致性备份 | `sqlite3` backup API → `99_temp/`,26.5MB(跑后已删,见 4.7) | `technology_articles_backup.db` |
| 碰撞预检 | 批次候选 10 条链接 vs 当日 178 名单 / 跑前 904 名单 ⇒ **交集 0** | `listing_pool.json.collision_with_precheck` |

**为什么这批里没有它 / 为什么这条会被拒**:当日 178 条 URL 没有一条出现在本批拿到的链接池里(池错位见 4.6 缺陷②),逐条答案统一是"它不在本批频道页给出的链接集合内";而入库的 8 条 URL 与当日 178 名单、跑前 904 名单同样**零碰撞** ⇒ `articles.url_normalized` 身份闸只会判 new,**本轮不存在"同 URL 命中既有行"的路径,`bodies_upgraded` 结构上只能为 0**。名单已留档,可逐条复核。

### 4.2 批次回执(`crawl_jobs` id=386,status=done)

```
ok=true  robots_allowed=true  source=toutiao_tech  category=technology
pages_fetched=11  listing_links=13  feed_total=10  items_extracted=8  inserted=8
duplicates=0  keyword_filtered_out=2  keyword_hits={'数据': 7, '全球': 1}
bodies_upgraded=0  bodies_kept_existing=0  url_identity_conflict=0
article_errors=0  errors=0  robots_skipped=0  pagination_stop=null
pipeline: cleaned=8  dates_normalized=8  clusters=903  near_duplicate_pairs=14  indexed=8  clean_version=p3-v1
elapsed_s=61.98  metrics_linked=8
plan: keywords=[效率工具,数据,中国,全球,工程,国家]  url_override=null  reason=keyword_filter
```

链路核对:`keyword_crawl.plan_category("technology")` → `enqueue_plan`(`meta_json` 带 `keyword_filter`)→ `runner.run_next(category="technology")` → `toutiao.collect`(Edge 会话、逐链接 robots 闸、2s 限速)→ `extract.keyword_hit` 闸 → `store.insert_items` 三道闸。驱动脚本自身不采集、不去重、不写库。

### 4.3 逐篇清单

**入库 8 篇**(id 1039–1046;全部 `source=toutiao_tech`、`fetched_at=2026-11-20T14:23:34+08:00`):

| id | 标题 | 正文 | completeness | 去重结论(URL 身份 / content_hash / simhash 最近汉明距) |
|---|---|---|---|---|
| 1039 | 10天超长续航!新品官宣:高亮大屏,11月20日开售 | 1,097 字,有正文 | full | new / 无重复 / 19 |
| 1040 | 刚刚,DeepSeek张博创业曝光:开源个人Agent | 2,974 字,有正文 | full | new / 无重复 / 18 |
| 1041 | 原来找一个人这么简单!打开微信,按一下就能知道对方位置了 | 1,546 字,有正文 | full | new / 无重复 / 19 |
| 1042 | 美国发现一个离谱真相:中国根本不是对手,而是打不动的底层系统 | 2,506 字,有正文 | full | new / 无重复 / 18 |
| 1043 | 价格大跳水!"月初还要2万元,现在只要1.6万元"… | 1,318 字,有正文 | full | new / 无重复 / 18 |
| 1044 | Meta内部聊天记录显示,Muse隐私泄露问题引发员工担忧 | 1,398 字,有正文 | full | new / 无重复 / 20 |
| 1045 | 腾讯公关总监张军谈年轻人重新用QQ:微信和QQ都用,"我的QQ号七位数呢" | **310 字(残缺)** | **short** | new / 无重复 / 19 |
| 1046 | 美光新加坡HBM先进封装厂首部机台搬入,预计2027年初产出 | 628 字,有正文 | full | new / 无重复 / 18 |

`published_at` 8/8 齐全(4 条 11-20 当日、3 条 11-19、1 条 09-16);`clean_version=p3-v1`;全库 `completeness` 只有 `full`/`short` 两值。**正文最短 310 / 最长 2,974 字**。

**关键词闸卡掉的 2 篇**:`feed_total=10 − items_extracted=8 = 2`。**这 2 条无法逐篇归属** —— 台账(`crawl_jobs.meta_json`)只有计数,不记录被卡条目的 URL/标题,而本批的链接池未被持久化(见 4.6 缺陷②)。照实按聚合计数报,不猜标题。

### 4.4 拒因分布(10 篇候选)

| 结论 | 计数 | 依据 |
|---|---|---|
| 入库 new | 8 | `inserted=8`;URL 身份/content_hash/simhash 三闸全过 |
| `keyword_filtered_out` | 2 | 标题+列表页摘要未命中本轮 6 词(闸门在正文补全**之前**跑,见 4.6③) |
| `duplicates` | 0 | URL 归一化后库内无同址 |
| `bodies_upgraded` / `bodies_kept_existing` | 0 / 0 | 预检零碰撞,结构上不可能触发 P20 就地升级闸 |
| `url_identity_conflict` | 0 | 跨库归属闸无冲突 |
| `article_errors` / `errors` / `robots_skipped` | 0 / 0 / 0 | 无抓取异常,无 robots 拒抓 |

### 4.5 限速与异常记录

- 逐篇限速 `delay = max(source.rate_limit_s=2.0, robots Crawl-delay)` = **2.0s**,11 页 / 61.98s 与 10 × 2s + 渲染耗时一致;未并发、未批量快取。
- 不登录、不绕验证、无验证码、无 cookie 注入;robots 全程放行(`robots_allowed=true`、`robots_skipped=0`),搜索类 URL 未碰。
- **首帧空列表重取未触发**:`pages_fetched=11` = 1 列表页 + 10 正文页 ⇒ 列表页只抓 1 次就拿到 13 条链接。观测字段 `listing_attempts` 只在 `collect()` 返回,`runner` 未透传到回执 ⇒ 该轮只能由 `pages_fetched` 反推,已记为待议观测缺口(不在放行范围,未改)。
- 无连续失败、无重试硬刷;`pagination_stop=null`(头条频道页无 next 链)。

### 4.6 如实缺陷(不粉饰)

1. **10 篇里 1 篇正文残缺**:id 1045 仅 310 字,链路无"最短正文长度"闸 ⇒ 照样入库,只被 `completeness=short` 标记。这是既有闸门语义,本轮不新增闸(不在放行范围)。另有 id 1046 仅 628 字仍判 `full`,该标记的阈值口径偏松,登记待议。
2. **2 篇无法逐篇归属 —— 台账与频道页动态流的组合缺陷**:
   - `crawl_jobs` 不记录被关键词闸卡掉的条目 URL/标题,只记计数;
   - 头条频道页是客户端渲染的**动态流**:预检渲染的 14 条池(`listing_pool.json`)与 job 实际用的 13 条池**交集 0** —— 两次渲染相隔约 2 分钟整屏换血。已把链接池渲染移到入队前以缩短错位,但错位无法消除;
   - ⇒ 逐篇清单只有入库的 8 行可完全归属,其余按聚合计数报。**不猜、不补、不美化**。
3. **命中率虚高来自词太宽,不是闸门失效**:本轮关键词取库内 gap 词 `效率工具` + 高分 active 词 `数据/中国/全球/工程/国家`。`extract.keyword_hit` 匹配 `标题 + item.content`,而闸门跑在**正文补全之前**,所以实际只看标题 + 列表页摘要;`keyword_hits` 按**首个命中词**计数 = `数据 7 / 全球 1`,其余 4 词零首命中(预检池里标题级命中也只有 1 条,词是 `中国`)。宽词在摘要层几乎必中 ⇒ 命中率不反映"关键词驱动"的真实选择性。**未换更松的词凑量、未放宽闸**(风险 C 照实办:10 篇候选 → 8 入库就是 8)。

### 4.7 磁盘与清理

- 摘除 R54 装的 patchright 备用引擎:`chromium-1248` 457.6 + `chromium_headless_shell-1248` 290.9 + `ffmpeg-1013` 4.0 + `winldd-1007` 0.3 = **752.8MB**;`02_crawler/.browser` 1492.6MB → **1035.0MB**;生产浏览器路径仍是本机 Edge,已冒烟复验(200 / 282,114 字节 / 23.9s)。
- `99_temp` 63.9MB → **0.8MB**:删 r55 备份 26.5MB、`r54_probe/browser-profile` 36.6MB、`pw_dl`;本轮 JSON 预检/回执留档(`precheck_*.json`、`listing_pool.json`、`job_receipts.json`、`diff_existing_rows.json`、`per_item_list.json`、`summary.json`、`verify_facts.json`),用完即删的一次性对账脚本(`post_diff.py`、`verify_facts.py`)已删。项目目录内无新增日志堆积。
- `02_crawler/.browser/profile`(Edge profile **739.8MB**)属生产态(跨进程 profile 锁在用),未动,登记待议:是否需要定期清理与锁安全边界的关系。

### 4.8 红线核对与回滚

- 既有 904 行:**逐行 diff `existing_rows_changed = []`,一字节未改**(16 列指纹全等);904 → 912 只来自 8 条 INSERT。`bodies_upgraded=0` ⇒ 无需回滚,未触发停手条件。
- schema 未改;`资产库/**` 只读;`00_control/config.json` 模型段与浏览器段未动;本轮零模型调用;只写 technology 当期库(其余类目库未碰)。
- 回滚路径:①代码 `git revert`(toutiao.py 单 hunk / r55_batch.py 删文件);②数据 `DELETE FROM articles WHERE id BETWEEN 1039 AND 1046` 并同步 FTS/聚类(有备份时可整库还原,备份已按用户要求跑后清理 —— 如需保留请另行指定)。

### 4.9 测试(只跑改动涉及的,不跑全量)

`10_tests/test_p12_crawler_hardening.py`(**新增 2 项**:首帧空列表重取 3 次后成功、连续空列表封顶 3 次不硬刷)+ `test_p9_toutiao.py` + `test_p25_toutiao_channels.py` + `test_p22_render_counters.py` = **61 passed**。
