# 会话检查点（压缩/新会话的唯一接手文件）

**这个文件的作用是"无损压缩"**：上下文被压缩、或换新窗口时，只相信这里写的事实，不靠对话记忆。
规则：每完成一个阶段、每次提交推送后、每次产生大输出（全量测试日志、全盘扫描、长报告）之后，必须重写本文件。
最后更新：2026-11-21 13:07 — **R61 全部完成（DONE）**。远端 `main` = `845dec9`（报告+账本）；
本检查点为紧随其后的单独提交，最终哈希以 `git ls-remote origin main` 实测为准。

## R61 最终状态（全部闭合）

1. **黄金集 12 张分歧卡重裁闭合**（`586b5f2` + `54ecb6b`）：本地钉死 §6 模板第二轮 12/12 自洽
   （v2h01 能核实、v2h03 对不上、其余 10 张对不上）；DeepSeek 第二来源 12/12 ③同意。
   卡片文件未动、sidecar 只写 `ai_*`、`human_adjudicated_count=0`；生成器 `--check` 0 错误、61/61 对齐；
   判据过期卡 16 张全部补 `CURRENT_BASIS_REREAD`。证据 `10_tests/golden/adjudications/r61_recheck_*.json`。
2. **DeepSeek 生产评审已切换并运行核验**：`review.served_model()=deepseek-v4-pro`、
   `https://api.deepseek.com`、`verdict_contract=labels_v1`、temp 0.0、max_tokens 8000；
   key 仅 `.secrets/deepseek_api_key`（gitignored，空文件按缺失显式报错）。
   切换前校准 8 篇 → **0 可以发布、7 修改后再评、1 不建议发布、0 抽取错误**
   （`03_intelligence/r61_verdict_calibration.json`）⇒ 本地同模型评审通过 ≠ 可发布。
3. **单篇评审 ≤3 次修订回路已落地**（`907641b`）：`writing.independent_review_max=3`；
   `修改后再评` 的 `verdict_text` 作为修订意见逐条落实 → 重抽 claim/重核事实/本地复审 → 再评；
   `不建议发布` 立即硬拒；额度耗尽即拒；回执逐轮记 label/excerpt/elapsed；
   单测三场景全过；真实烟测最多恰 3 轮，无超限、无卡死。
4. **G4 写作批次复验完成**：40 主题（social 20 + finance 20）1 轮快照，
   通过 **22/40 = 55.0%**（R50 基线 27.8%，5/18）；分布 passed 22、other 7、fact.binding 6、
   anchor_missing 3、structure_truncated 2；social 12/20、finance 10/20。
   **口径：判官是本地同模型评审，不是独立质量结论**（`03_intelligence/pass_rate_probe-20261121-r61.json`）。
5. **G5 few-shot 库完成**：`00_control/fewshot_library.json` 11 条实测样本
   （social 5 = 3 正 + 2 反；finance 6 = 3 正 + 3 反；正例 kind=verified_viral、反例 verified_junk）；
   未实测/类目不符/kind 非法/字段不齐一律过滤；库缺失/空/坏自动降级并如实回执；只注入草稿提示词。
   局限：正例仅本地门禁通过，未获 DeepSeek `可以发布` 认证，R62 应改用独立通过稿替换。
6. **0 产出根因已定并有回执证据**（`03_intelligence/r61_smoke_receipt*.json`，3 主题 4 次烟测 0 可发布）：
   相邻重复片段/句；材料拼贴、数字锚点越出 quote 窗口；编造/占位来源（民政部/国家统计局/示例性文献）；
   标题超出正文证据；修订截断/插入审稿说明；本地评审偏松（0.85–0.95 仍被拦）；本地修订改不动概念/论证；
   finance 事实绑定硬门槛在评审前拦截（R50 既有规则未放宽）。
7. **已落地修复**（`1cf7e17`）：`style.copyedit_draft` 确定性去重（相邻重复 ≥6 字/相邻重复句，
   Markdown 表格分隔行不误伤）；`_revise_with_retry` 截断重试一次；草稿提示词禁编来源/占位引用、
   标题不得超出正文证据；修订提示词只改点名问题、禁新增/禁审稿说明/禁删引用。
   门禁阈值全部未动（800 字、事实门禁、评审阈值、quote 窗口/重叠率）。
8. **回归**：5 文件 **110 passed / 0 error**（golden 批3、批4、few-shot、评审回路、copyedit）。
   跑测期间曾暂停常驻托管（避免 Worker 写生产日志触发 conftest 生产隔离误报），
   测后已清停止标志并 `supervisor --ensure` 恢复：supervisor/scheduler/worker 在线。
9. **提交链**（均推送并 `git ls-remote` 核验）：`586b5f2`、`54ecb6b`、`cff6c6f`、`817a86e`、
   `e259fda`、`907641b`、`1cf7e17`、`845dec9`（报告+账本）；本检查点紧随其后。
10. **报告**：`08_wiki/reports/r61-writing-batch-fewshot-golden.md`（10 节，含证据索引）。

## R62 待办（按优先级，均不得放宽门禁）

1. **可发布产出仍为 0**：3 主题修复后仍被 DeepSeek 判「修改后再评」，主因论证/概念质量与
   本地模型修订能力——从素材组织（分节写作、一句一锚点）与修订手段（更强模型或分句修复）继续攻。
2. **finance 事实绑定**：若长期为主拦截项，专项优化「一句一锚点 + 整句引用」。
3. **few-shot 正例升级**：用 `independent_review_passed` 稿替换本地过稿正例。
4. **批次化成本**：DeepSeek 单轮 35–58s，3 轮上限下评审侧 2–3 分钟/篇，需评估预算与并发。
5. **G4 复测口径**：下一轮探针应带独立评审读数，否则只能报「链路产出率」。

## 已实测事实（2026-11-21 13:0x）

- G4 探针 40/40 完成，无断点续跑需求；导出件在 `07_tmp/r49_passrate/exports/r1/<类目>/`。
- DeepSeek 模型 = `deepseek-flash` / `deepseek-v4-pro`（`deepseek-chat` 已下线），
  reasoning 模型 max_tokens 需 ≥8000，否则空补全。
- 生产评审配置在 `00_control/config.json` review 段；`writing.independent_review_max=3`。
- venv = `.venv/Scripts/python.exe`（pytest 9.2.0）；脚本需 `PYTHONPATH=.`。
- 工作区提交后应仅剩：`00_control/scheduler_state.json`（运行时状态，不入库）、
  `00_control/supervisor_state.json.tmp`（运行时残留，不入库）、`03_intelligence/account_metrics/`（运行时数据）。

## 红线（继续有效）

只读自有头条账号，不碰他人数据；账号 profile / cookie / state 不入 git（`02_crawler/.account/`、
`00_control/account_metrics_state.json`）；两小时硬下限不得下调；`--force` 不得进调度；
调度器绝不触发写作/发布/模型；800 字门禁与规则 57/64 不得放宽；127.0.0.1:8080 Strata
(qwen3.8-flash-next-iq3_s, 147456 ctx) 配置/端口/量化不动；DeepSeek 评审密钥只允许
`.secrets/deepseek_api_key`；每完成一项独立提交并推送 origin/main（已预授权），推送失败如实报告并用
`git ls-remote` 核验；黄金集卡片文件一字节不改、sidecar 只加 ai_* 字段、human_adjudicated_count 保持 0、
AI 裁定≠人工裁定；生产类目库零写入（探针/烟测走 snapshot）；`01_categories/`、`资产库/`、`11_backups/` 不删；
主会话执行，子代理只派窄任务；few-shot 样本只来自实测回执，不编样本。

DONE
