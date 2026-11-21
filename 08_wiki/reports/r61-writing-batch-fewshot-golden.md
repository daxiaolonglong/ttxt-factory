# R61 报告：写作批次复验 / few-shot 样本库 / 黄金集重裁

日期：2026-11-21　执行：主会话（本地模型 + DeepSeek 评审）
范围：R61 四条工作线——黄金集 12 张分歧卡重裁、DeepSeek 评审切换与判定契约、
单篇评审 ≤3 次修订回路（用户新规）、G4 写作批次复验 + G5 few-shot 库 + 0 产出根因。

## 1. 结论摘要

| 项目 | 结果 | 证据 |
|---|---|---|
| 黄金集 12 张分歧卡重裁 | 本地钉死模板重裁 12/12 自洽；DeepSeek 第二来源 12/12 与本地一致 | `10_tests/golden/adjudications/r61_recheck_local_round2.json`、`..._deepseek.json` |
| DeepSeek 生产评审 | 已切换并经运行时核验：`deepseek-v4-pro`、`labels_v1`、temp 0.0、max_tokens 8000 | `00_control/config.json`；`review.served_model()` 实测 |
| 单篇评审 ≤3 次 | 已落地：最多 3 轮独立评审，`修改后再评` 转修订反馈、`不建议发布` 立即拒、额度耗尽即拒 | `ttxt/writing/workflow.py`；`10_tests/test_p40_independent_review.py` |
| G4 40 篇复验（本地同模型评审口径） | 通过 22/40 = **55.0%**；R50 基线 27.8%（5/18） | `03_intelligence/pass_rate_probe-20261121-r61.json` |
| G5 few-shot 库 | 11 条实测样本（social 5、finance 6；正例 6、反例 5），库缺失/空/坏自动降级 | `00_control/fewshot_library.json`；`10_tests/test_r61_fewshot.py` |
| 真实烟测（DeepSeek 评审） | 3 主题 4 次运行，**0 篇可发布**；评审次数从未超过 3 次，无卡死 | `03_intelligence/r61_smoke_receipt*.json` |
| 0 产出根因 | 已定位到生成侧缺陷 + 本地评审偏松 + 修订质量三类根因，并逐项修复（见 §7-8） | 同上回执 + G4 探针读数 |
| 回归 | 5 个测试文件 **110 passed, 0 error**（含生产隔离门禁） | 2026-11-21 13:0x 实跑 |

## 2. 黄金集 12 张分歧卡重裁

- 裁定对象：`10_tests/golden/golden_set_v2.jsonl` 中 12 张历史分歧卡（10 张伪造归属卡 +
  v2h01 + v2h03）。卡片文件一字节未改，sidecar 只写 `ai_*` 字段，
  `human_adjudicated_count` 保持 0，AI 裁定 ≠ 人工裁定。
- 第一轮：原始模板下 11/12 行自相矛盾，存证不采用。
- 第二轮：钉死 §6 模板后 **12/12 自洽**（v2h01「能核实」、v2h03「对不上」、其余 10 张「对不上」）。
- DeepSeek 第二来源：同一 §6 钉死 prompt，`deepseek-v4-pro` **12/12 ③同意**，与本地完全一致。
- 生成器 `scripts/golden_v2_ai_adjudicate.py --check`：0 错误，61/61 与今日机器结论对齐，
  判据过期卡 16 张全部补 `CURRENT_BASIS_REREAD` 重述。
- 注意：DeepSeek 首轮重裁时密钥缺失（commit 586b5f2 如实记录本地模型身份），
  第二来源在密钥就位后补齐（commit 54ecb6b）。

## 3. DeepSeek 评审通道

- 端点 `https://api.deepseek.com`，模型 `deepseek-v4-pro`，密钥仅存 `.secrets/deepseek_api_key`
  （gitignored，空文件按缺失处理并显式报错）。
- 判定契约 `labels_v1`：评审输出后第二次调用只抽取一个标签——
  `可以发布 / 修改后再评 / 不建议发布`；仅 `可以发布` 放行，其余与解析失败一律拦截。
- 校准（切换前）：8 篇样本（4 篇本地通过 + 4 篇被拒）→ **0 可以发布、7 修改后再评、
  1 不建议发布、0 抽取错误**。结论：本地同模型评审通过 ≠ 可发布，生产评审必须走 DeepSeek。
- 切换后运行时核验：`served_model=deepseek-v4-pro`、`base_url=https://api.deepseek.com`、
  `verdict_contract=labels_v1`、`temperature=0.0`、`max_tokens=8000`。

## 4. 单篇评审 ≤3 次修订回路（用户新规落地）

规则：一篇文章最多允许评审 3 次；每轮 `修改后再评` 必须把评审原文作为修订意见逐条落实，
修订后重抽 claim、重核事实、本地复审，再送下一轮；`不建议发布` 立即硬拒；
第 3 轮仍不通过即拒，**不进入死循环**。

实现要点（`ttxt/writing/workflow.py`）：

- 旧「单次独立评审」替换为外层评审回路；`writing.independent_review_max=3`（配置可调，下限 1）。
- 每轮记录 `round / verdict_label / blocking / publishable / reviewer_model / reviewer_endpoint /
  verdict_excerpt / elapsed_s`，回执含 `independent_reviews_used` 与
  `independent_review_rounds`，便于审计。
- 额度耗尽或判 `不建议发布` 时把「独立评审判定不通过」并入确定性 critical，`passed=False`。
- 评审通道不可用时按既有口径 fail-open 记录 `reviewer_limitation`，不击穿主流程（原有测试保持通过）。
- 单元测试覆盖：3 轮全 `修改后再评` → 恰好 3 次调用并拒绝；`修改后再评`→`可以发布` → 2 次调用放行；
  `不建议发布` → 1 次调用直接拒。
- 真实烟测：最多 3 轮（催婚第二跑恰为 3 轮后拒），无超限、无卡死。

## 5. G4 写作批次复验读数

- 批次：40 主题（social 20 + finance 20），1 轮，快照库，`template_decheck=False`，导出
  `07_tmp/r49_passrate/exports/r1/<cat>/<draft_id>.md`。
- 结果：**22/40 通过（55.0%）**；R50 基线 27.8%（5/18）。
- 分布：passed 22（55.0%）、other 7（17.5%）、fact.binding 6（15.0%）、
  fact.anchor_missing 3（7.5%）、gate.structure_truncated 2（5.0%）。
  分类：social 12/20、finance 10/20。
- 口径警示：本轮判官是**本地同模型评审**，不是独立评审；随后 DeepSeek 校准（0/8 可发布）
  证明这一通过率只反映「本地门禁 + 本地评审」口径，不能当作可发布质量结论。
  该读数证明的是：提示词/评审/去重修复后，链路产出率显著回升，且主要剩余失败集中在事实绑定。

## 6. G5 few-shot 样本库

- `00_control/fewshot_library.json`：11 条实测样本——social 5（3 正例 + 2 反例）、
  finance 6（3 正例 + 3 反例）；正例 kind=`verified_viral`，反例 kind=`verified_junk`，均 `verified=true`。
- 三条红线已由测试钉死：未实测/类目不符/kind 非法/字段不齐一律过滤；
  库缺失/空/损坏返回空样本 + 如实状态（`library_missing / library_empty / library_unreadable /
  no_verified_samples`），写作链路降级为无示例，回执 `fewshot_status` 不得写成功；
  注入只进草稿提示词（正例 + 反例 + note），门禁与判定尺度不动。
- 已知局限：正例来源是「本地门禁通过」的稿子（G4 样本），
  未经 DeepSeek `可以发布` 认证；R62 起应优先用 `independent_review_passed` 的稿子替换正例。

## 7. 0 产出根因（实测证据）

| # | 根因 | 证据 |
|---|---|---|
| 1 | 生成侧相邻重复：短语/整句连写（如「每4个月延1个月」两遍） | 延迟退休烟测回执；`copyedit` 修复前版本 11 版 5 修 |
| 2 | 材料拼贴/移花接木：数字锚点落在 quote 窗口外 | G4 `fact.binding` 6/40；A股烟测「3.55亿元」被判锚点越窗 |
| 3 | 编造/占位来源：「民政部/国家统计局」「示例性文献……请以实际检索为准」 | 催婚第一跑回执 critical |
| 4 | 标题超出正文证据（标题党） | 草稿提示词第 10 条据此新增 |
| 5 | 修订截断/插入审稿说明（「此处应改为」、突然换头、多余地名） | 催婚第一跑终稿 truncated；修订提示词 5 条规则据此新增 |
| 6 | 本地同模型评审偏松：score 0.85–0.95、fc 1.0 仍被 DeepSeek 判「修改后再评」 | 催婚第二跑 3 轮全拦；DeepSeek 校准 0/8 可发布 |
| 7 | 本地修订质量不足：概念/论证问题改不动，3 轮仍不过 | 催婚第二跑 3 轮评审意见均为「概念边界/论证衔接」 |
| 8 | finance 事实绑定硬门槛在评审前即拦截（R50 既有硬规则，未放宽） | A股烟测 0 次评审、`fact.binding` critical |

结论：0 产出不是单一故障，也不是「评审太严」；是生成质量、评审独立性与修订能力三者叠加。
本次只修「可确定性修」的部分，未放宽任何门禁阈值（800 字、事实门禁、评审阈值均未动）。

## 8. 已落地修复

1. **生产评审切 DeepSeek**：`labels_v1` 契约 + 独立抽取，仅「可以发布」放行。
2. **评审 ≤3 次回路**：评审原文作为修订意见，修订后重核事实再评；硬拒/额度耗尽不再循环。
3. **生成侧确定性去重** `style.copyedit_draft`：只删相邻重复片段（≥6 字）与相邻重复句，
   Markdown 表格分隔行不误伤；不改写事实，修完仍走 claim 抽取/事实核验/门禁。
4. **修订截断重试** `_revise_with_retry`：输出不完整时按草稿同先例放宽 token 重试一次，
   完整性判定不变。
5. **提示词收紧**：草稿加「来源只能来自材料、禁占位引用」「标题不得超出正文证据」；
   修订加「只改点名问题、禁新增/禁审稿说明/禁删引用」。
6. **few-shot 注入**：草稿侧正反例，库异常自动降级并如实回执。

## 9. 未覆盖项与风险

- **可发布产出仍为 0**：修复后 3 主题真实烟测仍被 DeepSeek 拦在「修改后再评」，
  主因是论证/概念质量与本地模型修订能力；需要 R62 从素材组织（分节写作、单锚点约束）
  和修订手段（更强模型或分句修复）继续攻，不能靠放宽门禁。
- **G4 读数非独立口径**：22/40 是本地评审结果；对外只能引用为「链路产出率」，
  不能引用为「可发布率」。
- **few-shot 正例未认证**：见 §6 局限。
- **DeepSeek 成本/时延**：单轮 35–58s，3 轮上限下单篇评审侧约 2–3 分钟；
  批次化生产需评估预算与并发。
- **finance 事实绑定**：若长期为主要拦截项，R62 应专项优化「一句一锚点 + 窗口整句引用」，
  仍不放宽规则。

## 10. 证据索引

- 黄金集：`10_tests/golden/adjudications/r61_recheck_local_round2.json`、`r61_recheck_deepseek.json`；
  sidecar `10_tests/golden/golden_set_v2_ai_adjudication.jsonl`
- 校准：`03_intelligence/r61_verdict_calibration.json`
- G4：`03_intelligence/pass_rate_probe-20261121-r61.json`；R50 对照 `pass_rate_probe-20261119-r50.json`
- few-shot：`00_control/fewshot_library.json`
- 烟测回执：`03_intelligence/r61_smoke_receipt.json`（延迟退休）、
  `r61_smoke_receipt_cuihun_round1.json` / `r61_smoke_receipt_cuihun.json`（催婚修复前后）、
  `r61_smoke_receipt_astock.json`（A股）
- 测试：`10_tests/test_p40_independent_review.py`、`test_r61_fewshot.py`、`test_r61_copyedit.py`、
  `test_p13_batch3_golden_ai_adjudication.py`、`test_p13_batch4_attribution_routing.py`
- 提交：586b5f2、54ecb6b、cff6c6f、817a86e、e259fda、907641b、1cf7e17

> 环境说明：最终回归期间暂停了常驻托管（避免 Worker 写生产日志触发测试隔离门禁），
> 测试通过后已清理停止标志并 `supervisor --ensure` 恢复，scheduler 与 worker 均在线。
