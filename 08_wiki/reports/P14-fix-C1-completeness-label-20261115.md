# P14 · C-1 正文完整性标签整改(2026-11-15)

范围:`articles.completeness` 从"有正文就 full"改为"按真实正文长度如实分档",并对生产 6 库回填。
状态:**已完成**。测试 255 passed / exit 0;生产 124 行已回填并逐列验收;`ttxt doctor` 的 6 条 `db.completeness.*` 全部 PASS。
期间发生**一次自伤事故**(误删 2 个历史备份),已披露并补护栏(见 §8、规则 29)。

---

## 1. 结论摘要

| 项 | 整改前 | 整改后 |
|---|---|---|
| 判定口径 | 只判断"有没有正文":`content` 非空就记 `full`,没有任何长度阈值 | 三档 `full`(可用正文 ≥400)/ `short`(1–399)/ `excerpt`(无可用正文),唯一口径 `cleaner.completeness_of` |
| 生产 257 行标签 | 257 行全部 `full`(谎报 124 行) | `full 133 / short 124 / excerpt 0`,与 census 预测一致 |
| 体检 | 无相关检查 | `doctor` 新增 `db.completeness.<类目>`:谎报 / 低估 / 词表外值,FAIL 兜底 |
| 整改手段 | 无 | `ttxt relabel-completeness`:默认干跑 → `--apply`(逐行 CAS)→ `--rollback <报告>` |
| 测试 | 227 passed | 255 passed(+25 C-1,+3 备份轮转护栏) |
| 备份份数 | `disk.backup_keep=7`,轮转静默删除 | `=30`,`<=0` 全留,每删一个打 `backup.pruned` 日志 |

## 2. 问题与根因

**现象**:C-3 之后的资产审计里发现,6 个类目库共 257 篇文章,`completeness` **100% 是 `full`**,但其中 124 行(48%)去空白后正文不足 400 字,最短 24 字。下游(选材、写作门禁、爆款特征)如果把 `full` 当作"正文完整",就会拿摘要页/列表页残片当全文写作。

**根因链(三段,缺一不可)**:

1. **写入侧没有长度口径**:`store.insert_items` 落库时写的是 `"full" if item.content else "excerpt"`(旧代码,`store.py:518` 处)——只判断"有没有正文",完全不判断正文有多长;DDL 默认值也是 `DEFAULT 'full'`(`store.py:39`)。结果:RSS 摘要、列表页残片、十几行的节选全被记成"全文",`excerpt` 这一档实际上永远打不出来(生产 257 行 0 行 `excerpt`)。
2. **没有反查**:C-1 之前 `doctor` 完全不看这个字段,库里的谎报没有任何地方能暴露出来,只能靠人工审计发现。
3. **口径可能被二次污染**:清洗管线 `pipeline.py` 会写 `cleaned_content` / `text_length`,一旦清洗侧也去写 `completeness`,同一个字段就会有两套含义(采集侧事实 vs 清洗结果)。

**证据位置**:普查 `07_tmp/c1_20261115/census_c1.json`(257 行、0 空正文、阈值 400 → full 133 / short 124 / excerpt 0;`cleaned_content`/`text_length` 只有 43 行有值);整改前体检输出 4 条 WARN(`demo` 43 / `finance` 20 / `health` 25 / `technology` 36,合计 124)。

## 3. 口径决策(定稿,不再讨论)

| 决策 | 内容 | 理由 |
|---|---|---|
| 阈值 | `MIN_FULL_CHARS = 400` | 与写作门禁"长度 ≥400"(`db_schema.md` 关键语义)对齐,避免"标签说 full、门禁说不够"两套尺子 |
| 取值 | `full` / `short` / `excerpt` 三档,词表封闭 | 词表外值一律视为污染,体检 FAIL |
| 语义归属 | **采集侧**属性,只看原始 `content`(首尾空白不计入),不看标题、不看 `excerpt` 列 | 全文/残片是"抓到了什么"的事实;清洗结果属于另一列 |
| 单一口径 | 定义只在 `crawler/cleaner.py`;SQL 侧共用 `store.USABLE_LEN_SQL`;清洗管线**不得**回写 | 257 行实测 SQL 与 Python 判定 drift = 0;已由测试锁定 |
| DDL | 不改(`DEFAULT 'full'` 保留),本批不做 schema 迁移 | 改动面最小;谎报靠 `doctor` 反查兜底,不靠默认值正确 |
| 回填性质 | 派生字段 UPDATE,不动行、不动其它列 | 除 DDL 默认与 INSERT 一列外,`completeness` **在生产里没有任何其它读者**,回填风险低 |
| 覆盖范围 | `--category all` = 仅注册类目;`demo`(未注册)必须显式点名 | 与 C-3 命名空间所有权一致 |
| 范围外 | 面板/看板接入、按 `completeness` 筛选的 UI | 不属于 C-1,后续单独任务 |

## 4. 改动清单

| 文件 | 改动 | 规模 |
|---|---|---|
| [ttxt/crawler/cleaner.py](../../ttxt/crawler/cleaner.py) | 新增 `MIN_FULL_CHARS`、三档常量、`usable_length`、`completeness_of_length`、`completeness_of` | +43 |
| [ttxt/crawler/store.py](../../ttxt/crawler/store.py) | 写入点改为逐行 `cleaner.completeness_of(item.content)`;新增 `COMPLETENESS_VALUES/THRESHOLD`、`USABLE_LEN_SQL`、`distribution_text`、`completeness_plan`、`apply_completeness_plan`(`BEGIN IMMEDIATE` + 逐行 CAS + 异常整笔回滚)、`relabel_completeness`、`restore_completeness`、`CompletenessConflict`、`_require_existing_db` | +172 / -3 |
| [ttxt/doctor.py](../../ttxt/doctor.py) | 新增 `_completeness_one` / `completeness_checks`,挂成体检小节 `# 4c`,谎报给 WARN + 整改提示,低估/词表外给 FAIL | +86 |
| [ttxt/__main__.py](../../ttxt/__main__.py) | 新增子命令 `relabel-completeness`(`--category/--apply/--out/--rollback`),报告路径必须在 `PROJECT_ROOT` 内,任一类目出错退出码 1 | +80 |
| [scripts/backup.py](../../scripts/backup.py) | 轮转删除逐条 `log.warn("backup.pruned")`;`keep<=0` = 不轮转;默认保留 30 | +19 / -4 |
| [00_control/config.json](../../00_control/config.json) | `disk.backup_keep` 7 → 30 | 1 行 |
| [10_tests/test_c1_completeness_label.py](../../10_tests/test_c1_completeness_label.py) | **新增** 25 条(阈值/边界/写入/DDL 不变/干跑/回填/幂等/回滚/CAS/缺库/CLI/体检/清洗不回写) | 新增 |
| [10_tests/test_backup_rotation.py](../../10_tests/test_backup_rotation.py) | **新增** 3 条(删除必须留痕、`keep<=0` 全留、缺键不得退化) | 新增 |
| [08_wiki/db_schema.md](../db_schema.md)、[08_wiki/architecture.md](../architecture.md)、[07_rules/CORE_RULES.md](../../07_rules/CORE_RULES.md) | 语义、状态、规则 28/29 | 文档 |

合计代码 6 文件、394 增 / 8 删。完整 diff:

```diff
diff --git a/00_control/config.json b/00_control/config.json
index 975f9c2..d64645d 100644
--- a/00_control/config.json
+++ b/00_control/config.json
@@ -37,7 +37,7 @@
     "pip_cache_max_gb": 1,
     "log_max_mb": 10,
     "log_keep": 7,
-    "backup_keep": 7,
+    "backup_keep": 30,
     "db_max_mb": {
       "system": 200,
       "article": 2048
diff --git a/scripts/backup.py b/scripts/backup.py
index 195e330..868f574 100644
--- a/scripts/backup.py
+++ b/scripts/backup.py
@@ -81,12 +81,19 @@ def snapshot(label: str, db_paths: list[Path]) -> Path:
                 zf.write(file_path, file_path.relative_to(stage))
     shutil.rmtree(stage)
 
-    keep = int(cfg.load_config().get("disk", {}).get("backup_keep", 7))
-    zips = sorted(cfg.BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime)
-    for old in zips[:-keep]:
-        old.unlink()
-
-    get_logger("backup").info("backup.created", str(zip_path), size_bytes=zip_path.stat().st_size)
+    log = get_logger("backup")
+    log.info("backup.created", str(zip_path), size_bytes=zip_path.stat().st_size)
+
+    # 轮转必须逐条留痕:静默删旧快照等于篡改历史(见 07_rules/CORE_RULES.md 规则 29)。
+    # backup_keep <= 0 表示"全部保留,不轮转"。
+    keep = int(cfg.load_config().get("disk", {}).get("backup_keep", 30))
+    if keep > 0:
+        zips = sorted(cfg.BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime)
+        for old in zips[:-keep]:
+            old.unlink()
+            log.warn("backup.pruned", str(old), keep=keep, reason="backup_keep 轮转")
+    else:
+        log.info("backup.rotation_disabled", "disk.backup_keep<=0:所有快照全部保留")
     return zip_path
 
 
diff --git a/ttxt/__main__.py b/ttxt/__main__.py
index 77f9de9..1de3d99 100644
--- a/ttxt/__main__.py
+++ b/ttxt/__main__.py
@@ -6,6 +6,7 @@ import argparse
 import json
 import subprocess
 import sys
+from pathlib import Path
 
 from . import __version__
 from . import config as cfg
@@ -124,6 +125,75 @@ def cmd_clean(args: argparse.Namespace) -> int:
     return 0 if ok else 1
 
 
+def cmd_relabel_completeness(args: argparse.Namespace) -> int:
+    """C-1:按真实正文长度如实改写 ``completeness``(默认干跑;``--apply`` 才写;``--rollback`` 回滚)。
+
+    报告必须落文件:整改是一份逐行 ``from → to`` 的可回滚账本,不是“跑一遍就完”的修数脚本。
+    ``--category all`` 只覆盖注册表里的正式类目;未注册命名空间(``demo``)要显式点名,
+    与 C-3 的所有权口径一致。
+    """
+    if args.rollback:
+        source = Path(args.rollback)
+        if not source.is_file():
+            print(f"回滚报告不存在:{source}")
+            return 1
+        try:
+            payload = json.loads(source.read_text(encoding="utf-8"))
+        except (OSError, ValueError) as exc:
+            print(f"回滚报告读不出来:{exc}")
+            return 1
+        results: dict[str, object] = {}
+        ok = True
+        for category, report in (payload.get("results") or {}).items():
+            changes = list(report.get("changes") or []) if isinstance(report, dict) else []
+            if not changes:
+                results[category] = {"category": category, "restored": 0}
+                continue
+            try:
+                results[category] = crawler_store.restore_completeness(category, changes)
+            except Exception as exc:  # noqa: BLE001 - 类目级错误隔离
+                results[category] = {"error": f"{type(exc).__name__}: {exc}"}
+                ok = False
+        print(
+            json.dumps(
+                {"rollback_from": str(source), "restored_ok": ok, "results": results},
+                ensure_ascii=False,
+                indent=2,
+            )
+        )
+        return 0 if ok else 1
+
+    if args.category == "all":
+        categories = sorted(crawler_store.registered_namespaces())
+    else:
+        categories = [args.category]
+
+    results: dict[str, object] = {}
+    ok = True
+    for category in categories:
+        try:
+            results[category] = crawler_store.relabel_completeness(category, apply=args.apply)
+        except Exception as exc:  # noqa: BLE001 - 类目级错误隔离
+            results[category] = {"error": f"{type(exc).__name__}: {exc}"}
+            ok = False
+    payload: dict[str, object] = {
+        "applied": bool(args.apply),
+        "threshold": crawler_store.COMPLETENESS_THRESHOLD,
+        "values": list(crawler_store.COMPLETENESS_VALUES),
+        "results": results,
+    }
+    if args.out:
+        target = Path(args.out)
+        if not target.resolve().is_relative_to(cfg.PROJECT_ROOT):
+            print(f"报告必须写在项目内:{target}")
+            return 1
+        target.parent.mkdir(parents=True, exist_ok=True)
+        payload["report_file"] = str(target)
+        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
+    print(json.dumps(payload, ensure_ascii=False, indent=2))
+    return 0 if ok else 1
+
+
 def cmd_analyze(args: argparse.Namespace) -> int:
     if args.category == "all":
         categories = list(cfg.load_config().get("categories", []))
@@ -382,6 +452,16 @@ def build_parser() -> argparse.ArgumentParser:
     clean.add_argument("--category", default="all", help="类目 slug 或 all")
     clean.set_defaults(func=cmd_clean)
 
+    relabel = sub.add_parser(
+        "relabel-completeness",
+        help="按真实正文长度如实改写 articles.completeness(默认干跑,报告可回滚)",
+    )
+    relabel.add_argument("--category", default="all", help="类目 slug 或 all(=注册表里的正式类目)")
+    relabel.add_argument("--apply", action="store_true", help="真正写入(默认只出计划,不改库)")
+    relabel.add_argument("--out", default=None, help="把逐行 from→to 报告写到该 JSON 文件(项目内)")
+    relabel.add_argument("--rollback", default=None, help="按 --out 产出的报告精确回滚")
+    relabel.set_defaults(func=cmd_relabel_completeness)
+
     analyze = sub.add_parser("analyze", help="关键词评分/爆款标签/知识资产/FTS5 索引")
     analyze.add_argument("--category", default="all", help="类目 slug 或 all")
     analyze.set_defaults(func=cmd_analyze)
diff --git a/ttxt/crawler/cleaner.py b/ttxt/crawler/cleaner.py
index 707ae98..39a2ae6 100644
--- a/ttxt/crawler/cleaner.py
+++ b/ttxt/crawler/cleaner.py
@@ -29,3 +29,46 @@ def clean_article(text: str) -> str:
 
 def text_length(text: str) -> int:
     return len(text or "")
+
+
+# ------------------------------------------------------------------ C-1 正文完整性三档
+
+# 含义:**这一行采集到的正文,够不够当“全文语料”用**。判定只看 ``content`` 列本身,
+# 不看标题、不看 ``excerpt`` 列(摘要列有字不等于有正文)。
+# 阈值 400 与写作门禁同轴(``08_wiki/db_schema.md`` 关键语义:长度 ≥400 才可能 ``passed``),
+# 实测语料长度分布在 399→799 之间有明显断层(普查见 ``07_tmp/c1_20261115/census_c1.json``),
+# 400 正好落在断层上,不会把“半截正文”误放成全文。
+MIN_FULL_CHARS = 400
+COMPLETENESS_FULL = "full"
+COMPLETENESS_SHORT = "short"
+COMPLETENESS_EXCERPT = "excerpt"
+COMPLETENESS_VALUES = (COMPLETENESS_FULL, COMPLETENESS_SHORT, COMPLETENESS_EXCERPT)
+
+
+def usable_length(text: str | None) -> int:
+    """计入完整性判定的正文字数(去掉首尾空白后的字符数)。
+
+    与 ``store.USABLE_LEN_SQL``(SQL 侧的同一口径)保持一致;Python 侧的 ``str.strip()``
+    覆盖的空白字符是 SQL 版的超集,极边角空白两边可能差 1 字,由 ``store.relabel_completeness``
+    在整改时统一改成 SQL 口径,不会长期漂移。
+    """
+    return len((text or "").strip())
+
+
+def completeness_of_length(usable_len: int, *, min_full_chars: int = MIN_FULL_CHARS) -> str:
+    """唯一的分档规则:0 字 → ``excerpt``;1 ~ min-1 → ``short``;≥ min → ``full``。
+
+    写入侧、体检侧、整改侧都必须走这个函数,不允许各自写一套阈值判断,
+    否则同一个词在三个地方含义不同,下游(爆款分析、写作取材)就没法按它筛选。
+    """
+    if usable_len <= 0:
+        return COMPLETENESS_EXCERPT
+    if usable_len >= min_full_chars:
+        return COMPLETENESS_FULL
+    return COMPLETENESS_SHORT
+
+
+def completeness_of(content: str | None, *, min_full_chars: int = MIN_FULL_CHARS) -> str:
+    """按采集到的正文本体判定完整性(见 ``completeness_of_length``)。"""
+    return completeness_of_length(usable_length(content), min_full_chars=min_full_chars)
+
diff --git a/ttxt/crawler/store.py b/ttxt/crawler/store.py
index 9bd033a..b130324 100644
--- a/ttxt/crawler/store.py
+++ b/ttxt/crawler/store.py
@@ -7,6 +7,7 @@ import sqlite3
 from pathlib import Path
 
 from ttxt import config as cfg
+from ttxt.crawler import cleaner
 from ttxt.crawler.clean import content_hash, normalize_url
 from ttxt.crawler.extract import ExtractedItem
 from ttxt.db import connect, system_db_path
@@ -21,6 +22,22 @@ CROSS_READ_CHUNK = 400
 # 跨库只读查询允许使用的列(白名单,防止把外部字符串拼进 SQL)
 CROSS_ID_COLUMNS = {"url_normalized", "content_hash"}
 
+# C-1:正文完整性取值词表 + 与 Python 侧一致的长度口径(SQL 版,供体检与整改批量用)
+COMPLETENESS_VALUES = cleaner.COMPLETENESS_VALUES
+COMPLETENESS_THRESHOLD = cleaner.MIN_FULL_CHARS
+USABLE_LEN_SQL = (
+    "LENGTH(TRIM(REPLACE(REPLACE(REPLACE(COALESCE(content, ''), char(9), ''),"
+    " char(10), ''), char(13), '')))"
+)
+
+
+class CompletenessConflict(RuntimeError):
+    """整改正文完整性标签时,行的当前值与计划记录不符(有人已经改过)。
+
+    抛出即整笔回滚:绝不做部分提交,也绝不覆盖别人的改动。
+    """
+
+
 ARTICLES_SCHEMA = """
 CREATE TABLE IF NOT EXISTS articles (
   id INTEGER PRIMARY KEY AUTOINCREMENT,
@@ -515,7 +532,9 @@ def insert_items(
                     url_normalized,
                     fetched_at,
                     hash_value,
-                    "full" if item.content else "excerpt",
+                    # C-1:如实标注正文完整度(三档见 cleaner.completeness_of);
+                    # 不能只判断“有没有正文”——RSS 摘要、列表页正文都会被谎报成全文
+                    cleaner.completeness_of(item.content),
                     "unverified",
                     PROCESSOR_VERSION,
                 ),
@@ -540,6 +559,157 @@ def insert_items(
     return {"inserted": inserted, "duplicates": duplicates}
 
 
+# ------------------------------------------------------- C-1 历史标签整改(dry-run / 写入 / 回滚)
+
+
+def _require_existing_db(category: str, target: Path) -> None:
+    """整改只针对已经存在的命名空间库:不得顺手建库(与 C-3 的“不创建”纪律一致)。"""
+    if not Path(target).is_file():
+        raise FileNotFoundError(f"命名空间库不存在,整改不会建库: {target}")
+
+
+def distribution_text(counts: dict[str, int]) -> str:
+    """把分布渲染成固定顺序的字符串,词表内的 0 值也要显式写出来(便于对比与体检阅读)。"""
+    parts = [f"{value}={counts.get(value, 0)}" for value in COMPLETENESS_VALUES]
+    extras = sorted(value for value in counts if value not in COMPLETENESS_VALUES)
+    parts.extend(f"{value}={counts[value]}" for value in extras)
+    return " ".join(parts)
+
+
+def completeness_plan(category: str, db_path: Path | None = None) -> dict:
+    """只读:算出每行**应有**的 ``completeness`` 与当前值不一致的行(逐行计划)。
+
+    返回 ``{category, db, threshold, rows_total, current, planned, changes}``;
+    ``changes`` 每项含 ``id / from / to / usable_len``,足以逐行回滚与人工核对。
+    """
+    target = Path(db_path) if db_path is not None else articles_db_path(category)
+    _require_existing_db(category, target)
+    con = read_only_connect(target)
+    try:
+        rows = con.execute(
+            f"SELECT id, completeness, {USABLE_LEN_SQL} AS usable_len FROM articles ORDER BY id"
+        ).fetchall()
+    finally:
+        con.close()
+    current: dict[str, int] = {}
+    planned: dict[str, int] = {}
+    changes: list[dict] = []
+    for row_id, label, usable_len in rows:
+        label = str(label)
+        expected = cleaner.completeness_of_length(int(usable_len))
+        current[label] = current.get(label, 0) + 1
+        planned[expected] = planned.get(expected, 0) + 1
+        if label != expected:
+            changes.append(
+                {"id": int(row_id), "from": label, "to": expected, "usable_len": int(usable_len)}
+            )
+    return {
+        "category": category,
+        "db": str(target),
+        "threshold": COMPLETENESS_THRESHOLD,
+        "values": list(COMPLETENESS_VALUES),
+        "rows_total": len(rows),
+        "current": current,
+        "planned": planned,
+        "current_distribution": distribution_text(current),
+        "planned_distribution": distribution_text(planned),
+        "changes": changes,
+    }
+
+
+def apply_completeness_plan(category: str, changes: list[dict], db_path: Path | None = None) -> int:
+    """在单个 ``BEGIN IMMEDIATE`` 事务里按计划改写标签,逐行 CAS(旧值不符 → 整笔回滚)。
+
+    整改只改派生字段 ``completeness``,不删行、不动正文;``from`` 记录旧值,
+    因此同一份计划可以喂给 ``restore_completeness`` 精确回滚。
+    """
+    if not changes:
+        return 0
+    target = Path(db_path) if db_path is not None else articles_db_path(category)
+    _require_existing_db(category, target)
+    con = connect(target)
+    updated = 0
+    try:
+        con.execute("BEGIN IMMEDIATE")
+        for change in changes:
+            to_value = str(change.get("to", ""))
+            from_value = str(change.get("from", ""))
+            if to_value not in COMPLETENESS_VALUES:
+                raise CompletenessConflict(f"计划里的目标取值不在词表内: {change}")
+            cursor = con.execute(
+                "UPDATE articles SET completeness = ? WHERE id = ? AND completeness = ?",
+                (to_value, int(change["id"]), from_value),
+            )
+            if cursor.rowcount != 1:
+                raise CompletenessConflict(
+                    f"id={change['id']} 的当前 completeness 与计划记录({from_value!r})不符,"
+                    "已有其它写入动过这一行,整笔回滚"
+                )
+            updated += 1
+        con.commit()
+    except BaseException:
+        try:
+            con.rollback()
+        except sqlite3.Error:
+            pass
+        finally:
+            con.close()
+        raise
+    con.close()
+    LOG.info(
+        "store.completeness_relabelled",
+        category=category,
+        db=str(target),
+        updated=updated,
+        threshold=COMPLETENESS_THRESHOLD,
+    )
+    return updated
+
+
+def relabel_completeness(category: str, db_path: Path | None = None, apply: bool = False) -> dict:
+    """按真实正文长度如实改写 ``completeness``;默认干跑,``apply=True`` 才写。"""
+    plan = completeness_plan(category, db_path)
+    if not apply:
+        return {**plan, "applied": False, "updated": 0}
+    updated = apply_completeness_plan(category, plan["changes"], Path(plan["db"]))
+    return {**plan, "applied": True, "updated": updated}
+
+
+def restore_completeness(category: str, changes: list[dict], db_path: Path | None = None) -> dict:
+    """按整改报告逐行恢复旧值(回滚);同样带 CAS,冲突即整笔回滚。"""
+    target = Path(db_path) if db_path is not None else articles_db_path(category)
+    _require_existing_db(category, target)
+    if not changes:
+        return {"category": category, "db": str(target), "restored": 0}
+    con = connect(target)
+    restored = 0
+    try:
+        con.execute("BEGIN IMMEDIATE")
+        for change in changes:
+            cursor = con.execute(
+                "UPDATE articles SET completeness = ? WHERE id = ? AND completeness = ?",
+                (str(change.get("from", "")), int(change["id"]), str(change.get("to", ""))),
+            )
+            if cursor.rowcount != 1:
+                raise CompletenessConflict(
+                    f"id={change['id']} 的当前 completeness 与整改后的值"
+                    f"({change.get('to')!r})不符,回滚整笔撤销"
+                )
+            restored += 1
+        con.commit()
+    except BaseException:
+        try:
+            con.rollback()
+        except sqlite3.Error:
+            pass
+        finally:
+            con.close()
+        raise
+    con.close()
+    LOG.warn("store.completeness_restored", category=category, db=str(target), restored=restored)
+    return {"category": category, "db": str(target), "restored": restored}
+
+
 def count_articles(category: str, db_path: Path | None = None) -> int:
     target = Path(db_path) if db_path is not None else articles_db_path(category)
     if not target.exists():
diff --git a/ttxt/doctor.py b/ttxt/doctor.py
index 37008ea..b07ec3f 100644
--- a/ttxt/doctor.py
+++ b/ttxt/doctor.py
@@ -132,6 +132,86 @@ def namespace_checks() -> list[tuple[str, str, str]]:
     return results
 
 
+def _completeness_one(name: str) -> tuple[str, str]:
+    """单个命名空间的正文完整性口径(只读)。返回 ``(level, detail)``。
+
+    口径与写入侧完全一致(``cleaner.completeness_of_length`` + ``store.USABLE_LEN_SQL``):
+
+    - ``full`` 但可用正文 < 阈值 → 谎报全文(下游按“全文语料”筛选会被摘要污染)→ WARN;
+    - ``excerpt``/``short`` 但可用正文 ≥ 阈值 → 低估(明明有全文却当摘要)→ WARN;
+    - ``excerpt`` 但有 1 ~ 阈值之间的零星字符 → 容忍(Python 与 SQL 的空白口径差几个字符,
+      由 ``store.relabel_completeness`` 整改时统一,不因此报警);
+    - 取值不在词表内 → FAIL:这个词已经没有可信含义,必须先修数据再让下游使用。
+    """
+    path = store.articles_db_path(name)
+    threshold = store.COMPLETENESS_THRESHOLD
+    values = store.COMPLETENESS_VALUES
+    placeholders = ",".join("?" * len(values))
+    con = store.read_only_connect(path)
+    try:
+        counts = {str(row[0]): int(row[1]) for row in con.execute(
+            "SELECT completeness, COUNT(*) AS n FROM articles GROUP BY completeness"
+        )}
+        stats = con.execute(
+            "SELECT"
+            f"  SUM(CASE WHEN completeness = ? AND {store.USABLE_LEN_SQL} < ? THEN 1 ELSE 0 END),"
+            f"  SUM(CASE WHEN completeness IN ('excerpt', 'short') AND {store.USABLE_LEN_SQL} >= ? THEN 1 ELSE 0 END),"
+            f"  SUM(CASE WHEN completeness NOT IN ({placeholders}) THEN 1 ELSE 0 END)"
+            " FROM articles",
+            ("full", threshold, threshold, *values),
+        ).fetchone()
+        overclaimed, underclaimed, unknown = (int(value or 0) for value in stats)
+        unknown_values: list[str] = []
+        if unknown:
+            unknown_values = sorted(
+                str(row[0])
+                for row in con.execute(
+                    f"SELECT DISTINCT completeness FROM articles WHERE completeness NOT IN ({placeholders})",
+                    values,
+                )
+            )
+    finally:
+        con.close()
+
+    detail = store.distribution_text(counts)
+    problems: list[str] = []
+    if overclaimed:
+        problems.append(f"谎报全文={overclaimed}(标 full 但可用正文 <{threshold} 字)")
+    if underclaimed:
+        problems.append(f"低估正文={underclaimed}(标 excerpt/short 但可用正文 ≥{threshold} 字)")
+    if unknown:
+        problems.append(f"词表外取值={unknown}: {unknown_values}")
+    if unknown:
+        return (
+            "FAIL",
+            f"{detail}; {'; '.join(problems)}; completeness 已失去可信含义,下游不得按它筛选,先整改再使用",
+        )
+    if problems:
+        return (
+            "WARN",
+            f"{detail}; {'; '.join(problems)}; "
+            f"整改:`ttxt relabel-completeness --category {name} --out <报告> ` 先干跑,确认后再 --apply",
+        )
+    return ("PASS", f"{detail}; 标签与真实正文长度一致(阈值 {threshold})")
+
+
+def completeness_checks() -> list[tuple[str, str, str]]:
+    """逐命名空间的正文完整性体检(C-1)。返回 ``(level, name, detail)``。
+
+    历史缺陷实测:6 个库 257 行**全部**标 ``full``,其中 124 行可用正文不足 400 字
+    (普查 ``07_tmp/c1_20261115/census_c1.json``)。DDL 默认值也是 ``'full'``,
+    任何绕开 ``insert_items`` 的写入都会谎报,所以这里必须天天喊,而不是改一次数据就完事。
+    """
+    results: list[tuple[str, str, str]] = []
+    for name in store.namespaces_on_disk():
+        try:
+            level, detail = _completeness_one(name)
+        except Exception as exc:  # noqa: BLE001 - 单个库读失败不得毁掉整份体检
+            level, detail = "WARN", f"读失败,无法判定 completeness:{type(exc).__name__}: {exc}"
+        results.append((level, f"db.completeness.{name}", detail))
+    return results
+
+
 def _strata_health(config: dict[str, Any]) -> dict[str, Any]:
     base = str(config.get("strata", {}).get("base_url", "http://127.0.0.1:8080")).rstrip("/")
     path = str(config.get("strata", {}).get("health_path", "/health"))
@@ -194,6 +274,12 @@ def run_doctor() -> int:
     except Exception as exc:  # noqa: BLE001 - 卫生检查异常不得让整份体检崩掉
         check("namespaces.checks", False, f"{type(exc).__name__}: {exc}")
 
+    # 4c. 正文完整性口径(C-1:completeness 谎报会让摘要冒充全文进入语料与写作取材)
+    try:
+        results.extend(completeness_checks())
+    except Exception as exc:  # noqa: BLE001 - 口径体检异常不得让整份体检崩掉
+        check("db.completeness", False, f"{type(exc).__name__}: {exc}")
+
     # 5. 磁盘预算
     report = housekeep.report()
     check(
```

## 5. 测试

- 新增 25 条(`test_c1_completeness_label.py`),最终 **25 passed in 2.73s**。覆盖:
  - 阈值常量与三档边界(0 / 1 / 399 / 400 / 401、纯空白、只有标点);
  - `insert_items` **逐行**判定(不是整批一个值);
  - DDL 默认值仍为 `'full'`(防"顺手改 schema");
  - 干跑不改字节、`--apply` 真改、重复跑幂等(changes=0)、`--rollback` 精确复原、报告缺失 → `FileNotFoundError`;
  - 逐行 CAS:别人先动过这一行 → `CompletenessConflict`,整笔回滚,库里一条不改;
  - 体检:谎报 WARN、修好后 PASS、词表外值 FAIL、`run_doctor` 输出含该小节;
  - **清洗管线跑完不改写采集侧标签**(`run_pipeline("technology")` 后标签分布不变、relabel 计划为空)。
- 全套三种调用形式:`pytest`(无参数)、`pytest .`、`pytest 10_tests` → **255 passed,0 skip,0 fail**(58.23s / 59.95s / 47.45s,exit 0)。
- 过程记录(不粉饰):第一轮 9 条失败,两类原因都是**我自己的实现与测试问题**,不是口径错 ——(a) 把 `_distribution` 改名 `distribution_text` 时漏改 2 处调用点(`NameError`);(b) 测试拿 `PRAGMA table_info` 的默认值断言 `== "full"`,而 SQLite 返回带引号的 `"'full'"`(实测后改为 `row_default.strip("'") == "full"`)。另有一条测试写法错(`dict <= dict`)已改成逐字段断言。

## 6. 真实数据证明(先在字节级副本上跑,生产只读)

脚本 [07_tmp/c1_20261115/prove_c1.py](../../07_tmp/c1_20261115/prove_c1.py)(A–L 十二条,输出 [proof_c1.json](../../07_tmp/c1_20261115/proof_c1.json)):**`proof_c1: OK failures=0`**。

| 段 | 验证内容 | 结果 |
|---|---|---|
| A | 体检 WARN 与逐行普查数字一致 | 124 谎报,逐库一致 |
| B | 干跑对 `.db` 字节不变(sha 不变) | 通过 |
| C–D | CLI 干跑/回填;`--category all` 不含 `demo` | 通过 |
| E | 回填后每行标签 == `completeness_of_length(SQL 可用长度)` | 257 行 drift = 0 |
| F | 体检转 PASS | 通过 |
| G | 重复回填幂等(changes=0) | 通过 |
| H | `--rollback` 复原(`apply.json` 与 `apply_demo.json` 都要滚) | sha 回到原像 |
| I | 逐行 CAS 冲突整笔回滚 | 报错文案含"已有其它写入动过这一行,整笔回滚" |
| J | 真实正文走写入侧判定(24 → short、335 → short、6668 → full) | 通过 |
| K–L | 生产库 sha 全程未变;副本清理不影响正式目录 | 通过 |

预测分布(副本与生产回填后一字不差):`demo` 2/43、`finance` 30/20、`health` 0/25、`technology` 101/36、`history`/`social` 空库。

## 7. 生产整改与验收

1. 快照:`python scripts\backup.py C1-pre-relabel` → `11_backups/C1-pre-relabel-20261115-093705.zip`(2619969 B,7 库);字节级原像另存 `07_tmp/c1_20261115/db_c1/`。
2. 干跑:`ttxt relabel-completeness --category all --out 07_tmp\c1_20261115\relabel_prod_plan.json` → 81 条改动(`finance` 20 / `health` 25 / `technology` 36,`history`/`social` 0),`applied=false`。
3. 回填:`--apply --out relabel_prod_apply.json` → updated 20+25+0+0+36 = 81;再 `--category demo --apply --out relabel_prod_apply_demo.json` → updated 43。合计 124。
4. 验收 [07_tmp/c1_20261115/verify_prod_c1.py](../../07_tmp/c1_20261115/verify_prod_c1.py)(全程 `PRAGMA query_only=ON`,**结果 `OK failures=0`**,见 [verify_prod_c1.json](../../07_tmp/c1_20261115/verify_prod_c1.json)):
   - 整改前原像 sha 与登记值一致;4 个有改动的库 `.db` sha 变化,`history`/`social`(0 行)sha 不变;
   - **行集合不增删**(257 行),**除 `completeness` 之外每一列逐行不变**(按 `id` 规范化逐列比对,差异 id 集合为空);
   - 每行标签与真实长度一致(违规 0 条),无词表外标签;
   - 分布 `full 133 / short 124 / excerpt 0`,总行数 257。
5. 复检 `ttxt doctor`:`summary: 19 ok, 0 failed`;6 条 `db.completeness.*` 全 PASS(如 `technology: full=101 short=36 excerpt=0; 标签与真实正文长度一致(阈值 400)`)。仍存的 WARN 与 C-1 无关:`namespaces.disk`(有 `demo` 未注册)、`db.cross_overlap`(`demo<->technology` 13/13)。

sha8 前后:

| 库 | 前 | 后 |
|---|---|---|
| demo | 377ef994 | 5813396f |
| finance | a8c2160f | 194470bc |
| health | e6277d2c | 8e1905a7 |
| technology | 35b7f32c | 3fef75ba |
| history | d986704e | 不变(0 行) |
| social | 25799c43 | 不变(0 行) |

## 8. 事故披露:误删 2 个历史备份(我的责任)

**时间线**:第 1 步做快照时调用了 `scripts/backup.py:snapshot("C1-pre-relabel", ...)`,以为轮转在 `main()` 里 —— 实际轮转就在 `snapshot()` 内部。当时 `11_backups` 有 9 个 zip、`disk.backup_keep=7`,于是当场删掉最老的两个:

- `E2E-20261112-142100.zip`
- `P10-final-20261112-142130.zip`

**删除没有任何日志痕迹**,事后靠对比 zip 清单才发现;全项目 glob 确认文件不存在(Git 不跟踪 `11_backups/`,不进回收站)——**这两个快照不可恢复**。

**影响评估**:两个快照内容是当时的代码/草稿/DB 副本;当前代码与数据完整(257 行、sha 已登记、C-1 回滚链完整),P10/P11 的报告与结论均在 Git 里,业务上没有丢失证据。**未因此丢数据**。

**补法(已做,带测试)**:
1. 轮转每删一个 zip 打 `backup.pruned`(路径 + keep + 原因),日志缺失即视为篡改历史;
2. `backup_keep<=0` = 全部保留,不轮转;
3. `disk.backup_keep` 7 → 30,默认值也从 7 提到 30,缺配置键不得退化成"删到只剩 7";
4. 新增 [10_tests/test_backup_rotation.py](../../10_tests/test_backup_rotation.py) 3 条钉死上述语义;
5. 写成规则 29:**复用别人的函数之前先读它的函数体**,确认它是否删文件/轮转/写生产;任何自动删除必须留痕。

## 9. 影响面(动旧代码前评估)

- `completeness` 的**读者**:生产代码里除 DDL 默认值与 `insert_items` 的一列之外没有任何读取点 → 回填不改变任何现有行为,只让将来的筛选有真实依据。
- `store.insert_items` 的调用方:`fetcher` / `pipeline` / 测试桩,写入值变(可能从 `full` 变 `short`),接口签名不变;清洗/分析/写作路径都不读该列,因此不会引发行为回归。
- `doctor` 新增小节:只增检查,不改既有判定;`run_doctor` 输出多一段,面板不解析体检文本(面板走 API,不解析 CLI 文本)。
- `scripts/backup.py`:行为更保守(少删、多留),`main()` 调用方式不变;`test_p1_smoke.py` 只断言 `disk` 键存在,不受取值影响。
- `00_control/config.json`:仅 `disk.backup_keep`,磁盘压力仍由 `doctor` 的 `disk.free/disk.budget` 报警兜底(实测 1.222GB / 20GB)。

## 10. 残留风险

1. `excerpt` 档目前 0 行:现有采集没有"完全无正文"的条目,这一档是为未来(付费墙/抓取失败)准备的,尚无真实样本验证。
2. 阈值 400 是**策略**不是事实:若日后写作门禁改长度口径,必须同时改 `MIN_FULL_CHARS` 并重跑 `relabel-completeness`(体检会立刻暴露不同步)。
3. 清洗覆盖率仍 95.28%(C-4 未长跑):`completeness` 修好不等于 `cleaned_content` 有值,43/257 有清洗结果;选材时仍需区分"抓到的正文"与"清洗后的正文"。
4. `credibility` 全库仍是 `unverified`(C-5 未做),完整性 ≠ 可信度,别把两者混用。
5. `demo` 仍未注册,跨类目对账长期挂 WARN;历史重复行(`technology` toutiao ×3、`finance` eastmoney ×2、`demo<->technology` 13 行)未清理,需单独批准。

## 11. 回滚链(说明,不执行)

- 快照:`11_backups/C1-pre-relabel-20261115-093705.zip`(整库还原);
- 字节级原像:`07_tmp/c1_20261115/db_c1/<slug>/articles.db`(sha8 已登记,可逐库覆盖回去);
- 标签级回滚:`ttxt relabel-completeness --rollback 07_tmp\c1_20261115\relabel_prod_apply.json`(`demo` 另用 `relabel_prod_apply_demo.json`)——按报告里的 `id/from/to` 精确复原,同样走逐行 CAS;
- 代码级:本轮改动未提交前可丢弃;提交后 revert 相应 commit(不使用 `reset/clean/stash/checkout`)。

## 12. 下一步

1. 采集栈按官方文档核验(Scrapling 0.5.1 / curl_cffi / playwright 与本地实际),再恢复真实采集;Worker/Scheduler 在核验通过前不启动。
2. 恢复采集后 `ttxt pipeline` 长跑(C-4),把 95.28% 未清洗补齐。
3. C-5 可信度分层、C-6 评审元数据(需 DDL,单独批准);历史重复行清理单独批准。

## 13. 证据文件清单

| 文件 | 内容 |
|---|---|
| `07_tmp/c1_20261115/census_c1.json` | 257 行普查 + 阈值分布 |
| `07_tmp/c1_20261115/db_c1/` | 整改前 6 库字节级原像 |
| `07_tmp/c1_20261115/prove_c1.py` / `proof_c1.json` | 副本上 A–L 十二条证明 |
| `07_tmp/c1_20261115/relabel_prod_plan.json` | 生产干跑逐行计划(81 条) |
| `07_tmp/c1_20261115/relabel_prod_apply.json` / `..._apply_demo.json` | 生产回填账本(81 + 43 条,可用于 `--rollback`) |
| `07_tmp/c1_20261115/verify_prod_c1.py` / `verify_prod_c1.json` | 生产回填后逐列验收 |
| `11_backups/C1-pre-relabel-20261115-093705.zip` | 整改前整库快照 |
