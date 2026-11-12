# P3 验收报告 — 数据库 / 清洗 / 去重聚类

- 阶段:P3
- 日期:2026-11-12
- 结论:**通过**(25 项测试全绿;清洗 33 篇、聚类幂等;真实文章"微改转载"实验命中近重复;doctor 10/10)

## 一、事实(已完成内容)

### 1. Schema v2(articles.db,幂等迁移)
- 新增列:`cleaned_content`、`text_length`、`simhash`、`cluster_id`、`cleaned_at`、`clean_version`
- 新增表:`article_clusters`(cluster_id, category, representative_id, member_count, created_at, updated_at)
- 迁移记录:`articles_meta.schema_version = p3-v1`;重复执行不重复加列(测试覆盖)
- 原始 `content` 保留,清洗结果单独存 `cleaned_content`,不覆盖原始数据

### 2. 新增模块(`ttxt/crawler/`)
| 模块 | 职责 |
|---|---|
| `timeparse.py` | ISO / RFC 2822 / 中文日期 / 10-13 位时间戳归一化为带时区 ISO;缺年份拒绝猜测 |
| `cleaner.py` | 空白归一化 + 保守剔除栏目噪声行(上一篇/责任编辑/关注公众号等,兼容全角冒号) |
| `simhash.py` | 64 位 SimHash:中文双字 + 英文/数字词,TF 加权,Hamming 距离 |
| `clusters.py` | 并查集聚类,阈值 3,长度比 ≤2.5 防误合;代表文章取最小 id |
| `pipeline.py` | 清洗 → 时间归一化 → 聚类,幂等 |

### 3. CLI 新增
- `ttxt clean --category <slug|all>`:输出每类目文章数、聚类数、近重复对数、单例数、时间归一化数

### 4. 实测结果
- demo 库:33 篇清洗完成,33 个聚类(均为单例,符合演示语料互不重复的事实),`clean_version=p3-v1`
- 幂等:二次运行聚类数不变、时间归一化计数归零
- **真实语料近重复实验**:取少数派真实文章 + 追加句号的"转载副本"(内容哈希不同、精确去重不会命中)→ 模拟转载 → 聚类为 **1 簇 / 2 成员**,`near_duplicate_pairs=1` → simhash 召回有效
- 类目隔离:两个独立 articles.db 分别聚类,互不影响(测试覆盖)

## 二、发现的问题与根因

1. **聚类未合并近重复(首轮)**
   - 现象:`test_pipeline_clusters` 期望 2 簇,实际 3 簇。
   - 根因:`cleaned_content` 已包含标题;聚类文本又拼接了一次标题,使同一文章与"微改副本"的 simhash 差异被放大(Hamming 超过阈值 3)。
   - 修改:`clusters.text_of` 优先使用 `cleaned_content`,仅在缺失时回退 `title + content`;修复后近重复正常合并。
2. **噪声行清洗未生效**
   - 现象:`上一篇：某文章`、`责任编辑：张三` 未被剔除。
   - 根因:正则字符类只含 ASCII 冒号,正文使用全角冒号(`U+FF1A`)。
   - 修改:改用显式 `[:\uFF1A]`;补测通过。

## 三、测试结果

| 检查 | 结果 |
|---|---|
| `ttxt selftest` | **25 passed**(P1 10 + P2 9 + P3 6,0.95 s) |
| 覆盖项 | 时间归一化 5 种格式、噪声行剔除、simhash 近重复/无关文本、迁移幂等、聚类合并与重跑幂等、类目隔离 |
| `ttxt clean --category demo` | 33 篇 → 33 簇、0 近重复对(演示语料本身互异) |
| 真实语料近重复实验 | 2 篇 → 1 簇/2 成员 ✓ |
| `ttxt doctor` | 10 PASS / 0 FAIL |

## 四、磁盘变化

- 项目总计 **0.358 GB / 20 GB**;categories 276 KB(demo 库 + 5 个空类目库)
- D: 空闲 **109.6 GB**

## 五、剩余阻塞与下一阶段

- 真实来源清单仍待你提供;不影响 P4 使用 demo 数据构建分析框架。
- P4:爆款分析(缺指标时强制"数据缺失"置信度)、关键词效果评分与反哺候选、SQLite FTS5 类目 RAG、知识资产沉淀。

## 六、回滚位置

- `D:\ttxt\11_backups\P3-20261112-123333.zip`
- 上一回滚点:`P2-20261112-123059.zip`、`P1-20261112-122441.zip`
