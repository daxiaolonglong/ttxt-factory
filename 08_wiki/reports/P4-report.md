# P4 验收报告 — 关键词 / 爆款标签 / 类目 RAG / 知识资产

- 阶段:P4
- 日期:2026-11-12
- 结论:**通过**(33 项测试全绿;关键词反哺候选、数据缺失标签、FTS5 检索、知识资产均按规则产出;doctor 10/10)

## 一、事实(已完成内容)

### 1. Schema v3(p4-v1,幂等迁移)
新增表:`keywords`、`keyword_performance`、`viral_labels`、`article_metrics`、`metric_snapshots`、`knowledge_assets`、`articles_fts`(FTS5)。

### 2. 新增模块(`ttxt/analysis/`)
| 模块 | 职责 |
|---|---|
| `rag.py` | 中文双字词 FTS5 索引与检索;**AND 优先**,短查询(≤3 双字词)严格不回退,长查询才 OR 回退保召回 |
| `viral.py` | 爆款标签:无曝光数据一律 `unscored + missing_data`;有曝光按阅读/收藏/点赞/评论率加权评分(0.15 验证爆款 / 0.08 潜力 / 0.02 普通 / 以下低质) |
| `keywords.py` | 种子关键词维护、按真实语料覆盖率评分、零覆盖关键词生成反哺候选 |
| `knowledge.py` | 将真实统计沉淀为 `corpus_stats` 与 `keyword_gap` 知识资产(带证据 JSON 与支持计数) |
| `analyze.py` | 编排:迁移 → 关键词 → 爆款标签 → 知识资产 → RAG 索引 |

### 3. CLI 新增
- `ttxt analyze --category <slug|all>`:输出关键词/标签/知识/索引统计
- `ttxt search --category <slug> --query "..."`:类目内 RAG 检索(bm25 排序)

### 4. demo 实测结果
| 项目 | 结果 |
|---|---|
| 关键词 | 4 个种子,全部零覆盖 → **4 个反哺候选**(下一轮采集应覆盖) |
| 爆款标签 | 33 篇全部 `unscored / missing_data`(未接入授权指标,**不臆测**) |
| 知识资产 | `corpus_stats`(33 篇、均 214 字、3 来源、33 聚类)+ `keyword_gap`(4 个零覆盖) |
| RAG 索引 | 33 行 FTS5 索引 |
| 检索"智能" | 命中 1 篇智能音箱文章 ✓ |
| 检索"城市" | 命中 1 篇城市漫步文章 ✓ |
| 检索"人工智能" | **0 命中**(语料确无该连续子串;严格语义,不误报"智能") |

## 二、发现的问题与根因

1. **FTS OR 误报**
   - 现象:检索"人工智能"命中仅含"智能"的文章,而关键词评分(严格子串)为 0,口径不一致。
   - 根因:双字词索引用 OR 连接,任一 2-gram 命中即返回,短查询语义被放宽。
   - 修改:改为 **AND 优先**;仅当双字词数 >3 且 AND 无结果时才 OR 回退;补"严格短查询"与"OR 回退"两个测试。实测"人工智能"→0 命中、"智能"→1 命中、"城市"→1 命中。
2. **P3 测试的 schema 版本断言**
   - 现象:P4 迁移后版本号变为 `p4-v1`,P3 测试仍断言 `p3-v1`。
   - 修改:统一断言 `store.SCHEMA_VERSION`,版本升级不再破坏测试。

## 三、测试结果

| 检查 | 结果 |
|---|---|
| `ttxt selftest` | **33 passed**(P1 10 + P2 9 + P3 6 + P4 8,1.84 s) |
| 覆盖项 | FTS5 检索/严格短查询/OR 回退、关键词评分与反哺候选、缺数据标签、有指标标签(验证爆款)、分析幂等、检索类目隔离 |
| `ttxt analyze --category demo` | 4 关键词 / 4 候选 / 33 missing_data / 2 资产 / 33 索引 |
| `ttxt search` 三种查询 | 语义正确(见上表) |
| `ttxt doctor` | 10 PASS / 0 FAIL |

## 四、磁盘变化

- 项目总计 **0.358 GB / 20 GB**;categories 376 KB(demo 库含 FTS 索引)
- D: 空闲 **109.6 GB**

## 五、剩余阻塞与下一阶段

- 真实来源清单与**授权指标数据**(头条后台导出/合法接口)待提供;`article_metrics`/`metric_snapshots` 表已就绪,接入后爆款标签自动从 `missing_data` 升级为实测。
- P5:Strata 单模型串行闸门(SQLite 持久队列 + 文件锁 + `/health` 等待),用真实模型任务证明零并发。

## 六、回滚位置

- `D:\ttxt\11_backups\P4-20261112-123611.zip`
- 历史回滚点:P3、P2、P1 快照
