# R64：素材主题相关度闸 + 数据驱动选题

日期：2026-11-21　范围：取材链（rag → 选材 → 门禁）、选题层（新增 `analysis/topics.py` + `ttxt topics`）
结论先说：**R63 的 0/10 不是模型能力问题，是「人工拿整条爆款标题出题 + 检索召回离题稿」两件事叠加**。
本轮把这两件事分别钉死：①取材链不再让离题稿充当引句；②选题由库里数据提出，并在烧模型之前先自评语料支持度。

## 1. 实测根因（生产 social，快照只读）

R63 的 10 主题烟测 0/10 写成；同期 28 个候选（含整条爆款标题）里 11 个被取材门禁拦下，其中 **10 个是整条标题**
（FTS 按标题 token 做 AND ⇒ 只召回它自己：相关全文 1、独立来源 1）。反过来，**2 条整标题靠标题里的常见二字组
蒙过了门禁**（相关全文 15/15、离题剔除 0 —— 检索假象，不是语料真撑得起）。同一把尺子对整标题既误杀又误放。

「库里真实关键词」的 A/B（`07_tmp/r64_snap/gate_ab.json`，5 类目 48 个 active keyword，同一候选池）：

| 口径 | 放行 | 拦下 |
|---|---|---|
| R64-1 之前（`_rank_materials` 不带 topic） | **48/48** | 0 |
| R64-1 之后（现网取材链，带 topic 相关度过滤） | 39 | **9**（美女/财报/神器/降价/科学家/特朗普/健康/多人/文明） |

- 检索窗口内**离题候选合计剔除 220 条**（窗口上限 15×48=720）≈ 召回稿的 31% 与主题无关；
- **只有 15/48 主题零剔除**；**16/48 主题的「相关全文」被砍掉一半以上**（例：警方 15→6、特朗普 14→2、多人 15→2、开源 12→6）；
- 也就是说：门禁此前从没挡住「离题稿凑数」，19% 的"可写"主题是靠离题稿撑起来的。

## 2. R64-1 素材主题相关度闸（已进现网取材链）

- `analysis/rag.py::topic_relevance`：非重叠计数，判据 `标题命中≥1 且 每千字命中≥2.0，或 每千字命中≥8.0`；
  主题切不出 token ⇒ 判相关并记 `skipped`（不猜）。
- 接线点唯一：`writing/workflow.py::_rank_materials(matches, db_path, topic=…)`（写作与 `precheck.precheck_topic`
  同一条链），统计进 `stats["relevance"]{rule,tokens,searched,kept,dropped,dropped_full,off_topic_samples}`。
- 门禁文案改为可归因：`取材密度不足:候选里全文文章 N 篇 < 6(短稿撑不起引句);其中 M 篇与主题不相关已剔除
  (语料缺该主题素材,补采集而非放宽门槛)`。
- **没有新增或放宽任何阈值**：`material_min_full=6`、`material_min_sources=3` 仍只从 `config.writing` 读。

## 3. R64-2 数据驱动选题（`ttxt topics`，只读）

候选主题**只来自库里真实数据**：`keyword_performance` 战绩榜（score 降序、带 `origin` 溯源：`seed`=人给的、
`mined`=系统从已标定爆款标题挖的词）+ `--topic` 人工补题。爆款标题**不进候选池**，只作参考线索
（`used_for="reference_only_not_topic"`，并指回既有挖词通道 `analyze → mine_keywords`）。

支持度判定**复用写作同一条链、同一个门禁实现**（`rag.search(MATERIAL_POOL_LIMIT) → _rank_materials(topic=…)
→ _material_gate`），先声明排序规则：放行优先 ⇒ `kept_full` ⇒ `distinct_sources` ⇒ `kept` ⇒ 来源战绩分。
被拦候选只出 `ttxt crawl-plan`（干跑）建议，**绝不自动入队、绝不触发调度器**。

逐类目实跑（快照只读，`07_tmp/r64_snap/evidence/*.json`）：

| 类目 | 候选 | 可写 | 被拦 | 被拦题 |
|---|---|---|---|---|
| social | 13（10 mined+3 seed） | 12 | 1 | 美女 |
| finance | 14 | 12 | 2 | 神器、财报 |
| technology | 10 | 7 | 3 | 科学家、特朗普、降价 |
| health | 6 | 4 | 2 | 健康、多人 |
| history | 5 | 4 | 1 | 文明 |
| demo | 0 | 0 | 0 | —（无 active 关键词，如实报空不编主题） |

对比 R63 的人工出题 0/10：**库里出的题 39/48 当场可写**，且每道题的支持度数字（相关全文/独立来源/离题剔除）都可核对。

审计口径补两条：`--sources` 写错（或把爆款标题当来源）会在行首点名「未知候选来源」并给可用来源，
结论段改成「命令行写错，不是『库里没有主题』」；证据文件里同时留 `requested_sources`/`unknown_sources`。

## 4. 测试与验证

- 新增 `10_tests/test_p42_data_driven_topic_selection.py`（18 用例）：阈值来自配置、与 `write_article` 判定逐字相同、
  冷主题/离题召回被拦并计数、战绩榜 score 降序 + `origin` 溯源、可写题排在"高分写不出"之前、
  爆款标题只作参考（`measured/measured_reads` + `verified_viral/potential`，`normal/low_quality` 逐个计数排除，
  非测得档不当证据）、太短/太长标题跳过必计数、整标题进榜必被拦（生产形状回归）、
  只读红线（全表行数指纹前后一致 + `writing_history=0`）、CLI 退出码 0/3 与文案、`--no-refs`、未知来源点名。
- `test_p41_material_topic_relevance.py` 覆盖相关度判据本身。
- 定向 4 文件 62 passed；**全量 `10_tests` 基线：1092 passed（331.57s，exit 0）**。
  首跑曾 1090 passed / 1 failed / 1 error：失败项是仓库根残留测试垃圾 `D:/ttxt/tmpq092s9ax`
  （内含合成 `fewshot_library.json`，18:34 由一次性探针留下）触发
  `test_p38_repo_root_has_no_stray_temp_dirs`；该垃圾目录已清理（生产库 `00_control/fewshot_library.json`
  11854 字节、`01_categories/`、`资产库/`、`11_backups/` 均未动），error 项（`test_r61_fewshot.py`
  单条）定向复跑未复现。清理后全量复跑即上面的全绿基线。
- 全程零模型调用（选题层红线：不调模型、不写业务表、不产草稿），生产 `01_categories/**` 零写入
  （所有探针与烟测走 `07_tmp/r64_snap` 的一致性快照）。

## 5. 仍未闭合的缺口（下一步）

1. **候选广度**：每类目 active keyword 只有 5–14 个，`mined` 稳定卡在 10（=`MINE_TOP_N`）——
   挖词每轮只出 10 个且不跨轮累积 ⇒ 主题池上不去。这是下一个要动的地方（挖词批量/去重与 `analyze` 频率）。
2. **可写主题 → 写作**：`ttxt topics` 只给榜和数字，写作仍要人跑 `ttxt write --topic …`（红线：调度器绝不触发写作）。
   要不要做"人一键确认"的衔接，需要用户拍板。
3. 9 个翻转主题是**语料缺口** ⇒ 按建议补采集（crawl-plan 干跑）后应复检，而不是放宽门禁。
