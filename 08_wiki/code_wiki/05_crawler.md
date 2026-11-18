# 05 · 采集子包 ttxt/crawler/（21 个文件）

数据流：`sources.json → crawl_jobs → runner/worker → fetcher(+robots/url_safety/browser_lock) → extract/toutiao → store.insert_items → articles 表`；后续 `pipeline`（清洗/聚类）与 `dedupe`（治理）作用于类目库。

## 5.1 sources.py — 来源注册表

- `SOURCES_PATH = 02_crawler/sources.json`（L11，唯一读取入口）。
- `@dataclass Source`（L14-55）字段：`id, name, category, kind(html|rss), url, enabled=True, rate_limit_s=1.0, max_pages=3, item_selector, title_selector, author_selector, next_selector, link_selector, base_url, demo=False, notes, extractor="generic"(generic|toutiao), render="http"(http|browser), max_articles=10, keywords=[], url_template(None, 含 {keyword}), fetch_item_pages=False, max_item_pages=10, item_content_selectors=[]`。
  - 注释明确：`url_template` 只对 robots.txt 放行的地址生效；百度/360/搜狗/头条搜索/Google News 均 disallow，**禁止配置**（"永不绕过访问控制"）。
- `load_sources(path=None) -> list[Source]`（L58）、`get_source(source_id)`（L66，未找到 KeyError）。

sources.json 现状：12 个来源（11 enabled）——toutiao_tech/finance/history（html+toutiao 适配器+browser 渲染）、sspai_rss、ruanyifeng_atom、eastmoney_rss、tmtpost_rss、ifanr_feed、who_cn_news、chinanews_society/culture（RSS + `fetch_item_pages=true` 正文补全）、quotes_toscrape（demo，disabled）。`notes` 记录 robots/RSS 取证史。

## 5.2 clean.py / cleaner.py / timeparse.py — 规范化与完整性

- `clean.py`：`TRACKING_PARAMS`（utm_* 等）；`normalize_text`（L15）、`excerpt`（L23）、`normalize_url(url, base)`（L28 → `articles.url_normalized` 去重主键之一）、`content_hash(title, content)`（L51，sha256(归一标题+正文) → `articles.content_hash` 去重主键之二）。
- `cleaner.py`：`NOISE_LINE_PATTERNS`（L9 广告/关注/免责行）；`clean_article`（L22）；`usable_length`（L48，优先 cleaned 口径）；**`MIN_FULL_CHARS = 400`**（L41）；completeness 三值 `full/short/excerpt`（L42-44）；`completeness_of_length`（L58：≥400→full，>0→short，0→excerpt）。
- `timeparse.py`：`normalize_datetime`（L16）解析 RFC822/ISO/中文日期/相对时间（"X分钟前"），归一为本地 ISO；不可解析返回 None（不造假时间）。

## 5.3 simhash.py / clusters.py — 指纹与聚类

- `simhash.py`：`tokenize`（L12，中文连续段切重叠双字词+英数词——**analysis/rag.py 索引分词复用此函数**）、`simhash64`（L46，词频加权 64 位）、`hamming`（L71）、`to_hex/from_hex`。
- `clusters.py`：`DEFAULT_THRESHOLD=3`（汉明距离≤3 同簇）、`LENGTH_RATIO_LIMIT=2.5`（防误并）、`BRUTE_FORCE_FALLBACK_THRESHOLD=32`（小规模全对比较，否则 banding 分块）；`content_fingerprint`（L35，清洗版本绑定的正文指纹，写 `pipeline_state.content_fp` 供增量判断）；`cluster_category`（L79）：DELETE article_clusters → UPDATE articles SET simhash/cluster_id → INSERT 簇表。

## 5.4 store.py — SQLite 模式与写入核心（最大文件，约 1300 行）

- 常量：`PROCESSOR_VERSION="p2-v1"`、**`SCHEMA_VERSION="p12-v1"`**、`CROSS_READ_CHUNK=400`、`CROSS_ID_COLUMNS={"url_normalized","content_hash"}`、`USABLE_LEN_SQL`（SQL 层可用长度表达式）、`ARTICLES_FTS_DDL`（L41）。
- Schema DDL 常量：`ARTICLES_SCHEMA`（L43）、`CLUSTERS_SCHEMA`（L71）、`PIPELINE_STATE_SCHEMA`（L87，`pipeline_state(article_id, content_fp, simhash, text_len, fp_at)` + `pipeline_state_meta(key,value)`）、`P3_COLUMNS`（L102 增量 ALTER 列清单）、`V3_SCHEMA`（L111）、`V4_SCHEMA`（L176，keywords/keyword_performance/viral_labels/article_metrics/metric_snapshots/knowledge_assets/prediction_records 等分析表）。
- `migrate_articles_db(category, db_path=None)`（L223）：幂等建库+分版本迁移。
- 路径/命名空间：`category_dir`（L261）、`articles_db_path`（L267）、`registered_namespaces()`（L274 系统库注册表）、`namespaces_on_disk()`（L292 扫盘）、`namespace_of_db`（L308）、`read_only_connect`（L328）。
- **跨类目守卫**（核心机制）：`_column_hits`（L349，列名白名单=CROSS_ID_COLUMNS，按 400 行分批 IN 查询）、`cross_namespace_hits(article_urls, hashes, exclude_path)`（L368，扫其余全部命名空间库）、`_namespace_blockers`（L413）。
- `insert_items(...)`（L453，写 articles 的唯一主通道）：计数器 `inserted/duplicates/cross_blocked/identity_conflicts/upgrades/kept_existing`；命中他库 url_normalized/content_hash → `cross_blocked+=1` 跳过；同 URL 内容升级走 `UPDATE articles SET content=...`（L617）；可接受外部预计算 `cross_report`（doctor 用 `db.cross_overlap` 对账）；P20 正文升级才写键（L748）。
- 完整性维护：`completeness_plan`（L796）、`apply_completeness_plan`（L837 条件 UPDATE）、`relabel_completeness(apply=False)`（L886）、`restore_completeness`（L895 按报告回滚）、`count_articles`（L930）。
- **crawl_jobs 队列 API**（全部经 store）：`create_crawl_job`（L944）、`find_pending_crawl_job`（L984）、`load/save_dispatch_state`（L1002/1030）、`crawl_job_params`（L1046）、`list_queued_crawl_jobs`（L1074）、`mark_job_started`（L1098）、`finish_crawl_job`（L1111 整体覆盖 meta_json）、`abort_crawl_job`（L1132）、`fail_crawl_job`（L1152，attempts<3 回到 queued）、`requeue_failed_crawl_jobs`（L1185）、**`claim_next_crawl_job(owner, lease_s, category)`**（L1200，进入 running 的唯一通道，写 owner+lease）、`renew_lease`（L1255）、`recover_stale_crawl_jobs(lease_s=1800)`（L1274，过期 lease 的 running→failed）。

## 5.5 url_safety.py — SSRF 防护（纯逻辑，不联网）

- `ALLOWED_SCHEMES={"http","https"}`、`REDIRECT_STATUSES={301,302,303,307,308}`、`DEFAULT_MAX_HOPS=5`、`EXTRA_BLOCKED_V4/V6`（额外黑名单段）。
- `Verdict/HopResponse/Hop/WalkResult/UnsafeURLError` 数据结构（L57-94）。
- `_embedded_ipv4`（L105，拆穿 IPv6 内嵌 IPv4）、`_ip_is_bad`（L119 私有/环回/保留/链路本地）、`is_blocked_ip`（L139）、`resolve_host`（L148）、`_cached_lookup`（L159）。
- `check_url`（L171，scheme+解析后每个 IP 校验）、`check_cached`（L222）、**`walk(fetch_hop, url, max_hops)`**（L227，手动逐跳跟随重定向，每跳先 check 后 fetch，防 DNS 重绑定/重定向绕过）。

## 5.6 robots.py — 采集闸门

- `RobotsRules`（L31，disallow/allow/crawl_delay/`allowed(path)`）；`parse_robots`（L70）。
- `fetch_robots`（L127，抓 robots 时禁自动重定向 `_NoRedirect`，每跳经 url_safety 校验）。
- **`gate(url, user_agent, cache, timeout=10.0) -> (RobotsRules, bool)`**（L178）：按 host 缓存的规则闸门，runner/keyword_crawl/metrics_pages 采集前统一调用；**不放行则一次请求都不发**。

## 5.7 fetcher.py — 抓取层（Scrapling HTTP-first、按需浏览器）

- 常量：`TERMINAL_STATUSES={401,403,404,410,451}`（不重试）、`RETRYABLE_STATUSES={429,500,502,503,504}`、`DEFAULT_DISK_CACHE_MB=64`、`PROFILE_LOCK_FILES`（L195）。
- `FetchError` / `FetchResult`（L169/177）。
- 浏览器 profile 治理：`browser_profile_dir()`（L184，`02_crawler/.browser/profile`）、`_clear_stale_locks`（L198）、`_foreign_browser_using`（L247，外部 Edge 占用检测）、`_browser_kwargs`（L264，Edge channel、代理 host 绑定 `ssrf_host_resolver_rules` L131/157）。
- 会话：`browser_session(wait_s=None)` 上下文管理器（L389，内部经 `browser_lock.acquire`）、`session_context`（L441）。
- 抓取内核：`_scrapling_get`（L450，StealthyFetcher `stealthy_headers` HTTP-first，curl_cffi 底座）、`_http_hop`（L471，喂给 url_safety.walk）、**`_install_request_guard(page)`**（L498，浏览器内子请求同样过 SSRF 检查）、`_hook_service_workers/_stop_service_worker`（L546/566，阻断 SW 绕过）、`_fetch_once`（L582）、`_assert_start_allowed`（L586）。
- **`fetch_page(...)`**（L603，主入口：url_safety 逐跳校验 + 重试 + 磁盘缓存 + http/browser 分流）、`page_bytes/page_text`（L672/680）。
- 第三方：scrapling、patchright/playwright、browserforge。项目内：config、browser_lock、url_safety。

## 5.8 browser_lock.py — 浏览器 profile 跨进程互斥

- `LOCK_FILE_NAME="profile.lock"`（在 profile 目录**之外**：`02_crawler/.browser/profile.lock`）、`DEFAULT_WAIT_S=600.0`、`ProfileBusyError`。
- `acquire(tag, wait_s, poll_s)`（L182）上下文管理器：msvcrt 区域锁 + 锁文件内 JSON 持有人（`holder_info` L156）+ `held_by_live_process`（L269，pid 探活清野锁）+ `probe_free`（L259）。OS 释放机制保证强杀不留死锁。

## 5.9 extract.py / toutiao.py — 抽取

- `extract.py`：`ExtractedItem`（L21）；**链接归因链**（C-7b 机制，L39-139）：逐条链接只采信"标题元素自身/标题内链接 → 锚文本=标题 → 块内唯一可用链接"，全部不成立则明确计"没拿到逐条链接"，**绝不把列表页 URL 冒充文章 URL**；`extract_html_items`（L141）、`extract_rss_items`（L215）；正文补全 `MIN_BODY_CHARS=160`（L294）、`extract_article_body`（L310，按 `item_content_selectors` 顺序试；猜不到保留原摘要不编造）；`keyword_hit`（L334）。
- `toutiao.py`：今日头条频道适配器——`canonical_article_url`（L25）、`extract_listing`（L38，频道页逐篇链接+时间/来源元数据 META_RE）、`extract_article`（L55）、`collect`（L98，browser_session 内翻页采集，逐篇限速、逐 URL robots gate）。

## 5.10 runner.py / worker.py — 队列消费

- `runner.py`：`resolve_category`（L19，拒绝越界路由）；`crawl_source`（L43，手工入口：建 job→执行）；**`run_next(owner, lease_s, category)`**（L59，唯一认领通道 claim_next_crawl_job→_execute_job）；`run_queued`（L95）；`_enrich_item_bodies`（L106，RSS 摘要补正文：fetch_item_pages/max_item_pages 封顶、逐 URL robots gate、仅同域；预算<配额时告警"明知能补正文却先入库摘要级"）；`_execute_job`（L190，来源检查 abort 原因 source_missing/source_disabled/category_refused → extractor 分流 → insert_items(带 cross_report) → finish/fail_crawl_job；robots disallow 直接钉 failed）。
- `worker.py`：`STOP_FILENAME="worker_stop"`（落 `02_crawler/worker_stop`）、`worker.lock` 单实例互斥（msvcrt）、`DEFAULT_LEASE_S=900.0`、`WorkerAlreadyRunning`；`run_worker`（L80）循环：recover_stale → run_next → lease 续期/空闲退避 → 检查 stop 文件退出。

## 5.11 pipeline.py — 存量重清洗管线

- `CLEAN_VERSION = "p3-v1"`（L31）。
- `run_pipeline(category, full=False)`（L34）：migrate_articles_db → 读 `pipeline_state` 台账（content_fp 未变更则跳过，增量）→ 逐篇 clean_article + normalize_datetime 回填 published_at + UPDATE articles（cleaned_content/text_length/cleaned_at/clean_version）→ 台账写 simhash（与 clusters 同口径）→ 清理已删文章的台账行 → `clusters.cluster_category(clean_version=...)`。
- 注释（L122）明确单向依赖：FTS 索引重建归 analysis 层，本层不碰。

## 5.12 dedupe.py — 库内/跨库去重与回滚（约 620 行）

- `PK_REFERENCE_TABLES=("viral_labels","article_metrics")`（每文章唯一，须迁移或剥离）、`ROW_REFERENCE_TABLES=("metric_snapshots","evidence_links")`（随行迁移）、`FTS_TABLE="articles_fts"`。
- `load_page_urls()`（L63，读 `02_crawler/page_urls.json` 页面 URL 台账）——列表页地址绝不作为文章身份。
- `classify(rows, page_url_set)`（L115）：按 url_normalized/content_hash 分组，keeper=可用正文最长的一条。
- `plan_category`（L189）/`apply_plan`（L267，strip_references→DELETE→FTS 同步）；`plan_cross`（L341）/`apply_cross`（L398，经 store.cross_namespace_hits）；**`rollback(report)`**（L498，按 ledger 逐条恢复误删）；`summarize()`（L560，doctor 用）。

## 5.13 keyword_crawl.py — 关键词驱动采集（反哺执行端）

- `DEFAULT_KEYWORD_LIMIT=6`；`category_keywords`（L42，从 keywords/keyword_performance 取 gap 与高分词）；`plan_category`（L93，用来源 `url_template({keyword})` 生成检索页计划，逐 URL 过 robots.gate，被 disallow 的引擎直接不出计划）；`enqueue_plan`（L203，写 crawl_jobs 携带关键词上下文）；`plan_and_enqueue`（L234，供 `analysis.keywords.auto_feedback` 调用）；取证落 `03_intelligence/keyword_crawl/`。

## 5.14 metrics_pages.py — 头条公开指标页回收（约 800 行）

- `SOURCE_ID="toutiao_public_article"`、`PUBLIC_HOSTS={www.toutiao.com, m.toutiao.com}`、`METRIC_FIELDS=("reads","likes","favorites","comments","impressions")`、`UNPROVEN_FIELDS=("favorites",)`（口径未证实→置 NULL）。
- outcome 枚举（L60-66）：`metrics_ok / no_public_counts / article_gone(404/410) / blocked_by_legal(451) / access_blocked(401/403/429) / fetch_failed / robots_refused`——失败必须归因，不混成一锅。
- 链接状态台账：`LINK_STATUS_KEY="metrics_link_status"`（存 pipeline_state_meta）、`GONE_RETRY_DAYS=90`（判定 gone 的文章 90 天内不重复请求）。
- 页面解析：`RENDER_SCRIPT_ID="RENDER_DATA"`（头条 Next.js 数据岛）、`parse_count_text`（"1.2万"→12000）、`_walk_counter_blocks(..., near_id)`（L312，**计数块必须绑定到本文章 id**，防抓别人的计数）、`extract_public_metrics`（L407）。
- 主流程：`_candidates`（L457，缺指标且 URL 在白名单 host，轮转防饥饿）→ `round_plan`（L503）→ `collect_public_metrics`（L523，robots gate → fetcher（http 优先、浏览器按需经 browser_lock）→ 解析 → **写库经 `ttxt.analysis.metrics_import.write_metric_rows`（本模块不直接 INSERT 指标表）** → 更新 link status）。

## 5.15 stackcheck.py — 抓取栈体检

- `DIST_PACKAGES=("scrapling","curl-cffi","patchright","playwright","browserforge","lxml")`；`allowed_params()`（L41，inspect scrapling 签名防版本漂移）；`profile_bytes()`（L77）；`doctor_checks()`（L233）返回 (name,status,detail) 供 doctor 消费。
