# 架构与环境(P0 基线)

> 本文件随阶段推进持续更新。P0 实测基线:2026-11-12。

## 1. 实测环境(P0)

| 项 | 值 |
|---|---|
| OS | Windows 10 专业版 22H2 (19045) |
| CPU | Xeon E5-2696 v3,18C/36T @2.30GHz |
| RAM | 63.9 GB |
| GPU | Tesla V100-SXM2-16GB(驱动 537.13,严禁升级)+ AMD HD 6570(显示) |
| 显存现状 | Strata 引擎占用 15.1 GB;禁止第二个模型 |
| 磁盘 | C: 240GB(59.4 空闲)/ D: 234.7GB(**110 空闲**,含 111GB Q4 模型包)/ E: 450GB / F: 504GB |
| Python | 3.12.10 + pip 25.0.1(用户级);venv 在 `D:\ttxt\.venv` |
| 其他工具 | 无 git / node / npm / pnpm / uv / conda;SQLite 用 Python 内置 |
| 浏览器 | 仅 Microsoft Edge(x86);HTTP 采集优先 |
| Strata | `http://127.0.0.1:8080`,OpenAI 兼容 `/v1`;`/health` 实测 200:`qwen3.8-flash-next-iq3_s`,max_context 147456,无需 API Key |
| DSH | home `C:\Users\yangl\.dsh`,活动 preset `cordis`;项目级 skill 发现待 P7 实测 |

## 2. 组件与数据流

```
面板(05_dashboard,127.0.0.1:8765)  ← 读 DB/日志 + 受护栏的任务下发(仅 crawl 入队/analyze;写作/发布/模型 404)
       │
本地调度器(ttxt scheduler:仅 crawl_enqueue/analyze/recover/housekeep;配置禁止 write/publish/model)
        │
控制中心(00_control:system.db/config/registry/scheduler_state.json)
       │
采集(02_crawler:Scrapling HTTP 优先 → 浏览器按需)→ 清洗/去重/聚类
       │  ↑ C-4(2026-11-15):采集与清洗是同一条链 —— runner._execute_job 插入之后当场
       │    跑 pipeline.run_pipeline(category),统计进 summary["pipeline"] 与 crawl_jobs.meta_json
        │  ↑ P25(2026-11-16):同一条链还要补齐检索索引 —— 管线末尾调 rag.rebuild_index 写 articles_fts,
        │    回执新增 indexed;对账在 doctor.db.fts_coverage(不一致即 FAIL)。analyze 退为每日兜底。
       │
类目隔离库(01_categories/<cat>/articles.db + rag/keywords/metrics)
       │
分析(03_intelligence:指标快照→爆款四级标签+置信度→趋势/生命周期→关键词评分(P28:真实爆款信号按 0.3 权重融进
      keyword_performance.score;只认 measured 且 score 非空,类目样本<30 或词级命中<3 一律拒发加分,缺失≠0)
      →知识资产/FTS5 RAG
      →关键词反哺:零覆盖词标 gap 并自动入队采集(去重),gap↔active 状态机,每类目 JSON 证据)
       │
唯一模型入口(00_control/model_gate:SQLite 队列 + 串行锁 + /health 等待)
       │
写作(04_writing:选题→大纲→正文→事实核查(证据链)→反向评审→修改≤2 轮→版本)
       │
六角色(06_agents)由总调度装载,全部经 model_gate 串行
       │
真实表现回流(仅 config 白名单来源导入,未授权拒绝)→ 预测对照校准
(无实测基线拒绝预测;回填样本 < min_samples 不写知识资产)
```

## 3. 关键技术决策

| 编号 | 决策 | 理由 |
|---|---|---|
| D1 | 全 Python 标准库优先;面板用 `http.server` + 原生 JS 轮询 | 无 Node,减少组件与依赖 |
| D2 | 数据库 SQLite(WAL,每类目独立 articles.db) | 无额外常驻服务,类目物理隔离 |
| D3 | 唯一模型闸门 model_gate,SQLite 持久队列 + 串行锁 | 杜绝模型并发;崩溃可恢复 |
| D4 | 采集 HTTP 优先;CloakBrowser 暂缓 | 成本/许可/缓存管理;Scrapling 已够用 |
| D5 | 来源清单由用户批准写入 sources.yaml;robots 遵守 | 合法合规,不绕过访问控制 |
| D6 | 项目磁盘预算 20GB,每阶段实测 | D: 空闲 110GB 与 111GB 模型包共享 |
| D7 | 指标仅授权来源,带 completeness/credibility | 杜绝伪造指标 |
| D8 | Agency Agents 角色本地化进 `06_agents`,不装全局 skill | 零 DSH 全局配置改动 |

## 4. 阶段状态

| 阶段 | 状态 |
|---|---|
| P0 环境扫描 + 架构审查 | ✅ 已批准 |
| P1 脚手架 | ✅ 已完成(报告:`reports/P1-report.md`) |
| P2 Scrapling 采集 | ✅ 已完成(报告:`reports/P2-report.md`) |
| P3 数据库 / 清洗 / 去重 | ✅ 已完成(报告:`reports/P3-report.md`) |
| P4 关键词与爆款分析 | ✅ 已完成(报告:`reports/P4-report.md`) |
| P5 Strata 串行模型接入 | ✅ 已完成(报告:`reports/P5-report.md`) |
| P6 写作与反向评审 | ✅ 已完成(报告:`reports/P6-report.md`) |
| P7 面板与自动化 | ✅ 已完成(报告:`reports/P7-report.md`) |
| P8 全链路验收 | ✅ 已完成(报告:`reports/P8-report.md`) |
| P9 头条科技来源接入 | ✅ 已完成(报告:`reports/P9-toutiao-tech.md`) |
| P10 生产可靠性修复 | ✅ 第一批完成(报告:`reports/P10-report.md`) |
| P11 生产闭环第二批 | ✅ 已完成(报告:`reports/P11-report.md`) |
| P12 可靠性与硬化(第二批) | ✅ 已完成(报告:`reports/P12-batch2..5*.md`) |
| P13 资产审计 / 质量闭环审计 | ✅ 已出报告(`reports/P13-asset-audit.md`、`reports/P13-quality-audit-20261113.md`) |
| P13 最小修复 C-7 + C-8(模型闸门锁、测试隔离) | ✅ 已完成(报告:`reports/P13-fix-C7C8-20261114.md`,198 passed) |
| P13 最小修复 C-2(同类目 URL 重复插入) | ✅ 已完成(报告:`reports/P13-fix-C2-url-dedupe-20261115.md`,214 passed / exit 0) |
| P13 最小修复 C-3(跨类目归属) | ✅ 已完成(报告:`reports/P13-fix-C3-cross-category-dedupe-20261115.md`,227 passed / exit 0) |
| P13 最小修复 C-1(正文完整性判定 + 生产回填) | ✅ 已完成(报告:`reports/P14-fix-C1-completeness-label-20261115.md`,255 passed / exit 0;生产 124 行标签已如实回填) |
| P15 采集栈核验(栈体检 + 真实采集恢复) | ✅ 已完成(报告:`reports/P15-crawler-verify-C4-clean-coverage-20261115.md`) |
| C-4 采集→清洗一条链 + 清洗覆盖率体检 | ✅ 已完成(同一份报告,278 passed / exit 0;覆盖率 43/328 → 328/328) |

## 5. 当前状态(2026-11-12)

- P0–P11:框架 + 采集 + 数据库 + 分析 + 串行模型 + 写作门禁 + 面板 + 头条科技真实来源 + 可靠性修复 + **生产闭环(指标回流/预测校准/关键词反哺/本地调度/面板下发)**。
- 诚实状态:预测与校准环等待头条后台真实指标导出(机制就绪,无数据不伪造);第三批(多类目长跑、故障全矩阵、写作质量回归、磁盘压测)未做。P11 发现并修复三个闭环级缺陷(调度器时区崩溃、IPv6 回环护栏不自洽、gap 关键词评分死锁),见 `reports/P11-report.md` §2。
- 运维命令见 `operations.md`;回滚快照见 `11_backups/`。

## 6. 当前状态(2026-11-15 实测)

- 测试基线:`python -m pytest 10_tests -q` → **255 passed,47.45s,exit 0**(227 旧 + 25 C-1 新增 + 3 备份轮转护栏,0 skip / 0 fail)。`pytest`(无参数)、`pytest .`、`pytest 10_tests` 三种调用形式均 255 passed —— 前两种原先会让 C-8 的 `pytest_configure` 不被调用、临时库落到仓库根 `pytest-of-*`,已用仓库根 `conftest.py` 前转配置钩子修好(规则 27)。
- 数据现状(2026-11-15 C-1 回填后):`01_categories/` 6 库 + `00_control/system.db` 行数不变(文章共 257 行),但 4 个库的 `.db` sha **因 C-1 整改而变**:回填前快照 `11_backups/C1-pre-relabel-20261115-093705.zip` + 字节级原像 `07_tmp/c1_20261115/db_c1/` + 逐行回滚账本 `07_tmp/c1_20261115/relabel_prod_apply*.json`;逐列比对证明除 `completeness` 外没有任何列变化(`07_tmp/c1_20261115/verify_prod_c1.json`)。只读打开 WAL 库会把空 WAL checkpoint 回主库并删除 `-wal/-shm` 边车,主库 sha 不变(规则 25)。
- C-2 已修:类目库内同 URL 不再插第二次,重复如实计数,并发实测 4 线程只 1 行(证据 `reports/P13-fix-C2-url-dedupe-20261115.md` §5–§6)。
- C-3 已修:类目命名空间之间归属互斥。写入前只读扫描其它命名空间(`url_normalized` + `content_hash`),别的**注册**类目已持有就不落第二行,少掉的条目如实计入 `duplicates`,归属进 `meta_json.cross_db` + `crawl.cross_db_duplicate` 日志;`doctor` 新增 `namespaces.*` 与 `db.cross_scan` / `db.cross_overlap` 对账(正式类目之间同一篇文章 = FAIL)。实测真实数据:唯一交集 `demo`↔`technology` 13 行(根因 `sspai_rss`/`ruanyifeng_atom` 在 P12 由 demo 转正),`ttxt doctor` 现在如实报 WARN;副本上四条验证全绿(正式→正式被拦、未注册不卡正式、注册持有者卡 demo、绕过守卫后 doctor FAIL),证据 `reports/P13-fix-C3-cross-category-dedupe-20261115.md` 与 `07_tmp/c3_20261115/proof_c3.json`。
- C-1 已修:`completeness` 不再"有正文就 full"。三档 `full`(≥400)/`short`(1–399)/`excerpt`(无正文),判定唯一在 `cleaner.completeness_of`,写入点逐行算,清洗管线不回写;`doctor` 新增 `db.completeness.<类目>` 反查谎报/低估/词表外值;整改命令 `ttxt relabel-completeness`(默认干跑 → `--apply` 逐行 CAS → `--rollback <报告>`)。生产回填 124 行(正式 81 + `demo` 43),分布从 `full=257` 变成 `full 133 / short 124 / excerpt 0`,与 census 预测一字不差;副本演练 A–L 十二条全绿、SQL 与 Python 口径 drift=0(证据 `07_tmp/c1_20261115/proof_c1.json`、`reports/P14-fix-C1-completeness-label-20261115.md`)。
- 事故与补法(C-1 期间,规则 29):误用 `scripts/backup.py` 的 `snapshot()`(轮转就在该函数内,不在 `main()`),`backup_keep=7` 当场静默删掉 2 个历史快照(`E2E-20261112-142100.zip`、`P10-final-20261112-142130.zip`)。已补:轮转逐条打 `backup.pruned` 日志、`backup_keep<=0` 表示全留、`disk.backup_keep` 7→30,并加 3 条回归测试(`10_tests/test_backup_rotation.py`)。历史备份无法恢复(不在 Git、不进回收站),但当前数据完整、C-1 回滚链完整。
- 未做/待办:历史重复行清理(`technology` toutiao ×3、`finance` eastmoney ×2、`demo`↔`technology` 13 行)需单独批准的修复任务;html 列表页抽取器仍不逐条取详情链接(`extract.py:55`),是列表页数据只按 `content_hash` 去重的根因;C-4 `pipeline` 长跑(覆盖率 95.28% 从未清洗)、C-5 可信度分层(257 行全是 `unverified`)、C-6 评审元数据(需 DDL)。
- 采集侧现状:Scrapling 0.5.1 + curl_cffi 0.17.0 + playwright 1.65.1 已装,HTTP 优先(D4);CloakBrowser 未安装(暂缓)。C-1/C-2/C-3 已全部通过,下一步按官方文档核验采集栈后恢复真实采集;Worker/Scheduler 在采集核验前仍不启动。
- 磁盘与临时文件:项目内临时文件统一到 `07_tmp`(C-8 保留 10 份 basetemp 并自动回收);本轮实测项目占用 1.222GB / 预算 20GB(`ttxt doctor`),无暴涨。仓库根遗留 `pytest-of-yangl\`(pytest 早于 C-8 时留下的临时目录)未删除,按"不得删旧数据"保留,等批准清理。

## 7. 当前状态(2026-11-15 P15 + C-4 实测)

完整报告:`reports/P15-crawler-verify-C4-clean-coverage-20261115.md`;证据目录 `07_tmp/p15_crawler_verify_20261115/`。

- **真实采集已恢复并跑完**:7 个启用正式来源各一次(`crawl_*.log` ×7),库内总计 **328 行**(demo 45 / finance 97 / health 25 / technology 161 / history 0 / social 0)。头条端到端:列表页 15 条链接 → 2 篇正文(7465 / 4304 字,都是 `full`)。Worker/Scheduler 仍未启动(等下一批决定)。
- **栈体检常驻**(`ttxt/crawler/stackcheck.py`,`doctor` 的 `crawler.stack.*` 六行):已装版本 scrapling 0.5.1 / curl-cffi 0.17.0 / patchright 1.64.4 / playwright 1.65.1 / browserforge 1.2.4 / lxml 6.1.3;浏览器与 HTTP 实参全部命中 Scrapling 词表(38 + 19 键,取 `__annotations__`,不用 `get_type_hints`);Edge 可执行文件在;浏览器来源 1 个;profile 547MB / 限额 3.0GB。浏览器实参漂移 = FAIL(规则 30)。
- **浏览器磁盘缓存在源头封顶**:`fetcher._browser_kwargs` 加 `--disk-cache-size`,`00_control/config.json → browser.disk_cache_mb = 64`(`<=0` 不加旗标)。起因:头条频道页一次访问让 profile 涨 16MB。
- **C-4 根因与修复**:清洗覆盖率低不是性能问题(328 行全量清洗 4.3 秒),而是采集入口从不叫清洗 —— 43/328(13%)。现在 `runner._execute_job` 插入后当场跑管线,管线异常只记 `pipeline_error`(采集仍 ok,文章行保留);`doctor.db.cleaned_coverage` 按库对账,三种 WARN(完全没有清洗列 / 空库 / 覆盖率落后)都给补跑命令。实测链上证据:`ttxt crawl --source ruanyifeng_atom` → `ok: true … pipeline {cleaned: 161, clusters: 161, clean_version: p3-v1}`,整轮 3.24s。覆盖率现在 **328/328 PASS**。
- **测试基线**:**278 passed / exit 0**,三种调用形式(`pytest`、`pytest .`、`pytest 10_tests`)全过(63.94s / 62.36s / 50.98s);新增 15 条栈体检 + 8 条 C-4。
- **两次生产事故(已披露,规则 32)**:①测试里用 `monkeypatch.undo()` 恢复单个补丁,连带撤掉类目隔离,管线改写生产 `demo` 库;②老测试只 patch system.db 忘了 patch 类目根 + `init_articles_db` 桩返回 `None`,导致套件跑期间打开生产 `demo` 库、并在仓库根造出 4096 字节的 `None` 库文件。补法都是系统性的:conftest 新增 session 级夹具把 `CATEGORIES_DIR` 默认挪到 `07_tmp`;桩改返回 tmp 真实路径;`migrate_articles_db` 对非路径返回值当场 `TypeError`。那个 `None` 文件未删除,已挪进证据目录 `artifact_None_from_test_stub.db`。两次事故都只碰清洗派生列,采集侧逐行不变(`clean_proof.py compare` → `compare: OK`,`added/immutable_broken/rows_lost` 皆空)。
- **`ttxt doctor` 现状**:25 ok,0 failed;仍存 WARN 只有两条,都如实:`namespaces.disk`(`demo` 有库未注册)、`db.cross_overlap`(`demo`↔`technology` 13 行,P12 期间来源转正留下的历史重复)。
- 未做/需单独批准:历史重复行清理(`technology` toutiao ×3、`finance` eastmoney ×2、`demo`↔`technology` 13 行)、C-5 可信度分层(328 行全是 `unverified`)、C-6 评审元数据(需 DDL)、删除仓库根 `pytest-of-yangl\` 遗留目录。
