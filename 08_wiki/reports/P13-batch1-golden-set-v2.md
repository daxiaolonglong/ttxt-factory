# P13 批次1:黄金核验集 v2 建设状态

生成时间:2026-11-13 10:28(+08:00)。本文所有数字均为本机本次实跑所得;未在此列出的结论一律视为未执行。
状态:**批次1 未完成**(`quality_claim = NOT_AVAILABLE`)。

## 1. 一句话结论

v1 那 57 条「机器预裁定」卡片已不能作为质量证据;v2 已建成 **61 张候选卡片 + 一套不可自证的评测口径**,但
**已人工裁定卡片 = 0**,因此 L4 质量结论必须写作 `NOT_AVAILABLE`,不能给出准确率、误放行率或达标判定。

## 2. 本批实际落地的东西(全部本机验证)

| 产物 | 路径 | 实测 |
|---|---|---|
| v2 卡片规范 | [golden_set_v2.schema.md](../golden/golden_set_v2.schema.md) | 三项判定分离 + 证据分级 A–D + 硬门禁 |
| 生成器 | [build_golden_set_v2.py](../../scripts/build_golden_set_v2.py) | 产出 61 张候选卡片 |
| 评测器(唯一口径) | [golden_v2_harness.py](../../scripts/golden_v2_harness.py) | 模式校验 0 错;退出码 0=门禁通过 / 1=模式校验错误 / **2=质量 NOT_AVAILABLE(本次实测 2)** |
| 候选集 | [golden_set_v2.jsonl](../../golden/golden_set_v2.jsonl) | 61 行,schema 校验 `errors: []` |
| 人工裁定工作表 | [P13-batch1-golden-v2-review.md](P13-batch1-golden-v2-review.md) | 61 条待人工三问 |
| 机器状态快照 | [P13-batch1-golden-v2-status.md](P13-batch1-golden-v2-status.md) | 由 harness 生成,10:33:15 |
| 回归测试 | [test_p13_golden_v2.py](../../10_tests/test_p13_golden_v2.py) | 13 条用例;`10_tests` 全量 **174 passed in 23.75s**(10:34:22–10:34:47 本机实跑;基线由 161 变为 174) |

**为什么必须存在消费方**:v1 的 `golden_set_v1.json` 从未被任何代码读取(全仓 grep 无引用),所以「有黄金集」只是文件存在。
v2 由 harness + 12 条测试真实消费;门禁测试会在人工裁定不足 50 条时**主动拒绝**给出质量结论。

## 3. 三项判定分离(核心设计)

一张卡片记录三个互不推断的判断,harness 不把任何机器结论写进卡片:

| 判定 | 谁负责 | 字段 | 不能推断 |
|---|---|---|---|
| ① 草稿与来源文本是否一致 | 规则(每次重算) | 运行时 `verify_claim` + `quote_verbatim`,不落盘 | ⇏ 事实为真 |
| ② 事实是否成立 | **人工** | `human_adjudicated` / `human_verdict` / `human_fact_support` | ⇏ 来源可靠 |
| ③ 来源等级与独立性 | 分级 + 人工确认 | `source_tier` / `corroborating_urls` / `human_confirms_tier` | ⇏ 事实成立 |

反自证设计:harness 每次用 `verifier.verify_claim` 现算 ①;卡片里若出现机器结论字段会被模式校验拒绝。
v1 之所以虚高,正是把 ① 当成了质量分数:`g002`「现在18家大国企几乎全军覆没,痛心」、`g003`「打脸了,72小时卖了10000台」、
`g012`「兄弟们,今天是双 11 最后一天了」都被判 `verified`——只因为原文里有这句话。

## 4. 候选集构成(61 张)

- 类目:technology 25 / finance 19 / health 17(v1:54/3/0,health 完全缺失)
- kind:`positive` 10、`negative_perturbed` 16、`negative_fabricated_causal` 10、`negative_wrong_entity` 9、
  `opinion_attributed` 10、`unrelated` 6 —— 六类均有,负样本 41/61 = 67.2%
- fact_type:quote 26 / causal 10 / number 9 / date 7 / org 5 / person 4
- 证据分级:**A 17 / C 44 / B 0 / D 0**

## 5. 证据分级的真实现状(不能粉饰)

- 17 张 A 级全部来自 `who_cn_news`,URL 为 who.int 一手稿(已核对前 5 条 URL);其余 44 张一律 **C 级起步**——
  头条/财富号等聚合稿只能证明「草稿与来源文本一致」,不构成事实为真的证据。
- **B/D 级 = 0 张,独立第二来源 0 条**。`corroborating_urls` 目前为空;`human_confirms_tier` 全空。
- 我尝试补 12 张独立来源卡片:子代理两次声称在写文件,但
  `07_tmp/golden_v2_independent_candidates.json` 始终不存在(两次核实均为 `INDEPENDENT FILE MISSING`)。
  **本批没有任何独立来源卡片入库**,不做任何「已补充」的暗示。补齐需要你批准一次明确的联网检索任务,或由你提供来源/指标导出。
- 语料侧连带问题:`articles.credibility` 对 who.int 与自媒体一律写 `unverified`,证据分级没有接到库字段上(观察项,未改动)。

## 6. `machine_vs_construction = 55/61 = 0.902` 只说明规则吻合度

该数字是「卡片构造预期」与「规则现算结论」的一致率,**不是质量分数**;harness 输出里显式标注了这一点,
门禁也禁止把它用于 L4 验收。人工口径仍为空:`accuracy / false_accept_rate / false_reject_rate = null`。

## 7. 6 条不一致的根因(这批真正的产出:批次2 的规格输入)

| 证据卡片 | 现象 | 根因 | 批次2 规格 |
|---|---|---|---|
| `v2h01`(A 级正样本) | WHO「专家小组**建议**纳入两款埃博拉候选疫苗」被判 `opinion`,完全跳过事实门禁 | `OPINION_RE` 含 建议/预计/或将/可能/应该,机构立场陈述也是可核验事实 | 观点路由须按「归属+谓语」限定,不得凭子串整句豁免 |
| `v2t14` | 捏造日期句「**预计**3126年11月15日后有货」路由为 `opinion` → **日期造假绕过门禁** | 同上(豁免路径由一个副词打开) | 观点句中的数字/日期仍需锚定核验 |
| `v2h02` / `v2h03` / `v2h16` | 捏造因果、张冠李戴、无关来源卡片全部因原文含「建议」被路由为 `opinion` | 同上 | 同上;这三类是负样本主战场 |
| `v2t13` | 数字扰动后仍判 `verified`(「开售仅10天」+「10000台」) | 数字校验是全篇集合包含,不是绑定引句窗口内比对 | 数字/日期必须在**绑定引句窗口**内比对;跨段共现不得放行 |

结论:6 条不一致中 5 条同一根因(观点路由过宽),1 条为数字锚定过松。两条都是**可绕过门禁的真实路径**,
必须在批次2 修复后才能谈 L4;本批不改 `verifier.py`(批次2 的范围)。

## 8. 语料可用性阻断(实测,决定 health 能否验收)

- **health 类目 25 篇全部是 RSS 一句话摘要:正文长度 35–142 字**(min 35 / max 142,来源只有 `who_cn_news`)。
  这就是 health 草稿反复 `no_source` 被驳回的直接原因;以当前语料 health 不可能通过 L4 写作验收。
- **清洗缺口:财经 31/50、科技 30/137 篇文章的 `cleaned_content` 仍含 HTML 片段**
  (`-- EM_StockImg_Start --><p style=...`、`height:8px;` 这类残片),会直接进入引句绑定与锚点抽取。
  生成器已用「必须含 ≥6 连续汉字」+ HTML 黑名单过滤,但库里的脏数据仍在(本批不改数据)。
- FTS 覆盖缺口(批次0 实测):科技 137 篇仅 62 条入索引(缺 75 条 `toutiao_tech`),财经缺 3 条;
  用缺索引文章标题构造的 4 次检索,目标文章召回 **4/4 全部 False**。

## 9. 人工裁定的完成条件(未满足前禁止宣称质量达标)

工作表每条卡片需回答三问:① 草稿与来源是否一致(规则已给出,人工核对即可);② 该事实是否成立
(`通过 / 驳回 / 证据不足`);③ 来源分级是否接受、是否存在独立第二来源。

硬门禁(`scripts/golden_v2_harness.py` 内常量):已人工裁定 ≥50 条、三类目各 ≥10 条、
A+B 级占比 ≥50%、负样本占比 ≥30%、每 kind ≥3 条。任一不满足 → `quality_claim = NOT_AVAILABLE`,
并在 `gate_failures` 列明原因。当前三条失败:`已人工裁定 0 < 50`、`每类目有效卡片不足`、`A/B 级独立证据占比 None < 0.5`。
门禁同时是**机器可判定**的:harness 退出码 0/1/2 分别对应 通过 / 模式校验错误 / 质量 NOT_AVAILABLE(本次实测 2),
行为由用例 `test_gate_is_machine_detectable_via_exit_code` 固定,每周巡检与 CI 都能据此判定,不依赖人工读报告。

## 10. 未闭合项

1. 批次1 未完:61 条全部待人工裁定;A/B 级独立来源卡片为 0。
2. 批次2 规格已备(见 §7 两行根因),尚未动代码。
3. **批次3 Q4/Q3 盲测无法执行**:Strata 当前只加载 `qwen3.8-flash-next-iq3_s`(`/v1/models` 只有一个模型,
   进程 PID 26032 自 11-12 19:38:59)。除非你明确解除「不动 Strata」约束,否则该批必须改为「单模型内部一致性检查」或顺延。
4. 真实指标仍为 0 行(`article_metrics` / `metric_snapshots` 各类目均 0),校准样本不足,禁止校准。
5. 浸泡:72h 与 7 天均未本机复跑,只有 P12 外部凭据,不能当作本机结论。

## 11. 结论口径(必须分开写)

- `AUTO_PASSED`:仅「规则吻合度 0.902」「测试 174 passed」「doctor 10 ok / 0 failed」这类自动事实。
- `HUMAN_APPROVED`:**无**。批次0/1 尚无人类批准记录,本批产物在提交前 `git status` 全部为未跟踪/未提交。
- 因此对外只能写:**P13 批次0 已完成(只读核实 + 基线备份),批次1 已建成候选集与评测口径,质量结论 NOT_AVAILABLE。**
