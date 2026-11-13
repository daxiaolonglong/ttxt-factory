# 黄金核验集 v2 规范(`golden_set_v2.jsonl`)

版本:`p13-golden-v2`。一行一张卡片(JSONL),便于逐行 diff、逐条人工裁定、增量补写。
消费方:`scripts/golden_v2_harness.py`(度量)与 `10_tests/test_p13_golden_v2.py`(模式校验 + 门禁)。
**v1 的缺陷必须在这一版消除:v1 没有任何测试或 CLI 读取它,它只是候选清单,不是验收集。**

## 1. 三个判据必须分开记录(核心设计)

| 判据 | 由谁判定 | 含义 | 存字段 |
|---|---|---|---|
| ① 原文匹配 | 机器(确定性) | 草稿句能否绑定到来源正文的**连续引句**,数字/日期/人物/机构/因果锚点是否在绑定引句中出现 | `machine.*`(由 harness 现算,不入库) |
| ② 事实支持 | **人工** | 该来源是否真的支持该断言为事实(而不是「原文说过」) | `human_verdict` + `human_fact_support` |
| ③ 来源可信度与独立性 | 人工确认机器分级 | A/B/C/D 级证据,是否独立、是否互相转载 | `source_tier`、`corroborating_urls`、`independence_note`、`human_confirms_tier` |

`credibility ∈ {verified, unverified, no_source, opinion}` 只描述 ①,不得当作「事实为真」。

## 2. 卡片字段(缺任一必填字段 = 无效卡片)

```
card_id            必填  v2<t|f|h><NN>
category           必填  technology | finance | health
fact_type          必填  number | date | person | org | causal | quote
kind               必填  positive | negative_perturbed | negative_fabricated_causal
                           | negative_wrong_entity | unrelated | opinion_attributed
claim              必填  候选草稿句(中文,含可核验锚点)
source_url         必填  卡片绑定的来源 URL(必须真实存在)
source_title       选填  来源标题
source_publisher   必填  来源机构名
source_tier        必填  A | B | C | D(见 §3)
corroborating_urls 必填  列表;tier=B 时至少 1 条独立来源;tier=A 允许为空但需一手出处
independence_note  必填  一句话:这些来源是否互相转载,依据是什么
source_text        必填  harness 离线复现用的来源正文(逐字抓取,≥ claim 所在句)
source_article_id  选填  若取材自本机 articles.db,记录 article id(仅溯源,不作独立性证据)
source_excerpt     必填  逐字片段(≤200 字),必须出现在 source_text 中
quote_offset       必填  片段在 source_text 中的字符偏移(未知填 0 并在 note 说明)
expected_by_construction 必填  verified | unverified | no_source | opinion(构造预期,不是真值)
human_adjudicated  必填  布尔;只有人工逐条裁定后才计入质量度量
human_verdict      必填  null | "通过" | "驳回"(「驳回」= 机器结论错误)
human_fact_support 必填  null | "supported" | "unsupported" | "needs_more_evidence"
human_confirms_tier 必填 null | "A" | "B" | "C" | "D"(人工确认的证据级)
note               必填  该卡片的教学价值/来源抓取情况
```

## 3. 证据级(A–D)

- **A 一手证据**:官方公告、监管文件、原始报告、财报/招股书原文直接支持关键事实(必须是实际抓到正文的页面)。
- **B 多来源交叉**:≥2 个互不隶属、非互相转载的可信来源支持同一事实,且 `corroborating_urls` 可核。
- **C 单一/间接证据**:可作线索,不得当作已独立证实。**头条等自媒体聚合稿一律 C 起步。**
- **D 无来源/冲突/无法核验**。

## 4. 质量度量与门禁(harness 强制)

harness 同时输出两类数字,且**不得混称**:

1. `machine_vs_construction`:机器结论与「构造预期」的一致率。**只是规则吻合度,不代表质量,不得作为 L4 验收依据**(v1 的 52/57 属于这一类)。
2. `machine_vs_human`:仅对 `human_adjudicated=true` 的卡片计算机器与人工裁定一致率;附
   - `false_accept_rate`:被判 `verified` 的**负样本**占已裁定负样本的比例(最关键指标);
   - `false_reject_rate`:正样本被判 `unverified/no_source` 的比例;
   - `opinion_routing_errors`:hedge 词路由错误(该走事实门禁的走了观点,或反之);
   - `independent_source_coverage`:tier A/B 且独立性经人工确认的卡片占比。

**门禁**:`已人工裁定卡片数 < 50` 时,harness 必须输出 `quality_claim: NOT_AVAILABLE`,并拒绝给出「L4 事实核验达标」的任何结论。有效卡片还要求:每类目 ≥10、A+B 级 ≥50%、负样本 ≥30%、六种 `kind` 至少各 3 张。

## 5. 构造纪律

- 正样本的 claim 必须来自真实可核事实,并给出 A 或 B 级证据;C/D 级只能作线索卡(`note` 说明)。
- 负样本必须来自真实出现过的错误形态:改动数字/日期、张冠李戴的人物/机构、强行建立的因果关系。禁止凭空编造「好让规则通过」的负样本。
- `opinion_attributed`:同一事实改为「某媒体报道称…」的归属写法,预期 `opinion`,用于检验事实/观点分离。
- 任何凭记忆写的 URL、未实际抓到正文的来源,一律不得进入 A/B 级;发现即降级为 D 并剔除出质量度量。
