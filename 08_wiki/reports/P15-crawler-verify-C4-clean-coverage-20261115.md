# P15 采集栈核验 + C-4 清洗覆盖率(2026-11-15)

报告口径:事实 / 问题与根因 / 修改 / 测试 / 事故 / 前后对比 / 回滚位置 / 剩余风险。
本批全部证据在 `07_tmp/p15_crawler_verify_20261115/`(下列文件名都指该目录)。

## 1. 一句话

按官方文档把爬虫栈核验清楚并做成体检项,真实采集**已恢复并跑完 7 个来源**;然后把"采进来的文章从来没被清洗"这条断链补上:采集入口现在当场清洗,`ttxt doctor` 现在能看出清洗覆盖率,不再可能悄悄退化。

## 2. 事实(全部实测)

| 项 | 实测值 | 证据 |
|---|---|---|
| 已装栈版本 | scrapling 0.5.1 / curl-cffi 0.17.0 / patchright 1.64.4 / playwright 1.65.1 / browserforge 1.2.4 / lxml 6.1.3 | `probe_stack.py` 输出、`ttxt doctor` 的 `crawler.stack.import` |
| 未装(本项目自己实现) | camoufox、httpx、bs4、robots-parser、feedparser | 同上 |
| Scrapling 参数词表 | 取自 `scrapling.engines._browsers._types.__annotations__`:`StealthSession` 38 键、`GetRequestParams` 19 键。**不能**用 `get_type_hints`(0.5.1 上会炸) | `probe_stack.py` |
| robots | 7 个启用正式来源全部允许(只认 `User-Agent: *`) | `smoke_net.json` |
| RSS HTTP | 全部 200 | `smoke_net.json`、`crawl_*.log` |
| 浏览器渲染 | 系统 Edge(`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`)headless,头条频道页 383 KB / 3.74 s,profile 当场涨 16 MB | `smoke_net.json` |
| 头条端到端抽取 | 列表页 15 条链接 → 2 篇正文(7465 字、4304 字,两篇都判 `full`) | `probe_toutiao.json`、`crawl_toutiao_tech.log` |
| 真实采集(恢复后) | 7 个来源各跑一次,库内总计 **328 行**:demo 45 / finance 97 / health 25 / history 0 / social 0 / technology 161 | `pre_crawl_state.json`、`post_crawl_state.json`、`crawl_eastmoney_rss.log`、`crawl_ifanr_feed.log`、`crawl_ruanyifeng_atom.log`、`crawl_sspai_rss.log`、`crawl_tmtpost_rss.log`、`crawl_toutiao_tech.log`、`crawl_who_cn_news.log` |
| 清洗覆盖率(C-4 之前) | **43 / 328 = 13%** 的行有 `cleaned_content` | `pre_clean_proof.json` |
| 手工全量清洗耗时 | 6 个命名空间 328 行,**4.3 秒** | `clean_run.log`、`post_clean_proof.json` |

## 3. 问题与根因(C-4)

**现象**:面板和分析拿不到清洗文本、聚类、`text_length`,看起来"数据在但不聪明"。审计口径曾把这件事记成"95.28% 从未清洗,需要让清洗跑得快一点"。

**根因(不是性能)**:采集入口 `ttxt/crawler/runner.py` 的 `_execute_job` **从来没有调用清洗管线**。`clean` 只有人(或 `ttxt clean`)手动跑,所以每采一批就落后一批,越积越多。实测证据:全量清洗只用 4.3 秒 —— 慢不是原因,"没人叫它"才是原因。

**顺带查出来的事实**:清洗相关列(`cleaned_content, text_length, simhash, cluster_id, cleaned_at, clean_version`)不在 `init_articles_db` 的基础建表语句里,是 `store.migrate_articles_db()` 加的,而只有 `pipeline.run_pipeline` 会调它。所以**从未清洗过的命名空间连这些列都不存在**(`PRAGMA table_info(articles)` 里没有 `cleaned_content`),不是"列在但值为 NULL"。这一点直接决定了 doctor 新检查的写法。

## 4. 修改(8 改 + 3 新增,`+144 / -7`;新文件 221 / 207 / 210 行)

| 文件 | 改了什么 |
|---|---|
| `ttxt/crawler/runner.py` | 采集插入之后、`summary["ok"] = True` 之前,当场 `pipeline.run_pipeline(target_category)`;统计进 `summary["pipeline"]`;管线失败只记 `summary["pipeline_error"]` + `LOG.error("crawl.pipeline_failed")`,**不得让派生字段落后反过来把采集判成失败** |
| `ttxt/doctor.py` | 新增 `_cleaned_coverage_row()` 并在 `completeness_checks()` 末尾接入(整段用 try/except 包住:覆盖率统计失败不得毁掉整段完整性体检)。四种结论:完全没有清洗列 / 库里没有文章 / 覆盖率落后(逐个命名空间列 `名称 已清洗/行数`)/ 全覆盖 PASS;三种 WARN 都给补跑命令 |
| `ttxt/crawler/store.py` | `migrate_articles_db` 不再信任 `init_articles_db` 的返回值:拿到非路径就 `TypeError`(见 §6 事故二) |
| `ttxt/crawler/fetcher.py` | 浏览器磁盘缓存在**源头**封顶:`--disk-cache-size=…`(`DEFAULT_DISK_CACHE_MB = 64`,配 `browser.disk_cache_mb` 覆盖,`<=0` 不加旗标),保留原 `extra_flags` |
| `00_control/config.json` | `browser.disk_cache_mb = 64` |
| `ttxt/crawler/stackcheck.py`(**新**) | 第三方栈体检:`crawler.stack.import / api_drift / browser / sources / browser_disk / notes`。浏览器实参不在词表 → **FAIL**;词表取不到 → WARN;有浏览器来源但可执行文件缺失 → **FAIL**;不删文件、不联网 |
| `10_tests/test_crawler_stackcheck.py`(**新**,15 条) | 版本读取、词表命中/漂移、缺可执行文件、profile 体积、notes 如实 |
| `10_tests/test_c4_clean_coverage.py`(**新**,8 条) | 见 §5 |
| `10_tests/conftest.py` | 新增 session 级夹具 `session_categories_dir`:把 `cfg.CATEGORIES_DIR` 与 `ttxt_db.CATEGORIES_DIR` 默认重定向到 `07_tmp` 下的空目录,挂在 `production_state_untouched` 依赖上;docstring 补第 6 条 |
| `10_tests/test_p12_worker_lease.py` | 两处 `init_articles_db` 桩不再返回 `None`,改返回 `tmp_path / "<category>.articles.db"` |
| `10_tests/test_p1_smoke.py` | `test_category_directories` 显式对着 `config.PROJECT_ROOT / "01_categories"` 断言(它测的是仓库真实布局,不该跟着测试会话的重定向落空) |

关键 diff(完整 diff 用 `git diff` 看,HEAD 见 §7):

```diff
+        # C-4:采集与清洗必须是一条链。实测根因:恢复真实采集后 328 行里只有 43 行有
+        # 清洗文本(13%),不是因为清洗跑不动(6 个命名空间 328 行实测 4.3 秒),而是
+        # 采集入口从来没调用清洗管线,派生字段(cleaned_content/text_length/simhash/
+        # cluster_id)长期落后于采集,下游取不到清洗文本还看不出为什么。
+        # 管线失败绝不能吞掉已经落库的文章:单独记 pipeline_error,采集结论保持 ok。
+        try:
+            stats = pipeline.run_pipeline(target_category)
+            summary["pipeline"] = {key: stats.get(key) for key in
+                ("cleaned","dates_normalized","clusters","near_duplicate_pairs","clean_version")}
+        except Exception as exc:  # noqa: BLE001 - 派生字段落后不能反过来让采集判失败
+            summary["pipeline_error"] = f"{type(exc).__name__}: {exc}"
+            LOG.error("crawl.pipeline_failed", source=source.id, category=target_category,
+                      error=summary["pipeline_error"])
         summary["ok"] = True
```

```diff
     target = init_articles_db(category, db_path=db_path)
+    # 实测教训(P15/C-4):测试里把 init_articles_db 打桩成返回 None,迁移就拿着 None
+    # 去 connect,sqlite 把 str(None) 当相对路径,于是仓库根出现一个名为 ``None`` 的库文件。
+    # 路径没解析出来就必须当场失败,绝不允许"就地造出一个谁都没要的库"。
+    if not isinstance(target, (str, Path)):
+        raise TypeError(f"init_articles_db({category!r}) 没有返回数据库路径(得到 {target!r}),拒绝继续迁移")
     con = connect(target)
```

## 5. 测试

- 全量:**278 passed,exit 0**,三种调用形式都过:`pytest` 63.94s / `pytest .` 62.36s / `pytest 10_tests` 50.98s。基线 255 → 278(新增 15 条栈体检 + 8 条 C-4)。
- C-4 单独:`10_tests/test_c4_clean_coverage.py` **8 passed / 1.24s**。
- 8 条覆盖的事实:
  1. 真采集(RSS + 假 fetcher)跑完就有清洗文本、`text_length`、`cluster_id`;
  2. `summary["pipeline"]` 与 `crawl_jobs.meta_json.pipeline` 都带上统计;
  3. 管线故意抛异常 → 采集仍 `ok: true`、文章行保留、`pipeline_error` 有内容、该库仍没有清洗列(不假装清洗过);
  4. 从未清洗的命名空间 → doctor WARN `完全没有清洗列` + 给出 `ttxt clean --category demo`;再真跑一次管线 → doctor PASS `1/1`;
  5. 清洗过之后又绕过入口塞一条 → doctor WARN `demo 1/2`;
  6. 空库 → WARN `库里没有文章`;
  7. `completeness_checks()` 里必须出现 `db.cleaned_coverage`;
  8. `migrate_articles_db` 拿到非路径返回值必须 `TypeError`,且不在当前位置造出计划外的库文件。
- **实现前先失败**的证据(规则 24):第 4/5 条在 `_cleaned_coverage_row()` 只按"列存在"统计时失败,报 `sqlite3.OperationalError: no such column: cleaned_content` 与 `KeyError: 'cleaned_content'` —— 这两条失败暴露的是真实产品事实(从未清洗的库连列都没有),修的是 doctor 的判定口径,不是把断言调松。
- 如实记录一处测试自己的 bug:第 1 条一开始 `ok=False`,原因是测试假对象 `FakeRules` 缺 `unknown` 属性(runner 需要),与实现无关;补 `unknown = False` 后通过。当时因为 pytest 会把长 dict 截断成 `...`,临时用 `-s` + `print` 打印 `summary` 才定位到,打印语句已删除。

## 6. 两次生产事故(如实披露)

**事故一(测试写法错,写到了生产 `demo` 库)**
`test_cleaned_coverage_flags_never_cleaned_namespace` 里为了"恢复被 patch 掉的 `run_pipeline`"用了 `monkeypatch.undo()`。`undo()` 会把**所有**补丁一起撤掉,包括把 `cfg.CATEGORIES_DIR` 指向临时目录的那一个;紧接着 `pipeline.run_pipeline("demo")` 就落到生产 `01_categories/demo/articles.db`。会话结束时的指纹护栏当场抓到:`assert not ['01_categories/demo/articles.db']`。
补法:只重新 `setattr` 那一个函数,绝不用 `undo()`(已在测试里写注释,并写进规则 31)。
影响面:只有清洗派生列被幂等重写。用字节级原像逐列比对证明采集侧没动:
`clean_proof.py compare post_clean_proof.json post_accident_proof.json` → `compare: OK 采集侧逐行不变`,`added: []`、`immutable_broken: []`、`rows_lost: []`、`published_at_normalized: 0`(全部命名空间)。

**事故二(测试隔离有系统性漏洞 + 仓库根多出一个库文件)**
把管线串进采集入口之后,全量套件变红:`1 failed, 276 passed, 1 error`,报的是
`以下测试打开过生产数据库:['D:/ttxt/01_categories/demo/articles.db']`。
定位(先按"不是新测试文件"排除:`--deselect 10_tests/test_c4_clean_coverage.py` 仍然红)后确认:老测试 `10_tests/test_p12_crawler_hardening.py`、`10_tests/test_p12_worker_lease.py` 只 patch 了 system.db 和 `insert_items`,**没有** patch 类目根;管线一接上,`demo` 就解析到生产库。
同一个跑里还有第二个副作用:`test_p12_worker_lease.py` 把 `store.init_articles_db` 打桩成返回 `None`,于是 `migrate_articles_db` 拿着 `None` 去 `sqlite3.connect(str(None))`,在**仓库根**造出一个 4096 字节的名为 `None` 的库文件(`2026-11-15 10:33:44`)。
补法(三条,都是系统性而不是逐个堵):①conftest 新增 session 级夹具,`cfg.CATEGORIES_DIR` / `ttxt_db.CATEGORIES_DIR` 默认就在 `07_tmp` 下,生产类目库默认不可达;②两处桩改成返回 tmp 下真实路径;③产品侧 `migrate_articles_db` 对非路径返回值当场 `TypeError`。那个 `None` 文件**没有删除**,已挪到 `07_tmp/p15_crawler_verify_20261115/artifact_None_from_test_stub.db` 作为证据,仓库根已干净(`Test-Path D:\ttxt\None` → False)。
补完之后三种调用形式全部 `278 passed / exit 0`,会话护栏(生产指纹、连接记录、`07_tmp` 之外连接)全部通过;跑完套件再取证:`clean_proof.py compare post_c4_crawl_proof.json post_suite_proof.json` → `compare: OK`。

## 7. 前后对比

| 指标 | 之前 | 之后 |
|---|---|---|
| `db.cleaned_coverage` | 不存在这个检查;实测 43 / 328(13%) | **PASS:328/328 行全部有清洗文本** |
| `ttxt doctor` 汇总 | 无栈体检 | **summary: 25 ok, 0 failed**;`crawler.stack.*` 六行全 PASS(`stealth_session_keys=38, get_keys=19`;`浏览器来源=1;磁盘缓存旗标=有`;`启用正式来源=7`;profile 547MB / 限额 3.0GB) |
| 采集 → 清洗 | 采集完就结束,清洗要人喊 | 一条链:`python -m ttxt crawl --source ruanyifeng_atom` → `ok: true`,pages_fetched 1,items_extracted 3,inserted 0,duplicates 3,elapsed_s 3.24,`pipeline {cleaned: 161, dates_normalized: 0, clusters: 161, near_duplicate_pairs: 0, clean_version: p3-v1}`(证据 `crawl_ruanyifeng_atom_after_c4.log`) |
| 浏览器磁盘缓存 | 无上限,靠 housekeep 事后删 | 源头 `--disk-cache-size=64MB` |
| 测试对生产类目库 | 靠每个测试自己记得 patch | 会话级默认不可达,护栏 + 278 passed 验证 |
| 数据总量 | 328 行(采集后) | 328 行,清洗与聚类全覆盖:demo 45/45/45、finance 97/97/97、health 25/25/25、history 0、social 0、technology 161/161/161(`post_suite_proof.json`) |

内容完整性体检(C-1 口径,采集侧,不受清洗影响):demo `full=2 short=43`、finance `full=60 short=37`、health `short=25`、technology `full=111 short=50`、`excerpt` 全为 0。

Git:改动前 HEAD `9dc64d7`(= `origin/main`);本批提交后已推送。回滚快照(只描述,不执行):
`11_backups/P15-pre-crawl-20261115-20261115-101602.zip`(2607.3 KB,采集前)、
`11_backups/P15-pre-clean-20261115-20261115-102005.zip`(2760.5 KB,手工清洗前)、
`11_backups/P15-postC4-pre-crawl-20261115-103253.zip`(3095.7 KB,C-4 实采验证前)。
字节级原像:`pre_clean_proof.json` / `post_clean_proof.json` / `post_accident_proof.json` / `post_c4_crawl_proof.json` / `post_suite_proof.json`;比对工具 `clean_proof.py`(`dump` / `compare`,不可变列 15 个逐行比,`published_at` 单列为"管线可归一化",派生列单列)。

## 8. 剩余风险与未做(需单独批准)

- robots 门与 HTTP 实际出站不同口径:Scrapling 发的是伪装 UA + Google referer,门用的是项目 UA 且只认 `User-Agent: *`。这是已知的、写进代码注释的取舍,不是 bug,但要清楚它偏宽松。
- `excerpt` 档至今 0 行真实数据;400 字阈值是**策略**不是事实。
- `extract.py:55` html 列表页仍不逐条取详情链接,这类条目只按 `content_hash` 去重(老问题,未在本批处理)。
- 每次采集都会对该类目库跑一遍管线,实测 161 行的库整轮 3.24 秒(其中管线约 0.7 秒);类目变大后要看实测,不预先优化。
- 未做/需批准:历史重复行清理(`technology` toutiao ×3、`finance` eastmoney ×2 → finance 有 1 对近似重复、`demo`↔`technology` 13 行)、C-5 可信度分层(328 行全是 `unverified`)、C-6 评审元数据(需要 DDL 批准)、清理仓库根 `pytest-of-yangl\` 遗留目录(pytest 早于 C-8 时留下的,按"不删旧数据"保留)。
- Chromium profile 547MB / 限额 3.0GB(`crawler.stack.browser_disk` PASS),磁盘未见暴涨。
