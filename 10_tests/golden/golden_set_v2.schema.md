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

harness 还必须先数人再数 AI,两套计数并列输出、绝不合并:`human_adjudicated_count`(真人逐张裁定)与 `ai_adjudicated_count`(AI 辅助初审)。质量结论只有一个人工口径 `quality_claim`,**只由 `gate_failures` 决定**;AI 口径另出一套 `quality_claim_ai` / `ai_gate_failures`,仅供参考,**不参与退出码**(门禁未过一律退 2)。`machine_vs_ai` 一节里 `verdict_as_recorded` 与 `verdict_on_current_machine_label` 必须分开报,后者只在判据过期时由 AI 自己写明的应判标签派生。

**门禁**:`已人工裁定卡片数 < 50` 时,harness 必须输出 `quality_claim: NOT_AVAILABLE`,并拒绝给出「L4 事实核验达标」的任何结论。有效卡片还要求:每类目 ≥10、A+B 级 ≥50%、负样本 ≥30%、六种 `kind` 至少各 3 张。

## 4b. AI 辅助初审 sidecar(2026-11-17 起)

- 文件:与卡片文件同名加后缀 —— `golden_set_v2_ai_adjudication.jsonl`(定位规则 `harness.ai_sidecar_path_for()`)。一行一张,按 `card_id` 对齐。
- 只允许这些字段:`card_id / ai_row_no / category / kind / card_source_tier / ai_adjudicated / ai_verdict(通过|驳回) / ai_fact_support(supported|unsupported) / ai_confirms_tier(A|B|C|D) / ai_adjudicator / ai_adjudicator_note / ai_at / ai_at_basis / ai_reason / sheet_printed_machine_token / machine_at_adjudication / machine_now / ai_basis_stale / ai_verdict_current / ai_verdict_current_reason / ai_self_consistency_flag / needs_human_review / tier_matches_card`。
- **铁律**:sidecar 里出现任何 `human_*` 键 = 冒充人工裁定,harness 直接判模式错误(退 1);卡片文件的 `human_*` 四个字段一律保持原值(现全空),AI 结果永不得回填。
- **判据必须可取证**:`machine_at_adjudication` 取 AI 当时看到的机器标签(= 打勾单纸面实印短词,证据 `adjudications/printed_sheet_tokens_20261116.jsonl`),不得用今天的机器结论反推;纸面标签与 `machine_now` 不一致时 `ai_basis_stale=true`,且必须带 `ai_verdict_current_reason`,否则生成器报错。
- **一轮一份,不许合并**:不同轮 AI 初审对同一张卡片判定相反时,不得平均、不得挑好看的用,必须出分歧清单退回重判(本轮实测两版 ① 只一致 49/61,分歧 12 张,见 `08_wiki/reports/P13-ai-round2-disputes.md`)。sidecar 目前只存第一轮那一份。
- **重判写进同一行的 `ai_recheck_*` 字段,不加行、不加第二口径**:`ai_recheck_adjudicator / ai_recheck_at / ai_recheck_at_basis / ai_recheck_basis_label / ai_recheck_verdict / ai_recheck_reason / ai_recheck_rubric / ai_recheck_flag / ai_recheck_changes_recorded / ai_recheck_changes_derived`。硬要求:`ai_recheck_basis_label` 必须等于**今日**机器结论(按 2026-11-16 纸面旧标签做的重判一律报错),历史 `ai_verdict` 不改写,`human_*` 一律不写。

## 5. 构造纪律

- 正样本的 claim 必须来自真实可核事实,并给出 A 或 B 级证据;C/D 级只能作线索卡(`note` 说明)。
- 负样本必须来自真实出现过的错误形态:改动数字/日期、张冠李戴的人物/机构、强行建立的因果关系。禁止凭空编造「好让规则通过」的负样本。
- `opinion_attributed`:同一事实改为「某媒体报道称…」的归属写法,预期 `opinion`,用于检验事实/观点分离。
- 任何凭记忆写的 URL、未实际抓到正文的来源,一律不得进入 A/B 级;发现即降级为 D 并剔除出质量度量。

## 6. 裁定口径(2026-11-17 钉死;两版 AI 各自独立给出同一把尺子)

任何模型做初审,①②③ 必须按这把尺子问,不得自行换定义 —— 否则再判几轮仍是互相打架(第一轮判 15 张不贴切、第二轮判 5 张,差在定义不在数据)。

- **① 机器标签贴不贴切 = ①a + ①b 合并判定,不是两套评价体系**
  - ①a 句子类型(直接陈述 / 转述 / 与来源不符)与机器标签是否一致;
  - ①b 若机器贴「转述」,**那个说话的人或机构在来源原句里是否真实存在**;
  - **归属在来源原句里不存在 ⇒ ① 一律判「不贴切」,应判「对不上」。不许因为句子里有「××表示 / 业内人士预计」就算贴切。**
  - 机械自核(生成器强制):今日标签「转述」+ ①贴切 + ②不能 ⇒ 违反 ①b,记 **R5**,直接报错不入库。
- **② 原句能否支撑整句话**:数字、日期、人物、机构、归属、因果**逐项**核,只对上一半不算通过。取值只有 `supported / unsupported`;将来若要加「说不清」这一档,必须先改本 schema 与度量器,不得在 sidecar 里私自加第三种取值。
- **③ 证据等级**:A 一手 / B 权威二手 / C 一般转载 / D 来源不明。**「同意卡片现有等级」≠ 来源已独立核实**;等级不因内容看着可信而自动升级。A+B 占比仍是门禁分母(现 17/61 = 27.9%,T-02 未解)。
- 五条裁定纪律:①必须同一张卡、同一份原文、**同一版机器标签**才可比;②不许把「句式像转述」当成「来源支撑转述」;③数字/日期/人物/机构/归属/因果逐项查;④证据不足要标「说不清」类标记而不许猜(需先按上条改 schema);⑤两版结果不一致必须列分歧与理由,不取平均、不挑好看的结果、不把多数票当真相。裁定者身份必须真实记录(第二轮自报「ChatGPT辅助初审」而负责人标注来源为 chat.deepseek.com,矛盾照原样存证,不替 AI 认账)。
