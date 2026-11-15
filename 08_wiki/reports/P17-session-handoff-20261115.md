# 会话交接 · 2026-11-15(C-2 → C-3 → C-1 → P15/C-4 → C-7)

用途:下一个窗口接着干时,先看这一页就知道"做到哪了、证据在哪、下一步做什么"。
本页只列事实,不写计划口号。

## 一、本窗口完成的事(按发生顺序)

| 序号 | 事项 | 关键证据 | 提交 |
| --- | --- | --- | --- |
| 1 | **C-2 同类目 URL 重复插入**:写入侧把规范化 URL 当文章身份,`check-then-insert` 放进同一个 `BEGIN IMMEDIATE` 写事务;来源页/订阅地址通过 `page_url` 参数排除在身份之外 | `07_tmp/c2_*`、`10_tests/test_c2_*`、规则 23/24 | `6e3eed8`(已推送) |
| 2 | **C-3 跨类目库重复**:写入前只读查别的注册类目是否已持有(URL 或 `content_hash`);`demo` 无拦截权但注册类目能卡住它;`doctor.db.cross_overlap` 对账 | `07_tmp/c3_*`、规则 26/27 | 已推送 |
| 3 | **C-1 正文完整性标签**:`completeness` 三档 `full/short/excerpt`,判定只在一处(`cleaner.completeness_of`,SQL 侧 `store.USABLE_LEN_SQL`),属采集侧语义;历史 328 行谎报 `full` 全部回填 | `07_tmp/c1_20261115/census_c1.json`、`07_tmp/c1_relabel_*`、规则 28/29、报告 `08_wiki/reports/P14-fix-C1-completeness-label-20261115.md` | `6f20a58` + `9dc64d7` |
| 4 | **P15 采集栈核验 + C-4 采集→清洗一条链**:`doctor crawler.stack.*` 常驻核验 Scrapling/浏览器实参与可执行文件;`runner._execute_job` 采完当场 `pipeline.run_pipeline(category)`;`doctor.db.cleaned_coverage` 覆盖率体检。真实采集跑通(health/technology/finance 落库) | 报告 `08_wiki/reports/P15-crawler-verify-C4-clean-coverage-20261115.md`、规则 30/31/32 | `1de94a3`(已推送) |
| 5 | **C-7 历史重复行整改**(本窗口最后完成):新增 `ttxt/crawler/dedupe.py`、`doctor` 三项体检、`dedupe-history` 子命令;生产删 16 行(同类目 3 + demo 跨库副本 13),`demo` 那 30 行列表页塌陷行**判为可疑、一行不删**;顺带修好长期滞后的 `articles_fts` | `07_tmp/p16_dedupe_20261115/census.json`、`07_tmp/p17_dedupe_apply_20261115/{state_before,plan_dryrun,ledger_apply,state_after,rollback_rehearsal}.json`、报告 `08_wiki/reports/P16-fix-C7-history-dedupe-20261115.md`、规则 33/34 | `d817805`(**已推送**,远端 `refs/heads/main` 现为 `3bddf38`,取证见 §八) |

## 二、生产数据现在的状态(2026-11-15 11:35 实测)

- 文章行合计 **312**(整改前 328):demo 32 / finance 96 / health 25 / history 0 / social 0 / technology 159。
- 每个 URL 在每个命名空间内**只有一行**;命名空间之间**没有同一篇文章**(`db.cross_overlap` PASS)。
- `articles_fts` 与行数一致(32/96/25/159),整改前 finance 47/97、technology 62/161 是滞后。
- 清洗覆盖率 312/312;`completeness` 标签与真实正文长度一致。
- 仍保留的真实问题:`demo` 里 `https://quotes.toscrape.com/` 30 行(列表页塌陷,不许删);`evidence_links` 9 条没有出处(finance 4、health 5,`article_id`/`quote`/`source_url` 全空);`credibility` 全 `unverified`;history/social 0 行。

## 三、测试与体检基线

- `python -m pytest 10_tests -q` → **298 passed**,56.59s,exit 0。
- 仓库根 `python -m pytest -q` → **298 passed**,66.30s,exit 0。
- `python -m ttxt doctor` → **28 ok, 0 failed**(仅 WARN:`namespaces.disk`(demo 未注册)、`db.identity_suspect`(quotes 30 行)、`db.orphan_references`(9 条证据无出处))。
- 隔离夹具:测试不得打开生产库、不得在 `07_tmp` 之外建库、生产 gate.lock 必须不存在 —— 全绿。

## 四、事故如实记录(1–6 已全部修掉并写进规则;7 为外部网络事故,本机不改配置)

1. C-4 测试里 `monkeypatch.undo()` 撤销了路径隔离,改写生产 `demo/articles.db`(证明无害);规则 32 禁止。
2. 一次全量测试打开过生产 demo 库(补 `session_categories_dir` 夹具修掉);规则 25/32。
3. 打桩返回 `None` 在仓库根造出名为 `None` 的库文件(产物已移入 `07_tmp/p15_crawler_verify_20261115/`,产品侧现在抛 `TypeError`)。
4. `backup.py` 的轮转藏在 `snapshot()` 里,曾静默删掉两个历史备份;规则 29:`disk.backup_keep` 提到 30,自动删除必须逐条打日志。
5. C-7 首版 `read_rows` 用 `sqlite3.Row`,而 `store.read_only_connect` 返回元组 → 11 红;`apply_cross` 首版 CAS 空转 → 重写按 `content_hash` 逐行比对。
6. C-7 删行后 P7 面板一条测试红:断言押的是生产正文内容("demo 里搜得到『城市』"),已改为播种一行(规则 34)。
7. **外部网络事故(非代码缺陷,未改本机任何配置)**:12:47–12:59 实测本机到 GitHub 的出口断死——直连 443 的 TLS 握手被切断,走系统代理(`365vpn`,127.0.0.1:57777)对 GitHub 的 CONNECT 返回 502;用户按其客户端侧处理后 13:00:30 同一命令链即恢复(`d817805`/`3bddf38` 已入库;恢复动作不在本机取证,故归因为节点/出口侧,属推断)。读数与判定见 §八。

## 五、待办(按优先级,接手就从 **3** 开始;1、2 已完成)

1. **推送 `d817805`** —— **已完成**(2026-11-15 13:00:30 +08:00):`scripts\push.cmd` 推成功,远端 `refs/heads/main` 实测 = `3bddf387ed7bcf67935f6aa66fe7fede9f0ab114`,`git rev-list --left-right --count origin/main...main` = `0 0`,工作区干净。中断期的读数与恢复取证见 §八(当时设想的 `git push origin HEAD` 不必再执行)。
2. **C-7b 修抽取器身份塌陷**:`ttxt/crawler/extract.py:31-58` 的 `extract_html_items` 把 `url=source.url` 赋给每一条 → 列表页 30 行同一个 URL。要改成取每条自己的链接;入库时"同 URL 不同内容"不得静默,要计数 + 记日志;配一条 `doctor.db.url_identity` 体检。做完才能谈 `demo` 里那 30 行。 —— **已完成**(2026-11-15 14:20 +08:00,取证见 `P18-fix-C7b-url-identity-20261115.md`):逐条链接按证据取(配 `link_selector` → 标题元素自身/内部 `<a>` → 锚文本与标题可信相等),取不到就打标 `url_is_page_fallback` 回退,**不猜地址**(实测兜底猜会把 `/tag/...`、`/author/...` 变成假身份);入库计数 `url_identity_conflict` / `url_no_item_link` + 逐条日志;`doctor.db.url_identity` 三级(责任来源仍启用 = FAIL)。那 30 行的处置结论在 P18 §六:**不删不改写**,且实测该站(含 `/js/` 浏览器渲染)根本没有逐条链接,身份修不好,只能长期 WARN。测试 314 passed、doctor 29 ok / 0 failed(见 §九)。
3. **恢复真实采集并核验采集栈**:按 Scrapling / CloakBrowser 官方文档核验本地部署(Scrapling 0.5.1 + curl_cffi + playwright,HTTP 优先;CloakBrowser 不装新东西)。已知残留风险:Scrapling 发伪装 UA + Google referer,而 robots 判定用项目 UA(`07_rules` 规则 19 相关,需要人拍板口径);浏览器 profile 547MB vs 配额 `disk.browser_max_gb 3`。
4. **Worker / Scheduler 未启动**:定时采集与模型串行队列还没跑起来(`00_control/scheduler_state.json`)。
5. **history / social 两个类目 0 行**:缺已批准来源,要人给来源清单才能补采。
6. **C-5 可信度分档**:现在全 `unverified`,要人批准分档口径。
7. **C-6 评审元数据 DDL** + **9 条 `evidence_links` 无出处**的修法:涉及改表结构,必须先获批准。
8. 清理项(需批准才动):`D:\ttxt\pytest-of-yangl` 残留目录 —— 本窗口没有删除任何旧文件/日志/备份/WAL/SHM。
9. **新发现的标题抽取缺陷**(C-7b 过程中实测出来,未修):标题元素是 `<h2 class="title"><a>标题</a></h2>` 时 `extract._element_text` 取到空串(Scrapling `element.text` 只取直接文本,取证 `07_tmp/p18_c7b_probe/probe_title.py`),于是标题退化成整块文本(标签、作者混进标题),`content_hash` 与 FTS 内容跟着漂。修它=改标题口径=改 `content_hash` 语义,必须先决定 `PROCESSOR_VERSION` 是否 bump(本轮故意没 bump)。详见 `P18-fix-C7b-url-identity-20261115.md` §七。

## 六、目标与恢复方法

- 目标 id `goal-93ec1650-5794-4b10-8892-bc007a204c75`,当前 **paused**(用户要求暂停),`roundsStarted=1`,`maxGoalRounds=256`。
- 继续时说"继续",由我用 `update_goal(action=resume)` 重新装载,然后从上面待办 1 开始。

## 七、备份与回滚清单(只描述,不执行)

| 备份 | 内容 | 用法 |
| --- | --- | --- |
| `11_backups/C7-pre-dedupe-20261115-112912.zip` | C-7 整改前全部类目库 + system.db + 配置(189 条目,2,616,153 字节,sha256 `4912adf9…c109`) | 整库回滚 |
| `07_tmp/p17_dedupe_apply_20261115/ledger_apply.json` | 被删 16 行整行原文 + 被搬移/丢弃引用 | `python -m ttxt dedupe-history --rollback <此文件>` |
| `11_backups/P15-pre-crawl-*-101602.zip` / `P15-pre-clean-*-102005.zip` / `P15-postC4-pre-crawl-*-103253.zip` / `C1-pre-relabel-*-093705.zip` | 各阶段改动前快照 | 对应阶段回滚 |
| 派生索引不进账本 | `articles_fts`、`article_clusters` | `python -m ttxt clean --category <类目>` + `python -m ttxt analyze --category <类目>` 重建 |

备份一律用 `Connection.backup()`(纯复制 `.db` 会丢 WAL 内容);轮转保留数 `disk.backup_keep=30`。

---

## 八、待办 1 完成取证 + GitHub 出口中断的实测读数(2026-11-15 12:47–13:02,追加不改写)

### 8.1 推送完成(远端实测,不是本地跟踪 ref)

| 时刻(+08:00) | 观测 | 来源 |
| --- | --- | --- |
| 13:00:30 | `.git/logs/refs/remotes/origin/main` 记录 `1de94a3a…2edd178 → 3bddf387…9f0ab114 … update by push` | reflog |
| 13:02:25 | `tools\git\cmd\git.exe -c http.sslBackend=openssl ls-remote --heads origin refs/heads/main` → `3bddf387ed7bcf67935f6aa66fe7fede9f0ab114` | 远端读数 |
| 13:02 | `git fetch` 后 `rev-list --left-right --count origin/main...main` → `0  0`;`status --porcelain` 空 | 本会话 |

- 命令:`scripts\push.cmd` 不带 `-m` ⇒ 只推已有提交,**当时没有新建提交**;凭据来源 `D:\ttxt\.secrets\github_token`;git = `tools\git\cmd\git.exe`。
- 入库的就是滞留的两个提交:`d817805`(12 文件,+1584/−4)、`3bddf38`(本页)。
- 恢复后的 `ls-remote` 与 `push` **都没有**显式指定 `http.proxy`,git 自行走系统代理 ⇒ 本机未改任何网络配置,恢复发生在代理/节点侧(**推断**,用户未说明客户端里改了什么)。
- 本页自身的收尾会再构成一个提交,推送后远端 HEAD 再前进一次(不影响以上读数对应的 `3bddf38`)。

### 8.2 中断期三条路径的读数(12:47–12:59,全部只读探测)

| 路径 | 结果 | 读数 |
| --- | --- | --- |
| 直连 `github.com:443` | TCP 通、TLS 握手被切断 | 对端 `20.247.166.64:443`;Python 侧 `SSLEOFError: UNEXPECTED_EOF_WHILE_READING`;git 侧 `TLS connect error: error:0A000126:SSL routines::unexpected eof while reading` |
| 系统代理 `127.0.0.1:57777`(进程 `365vpn`,PID 16152) | GitHub 的 CONNECT 全 502 | `api.github.com`、`codeload.github.com` → `Tunnel connection failed: 502 Bad Gateway`;对照组 `https://www.bing.com/` → **200 OK**(代理本身活着) |
| 备用 `ssh.github.com:443` | TCP 通、SSH banner 为空 | 对端 `20.205.243.160:443`,`recv()` 返回 0 字节(对端关闭) |

- 代理来源:注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings` → `ProxyEnable=1`、`ProxyServer=127.0.0.1:57777`;`365vpn` 只监听这一个端口,本机没有第二个代理进程可换。
- 判定(**推断,非实证**):断点在代理节点的 GitHub 出口侧,本机 git、令牌、`http.sslBackend=openssl` 都不是原因。上一次成功推送是 10:59:37(`→ 1de94a3`),说明中断落在其后、13:00:30 之前。
- 本轮**没有**做:改 hosts、改注册表代理、安装新代理、新建自动重试定时任务(用户选择手动切节点后再推)、以及 8.1 之外的任何写操作。

## 九、待办 2(C-7b)完成取证(2026-11-15 13:50–14:20,追加不改写)

完整报告在 `P18-fix-C7b-url-identity-20261115.md`;这里只登记交接需要的读数。

| 项 | 修复前 | 修复后(实测) |
| --- | --- | --- |
| `pytest -q` | 298 passed | **314 passed**(`10_tests/test_c7b_url_identity.py` 新增 16 条) |
| `ttxt doctor` | 28 ok / 0 failed,3 WARN | **29 ok / 0 failed**,4 WARN:新增 `[WARN] db.url_identity: 1 组共 30 行把来源页/订阅地址当文章地址,责任来源今天都已停用(历史残留)` |
| 列表页抽取 | 每条 `url=source.url` | 按证据取逐条链接;取不到 → 打标 `url_is_page_fallback` 回退当前页,**不猜** |
| 入库账 | 塌陷混进 `duplicates` | `url_identity_conflict` / `url_no_item_link` 计数 + `insert.url_identity_conflict` / `insert.page_url_collapse` 日志 |
| 真实列表页 | — | books.toscrape **20/20** 取到逐条链接;quotes.toscrape **0/40**(含 `/js/` 浏览器渲染,`a[href^='/quote/']` 命中 0) |

- 关键取舍(**实测推翻了直觉方案**):"兜底取块内第一个链接"会把 `/author/...` 当地址;退一步"只信块内唯一链接 + 锚文本互相包含"实测把 30 条里 **14 条**写成 `/tag/change/page/1/` 假地址。最终版宁可回退计数也不编地址 —— 假身份比塌陷更隐蔽(塌陷会被体检抓到,`/tag/...` 永远不会报警)。已固化为规则 35。
- 过程读数:改造后一度 **18 failed / 292 passed**(c2 预期 9 处 + c3 1 处 + p12 hardening 6 + p12 worker_lease 2),原因是老预期押的是"静默记账",以及三处 `FakeElement` 替身没实现 `css()`(实测 Scrapling 真实元素有 `tag/text/attrib/css`,老抽取器从不向块元素要链接所以没暴露)。逐条补齐后全绿,**没有**放宽任何产品断言。
- 新发现缺陷(未修,待办 9):标题元素是 `<h2 class="title"><a>标题</a></h2>` 时 `_element_text` 取到空串(Scrapling `.text` 只取直接文本),标题退化为整块文本,牵连 `content_hash` 与 FTS。修它必须同时决定 `PROCESSOR_VERSION` 是否 bump —— 本轮**故意没有** bump(仍 `p2-v1`)。
- 本轮写操作范围:只有代码、测试、Wiki/规则文档;**没有**写生产库、没有改 `02_crawler/sources.json`、没有删任何行(`articles` 仍 312 行)。
- 遗留风险:GitHub 出口依赖单个 VPN 客户端的单节点,断供时提交只能滞留在本地(本次两个提交滞留约 2 小时)。若要自动重试,需先获用户对定时任务的明确批准。

## 十、本轮(2026-11-15 14:46,新窗口)开工整理:仓库实际状态读数 + 本轮待办

### 10.1 接手时实测(不看上一轮记忆,只看仓库)

| 项 | 读数 | 取证方式 |
| --- | --- | --- |
| 远端 ``refs/heads/main`` | **`3d4ea158b56cbc89695b52604c17c15034757810`**(`fix(C-7b): 抽取器不得编造文章地址…`) | ``git ls-remote --heads origin`` |
| 本地 ``main`` | 同一个提交,**工作区干净**,``ahead 0`` | ``git status --porcelain`` 空 |
| 全量测试 | **314 passed**,60.25s,exit 0 | ``python -m pytest 10_tests -q`` |
| 规则文件 | 已编到 **规则 35**(C-7b:假身份比塌陷更隐蔽),本轮追加 36/37 | ``07_rules/CORE_RULES.md`` |
| 已安装采集栈 | scrapling 0.5.1 / curl-cffi 0.17.0 / playwright 1.65.1 / lxml 6.1.3 / cssselect 1.6.0 / w3lib 2.6.0 | ``importlib.metadata`` |
| 已注册来源 | 8 个(7 启用):头条科技频道(browser)、sspai/ruanyifeng/ifanr(eastmoney/tmtpost 财经、who 健康)+ 停用 demo 站 | ``02_crawler/sources.json`` |

> 结论:上一轮记忆里的"待推送 d817805""待做 C-7b"**都已过期** —— 另一窗口已推送并完成 C-7b(见 §八、§九)。本轮从 §五 待办 **3** 开始。

### 10.2 本轮(第 2 轮)要做的事与"怎么算完成"

| 序号 | 事项 | 怎么算完成(验收口径) |
| --- | --- | --- |
| R2-1 | **按官方文档核验采集栈**:Scrapling 官方文档 vs 本地 0.5.1 实参/用法;CloakBrowser 是否需要在本地部署(磁盘与必要性) | 逐条写出"官方口径 / 本地现状 / 差异 / 决策";决策与差异写进规则或 Wiki;不装无用大件 |
| R2-2 | **关键词驱动采集**:采集必须能按类目关键词发起(关键词来自 ``03_intelligence/keywords``),不是只盯固定订阅源 | 新增可通过 robots 门禁的关键词检索源;``ttxt`` 一条命令按关键词发起采集;入库仍走 URL/哈希去重;测试先失败后通过;真实数据入库前后对账 |
| R2-3 | **补齐 history / social 两个 0 行类目**:候选源逐个过 robots + 新鲜度 + feed 可用性探测,合格才注册 | 探测证据 JSON 落 ``07_tmp``;合格源注册后真实采集到**非 0 行**且体检 ``db.*`` 无 FAIL |
| R2-4 | **头条公开互动数据(阅读/评论/点赞/收藏)入库**(下一轮 R3,本轮只出探测与方案) | 本轮只出:页面是否公开可见的实测读数 + 字段映射 + 白名单口径;不动生产库 |
| R2-5 | 收尾 | 全量测试 exit 0、``ttxt doctor`` 无 FAIL、报告 P19、交接页追加完成取证、提交并推送 |

### 10.3 本轮明确不做(防止范围失控)

- 不改表结构(DDL)、不动 C-5 可信度分档口径、不修规则 9 的标题抽取(那要决定是否 bump ``PROCESSOR_VERSION``,单独一轮)。
- 不安装 CloakBrowser 之外的新浏览器/新模型;不启动 Strata 相关操作(只读)。
- 不删除任何旧文件、日志、备份、WAL/SHM、pytest 残留。

## 十一、第 3 轮(2026-11-15 16:0x–16:2x)完成账本:P19 采集 + P20 三处根因

完整取证与根因见 `08_wiki/reports/P19-keyword-driven-crawl-20261115.md`;规则新增 **38/39/40**。

| 项 | 读数(本轮实测) | 取证 |
| --- | --- | --- |
| 远端 / 本地 HEAD(开工) | `3d4ea15`(C-7b),工作区含 P19 未提交改动 | `git log --oneline -6`、`git status --porcelain` → `07_tmp/p19_keyword_crawl/git_status_p20.txt` |
| 全量测试 | **339 passed / 55.60 s / exit 0**(接手时 314) | `python -m pytest 10_tests -q` |
| 生产行数 | 312 → **354**(social 0→22、history 0→20) | `07_tmp/p19_keyword_crawl/verify_p20_20261115T1615.json` |
| 语料质量 | social full 20/short 2,min 136,avg 1127.9;history full 19/short 1,min 402,avg 891.6 | 同上 |
| 采集任务 | job 64/65(第一轮各 20 行)、66(暴露 P20 三处根因)、**67/68 各 `bodies_upgraded: 8`**、69(闸门:2 页 / 4.31 s / `feed_total 30`) | `crawl_jobs` + `crawl-queue` 回执 |
| 体检 | **29 ok / 0 failed**;WARN 3 条全是 `demo` 历史残留 + 9 条空 `evidence_links` | `07_tmp/p19_keyword_crawl/p20_doctor.txt` |
| 索引与知识资产 | `analyze` 后 social indexed 22 / history indexed 20;`viral.tiers.missing_data` 22/20 | `03_intelligence/keyword_feedback/{social,history}-feedback-20261115T1615*.json` |

第 4 轮待办(优先级从上到下):

1. **R2-4 互动指标入库**:头条公开阅读/点赞/收藏/评论,建 `article_metrics` 带 `provenance`(缺失≠0,未授权来源拒绝);没有它 `viral.tiers` 全是 `missing_data`,爆款与垃圾永远分不出,关键词反馈也没有信号。
2. **R2-5 短正文回填工具**(`ttxt crawl-enrich --category`):已被 feed 挤出去的短行(现 social 2 / history 1 / health / finance / technology / demo 里若干)不能只靠"下一轮再被列出来"补救。
3. Worker / Scheduler 上线(队列里仍有别的窗口留的 job 54 `toutiao_tech`、job 63 `who_cn_news`)。
4. C-5 可信度分档(全 `unverified`)与 9 条空 `evidence_links`(要 DDL 口径,需批准)。
5. `_element_text` 标题缺陷修复 + `PROCESSOR_VERSION` bump(单独一轮);`D:\ttxt\pytest-of-yangl` 残留清理(需批准)。
6. 恢复规则 24 的"实现前先失败":本批 P20 断言与实现同批写,已在 P19 报告 §五 如实记录偏差与替代证据。
