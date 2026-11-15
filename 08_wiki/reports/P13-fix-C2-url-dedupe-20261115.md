# P13 最小修复批次 C-2(同类目 URL 重复插入)2026-11-15

状态:**已实现、已实测、已推送**。本文档只追加,不改写历史报告。
基线时刻:2026-11-15 07:24(本地)。Git 基线:`4bc97429d52ce4e3d0ec89fd267e98e823c37042`(= `origin/main`)。

## 0. 批次边界与"怎么算完成"

只做 C-2。验收标准(逐条实测,见下文):

1. 同一个类目库内,同一 URL 不得产生第二行(含"正文被站点改写"的场景)。
2. 重复必须如实记为 `duplicate`,不得谎报 `inserted`。
3. 并发写入器不得双双插入(不是"多一次 SELECT"就算修好)。
4. 既有文章、schema、历史日志不得被改动;不做删改回填。
5. 不触碰 C-3(跨类目归属)与 C-1(正文完整性判定)。
6. 先写测试再改实现;跑相关测试 + 全量 `10_tests`;出具事实报告与回滚位置。

批次内**未**执行:crawl / Worker / Scheduler / recover / pipeline / analyze / write;生产库只读打开(`mode=ro`)。

## 1. 前置核对(只读,实际输出)

| 项 | 改动前 |
|---|---|
| `git rev-parse HEAD` | `4bc9742`(docs: P13 C-8 最终验收段) |
| `git status --porcelain` | 空 |
| `origin/main` | 与 HEAD 相同(不落后不超前) |
| 生产文章库 | technology 1794048 B / finance 1265664 B / health 286720 B / social 118784 B / history 118784 B / demo 397312 B;`00_control/system.db` 454656 B;全部无内容变化(见 §7) |
| 备份 | `11_backups/` 既有 8 份,未增未删 |
| 环境 | Python 3.12.10 venv `D:\ttxt\.venv`;`pytest 9.2.0`;无 `pytest.ini/pyproject/setup.cfg`,唯一 conftest 为 `10_tests/conftest.py` |

## 2. 根因(读代码 + 查库得到,不是推测)

| 证据 | 位置 | 事实 |
|---|---|---|
| URL 无唯一约束 | [ttxt/crawler/store.py:37-38](../../ttxt/crawler/store.py) | 仅 `CREATE UNIQUE INDEX idx_articles_content_hash`;`idx_articles_url_norm` 是普通索引 |
| 插入前不做 URL 检查 | `store.insert_items` 改前循环(现 [store.py:293](../../ttxt/crawler/store.py) 的 `INSERT OR IGNORE`) | `OR IGNORE` 只对 `content_hash` 生效;正文一改 → 同 URL 再插一行 |
| `content_hash` 覆盖标题+正文 | `content_hash(item.title, item.content)` | 站点改标题即绕过去重 |
| 事务边界 | 改前默认隐式事务 | Python `isolation_level=""` → DEFERRED:读不加写锁,"查→插"之间有 TOCTOU 窗口 |
| 计数谎报 | `00_control/system.db → crawl_jobs.meta_json` | technology 28 任务 inserted 合计 137 = 库内实际 137 行 → 那 4 条 URL 重复行当年全被记成 `inserted`。另:demo{9 任务,91,45,46}、finance{6,140,50,90}、health{3,75,25,50} |
| 列表页 URL 形态 | [ttxt/crawler/extract.py:55](../../ttxt/crawler/extract.py)、[:91](../../ttxt/crawler/extract.py) | html 抽取器把**来源页地址**赋给每一条;RSS 缺 link 时回退到它 |
| 唯一写入点 | [ttxt/crawler/runner.py:247](../../ttxt/crawler/runner.py) | `articles` 表全包只有 `store.py:293` 一处 INSERT;`analysis/rag.py:29` 只写 FTS |

生产重复实据(只读查询,快照 `07_tmp/c2_20261115/dup_pre.json`):

- technology:`https://www.toutiao.com/article/7486434702922531379/` **3 行**(id 253/261/268,同标题、3 个不同 hash,抓于 02:27 / 02:33 / 02:39 → 三次抓取三次插入)。
- finance:`http://finance.eastmoney.com/news/1345,202611123929972290.html` **2 行**(id 1/65,标题被站点改过)。
- demo:`https://quotes.toscrape.com/` **30 行**(列表页形态,URL 不是文章身份 → 属合法数据,必须保留)。

结论:**URL 身份从未被强制执行**;去重只覆盖"正文完全未变"的情形。

## 3. 改动清单(2 文件,+55/−3)+ 完整 diff

无新表/新列/新索引/无迁移;`clean.normalize_url` 未改(只去跟踪参数,不剥离可能区分文章的参数)。

```diff
diff --git a/ttxt/crawler/runner.py b/ttxt/crawler/runner.py
index fa776ee..b864acc 100644
--- a/ttxt/crawler/runner.py
+++ b/ttxt/crawler/runner.py
@@ -242,7 +242,9 @@ def _execute_job(
         summary["feed_total"] = len(items)
         items = items[: max(1, int(source.max_articles))]
     summary["items_extracted"] = len(items)
-        counts = store.insert_items(target_category, source.id, items)
+        # C-2:来源页/订阅地址不是文章身份(html 列表页把它的 URL 赋给每一条),
+        # 必须显式告知去重判定,否则列表页正文会被按 URL 误判为重复。
+        counts = store.insert_items(target_category, source.id, items, page_url=source.url)
         summary.update(counts)
         summary["ok"] = True
diff --git a/ttxt/crawler/store.py b/ttxt/crawler/store.py
index 3b41a9a..04f6eb2 100644
--- a/ttxt/crawler/store.py
+++ b/ttxt/crawler/store.py
@@ -237,15 +237,57 @@ def insert_items(
     source_id: str,
     items: list[ExtractedItem],
     db_path: Path | None = None,
+    page_url: str | None = None,
 ) -> dict[str, int]:
+    """一批条目写入同一个类目库;URL 文章身份 + content_hash 双重去重,整批一个写事务。
+
+    C-2(2026-11-15)判定与理由:
+
+    - 唯一约束只有 ``idx_articles_content_hash``(:37),正文一改就被当成新文章;
+      ``idx_articles_url_norm``(:38)是普通索引,不阻止同 URL 重复。所以 URL 身份
+      必须在插入前显式检查,不能指望索引。
+    - 检查与插入必须处于同一个写事务。``connect()``(``ttxt/db.py:94``)是 WAL +
+      ``busy_timeout=30000``,Python 默认隐式事务是 DEFERRED:读不加写锁,
+      "查 URL → 插入"之间存在 TOCTOU 窗口,两个并发任务会双双插入。这里用
+      ``BEGIN IMMEDIATE`` 先取写锁(与 ``model_gate/queue.py:33`` 同一做法),
+      并发者要么等锁,要么在本批提交后看到该行,不会重复插入。
+    - 类目库即类目命名空间:URL 身份按整库判定,与行上的 ``category`` 取值无关。
+    - ``page_url`` 是来源页/订阅地址。html 列表页抽取器把来源页地址赋给每一条
+      (``extract.py:55``),RSS 条目缺 link 时也回退到它(``extract.py:91``)。
+      这种 URL 不是文章身份,按它去重会把列表页正文误判为重复(实测:demo 库
+      ``quotes_toscrape`` 30 行共用 ``https://quotes.toscrape.com/``),因此这类
+      条目仍只按 content_hash 去重。调用方(只有 ``runner``)必须传入来源 URL。
+    - URL 规范化沿用 ``clean.normalize_url``(只去跟踪参数),不额外剥离可能
+      区分文章的查询参数。
+    - 整批一个事务:任一条异常 → 整批回滚,不留半批数据。
+    """
+    # 规范化放在开库之前:坏 URL 直接失败,不会先创建一个空类目库
+    normalized = [normalize_url(item.url) for item in items]
+    non_identity_urls: set[str] = set()
+    if page_url:
+        non_identity_urls.add(normalize_url(page_url))
     target = Path(db_path) if db_path is not None else init_articles_db(category)
     con = connect(target)
     inserted = duplicates = 0
     fetched_at = cfg.now_iso()
+    batch_urls: set[str] = set()  # 本事务内已占用的文章级 URL(批内同 URL 也算重复)
     try:
-        for item in items:
-            url_normalized = normalize_url(item.url)
+        con.execute("BEGIN IMMEDIATE")
+        for item, url_normalized in zip(items, normalized):
             hash_value = content_hash(item.title, item.content)
+            if url_normalized not in non_identity_urls:
+                clash = url_normalized in batch_urls
+                if not clash:
+                    clash = (
+                        con.execute(
+                            "SELECT 1 FROM articles WHERE url_normalized = ? LIMIT 1",
+                            (url_normalized,),
+                        ).fetchone()
+                        is not None
+                    )
+                if clash:
+                    duplicates += 1
+                    continue
             cursor = con.execute(
                 """
                 INSERT OR IGNORE INTO articles(
@@ -274,9 +316,17 @@
             )
             if cursor.rowcount:
                 inserted += 1
+                batch_urls.add(url_normalized)
             else:
                 duplicates += 1
         con.commit()
+    except BaseException:
+        # 半批数据不得留下:整批回滚后原异常继续上抛,由 runner 记 failed 任务
+        try:
+            con.rollback()
+        except sqlite3.Error:
+            pass
+        raise
     finally:
         con.close()
     return {"inserted": inserted, "duplicates": duplicates}
```

字节级 patch:`07_tmp/c2_20261115/c2_diff.patch`(4890 B,UTF-8)。

**为什么不加唯一索引**:加 `UNIQUE(url_normalized)` 需要 DDL/迁移,且会直接拒掉 demo 那 30 行合法列表页数据(或要求先清理历史行,属删改)。当前令禁止 DDL 与数据清理,故采用"写锁事务内的显式 URL 检查 + `page_url` 语义豁免",效果等价且零 schema 变更。

## 4. 测试证据(先写测试,后改实现)

新增 [10_tests/test_c2_url_dedupe.py](../../10_tests/test_c2_url_dedupe.py)(527 行 / 16 个测试;文件头 docstring 记录生产证据与三条判定规则)。

| 步骤 | 命令 | 结果 |
|---|---|---|
| 实现前(证明测试真的能抓出问题) | `pytest 10_tests/test_c2_url_dedupe.py -q` | **10 failed / 6 passed**,1.75s,exit 1 |
| 实现后(同一文件) | 同上 | **16 passed**,1.63s,exit 0 |
| 定向回归 | `pytest test_p2_crawler test_p3_clean test_p9_toutiao test_p11_closed_loop test_p12_crawler_hardening test_p12_db_temp_store test_p4_analysis test_p13_golden_v2 -q` | **84 passed**,6.33s |
| 全量 | `python -m pytest 10_tests -q` | **214 passed**,44.26s,**exit 0**(基线 198 + 新增 16,0 skip / 0 fail) |

实现前失败的 10 项:`same_url_different_content_inserts_only_once`、`third_crawl_still_refused`、`same_batch_same_url_*`、`url_identity_is_scoped_to_the_category_database`、`tracking_param_variants_*`、`page_level_listing_url_items_not_collapsed`(缺 `page_url` 参数)、`mixed_batch_counts_match_database`、`write_lock_covers_check_then_insert`、`two_threads_same_url_only_one_row`、`repeat_rss_job_records_duplicates_not_inserted`。

实现前已通过、实现后仍通过的 6 项 = "必须保持的行为":跨类目库各自保留、同标题不同 URL 不合并、`?page=1` 与 `?page=2` 不合并、`content_hash` 去重仍生效、整批回滚、html 列表页 runner 回归(30 条不塌)。

诚实记录:实现后第一版有 **2 处我自己写错的测试期望**(`mixed_batch` 误以为 `content_hash` 只覆盖正文;`write_lock` 误期待 blocker 的 `UPDATE` 新增一行)。两处均按真实语义**修正测试**,未放宽实现。

## 5. 并发/事务边界实测

- `test_write_lock_covers_check_then_insert`:另一连接先 `BEGIN IMMEDIATE` + `UPDATE articles SET fetched_at = fetched_at WHERE url_normalized = ?` 持锁 → 0.25s 后 `insert_items` **仍未返回**(证明检查确实在写锁内);对方提交后本批返回 `{inserted: 0, duplicates: 1}`,库内 **1 行**(没有第三条)。
- `test_two_threads_same_url_only_one_row`:4 线程 `Barrier` 同 URL 不同正文 → `inserted` 合计 **1**、`duplicates` 合计 **3**、该 URL **1 行**;远未触及 `busy_timeout`。
- `test_batch_failure_rolls_back_entire_batch`:第 2 条 URL 非法(`http://[::1]:bad/x`)→ `ValueError`,库内 **0 行**(整批回滚)。

## 6. 真实数据副本回放(生产库全程 `mode=ro`)

脚本 [07_tmp/c2_fix_probe.py](../../07_tmp/c2_fix_probe.py);副本 `07_tmp/c2_20261115/fixcheck/`(sqlite3 在线 backup,源库只读)。

```
technology 副本 快照:总行数=137 目标URL现有行=3 ids=[253,261,268]
重放同 URL(正文已变)→ counts={'inserted': 0, 'duplicates': 1} 总行数=137→137 目标URL行数=3
新增文章 → counts={'inserted': 1, 'duplicates': 0} 总行数=137→138
并发 4 线程同 URL → inserted合计=1 duplicates合计=3 该URL行数=1
副本上仍存在的历史 URL 重复组(未清理,属预期残留)=[('https://www.toutiao.com/article/7486434702922531379/', 3)]
demo 副本 快照:总行数=45 列表页URL行=30 → 30 条带 page_url 插入 counts={'inserted': 30, 'duplicates': 0} 45→75
```

即:重复被挡住 / 新文章不受影响 / 列表页形态不丢数据。

## 7. 前后核验(`07_tmp/c2_20261115/snapshot_pre.json` vs `snapshot_post.json`)

| 项 | 结果 |
|---|---|
| 7 个生产库 | **sha256 + 字节完全相同**:system `ab3bea4d8bbd38ed…` 454656、technology `35b7f32c3b49396f…` 1794048、finance `a8c2160f8fcd46b6…` 1265664、health `e6277d2c05f8c142…` 286720、social `25799c430cbda403…` 118784、history `d986704ee85285a6…` 118784、demo `377ef994432a6688…` 397312 |
| 日志 | `09_logs/ttxt-20261112.log` 171555 B/863 行、`20261113` 169026/789、`20261114` 53706/206、两个 0 B dashboard 日志:bytes/lines/sha 全部一致 |
| `00_control` 配置 | 4 个文件一致 |
| 锁 | `00_control/worker.lock` 存在(未创建/未删除 Worker)、`00_control/model_gate/gate.lock` 不存在(前后一致) |
| sidecar | 数量 12 → 12;所有 `-wal` 仍 0 B;**`01_categories/technology/articles.db-shm` 与 `01_categories/demo/articles.db-shm` 的 mtime 变为 2026-11-15T08:02:35**(字节仍 32768)。归因:核验用在线 backup 通过 `mode=ro` 连接读取这两个 WAL 库,attach WAL 库会刷新 `-shm`;主库字节未变(见上行)。后续同类核验先复制再读 |
| `07_tmp` | 1973→1643 文件、571756182→492317596 B。逐路径比对:消失 854 条 **100% 位于 `07_tmp/pytest_basetemp/pytest-{0,1,2,4,5,6,7}`**,basetemp 之外 0 条 = 已验收的 C-8 保留规则(`KEEP_BASETEMP_RUNS=10`)在测试运行时回收旧 scratch;同名文件 size/mtime 变化 **0** 条;新增 524 条全在 basetemp 与 `07_tmp/c2_*` 下 |
| 根目录遗留 `pytest-of-yangl/` | 未动(mtime 2026-11-13 10:34) |
| 磁盘 | 未暴涨:项目总占用因 basetemp 回收而**下降**约 79 MB |

## 8. Git

- 改动前:HEAD `4bc9742`,`status --porcelain` 空,`origin/main` 相同。
- 改动后(实现完成时):`M ttxt/crawler/runner.py`、`M ttxt/crawler/store.py`、`?? 10_tests/test_c2_url_dedupe.py`;`git diff --stat` = 2 files changed, 55 insertions(+), 3 deletions(-);numstat:runner.py `3 1`、store.py `52 2`。
- 用户裁定(2026-11-15):`https://github.com/daxiaolonglong/ttxt-factory/` 是自有仓库,**完成后直接推送**。因此本批次提交并推送;`add`/`commit`/`push` 由用户明示授权。`reset/clean/stash/checkout` 仍未使用。
- `.gitignore` 已排除 `07_tmp/ 09_logs/ 11_backups/ 99_temp/ *.db* 资产库/ .venv/ 02_crawler/.browser/ tools/ .secrets/`,推送内容仅代码 + 测试 + Wiki/规则文档。

## 9. 残留风险

1. **历史重复行未清理**(禁删改令下不回填):technology 1 组×3、finance 1 组×2、demo 1 组×30(最后者是合法数据)。[ttxt/analysis/metrics_import.py:76](../../ttxt/analysis/metrics_import.py) 的 `SELECT id FROM articles WHERE url_normalized=? AND category=?` 对前两组仍可能取到多个 id → 需在 C-3/C-1 之后单列一个带批准的数据修复任务。
2. `page_url` 豁免意味着**列表页抽取器没有逐条 link** 的来源仍只按 `content_hash` 去重;站点改写引文时 demo 类来源仍会长行。根因在抽取器(应逐条取详情链接),归入 C-1/C-3 后续。
3. 新的 `insert_items` 调用方若插入文章级 URL 可不传 `page_url`;插入列表页条目**必须**传(docstring 已写明)。当前生产仅 `runner.py:247` 一处。
4. 整批写锁把同一类目库的写入串行化,批越大持锁越久(`busy_timeout=30000` → 等待而非报错)。实测 4 并发远低于阈值。
5. FTS/RAG 不在本事务内(`analysis/rag.py` 单独重建),行为未变。

## 10. 回滚位置与方式(仅说明)

- `07_tmp/c2_20261115/pre_ttxt_crawler_store.py`(19264 B,HEAD 原文件字节)
- `07_tmp/c2_20261115/pre_ttxt_crawler_runner.py`(12522 B)
- `07_tmp/c2_20261115/c2_diff.patch`(4890 B,可反向套用 `git apply -R`)
- 新增测试 `10_tests/test_c2_url_dedupe.py` 本轮新建,无历史内容
- 既有 `11_backups/*.zip`(8 份)未增未改;`git reset/clean/stash/checkout` 未使用
