# P16 · C-7 历史重复行整改报告(2026-11-15)

## 一、事实(实测证据)

| 项 | 整改前 | 整改后 |
| --- | --- | --- |
| 文章行合计 | 328 | **312**(删 16 行) |
| finance | 97 行 / FTS 47 / 1 个多行 URL | 96 行 / FTS **96** / 多行 URL **0** |
| technology | 161 行 / FTS 62 / 1 个多行 URL | 159 行 / FTS **159** / 多行 URL **0** |
| demo(未注册命名空间) | 45 行,其中 13 行是 technology 文章的跨库副本 | 32 行 / FTS 32 / 跨库副本 **0** |
| health | 25 行 / FTS 25 | 25 行 / FTS 25(无重复,未动) |
| history / social | 0 行 | 0 行(没有已批准来源,本来就没数据) |
| `doctor` | 25 ok,1 failed(`db.duplicate_rows` FAIL + `db.cross_overlap` WARN) | **28 ok,0 failed** |
| `dedupe.summarize()` | duplicate_rows 3 / cross_copy_rows 13 / identity_suspect 1 组 30 行 | duplicate_rows **0** / cross **0** / identity_suspect 1 组 30 行(原样保留) |

证据文件:`07_tmp/p16_dedupe_20261115/census.json`(普查)、`07_tmp/p17_dedupe_apply_20261115/state_before.json`、`plan_dryrun.json`、`ledger_apply.json`、`state_after.json`、`rollback_rehearsal.json`。

## 二、问题与根因

1. **同一个 URL 在同一类目库里存了多行**(finance 1 组 2 行、technology 1 组 3 行)。根因:C-2 之前的写入侧没有把 URL 当文章身份;这些行是同一次事件的不同时刻快照(标题或正文略有差异),不是不同文章。
2. **technology 的文章在 `demo` 库里又存了一份**,共 13 行(P12 转正时留下的副本)。根因:C-3 之前写入侧不做跨类目拦截,`demo` 又是未注册命名空间,没有拦截权。
3. **`articles_fts` 严重滞后**:finance 97 行只有 47 条索引、technology 161 行只有 62 条。根因:历史写入路径没有同步 FTS;本次顺手由 `ttxt analyze` 全量重建修好(现在是 96/96、159/159)。
4. **`evidence_links` 有 9 条没有出处**(finance 4、health 5:`article_id`、`quote`、`source_url` 全空)。这不是引用断链,是写作闭环取证没做全;现在 `doctor.db.orphan_references` 会把这件事天天喊出来。
5. **`demo` 的 30 行 `https://quotes.toscrape.com/` 不是重复**:列表页抽取器把来源页地址赋给了每一条(`extract.py:31-58` 的 `url=source.url`)。删掉就毁掉真实语料,所以判为 `identity_suspect` 只报警不删,修的方向是 C-7b。

## 三、判定规则(写进代码的口径,`ttxt/crawler/dedupe.py`)

1. 同一命名空间内,规范化 URL 相同 = 同一篇文章。**例外**:来源页/订阅地址(`sources.load_sources()` 里的 page URL)永远不算身份,只进 `identity_suspect`。
2. 只有满足下列任一条才允许删:标题相同 / `content_hash` 相同 / 全部行同属一个 `cluster_id`。否则一律 `identity_suspect`,**不删**。
3. 保留行(keeper):可用正文最长 → `completeness='full'` → id 最小。被删行的**整行原文**进账本。
4. 引用表(`viral_labels`/`article_metrics`/`metric_snapshots`/`evidence_links`/`articles_fts`)只允许**搬移**或**丢弃并记录**,绝不留下孤儿引用。
5. 跨命名空间副本只能从**未注册**命名空间删;两个注册类目都持有 → `ambiguous`,交人决定,机器不删。跨库引用直接丢弃(类目隔离禁止跨库指针)。
6. 默认干跑;`--apply` 必须配 `--out` 出账本;逐行 CAS(行被人动过就整笔回滚);`--rollback <账本>` 按原 id 逐行复原。

## 四、改了哪些文件

- 新增 `ttxt/crawler/dedupe.py`(普查/判定/账本/搬移引用/逐行 CAS/回滚/汇总)。
- `ttxt/doctor.py`:新增 `db.duplicate_rows`、`db.identity_suspect`、`db.orphan_references` 三项体检。
- `ttxt/__main__.py`:新增 `dedupe-history` 子命令(`--category/--apply/--out/--rollback/--skip-cross`)。
- `10_tests/test_c7_history_dedupe.py`:新增 20 条测试。
- `10_tests/test_p7_dashboard.py::test_category_articles`:检索断言不再押生产内容(改成自己播种一行)。原因见"事故"一节。

## 五、测试

- `python -m pytest 10_tests -q` → **298 passed**,56.59s,exit 0。
- 仓库根 `python -m pytest -q` → **298 passed**,66.30s,exit 0。
- 生产库隔离夹具(`production_state_untouched`、`session_db_opens`)全程绿:测试没有打开过生产库,没有在 `07_tmp` 之外建库。
- **回滚演练**(不是单元测试,是拿生产数据重放):把整改前备份 zip 里的库副本摆到 `07_tmp/p17_c7_rehearsal/`,用生产账本重放"删 16 行 → 回滚"。结果 `REHEARSAL_OK`:文章行逐行(原 id + `content_hash` + 标题 + URL + 长度 + 簇)完全一致、引用计数完全复原、孤儿引用没有新增,演练删除行数 16 == 生产账本行数 16。见 `07_tmp/p17_dedupe_apply_20261115/rollback_rehearsal.py` / `.json`。

## 六、事故与如实披露

1. 第一版 `read_rows` 用 `sqlite3.Row` 取列,而 `store.read_only_connect` 不装 Row 工厂 → `TypeError`,连带 11 条测试红。改成 `cursor.description` + `zip`。
2. 有一条测试想在同一个库里塞两条同 `content_hash` 的行 → `UNIQUE constraint failed`。普查证明 `same_hash_in_same_db = 0`(物理上不可能),测试改为验证并列时的取舍顺序(先 `full`、再小 id)。
3. `apply_cross` 的第一版 CAS 是空转(计划里只有 id 没有整行),重写为按 `content_hash` 逐行比对,不一致抛 `DedupeConflict`。
4. **删完行之后 P7 面板测试红了一条**(`assert 0 >= 1`):`isolated_data_root` 夹具是把生产库**复制**到 `07_tmp` 再测,而那条断言押的是"demo 里能搜到『城市』"—— 被删掉的 13 行 sspai 副本里恰好有唯一命中。这是测试期望押生产内容的坑,已改成播种一行并保留命中/不命中两次断言,没有放宽覆盖面。
5. Scrapling 仍然发伪装 UA + Google referer,而 robots 判定用项目自己的 UA;`excerpt` 档 0 行真实数据;400 字阈值是口径不是事实;`extract.py` 的列表页身份塌陷仍未修(C-7b)。

## 七、剩余阻塞(下一步)

1. **C-7b**:`extract_html_items` 要用每条自己的链接,不再把来源页赋给每条;入库时同 URL 不同内容不得静默吞掉,要计数 + 记日志。
2. 真实采集还没恢复:需要按官方文档核验 Scrapling/CloakBrowser 部署(`ttxt doctor crawler.stack.*` 已绿但真实抓取未跑),恢复后每类目必须"干净、去重、完整、类目垂直"。
3. `history`/`social` 两个类目 0 行,缺已批准来源。
4. C-5 可信度分档(现在全部 `unverified`)、C-6 评审元数据 DDL、`evidence_links` 9 条无出处 —— 都要单独批准后再动。
5. 浏览器 profile 547MB,配额 `disk.browser_max_gb 3`;Worker/Scheduler 未启动。

## 八、回滚位置

- 整改前完整备份:`11_backups/C7-pre-dedupe-20261115-112912.zip`(189 个条目,2,616,153 字节,sha256 `4912adf9abd97f5885aa2a596aa373f86486f8cbaec8cd3e8796ff1a9b99c109`)。备份用的是 `Connection.backup()`(纯复制文件会丢 WAL 内容)。
- 行级账本:`07_tmp/p17_dedupe_apply_20261115/ledger_apply.json`(16 行整行原文 + 被搬移/丢弃的引用)。复原命令:`python -m ttxt dedupe-history --rollback 07_tmp/p17_dedupe_apply_20261115/ledger_apply.json`。
- 派生索引不在账本里:`articles_fts`、`article_clusters` 由 `ttxt clean --category <类目>` + `ttxt analyze --category <类目>` 重建。
- 本次没有删除任何旧文件、日志、备份、WAL/SHM 或 pytest 残留。
