# P13 批次1 黄金核验集 v2 状态(由 scripts/golden_v2_harness.py 生成)

- 生成时间:2026-11-19T02:03:38+08:00
- 卡片总数(候选):61
- **已人工裁定:0**;已 AI 辅助初审:0
- human_adjudicated_count = 真人逐张裁定;ai_adjudicated_count = AI 辅助初审。两个口径分开报数:AI 数量再多也不构成人工裁定,AI 口径达标 ≠ 人工达标 ≠ L4 通过。
- **质量结论(人工口径,唯一决定退出码):NOT_AVAILABLE** —— 门禁未通过,禁止宣称「L4 事实核验达标」
- 质量结论(AI 口径,仅供参考):NOT_AVAILABLE
- 人工口径门禁未满足项:
  - 已人工裁定 0 < 50
  - 类目有效卡片不足: {} 缺类目 ['finance', 'health', 'technology'](每类目需 ≥10)
  - A/B 级独立证据占比 None < 0.5
- AI 口径门禁未满足项:
  - AI 口径 0 条:未找到 sidecar 或尚未做 AI 初审
  - 已 AI 初审 0 < 50
  - AI 口径类目覆盖不足: {} 缺类目 ['finance', 'health', 'technology'](每类目需 ≥10)

## 1. 规则吻合度(不是质量分数)
- 机器结论与构造预期一致:57/61(rate=0.934) —— 机器结论与构造预期一致率 —— 仅规则吻合度,不是质量分数,不得用于 L4 验收

## 2. 机器 vs 人工(唯一的质量口径)
- 已人工裁定:0;通过 0 / 驳回 0;一致率 None
- 负样本(已裁定 0):误判 verified 0;**false_accept_rate = None**
- 正样本(已裁定 0):误判 unverified/no_source 0;false_reject_rate = None

## 2b. 机器 vs AI 辅助初审(参考口径,不是质量分数)
- 已 AI 初审:0;裁定者 None
- 按纸面标签记的 ①:{};按今日机器标签重述:{}
- 判据已过期(纸面标签≠今日标签)的卡片:无
- sidecar 记录的机器标签与当前不一致:无
- 负样本(已 AI 初审 0):机器 verified 而 AI 认可 0;false_accept_rate = None
- 正样本(已 AI 初审 0):机器对不上/没出处 而 AI 认可 0;false_reject_rate = None
- 走「转述」通道的卡片 0 张,AI 判机器贴得不贴切 0 张(T-15 的线索)
- ② 原句支撑分布:{}
- AI 辅助初审口径:只用于定位机器的可疑误判与回归样本,**不是质量分数、不是人工裁定、不得用于 L4 验收**

## 3. 来源独立性
- 声明证据级分布:{'C': 48, 'B': 6, 'A': 7}
- 人工确认证据级分布:{};AI 确认(AI 口径):{}
- 已人工裁定卡片中 A/B 级占比:None;AI 口径:None
- 成对独立机构占比(pair_independence_share,只读观测、不设线):0.0084(3/359) —— 同题候选中独立机构对占比;只读观测,不参与门禁

## 4. 覆盖分布
- 类目(全部/已人工裁定/已 AI 初审):{'technology': 25, 'finance': 19, 'health': 17} / {} / {}
- kind:{'positive': 11, 'negative_perturbed': 16, 'negative_fabricated_causal': 10, 'negative_wrong_entity': 9, 'opinion_attributed': 9, 'unrelated': 6}
- fact_type:{'quote': 26, 'number': 10, 'date': 6, 'causal': 10, 'org': 3, 'person': 6}

## 5. 人工裁定方法
- 逐条回复 `v2xNN: 通过` 或 `v2xNN: 驳回`;需要修正证据级时回复 `v2xNN: 证据级=B`。
- 「驳回」= 认为机器结论错误;只有 `human_adjudicated=true` 的卡片进入 §2 的质量口径。
- AI 辅助初审结果只进 sidecar(`*_ai_adjudication.jsonl`)的 `ai_*` 字段,**永远不得**写进卡片文件的 `human_*` 字段。
