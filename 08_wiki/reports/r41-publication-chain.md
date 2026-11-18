# R41 草稿 → 发布链接 → 表现数据:绑定链路与 T+N 口径

日期:2026-11-19 · 范围:只做三步走的第一步(绑定链路),评分连续化与去模板化未动。
口径约束:缺失字段一律 `NULL`(不填 0);发布时间不可解析就拒绝绑定(不猜);A+B 门禁、事实可核率、取样规则、A+B 读数一字节未改。

## 1. T+N 定义(本轮起替代「采集时点快照」口径)

| 项 | 定义 |
| --- | --- |
| T | 文章真实发布时间 `published_at`,由 `crawler.timeparse.normalize_datetime` 归一化 |
| N | 默认 **3 天**;`TTXT_PERFORMANCE_WINDOW_DAYS` 环境变量或 `config.metrics.performance_window_days` 可覆盖 |
| `due_at` | `T + N 天` |
| 有效窗口 | `[due_at, due_at + 24h]`;窗口内多条观测取**最早**一条 |
| `on_time` | 在有效窗口内取到读数 |
| `late` | 窗口内无观测,取窗口后最早一条(**口径不同,不与 on_time 混用**) |
| `early` | 只有 `due_at` 之前的观测,不作为 T+N 读数 |
| `pending` | `due_at` 未到且暂无观测 |
| `overdue` | 窗口已过、只有到期前观测 |
| `unavailable` | 一条观测都没有(不可回填) |

代码位置:`ttxt/writing/publication.py`(`window_days` / `window_bounds` / `classify`)。

**口径替换的边界**:`performance_windows` 成为发布表现读数的唯一入口;`metric_snapshots` 降级为原始时序输入,不再直接当「表现读数」。`viral.label_category` 与 A+B 标签本轮**保持原样**(否则会改动 A+B 读数),切换需单独评估,不在本轮。

## 2. 数据结构(每类目 `articles.db` 内,幂等建表)

- `publications(id, category, draft_id, version, platform, url, url_normalized, published_at, published_at_source, bound_at, note)`,URL 唯一。
- `publication_observations(publication_id, captured_at, reads, likes, favorites, comments, impressions, data_source, note)`,原始观测,`COALESCE` 合并不抹掉已知值。
- `performance_windows(subject_type, subject_key, category, publication_id, draft_id, window_days, window_start, due_at, window_end, captured_at, 五项指标, status, reason, source)`,发布与历史文章共用,`draft_id` / `publication_id` 留在行上供后续评分对照直接 join。

CLI:`python -m ttxt publish {bind|record|report|backfill|usability}`。

## 3. 链路可用性实测(真实库,非模拟)

### 3.1 草稿 → 发布链接:0 / 14,断在第一步

| 类目 | 草稿数(distinct draft_id) | 已绑定 | 结论 |
| --- | --- | --- | --- |
| technology | 7 | 0 | broken |
| social | 3 | 0 | broken |
| finance | 2 | 0 | broken |
| health | 2 | 0 | broken |
| history | 0 | 0 | empty |
| 合计 | 14 | 0 | **broken** |

断点:`writing_history` 只有 `draft_id/category/topic/version/content/model_id/status/created_at`,**没有 `url`、没有 `published_at`、也没有平台文章 id**。在「不猜测」的前提下无法自动绑定,只能由发布动作显式调用 `publish bind` 写入。这正是 R40 里「38 个评分版本全都没法对上表现」的根因。

### 3.2 文章 → T+N 快照:53,721 篇全部回填,只有 40 篇命中窗口

回填回执(`backfill-20261119-apply.json`):`articles_with_published_at = 53,721`,`bad_published_at = 0`,`windows_written = 53,721`;无发布时间 598 篇(finance 170、social 428,如实计为不计入)。

| 类目 | 有发布时间 | 窗口行 | 有读数 | on_time | late | early | pending | overdue | unavailable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| finance | 12,824 | 12,824 | 7,999 | 0 | 7,999 | 73 | 458 | 4 | 4,290 |
| social | 40,146 | 40,146 | 26,328 | 0 | 26,328 | 0 | 146 | 0 | 13,672 |
| technology | 468 | 468 | 117 | **35** | 82 | 88 | 175 | 31 | 57 |
| history | 216 | 216 | 28 | **5** | 23 | 35 | 92 | 18 | 43 |
| health | 67 | 67 | 14 | 0 | 14 | 26 | 0 | 0 | 27 |
| **合计** | **53,721** | **53,721** | **34,486** | **40** | **34,446** | **222** | **871** | **53** | **18,089** |

结论:**绑定逻辑不是断点,采集时点才是。** 历史快照由批量采集写入,绝大多数落在发布后远超 T+3d 的位置(`late` 占 34,446/34,486 = 99.88%),因此 on_time 只有 40 篇(占 0.074%);另有 **18,089 篇没有任何快照**,按「无法回填」标 `unavailable`,不拿别的时点数据冒充。technology/history 能命中,是因为 `toutiao_public_article` 的采集批次恰好落在窗口内,证明 T+N 选点本身工作正常。

### 3.3 有读数窗口的字段缺失(缺失 ≠ 0)

| 字段 | 有读数窗口缺失数 | 占比(34,486) |
| --- | --- | --- |
| reads | 1,548 | 4.5% |
| likes | 16,345 | 47.4% |
| comments | 25,901 | 75.1% |
| impressions | 34,329 | 99.5% |
| favorites | **34,486** | **100%** |

`favorites` 从未被采集(公开页拿不到、无后台导出),因此永远 `NULL`;`impressions` 只有 `toutiao_public_article` 采集批次带得回来。这些字段全部存 `NULL`,没有任何一处填 0。

### 3.4 按单篇可查样例(technology,on_time)

`publish report --category technology --subject article` 实测 5 篇(完整字段见 `report-technology-on-time-samples.json`):

| article_id | 发布时间 | due_at | 采集时间 | reads | likes | comments | impressions | favorites |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 492 | 11-15 23:56 | 11-18 23:56 | 11-19 02:11 | 1,047 | 12 | 1 | 52,424 | NULL |
| 339 | 11-13 22:20 | 11-16 22:20 | 11-16 22:22 | 200 | 3 | 0 | 8,151 | NULL |
| 334 | 11-13 16:21 | 11-16 16:21 | 11-16 22:22 | 1,709 | 22 | 1 | 57,075 | NULL |
| 330 | 11-13 15:24 | 11-16 15:24 | 11-16 22:22 | 156,076 | 873 | 357 | 3,447,726 | NULL |
| 291 | 11-13 13:54 | 11-16 13:54 | 11-16 22:22 | 3,257 | 23 | 6 | 190,711 | NULL |

每篇都能同时查到发布链接、发布时间、`due_at`、实际采集时间、五项指标与 `missing_fields`。

## 4. 断点、缺什么、下一步

1. **draft → publication(断)**:缺 `url`、`published_at`、平台文章 id。已提供 `publish bind`,发布动作发生时调用即可;不补历史(没有可信的发布时间来源,补就是猜)。
2. **publication → observation(通,但尚无数据)**:通道已通,`publish record` 可写入;当前 0 条是因为还没有「按发布维度」采集,下一步把 T+N 到期采集挂到调度上。
3. **article → T+N(通,但命中率 0.074%)**:历史数据能回填就回填,回填不了(无快照/无该字段)存 `NULL` 并计入 `overdue`/缺失,不伪造。要提升 on_time 命中率,需把采集从「批量任意时点」改为「按 `due_at` 到期触发」,这是下一步的数据采集工作,不是本轮的绑定逻辑问题。

## 5. 本轮未动 / 保持原样

- 评分连续化、去模板化:未动(只在 `performance_windows` 行上预留 `draft_id` / `publication_id` 供后续 join)。
- A+B 门禁:窗口集 **A 7 / B 6 / C 48 = 13/61(21.31%)`NOT_AVAILABLE`**,事实可核率 **57/61 = 0.934 MET**,`release = BLOCKED`;旧集 **17/61(27.87%)`NOT_AVAILABLE`**、事实可核率 58/61 = 0.951 —— 与本轮之前完全一致。
- 取样规则 `SEEDS` / `select_seeds` / `MAX_SEEDS_PER_SOURCE`:一字节未改。
- 只用本地模型:本轮未调用任何模型。

## 6. 复现

```powershell
$env:PYTHONPATH='D:\ttxt'
python -m ttxt publish backfill --category all --out 03_intelligence/publication_chain/backfill-dryrun.json   # 只报告
python -m ttxt publish backfill --category all --apply --out 03_intelligence/publication_chain/backfill-apply.json
python -m ttxt publish usability --category all --out 03_intelligence/publication_chain/usability.json
python -m ttxt publish report --category technology --subject article --limit 5
```

产物:`03_intelligence/publication_chain/{backfill-20261119-dryrun,backfill-20261119-apply,usability-20261119,report-technology-on-time-samples}.json`。
测试:`10_tests/test_r41_publication_chain.py`(10 项)。
