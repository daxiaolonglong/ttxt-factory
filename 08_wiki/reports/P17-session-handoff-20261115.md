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

## 十二、第 4 轮(2026-11-15 16:3x–17:0x)完成账本:P21 头条公开互动指标入库

完整根因、探测读数、改动、前后对账与回滚见 `08_wiki/reports/P21-public-engagement-metrics-20261115.md`;规则新增 **41**。

| 项 | 读数(本轮实测) | 取证 |
| --- | --- | --- |
| 开工时仓库 | HEAD `405cc1c`,工作区干净;远端 `main` = `405cc1ca…`(已推送) | `git log --oneline -3`、`git status --porcelain` |
| 开工时指标底座 | 六类目 `article_metrics` / `metric_snapshots` **全 0**,`viral_labels` 全 `missing_data` / `unscored` | `07_tmp/r24_metrics_probe/inventory.json` |
| 公开可见性实测 | robots 允许 `/article/`;纯 HTTP 三页同一 72,914 字符 JSVMP 空壳 ⇒ 必须浏览器渲染;渲染后 200 / 2.6–3.8 s | `probe1.json` / `probe2_render.json` / `probe3_selectors.json` |
| 归属口径 | 本文章只有工具条(点赞 437 / 评论 47)与评论区标题;卡片"16万阅读""1628评论""点赞7"**都不是本文章** ⇒ 代码写死并测试锁住 | `probe3_selectors.json` + `test_p21_public_metrics.py` |
| 实现前先失败 | **exit 2 `ImportError: cannot import name 'metrics_pages'`** | `07_tmp/r24_metrics_probe/p21_red.txt` |
| 测试 | 单文件 **28 passed / 1.86 s**;**全量 367 passed / 67.62 s / exit 0**(接手 339,+28) | `p21_cli.txt`、`p21_full_suite.txt` |
| 真实入库(写前备份 4,874,240 字节) | `--fetch-public --apply --limit 12`:written **12** / snapshots **12** / `filled_from_null 24` / 0 错误;technology 159 行中 112 行是头条可采 | `p21_apply_tech.json`、`backup_pre_p21/` |
| 入库后状态 | article_metrics **12**(credibility 全 `public_page`)、with_likes 12、**with_reads 0 / with_impressions 0**(老实 NULL)、tiers `{no_impressions 12, missing_data 147}` | `state_before.json` / `state_after_tech.json` |
| 时序能力 | 重跑 limit 2 → **written 0 / updated 2 / snapshots 2**;累计 metrics 12 / snapshots **14** / 2 篇各 2 次 | `p21_apply_tech_rerun.json` |
| 体检与闭环 | doctor **29 ok / 0 failed**(WARN 无新增);`analyze technology` exit 0,`gap_keywords ["效率工具"]`、`queued_job_id 54` 不重复入队 | `doctor_after_p21.txt`、`analyze_after_p21.txt` |
| 网络量 | 19 次文章页 + 2 次 robots,间隔 2.0 s,全 200、0 错误 | 上述回执时间戳 |

第 5 轮待办(优先级从上到下,验收口径写在报告里):

1. **扩样到可标定**:`ttxt metrics --category technology --fetch-public --apply --limit 30`(112 行可采)⇒ `metric_snapshots` 时序继续长;
   并等用户给头条**后台导出**(展现/收藏)⇒ `--import --source toutiao_admin_export` 补空、`--calibrate` 才能真标定(门槛 30 样本)。
2. **R2-4b 卡片阅读数**:从推荐卡片"16万阅读"按卡片自己的 `href` 取数,记 `source_id=toutiao_public_card`(近似、万取整、可信度 2 档)+ `source_page` 出处;
   同时给"只有互动数没有展现"的文章加**互动率评分路径**(点赞+评论 ÷ 阅读,重新归一)与新分档;未标定前 `label` 仍保持 `unscored`。
3. **R2-5 短正文回填工具**(`ttxt crawl-enrich --category`):已被 feed 挤出去的短行不能只等下一轮。
4. Worker / Scheduler 上线(队列仍是别的窗口留的 job 54 `toutiao_tech`、job 63 `who_cn_news`)。
5. C-5 可信度分档(语料本身仍全 `unverified`)与 9 条空 `evidence_links`(需 DDL 批准)。
6. `_element_text` 标题缺陷 + `PROCESSOR_VERSION` bump(单独一轮);`D:\ttxt\pytest-of-yangl` 残留清理(需批准)。

## 十三、第 5 轮(2026-11-15 17:1x–18:1x)完成账本:P22 内嵌 RENDER_DATA 计数 + 阈值第一次真标定

完整根因、取证、改动、前后对账与回滚见 `08_wiki/reports/P22-render-data-metrics-20261115.md`;
规则 **41 更正**(P21 判错的"展现量公开页根本没有")+ 新增 **42**。

| 项 | 读数(本轮实测) | 取证 |
| --- | --- | --- |
| 开工时仓库 | HEAD `757e2e6`(P21),工作区干净;远端 `main` = `757e2e6…` | `git log --oneline -3`、`git status --porcelain` |
| **推翻上一轮结论** | P21 说"展现量公开页根本没有"⇒ **错**:内嵌 `<script id="RENDER_DATA">` 是 percent-encoded JSON,`data.itemId == 本页 URL 文章 id`,`itemCounter = {readCount 8937, showCount 111790, diggCount 437, commentCount 47, shareCount 1801, repinCount 1927, forwardCount 15, buryCount 0, danmuCount 0}`;437/47 与 P21 核验的工具条逐位相同 ⇒ 归属可证明 | `07_tmp/r24_metrics_probe/probe5_offline_keys.json`、`probe6_render_data.json` |
| 实现前先失败 | **21 failed / 4 passed**(`unexpected keyword argument 'url'`、`article_id_from_url` 不存在、tiers 全 `incomplete_data`) | `07_tmp/r25_render_counters/p22_red.txt` |
| 候选轮转先红 | 改前第二批仍会选中同一批:`picked [3,2]` / `[4,3]` ⇒ **2 failed**;改为「未测过优先 + `captured_at` 旧→新」后绿 | 同上文件 `-k "picked_before or refreshed_after"` |
| CLI 标定先红 | `--calibrate` 标定成功时**不**重算标签,且不先重算导致样本读成 0 ⇒ `'insufficient_sample' == 'calibrated'` **1 failed**;改成「重算→标定→按新阈值再重算」后绿 | 同上文件 `-k calibrate_command` |
| 测试 | 单文件 **28 passed / 1.41 s**;**全量 395 passed / 77.17 s / exit 0**(接手 367 → 392 → **395**) | `p22_green2.txt`、`p22_full.txt`、`p22_full2.txt`、`p22_full3.txt` |
| 写库前备份(`Connection.backup()`,不是复制文件) | `11_backups/P22-pre-public-metrics-20261115-173836.zip` **3,483,004 B**;批次 2 前 `P22-pre-batch2-metrics-20261115-175009.zip` **3,483,259 B** | `pre_state_technology_console.txt`、`pre_state_batch2_console.txt` |
| 真实采集批次 1(20 页,dry-run 后 apply) | fetched 20 / 0 错 / robots 拒 0;**`render_ok 20` / `render_mismatch 0` / `render_unattributed 0` / `render_errors 0` / `sanity_rejected 0`**;written 8 / updated 12 / snapshots 20 / `filled_from_null 56` / `null_kept 0` | `live_dryrun_technology.txt`、`live_apply_technology.txt`、`inspect_dryrun_report.py` |
| 数值可信度交叉核对 | 文章 292 probe 早间 8937/111790 → 本轮 **8972/112181**(只涨不减),点赞 437 / 评论 47 不变;CTR 0.06%–8.94%(292 为 8.00%) | 同上 |
| 真实采集批次 2(12 页) | picked ids `257,255,254,…,244` **全是没测过的**(证明轮转生效);`render_ok 12` / written 12 / `filled_from_null 48` | `live_dryrun_batch2.txt`、`live_apply_batch2.txt` |
| 阈值标定 | 第一次 **`insufficient_sample 20/30`** 老实拒;攒到 32 后 **`calibrated`**,阈值 **`[0.0567, 0.0247, 0.0113]`**(p90/p60/p25),已进 `articles_meta.viral_calibration` | `calibrate_receipt.txt`、`live_calibrate_batch2.txt` |
| 入库后状态 | `article_metrics` **32 行**(reads/likes/comments/impressions 各 32 有值,**favorites 仍 0 有值**);snapshots **46**;tiers `{measured 32, missing_data 127}`;labels `{unscored 127, normal 11, potential 9, low_quality 9, verified_viral 3}` | `post_state_technology_after_batch1.json`、`post_state_technology_after_batch2.json` |
| 打分留痕 | `reason = "真实曝光与互动数据 + 校准阈值;收藏指标该来源不可得,按可观测率归一化(未代入 0)"`;`features_json.metric_fields = ["reads","likes","comments","impressions"]` | 同上 |

第 6 轮待办(优先级从上到下):

1. **其余类目复制同一流程**:`--fetch-public` 先看 dry-run 的 `render_ok/render_mismatch`,再 `--apply`;
   各类目 measured 样本推到 ≥30 才能 `--calibrate`(现在只有 technology 标定过)。
2. **头条创作后台导出交叉验证**:`ttxt metrics --category technology --import FILE --source toutiao_admin_export`
   ⇒ 补 `favorites`、并用 `verified` 核对 `showCount` 口径(对不上就说明展现量语义有偏)。
3. **R2-5 `ttxt crawl-enrich --category`**:短正文回填。
4. Worker / Scheduler 上线(队列仍是 job 54 `toutiao_tech`、job 63 `who_cn_news`,别的窗口的活儿)。
5. C-5 可信度分档;9 条空 `evidence_links`(需 DDL 批准);`_element_text` 标题缺陷 + `PROCESSOR_VERSION` bump;
   `D:\ttxt\pytest-of-yangl` 残留清理(需批准)。

## 十四、第 6 轮(2026-11-15 22:4x → 2026-11-16 03:4x)完成账本:P23 身份判定收口 + 被拒插入可见 + 磁盘口径闭合

起因:用户自己写的日检 `08_wiki/reports/quality-20261115.md`(只读,不改)指出三件事:
①注册类目 3 次「身份塌陷」告警来源不明;②体检没有统计**被拒插入**,存在检测盲区;
③预算表报 1.301 GB 而根目录实测 6.60 GB,差额全是没登记的目录。

| 事项 | 实测读数 | 凭据 |
|---|---|---|
| 根因 | job 66(15:53)那 3 条 `url_identity_conflict` 就是库里 social id **13/16/20**:标题逐字相同、`full`、正文 **708/846/684 字**;21:23 只读复现同一三页 `div.left_zw` 又取到 708/846/684 字(第二个选择器 `div.content_maincontent_content` 每篇多 14 字)⇒ **content_hash 跨轮抖动**;job 66 `items_enriched=20 / item_page_errors=0` 证明抓的确实是文章页正文 | `07_tmp/r26_identity_conflict/probe_conflict_out.txt`、`probe_item_pages_out.txt`、`probe_job_meta_out.txt` |
| 代码 | `store.insert_items` 新增分支:同一 URL + **同一标题** + 正文不更长 ⇒ **保留原行**,计 `bodies_kept_existing` + `LOG.info("insert.body_kept_existing")`;`url_identity_conflict` 只留给标题不同(或本批内冲突);`config.SCRATCH_DIR/ASSET_DIR`、`housekeep.MAJOR_PATHS` 补 `07_tmp`/`资产库`/`.git`/`.secrets`,`report()` 按根目录逐子项核算并列出未登记占用;`doctor` 新增 `crawler.insert_judgements`(24h 窗口)+ `disk.root_budget` + `disk.scratch` + `disk.accounting`;`config.json` 加 `paths.scratch/assets`、`disk.scratch_max_gb=2` | `git show`(本轮提交)、`10_tests/test_p23_identity_and_visibility.py` |
| 既有测试同步 | `test_c2_url_dedupe.py` 三处期望从 `url_identity_conflict` 改成 `bodies_kept_existing`(同标题 + 等长字节不同 = 同一篇重抓);**拒绝插入、只留一行**这些核心断言不变,并新增「等长重抓不得改写正文」断言 | 同上 |
| 红→绿凭据 | 改动 stash 后跑新测试:**9 failed / 2 passed**;`stash pop` 后:**11 passed**;全量回归:**406 passed,113.60 s,exit 0**(上轮基线 395) | `p23_red.txt`、`p23_green2.txt`、`p23_full_suite.txt` |
| Worker 上线 | 写库前先备份 `11_backups/P23-pre-worker-20261116-020806.zip`(4,878,116 字节,`Connection.backup()` 一致性副本,system + 6 类目 + 配置);`ttxt worker --owner r26-p23-worker --max-jobs 2 --idle-stop-s 20` 消费 job **54**(toutiao_tech/technology:inserted 9, dup 1)与 **63**(who_cn_news/health:inserted 0, dup 25),`worker.stop reason=max_jobs done=2`;**队列已清空**(queued 2 → 0,done 60 → 63) | `pre_worker_receipt.txt`、`worker_run.txt`、`post_worker_state.json` |
| 实采复验(修抽取器方向,不删数据) | `ttxt crawl --source chinanews_society`:feed 30 条 → 抽 20、文章页 20 页 0 错、inserted **11**、duplicates 9、**0 身份塌陷**(同来源同批次昨天报 3 次),耗时 33.34 s;social 22 → 33 行,full 20 → 30,short 2 → 3;三条争议行 id 13/16/20 的 `content_hash/fetched_at(15:47:53)/processor_version` **一字未动**,同类目标题无重复组 | `recrawl_social.txt`、`post_worker_state.json`(`disputed_rows_social`、`social_dup_check.dup_titles=0`) |
| 磁盘口径闭合 | 体检从 29 项 → **33 项 / 0 失败**;`disk.root_budget: root_used=6.293GB budget=20.0GB (bucketed=6.292GB)`、`disk.scratch: 07_tmp=0.548GB cap=2GB;99_temp=0.003GB`、`disk.accounting: 未登记=0.001GB`(以前差 5.3 GB 无人可见);WARN 5 项 = `namespaces.disk`、`db.identity_suspect`、`db.url_identity`、`db.orphan_references`、`crawler.insert_judgements` | `doctor_after_p23.txt`、`doctor_after_worker.txt` |
| 规则 | 新增 **规则 43**(身份判定收口 / 被拒插入必须计数 / 磁盘口径必须闭合且只告警不自动删);`db_schema.md` §2 按实测校正:文档写 `p10-v1` 而 6 库与代码都是 `p12-v1`,并把 `schema_version`(库)与 `processor_version`(行)分清、写清正文升级改哪些列、`text_length/cleaned_*` 归清洗阶段 | `07_rules/CORE_RULES.md`、`08_wiki/db_schema.md` |
| 语料总量 | **374 行**(上轮 354,+20);technology 168、finance 96、health 25、social 33、history 20、demo 32 | `post_worker_state.json`、体检 `db.cleaned_coverage` |

诚实说明两条:①今天(11-16 02:47)那次 chinanews 复采**没有**出现哈希抖动,所以 `bodies_kept_existing` 计数为 0,
生产日志里暂时没有该事件的真实样本 —— 结论只覆盖已探明范围(规则 42(7)),修复由「复现取证 + 单测」证明,不是由「今天没发生」证明;
②体检 24h 窗口仍显示 3 条身份塌陷,因为 11-15 15:53 那三条是历史事实,窗口滑过(11-16 15:53 之后)才会归零。

第 7 轮待办(优先级从上到下):

1. **指标回流扩到其余类目**:technology 已 `measured=32` 标定过;继续 `--fetch-public` 刷剩余 127 篇,
   finance/health/social/history 各攒 ≥30 measured 样本后 `--calibrate`(现在 4 个类目全是 `missing_data`)。

   > **更正(2026-11-16,P24)**:后半句口径写错。`host_inventory_out.txt` 取证显示 finance/social/history/health/demo 的
   > **头条文章数为 0**,而候选筛选只认 `host_allowed(url)`(`ttxt/crawler/metrics_pages.py:308-334`)⇒ 那些类目的 `--fetch-public`
   > 只会返回 `candidates: 0 / host_skipped: N`。前置条件是**先注册带公开计数的来源**:头条 `news_finance` / `news_history`
   > 频道已用生产适配器两级取证通过(每频道 pages 4 / 逐篇链接 17 / 正文 1450–2985 字 / 当天发布 / robots 放行),
   > social/health 的频道 slug 不存在(404 或落地 `channel=` 空),需要先渲染导航取真 slug。
   > technology 一支照原计划做了:三批 `written 53 / snapshots 53`,`measured` 32 → 85,标定样本 85。
   > 详见 `08_wiki/reports/P24-metrics-and-prediction-loop-20261116.md` 第一、六节。
2. **`prediction_records` 仍是 0**:technology 已够 `prediction.min_samples=5`,跑基线预测 + 实测回填 + 准确率报告。
3. **R2-5 `ttxt crawl-enrich --category`**(health 25 行全是 `short`,avg 119.4 字 ⇒ 正文回填是语料质量最短的一块)。
4. **调度器**:`ttxt schedule --once` 单轮上线(队列已清空,`crawl_enqueue_4h` 已错过约 17 轮;`scheduler_state.json` 冻结在 11-12 19:57);
   长期 Worker 常驻需要用户批准(前台进程会一直占网络与浏览器 profile)。
5. C-5 可信度分档;9 条空 `evidence_links.article_id`(需 DDL 批准);`_element_text` 标题缺陷 + `PROCESSOR_VERSION` bump;
   `manual ttxt crawl` 路径不做关键词闸门(job 70 无 `keyword_filtered_out`)是否要与队列路径统一,待定。
6. 磁盘:`07_tmp` 已 0.548 GB(限额 2 GB),**清理必须先出清单交用户批准**,不许自动删(规则 29/43)。

## 十五、第 7 轮(2026-11-16 04:5x → 07:5x)完成账本:P24 指标回流扩量 + 预测闭环首次真跑 + 两处口径根因

写库前备份:`11_backups/P24-pre-metrics-20261116-050544.zip`(4,866,663 字节,`Connection.backup()` 一致性副本 + 基线状态 JSON)。

| 事项 | 实测读数 | 凭据 |
|---|---|---|
| 先取证再定口径 | 各类目主机分布:toutiao 只在 technology(121/168);finance=eastmoney 60+tmtpost 36、social/history=chinanews.com.cn、health=who.int、demo=quotes+sspai ⇒ 其余类目头条文章 **0** 行 ⇒ 上一轮"刷其余类目公开指标"是空话,已在 §十四 与 P23 §九 下加日期更正 | `07_tmp/r27_metrics_expand/host_inventory_out.txt` |
| 指标回流(technology) | dry-run 预检 19/19 带计数;三批 `--apply`:fetched 19/18/16、fetch_errors 1/2/4、**render_ok 53、mismatch/unattributed/sanity_rejected 全 0**、**written 53、snapshots 53、filled_from_null 212**、updated 0;`host_skipped` 每批 14(非头条主机不猜) | `dryrun_batch3.txt`、`apply_batch3/4/5.txt` |
| 库内变化 | technology `article_metrics` 32 → **85**(reads/likes/comments/impressions 各 85,`favorites` 仍 0)、`metric_snapshots` 46 → **99**;`measured` 32 → 85,标签 verified_viral 3 → **9**、potential 25、normal 30、low_quality 21、unscored 83 | `post_p24_state.json` |
| 标定两次 | 样本 69 → 阈值 `0.0462/0.0235/0.0108`(05:58);样本 85 → `0.0551/0.0261/0.0123`(07:15);方法仍是 p90/p60/p25,`MIN_CALIBRATION_SAMPLE=30` | `calibrate_69.txt`、`calibrate_85.txt` |
| **根因:`prediction_records` 恒 0** | 实跑 `predict` 返回 `recorded 0 / already 0 / skipped_no_baseline 0` 而 `baseline_buckets` 有 69 个 measured 样本 ⇒ 候选 SQL `WHERE v.article_id IS NULL` 只挑"没有标签行"的文章,而生产每轮 relabel 让每篇都有标签行;另外 `LIMIT` 在"是否已有预测"之前生效 ⇒ 即便有候选也原地打转 | `predict1.txt` |
| 代码修正 | `prediction.predict_articles`:候选=未实测(`tier<>'measured' OR score IS NULL OR 无标签行`);**已实测不许预测**;已有预测下推 SQL 反连接 + `ORDER BY a.id DESC` ⇒ `LIMIT` 只消耗新候选;`already_has_prediction` 语义保持 | `ttxt/analysis/prediction.py`、`10_tests/test_p24_prediction_candidates.py` |
| 红→绿 | 新测试 5 项:改前 **4 failed / 1 passed**(`assert 0 == 2`、`assert (0,0,0) == (1,1,1)`);改后与新测试同跑的 P11 旧预测断言合计 **24 passed** | `p24_red.txt`、`p24_green.txt` |
| 闭环真跑 | `predict --limit 40` → **recorded 40**;batch5 实测其中 16 篇 → `settle` → **settled 16 / pending 24**;`report` → **MAE 0.0168 / mean_bias −0.0116 / 16 样本 / asset_written true**;独立 SQL 复核 `AVG(ABS(predicted-outcome))=0.0168` 一致;知识资产 `kind=prediction_accuracy, confidence=measured` | `predict2.txt`、`settle3.txt`、`report3.txt`、`post_p24_state.json` |
| 头条频道取证(为其余类目) | 静态 6 slug:finance/history 200 且落地 `channel=finance/history`(但静态只有 4,827 字节 JS 外壳、0 链接),health 落地 `channel=` 空,economics/society/culture 404;robots 全放行。用**生产适配器**(`browser_session` + `toutiao.collect(limit=3)`)复探:finance 与 history 各 pages 4 / 逐篇链接 17 / items 3 / errors 0,正文 1450–2985 字,发布到 11-16 06:26/07:15 ⇒ 第 8 轮注册 `toutiao_finance`/`toutiao_history` 的依据 | `probe_channels_out.txt`、`probe_channels_browser_out.txt` |
| `favorites` 仍 NULL | 同 URL dry-run 里 `itemCounter` 有 shareCount 54 / repinCount 196 / forwardCount 3 / buryCount 0 / danmuCount 0,但十几分钟后探针再抓**同一 URL** 拿到无 `RENDER_DATA` 外壳(`RENDER_DATA` 0 次、`收藏` 0 次)⇒ 中文标签与数字配对取证没做成,`repinCount=收藏` 仍是猜测,继续只入 4 个已证实计数 | `dryrun_batch3.txt`、`probe_counter_labels_out.txt` |
| 测试护栏误报根因 | 全量 `411 passed, 1 error`:teardown 断言 `['wal_sidecars']` 变化 —— 会话**开始前**就有 14 个 `-wal/-shm`(我自己的 `mode=ro` 巡检脚本留下,只读连接不能 checkpoint)。`conftest` 改成:受保护文件 sha256/行数必须一致(含缺失),**新增**边车才算违规(配合 `session_db_opens` 白名单);重跑带边车 → **411 passed / 109.69 s / exit 0** | `p24_full_suite.txt`、`p24_full_suite2.txt` |
| 体检与磁盘 | doctor **33 ok / 0 failed / 5 WARN**(与上一轮同一批 WARN);`disk.root_budget root_used=6.315GB budget=20GB`、`disk.scratch 07_tmp=0.571GB cap=2GB`、`disk.accounting 未登记=0.001GB`;`db.cleaned_coverage 374/374`;队列 `done 63 / aborted 7`(本轮无新采集) | `doctor_after_p24.txt`、`post_p24_state.json` |
| 语料总量 | 仍 **374 行**(本轮只做指标与预测,没有新增采集) | `post_p24_state.json` |

诚实说明:①`fetch_errors` 1→2→4 没做逐 URL 取证,只证明"同一 URL 会被返回不同外壳",没查明是限流还是别的;②MAE 只有 16 个回填样本,基线是"同类目同标题形态桶均值",不是模型;③其余 5 个类目 `article_metrics` 仍是 0、`prediction_records` 仍是 0(没可测样本,`predict` 如实拒绝);④频道取证 limit=3,只证明"能拿到逐篇链接与正文",没测整频道产量与翻页;⑤没跑 Worker 常驻、没开 `ttxt schedule --once`、没清 `07_tmp`、没改表结构(9 条空 `article_id` 仍等 DDL 批准)。

第 8 轮待办(优先级从上到下):

1. **注册 `toutiao_finance` / `toutiao_history`**(第六节两行取证),启用前逐 URL robots 复核 + 限速 2s + `render: browser`;采集后 analyze → ≥30 measured → `--calibrate` → `predict/settle/report`,把闭环复制到 finance/history。
2. **social / health 头条频道 slug**:渲染首页导航取真实 slug(不许猜),再走"静态 → 生产适配器"两级取证。
3. **R2-5 `ttxt crawl-enrich --category`**(health 25 行全 `short`,avg 119.4 字)。
4. **调度器上线**:`ttxt schedule --once` 单轮;Worker 常驻需用户批准(`scheduler_state.json` 仍冻结在 11-12 19:57,`crawl_enqueue_4h` 已错过约 18 轮)。
5. C-5 可信度分档;9 条空 `evidence_links.article_id`(需 DDL 批准);`_element_text` 标题缺陷 + `PROCESSOR_VERSION` bump;`ttxt crawl` 手动路径不做关键词闸门是否统一。
6. **关键词反哺要读爆款标签**:`analysis/keywords.py` 目前不读 `viral_labels` ⇒ 爆款结论回不到选词(用户目标 2 的关键缺口)。
7. `07_tmp` 0.571 GB(限额 2 GB):清理必须先出清单交用户批准(规则 29/43)。

## 十六、第 8 轮(2026-11-16 12:4x → 13:3x)完成账本:P25 新频道排干队列 + FTS 索引根因 + 旧资产取证

写库前备份:`11_backups/p25-pre-20261116-125627.zip`(6,578,403 字节)。完整账本与全部证据路径见
`08_wiki/reports/P25-two-new-channels-and-fts-index-20261116.md`;本节只记结论与更正。

| 事项 | 实测读数 | 凭据 |
|---|---|---|
| 注册新频道 | `toutiao_finance` / `toutiao_history` 写入 `02_crawler/sources.json`(`render: browser`、`rate_limit_s: 2.0`、取证写进 `notes`) | `02_crawler/sources.json` |
| **改判:不各入队 3 次** | `toutiao.collect` = 1 个频道页 + ≤10 篇文章页,连开 3 任务等于同一批 17 条链接抓三遍,第 2、3 次几乎全落去重 ⇒ 各入队 1 次 | `ttxt/crawler/toutiao.py:120-139`、`07_tmp/r29_channels/enqueue_p25_new_channels.py` |
| 排干队列 | Worker `--owner p25 --max-jobs 3`:job 71/72/73 全 `done`,`worker.stop reason=max_jobs done=3`;每任务 pages 11 / links 17 / extracted 10 / inserted 9+10+10 / duplicates 1+0+0 / elapsed ≤58.7 s | `09_logs/ttxt-20261116.jsonl`、Worker 回执 |
| 语料增长 | technology 168→**177**、finance 96→**106**、history 20→**30**;全库 **374→403 行** | `07_tmp/r29_channels/post_p25_state.json` |
| 指标回流 | finance candidates 10 / fetched 10 / errors 0;history 同;technology candidates 20 / fetched 16 / **fetch_errors 4(未查明)**;`render_mismatch=0`、`render_unattributed=0` | `metrics.public.summary` 13:03:42 / 13:04:24 / 13:07:53 |
| 标定 | technology 样本 85→**101**,阈值 **0.0562/0.0272/0.0115**(p90/p60/p25,`articles_meta.viral_calibration` 13:09:10);`verified_viral` 9→**10**;finance/history 各 **10 条 < `MIN_CALIBRATION_SAMPLE=30`** ⇒ 拒绝标定(如实) | `post_p25_state.json`、`ttxt/analysis/viral.py:28` |
| 预测闭环扩类目 | finance `recorded 20`、history `recorded 20`(全部 pending);technology `settle` → **settled 3 / pending 21**;`report` → **19 样本 / MAE 0.018 / mean_bias −0.0127** | 命令回执 |
| **根因:FTS 只在 analyze 里建** | 新体检项 `db.fts_coverage` 抓到 374 行只有 354 行可搜(social 22/33、technology 159/168);`articles_fts` 只由 `rag.rebuild_index` 写,采集路径 `pipeline.run_pipeline` 不写 ⇒ 当天采的文章搜不到、写作取不到材 | `ttxt/analysis/rag.py:22-42`、`ttxt/crawler/pipeline.py` |
| 修根因 | `run_pipeline` 末尾 `rag.rebuild_index(...)`(单向依赖,函数内导入避免循环),回执新增 `indexed`;红→绿:新测试 **3 failed → 46 passed** | `10_tests/test_p25_fts_after_crawl.py`、`p25_fts_red.txt` / `p25_fts_green.txt` |
| **根因:入队无审计** | P24 说"job 70/71 疑似面板触发"只有"时间相隔 11 秒"这一条,是推断不是证据;现 `create_crawl_job` 打 `crawl.enqueued` 带 `actor=dashboard/scheduler/keyword_plan/manual`,job 72/73 实测 `actor: "manual"` | `ttxt/crawler/store.py`、`09_logs/ttxt-20261116.jsonl` |
| 全库索引与清洗 | `db.fts_coverage 403/403 PASS`、`db.cleaned_coverage 403/403 PASS`、`db.duplicate_rows PASS` | `ttxt doctor` 收账 |
| 体检与磁盘 | doctor **34 ok / 0 failed / 5 WARN**(全是 P24 已知那批);`disk.root_budget 6.43GB/20GB`、`07_tmp 0.635GB/2GB`、浏览器 profile 608MB/3.0GB | 同上 |
| **旧资产取证(只读)** | `资产库/01_语料库/factory.db`:**53,678 篇**全为 `toutiao.com/article/<gid>/` 且 URL 全唯一;`search_ssr` 计数 53,289 行,`read_count>0` **19,970** 行(0 值 33,708 行**必须当缺失**);正文 ≥400 字且有阅读 **33,412 行**、有阅读且有赞 **17,313 行**;发布 2026 年 51,651 篇(最新 2026-11-06);正文均值 2,208 字、**8,000 字是旧采集截断**;`keywords` **28,171** 条(ACTIVE 16,796);旧模型 AUC **0.8803**(2026-11-04,deployed) | `07_tmp/r29_legacy_asset_probe/*` |
| 规模石头(实测+外推) | `rebuild_index` 实测 177 行/317,017 字/0.60 s = 295 行/s;按 53,678 行外推 **3.0–3.7 分钟/任务** ⇒ `run_pipeline` 全表重算必须先增量化 | `07_tmp/r29_channels/fts_cost_out.txt` |
| 测试 | 本轮新增 **11 项**(观测 4 + 频道 4 + FTS 3);全量 **422 passed / exit 0 / 138.26 s** | `p25_full_suite.txt` |
| **口径更正** | 上一份交接清单写"P25 新增 9 项、应 420",实际新增 8 项 / 419 passed —— 是清单数错,不是测试缺失(`--collect-only` 419) | `--collect-only` 输出 |
| 工具踩坑 | ①后台 pwsh 作业**只要 stderr 有输出就回传 exit 1**(最小复现见报告第十四节),`worker`/`metrics` 的 exit 1 不代表失败;②只读旧库排序要先 `PRAGMA temp_store=MEMORY`;③`git` 不在 PATH,须前置 `D:\ttxt\tools\git\cmd` | 报告第十四节 |

诚实说明:①finance/history 仍不能标定(样本 10),闭环只复制了采集/指标/预测三段;②technology 的 4 条 404 未查明根因;
③`favorites` 仍未证实,继续 NULL;④job 71 是谁触发的**永远不可证**(审计在修好之后才生效);
⑤analyze 关键词反哺本轮自动入队 **job 75**(health),这是收账时队列非空的唯一来源,不是残留;
⑥没跑 `schedule --once`、没上常驻 Worker、没做 `crawl-enrich`、没改 DDL、`keywords.py` 仍不读 `viral_labels`;
⑦`quality_claim` 仍 **NOT_AVAILABLE**(黄金集 61 张卡片人工裁定 0/61,只有本人能做)。

第 9 轮待办(P26,优先级与每项"怎样算做到"见 P25 报告第十三节):①**旧资产迁移为唯一主线**(六步硬前置:红名单测试 → 只读映射表 → 碰撞预演 → 新鲜度隔离 → 管线增量化 → 分批导入对账);②管线增量化(5 万行单任务 < 60 s);
③`keywords.py` 读 `viral_labels`;④404 根因;⑤`crawl-enrich`(health 全 `short`);⑥`schedule --once`;
⑦C-5 分档 + 9 条空 `article_id`(需 DDL 批准)+ `_element_text` 标题缺陷;⑧`07_tmp` 526 MB 与项目外 `pytest-of-yangl` 50.8 MB
**必须先出逐项清单交批准**;⑨黄金集人工裁定(只有本人能做;裁定单已于 2026-11-16 14:00 出纸,见 §十六 补记 ⑬,现在只等打勾)。

**补记(13:31 → 13:45,规则 44:上面的账是中途读数,不改写,就地更正)**:
⑧反哺出来的 job 74/75 已排干(`worker --owner p25b --max-jobs 2`,`stop_reason=max_jobs done=2`,64.19 s):
   job 74 `toutiao_tech` 净增 **9 行**(pages 11 / links 16 / extracted 10 / dup 1),job 75 `who_cn_news` **inserted 0 / duplicates 25**;
⑨最终读数:全库 **412 行**(technology **186** / finance 106 / history 30 / social 33 / health 25 / demo 32),
   `db.fts_coverage` **412/412 PASS**、`db.cleaned_coverage` **412/412 PASS**、`ttxt doctor` **34 ok / 0 failed / 5 WARN**、
   `disk.root_budget 6.534GB/20GB`、`07_tmp 0.634GB`;全量回归 **422 passed / exit 0 / 137.57 s**;
⑩**又抓到同源第二缺陷并修掉**:`runner.py:452-458` 拷贝管线统计是**键白名单**,新键 `indexed` 被静默丢掉 ⇒ 生产 Worker 回执看不见索引计数;
   红 `KeyError: 'indexed'`(1 failed / 7 passed)→ 绿(与 FTS 测试同跑 **11 passed**),凭据 `07_tmp/r29_channels/p25_indexed_red.txt`;
⑪**新缺陷,未修,列为第 9 轮 ⓪(半天,排在旧资产迁移之前)**:关键词反哺入队是**无界循环** —— 同一 gap 词今天入队 4 次
   (`crawl.enqueued actor=keyword_feedback`:13:03:23 job 74 / 13:04:55 job 75 / 13:34:25 **job 76** / 13:34:27 **job 77**,
   关键词仍是 `效率工具` / `睡眠`),而 health 那一支永远 `inserted 0 / duplicates 25` ⇒ gap 补不上却每轮重新排队,白烧限速预算。
   验收:同一 gap 词 24h 冷却内不重复入队,红→绿测试证明"连跑两次 analyze 只入队一次",日志带第几次尝试;
⑫交班时队列 `aborted 7 / done 68 / queued 2`(job 76/77)—— 是上述循环的产物,**故意不再排干**,等冷却机制修好后再消费;
⑬14:00 追加(不占本轮账,但把卡了三轮的那件事解开了):黄金核验 61 张卡压成**一张打勾裁定单**
   `08_wiki/reports/P13-golden-adjudication-sheet.docx` / 同名 PDF(9 页 A4 横向),每行 3 个勾 → `human_verdict` / `human_fact_support` / `human_confirms_tier`;
   配套两个可重跑脚本 `scripts/golden_v2_dump_flat.py`、`scripts/golden_v2_adjudication_sheet.py`(机器结论与 harness 同源,刻意不印 `kind` 防污染)。
   **现在等的只剩本人打勾**;即便全勾完,门禁仍差 `A/B 级证据占比 28% < 50%`,那条是工程侧的活。
⑭第 9 轮 ⓪ 已做完(14:2x):关键词反哺入队加 **24 小时冷却**,状态存在**类目库 `articles_meta.dispatch_state`**
   (不是 `crawl_jobs.meta_json` —— 它会被 `finish_crawl_job` 的执行回执整体覆盖),`cooldown_hours=0` 是逃生阀;
   `crawl.enqueued` 日志新增 `attempt`。5 项红→绿 + 全量 **427 passed / exit 0 / 114.03 s**。
   取证 `07_tmp/p26_cooldown/`,说明 `window-progress-20261116.md` §十。**生产上第一次看到"冷却中"文案要等 job 76/77(修复前排的队)排干。**
⑮裁定单按本人复核要求改了一处(14:5x):**附录印完整原始文章网址**,不再只印域名;61/61 行都有网址(取自卡片 `source_url`,
   0 行缺失,没有一条手写或猜测)。**页数 9 → 11**(A4 横向,双面 6 张纸);61 行裁定内容、三个勾、行号一律没动,
   陷阱卡 `v2t13` 仍在**印出的第 13 行**(本人记成第 49 行 —— 就地纠正)。翻页核验:`tblHeader` + `cantSplit` + 渲染图像素比对(表头行差 0.0)。
   详见 `window-progress-20261116.md` §九·补。


