# P18 · C-7b 抽取器 URL 身份塌陷整改报告(2026-11-15)

对应待办:`P17-session-handoff-20261115.md` §五 待办 2。本轮只做代码与体检,**没有**写任何生产库、没有改来源配置、没有删任何行。

## 一、结论(实测)

| 项 | 修复前 | 修复后 |
| --- | --- | --- |
| 列表页每条的 `url` | 一律 `= source.url`(30 行同一个 URL) | 逐条取自己的链接;取不到就回退当前页**并打标** `url_is_page_fallback=True` |
| 入库"同 URL 不同内容" | 静默混进 `duplicates`,与真重复不可分 | 计数 `url_identity_conflict` + 逐条 `LOG.warn`;身份位仍不插入,非身份页 URL 的行保留 |
| URL 身份体检 | 只有事后 `dedupe` 普查(`db.identity_suspect`) | 新增 `doctor.db.url_identity`:责任来源仍启用 = **FAIL**,已停用来源历史 = WARN |
| 真实列表页取到逐条链接 | quotes 0/30(全同一 URL) | books.toscrape **20/20**;quotes 0/40(站点根本没有逐条链接,含 `/js/` 版) |
| 测试 | 298 passed | **314 passed**(新增 `10_tests/test_c7b_url_identity.py` 16 条) |
| `ttxt doctor` | 28 ok / 0 failed,3 WARN | **29 ok / 0 failed**,4 WARN(新增 `db.url_identity` WARN) |

命令与读数(均为本轮实测):

| 命令 | 读数 |
| --- | --- |
| `D:\ttxt\.venv\Scripts\python.exe -X utf8 -m pytest -q` | `314 passed in 66.93s` |
| `D:\ttxt\.venv\Scripts\python.exe -X utf8 -m ttxt doctor` | `summary: 29 ok, 0 failed`;`[WARN] db.url_identity: 1 组共 30 行把来源页/订阅地址当文章地址,责任来源今天都已停用(历史残留)` |
| `07_tmp/p18_c7b_probe/probe_live_demo.py` | `汇总:取到逐条链接 0 条,明确标记回退 40 条(不编造地址)`(4 页 × 10 条) |
| `07_tmp/p18_c7b_probe/probe_books_demo.py` | `items=20 own=20 fallback=0`;不配 `link_selector` 也是 `own=20`(标题本身就是链接) |
| `07_tmp/p18_c7b_probe/probe_js_demo.py` | 浏览器渲染 `https://quotes.toscrape.com/js/`:`a[href^='/quote/'] 命中 0`、`a.text 命中 0` |
| `07_tmp/p18_c7b_probe/probe_title.py` | Scrapling `element.text` 只取直接文本:`<h2 class="title"><a>标题</a></h2>` → `.text` 为 `''` |

## 二、问题与根因

P17 待办 2 原文:

> **C-7b 修抽取器身份塌陷**:`ttxt/crawler/extract.py:31-58` 的 `extract_html_items` 把 `url=source.url` 赋给每一条 → 列表页 30 行同一个 URL。要改成取每条自己的链接;入库时"同 URL 不同内容"不得静默,要计数 + 记日志;配一条 `doctor.db.url_identity` 体检。做完才能谈 `demo` 里那 30 行。

三条根因(实测):

1. `extract.py` 老实现把来源页地址赋给每一条 —— 塌陷的制造点。
2. `store.insert_items` 只按 `content_hash` 去重,"同 URL 位上正文互不相同"被计进 `duplicates`,与"真重复"混在一起,塌陷当场不可见(静默 3 天,直到 C-7 普查才发现 30 行)。
3. 体检没有 URL 身份维度,只有删数据前的一次性普查脚本 —— 事后工具,不是哨兵。

## 三、写进代码的口径

| 位置 | 口径 |
| --- | --- |
| `ttxt/crawler/extract.py:89` `_candidate_links` | 逐条链接取证顺序:①来源点名 `link_selector`;②标题元素自身是 `<a>`;③标题元素内部的 `<a>`;④锚文本与标题**可信相等**;⑤都不成立 → **不猜** |
| `ttxt/crawler/extract.py:74` `_title_match` | 可信相等 = 完全相等,或互相包含且**短的那条 ≥20 字符**。纯"互相包含"实测会把标签锚文本 `change` 当成标题命中 |
| `ttxt/crawler/extract.py:17` `NON_ARTICLE_HREF` | `#` / `javascript:` / `mailto:` / `tel:` / `data:` / `blob:` 不是文章地址 |
| `ttxt/crawler/extract.py:141` `extract_html_items` | 页内唯一性:候选链接在本页出现次数 `spread` 必须 = 1 才采信(共享链接不是逐条链接);基准 = **当前页** `page_url or source.url`,相对链接按当前页解析;取不到 → `url=base` + `url_is_page_fallback=True`(`extract.py:31`) |
| `ttxt/crawler/sources.py:32` | 新增 `link_selector: str \| None = None`(来源可点名逐条链接) |
| `ttxt/crawler/store.py:493` | 新增 `page_urls`:每一页/列表页/订阅地址都进 `non_identity_urls`,身份判定跳过它们 |
| `ttxt/crawler/store.py:532` `same_content_elsewhere` | 同 URL 但正文相同(本批或库内)= 真重复;正文不同 = 塌陷 |
| `ttxt/crawler/store.py:553` / `:572` | 两类塌陷分别计数:身份 URL 位上正文不同 → `insert.url_identity_conflict`(仍不插入);非身份页 URL 上正文不同 → `insert.page_url_collapse`(**行保留**,规则 33)。两者都进 `url_identity_conflict` 计数 |
| `ttxt/crawler/store.py:629-639` | 返回值只在非零时多出 `url_identity_conflict` / `url_no_item_link`(打标条目数),老两键 `inserted`/`duplicates` 语义不变 |
| `ttxt/crawler/runner.py:156/219/261` | 每抓一页就 `listing_pages.append(url)`,整批传 `page_urls`;`:264-278` 采集侧汇总告警 `crawl.url_identity_conflict` / `crawl.no_item_link` |
| `ttxt/doctor.py:342` `_url_identity_row` | 只读连接扫每个命名空间:`GROUP BY source,url_normalized`;命中已知来源页/订阅地址就报;责任来源**仍启用** → FAIL(`:406`),都已停用 → WARN(`:397`),干净 → PASS(`:390`)。消息里写死"不得删除" |

设计边界(必须写清,不能夸大):**本次保证的是"不会静默塌陷"** —— 每条要么有自己的链接,要么明确打标回退并被计数/体检;**不保证**"猜出来的链接语义正确"。正因如此,取不到可信链接时选择不猜。

## 四、把方案推回去的三次实测翻转(为什么最终没有兜底猜链接)

| 版本 | 规则 | 实测结果 |
| --- | --- | --- |
| v1 | 兜底:取块内第一个可用链接 | `probe_candidates.py`:候选首位是 `/author/Albert-Einstein`(来源未配 `link_selector` 时 `_link_hrefs(block, None)` 返回块内全部链接)→ 会把作者页当文章地址(候选顺序实测;落库后果推断) |
| v2(中间实现,已删除) | 只信"块内唯一链接" + 锚文本与标题互相包含 | `probe_live_demo.py` 首轮:30 条里 **14 条**被写成 `/tag/change/page/1/` 这类标签页假地址 |
| v3(最终) | 唯一链接也不采信;锚文本必须与标题可信相等 | `probe_live_demo.py` 复跑:4 页 40 条 → **0 条假地址、40 条明确回退** |

判断依据:**假身份比塌陷更隐蔽**。塌陷会被 `doctor.db.url_identity` / `db.identity_suspect` 抓到;一个看起来正常的 `/tag/...` 地址永远不会再报警,却把 30 篇不同文章的身份指到了同一类标签页上。

## 五、对既有测试的影响(语义没有放松)

1. `10_tests/test_c2_url_dedupe.py`:9 处断言补上 `url_identity_conflict`(列表页 30 行那处 = **29**,第一条占位不算冲突)。原语义不变 —— "只插一次"、"仍然拒绝"、"30 行仍然保留"逐条保持;并新增 `again == {"inserted": 0, "duplicates": 30}` 明确证明"重采同一批是真重复,不得谎报成塌陷"。
2. `10_tests/test_c3_cross_category_dedupe.py:268`:同名计数补进断言,跨类目结论不变(URL 相同仍不互斥合并,拦下的是 `content_hash`)。
3. 测试替身 `FakeElement`(`test_c2:382`、`test_p12_crawler_hardening:62`、`test_p12_worker_lease:65`)补 `css()` / `tag`:实测 Scrapling 真实元素都有 `tag/text/attrib/css`(`probe_title.py`),而老替身从来没被调用过 `css`(老抽取器不向块元素要链接)。默认"块内没有链接"正好对应"列表页没给逐条链接"。这是替身补齐契约,不是放宽产品断言。
4. 规则 34 遵守:新用例全部在 tmp 目录构造临时库,没有对生产库内容做断言。
5. "实现前先失败"证据(规则 24):新测试文件先落地时 `test_each_item_gets_its_own_link` 在旧实现下失败;全量改造后一度 **18 failed**(c2 9 处 + c3 1 处 + p12 hardening 6 + p12 worker_lease 2),逐条修完替身与预期后才 314 全绿。

## 六、`demo` 那 30 行的处置(待办 2 尾句"做完才能谈")

1. **不删、不改写**(规则 33):30 行是真实语料,只是 URL 身份塌在来源页上。
2. 站点事实(实测):quotes.toscrape 普通页 4 页 40 条**没有一条**逐条链接;`/js/` 版用浏览器渲染后 `a[href^='/quote/']`、`a.text` 命中 0 → 该站不存在逐条文章地址,**配 `link_selector` 也救不了**。
3. 所以这 30 行的 URL 身份**修不好**(不是代码缺陷,是站点没有这个信息)。`doctor.db.url_identity` 现在把它们分级为 WARN(责任来源 `quotes_toscrape` 已停用);一旦有人重新启用且仍塌陷 → **FAIL**,不许静默通过。
4. 可选整改(需批准,本轮未做):(a)换列表页 —— 实测 `books.toscrape.com` 20/20 取到逐条链接(配不配 `link_selector` 都行);(b)接受现状,把 quotes 条目录为"来源页 URL + 显式回退标记"的已知局限,并在 `sources.json` 的 `notes` 里登记。

## 七、顺手发现的新缺陷(不在 C-7b 范围,本轮未修)

| 缺陷 | 实测证据 | 影响 | 建议 |
| --- | --- | --- | --- |
| 标题元素是 `<h2 class="title"><a>标题</a></h2>` 时 `_element_text` 取到空串 | `probe_title.py`:Scrapling `element.text` 只取**直接**文本,不取后代文本 | 标题退化为整块文本(把标签、作者混进标题),`content_hash` 与 FTS 内容跟着漂;也是 v2 里 `change` 命中"标题"的成因 | 下一批修 `_element_text`(取全部后代文本 / 去标签),但必须同时决定 `PROCESSOR_VERSION` 是否 bump |
| `PROCESSOR_VERSION` 本轮**没有** bump(仍 `p2-v1`) | `ttxt/crawler/store.py` 未改该常量 | 抽取器口径变了但清洗版本没变,历史行不会重算 | 需要人拍板:重算会改全库 `content_hash`(推断),牵连去重与簇 |

## 八、未做(明确列出)

- 没有改 `02_crawler/sources.json`(`quotes_toscrape` 仍 `enabled:false`),没有新增 books.toscrape 来源。
- 没有重采、没有写生产库;`db.url_identity` 走 `store.read_only_connect` 只读连接。
- 没有修 `_element_text` 标题缺陷,没有 bump `PROCESSOR_VERSION`。
- C-5 可信度分档、C-6 评审元数据 DDL + 9 条无出处 `evidence_links`、Worker/Scheduler 启动、history/social 来源清单、`D:\ttxt\pytest-of-yangl` 清理:都没动(需批准或需人给口径)。
- 没有新增备份(本轮零写操作,`articles` 行数仍 312)。

## 九、改动清单与回滚

| 文件 | 变更 |
| --- | --- |
| `ttxt/crawler/extract.py` | +163/-… :候选链接规则、`_title_match`、`_link_hrefs`/`_own_href`/`_anchor_pairs`、`page_url` 参数、`url_is_page_fallback`、RSS/Atom 链接绝对化与打标 |
| `ttxt/crawler/sources.py` | +5:`link_selector` 字段 |
| `ttxt/crawler/store.py` | +95:`page_urls`、`same_content_elsewhere`、两类塌陷计数、`batch_hashes`、汇总键 |
| `ttxt/crawler/runner.py` | +32:`listing_pages` 逐页登记、`page_urls` 透传、采集侧告警 |
| `ttxt/doctor.py` | +74:`_url_identity_row` 与注册(PASS/WARN/FAIL 三级 + 读失败兜底 WARN) |
| `10_tests/test_c7b_url_identity.py` | 新建 16 条用例 |
| `10_tests/test_c2_url_dedupe.py`、`test_c3_cross_category_dedupe.py`、`test_p12_crawler_hardening.py`、`test_p12_worker_lease.py` | 预期补计数;`FakeElement` 补 `css`/`tag` |
| `07_rules/CORE_RULES.md` | 追加规则 35(不得编造文章地址) |

回滚:`git revert <本次提交>`。数据侧零改动(没有 UPDATE/DELETE),`articles` 仍 312 行;新增计数只出现在 `crawl_jobs.meta_json`、日志与 doctor 输出里,回滚不影响已有数据。备份沿用 P17 §七 的 `11_backups/`,本轮未新增。
