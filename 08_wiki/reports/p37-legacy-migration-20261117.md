# T-14 报告:旧资产库 `factory.db` 迁入类目库(2026-11-17)

本轮解决账本里的 **T-14**(`资产库/01_语料库/factory.db` 未迁移),顺带修掉迁移暴露出的只读连接缺陷(新增规则 58),
并把「爆款口径」第一次真跑出来。采集口径、限速、SSRF 保护、浏览器 profile 锁、指标公式、写作与发布、
表结构(零 DDL)、来源 allowlist、Strata 与 V100 **一律未改**。

一句话结论:**旧库 53,678 行里 52,743 行按类目隔离进了正式库,旧库一字节未动(sha256 前后一致);
新语料当场可检索;但"有语料"不等于"有口径"——旧库没有展现量,social 的爆款阈值依旧如实拒绝标定。**

---

## 1. 为什么现在做(实测依据,不是推测)

| 事实 | 实测结果 |
|---|---|
| 旧库里有多少可用语料 | `factory.db` 的 `articles` 53,678 行,正文在 `article_bodies` 里,`read_count/digg_count/comment_count` 有值 |
| 这些语料现在能不能被系统用 | **不能**:它们在 `资产库/`(只读资产区),类目库里查不到、检索不到、不参与爆款分析 |
| 类目隔离会不会被污染 | 预演实测:与现网 486 行**零碰撞**(`would_collide_same_namespace 0`),跨命名空间拦截 0 |
| 旧库的标签能不能直接信 | 不能。`collect_count / hot_score / hot_level / tier_v2 / is_elite / is_viral` 语义未证实 ⇒ **一律不迁**,`collect_count` 不许冒充 `favorites` |

## 2. 改了什么(零 DDL,复用既有入口)

| 文件 | 改动 |
|---|---|
| `ttxt/legacy/factory.py` | **新增**。旧库只读层:`mode=ro` + `PRAGMA temp_store=MEMORY` + `query_only=ON`;`sha256_file` 前后取指纹;`iter_articles` 流式读(不把 5 万行 load 进内存);`to_item` 复用 `clean_article` + `normalize_url` + 时间归一;`metrics_row` 按口径把 0 记 NULL,全空则不产出空观测 |
| `ttxt/legacy/migrate.py` | **新增**。迁移编排:**默认只读预演**,`--apply` 才写;类目映射来自配置不写在代码里;入库直接复用 `store.insert_items`(去重/身份/计数规则与采集入口同源,绝不另立一套);指标复用 `metrics_import.write_metric_rows`(白名单校验照旧);每类目**首次写入前** `sqlite3.backup` 备份;逐批对账(articles/metrics/snapshots 前后差值写进回执);导入后按类目 `rag.rebuild_index` 一次 |
| `ttxt/__main__.py` | 新增子命令 `legacy-migrate`(`--policy/--legacy-db/--apply/--batch-size/--limit/--backup-dir/--receipt`),预演与真实跑同一份回执结构 |
| `00_control/legacy_migration.json` | **新增口径**:旧库路径、`source_id=legacy_factory_search_ssr`、`赚钱案例→finance`、`真人故事→social`、`min_body_chars 400`、`zero_as_missing_fields [reads,likes,comments]` |
| `00_control/config.json` | 指标白名单注册真实来源 `legacy_factory_search_ssr: public_page`(不是放宽,是把旧库来源纳入同一套可信度分级) |
| `ttxt/crawler/store.py` | `read_only_connect` 补 `PRAGMA temp_store=MEMORY`(见 §5) |

## 3. 预演(只读,零字节)

回执 `09_logs/legacy_migrate/2026-11-17T2101240800-dry_run.json`:

- 扫描 53,678 行 ⇒ `would_insert 52,743`、`would_collide_within_migration 542`、`would_collide_same_namespace 0`
- 拒绝:`body_too_short 393`;正文按 8,000 字上限截断 874 条
- 备注如实写明:`预演只读:没有建库、没有写一个字节`
- 测试 `test_dry_run_writes_zero_bytes` 钉住:预演不得顺手造出一个命名空间(`social.db` 不存在)

## 4. 真实导入(107 批,逐批对账)

回执 `09_logs/legacy_migrate/2026-11-17T2103320800-apply.json`,退出码 0,`errors []`、`index_errors []`:

| 计数 | 读数 | 预演 vs 真实 |
|---|---|---|
| 旧库扫描 | 53,678 | 一致 |
| 入库 | **52,743**(finance 12,348 / social 40,395) | 与预演 `would_insert` 一致 |
| 拒重(旧库一稿多发) | 542 | 与预演 542 一致 |
| 正文过短拒收 | 393 | 一致 |
| 正文触顶 8,000 字 | 874 | 一致 |
| 指标写入 / 快照追加 | 34,549 / 34,549 | — |
| **指标孤儿(不入库)** | **412** | 对应文章没落地(一稿多发或跨命名空间拦截),**绝不把指标挂到别的文章上** |
| 与现网碰撞 / 跨命名空间拦截 | 0 / 0 | 一致 |
| FTS 索引 | `indexed 53,229`(本轮类目重建) | `doctor.db.fts_coverage` **53,757/53,757 PASS** |

- **旧库只读红线**:`legacy_sha256_before == legacy_sha256_after == a8a1f4c3dbe2f3d25ccafd350e95a9892f44b5517008629322be2bb18976e79e`;sha256 若变化 `migrate.run` 直接 `RuntimeError` 终止。
- **备份**:`11_backups/social-before-2026-11-17T2103430800.db`(4,399,104 B)、`11_backups/finance-before-2026-11-17T2103460800.db`(16,060,416 B)——备份在 `ensure_schema` **之前**,不是之后。
- **幂等**:测试 `test_second_run_is_idempotent` 钉住二次跑 `inserted 0`、快照不增。
- **可检索**:`ttxt search --category social --query 副业/退休金` 直接命中旧语料(标题/URL/发布时间齐全),不需要先跑 `analyze`。
- 磁盘:`01_categories` 407 MB → 1.7 GB,项目总量 9.44 GB / 预算 20 GB,`disk.root_budget` PASS。

## 5. 迁移当场暴露的缺陷:只读连接没有 `temp_store=MEMORY`(已修)

- **现象**:`ttxt doctor` 的 `db.url_identity` 报 `URL 身份对账读失败:OperationalError: unable to open database file`,体检在这一项上瞎了。
- **根因**:`store.read_only_connect` 设了 `query_only=ON` 却没设 `temp_store`。`GROUP BY source, url_normalized` 的临时 B-tree 一旦从内存升级到磁盘临时库就要在 TMP 落文件,而本机 TMP 不可写(规则 27 的边界)。P12 批次4 当年只在**写连接** `ttxt/db.py:102` 修过同一个坑,只读连接漏了;库小的时候永远不升级,所以潜伏了几十个批次。
- **复现**:`300` 行 × `8,000` 字正文的宽列 `GROUP BY` 即可确定性复现(`10_tests/test_p37_read_only_temp_store.py`)。
- **修复**:只读连接加 `PRAGMA temp_store=MEMORY`;**没有**摘掉 `query_only=ON`(修的是临时库,不是只读红线,测试 `test_read_only_connection_still_cannot_write` 钉住)。
- **验证**:真实 social 库 `GROUP BY` 从报错 → 40,506 组正常返回;把 `TEMP` 指向不存在的目录仍然正常。教训写进规则 58(含「审计脚本必须用 `store.read_only_connect`,不许手搓 `sqlite3.connect` 绕过防线」——本轮我自己的审计脚本在同一处栽了第二次)。

## 6. 爆款口径:第一次真出阈值(以及依然如实拒绝的部分)

现场跑 `ttxt metrics --calibrate`(先按当前指标重算标签,再标定,标定后再重算):

| 类目 | 状态 | 实测样本 | 阈值(verified/potential/normal) | 重算标签分布 |
|---|---|---|---|---|
| technology | **calibrated** | 200 | 0.0548 / 0.0261 / 0.0125 | measured 200、missing_data 120 |
| finance | **calibrated** | 46 | 0.0553 / 0.0288 / 0.0147 | measured 46、**no_impressions 8,037**、missing_data 4,640 |
| history | **calibrated** | 46 | 0.0528 / 0.0299 / 0.0193 | measured 46、missing_data 104 |
| social | **calibrated**(公开页回访之后) | **80** | 0.0347 / 0.0083 / 0.0003 | measured 80、`no_impressions 26,514`、`missing_data 13,912` |
| health | **insufficient_sample** | 0 | 不改口径 | 26 行 `missing_data`(类目总共 26 行,结构上不可能到 30) |

关键判断:**旧库没有展现量,所以它进不了 `measured` 分母**——`ttxt/analysis/viral.py:127` 规定 `impressions` 缺失或 ≤0 一律 tier `no_impressions`。这是设计而不是 bug(规则 6:缺 ≠ 0,更不等于已测得)。语料从 83 篇涨到 40,506 篇并没有让 social 的口径成立。

**T-16 的 social 部分已执行(同日 21:58–22:11,`--apply`)**:`ttxt metrics --fetch-public --category social --apply --limit 90 --sleep 4`,回执 `09_logs/metrics/social_apply_20261117T2158.json`。

| 计数器 | 值 | 口径 |
|---|---|---|
| candidates / host_skipped | 90 / **111** | 111 是中国新闻网那批非头条页面,按白名单跳过,不碰 |
| fetched / render_ok / render_mismatch / render_unattributed | 82 / 82 / **0** / **0** | 每页都按文章 id 核对计数归属 |
| fetch_errors / article_gone | 8 / **8** | 8 篇 HTTP 404 终态,记进 `articles_meta.metrics_link_status`,默认不重试 |
| written / snapshots / filled_from_null | **82 / 82 / 326** | 只填 NULL,不覆盖已有值(`protected_by_credibility 0`) |
| sanity_rejected | **2** | 展现量 < 阅读量(物理不可能)⇒ 只拒 `impressions`,阅读/点赞/评论照写(`metrics_pages.py:347`) |
| `impressions` 实际入库 | **80** | 其余 2 页被上一行那道守卫拒了 |
| `favorites` | 全部 `stayed_null` | 公开页结构上没有这个计数,`repinCount` 语义未证实 ⇒ 绝不改名充当(规则 6 / P22) |
| row_errors | **0** | — |

标定阈值跨度比别的类目大一个量级(p25 = 0.0003),因为公开页 `readCount` 很多是个位数(例:`read 1 / show 12258` ⇒ 阅读率 0.00008),分布极度右偏。这条如实记在 T-07:**social 的阈值可用但偏斜,别拿 p25 当"及格线"**。

## 7. 新语料带来的新欠账(不许因为"迁移完成"就忘掉)

1. **T-17 管线未增量化(欠账仍在,但已跑通一次)**:`clusters.cluster_category` 是 O(n²) simhash 两两比对。`clean --category finance`(12,723 行)可跑完;**social 40,506 行本轮跑完了,`real 47m51.9s`**(退出码 0)。回执读数:`articles 40506`、`cleaned 40506`、`indexed 40506`、`clusters 39169`、`near_duplicate_pairs 3717`、`singletons 38410`、`dates_normalized 0`、`clean_version p3-v1`;事后库内 SQL 复核:`cleaned_content` 非空 **40,506/40,506**、`articles_fts` **40,506**、`article_clusters` **39,169**、`cluster_id` 非空 40,506。**结论:能跑完,但每跑一次都是全量 O(n²) —— 40k 行 48 分钟,增量化的欠账没还。** 迁移回执里那句「simhash 聚类与 `analyze` 派生列未跑」指的是**导入当场**,不是永久跳过。
2. **T-13 卫生项已整改**:`relabel-completeness --category social --apply` 把 **8 行**(40,506 中)`full → short`(可用正文 385–397 字),回执 `07_tmp/relabel-social-apply-20261117.json`,支持 `--rollback`,**未删任何语料**;finance 7 行、history 1 行同法已在前一轮 apply。
3. **T-16 展现量缺口:social 已闭合,health 结构上补不了**:见 §6。health 只有 26 行,`MIN_CALIBRATION_SAMPLE=30` 意味着**该类目标定不可能达标**,除非先扩采集;这条要当采集缺口记,不是标定 bug。
4. **类目纯度待审计**:旧库按 `primary_type`(赚钱案例/真人故事)映射到 finance/social,映射本身在配置里可改,但「赚钱案例」里是否混着非财经内容尚未逐条核对 —— 规则 29 要求类目垂直,这条单独记账。
