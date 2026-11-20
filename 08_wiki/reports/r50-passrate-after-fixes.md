# R50 修复后同口径重测:通过率改前/改后对比

日期:2026-11-20(探针于 2026-11-19 19:38 启动,21:2x 收口,18 run 全部完成,`summary` 已写出)。

## 1. 口径

- 与 R49 **同一工具、同一批 6 选题 × 3 轮 = 18 run**:`scripts/pass_rate_probe.py`,`template_decheck=False`,类目库 sqlite `backup` 快照全轮共用,正式库零写入;分母一致 18 vs 18。
- 与 R49 唯一差异:**代码基线含本轮两项修复**(commit 072358c)——
  1. 评审软意见误判死修复:`parse_review` 新增 `_soft_concession`,让步/否定半句里的 critical 词降级回 issues,判死必须是转折后半句的真负面断言(回归测试锁住 R49 两句原话);
  2. 写作侧绑定约束收紧:`draft_prompt` 第 7 条 + `claims_prompt` 重写——**一条 claim 只承载一个事实锚点、quote 逐字整句照抄**。
- **门禁阈值零改动**:`FACT_CHECKABILITY_MIN=0.80`、gate_min_score、QUOTE_MIN_OVERLAP=0.34、±1 句窗口、取材门禁、取样规则、`viral.py`/`prediction.py` 一字节未动。
- 读数文件:`03_intelligence/pass_rate_probe-20261119-r50.json`。
- 运维如实记:supervisor 常驻与探针争抢 model_gate 串行锁,个别 run 显著变慢(人工智能 r3 3271.6s、r2 2350.7s;R49 最长 407.6s)。不影响判定,影响墙钟时长。

## 2. 分母一致对比(n=18 vs n=18)

**通过率:R49 1/18 = 5.6% → R50 5/18 = 27.8%(读数上升)。**

| primary | R49 | 占比 | R50 | 占比 |
|---|---|---|---|---|
| fact.binding | 6 | 33.3% | 8 | 44.4% |
| fact.anchor_missing | 5 | 27.8% | 1 | 5.6% |
| material.insufficient | 3 | 16.7% | 3 | 16.7% |
| other | 2 | 11.1% | 1 | 5.6% |
| gate.structure_truncated | 1 | 5.6% | 0 | 0 |
| passed | 1 | 5.6% | 5 | 27.8% |

claim 级规则命中(一 run 可多条):**锚点缺失 37→18(-51%)、引句未绑定(overlap<0.34)17→14、伪造归属 1→0**。

分类目:technology 0/9→2/9、finance 1/3→2/3、health 0/3→1/3、social 0/3→0/3。

R50 passed 的 5 个 run:人工智能 r2(score 0.65)、财报 r2(0.95)、财报 r3(0.85)、神舟 r2(0.95)、睡眠 r3(0.85);**五个全部 fc=1.0**。

## 3. 与修复方向一致的机理证据

- 修 2(一 claim 一锚点 + 整句照抄)直接打击引句未绑定与锚点缺失两个维度——实测锚点缺失命中减半(37→18)、伪造归属清零;anchor_missing 作为 primary 从 5 降到 1(绑定失败的形状明显好转,fact 家族 binding+anchor 合计 11→9)。
- passed run 的 evidence_brief 显示逐字整句绑定普遍成形(如财报 r2:「其股票在2026年11月16日、11月17日、11月18日连续三个交易日内收盘价格涨幅偏离值累计超过20%」整句照抄、锚点全命中;睡眠 r3:「世卫组织早已调研证实…60%取决于日常生活习惯」first_hand=true 带锚点)。
- 修 1(软意见降级)命中 other 类:2→1。R50 剩余的 other 1 例是**独立评审通道判语**「不可直接发布,建议重写」(人工智能 r3,score 0.95、全部 claim first_hand),与 CRITICAL_HINTS 子串升级无关,不是软意见误伤回归。

## 4. 未改善/须如实记的一面

- **fact.binding 作为 primary 反而 6→8(44.4%),仍是最大阻断块**。形状好转不等于消失:神舟 r1/r3 各差 1 条引句未绑定(「使其在轨总时长满一年」找不到逐字原文)、睡眠 r1 一次 9 条 critical(模型把自媒体断言当事实陈述,核验 unverified——**这类拦截是对的**)、social r2 出现「63岁 vs 证据链仅'举例:62岁'」——模型把举例值改写成最终值,门禁拦截正确。
- **取材天花板原样**:双11成绩单 3/3 仍 0.0s 早退(全文文章 1<6、独立来源 1<3),三轮读数与 R49 完全一致——这 3 run 不是任何修复能触及的,通过率上限受素材面约束。
- **social 0/3 原样**(延迟退休:55/50 岁口径、53岁AI绘图案例均 unverified)。
- **单轮随机性仍在**:R49 唯一 passed 是财报 r1,R50 财报 r1 反而失败(fc=0.6)、由 r2/r3 补上 pass/fail——同选题同批次跨轮翻转,与 R48 的观察一致。

## 5. 判定(如实)

**通过率读数上升:1/18(5.6%)→ 5/18(27.8%),方向与两项修复一致,且有规则命中减半、伪造归属清零、passed 全 fc=1.0 的机理证据支撑——不是单次成功当证据。**

但**不得宣称决定性提升**:n=18 单探针,Wilson 95% 区间 R49 [0.010, 0.258] 与 R50 [0.125, 0.509] **重叠**;同选题跨轮 pass/fail 仍翻转;最大阻断 fact.binding 计数不降。正确表述:**修复起效的 directional evidence + 机理证据,统计上未达显著**。下一轮若要确证,需同批次加轮次(如 5 轮)或与继续恶化项做逐项对账。

## 6. 验收与红线

- 定向测试 **42 passed**(新增 r50 回归 5 项 + r48 15 + r40 6 + p6 + p39;跑前确认 model_gate 锁空闲);本轮测量阶段生产代码零新增改动,全量测试留阶段收口(用户明示本轮不需要全量)。
- 红线复核:门禁判定/全部阈值/±1 句窗口/0.34 重叠率/取材门禁/取样规则/`viral.py`/`prediction.py` 零改动;探针只读,正式库零写入。

**产物**:`03_intelligence/pass_rate_probe-20261119-r50.json`、本报告;代码改动见 commit 072358c(`ttxt/writing/workflow.py`、`ttxt/writing/prompts.py`、`10_tests/test_r50_review_soft_critical.py`)。
