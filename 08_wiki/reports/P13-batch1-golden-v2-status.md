# P13 批次1 黄金核验集 v2 状态(由 scripts/golden_v2_harness.py 生成)

- 生成时间:2026-11-13T10:33:15+08:00
- 卡片总数(候选):61;**已人工裁定:0**
- **质量结论:NOT_AVAILABLE** —— 门禁未通过,禁止宣称「L4 事实核验达标」
- 门禁未满足项:
  - 已人工裁定 0 < 50
  - 类目有效卡片不足: {} 缺类目 ['finance', 'health', 'technology'](每类目需 ≥10)
  - A/B 级独立证据占比 None < 0.5

## 1. 规则吻合度(不是质量分数)
- 机器结论与构造预期一致:55/61(rate=0.902) —— 机器结论与构造预期一致率 —— 仅规则吻合度,不是质量分数,不得用于 L4 验收

## 2. 机器 vs 人工(唯一的质量口径)
- 已人工裁定:0;通过 0 / 驳回 0;一致率 None
- 负样本(已裁定 0):误判 verified 0;**false_accept_rate = None**
- 正样本(已裁定 0):误判 unverified/no_source 0;false_reject_rate = None

## 3. 来源独立性
- 声明证据级分布:{'C': 44, 'A': 17}
- 人工确认证据级分布:{}
- 已裁定卡片中 A/B 级占比:None

## 4. 覆盖分布
- 类目(全部/已裁定):{'technology': 25, 'finance': 19, 'health': 17} / {}
- kind:{'positive': 10, 'negative_perturbed': 16, 'negative_fabricated_causal': 10, 'opinion_attributed': 10, 'negative_wrong_entity': 9, 'unrelated': 6}
- fact_type:{'quote': 26, 'number': 9, 'date': 7, 'causal': 10, 'person': 4, 'org': 5}

## 5. 人工裁定方法
- 逐条回复 `v2xNN: 通过` 或 `v2xNN: 驳回`;需要修正证据级时回复 `v2xNN: 证据级=B`。
- 「驳回」= 认为机器结论错误;只有 `human_adjudicated=true` 的卡片进入 §2 的质量口径。
- 待裁定清单见同目录人工复核表(由 build_golden_set_v2.py 生成)。
