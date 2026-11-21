# 会话检查点（压缩/新会话的唯一接手文件）

**这个文件的作用是"无损压缩"**：上下文被压缩、或换新窗口时，只相信这里写的事实，不靠对话记忆。
规则：每完成一个阶段、每次提交推送后、每次产生大输出（全量测试日志、全盘扫描、长报告）之后，必须重写本文件。
最后更新：2026-11-21 11:05 — **R61 在途**。远端 `main` = `302cff0`。

## 接手清单（R61 现在到哪了）

1. **黄金集重判已闭合并推送**（`586b5f2`）：12 张分歧卡第二轮重裁完成，实际执行者 =
   本地 127.0.0.1:8080 模型 qwen3.8-flash-next-iq3_s（DeepSeek 密钥缺失未用，身份如实记录）。
   证据 `10_tests/golden/adjudications/r61_recheck_local_round{,2}.json`（round1 自相矛盾存证未采用；
   round2 钉死①元问题后 12/12 自洽）。重判只写 ai_recheck_*，human_* 全空、human_adjudicated_count=0。
   生成器 `--check` 0 错误；CURRENT_BASIS_REREAD = 16 张（T-03 6 + T-15 10）；machine_label_drift=[]。
   批3/批4 测试钉已按新现实更新（含批4 sidecar SHA-256 重新锚定 `dedb6bad…`，卡片文件与纸面标签哈希钉未动）。
2. **G5 few-shot 已实现并推送**（`302cff0`）：`ttxt/writing/fewshot.py`（≤6 样本、只收 verified、
   库空/缺失/损坏自动降级）、`prompts.draft_prompt(topic, outline, materials, fewshot_text="")`、
   `workflow.write_article` 两处 draft 注入 + 回执 `fewshot_status/fewshot_count`；
   建库脚本 `scripts/build_fewshot_library.py`（正例=passed 且 score≥0.8 且 fc≥1.0，反例=fact.* 失败，
   每类目 ≤3+3，只从探针回执+导出 .md 逐字抽取，导出件缺失跳过不冒充，0 样本拒绝写库）；
   测试 `10_tests/test_r61_fewshot.py`。`00_control/fewshot_library.json` 现为空（降级生效），
   G4 探针结束后用建库脚本填充。
3. **测试运行被 G4 探针占锁阻塞**：golden 批3/批4 与 G5 测试都因 conftest 的
   `model_gate_lock_isolated` 断言（生产 gate.lock 必须不存在）无法运行。锁持有者 =
   PID 12548 = G4 探针本体（`scripts/pass_rate_probe.py --batch 07_tmp/r61_batch.json --rounds 1`，
   09:55:49 起，**红线：不杀不重启**）。Git Bash 的 `kill -0` 不认 Windows PID，用
   `wmic process where "ProcessId=12548" get ProcessId` 判断存活。探针 14/40（10:44 读数，
   7 过 7 驳，均值 202s/篇），预计 12:30–13:30 跑完。探针结束后第一件事：跑三组测试
   （test_p13_batch3、test_p13_batch4、test_r61_fewshot），再建 few-shot 库、写账本/报告。
4. **DeepSeek 已解锁（2026-11-21 11:05）**：用户已把 key 存进 `.secrets/deepseek_api_key`
   （35 字符、sk- 前缀，gitignore 覆盖，不入库）。实测端点可用，模型 =
   `deepseek-flash` / `deepseek-v4-pro`（`deepseek-chat` 已下线）；两者都是 reasoning 模型，
   max_tokens 需 ≥8000（2000 会被 reasoning 吃光 → empty completion）。
5. **DeepSeek 第二来源交叉验证已完成**：复用本地 round2 的同一 §6 钉死 prompt（未改一字），
   12/12 ③同意，与本地 round2 完全一致（v2h01 能核实、v2h03 对不上、其余 10 张对不上）；
   证据 `10_tests/golden/adjudications/r61_recheck_deepseek.json`（v4-pro，52s，6864 tokens）。
   **生产评审配置仍指本地**：另用 6 字评审 prompt 真跑了两次 DeepSeek，输出都没有命中现有
   blocking/publishable 标记（且「修改后可发布」会误命中「可发布」），直接切换会让独立评审门
   失效或误放行 —— 决定不切，先做 verdict 契约（自由评审后做标签抽取）并在样本上校准+测试，
   列入 R62 首项。空 key 文件已按缺失处理（`review.api_key` 修复 + 新增测试，待释锁后跑）。

## 已实测事实（2026-11-21 10:40）

- 探针无断点续跑：崩溃时须自建「剩余选题」batch（输出 runs 里没有的 topic）续跑并合并。
- 探针 run 记录字段：passed/score/fact_check_rate/primary/primary_detail/draft_id/topic/category；
  草稿导出在 `07_tmp/r49_passrate/exports/r1/<类目>/<draft_id>.md`（失败件在 `rejected/` 子目录）。
- 探针失败主因（前半段）：fact.binding（含 fact.binding 0.4/0.857 等）、other（本地评审
  verdict 阻断，非事实绑定）；R50 基线通过率 27.8%（5/18）。
- **当前评审 = 本地 127.0.0.1:8080 同款模型**（`same_model_review=true`，按 T-04 不算独立评审）；
  云端 DeepSeek key 已注销（`00_control/config.json` review.note），切回需新 key + 改配置。
- DeepSeek 端点实测模型列表 = `deepseek-flash`,`deepseek-v4-pro`；reasoning_tokens 会占
  max_tokens，评审调用需 ≥8000；12 张第二来源 = 12/12 与本地一致。
- 进程：supervisor/schedule/worker/probe 各 1 个逻辑实例（venv 启动器+基解释器成对 = 一个进程，非重复）。
- 工作区提交后应仅剩：scheduler_state.json（运行时状态，不入库）、08_wiki/session_checkpoint.md、
  未跟踪证据件（03_intelligence/account_metrics/、03_intelligence/pass_rate_probe-20261121-r61.json）。

## 红线（接手后继续有效）

只读自有头条账号，不碰他人数据；账号 profile / cookie / state 不入 git（`02_crawler/.account/`、
`00_control/account_metrics_state.json`）；两小时硬下限不得下调；`--force` 不得进调度；
调度器绝不触发写作/发布/模型；800 字门禁与规则 57/64 不得放宽；127.0.0.1:8080 Strata
(qwen3.8-flash-next-iq3_s, 147456 ctx) 配置/端口/量化不动；DeepSeek 评审密钥只允许
`.secrets/deepseek_api_key`；每完成一项独立提交并推送 origin/main（已预授权），推送失败如实报告并用 `git ls-remote` 核验；
黄金集卡片文件一字节不改、sidecar 只加 ai_* 字段、human_adjudicated_count 保持 0、AI 裁定≠人工裁定；
生产类目库零写入（探针/烟测走 snapshot）；01_categories/、资产库/、11_backups/ 不删；主会话执行，子代理只派窄任务；
G4 探针跑完前不杀不重启（PID 12548）；few-shot 样本只来自实测回执，不编样本。

## 还欠着的账（R61 剩余步骤）

1. 探针结束 → 跑 golden 批3/批4 + G5 测试 + test_p40 新增空 key 测试（预期全绿；
   红了按新现实修，不许回退钉到旧口径）。探针结束前不切 review 配置。
2. G4 读数：40 runs 通过率 vs R50 27.8%（5/18），social/finance 各 20 的失败主因分布。
3. 建 few-shot 库：`scripts/build_fewshot_library.py --probe 03_intelligence/pass_rate_probe-20261121-r61.json --exports 07_tmp/r49_passrate/exports`，建库后跑 1 篇 snapshot 烟测证明注入后链路不破。
4. 收尾：目标回归 + 账本（PROJECT_STATUS R61 段）+ 本检查点重写（最后一行 DONE）+
   R61 报告 `08_wiki/reports/r61-writing-batch-fewshot-golden.md` + 提交推送 + V3.0 第十五部分 8 项报告。
   报告须如实写：DeepSeek 第二来源 §6 重判仍阻塞（密钥缺失），12 张重裁实际执行者是本地模型。

5. R62 首项：DeepSeek verdict 契约（自由评审后做标签抽取：可以发布/修改后再评/不建议发布），
   先在一批 passed/rejected 草稿上校准并测试，再切 config；不改 6 字 prompt、不猜语义、不伪造。

旧账（本轮不做）：T-02 第二来源（DeepSeek 交叉验证已留证）、T-16 展现量/标签覆盖、
T-17 管线增量化、health 标定（26<30）。

NEXT: 探针（PID 12548）跑完 → 三组测试 → 建 few-shot 库 → snapshot 烟测 → 账本/报告收尾；生产评审切换留到 verdict 契约修复后。
