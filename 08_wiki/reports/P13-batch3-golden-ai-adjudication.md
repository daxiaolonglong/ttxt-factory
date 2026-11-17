# P13 批次3:黄金核验集 v2 的 AI 辅助初审落库(2026-11-17)

这一轮推进:**T-01(AI 口径)** 把第一轮 61 张 AI 初审写进独立 sidecar 并让度量器分列 human/ai 两套计数;
**T-03 补强** 把已修掉的错标签卡钉成回归样本;**新开 T-15**(10 张伪造归属卡走「转述」通道,事实门禁根本不跑)。

## 1. 数据落点(全部版本可追)

- sidecar:`10_tests/golden/golden_set_v2_ai_adjudication.jsonl` —— 61 行,**只有第一轮那一份**。
- 生成器:`scripts/golden_v2_ai_adjudicate.py` —— 原始 61 行逐字转录 + 自检(行号连续性、纸面标签核对、
  等级核对、判据过期必须有重述理由),重跑必得同一份(`--check`)。
- 纸面实印标签证据:`10_tests/golden/adjudications/printed_sheet_tokens_20261116.jsonl`
  (由 `scripts/golden_v2_sheet_tokens.py` 从 docx 里抽出来:能核实 10 / 对不上 36 / 转述 15)。
- 字段与铁律:`10_tests/golden/golden_set_v2.schema.md` §4b。
- 卡片文件 `10_tests/golden/golden_set_v2.jsonl` **一个字节未改**,`human_*` 四字段仍全空。

## 2. 第一轮读数(AI 口径,不是质量分数)

| 项 | 按纸面标签记 | 按今日机器标签重述 |
|---|---:|---:|
| ① 机器贴切 | 46 | 51 |
| ① 机器不贴切 | 15 | 10 |
| ② 原句能支撑 | 10 | 同左 |
| ③ 证据级 | A 17 / C 44(61/61 与卡片声明一致) | 同左 |

- 两版 AI 与机器的 A+B 独立证据占比 **17/61 = 27.9% < 50%** ⇒ **T-02 仍未解**。
- 人工口径:`human_adjudicated_count = 0`,门禁三条仍失败,`quality_claim: NOT_AVAILABLE`,**度量器退出码 2 不变**。
  AI 口径另出 `quality_claim_ai: NOT_AVAILABLE` + `ai_gate_failures`,只报数、不参与退出码。

## 3. 判据取证(不写清楚就是造假)

1. AI 看的是 **2026-11-16 15:12 打印的那张打勾单**,不是今天的机器结论。纸面实印标签与今日标签相比只有 **6 张不同**:
   `v2h01 opinion→verified`、`v2h02/v2h03/v2h16 opinion→unverified`、`v2t13 verified→unverified`、`v2t14 opinion→unverified`
   —— 正是 T-03 当天修掉的 6 张绕过卡。sidecar 对每张同时记 `machine_at_adjudication / machine_now / ai_basis_stale`。
2. 所以第一轮 15 张「不贴切」里:**10 张仍然有效**(走「转述」通道的伪造归属卡),**5 张已随 T-03 过期**
   (`v2h02/v2h16/v2t13/v2t14` + `v2h01` 的反方向),过期部分按 AI 自己写明的应判标签派生 `ai_verdict_current`,理由写全。
3. 自洽检查(用纸面实印短词,不用构造真值):纸印「转述」+ ①贴切 + ②能支撑 = 自相矛盾,命中 **R4 = 1 张(`v2h01`)**,
   已标 `needs_human_review`。
4. **推翻一条旧读数**:P29 那批 `assist_draft_*` 里的 `machine_token_printed` 与纸面实印在这 6 张上不符 ——
   `scripts/golden_v2_revise_assist_draft.py` 把该字段强制写成 `expected_by_construction`,R3 再据此判 ①=对,
   于是得出「61/61 贴切」。那是**机器 vs 答案钥匙**,不含任何关于纸面标签的信息,不得再当标签准确性的证据。

## 4. 第二轮(来源标注 chat.deepseek.com)本轮没入库

- 按项目负责人指示**没有改成多口径结构**:第二轮 61 行未写进 sidecar,度量器里没有第二个 AI 口径。
- 只做对照(`scripts/golden_v2_ai_round2_diff.py` → `08_wiki/reports/P13-ai-round2-disputes.md`):
  **① 只一致 49/61,分歧 12 张;② 完全一致(同一批 10 张);③ 完全一致。**
- 第二轮自报 `ai_adjudicator` 写「ChatGPT辅助初审」,与负责人标注的来源 chat.deepseek.com **矛盾**,照原样存证,未替它改写。
- 第二轮说「机器把错当对 = 1/10 = 10%」:这个读数**只对 2026-11-16 纸面时点成立**(纸面 10 张「能核实」里 `v2t13` 一张判错);
  按今日标签重算,机器「能核实」的 10 张两版都认可,是 **0/10**。
- 第二轮点名的 5 张(`v2h01/v2h02/v2h16/v2t13/v2t14`)机器结论**已于 T-03 改掉**,不是待修项。

## 5. 明确没做的事

- 没有把 AI 裁定写进 `human_*`,没有宣称「事实核验达标」,没有宣称 L4 通过;
- 没有改 `expected_by_construction`、没有改事实路由器、没有改模型与 Strata 配置、没有改端口与量化;
- 没有新增治理文件,进度只更新 `08_wiki/PROJECT_STATUS.md`;
- 两版打架的 12 张不自行裁决,退回重判(重判模板见分歧报告 §4)。
