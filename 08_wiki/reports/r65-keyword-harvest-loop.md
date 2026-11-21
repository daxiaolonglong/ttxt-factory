# R65 关键词"采到没有"闭环（harvested）与定向补采退场（2026-11-21）

## 0. 一句话

`collected` 只能证明"库里出现过这个词"，证明不了"这个词把语料撑大了"。本轮新增
`keyword_performance.harvested`（该词被提出之后采集侧真的带回的标题命中），用它把挖词接进
反哺定向补采，并给"补采 3 轮仍一篇带回不了"的词一个**认账退场**（`status='exhausted'`）。
这是目标 ②（爆款→关键词→继续采集）与目标 ④（系统越来越聪明）上唯一一处**从未闭合**的断点。

## 1. 实测断点（只读生产，探针 `D:/ttxt/07_tmp/r65_harvest/harvest.json`）

5 个有挖词的类目，共 56,685 篇文章、38 个 `origin='mined'` 关键词：

| 类目 | 文章 | 挖词 | 建词后带回 0 篇 | 带回 >0 |
|---|---:|---:|---:|---:|
| social | 40,735 | 10 | **10** | 0 |
| finance | 14,087 | 10 | **9** | 1 |
| technology | 1,243 | 10 | 4 | 6 |
| history | 398 | 7 | 5 | 2 |
| health | 222 | 1 | 1 | 0 |
| 合计 | 56,685 | 38 | **29** | 9 |

典型证据（`literal_title_hits` = 库里存的 `collected`，`harvested_after_created` = 建词之后带回）：

- social `闷声发财`：标题命中 **399** 篇，建词后带回 **0** 篇；`男子` 138→0、`同居` 49→0；
- finance `女款` 57→0、`女子` 39→0、`神器` 35→0，但 `千问` 48→1；
- technology `中国` 107→**45**、`美国` 48→8、`航天` 14→4（说明"带回"是真的会发生的，不是恒 0）；
- history `毛主席` 32→**16**、`蒋介石` 15→7（同一把尺子在两类目上都能量出正反两面）。

**为什么闭环断在这里**：挖词本来就是从库里已有标题里挖出来的 ⇒ `collected` 天然 >0 ⇒
永远进不了"零覆盖 gap" ⇒ `auto_feedback` 从来没为它入过一条定向补采任务。
挖词侧的"反馈关键词继续采集"在代码里根本不存在（gap 通道只服务种子词）。
同时旧算法留下的碎片/通用词（`位女`、`钱小`、`女款`、`神器`、`司机`）长期霸占采集计划选词。

## 2. 新口径：`harvested`（只加指标，不动老口径）

- **定义**：标题命中（别名归一后）且 `articles.fetched_at > keywords.created_at` 的篇数；
  即"这个词被提出之后，采集侧真的带回来的文章"。
- **不可判定 ≠ 0**：`created_at` 或 `fetched_at` 时间读不动 ⇒ 记 `NULL`（unknown），
  **不参与**定向名单，也不参与退场判定（不拿读不动当采不到）。
- `collected / quality_articles / avg_text_length / score` 的算法、权重（0.6/0.4 与 0.3 爆款项）
  **一律未改**；`harvested` 只是旁边多的一列、旁边多的一个 `harvest` 汇总块与
  `viral.details[*].harvested_after_keyword`。
- 老库只加列：`store.migrate_articles_db` 幂等 `ALTER TABLE ADD COLUMN harvested INTEGER`，
  不触碰任何既有评分行（用例断言老行 `(12, 0.5, NULL)` 原样保留）。

## 3. 闭环改动（`ttxt/analysis/keywords.py`）

1. **定向补采名单扩容**：`wanted = gap 词 ∪ {harvested = 0 的 active 挖词}`，写进同一条
   `crawl_jobs.meta_json.keyword_filter`（runner 入库侧闸门同一套），`attempt` 计数照旧。
   gap 通道原样保留（P26/P25 用例对 `keyword_filter == gap_keywords` 的断言仍成立：
   没有挖词的类目里 wanted 就等于 gap 词）。
2. **逐词台账**：类目库 `articles_meta.dispatch_state.keyword_feedback.per_keyword.<词>`
   记 `directed_attempts`，**只有真实入队才 +1**（排队去重命中不计数，否则一轮没执行就把词判死）。
   与既有 per-source 冷却键同级并存，不改后者的形状。
3. **认账退场**：复审轮里 `harvested` 仍为 0 且 `directed_attempts ≥ MINE_FEEDBACK_MAX_ATTEMPTS(=3)`
   ⇒ `status='exhausted'`。判定发生在"能看到上一次补采结果"的那一轮，不在入队当轮提前判死。
   退场**不删**关键词行、**不删**评分行（采不动是学到的负面知识）。
4. **复活凭证只有一个**：`harvested > 0`（语料里真的因为这个词多出了文章）⇒ 自动回 `active`。
   不看尝试次数、不做时间衰减。
5. **退场的作用面**：只关采集侧。
   - `crawler/keyword_crawl.category_keywords`：`status NOT IN ('weak','exhausted')`（白烧限速预算的词不再进计划）；
   - `analysis/topics.keyword_candidates`：`status IN ('active','exhausted')` —— `exhausted` 的词
     **仍然是合法写作主题**（`闷声发财` 语料里本来就有几百篇，采不到新文章 ≠ 写不出来）。
6. **挖词退场的保护**：`mine_keywords` 的 prune 里，`harvested > 0` 的旧挖词不进 `target_ids`
   （只有被证明采得动的词能活过退场），回执新增 `proven_mined_kept`。

红线核对：未改任何既有阈值与判定（`MINE_MIN_DOCS=3`、`MINE_MIN_LIFT=2.0`、五道闸门、
`material_min_*`、verifier/review 全部未动）；冷却机制（24h→48h→96h…、`MAX=168h`、`cooldown_hours=0`
逃生阀）未动；仍**只入队不执行**；调度器仍不触发写作/发布/模型。

## 4. 真实库验证（只读生产，副本上跑实现代码）

脚本 `07_tmp/_r65_prod_verify.py`，产物 `D:/ttxt/07_tmp/r65_verify/verify.json`：
每个类目库以 `mode=ro` 打开 + `backup()` 到副本，在副本上跑 `migrate_articles_db →
score_keywords → auto_feedback`（`system_db` 也是副本，生产任务表零写入）。

- **两个独立实现读数一致**：探针 SQL 与本轮实现代码都得到 **38 挖词 / 29 个 harvested=0 / 9 个 harvested>0**；
  逐词数字一致（`闷声发财` 399→0、`中国` 107→45、`毛主席` 32→16、`千问` 48→1）。
- 生产 5 个类目库目前都**还没有** `harvested` 列（`PRAGMA table_info` 实测：7 列，无 harvested）
  ⇒ 本轮零写入生产，符合"生产类目库零写入"约束。
- 闭环真的接上了（副本实测）：
  - social：`gap=0`（旧逻辑在这里**一条任务都不会入**）⇒ 现在定向名单 10 个词
    （`闷声发财/男子/网恋/产品经理/女医生/三次创业/同居/指令/位女/钱小`），入队 1 条；
  - finance：gap=0 ⇒ 定向 9 词；history gap=0 ⇒ 定向 5 词；health gap=0 ⇒ 定向 1 词；
  - technology：gap 1（`效率工具`）+ 定向 4（`航天员/特朗普/消失/半导体`）= filter 5 词，1 条任务。
- `unknown=0`：生产时间戳全部可读，`harvested` 在生产上不存在"读不动"的空档。

## 5. 上线路径（无需人工写生产）

`analyze.run_analysis` 第一步就是 `store.migrate_articles_db`（`analysis/analyze.py:32`）
⇒ **下一次调度 analyze 自动加列并回填 harvested**，同轮 `score_keywords → auto_feedback`
就能用新指标定向补采。生产运行时（`D:/ttxt`）拉代码重启 supervisor 即生效；不需要 DB 手术。

## 6. 测试

新增 `10_tests/test_r65_harvest_loop.py`（9 用例）：迁移只加列不动行、harvested 与 collected
分开计量、时间读不动记 NULL 且不进名单、harvested=0 的挖词进定向 filter 且不挤掉 gap 通道、
去重命中不计尝试数、满 3 次退场（行与评分行都留着）、退场稳定不回拉、只有 harvested>0 能复活、
退场词离开采集计划但留在选题候选、proven 挖词活过 prune。

- 全量基线：`10_tests` **1101 passed（291.63s，exit 0）**（R64 基线 1092 + 本轮 9，零回归）。
- 生产类目库、`01_categories/`、`资产库/`、`11_backups/` 未写入、未删除。

## 7. 已知未覆盖与风险

1. **碎片词仍会先烧 3 轮预算再退场**：`位女`、`钱小`、`女款`、`神器`、`司机` 这类跨词边界/通用词
   能过五道挖词闸门（实测在 social/finance 的 mined 名单里），现在会被定向补采 3 次才判死。
   认账换的是"不靠猜就判死"，代价是 3 轮限速预算；下一轮该收窄的是挖词闸门（碎片判定
   只查"每次都被同一个字延长"，未查"左边是量词/右边是人名姓氏"这类边界证据）。
2. `harvested` 依赖 `articles.fetched_at` 可读且单调可信；来源时钟异常会把词记成 unknown（不会误杀）。
3. 退场词保留在写作候选里，`topics` 的支持度门禁会照常拦下无素材的词，但这只是"不写"，
   不是"不占候选槽"。
4. 候选广度问题（R64 §下一个缺口）未处理：`MINE_TOP_N=10` 每轮封顶、不跨轮累积。

## 8. 下一步

1. 生产拉代码 + 重启 supervisor，等一次 analyze 回填 `harvested`，读回执确认
   `filter_keywords` 里出现挖词（预期 social 10 词、finance 9 词）与逐词 `directed_attempts`。
2. 挖词闸门补"边界证据"检查（量词/姓氏/地名前后邻字），把 `位女/钱小` 类碎片在**入表前**拒掉。
3. 候选广度：`MINE_TOP_N` 与跨轮累积（keywords 表已有 `origin='mined'` 溯源，不需新表）。
