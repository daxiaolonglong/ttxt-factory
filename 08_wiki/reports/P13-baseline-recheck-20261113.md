# P13 基线复核实测报告(只读轮次)— 2026-11-13

## 0. 本轮边界与声明

- 本轮按评审要求**只做只读基线复核**:未启动/停止/切换 Strata,未启动真实采集任务,未执行任何写库/写配置/改代码动作。
- 本轮唯一新增文件 = 本报告。另有 4 个只读探针脚本落在被 gitignore 的 `07_tmp/`(`.gitignore:32`):
  [`07_tmp/base_probe_data.py`](../../07_tmp/base_probe_data.py)、
  [`07_tmp/base_probe_data2.py`](../../07_tmp/base_probe_data2.py)、
  [`07_tmp/base_probe_data3.py`](../../07_tmp/base_probe_data3.py)、
  [`07_tmp/base_probe_data4.py`](../../07_tmp/base_probe_data4.py)。
  全部以 `sqlite3.connect(f"file:{db}?mode=ro", uri=True)` 只读打开,未执行 INSERT/UPDATE/DELETE/DDL。
- 度量时间窗:**2026-11-13 10:52–11:05(+08:00)**。下文每条结论都标注口径。
- 证据等级标签:
  - **CONFIRMED** = 本轮在本机用命令/DB 读数/日志行直接测得(附路径、命令、读数)。
  - **SUPPORTED** = 本机有代码或配置事实支撑,但缺一次端到端实测。
  - **UNKNOWN** = 本轮未测或无法从本机证据判定。
- **外部回执(非本机实测,不得当成本轮证据)**:P12 各批 72h/7d 浸泡、161 项测试全绿的报告
  (`08_wiki/reports/P12-*.md`)、每日 18:00 巡检任务与每周一 09:30 P13 闭环任务的触发记录。

### 0.1 本轮执行的命令(全部只读)

| # | 命令 | 退出码 | 说明 |
|---|---|---|---|
| 1 | `tools\git\cmd\git.exe -C D:\ttxt rev-parse HEAD` / `status -sb` / `stash list` / `branch -a` | 0 | Git 状态 |
| 2 | `tools\git\cmd\git.exe ls-remote https://github.com/daxiaolonglong/ttxt-factory main`(带 Basic 头) | 0* | 远端 HEAD;`*` 见 §C 注 |
| 3 | `.venv\Scripts\python.exe --version` + `importlib.metadata` 版本读取 | 0 | 环境 |
| 4 | `Invoke-WebRequest http://127.0.0.1:8080/health` / `/v1/models` | 0 | **仅 GET** |
| 5 | `nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu --format=csv` | 0 | GPU 只读 |
| 6 | `Get-CimInstance Win32_Process` / `Get-Process` | 0 | 进程 |
| 7 | `07_tmp\base_probe_data.py` … `base_probe_data4.py` | 0 | 只读 DB 探针 |
| 8 | `Get-ChildItem -Recurse -Force` + 字节累加 | 0 | 磁盘口径 |
| 9 | `tools\git\cmd\git.exe status --porcelain` / `check-ignore -v` | 0 | 工作区洁净 |
| 10 | `netstat -ano \| Select-String ':8765'` | 0 | 端口属主 |

未运行:`ttxt doctor` / `ttxt crawl` / `ttxt clean` / `ttxt analyze` / `ttxt worker` / `ttxt schedule --once|--daemon`(都会写日志或写库,本轮禁止)。

---

## A. 已正常工作(CONFIRMED)

| 项 | 本机实测证据 |
|---|---|
| Git 三向一致 | 本地 HEAD = `f65326983de64849cfd4f826a8ba8f4845a19b53`;`ls-remote main` 同一 SHA;`status -sb` = `## main...origin/main` 且 ahead/behind `0 0`;`status --porcelain` 空;`stash list` 空;仅分支 `main`;共 19 次提交 |
| 类目物理隔离 | 5 个类目库 `PRAGMA integrity_check` 均 `ok`;`SELECT category, COUNT(*) FROM articles GROUP BY category` 每个库只出现自己的类目名(technology/finance/health/history/social) |
| 采集队列语义 | `00_control/system.db` `crawl_jobs` 54 行:done 46 / aborted 7 / queued 1;`attempts` 分布 0:9、1:36、2:9(≤ `max_attempts` 3);终态带 `finished_at`;owner 记名为 `p12b2-resume`/`p12b3-whitelist`/`p12b5-browser-crash` 等 |
| 模型串行队列真实生效 | `model_jobs` 70 行全部 `done`;**时间窗重叠计数 = 0**;最新 3 行(68/69/70)首尾相接:`01:00:03.372→01:01:29.961`、`01:01:29.990→01:02:45.217`、`01:02:45.243→01:04:05.064` |
| 浏览器会话复用与临时目录钉在项目内 | SUPPORTED(代码):`ttxt/crawler/fetcher.py:72-88`(`page_pool`、`user_data_dir=browser_profile_dir()`)、`:91-108`(`.browser/pw-artifacts` + 24h 清扫)、`:111-118`(把 `TMP/TEMP/TMPDIR` 全钉到项目内)、`:125-131`(profile 锁残留后重启一次) |
| robots 逐 URL 门禁 + 翻页再门禁 | SUPPORTED(代码):`ttxt/crawler/runner.py`(`store.claim_next_crawl_job(owner, lease_s, category)`、host 级 `rules_cache`、每个分页 URL 走 `robots.gate`、`pagination_stop="robots"`、跨 netloc `next_url` 拒绝);`ttxt/crawler/toutiao.py` 有 `robots_skipped` 计数,无登录/验证码绕过 |
| 写入门禁按类目落盘 | CONFIRMED(文件):`04_writing/drafts/technology/technology-20261113000422-5a990d.md`(5,702 B,passed);`drafts/finance/…cf7373.md`(passed)+ `drafts/finance/rejected/…dc9a4d.md`;`drafts/health/` 主目录 **0 个文件**,`rejected/` 2 个(7,317 B、6,420 B)——健康类被拒草稿没有混入正式草稿区 |
| 门禁判定真实存在且不看分数也能拒 | SUPPORTED(代码)+ CONFIRMED(读数):`ttxt/writing/workflow.py:320-325`(`complete && score≥min && critical 为空`)、`ttxt/writing/verifier.py:239-240`(确定性 critical「无来源断言」);实测 health 任务 `…e143da` 三版评分 **0.85/0.85/0.85 仍 final=rejected**(其 8 条 claim 全部 `no_source`、`overlap=0.0`、`source_url=NULL`) |
| 科技类证据链可用 | `evidence_links`(technology)= 3 行,全部 `credibility=verified`、带 `source_url` 与 `checks_json` 锚点(例:`numbers:["61%","15%","11%","3%","7%"]`,`quote_offset=93`,`overlap=0.412`) |
| 来源注册表为文件式且带探测说明 | `02_crawler/sources.json`(UTF-8 解析成功):8 条,7 条 enabled;`system.db.sources` = 0 行(表存在但未使用,注册表以文件为准) |
| 备份产物完整 | `11_backups` 7 个 zip,本轮重算 SHA256:`E2E-20261112-142100.zip` 443,938 B `9E718135…E1D7`;`P10-final-…142130.zip` 631,135 B `FD782F52…2ACD0`;`P11-final-…201051.zip` 856,556 B `AB510847…58A6`;`P12-pre-…203004.zip` 856,613 B `8D14593D…A92F`;`P12-batch4-20261113-011253.zip` 2,138,322 B `8F43F16B…07F8`;`P12-batch5-20261113-024308.zip` 2,116,603 B `5117218D…8404`;`P13-batch0-baseline-20261113-085637.zip` 2,116,611 B `7370585E…828C` |
| 磁盘总额度充裕 | `D:` 卷 free **108.78 GB** / total 234.69 GB,远高于 `config.json` 的 `min_free_gb: 5` |

---

## B. 代码存在但本机未部署 / 未运行(CONFIRMED 缺席)

| 组件 | 本机实况 | 直接后果(实测) |
|---|---|---|
| **调度守护 `ttxt schedule --daemon`** | **无该进程**。`Get-CimInstance Win32_Process` 全量 python 只有 4 个:PID 9136/14752(Strata 自带 `strata_control.runtime` / `serve.server`)、PID 29724/30760(`-m ttxt serve --port 8765`)。`ttxt/__main__.py:341-349` 显示守护需显式 `--daemon`;`ttxt/dashboard/api.py:267-272` 只做 `scheduler.status_report()` 读取 | `00_control/scheduler_state.json` 四个任务 `last_run_at` **全部冻结在 2026-11-12T19:57:35+08:00**;`crawl_enqueue_4h`(240 min)本应在 23:57 / 03:57 / 07:57 再跑,均未跑;`09_logs/ttxt-20261113.jsonl` 736 行中 **module="scheduler" 计数 = 0** |
| **采集 worker(`ttxt worker`)** | **无该进程**;`00_control/worker.lock` 残留 5 B(mtime 11-12 23:22:43,字节区间锁,进程退出即失效,文件残留无害);`00_control/worker_stop` 不存在 | 队列里有 1 条 `queued` 任务无人认领:`id 54 / technology / toutiao_tech / attempts 0 / owner NULL / created_at 2026-11-13T08:51:42`;`worker.lock` 存在但没有持锁进程 |
| 自动清洗 / 自动聚类 | 无任何自动入口触发 `crawler_pipeline.run_pipeline`:只有 `ttxt clean`(`ttxt/__main__.py:107-124`)会调;`scheduler._job_analyze`(`ttxt/scheduler.py:109-125`)只调 `analyze.run_analysis`;`ttxt/analysis/analyze.py:19-31` 的编排里**没有清洗与聚类步骤** | `cleaned_content`/`simhash`/`cluster_id` 大面积 NULL(见 §D1、§D2) |
| CloakBrowser | 未安装(项目内无该目录/依赖;`config.json` `browser.executable_path` = `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`) | 符合「禁止 CloakBrowser」约束,不是缺陷 |
| Agency Agents / 额外 Agent | 未安装;角色仍是 6 个(`06_agents/` 与技能 `ttxt-*`) | 符合约束 |
| 第二模型 / 并发模型调用 | 未启用(仅 `model_gate` 串行,§A 重叠=0) | 符合约束 |
| **看板双实例** | PID 29724(`.venv` Python)与 PID 30760(`C:\Users\yangl\AppData\Local\Programs\Python\Python312\python.exe`)都在跑 `ttxt serve --port 8765`;`netstat` 显示 **只有 30760 在 LISTENING** | 一个进程空占资源;且其中一个是**非 venv 解释器**,与 README 的 venv 口径不一致(CONFIRMED 事实;原因 UNKNOWN) |

---

## C. 本机存在但远端未提交:**无**

- `git status --porcelain` 空,`--porcelain --untracked-files=all` 空 → 无未提交跟踪文件、无未跟踪非忽略文件。
- 本轮新增探针脚本全部被忽略:`git check-ignore -v` 命中 `.gitignore:32: 07_tmp/`。
- **注(诚实口径)**:`ls-remote` 推送/查询类命令把进度写到 stderr,PowerShell 会把它记为 `[exit code: 1]`;本轮以 `ls-remote` 的 stdout SHA 为准判定同步(§A),不是失败。

---

## D. 真实运行缺陷(CONFIRMED,本轮新发现优先)

### D1 FTS 索引落后于语料(评审问题 2 的确证)
- 读数:`technology` 137 篇文章 / `articles_fts` **62 行**;`articles.id NOT IN (SELECT article_id FROM articles_fts)` = **79**;`article_id` 口径的陈旧行 = **0**;`MAX(article_id) in fts = 66` vs `MAX(articles.id) = 268`。
- `finance` 50 篇 / 47 行,缺 **3**(`MAX(id)=110` vs `MAX(indexed)=47`);`health` 25/25 全索引。
- 机制(CONFIRMED 代码):`ttxt/analysis/rag.py:22-42` 是**全量 DELETE + INSERT**,跑一次就必然等于文章数 → 索引停留在 62/47 只能解释为**最后成功 analyze 时语料只有 62/47 篇**,之后再无 analyze;而 §B 显示调度守护没有再跑(§A/§B 交叉验证)。采集结束路径不触发 analyze(SUPPORTED:`runner.finish_crawl_job` 无 analyze 调用)。
- **更正先前口径**:上一轮报告写「technology 缺 75 条 + 4 条陈旧行」。75 是错误数字,实测为 **79**;「4 条陈旧行」是**把 FTS5 自增 `rowid` 当作 `article_id` 比较造成的度量错误**,以 `article_id` 复核实测为 **0**。

### D2 清洗与聚类从未进入自动链路(评审问题 1/2 的上游根因)
- 读数:`cleaned_content` 非空 = technology **10/137**、finance **0/50**、health **0/25**;`clean_version` 分布 technology `{"p3-v1":10, NULL:127}`,finance/health 全 NULL;`simhash` NULL = 127/137、50/50、25/25;`cluster_id` 同上;`article_clusters` 行数 technology **10**、finance **0**、health **0**。
- 机制(CONFIRMED 代码):`ttxt/analysis/analyze.py:19-31` 只跑 migrate→keywords→viral→knowledge→rag;`ttxt/crawler/pipeline.py:23-30`(写 `cleaned_content/text_length/clean_version`)与 `ttxt/crawler/clusters.py:54-77`(写 `simhash/cluster_id`)**只由 `ttxt clean` 手工触发**。
- 连带效应(SUPPORTED,已读数印证):`keywords.py:48-49`、`rag.py:34`、`viral.py:168`、`clusters.py:47-49` 都用 `cleaned_content or content` 回退 → 未清洗行直接用带 HTML 残片的原文参与关键词/索引/爆款标签。

### D3 `completeness` 字段不可信(CONFIRMED)
- 读数:`completeness='full'` 覆盖 **technology 137/137、finance 50/50、health 25/25**,而 health `content` 长度实测 **35–142 字符**(平均 91.72)。
- 结论:该字段不能作为「正文完整可用」的判据;门禁的 `complete` 判定另有口径(`workflow.py:_is_complete`,`gate_min_chars=400`),与库内 `completeness` 不是同一件事。

### D4 来源绑定失败导致高分草稿被拒(CONFIRMED)
- `evidence_links`:finance 8 行、health 8 行的抽样全部是 `source_url=NULL / overlap=0.0 / credibility='no_source' / checks_json={"checks":{}}`;technology 3 行则 `verified`。
- 与 §A 的门禁读数一致:health 三版 0.85 仍 rejected;finance `…dc9a4d` 三版 0.30→0.40→**0.65**(已过 `gate_min_score 0.6`)仍 rejected。
- 判定:门禁方向正确(拒绝无来源断言),**缺陷在取材侧**——health 语料只有 35–142 字符、finance 语料 31/50 行含 HTML 残片,写手段言无法在原文锚定,`verifier.py:239-240` 必然产出 critical。
- 取材链实测口径(CONFIRMED 代码):取材 = `ttxt/writing/workflow.py:400-420`(`rag.search(category, topic, limit=5)` → `coalesce(cleaned_content, content)`),**喂给模型的素材每条被截到 240 字符**(`workflow.py:165` `[:240]`),而 `verify_claims`(`:170-182`)用的是整行全文 → **生成阶段可见证据 < 核验阶段要求证据**,这是 no_source/锚定失败的系统性放大器(SUPPORTED:机制确证;其对 3 条 verified 科技草稿的具体影响比例未量化)。
- 与 §D1 直接联动:health FTS 25/25 覆盖完整但语料本身是摘要;technology 只有 62/137 被索引 → `rag.search` 在科技类**最多只能检索到 45.2%(62/137)的语料**,其余 79 篇(57.7%)对写作完全不可见(CONFIRMED 读数)。

### D5 磁盘审计口径漏掉 `.browser`(CONFIRMED,与 §G 同源)
- `00_control/scheduler_state.json` 内 housekeep 自带读数:`sizes_bytes.crawler = 2,133 B`、`caps_status.browser_bytes = 0`、`project_total_gb = 0.364`。
- 本轮全量口径(含隐藏目录,`Get-ChildItem -Force -Recurse` 字节累加,10:53):`02_crawler/.browser` = **697,240,656 B**。
- 分解(同一口径):`.browser\profile` = **656,699,589 B**(mtime 11-13 02:39:02,最后一次浏览器采集时刻)、`.browser\pw-artifacts` = **40,541,067 B**。`fetcher.py:96-107` 的 24h 清扫**只作用于 `pw-artifacts` 子目录**,`profile` 本体(定义见 `fetcher.py:53 browser_profile_dir()`)没有任何老化/上限清理策略。
- 差异 ≈ 0.66 GB 的浏览器 profile **不在审计视野内**,`browser_max_gb: 3` 的限额检查因此恒真。

### D6 测试与 E2E 事件写进生产日志流(CONFIRMED)
- `09_logs/ttxt-20261113.jsonl` 内含 fixture 事件:`10:34:34` `crawler.worker worker.stop owner=test-worker reason=max_jobs`;`10:34:39` `model_gate gate.job_start/gate.job_end job_id=1..3`;`10:34:42` `writing writing.done category=demo draft_id=demo-20261113103442-2779aa status=passed`;`11-12 23:59:01` 同型事件重复。当日模块分布:crawler.runner 225、model_gate 160、crawler.fetcher 153、writing 104、crawler.worker 72、housekeep 10、tests 9、backup 3。
- 影响:生产日志与测试噪声混流,事后无法只用日志区分真实运行(本轮 §A 的 worker 事件即为 fixture,不能作为「worker 在生产运行」的证据)。

### D7 空类目的 schema 迁移不全(SUPPORTED→CONFIRMED 报错)
- 对 history/social 查询 `evidence_links.source_url` 报 `no such column: source_url`(同 `checks_json`)→ 这两个类目库没有 P12 新增列;`writing_history` 等表存在但为空。
- 影响:`run_analysis` 目前对 history/social 返回 "ok"(§scheduler_state 里 6 个类目全 "ok"),但任何依赖新列的读路径会在空类目上抛错。

---

## E. 数据质量问题(CONFIRMED 读数)

| 项 | 实测(口径:`01_categories/<slug>/articles.db`,`articles` 表) |
|---|---|
| health 语料不可写作(评审问题 1) | 25 行,`content` 长度 **min 35 / max 142 / avg 91.72**;`cleaned_content` 0/25;唯一来源 `who_cn_news`(公开 RSS,`max_articles 25`)给出的是**摘要级条目** |
| finance 语料含 HTML 残片 | 50 行,`content` 长度 min 10 / **max 26,844** / avg 3,697.82;按正则 `<[a-zA-Z/]|https?://|style=|class=|EM_|data-code` 命中 **31/50**;`cleaned_content` 0/50 |
| technology 语料含 HTML 残片 | 137 行,`content` min 24 / max 6,719 / avg 1,727.09;正则命中 **31/137**;已清洗的 10 行 `cleaned_content`(219–3,505 字符,avg 2,145)命中 **0** → 清洗本身有效,问题是从未对全量执行 |
| 可信度字段未接类目分级 | `credibility='unverified'` 在 5 个类目 **100%** 的行上 |
| 去重/聚类未生效 | `simhash` NULL 127/137、50/50、25/25;`article_clusters` 仅 technology 10 |
| 真实头条指标为零(评审问题 3,阻断 L4 校准) | `article_metrics` / `metric_snapshots` / `prediction_records` 在 5 个类目**全部 0 行**(`config.json` `prediction.min_samples: 5`) |
| 历史/社会无合格来源(评审问题 5) | `history`、`social` 文章 **0 行**;`sources.json` 中 enabled 分布 = technology 4、finance 2、health 1、**history 0、social 0**;注册表 notes 记录候选被 robots 拒绝(ftchinese/gmw/geekpark)、404(chnmuseum/dpm/chinanews)、存档陈旧(people_health/xinhuanet) |
| 写作通过率(诚实口径,两种粒度分开) | **任务级:draft 5 个(tech 1 / finance 2 / health 2),passed 2 → 2/5**;**版本级:`evaluation_history` 13 行评分**(tech 0.65/0.65/0.95;finance 0.30/0.40/0.65 + 0.95;health 0.40/0.65/0.60 + 0.85/0.85/0.85)。其中 score≥0.6 的版本有 10/13,但只有 2 个任务最终 passed(其余被 critical 挡住) |
| `writing_history.status` 不是逐版判定(度量陷阱) | 同一 draft 的每个 version 行都写入了**任务最终状态**(tech 三版都是 `passed`,health 三版都是 `rejected`)→ **不能用 `writing_history` 数「版本通过率」**,必须用 `evaluation_history` |
| P13 golden set(评审问题 4,阻断) | `10_tests/golden/golden_set_v2.jsonl` = **61 行**;`human_adjudicated = 0`;**tier-B 独立来源卡片 = 0**(上一轮子代理两次声称写文件、文件从未存在,本轮再次确认无该文件) |
| `01_categories/demo/` 测试残留 | `demo/articles.db` 397,312 B(mtime 11-13 00:36:55),不在 5 个注册类目内;`crawl_jobs` 里 demo 有 9 done(`quotes_toscrape`/`sspai_rss`/`ruanyifeng_atom`)+ 3 aborted(`e2e_unreachable`);`04_writing/drafts/demo/` 3 passed + 2 rejected。**仅登记,不删除** |
| `01_categories/*/rag/` 目录为空 | technology/finance 的 `rag/` 无文件 → RAG 资产实际在库内 FTS 表,目录为空壳(记录事实,不判定) |

---

## F. Strata 保护状态(全程只读,零干预)

- `GET /health` → **200**:`{"status":"ok","max_context":147456,"model":"qwen3.8-flash-next-iq3_s","images":true,"api_key":false,"image_generation":false,"loaded":true,"service":"strata"}`
- `GET /v1/models` → **200**:**只有 1 个模型** `qwen3.8-flash-next-iq3_s`,`n_ctx 147456`
- `nvidia-smi`:Tesla V100-SXM2-16GB,**16004 / 16384 MiB**,util 0 %(本轮测时无推理)
- 属主进程:PID 26032 `…\Strata\components\engine\0.1.58-windows-x86_64-cuda-cuda12\strata.exe`(启动 2026-11-12 19:38:59,WS 47.9 GB)+ PID 1976(权限拒绝读命令行);另有 Strata 自带 `strata_control.runtime`(9136)与 `serve.server --engine strata --port 8080`(14752)
- 未做任何干预:无 POST、无 chat/completions、无启停、无参数修改、无并发;历史 70 条 `model_jobs` 重叠窗口 = 0(§A)
- README 对照(只允许改文档):`README.md:11` 的当前模型 = `qwen3.8-flash-next-iq3_s`,与 live **一致**;`README.md:8` 写「Strata 引擎已占 15.1 GB」,实测 16004 MiB ≈ 15.6 GiB(**轻微漂移,SUPPORTED,文档可改**);README 未记录 `max_context`,与 live 147456 **无冲突**
- `00_control/model_gate` 文件 0 B(mtime 11-13 01:04:05)= 当前无持锁者,与「无模型任务在跑」一致(CONFIRMED)
- 浏览器侧无关噪声:26 个 `msedgewebview2.exe` 全部属 365VPN(`--webview-exe-name=365vpn.exe`),**不是本项目进程**;本项目当前没有 msedge/headless_shell 在跑(CONFIRMED)

---

## G. 磁盘与临时文件风险(CONFIRMED)

口径 A(10:53,含隐藏文件,`Get-ChildItem -Force -Recurse` 字节累加,`D:\ttxt`):
**总计 1,311,340,562 B(1.311 GB 十进制 / 1.221 GiB)**;不含 `.venv` = 1,023,093,095 B。

| 目录 | 字节 | 风险判定 |
|---|---|---|
| `02_crawler\.browser` | 697,240,656 | 最大单项;Edge profile + Playwright artifacts。`fetcher.py:91-108` 有 24h 清扫子目录,但 profile 本体无清理策略;且 §D5 显示审计看不见它 |
| `.venv` | 288,247,467 | 正常 |
| `tools` | 195,013,391 | 便携 MinGit + gh,刻意保留(离线可用性) |
| `.pip-cache` | 95,691,429 | 低于 `pip_cache_max_gb: 1`,OK |
| `.\pytest-of-yangl`(项目内) | 14,848,428 | 测试 tmp 残留,落在项目里计入预算 |
| `11_backups` | 9,159,778 | 7 份 zip,等于 `backup_keep: 7`,刚好满额 |
| `01_categories` | 3,981,312 | 远低于 `db_max_mb.article: 2048` |
| `99_temp` | 3,527,598 | 低于 `temp_max_gb: 2` |
| `.git` 762,186 / `09_logs` 329,517 / `00_control` 460,458 / `08_wiki` 235,630 / `07_tmp` 40,008 | — | OK |

**预算外残留(在用户 TEMP,不被 20 GB 项目预算覆盖)**:
`C:\Users\yangl\AppData\Local\Temp` 下匹配 `playwright|pytest|scope|ttxt` 的残留目录 **12 个**:
`playwright_chromiumdev_profile-1BOkx1` 18,457,758 B(mtime 11-12 11:19:15)、
`pytest-of-yangl` 6,134,982 B(11-12 13:01:45)、`ttxt_p11_http` 61 B(11-12 19:53:55)、
`playwright-artifacts-Ms2vZZ` 0 B(11-12 11:19:08)、`scoped_dir*` 8 个(11-08 23:07 ~ 11-12 12:08,均空/0 B)。
**这 12 项的 mtime 最晚为 11-12 19:53:55,没有 11-13 的新目录** → 说明 `fetcher.py:114-118` 的「TMP 钉到项目内」(提交 `7c1074d`,11-12 22:30,`git log -S "pw-artifacts"`)之后已停止增长(CONFIRMED);这批属修复前的历史遗留,是否清理由用户决定(本轮不动)。
但**项目内仍有 14.8 MB 同名 `pytest-of-yangl` 残留**,说明测试临时路径未完全受控(见 §D6/§H7)。

---

## H. 下一批最小修改建议(仅建议,本轮未改一行代码)

按「① 采集验收/FTS 同步」优先。**每条都是最小改动 + 明确影响文件 + 验收命令**,不做重构、不加新 Agent、不装 CloakBrowser。

1. **采集成功路径衔接清洗与索引**(解 D1/D2 的主干)
   - 改动:在 `analyze.run_analysis` 的 `keywords` 之前插入 `crawler_pipeline.run_pipeline(category)`(清洗+聚类),使一次 analyze 同时完成 clean → cluster → index;或新增 `kind="clean_analyze"` 调度项(不改现有 4 个任务的语义)。
   - 影响文件:`ttxt/analysis/analyze.py`、(可选)`ttxt/scheduler.py`、`00_control/config.json`(仅当新增 job)、`10_tests/`新增用例(断言 analyze 后 `articles_fts` 计数 == `articles` 计数、`clean_version` 非空率>0)。
   - 验收:`.venv\Scripts\python.exe -m ttxt analyze technology` → 返回 JSON `index.indexed == 137`;`SELECT COUNT(*) FROM articles WHERE cleaned_content IS NULL` 从 127 降至 0 或给出可解释的跳过原因;随后 FTS 缺失数应为 0。
   - 风险:`clean_article` 对 26,844 字符长文的处理耗时;需按类目分批并计时,不改 Strata、不调用模型。

2. **补跑一次清洗+分析,并让索引滞后变成可见指标**(解评审问题 2 的可观测性)
   - 改动:`doctor`/看板新增「FTS 覆盖率 = fts 行数 / 文章数」与「cleaned 覆盖率」两个只读指标(仅读数,不写库)。
   - 影响文件:`ttxt/doctor.py`、`ttxt/dashboard/api.py`、`10_tests/test_doctor*.py`。
   - 验收:`.venv\Scripts\python.exe -m ttxt doctor`(注意:doctor 会写 `09_logs`,需用户许可)。

3. **磁盘审计补上隐藏目录**(解 D5)
   - 改动:housekeep 的 sizes 计算包含 `02_crawler/.browser`(或按 `browser_profile_dir()` 直接测),并把 `browser_bytes` 填入 `caps_status`;必要时增加 profile 老化清理(仅删 `.browser\pw-artifacts` 与可再生缓存,**不动 profile 本体**,除非用户另行批准)。
   - 影响文件:`ttxt/housekeep.py`、`10_tests/` 新增用例(断言 `browser_bytes > 0` 时口径正确)。
   - 验收:`-m ttxt housekeep` 后 `scheduler_state.json` 的 `crawler` 值接近 697 MB 量级,`project_total_gb` 与 `Get-ChildItem -Force -Recurse` 差异 < 5%。

4. **健康类目取材:摘要级语料不得进入写作取材池**(解评审问题 1,先止血再补源)
   - 改动:取材查询加最低长度与 HTML 残片过滤(`material`/`rag` 取材层按 `cleaned_content` 长度阈值 + 正则过滤),并在 `completeness` 上按真实长度写值(不再恒 `'full'`)。
   - 影响文件:`ttxt/writing/workflow.py:400-420`(取材 SQL 与阈值)、`ttxt/writing/workflow.py:160-166`(`[:240]` 素材截断)、`ttxt/crawler/pipeline.py`(completeness 真实判定)、`ttxt/crawler/store.py`(字段语义)、`10_tests/` 新增用例(35 字符条目不得被选中)。**注意:不存在 `ttxt/writing/material.py`,取材就在 `workflow.py` 内联。**
   - 验收:在 health 上跑写作取材 → 候选数 0 且日志给出 `material_insufficient` 原因(而不是写出 `no_source` 高分草稿)。

5. **worker / scheduler 是否需要常驻:先确认运行模型再决定**(解评审问题 6,队列调度复核)
   - 现状事实:worker 与 scheduler daemon **都没有跑**,`queued` 任务 1 条(id 54,attempts 0,owner NULL)无人认领;`worker.lock` 文件残留但不持锁。
   - 建议(需用户批准):**不自动启动**任何常驻进程;先在 `doctor` 里把「worker 持锁状态 / 队列积压数 / 调度器最后 tick 距今」列为只读告警,再由用户决定用 `--daemon` 还是计划任务外部拉起。
   - 影响文件:`ttxt/doctor.py`、`08_wiki`(运维口径)、README 的 cron 段落(已有 dual-cron 说明)。
   - 陈旧任务回收复核(CONFIRMED 语义,无需改码即可判定):`runner.py` 的 `claim_next_crawl_job(owner, lease_s=900, category)` + `store.recover_stale_crawl_jobs` + `requeue_failed_crawl_jobs` 在 `scheduler._job_recover` 里已串好(`scheduler.py:128-137`);实测 `attempts` 从未超过 `max_attempts`,历史无「后来场景抢走先前场景陈旧任务」的正向证据(§A 读数);**但**评审担心的「跨场景回收」在 `recover` 按 `status+lease` 无差别回收的意义上确实成立 → 若要按场景隔离,需在 `crawl_jobs` 增加 `scenario` 列并在 recover 时按场景过滤(影响 `ttxt/crawler/store.py`、`runner.py`、`scheduler.py`、`10_tests`)。**本轮判定为 SUPPORTED(机制成立)、未观察到实际事故。**

6. **history / social:本轮无可注册来源,禁止为凑数注册**
   - 事实:`sources.json` 两类目 enabled = 0,候选探测记录均失败(robots 拒绝 / 404 / 存档陈旧)。
   - 建议:保持 0 源 + 空类目,把「无合格来源」作为验收结论登记;需要时只做**只读探测脚本**(读 robots + 抽样标题新鲜度),探测证据落 `99_temp/`,合格后才写 `sources.json`。
   - 影响文件:无(仅 `99_temp/` 探测产物)。

7. **测试与生产日志/路径隔离**(解 D6,属卫生问题,低优先)
   - 建议:测试运行时把日志目录指到 tmp(`tmp_path`),`demo` 类目库与 `drafts/demo` 也走 tmp;生产日志只保留真实事件。
   - 影响文件:`ttxt/config.py`(可注入的日志根)、`10_tests/conftest.py`。

8. **P13 golden set 人工裁定仍是硬门槛**
   - 事实:61 卡片、`human_adjudicated=0`、tier-B 独立来源 0。
   - 需要用户提供:≥50 条人工裁定(每类目 ≥10、A+B ≥50%、负例 ≥30%、每类 ≥3),以及**真实**独立来源集合。无可信来源时保持 tier-A,不做假源。

9. **文档待同步(等用户批准后才改)**:`README.md` 的 P13 行需指向本报告;`08_wiki` 索引加一行;`P13-batch1-golden-set-v2.md` 内相对链接错误待修;`scripts/build_golden_set_v2.py` 工作表措辞「通过=认可机器核验结论」应改为「人工判定事实是否被来源支撑」。**本轮一处未改。**

---

## I. 对先前报告/口径的更正(必须记账)

1. technology FTS 缺失:**79**(先前写 75)。「陈旧 4 行」作废——那是 FTS5 `rowid` 与 `article_id` 混用的度量错误,实测 `article_id` 口径 **0**。
2. 先前「科技 30/137、财经 31/50 含 HTML 残片」未标口径。本轮明确:命中的是**原始 `content`** 列(科技 **31/137**、财经 **31/50**);`cleaned_content` 命中 **0**,但 `cleaned_content` 只有科技 10 行非空、财经/健康 **0 行**。
3. 先前「attempt-level 2/5」口径错误。正确:`evaluation_history` 13 次逐版评分,10 次 ≥ 0.6,但**任务级 2/5 passed**;`writing_history.status` 是任务最终状态的冗余,不能当逐版判定。
4. 先前把 housekeep 快照的 0.364 GB 当作项目体量参考:该数字漏算 `.browser`(697 MB),不可用;本轮以 1,311,340,562 B(含隐藏)为准。

## J. UNKNOWN(本轮无法判定,不得当结论)

1. `crawl_jobs` id 54(`queued`,08:51:42)由哪条路径入队:`scheduler_state.json` 未更新 ⇒ 不是调度器运行所致;最可能是 P13 batch0 验收时手工/gap 入队,**未证实**。
2. 为何出现两个 `ttxt serve`(其一为非 venv 解释器)且只有一个持 8765:启动来源未记录。
3. `viral_labels` technology 114 行 vs 137 文章的差额 23 是标签阈值还是语料缺失:未逐行核。
4. `golden_set_v1.json` 1391 行是格式化 JSON 的行数,不是卡片数;其卡片总数未重新统计(v1 卡片口径待用脚本统计后再引用)。
5. P12 72h/7d 浸泡与 161 测试全绿:属外部回执,本机本轮未复跑测试(测试会写 `09_logs` 与 tmp,本轮禁止)。
6. Strata 内存 16004 MiB 中除引擎常驻外还有多少可释放:未测(且禁止干预)。

## K. 本轮结束状态

- 文件改动:**仅新增本报告 1 个文件**;业务代码、配置、数据库、备份、草稿、语料、README **零改动**。
- 未启动任何采集/写作/模型请求;未提交、未推送(是否把本报告提交进 `main` 等用户指令)。
- 不自动进入下一批。
