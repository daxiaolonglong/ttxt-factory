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
| 5 | **C-7 历史重复行整改**(本窗口最后完成):新增 `ttxt/crawler/dedupe.py`、`doctor` 三项体检、`dedupe-history` 子命令;生产删 16 行(同类目 3 + demo 跨库副本 13),`demo` 那 30 行列表页塌陷行**判为可疑、一行不删**;顺带修好长期滞后的 `articles_fts` | `07_tmp/p16_dedupe_20261115/census.json`、`07_tmp/p17_dedupe_apply_20261115/{state_before,plan_dryrun,ledger_apply,state_after,rollback_rehearsal}.json`、报告 `08_wiki/reports/P16-fix-C7-history-dedupe-20261115.md`、规则 33/34 | `d817805`(**未推送**,见待办 1) |

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

## 四、事故如实记录(已全部修掉并写进规则)

1. C-4 测试里 `monkeypatch.undo()` 撤销了路径隔离,改写生产 `demo/articles.db`(证明无害);规则 32 禁止。
2. 一次全量测试打开过生产 demo 库(补 `session_categories_dir` 夹具修掉);规则 25/32。
3. 打桩返回 `None` 在仓库根造出名为 `None` 的库文件(产物已移入 `07_tmp/p15_crawler_verify_20261115/`,产品侧现在抛 `TypeError`)。
4. `backup.py` 的轮转藏在 `snapshot()` 里,曾静默删掉两个历史备份;规则 29:`disk.backup_keep` 提到 30,自动删除必须逐条打日志。
5. C-7 首版 `read_rows` 用 `sqlite3.Row`,而 `store.read_only_connect` 返回元组 → 11 红;`apply_cross` 首版 CAS 空转 → 重写按 `content_hash` 逐行比对。
6. C-7 删行后 P7 面板一条测试红:断言押的是生产正文内容("demo 里搜得到『城市』"),已改为播种一行(规则 34)。

## 五、待办(按优先级,接手就从 1 开始)

1. **推送 `d817805`**:本机到 GitHub 的 TLS 现在被切断(`ls-remote` 也失败,TCP 443 通),`main...origin/main [ahead 1]`。网络恢复后执行:
   `git push origin HEAD`(用 Cherry Studio 自带 git + `GIT_DIR=D:\ttxt\.git` + `GIT_WORK_TREE=D:\ttxt`)。
2. **C-7b 修抽取器身份塌陷**:`ttxt/crawler/extract.py:31-58` 的 `extract_html_items` 把 `url=source.url` 赋给每一条 → 列表页 30 行同一个 URL。要改成取每条自己的链接;入库时"同 URL 不同内容"不得静默,要计数 + 记日志;配一条 `doctor.db.url_identity` 体检。做完才能谈 `demo` 里那 30 行。
3. **恢复真实采集并核验采集栈**:按 Scrapling / CloakBrowser 官方文档核验本地部署(Scrapling 0.5.1 + curl_cffi + playwright,HTTP 优先;CloakBrowser 不装新东西)。已知残留风险:Scrapling 发伪装 UA + Google referer,而 robots 判定用项目 UA(`07_rules` 规则 19 相关,需要人拍板口径);浏览器 profile 547MB vs 配额 `disk.browser_max_gb 3`。
4. **Worker / Scheduler 未启动**:定时采集与模型串行队列还没跑起来(`00_control/scheduler_state.json`)。
5. **history / social 两个类目 0 行**:缺已批准来源,要人给来源清单才能补采。
6. **C-5 可信度分档**:现在全 `unverified`,要人批准分档口径。
7. **C-6 评审元数据 DDL** + **9 条 `evidence_links` 无出处**的修法:涉及改表结构,必须先获批准。
8. 清理项(需批准才动):`D:\ttxt\pytest-of-yangl` 残留目录 —— 本窗口没有删除任何旧文件/日志/备份/WAL/SHM。

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
