# 会话检查点（压缩/新会话的唯一接手文件）

**这个文件的作用是"无损压缩"**：上下文被压缩、或换新窗口时，只相信这里写的事实，不靠对话记忆。
规则：每完成一个阶段、每次提交推送后、每次产生大输出（全量测试日志、全盘扫描、长报告）之后，必须重写本文件。
最后更新：2026-11-21 13:26 — **R61 已完成（DONE）；R62 轮次模型规则 + 首次真实烟测已完成，继续推进。**
远端 `main` 以 `git ls-remote` 实测为准：R62 规则提交 = `44706f4`；本次烟测回执/检查点为紧随其后的提交。

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

## R62 首次真实烟测（2026-11-21 13:26，延迟退休 养老金 / social）

- 结果：rejected，2 次评审后停（round1 `deepseek-flash` 21.3s、round2 `deepseek-v4-pro` 63.7s，
  均为「修改后再评」）；round3 未花——第 2 轮修订后本地事实绑定 critical 先拒（符合"门禁拦下不花评审"）。
- 轮次模型规则生产生效，回执逐轮模型可审计；总耗时 404.9s，10 版 4 修，本地 score 0.85、fc 1.0。
- 剩余硬伤：正文写「提前1至3个月查询社保缴费记录」——材料无此数字，锚点 3 越出 quote 窗口；
  引用把「渐进式延迟退休年龄」写成「渐进式延迟法定退休年龄」，quote 缺「法定」被判不支撑。
- 证据：`03_intelligence/r61_smoke_receipt_r62_delay_models.json`。

## R63 代码+同题烟测（2026-11-21 15:20，延迟退休 养老金 / social）

- 已推送 `2752746..7281e68` 四个提交（快进，未 force）：去重升级/三侧提示词收紧/列举承诺线索
  （只进 issues）/用例。报告：`08_wiki/reports/r63-writing-tightening-smoke.md`。
- 烟测（单任务独占模型 326.8s，回执 `03_intelligence/r63_smoke_receipt_r63_dedup_promises.json`）：
  rejected；versions 8、revisions 4、score 0.6、fc 1.0（6/6）；两轮独立评审 flash 19.3s →
  v4-pro 43.7s 均「修改后再评」；模板 within_budget。
  **移花接木（提前1—3年）与引用增字（法定/统筹兼顾）两类 critical 消失**——生成侧收紧命中；
  新停点是「有人退、有人退」（3 字短分句重复，低于去重门槛 4 字，按设计不删）。
  copyedit 0 处、列举线索 0 条（未承诺数量，非失败）；repair：claims 44/失败 11/修好 8/余 3（quote_unbound）、
  anchor_erased 4（守卫生效）。
- 单测：指定 6 文件 103 passed；全量 A/B 新代码 1041 passed/1 failed(环境)/23 errors(环境，基线同 23)，
  基线代码 12 failed（11 个正是新用例在基线失败）。
- 事故：15:20 首跑与全量单测并发抢单实例模型（first payload timeout），且因 `ttxt/config.py:10`
  硬编码 `D:/ttxt` 写进生产库（`writing_history` 6 行 draft `social-20261121151525-13cb57`、
  evaluation_history、导出 md、model_jobs 行）。该回执留档为 `r63_smoke_receipt_concurrent_1520.json`；
  生产数据未删（需授权）。规避：显式 `db_path`/`export_dir`；探针自带快照。
- 运维事实：worktree 用系统 Python 3.12.10 + `D:\ttxt\.venv\Lib\site-packages`（PYTHONPATH）；
  测本副本须 `-p _r63_warm`（conftest 会把生产目录插到 sys.path[0]）。**单任务串行，绝不并发跑模型**。
- 进行中：10 题同题族子集复测（R61 G4 social 同 10 题基线 6/10），
  输出 `03_intelligence/pass_rate_probe-20261121-r63.json`。

- 10 题批量复测（同题族，1 轮，快照库，`template_decheck=False`）：
  R63 **0/10**（270s/篇）、**基线代码同期复测也 0/10**（265s/篇，失败分布相同）
  → 本轮读数是素材主题漂移+本地模型能力所致，**不是 R63 回归**；与 R61 G4 的 6/10 不可比（时间窗/库/评审口径）。
  未达成"把可发布产出打出来"，如实记未达成。
- 耗时实测（421 调用/20 篇，均 9.5s）：repair 8.4 次/篇（4.4s，第二轮从未修好）> 本地评审 3.9 次（11s）
  > claims 3.9 次（16.9s）> revise 2.9 次（14.1s）。单实例服务（`--parallel 1`）对 2 并发请求
  **排队不报错**（job_timeout 600s），但**不提吞吐**。
- 提速尝试（均回退，报告 §6/§7）：①"原地打转早停"5 题实测无提速（323s/篇 vs 308s/篇，版本数 3–10 没变，
  修复/校对几乎每轮都有改动）→ 回退不留死代码；②`MAX_REPAIR_ROUNDS=1` 被 `test_r53` 契约否决 → 回退。
- 最快方案（需拍板，报告 §8）：① Strata 加并发槽 `--parallel 2`（~2×，需重启服务/授权；已实测 2 并发排队不报错）
  ② 砍尝试次数（门禁修订 2→1、独立评审 3→2，动 R61 用户令配额）③ 素材相关性闸（打主题漂移，R64 首选）
  ④ 修复批量化（~10%）。

NEXT: 5 题快速复测（验证早停耗时）跑完后补进报告 §7 与本节；提交代码+报告+回执+检查点并推送；剩余未达成项按报告 §6 由用户拍板。
