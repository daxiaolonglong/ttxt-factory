# P13 批次4:T-15 事实门禁路由漏洞修复(只收紧,不放宽)

时间:2026-11-17 18:5x(+08:00)。基线提交 `d2b4a12`。修复方案由两位 AI(chatgpt.com / chat.deepseek.com)各自独立给出,方向一致:**归属存在只是路由条件,不是通过条件**。本文件是版本化证据;实现由子代理完成,Diff 与全量测试由负责人侧本人复核后提交。

## 1. 根因(HEAD `d2b4a12` 的 file:line)

- `ttxt/writing/verifier.py:147` `is_opinion()` 只做归属/hedge **词表匹配**,从不核对那个归属是否来自来源原文;
- `ttxt/writing/verifier.py:249` 附近 `markers = is_opinion(claim)` → `if markers: credibility = "opinion"`,句子就此**离开事实通道**;
- `deterministic_findings()` 的缺维白名单只有 `numbers/dates/persons/orgs/causal`,所以这 10 张 `missing_dims` 全空、零 critical。

复现脚本 `07_tmp/t15_before_after.py`(用 `git show HEAD:` 取旧模块跑同一批卡):BEFORE = `credibility=opinion / missing_dims=[] / critical=[]`,**事实门禁根本不跑**。

## 2. 改动(生产代码 3 个文件,全部只收紧)

- `ttxt/writing/verifier.py`:`SELF_ATTRIBUTION_RE`(第一人称自述豁免存在性核验 —— 出处就是作者本人)、`attribution_markers()`、**`attribution_check()`(确定性逐字匹配,不调模型判官)**:`is_opinion()` 命中的**第三方归属**(业内人士预计/或将/显然、据 X 报道、据悉、外媒报道…)必须在**绑定引句 ±1 句的 `anchor_window` 内逐字出现**;缺失 ⇒ 不得路由 `opinion`,走事实门禁,`checks["attribution"] = {found, missing, self}`,`all_ok` 强制 False,`deterministic_findings` 产 critical **`attribution_not_in_source`**,缺维白名单加 `attribution`。
- `ttxt/writing/workflow.py:301`、`ttxt/writing/export.py:49`:证据说明与导出证据链的维度元组各加 `attribution`,让人工复核界面看得见这条结论。
- `is_opinion()` 本体一字未改;「表示」从未进 hedge 词表(否则具名机构的「××表示」会被当观点放行)。
- 与数字/日期同一把尺子:**只认窗口,不认全文** —— 正文别处出现一次「据 X 报道」不等于当前这句的归属有出处(移花接木同样成立);`v2t25`(归属在来源里但落在引句窗口之外)仍不放行。

## 3. 修复前后(10 张卡,同一张卡、同一来源文本)

| card | BEFORE | AFTER | critical | missing_dims |
|---|---|---|---|---|
| v2f06 / v2f12 / v2f17 / v2h04 / v2h09 / v2h15 / v2t05 / v2t11 / v2t17 / v2t23 | `kind=opinion`,`credibility=opinion`,`missing_dims=[]`,零 critical | `kind=fact`,`credibility=unverified` | 伪造归属(`attribution_not_in_source`)+ 事实未通过来源核验(每条 2 个 critical) | 含 `attribution:<缺失归属>`;4 张另含 `persons:业内人士` |

10/10:`checks["attribution"].missing` 非空。**逃逸数量 0**(`test_no_golden_card_escapes_the_fact_gate_through_a_fabricated_attribution`)。

## 4. 不误杀的证据

- 真转述(v2t01,来源里逐字写着「据数码闲聊站爆料」):仍 `credibility=opinion`、`attribution.found=["据数码闲聊站爆料"]`、`missing=[]`、无该 critical,且**不等于 verified**;
- 全 61 张扫描:触发归属核验 10 张、拦下 10 张、误伤 0 张;
- 逐字对照旧模块:`v2t01 / v2h01 / v2f01` 仍 `verified`,`v2t13` 仍缺 `numbers:10`,`v2t25` 不变;
- 第一人称自述(「我认为该模型或将改变行业格局」)不要求来源里出现该短语,仍走观点通道;无来源卡仍 `no_source`。

## 5. 答案钥匙迁移(可审计、幂等)

`scripts/golden_v2_fix_t15_answer_key.py`:内置 10 张卡的 `card_id → 旧值/新值` 显式表,旧值不符即报错。这 10 行 `expected_by_construction`: `opinion → unverified`(其余 20 个字段一字未改)。自证:**未改字段 1271 个(51 张全字段 + 10 张各 20 字段)逐字一致**,字段顺序不变,`human_*` 仍全空;再跑幂等不改字节,`--check` exit 0。其余 51 张、AI sidecar、2026-11-16 纸面 token 的 sha256 已钉进 `test_p13_batch4_attribution_routing.py`。

## 6. 外溢代价(方向都是变严,没有一条放宽)

- AI sidecar 对这 10 张的**判据已过期**(它按 2026-11-16 纸面「转述」判的),`golden_v2_ai_adjudicate.py` 现在 **exit 1 拒绝重生成**(20 条错误只涉及这 10 张),不写文件 —— 不许伪造「按今日标签重述」的理由去凑数,需重裁一轮;
- P29 审计:① 与构造真值自洽 **61 → 51**,R3 矛盾 **0 → 10 条**;`revise_assist_draft.write_draft()` 抛 `DraftError`,**v2 辅助草案无法重生成**;
- 度量器读数变化:`machine_vs_construction 61/61`(钥匙跟着机器走,仅规则吻合度,不是质量分);`machine_vs_ai accuracy 0.836` 不变;`machine_label_drift` 恰好这 10 张;`opinion_routed_cards` 仍报 10(**口径是 sidecar 里的 `kind`,不是今日标签**),原来的逃逸信号改由 `machine_label_drift` 承载,`opinion_routing_rejected_by_ai` 因此归零;`ai_gate_failures` 1 条 → 2 条。
- 卡片 `note` 里「期望路由为 opinion 且不得计入事实核验」这句**旧说明未改**(不碰卡片原文),它已与钥匙不一致,以钥匙与回归测试为准,此处留痕。

## 7. 实测读数与退出码

1. `pytest -q 10_tests/test_p13_batch4_attribution_routing.py 10_tests/test_p13_batch3_golden_ai_adjudication.py 10_tests/test_p13_batch2_gate_bypass.py 10_tests/test_p13_golden_v2.py` → exit 0,87 passed;
2. 全量 `pytest -q`(负责人本人复跑)→ **exit 0,727 passed / 0 failed,174.01s**(基线 693 + 33 新增 + 1 新增反例);
3. `golden_v2_harness.py --ai-sidecar …`(负责人本人复跑)→ **exit 2**;`human_adjudicated_count 0`、`ai_adjudicated_count 61`、`quality_claim NOT_AVAILABLE`、`quality_claim_ai NOT_AVAILABLE`;
4. 测试侧改动全部按现实改:`test_ten_opinion_routed_cards_still_escape_the_fact_gate` → `test_ten_fabricated_attribution_cards_now_hit_the_fact_gate`;生成器逐字复现 → 钉成「拒绝重生成」;`test_p13_golden_v2.py::test_bare_hedge_word_no_longer_exempts_fact_gate` 由 `opinion` 钉成 `unverified`;`test_p29_adjudication_v2.py` 自洽 61→51、R3 0→10、修订器拒重生成。**没有删除或放宽任何断言**。

## 8. 明确没做

没提交前不动 `human_*`;不改模型、Strata、端口、阈值、分母;不改卡片原文与历史裁定;不新增第二套评分体系;`quality_claim` 仍 NOT_AVAILABLE,**修好门禁 ≠ 质量达标**;真人裁定仍 0/61,T-01/T-02/P12/P13 状态不因本次修复而变。
