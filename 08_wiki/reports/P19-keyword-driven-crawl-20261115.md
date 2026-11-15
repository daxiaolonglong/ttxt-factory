# P19 + P20(2026-11-15 15:0x–16:2x):关键词驱动采集 + 正文补全 + 语料自我变好

对应交接页 §10.2 的 **R2-2 / R2-3 / R2-5**。R2-1(采集栈核验)已记在规则 30 与 P15 报告;R2-4(头条互动数据)按要求留给 R3。

## 一、结论(先给读数)

| 项 | 整改前 | 整改后 | 取证 |
| --- | --- | --- | --- |
| social 类目行数 | **0** | **22** | `01_categories/social/articles.db` |
| social `completeness` | — | full 20 / short 2 | 同上 |
| social 正文长度 | 一轮:61 / 924.1 / 2571(min/avg/max) | **136 / 1127.9 / 2571** | `07_tmp/p19_keyword_crawl/verify_p20_20261115T1615.json` |
| history 类目行数 | **0** | **20** | `01_categories/history/articles.db` |
| history `completeness` | 一轮:full 12 / short 8 | **full 19 / short 1** | 同上 |
| history 正文长度 | 一轮:75 / 554.3 / 1344 | **402 / 891.6 / 1369** | 同上 |
| 关键词闸门一次运行的网络成本 | job 66:feed 30 条 → **抓 20 个文章页 / 33.31 s** | job 69:feed 30 条 → **抓 2 个文章页 / 4.31 s** | `crawl_jobs` id 66 vs 69 |
| 同 URL 长正文的处理 | job 66:**3 次假 `url_identity_conflict`**,正文被拒之门外 | job 67/68:**各 8 行就地升级**(`bodies_upgraded: 8`),不再有假塌陷 | `09_logs/ttxt-20261115.jsonl` |
| 全量测试 | 314 passed(接手时) | **339 passed / 55.60 s / exit 0** | `python -m pytest 10_tests -q` |
| `ttxt doctor` | 0 failed | **29 ok, 0 failed**(3 条已知 WARN,全是 demo 命名空间历史残留) | `07_tmp/p19_keyword_crawl/p20_doctor.txt` |

生产库总行数:312 → **354**(social 22、history 20)。

## 二、P19 根因(三条,全部实测)

1. **关键词从来进入不了采集动作**:`analysis/keywords.py:auto_feedback` 只会给类目第一个启用来源入一条普通任务(`:154`),关键词只停在库里打分 ⇒ "补采"永远采同一个固定地址。
2. **RSS/列表页只给标题级摘要**:中新网七个 feed 每条 `description` 中位数 71–108 字(`07_tmp/p19_keyword_crawl/probe3.json`),直接入库只能得到"标题 + 一句摘要",爆款研究没有正文可学。
3. **通用正文选择器在这两个站点全部落空**:`probe5.json` 17 个候选全 0;真实容器要用来源配置点名 —— `div.left_zw`(819 字/9 段)、`div.content_maincontent_content`(833 字),见 `probe7_tightest_container.json`。猜不到就返回空串保留摘要,**绝不拿导航栏冒充正文**。

## 三、P19 改了什么

| 文件 | 内容 |
| --- | --- |
| `ttxt/crawler/keyword_crawl.py`(新增) | `category_keywords`(缺口关键词优先,其次分数降序)、`plan_category`(带 `url_template` 的检索源逐词出计划并**先过 robots 预门禁**,不合格的当场 `rejected`:cross_host / robots_disallow / robots_unknown;不带模板的来源走订阅源:空库 → `cold_start`(不上闸门,回执写 `cold_start: true` + `note`),非空库 → `keyword_filter` 闸门)、`enqueue_plan`(已有同类待办任务复用,不重复入队)、`plan_and_enqueue`(计划逐条落 `03_intelligence/keyword_crawl/<cat>-plan-<stamp>.json`) |
| `ttxt/crawler/sources.py` | 新字段:`keywords`、`url_template`、`fetch_item_pages`、`max_item_pages`、`item_content_selectors` |
| `ttxt/crawler/extract.py` | `extract_article_body(page, source)`(点名选择器,`MIN_BODY_CHARS=160`,拿不到就返回空串)、`keyword_hit(item, keywords)`、`ExtractedItem.url_is_page_fallback` |
| `ttxt/crawler/runner.py` | 读取任务 meta 里的 `url_override`(仅同 host)/`keyword_filter`;`_enrich_item_bodies`:逐条再过 robots、有上限、失败保留原摘要并计数、全损走可重试通道 |
| `02_crawler/sources.json` | 新增正式来源 `chinanews_society`(social)、`chinanews_culture`(history),robots 放行、feed 新鲜(2026-11-14/15),正文选择器点名 |
| `ttxt/__main__.py` | `crawl-plan`(`--category/--keyword/--source/--keyword-limit/--apply`,默认干跑) |
| 探测取证 | `07_tmp/p19_keyword_crawl/probe{2,3,4,5,6,7}_*.{py,json}`:合格源与淘汰源都要留读数 —— `people.com.cn/rss/*`(2025-06 陈旧)、`chinanews /rss/jk.xml`(2026-03 陈旧)、Google News RSS / so.toutiao.com / 360 / 搜狗 / 百度新闻 / `gmw.cn/rss/`(robots 不放行)一律拒绝;澎湃列表页正文在哈希类名 + `__NEXT_DATA__` 里,需要 JSON 适配器,本轮明确不做 |

## 四、P20 三处根因(来自 job 66 的实测回执,不是推测)

job 66(15:53,social 第二轮)回执:`feed_total 11`、`items_extracted 11`、`item_pages_fetched 20`、`keyword_filtered_out 19`、`inserted 2 / duplicates 9`、`url_identity_conflict: 3`、耗时 33.31 s。三条根因:

1. **闸门排在正文补全之后** —— 先为 30 条抓了 20 个文章页(33 秒真实网络成本),再扔掉 19 条;补全预算全花在"本轮不入库"的条目上。而且回执把闸门后的数量写进 `feed_total`,看起来像"订阅源只剩 11 条",**读数是假的**。
   整改(`runner._execute_job`):配额裁剪 → **关键词闸门** → 正文补全,全部排在 `with session_cm` 之内;`items_extracted` 在闸门之后统计,`feed_total` 只写订阅源真实条数。日志 `crawl.keyword_filter` 明确写"闸门只看标题与订阅摘要(补正文之前)",承认召回代价。
   实测对照:job 69(16:13,history,闸门开)→ `feed_total 30`、`keyword_filtered_out 18`、`item_pages_fetched 2`、4.31 s。
2. **同一篇文章拿到更完整正文会被永久拒收** —— `url_identity_conflict: 3` 经 `09_logs/ttxt-20261115.jsonl` 59–61 行核对,全是第一轮 RSS 摘要级(98/122 字)与第二轮文章页(900+ 字)同一 URL 同一标题的行:老规则把它当"身份塌陷"拒收,语料质量被锁死在摘要级,而且每轮重复报假塌陷。
   整改(`store.insert_items`):**同一规范化 URL + 同一标题 + 正文更长 ⇒ 就地升级这一行**(保留 id / URL / fetched_at,更新 `content/excerpt/content_hash/completeness/processor_version`),计数 `bodies_upgraded`;同一 URL + **不同标题**仍然是 `url_identity_conflict`,绝不改写标题(悄悄改标题就是 demo 那 30 行的做法)。升级行仍计入 `duplicates`,所以 `inserted + duplicates + cross_blocked == processed` 不变。标题比较只做空白折叠 + `casefold()`,不做相似度猜测。
   实测:job 67(social)`bodies_upgraded: 8` → short 8→2、avg 924.1→1127.9;job 68(history)`bodies_upgraded: 8` → short 8→1、avg 554.3→891.6。
3. **补全预算小于入库配额就会静默降级** —— 一轮 20 条配额只补 12 页,语料一半是摘要级却没有任何报警。
   整改:`max_item_pages` 提到 20(≥ `max_articles`),并在预算用尽时 `LOG.warn`(`item_pages_capped` + 整改建议:"把 max_item_pages 提到 ≥ max_articles,或降低 max_articles");`sources.json` 两个源的 `notes` 记下实测配额账。

## 五、测试(339 passed,55.60 s)

- `10_tests/test_p19_keyword_crawl.py` **23 条**:正文抽取器与"不冒充正文"、`keyword_hit`、计划→执行闭环(override 生效、计划回显在 `meta_json`)、跨 host 拒绝、补全计数/上限诚实/robots 拒跳过/全损走重试、`category_keywords` 冷启动与缺口优先、逐词检索计划与拒绝、干跑 + 复用去重、**闸门先于补全(抓取序列断言)**。
- `10_tests/test_c7b_url_identity.py` **18 条**(新增 2):**同 URL 同标题长正文 = 就地升级**(行只有一行、id 与 fetched_at 不变、正文/哈希/completeness 变新、不算塌陷)与**不同标题长正文仍是塌陷**(正文不变)。
- `10_tests/test_c2_url_dedupe.py`:同 URL 同标题、正文更长的重采案例断言从 `url_identity_conflict: 1` 改为 `bodies_upgraded: 1` —— **这条老断言在改实现当场翻转,就是新行为确实生效的证据**;等长正文的第三次重采仍被拒(`test_third_crawl_still_refused` 未改)。

**如实记录一条偏差(规则 16)**:P20 的三条新断言是与实现在同一批里写的,没有先做"实现前先失败"的红→绿;能证明断言绑定的替代证据是 (a) `test_c2_url_dedupe` 那条老断言在实现变更时翻红、(b) 生产回执前后对照(job 66 抓 20 页 / `feed_total 11` vs job 69 抓 2 页 / `feed_total 30`;job 66 `url_identity_conflict: 3` vs job 67/68 `bodies_upgraded: 8`)。下一批起恢复规则 24 的先红后绿。

## 六、闭环现状与下一轮

- `ttxt analyze --category social|history`:`index.indexed` 22 / 20(FTS 不再滞后)、知识资产各 1 条、`zero_coverage: 0`。
- 但 `viral.tiers.missing_data` = 22 / 20:**没有真实互动指标就分不出爆款与垃圾**,`analyze` 的 `feedback.gap_keywords` 为空也源于此 ⇒ R2-4(头条公开阅读/点赞/收藏入库,带 `provenance`)是下一轮的头号任务。
- 队列里别的窗口留下的待办未动:job 54 `toutiao_tech`/technology(browser 渲染)、job 63 `who_cn_news`/health。
- 仍待办:Worker/Scheduler 未启动;C-5 可信度分档全 `unverified`;9 条 `evidence_links.article_id` 为空(需批准 DDL 口径);`D:\ttxt\pytest-of-yangl` 残留清理(需批准);`_element_text` 标题缺陷要 bump `PROCESSOR_VERSION`(现 `p2-v1`)单独一轮。

## 七、回滚位置

- 提交前基线:`3d4ea15`(C-7b);本轮改动全部在未推送提交里,回滚 = `git revert` 该提交,代码/配置/测试一起回。
- 数据侧:`bodies_upgraded` 只改 `content/excerpt/content_hash/completeness/processor_version`,不改 id/URL/fetched_at;要退回整改前的语料状态用 `11_backups/C7-pre-dedupe-20261115-112912.zip`(C-7 时的整库备份,`Connection.backup()` 生成)。升级前的短正文仍可在 `09_logs/ttxt-20261115.jsonl` 的 `insert.body_upgraded` 逐条读出(记 url/旧新长度)。
- 配置侧:`02_crawler/sources.json` 两个新源可只置 `enabled: false`,不改代码即可停止采集。
