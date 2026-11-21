# 会话检查点（压缩/新会话的唯一接手文件）

**这个文件的作用是"无损压缩"**：上下文被压缩、或换新窗口时，只相信这里写的事实，不靠对话记忆。
规则：每完成一个阶段、每次提交推送后、每次产生大输出（全量测试日志、全盘扫描、长报告）之后，必须重写本文件。
最后更新：2026-11-21 13:20 — **R61 已完成（DONE）；R62 第一项已落地，继续推进。**
远端 `main` = `44706f4`（本检查点为紧随其后的单独提交，最终哈希以 `git ls-remote origin main` 为准）。

## R62 用户新规（2026-11-21，已落地）

**第 1 次评审用 `deepseek-flash`，第 2 次用 `deepseek-v4-pro`，第 3 次用 `deepseek-flash`。**

- 配置：`00_control/config.json` review 段
  `"round_models": ["deepseek-flash","deepseek-v4-pro","deepseek-flash"]`（顺序即轮次；
  未配置时回退 `model_id`，现为 `deepseek-v4-pro`）。
- 实现：`ttxt/writing/review.py::model_for_round(round_no)` 选型；
  `review(draft, round_no=N)` 正文盲评与 verdict 标签抽取**同用该轮模型**；回执/日志记 `review_round`。
- 写作回路 `workflow.write_article` 把第 1/2/3 次评审轮次真实传入；
  `independent_review_rounds[].reviewer_model` 即每轮实际模型。
- 端点核验（13:18）：round1/3 = `deepseek-flash`、round2 = `deepseek-v4-pro`，两模型均在可用列表。
- 测试：test_p40 新增 4 项（轮次模型序列、缺省回退、生产配置钉、回路传轮次）；
  连同 few-shot/copyedit **54 passed / 0 error**（13:17 实跑）。
- 提交：`44706f4`（已推送并 `git ls-remote` 核验）。

## R61 最终状态（已完成）

1. 黄金集 12 张分歧卡重裁：本地钉死 §6 模板 12/12 自洽；DeepSeek 第二来源 12/12 ③同意；
   卡片未动、sidecar 只写 ai_*、human_adjudicated_count=0；生成器 --check 0 错误、61/61 对齐。
2. DeepSeek 生产评审已切换；切换前校准 8 篇 → 0 可以发布、7 修改后再评、1 不建议发布、0 抽取错误。
3. 评审 ≤3 次修订回路（`writing.independent_review_max=3`）：
   `修改后再评` 回流修订→重核事实→复审；`不建议发布` 立即拒；额度耗尽即拒；无死循环。
4. G4 40 篇复验：通过 **22/40 = 55.0%**（R50 27.8%，5/18）；判官是本地同模型评审，
   只能当链路产出率，不是独立质量结论（`03_intelligence/pass_rate_probe-20261121-r61.json`）。
5. few-shot 库 11 条实测样本（social 5、finance 6；正例 6、反例 5），库缺失/空/坏自动降级；
   正例仅本地门禁通过、未获 DeepSeek 认证（R62 应改用独立通过稿）。
6. 0 产出根因：相邻重复、材料拼贴/锚点越窗、编造/占位来源、标题超证据、修订截断/审稿说明、
   本地评审偏松、本地修订改不动概念/论证、finance 事实绑定硬门槛；3 主题 4 次烟测 0 可发布。
7. 已修：确定性去重、修订截断重试、草稿/修订提示词收紧；门禁阈值全部未动。
8. 报告：`08_wiki/reports/r61-writing-batch-fewshot-golden.md`；账本 R61/R62 段已更新。

## 接手后下一步（R62）

1. **按新轮次模型跑真实烟测/批次复验**：验证第 1/3 次 flash、第 2 次 v4-pro 的实际判定与耗时；
   继续攻「可发布产出 0」的论证/概念质量与素材组织（分节写作、一句一锚点），不放宽门禁。
2. finance 事实绑定若长期为主拦截项，专项优化「一句一锚点 + 整句引用」。
3. few-shot 正例升级为 `independent_review_passed` 稿。
4. 批次化前评估 DeepSeek 成本/时延（单轮 35–58s，3 轮上限下评审侧 2–3 分钟/篇）。

## 已实测运维事实

- **测试隔离陷阱**：只停 supervisor 会留下孤儿 `ttxt schedule --daemon` 进程，继续写
  `09_logs/*` 与类目库，触发 conftest 生产隔离门禁失败。跑测试前必须确认
  `Get-CimInstance Win32_Process` 里没有 `-m ttxt schedule --daemon`（venv 启动器 + 基解释器
  成对出现），有则 `Stop-Process`，跑完再 `supervisor --ensure` 恢复。
- 恢复口令：`worker --clear-stop` → `supervisor --clear-stop` → `supervisor --ensure`；
  当前 supervisor/scheduler/worker 已恢复在线（13:19 核验）。
- venv = `.venv/Scripts/python.exe`（pytest 9.2.0）；脚本需 `PYTHONPATH=.`。
- 工作区提交后应仅剩：`00_control/scheduler_state.json`（运行时）、
  `03_intelligence/account_metrics/`（运行时数据）。

## 红线（继续有效）

只读自有头条账号，不碰他人数据；账号 profile / cookie / state 不入 git（`02_crawler/.account/`、
`00_control/account_metrics_state.json`）；两小时硬下限不得下调；`--force` 不得进调度；
调度器绝不触发写作/发布/模型；800 字门禁与规则 57/64 不得放宽；127.0.0.1:8080 Strata
(qwen3.8-flash-next-iq3_s, 147456 ctx) 配置/端口/量化不动；DeepSeek 评审密钥只允许
`.secrets/deepseek_api_key`；每完成一项独立提交并推送 origin/main（已预授权），推送失败如实报告并用
`git ls-remote` 核验；黄金集卡片文件一字节不改、sidecar 只加 ai_* 字段、human_adjudicated_count 保持 0、
AI 裁定≠人工裁定；生产类目库零写入（探针/烟测走 snapshot）；`01_categories/`、`资产库/`、`11_backups/` 不删；
主会话执行，子代理只派窄任务；few-shot 样本只来自实测回执，不编样本。

NEXT: 按新轮次模型（flash→v4-pro→flash）跑真实烟测/批次复验，继续攻可发布产出 0 的论证与素材组织问题。
