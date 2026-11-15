# P13 最小修复批次 C-3(类目命名空间跨库重复)2026-11-15

批次: C-3(在 C-2 之上)。范围严格限定在“写入侧跨命名空间互斥 + 体检侧跨库对账 + 归属可见”。
证据目录: `07_tmp/c3_20261115/`(预镜像、`cross_pre.json`、`proof_c3.py`、`proof_c3.json`、`diff_c3.patch`)。

## 0. 批次边界与“怎么算完成”

**做什么**

1. 写入前对其它命名空间做只读存在性判定(`url_normalized` + `content_hash`),别的注册类目已持有的文章不得再落一行(`ttxt/crawler/store.py`)。
2. 少掉的条目如实计数并说明归属(`cross_report` → 任务 `meta_json.cross_db` + `crawl.cross_db_duplicate` 日志,`ttxt/crawler/runner.py`)。
3. 体检侧新增命名空间卫生与跨库交集对账(`ttxt/doctor.py`)。

**明确不做**: 不改 DDL / 不迁移 / 不回填;不清理历史重复行;不恢复真实采集与 Worker/Scheduler;不装 CloakBrowser;不动 Strata;不删任何旧数据。

**怎么算完成(逐条可验证)**

- [x] 先写验收测试,实现前先失败并列出失败项(规则 24)。
- [x] 全套测试绿,且 `pytest`、`pytest .`、`pytest 10_tests` 三种调用形式都绿。
- [x] 真实数据副本上四条判定成立(`proof_c3.py` exit 0)。
- [x] 生产 6 个类目库字节 + sha256 与开工前完全一致。
- [x] `ttxt doctor` 在真实数据上如实报告命名空间卫生与跨库交集。
- [x] 报告 + Wiki + 核心规则同步,commit 并 push。

## 1. 前置核对(只读,实际输出)

先按“只读纪律”把 6 个类目库复制到 `07_tmp/c3_20261115/db_pre/`,在副本上跑逐对交集扫描(`07_tmp/c3_cross_pre.py` → `cross_pre.json`):

| 命名空间 | 行数 | 是否注册 |
| --- | --- | --- |
| demo | 45 | 否(未注册) |
| finance | 50 | 是 |
| health | 25 | 是 |
| history | 0 | 是 |
| social | 0 | 是 |
| technology | 137 | 是 |

- 注册表(`00_control/category_registry.json`)只有 5 个正式类目;磁盘上有 6 个带 `articles.db` 的目录 → `unregistered_namespaces=["demo"]`、`registered_without_db=[]`。
- **跨命名空间交集只有一对**: `demo` ↔ `technology`,`url_hits 13 / hash_hits 13`(`sspai_rss` 10 条 + `ruanyifeng_atom` 3 条)。**注册类目之间交集为 0**。
- 库内重复(C-2 遗留,合法/待清理): `demo` `quotes.toscrape` 列表页形态 30 行(合法)、`finance` eastmoney 同 URL ×2、`technology` toutiao 同 URL ×3。

生产基线(开工前实测,后续 §9 复核):

| 库 | 字节 | sha256 前 8 | `-wal` | `-shm` |
| --- | --- | --- | --- | --- |
| demo | 397312 | 377ef994 | 0 B | 32768 B / fd4c9fda |
| finance | 1265664 | a8c2160f | 0 B | 32768 B / fd4c9fda |
| health | 286720 | e6277d2c | 0 B | 32768 B / fd4c9fda |
| history | 118784 | d986704e | 0 B | 32768 B / fd4c9fda |
| social | 118784 | 25799c43 | 0 B | 32768 B / fd4c9fda |
| technology | 1794048 | 35b7f32c | 0 B | 32768 B / fd4c9fda |

## 2. 根因(读代码 + 查库得到,不是推测)

1. **写入侧从来没有跨命名空间检测**。`store.insert_items` 只在目标库内查 URL/hash(见 `reports/P13-fix-C2-url-dedupe-20261115.md` §2);类目 = 一个 `articles.db` 文件 = 一个命名空间,但“一篇文章只允许属于一个正式类目”这条规则(核心规则 4)没有任何代码执行点。
2. **体检侧从来没有跨库对账**。`doctor` 以前只看单库完整性与 `system.db`,从不比较类目库之间,所以 13 行重复静默存在。
3. **触发路径**: `sspai_rss` / `ruanyifeng_atom` 在 P12 由 `demo=true` 转正为 `technology`(`02_crawler/sources.json` 注释),两个源各跑过一遍全量 → 同一批文章在 `demo` 与 `technology` 各存一份。
4. 结论: 必须同时补“拦”(写入侧)与“发现”(体检侧)。只补写入侧,历史与并发漏网无法发现;只补体检,重复会继续产生。

## 3. 改动清单 + 完整代码 diff

| 文件 | 改动 | 作用 |
| --- | --- | --- |
| [ttxt/crawler/store.py](../../ttxt/crawler/store.py) | +214/−4 | `registered_namespaces` / `namespaces_on_disk` / `namespace_of_db` / `read_only_connect` / `_column_hits` / `cross_namespace_hits` / `_namespace_blockers`;`insert_items` 新增 `cross_report` 出参与跨命名空间拦截 |
| [ttxt/doctor.py](../../ttxt/doctor.py) | +126 | `_namespace_identity` / `_namespace_identities` / `namespace_overlap_rows` / `namespace_checks`;`run_doctor` 新增 “4b. 命名空间卫生与跨库交集” 段 |
| [ttxt/crawler/runner.py](../../ttxt/crawler/runner.py) | +18/−1 | 取 `cross_report`,写进任务 `meta_json.cross_db` 并 `LOG.warn("crawl.cross_db_duplicate")` |
| [conftest.py](../../conftest.py)(新) | +34 | 把 `10_tests/conftest.py` 的配置期钩子前转为 initial conftest 钩子(见 §6) |
| [10_tests/test_c3_cross_category_dedupe.py](../../10_tests/test_c3_cross_category_dedupe.py)(新) | +483 | 13 条验收测试(写入拦截 6 + 归属计数 2 + 命名空间判定 3 + doctor 对账 2) |
| [07_rules/CORE_RULES.md](../../07_rules/CORE_RULES.md) | 规则 25 补充 + 新增 26/27 | 踩坑沉淀 |
| [08_wiki/db_schema.md](../../08_wiki/db_schema.md) §3 | +3 | 命名空间互斥与对账规则 |
| [08_wiki/architecture.md](../../08_wiki/architecture.md) §6 | 重写 | 当前状态 |

完整代码 diff(store / doctor / runner / 根 conftest,`07_tmp/c3_20261115/diff_c3_code.patch`,sha8 `65c3cd20`):

```diff
diff --git a/conftest.py b/conftest.py
new file mode 100644
index 0000000..e404d97
--- /dev/null
+++ b/conftest.py
@@ -0,0 +1,34 @@
+"""仓库根 conftest:把 ``10_tests`` 的 C-8 测试基座挂成 initial conftest 钩子。
+
+为什么需要这个文件(2026-11-15 实测根因):
+
+pytest 只在 **initial conftest**(由命令行参数所在目录及其祖先在配置阶段加载)里调用
+``pytest_configure``。当调用形式是 ``pytest``(无参数)或 ``pytest .`` 时,
+``10_tests/conftest.py`` 要到 collection 阶段才被加载,它的 ``pytest_configure``
+**不会被调用**。实测后果:basetemp 退回 pytest 默认值,而在本项目的受限执行环境里
+``tempfile.gettempdir()`` 解析成仓库根(``C:\\Users\\...\\Temp`` 不可写,候选回落到
+当前目录),于是临时目录落到 ``D:\\ttxt\\pytest-of-*``,C-8 的三重隔离
+(临时目录限定 07_tmp、日志目录重定向、生产库前后指纹比对)同时失效,
+表现为 ``test_p15_test_isolation``/``test_p7_dashboard`` 等互不相干的 FAIL。
+
+这里只前转**配置期**钩子;夹具仍然由 ``10_tests/conftest.py`` 自己注册(它照常作为
+该目录的 conftest 被收集),不会重复注册,也不改变任何测试语义。
+``10_tests/conftest.py`` 自己带 ``--basetemp`` 早退保护,因此两次 ``pytest_configure``
+(例如 ``pytest 10_tests`` 时根与子目录各一次)只会生效一次。
+"""
+
+from __future__ import annotations
+
+import importlib.util
+from pathlib import Path
+
+_HARNESS = Path(__file__).resolve().parent / "10_tests" / "conftest.py"
+
+if _HARNESS.is_file():
+    _spec = importlib.util.spec_from_file_location("_ttxt_harness_conftest", _HARNESS)
+    assert _spec is not None and _spec.loader is not None
+    _harness = importlib.util.module_from_spec(_spec)
+    _spec.loader.exec_module(_harness)
+
+    # 只暴露配置期钩子;其余(session/autouse 夹具)由原 conftest 注册
+    pytest_configure = _harness.pytest_configure
diff --git a/ttxt/crawler/runner.py b/ttxt/crawler/runner.py
index b864acc..00281cf 100644
--- a/ttxt/crawler/runner.py
+++ b/ttxt/crawler/runner.py
@@ -244,8 +244,24 @@ def _execute_job(
         summary["items_extracted"] = len(items)
         # C-2:来源页/订阅地址不是文章身份(html 列表页把它的 URL 赋给每一条),
         # 必须显式告知去重判定,否则列表页正文会被按 URL 误判为重复。
-        counts = store.insert_items(target_category, source.id, items, page_url=source.url)
+        # C-3:cross_report 是写入侧跨库判定的归属回执(出参),用于 meta 与日志。
+        cross_report: dict[str, object] = {}
+        counts = store.insert_items(
+            target_category, source.id, items, page_url=source.url, cross_report=cross_report
+        )
         summary.update(counts)
+        if cross_report.get("blocked") or cross_report.get("scan_errors"):
+            # C-3:少掉的条目不是“正常重复”,要写明被谁持有;没有命中时不加键,
+            # 保持 meta_json 结构不变(dashboard/metrics 不需要为跨库特判)。
+            summary["cross_db"] = cross_report
+            LOG.warn(
+                "crawl.cross_db_duplicate",
+                source=source.id,
+                category=target_category,
+                blocked=cross_report.get("blocked", 0),
+                holders=sorted(cross_report.get("holders", {})),
+                scan_errors=cross_report.get("scan_errors", []),
+            )
         summary["ok"] = True
         # elapsed 必须在落库前算好:否则 meta_json 里永远是初始 0.0,观测谎报
         summary["elapsed_s"] = round(time.monotonic() - started, 2)
diff --git a/ttxt/crawler/store.py b/ttxt/crawler/store.py
index 04f6eb2..9bd033a 100644
--- a/ttxt/crawler/store.py
+++ b/ttxt/crawler/store.py
@@ -10,10 +10,16 @@ from ttxt import config as cfg
 from ttxt.crawler.clean import content_hash, normalize_url
 from ttxt.crawler.extract import ExtractedItem
 from ttxt.db import connect, system_db_path
+from ttxt.logging import get_logger
+
+LOG = get_logger("crawler.store")
 
 SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
 PROCESSOR_VERSION = "p2-v1"
 SCHEMA_VERSION = "p12-v1"
+CROSS_READ_CHUNK = 400
+# 跨库只读查询允许使用的列(白名单,防止把外部字符串拼进 SQL)
+CROSS_ID_COLUMNS = {"url_normalized", "content_hash"}
 
 ARTICLES_SCHEMA = """
 CREATE TABLE IF NOT EXISTS articles (
@@ -216,6 +222,160 @@ def articles_db_path(category: str) -> Path:
     return category_dir(category) / "articles.db"
 
 
+# ------------------------------------------------------------------ C-3 命名空间与跨库只读判定
+
+
+def registered_namespaces() -> set[str]:
+    """注册表(``00_control/category_registry.json``)里的正式类目 slug。
+
+    注册表读不出来时返回空集:任何库都不具备拦截写入的所有权。这个方向是故意的——
+    体检/卫生问题不得让采集停摆(``doctor`` 会把注册表本身读失败报成 FAIL)。
+    """
+    try:
+        registry = cfg.load_registry()
+    except (OSError, ValueError):
+        return set()
+    slugs: set[str] = set()
+    for item in registry.get("categories", []):
+        slug = str(item.get("slug", "")) if isinstance(item, dict) else ""
+        if SLUG_RE.match(slug):
+            slugs.add(slug)
+    return slugs
+
+
+def namespaces_on_disk() -> list[str]:
+    """``01_categories`` 下已经存在 ``articles.db`` 的命名空间目录名(排序)。
+
+    注册表之外的目录(如 ``demo``)也算命名空间,只是它没有数据所有权 —— 见
+    ``_namespace_blockers``。空目录(没有库)不参与。
+    """
+    root = Path(cfg.CATEGORIES_DIR)
+    if not root.exists():
+        return []
+    names: list[str] = []
+    for entry in root.iterdir():
+        if entry.is_dir() and SLUG_RE.match(entry.name) and (entry / "articles.db").is_file():
+            names.append(entry.name)
+    return sorted(names)
+
+
+def namespace_of_db(target: Path) -> str | None:
+    """写入目标是否位于类目命名空间根下(``01_categories/<slug>/articles.db``)。
+
+    C-3 判定:命名空间互斥只在类目命名空间库上生效。显式 ``db_path`` 指向命名空间
+    之外的一次性库(单测夹具、临时分析库)没有命名空间身份,也不参与互斥——
+    它们不在 ``01_categories`` 下,不会污染正式语料。生产写入路径(``runner`` 不传
+    ``db_path``)永远落在命名空间内,因此永远受管。
+    """
+    root = Path(cfg.CATEGORIES_DIR)
+    try:
+        resolved_root = root.resolve()
+        rel = Path(target).resolve().relative_to(resolved_root)
+    except (OSError, ValueError):
+        return None
+    parts = rel.parts
+    if len(parts) == 2 and parts[1] == "articles.db" and SLUG_RE.match(parts[0]):
+        return parts[0]
+    return None
+
+
+def read_only_connect(path: Path) -> sqlite3.Connection:
+    """跨库只读连接:不改 journal_mode、不建表、不跑迁移,只允许读。
+
+    不用 ``file:...?mode=ro`` URI:WAL 库在 ``-wal/-shm`` 边车不存在时只读打开会
+    直接失败(SQLite 必须能创建边车),会把正常库误报成不可用。只读约束由
+    ``PRAGMA query_only=ON`` 保证;副作用仅为 WAL 边车的创建与清理,与项目里其它
+    只读路径(``count_articles`` 等)一致。
+    """
+    con = sqlite3.connect(str(path), timeout=30.0)
+    con.execute("PRAGMA query_only=ON")
+    con.execute("PRAGMA busy_timeout=30000")
+    return con
+
+
+def _column_hits(con: sqlite3.Connection, column: str, values: set[str]) -> list[str]:
+    """在单个库内按列查存在性(IN 分片;绝不跨库 JOIN)。"""
+    if column not in CROSS_ID_COLUMNS:  # 防御:列名只允许白名单
+        raise ValueError(f"unsupported identity column: {column!r}")
+    if not values:
+        return []
+    hits: list[str] = []
+    ordered = sorted(values)
+    for start in range(0, len(ordered), CROSS_READ_CHUNK):
+        chunk = ordered[start : start + CROSS_READ_CHUNK]
+        placeholders = ",".join("?" * len(chunk))
+        rows = con.execute(
+            f"SELECT DISTINCT {column} AS value FROM articles WHERE {column} IN ({placeholders})",
+            chunk,
+        )
+        hits.extend(str(row[0]) for row in rows)
+    return hits
+
+
+def cross_namespace_hits(
+    urls: set[str], hashes: set[str], exclude_path: Path | None = None
+) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
+    """只读扫描其它命名空间是否已经持有这些 URL / content_hash。
+
+    返回 ``({命名空间: {"urls": [...], "hashes": [...]}}, 扫描失败的命名空间说明)``。
+    这是写入侧的**只读存在性查询**,不是跨库 JOIN,也不把不同类目库合并成检索
+    上下文(``08_wiki/db_schema.md`` §3 的类目物理隔离仍然成立)。
+    """
+    holders: dict[str, dict[str, list[str]]] = {}
+    failures: list[str] = []
+    if not urls and not hashes:
+        return holders, failures
+    exclude_resolved = None
+    if exclude_path is not None:
+        try:
+            exclude_resolved = Path(exclude_path).resolve()
+        except OSError:
+            exclude_resolved = None
+    for name in namespaces_on_disk():
+        path = articles_db_path(name)
+        if exclude_resolved is not None:
+            try:
+                if path.resolve() == exclude_resolved:
+                    continue
+            except OSError:
+                continue
+        try:
+            con = read_only_connect(path)
+        except sqlite3.Error as exc:
+            failures.append(f"{name}: {type(exc).__name__}: {exc}")
+            continue
+        try:
+            url_hits = _column_hits(con, "url_normalized", urls)
+            hash_hits = _column_hits(con, "content_hash", hashes)
+        except sqlite3.Error as exc:
+            failures.append(f"{name}: {type(exc).__name__}: {exc}")
+            url_hits = hash_hits = []
+        finally:
+            con.close()
+        if url_hits or hash_hits:
+            holders[name] = {"urls": url_hits, "hashes": hash_hits}
+    return holders, failures
+
+
+def _namespace_blockers(holders: dict[str, dict[str, list[str]]]) -> tuple[set[str], set[str]]:
+    """哪些命中真的拦截写入:只有**注册类目**持有才算所有权。
+
+    未注册命名空间(``demo`` 这类测试命名空间)没有数据所有权:它不得拦下正式类目的
+    采集,否则测试数据会饿死正式语料(生产实测 ``sspai_rss``/``ruanyifeng_atom`` 在
+    P12 由 ``demo=true`` 转正,13 篇早期落在 demo 库,若 demo 有拦截权,这 13 篇
+    永远进不了 technology 库)。反过来,注册类目持有的文章不得再复制进 demo 库。
+    """
+    registered = registered_namespaces()
+    blocked_urls: set[str] = set()
+    blocked_hashes: set[str] = set()
+    for name, hit in holders.items():
+        if name not in registered:
+            continue
+        blocked_urls.update(hit["urls"])
+        blocked_hashes.update(hit["hashes"])
+    return blocked_urls, blocked_hashes
+
+
 def init_articles_db(category: str, db_path: Path | None = None) -> Path:
     target = Path(db_path) if db_path is not None else articles_db_path(category)
     target.parent.mkdir(parents=True, exist_ok=True)
@@ -238,6 +398,7 @@ def insert_items(
     items: list[ExtractedItem],
     db_path: Path | None = None,
     page_url: str | None = None,
+    cross_report: dict[str, object] | None = None,
 ) -> dict[str, int]:
     """一批条目写入同一个类目库;URL 文章身份 + content_hash 双重去重,整批一个写事务。
 
@@ -260,21 +421,66 @@ def insert_items(
     - URL 规范化沿用 ``clean.normalize_url``(只去跟踪参数),不额外剥离可能
       区分文章的查询参数。
     - 整批一个事务:任一条异常 → 整批回滚,不留半批数据。
+
+    C-3(2026-11-15)追加:类目命名空间之间互斥(一篇正式文章只允许属于一个正式类目)。
+
+    - 实测(``07_tmp/c3_20261115/cross_pre.json``):磁盘上有 6 个命名空间库,注册表只有
+      5 个正式类目,``demo`` 未注册;逐对 url/hash 交集只有 ``demo`` ↔ ``technology``
+      一组(13 URL / 13 hash,``sspai_rss`` 10 + ``ruanyifeng_atom`` 3),根因是这两个源
+      P12 由 ``demo=true`` 转正(``02_crawler/sources.json`` 注释),而写入侧从来没有
+      任何跨库检测,体检也没有任何跨库对账,所以复制是静默发生的。
+    - 拦截权只属于**注册类目**:未注册命名空间(``demo``)不得卡正式类目的采集
+      (否则测试语料会饿死正式语料);注册类目持有的文章不得再写进 ``demo``。
+    - 跨库判定是只读存在性查询(``query_only``,不改对方库、不建库、不跨库 JOIN),
+      且只在写入目标确实位于 ``01_categories/<slug>/articles.db`` 时执行 ——
+      见 ``namespace_of_db``。
+    - 计数仍然诚实:跨库少掉的条目计入 ``duplicates``,绝不静默丢弃。归属写进
+      ``cross_report``(``{"blocked": n, "holders": {命名空间: {urls, hashes}}}``),
+      由 ``runner`` 放进任务 ``meta_json`` 并写日志,运维能看到“为什么少了一条、
+      那条属于谁”。
+    - 已知残留风险(诚实记录):跨库检查与目标库写事务不是同一个原子域,
+      两个类目同时采同一篇文章仍可能双双落库;兜底是 ``doctor`` 的
+      ``db.cross_overlap`` 对账(``ttxt doctor`` 会 FAIL),而不是假装跨文件原子。
     """
     # 规范化放在开库之前:坏 URL 直接失败,不会先创建一个空类目库
     normalized = [normalize_url(item.url) for item in items]
+    hashes = [content_hash(item.title, item.content) for item in items]
     non_identity_urls: set[str] = set()
     if page_url:
         non_identity_urls.add(normalize_url(page_url))
     target = Path(db_path) if db_path is not None else init_articles_db(category)
+    # 跨库只读扫描:开写事务之前做完,避免在持锁期间读别人的库
+    blocked_urls: set[str] = set()
+    blocked_hashes: set[str] = set()
+    holders: dict[str, dict[str, list[str]]] = {}
+    scan_failures: list[str] = []
+    if namespace_of_db(target) is not None:
+        article_urls = {value for value in normalized if value not in non_identity_urls}
+        holders, scan_failures = cross_namespace_hits(article_urls, set(hashes), exclude_path=target)
+        blocked_urls, blocked_hashes = _namespace_blockers(holders)
+    if cross_report is not None:
+        cross_report["blocked"] = 0
+        cross_report["holders"] = {
+            name: {"urls": sorted(hit["urls"]), "hashes": sorted(hit["hashes"])}
+            for name, hit in holders.items()
+        }
+        if scan_failures:
+            cross_report["scan_errors"] = list(scan_failures)
+    for failure in scan_failures:
+        # 卫生检查不能把采集打停:发现失败要喊出来,由 doctor 的跨库对账兜底
+        LOG.warn("insert.cross_db_scan_failed", category=category, detail=failure)
     con = connect(target)
-    inserted = duplicates = 0
+    inserted = duplicates = cross_blocked = 0
     fetched_at = cfg.now_iso()
     batch_urls: set[str] = set()  # 本事务内已占用的文章级 URL(批内同 URL 也算重复)
     try:
         con.execute("BEGIN IMMEDIATE")
-        for item, url_normalized in zip(items, normalized):
-            hash_value = content_hash(item.title, item.content)
+        for item, url_normalized, hash_value in zip(items, normalized, hashes):
+            if url_normalized in blocked_urls or hash_value in blocked_hashes:
+                # 别的注册类目已经持有这篇文章:本类目不得再存一行
+                cross_blocked += 1
+                duplicates += 1
+                continue
             if url_normalized not in non_identity_urls:
                 clash = url_normalized in batch_urls
                 if not clash:
@@ -329,6 +535,8 @@ def insert_items(
         raise
     finally:
         con.close()
+    if cross_report is not None:
+        cross_report["blocked"] = cross_blocked
     return {"inserted": inserted, "duplicates": duplicates}
 
 
diff --git a/ttxt/doctor.py b/ttxt/doctor.py
index 0011235..37008ea 100644
--- a/ttxt/doctor.py
+++ b/ttxt/doctor.py
@@ -10,6 +10,126 @@ from typing import Any
 
 from . import config as cfg
 from . import db, housekeep
+from .crawler import store
+
+
+def _namespace_identity(name: str) -> tuple[set[str], set[str]]:
+    """只读取一个命名空间的 url_normalized / content_hash 全集(不写、不建、不改)。"""
+    path = store.articles_db_path(name)
+    con = store.read_only_connect(path)
+    try:
+        urls = {str(row[0]) for row in con.execute("SELECT DISTINCT url_normalized FROM articles")}
+        hashes = {str(row[0]) for row in con.execute("SELECT DISTINCT content_hash FROM articles")}
+    finally:
+        con.close()
+    return urls, hashes
+
+
+def _namespace_identities() -> tuple[dict[str, tuple[set[str], set[str]]], dict[str, str]]:
+    """读出每个命名空间的身份集合;读失败的命名空间单独报告,不毁掉整项体检。"""
+    identities: dict[str, tuple[set[str], set[str]]] = {}
+    errors: dict[str, str] = {}
+    for name in store.namespaces_on_disk():
+        try:
+            identities[name] = _namespace_identity(name)
+        except Exception as exc:  # noqa: BLE001
+            errors[name] = f"{type(exc).__name__}: {exc}"
+    return identities, errors
+
+
+def namespace_overlap_rows(
+    limit_per_pair: int = 20, identities: dict[str, tuple[set[str], set[str]]] | None = None
+) -> list[dict[str, Any]]:
+    """逐对命名空间的 url/hash 交集(C-3 对账;只读)。
+
+    实测语料规模(千行级)下内存交足够;行数上万后应换成 SQL 分片或落一张对账表,
+    不在这里做跨库 JOIN —— 那会破坏 ``08_wiki/db_schema.md`` §3 的类目物理隔离。
+
+    只返回**有交集**的对;每对含 ``left/right/url_hits/hash_hits/registered_pair``
+    以及样本(``url_samples/hash_samples``),便于人工核对。
+    """
+    names = store.namespaces_on_disk()
+    registered = store.registered_namespaces()
+    if identities is None:
+        identities, _scan_errors = _namespace_identities()
+    rows: list[dict[str, Any]] = []
+    ordered = [name for name in names if name in identities]
+    for index, left in enumerate(ordered):
+        for right in ordered[index + 1 :]:
+            left_urls, left_hashes = identities[left]
+            right_urls, right_hashes = identities[right]
+            url_hits = left_urls & right_urls
+            hash_hits = left_hashes & right_hashes
+            if not url_hits and not hash_hits:
+                continue
+            rows.append(
+                {
+                    "left": left,
+                    "right": right,
+                    "url_hits": len(url_hits),
+                    "hash_hits": len(hash_hits),
+                    "registered_pair": left in registered and right in registered,
+                    "url_samples": sorted(url_hits)[:limit_per_pair],
+                    "hash_samples": sorted(hash_hits)[:limit_per_pair],
+                }
+            )
+    return rows
+
+
+def namespace_checks() -> list[tuple[str, str, str]]:
+    """命名空间卫生 + 跨库交集体检(C-3)。返回 ``(level, name, detail)``。
+
+    - ``namespaces.disk``:磁盘上有库但未登记进注册表(如 ``demo``)→ WARN。
+      测试命名空间不是正式语料,不能与正式类目平起平坐,必须显式可见。
+    - ``db.cross_overlap``:两个**注册类目**持有同一篇文章 → FAIL(违反类目隔离);
+      只涉及未注册命名空间的交集 → WARN(历史遗留,须清理但不阻断采集)。
+    """
+    results: list[tuple[str, str, str]] = []
+    registered = store.registered_namespaces()
+    on_disk = store.namespaces_on_disk()
+    unregistered = sorted(set(on_disk) - registered)
+    missing_dbs = sorted(registered - set(on_disk))
+    _identities, scan_errors = _namespace_identities()
+    if scan_errors:
+        detail = "; ".join(f"{name}: {message}" for name, message in sorted(scan_errors.items()))
+        results.append(("WARN", "db.cross_scan", f"命名空间库读失败,交集判定不完整: {detail}"))
+    results.append(
+        (
+            "PASS" if registered else "FAIL",
+            "namespaces.registered",
+            f"registered={sorted(registered)}",
+        )
+    )
+    if unregistered:
+        results.append(
+            (
+                "WARN",
+                "namespaces.disk",
+                f"磁盘上有库但未注册: {unregistered};注册类目缺库: {missing_dbs or '无'}",
+            )
+        )
+    else:
+        detail = "all on-disk namespaces are registered"
+        if missing_dbs:
+            detail = f"{detail}; registered namespaces without articles.db: {missing_dbs}"
+        results.append(("PASS", "namespaces.disk", detail))
+
+    rows = namespace_overlap_rows()
+    hard = [row for row in rows if row["registered_pair"]]
+    soft = [row for row in rows if not row["registered_pair"]]
+    if hard:
+        summary = "; ".join(
+            f"{row['left']}<->{row['right']} url={row['url_hits']} hash={row['hash_hits']}" for row in hard
+        )
+        results.append(("FAIL", "db.cross_overlap", f"正式类目之间存在同一篇文章: {summary}"))
+    elif soft:
+        summary = "; ".join(
+            f"{row['left']}<->{row['right']} url={row['url_hits']} hash={row['hash_hits']}" for row in soft
+        )
+        results.append(("WARN", "db.cross_overlap", f"仅未注册命名空间与正式类目重叠: {summary}"))
+    else:
+        results.append(("PASS", "db.cross_overlap", f"namespaces={on_disk} 两两无交集"))
+    return results
 
 
 def _strata_health(config: dict[str, Any]) -> dict[str, Any]:
@@ -68,6 +188,12 @@ def run_doctor() -> int:
         except Exception as exc:  # noqa: BLE001
             check("db.system", False, str(exc))
 
+    # 4b. 命名空间卫生与跨库交集(C-3:类目物理隔离必须被对账,否则复制是静默的)
+    try:
+        results.extend(namespace_checks())
+    except Exception as exc:  # noqa: BLE001 - 卫生检查异常不得让整份体检崩掉
+        check("namespaces.checks", False, f"{type(exc).__name__}: {exc}")
+
     # 5. 磁盘预算
     report = housekeep.report()
     check(
```

新增测试文件与文档改动见 `07_tmp/c3_20261115/diff_c3.patch`(全量,sha8 `f7e02bb1`)。

## 4. 测试证据(先写测试,后改实现)

**实现前先失败(规则 24 要求的证据)**: 先写 `10_tests/test_c3_cross_category_dedupe.py`,当时跑 → **10 failed / 3 passed**。失败项全部指向“实现里还没有跨命名空间判定”(`insert_items` 无 `cross_report` 参数 → `TypeError`;拦截计数不存在;`doctor` 无 `namespace_checks` / `namespace_overlap_rows`)。3 条通过的是与实现无关的命名空间发现类断言。

**实现后**:

| 命令 | 结果 |
| --- | --- |
| `pytest 10_tests/test_c3_cross_category_dedupe.py -q` | **13 passed in 1.76s** |
| `pytest 10_tests -q` | **227 passed in 44.91s**,exit 0 |
| `pytest -q`(仓库根无参数) | **227 passed in 52.30s** |
| `pytest . -q -k "pytest_temp_root or dashboard_never_opens"` | **2 passed in 10.06s** |

C-2 的 16 条测试([10_tests/test_c2_url_dedupe.py](../../10_tests/test_c2_url_dedupe.py))全程保持绿色,说明 C-3 没有把 C-2 的契约改掉:返回形状仍是 `{"inserted","duplicates"}`,跨库拦下的条目计入 `duplicates`。

## 5. 实现过程中修掉的三处自伤(如实记录)

1. **夹具缺字段导致假红**: `FakeRules` 缺 `unknown`,`runner.py:131` 读它 → `AttributeError` → 任务 failed → 断言 `inserted == 1` 收到 0。修夹具(`unknown = False`、`crawl_delay_s = None`),不改实现语义。
2. **测试期望写错**: 第一条 doctor 测试期望 `finance` ↔ `technology` 交集,但真实数据里所有交集都牵涉未注册的 `demo`(按判定只 WARN)。改成: `demo` 先合法写入 → `technology` 允许写入(因 `demo` 未注册)→ 用 `_legacy_row` 裸 INSERT 模拟“修复之前”的注册类目之间重复 → 断言 FAIL。这是测试期望错,不是实现错。
3. **命名越界**: `doctor` 一开始伸手调用 `store._read_only_connect`(私有名),已改成公开 `store.read_only_connect` 并同步调用点。教训: 跨模块只用公开名。

## 6. 顺带查出并修掉的测试基座缺陷(C-8 隔离在部分调用形式下失效)

**现象**: 第一次跑全套(`pytest -q`,不带路径)得到 `6 failed, 221 passed, 1 error`,断言里出现 `D:/ttxt/pytest-of-yangl/pytest-51/test_dashboard_never_opens_pro0/00_control/system.db` —— 测试库跑到了仓库根,且不在 `07_tmp` 下。

**根因链(逐环实测,不是猜)**:

1. `10_tests/conftest.py` 靠 `pytest_configure` 把 basetemp 钉到 `07_tmp/pytest_basetemp`。
2. pytest 只在 **initial conftest**(由命令行参数所在目录及其祖先在配置阶段加载)里调用 `pytest_configure`。`pytest`(无参数)与 `pytest .` 时,`10_tests/conftest.py` 要到 collection 阶段才被加载 → 它的 `pytest_configure` **不会被调用** → basetemp 退回默认值。
3. 本执行环境里 `tempfile.gettempdir()` 解析成 `D:\ttxt`(实测输出 `gettempdir= D:\ttxt`;用户 `Temp` 在受限沙箱不可写,候选目录回落到当前目录)→ 默认 basetemp 变成 `D:\ttxt\pytest-of-yangl\pytest-N`。
4. 于是 C-8 的三重隔离(临时目录限定 07_tmp、日志目录重定向、生产库前后指纹比对)同时失效,表现为一堆互不相干的 FAIL/ERROR;同一份测试用 `pytest 10_tests` 跑则全绿 —— 这也解释了为什么此前一直“看起来没问题”。

**修法**: 仓库根新增 `conftest.py`,只做一件事 —— 用 `importlib` 载入 `10_tests/conftest.py` 并前转 `pytest_configure`(夹具仍由原 conftest 注册,不重复注册;原 conftest 带“命令行显式 `--basetemp` 优先”的早退保护,所以 `pytest 10_tests` 时两次配置只会生效一次)。

**验证**: 上表三种调用形式全部 227 passed;`-k` 子集 2 passed。

**遗留(未删)**: `D:\ttxt\pytest-of-yangl\`(`pytest-11 / pytest-49 / pytest-50 / pytest-51 / pytest-52`,11/12 起就有,最早的是 C-8 之前留下的)按“不得删除旧数据/pytest 遗留”边界保留,等一次单独批准的清理任务再处理。

## 7. 真实数据副本验证(`07_tmp/c3_20261115/prove_c3.py`,生产库全程不打开)

把 6 个库(`.db` + 边车)复制成 `07_tmp/c3_20261115/db_proof/` 副本,把 `cfg.CATEGORIES_DIR` 指向副本根,然后按真实数据逐条验证:

| 用例 | 期望 | 实测 |
| --- | --- | --- |
| A `technology` 持有的文章写进 `finance` | 拦住 | `{"inserted":0,"duplicates":1}`,`cross_report.blocked=1`,`holders.technology` 列出 `https://www.toutiao.com/article/7705970580805173796/` 与 hash `e032b7b5…` |
| B 只有 `demo` 持有的文章写进 `technology` | 放行(未注册无所有权) | `{"inserted":1,"duplicates":0}`,`blocked=0`,但 `holders.demo` 仍列出该文(可见不拦截) |
| C `technology` 持有的文章写进 `demo` | 拦住(注册类目持有者优先) | `{"inserted":0,"duplicates":1}`,`blocked=1`,`holders.technology` |
| D 对照组:裸 `INSERT` 绕过 `insert_items`(模拟 C-3 之前) | `doctor` 必须 FAIL | `db.cross_overlap` → `FAIL 正式类目之间存在同一篇文章: finance<->technology url=1 hash=1`,`registered_pair=true` |

体检在两个时态的对照:

- 守卫起作用之后(A/C 被拦,没有产生新的正式类目交集): `PASS namespaces.registered` / `WARN namespaces.disk 磁盘上有库但未注册: ['demo']` / `WARN db.cross_overlap 仅未注册命名空间与正式类目重叠: demo<->technology url=14 hash=14`(13 行历史 + B 放行的 1 行)。
- 绕过守卫之后(同一篇文章被塞进 `finance`): 立刻 `FAIL db.cross_overlap finance<->technology url=1 hash=1`。

脚本退出码 **0**,完整输出 `07_tmp/c3_20261115/proof_c3.json`。

## 8. 生产只读体检(`ttxt doctor` 真实输出,改后)

```
[PASS] project.root: D:\ttxt
[PASS] project.dirs: all present
[PASS] config.load: version=1
[PASS] registry.categories: technology,finance,social,history,health
[PASS] paths.within_project: ok
[PASS] db.system: integrity=ok, categories=5
[PASS] namespaces.registered: registered=['finance', 'health', 'history', 'social', 'technology']
[WARN] namespaces.disk: 磁盘上有库但未注册: ['demo'];注册类目缺库: 无
[WARN] db.cross_overlap: 仅未注册命名空间与正式类目重叠: demo<->technology url=13 hash=13
[PASS] disk.free: free=103.8GB min=5.0GB
[PASS] disk.budget: used=1.221GB budget=20.0GB
[PASS] venv.python: D:\ttxt\.venv\Scripts\python.exe
[PASS] strata.health: served_model=qwen3.8-flash-next-iq3_s context=147456 loaded=True
summary: 13 ok, 0 failed
```

真实数据被如实喊出来: 13 行历史交集(牵涉未注册 `demo` → WARN),正式类目之间 0 交集(否则就是 FAIL)。

## 9. 前后核验(生产库全程未改)

体检跑完之后实测 `01_categories/*`:

| 库 | 字节 | sha256 前 8 | 与开工前 |
| --- | --- | --- | --- |
| demo | 397312 | 377ef994 | 一致 |
| finance | 1265664 | a8c2160f | 一致 |
| health | 286720 | e6277d2c | 一致 |
| history | 118784 | d986704e | 一致 |
| social | 118784 | 25799c43 | 一致 |
| technology | 1794048 | 35b7f32c | 一致 |

边车变化(如实说明): `-wal` / `-shm` 现在**不存在**了。原因: 只读打开 WAL 库会创建边车,最后一个连接干净关闭时 SQLite 把**空 WAL** checkpoint 回主库并删除边车;主库字节与 sha 完全未变,所以不是数据改动。已写进规则 25(核验以 `.db` 字节 + sha 为准)。

测试侧另有第二重证明: `10_tests/conftest.py` 的 session 夹具对 7 个生产库、4 个配置文件、`00_control/model_gate`、`09_logs` 全部日志做前后指纹比对,227 passed 的会话里没有任何一项变化;session 记录器也没有任何测试连接过生产库。

## 10. Git

- 开工前 HEAD: `6e3eed8`(`== origin/main`)。
- 本批改动: `ttxt/crawler/store.py`、`ttxt/doctor.py`、`ttxt/crawler/runner.py`、`conftest.py`(新)、`10_tests/test_c3_cross_category_dedupe.py`(新)、`07_rules/CORE_RULES.md`、`08_wiki/db_schema.md`、`08_wiki/architecture.md`。
- Diff 存档: `07_tmp/c3_20261115/diff_c3.patch`(sha8 `f7e02bb1`)、`diff_c3_code.patch`(sha8 `65c3cd20`)。
- 提交与推送结果: 见提交后补记(`git log --oneline`)。

## 11. 残留风险(诚实列出)

1. **跨文件不是原子**: 跨库只读扫描与目标库 `BEGIN IMMEDIATE` 写事务不在同一个原子域。两个类目同时采同一篇文章,仍可能双双落库。兜底是 `doctor` 的 `db.cross_overlap` FAIL 对账(以及任务日志 `crawl.cross_db_duplicate`),不是假装跨文件原子。
2. **历史重复未清理**: `demo`↔`technology` 13 行、`technology` toutiao ×3、`finance` eastmoney ×2 仍在库里(清理属于删除数据,需单独批准)。`demo` 的 `quotes.toscrape` 30 行是列表页形态,合法保留。
3. **`demo` 仍是未注册命名空间**: 只报 WARN。一旦把 `demo` 注册进注册表,`demo`↔`technology` 13 行会立刻变 FAIL —— 届时必须先清理或在清理前接受 FAIL。
4. **每批写入多一轮只读扫描**: 成本约为命名空间数 × 分片查询(每片 400 值)。当前 6 库、1.221GB 预算下可接受(全套测试时长与 C-2 基线同量级)。库里出现大量类目后需要复核分片大小。
5. **`completeness` 语义仍不对**: 仍写 `"full" if item.content else "excerpt"`,212 行里 75 行正文不足 200 字却标 `full`。这是 C-1,不在本批范围。
6. **扫描失败不阻断采集**: 只读扫描异常只记 `insert.cross_db_scan_failed` + `cross_report.scan_errors`,并让 `doctor` 的 `db.cross_scan` WARN 兜底 —— 故意选择“卫生不饿死采集”,代价是那一轮可能漏拦。

## 12. 回滚位置与方式(仅说明,不执行)

- 代码预镜像(`07_tmp/c3_20261115/`): `pre_ttxt_crawler_store.py`(sha `58426a2e…`)、`pre_ttxt_crawler_runner.py`(`ba85c659…`)、`pre_ttxt_doctor.py`(`451aa25d…`)、`pre_ttxt_config.py`(`daa9c7d1…`)、`pre_00_control_category_registry.json`(`16aedf88…`)、`pre_02_crawler_sources.json`(`89e56226…`)。回滚 = 用预镜像覆盖对应文件后跑全套测试。
- 新增文件回滚: 删除 `conftest.py` 与 `10_tests/test_c3_cross_category_dedupe.py` 即回到原调用形式行为(但 §6 的隔离缺陷会回来)。
- Git 回滚: `git revert <本批 commit>`,历史 commit 不改写。
- 数据库不需要回滚: 生产库字节/sha 全程未变;所有试验都在 `07_tmp/c3_20261115/db_pre`、`db_proof` 副本上。
- 更早的整仓快照: `11_backups/`(保留 7 份)。
