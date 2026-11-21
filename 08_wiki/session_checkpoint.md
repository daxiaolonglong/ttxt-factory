# 会话检查点（压缩/新会话的唯一接手文件）

**这个文件的作用是"无损压缩"**：上下文被压缩、或换新窗口时，只相信这里写的事实，不靠对话记忆。
规则：每完成一个阶段、每次提交推送后、每次产生大输出（全量测试日志、全盘扫描、长报告）之后，必须重写本文件。
最后更新：2026-11-21 11:20 — **R61 在途**。远端 `main` = `cff6c6f`。

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
   `powershell.exe -NoProfile -Command "Get-Process -Id 12548"` 判断存活。探针 23/40（15 过，
   11:16 读数，均值约 200s/篇），预计 12:10–12:20 跑完。探针结束后第一件事：跑四组测试
   （test_p13_batch3、test_p13_batch4、test_r61_fewshot、test_p40），再建 few-shot 库、写账本/报告。
4. **DeepSeek 已解锁（2026-11-21 11:05）**：用户已把 key 存进 `.secrets/deepseek_api_key`
   （35 字符、sk- 前缀，gitignore 覆盖，不入库）。实测端点可用，模型 =
   `deepseek-flash` / `deepseek-v4-pro`（`deepseek-chat` 已下线）；两者都是 reasoning 模型，
   max_tokens 需 ≥8000（2000 会被 reasoning 吃光 → empty completion）。
5. **DeepSeek 第二来源交叉验证已完成**：复用本地 round2 的同一 §6 钉死 prompt（未改一字），
   12/12 ③同意，与本地 round2 完全一致（v2h01 能核实、v2h03 对不上、其余 10 张对不上）；
   证据 `10_tests/golden/adjudications/r61_recheck_deepseek.json`（v4-pro，52s，6864 tokens）。
6. **DeepSeek verdict 契约 labels_v1 已实现并推送（`cff6c6f`）**：6 字盲评 prompt 一字不动；
   审评完再调一次，只从「可以发布/修改后再评/不建议发布」抽一个标签；可以发布=放行，其余
   （含解析失败）=拦截；`review.py` 加契约常量/解析/映射，`workflow.py` 评估记录记 `verdict_label`；
   `test_p40` 新增 2 组测试 + 空 key 文件测试（均待释锁跑）。
   **扩样本校准已完成（8 篇，证据 `03_intelligence/r61_verdict_calibration.json`）**：
   - 4 篇本地过稿 → 全部「修改后再评」；4 篇本地驳稿 → 3 篇「修改后再评」+1 篇「不建议发布」；
   - **0 篇「可以发布」**；抽取 0 错误、0 通道不可用；端点 https://api.deepseek.com，
     模型 deepseek-v4-pro（24–63s/篇）；
   - 真实硬伤（不是空泛差评）：重复句/标点缺失（finance e1abe2）、素材拼贴/标题党
     （07d6d2、383ddb）、文本循环/文不对题（aaeea0，本地 0.1 分）。
   - **决定：标签语义不放宽（非「可以发布」一律不给独立标签）；生产 review 配置暂不切 DeepSeek。**
     理由：按当前映射校准集 8/8 全拦，现管道会 0 产出，且没有「按独立评审意见修订→复审」的回路。
     先做修订回路（R62 首项），回路可用后再切生产评审并建独立基线；否则规则等于停机，不是验收。
7. **测试解耦（未跑）**：`test_p40` 的「请求只带 6 字 prompt」测试改为显式回环 section，不再绑
   生产 review 配置；否则切云端后会读真实 key、并因 `verdict_contract` 触发第二次抽取调用。
   生产端点仍由 R51 的 `test_production_review_endpoint_is_policy_compliant` 单验。

## 已实测事实（2026-11-21 11:16）

- 探针 23/40、15 过；失败主因：`fact.binding`、`other`（本地评审阻断）、`gate.structure_truncated`。
  探针无断点续跑：崩溃时须自建「剩余选题」batch（输出 runs 里没有的 topic）续跑并合并。
- 探针 run 记录字段：passed/score/fact_check_rate/primary/primary_detail/draft_id/topic/category；
  草稿导出在 `07_tmp/r49_passrate/exports/r1/<类目>/<draft_id>.md`（失败件在 `rejected/` 子目录）。
- R50 基线通过率 27.8%（5/18）；探针当前 65.2%，但评委是本地同源评审，不能当独立质量结论。
- **当前生产评审 = 本地 127.0.0.1:8080 同款模型**（`same_model_review=true`，按 T-04 不算独立评审）。
- DeepSeek verdict 校准：8 篇 0 篇「可以发布」，7 篇「修改后再评」、1 篇「不建议发布」；
  评审确实指出了重复句、标点、标题党、文本循环等可修问题。
- DeepSeek 端点实测模型列表 = `deepseek-flash`,`deepseek-v4-pro`；reasoning_tokens 会占
  max_tokens，评审调用需 ≥8000；12 张第二来源 = 12/12 与本地一致。
- 进程：supervisor/schedule/worker/probe 各 1 个逻辑实例（venv 启动器+基解释器成对 = 一个进程，非重复）。
- venv = `.venv/Scripts/python.exe`（pytest 9.2.0）；系统 `python` 无项目路径，脚本需 `PYTHONPATH=.`。
- 工作区提交后应仅剩：scheduler_state.json（运行时状态，不入库）、08_wiki/session_checkpoint.md、
  未跟踪证据件（03_intelligence/account_metrics/、03_intelligence/pass_rate_probe-20261121-r61.json、
  03_intelligence/r61_verdict_calibration.json 视报告需要入库）。

## 红线（接手后继续有效）

只读自有头条账号，不碰他人数据；账号 profile / cookie / state 不入 git（`02_crawler/.account/`、
`00_control/account_metrics_state.json`）；两小时硬下限不得下调；`--force` 不得进调度；
调度器绝不触发写作/发布/模型；800 字门禁与规则 57/64 不得放宽；127.0.0.1:8080 Strata
(qwen3.8-flash-next-iq3_s, 147456 ctx) 配置/端口/量化不动；DeepSeek 评审密钥只允许
`.secrets/deepseek_api_key`；每完成一项独立提交并推送 origin/main（已预授权），推送失败如实报告并用
`git ls-remote` 核验；黄金集卡片文件一字节不改、sidecar 只加 ai_* 字段、human_adjudicated_count 保持 0、
AI 裁定≠人工裁定；生产类目库零写入（探针/烟测走 snapshot）；`01_categories/`、`资产库/`、`11_backups/` 不删；
主会话执行，子代理只派窄任务；**G4 探针跑完前不杀不重启（PID 12548）**；few-shot 样本只来自实测回执，不编样本；
**探针结束且四组测试通过、独立修订回路可用前，不把生产 review 配置切到 DeepSeek**（规则已就绪，但先修恢复路径）。

## 还欠着的账（R61 剩余步骤）

1. 探针结束 → 跑四组测试（test_p13_batch3、test_p13_batch4、test_r61_fewshot、test_p40 含新增
   contract/空 key 测试）；预期全绿，红了按新现实修，不许回退钉到旧口径。
2. G4 读数：40 runs 通过率 vs R50 27.8%（5/18），social/finance 各 20 的失败主因分布；
   报告必须注明评委是本地同源评审。
3. 建 few-shot 库：`scripts/build_fewshot_library.py --probe 03_intelligence/pass_rate_probe-20261121-r61.json
   --exports 07_tmp/r49_passrate/exports`，先 `--check` 再写；建库后跑 1 篇 snapshot 烟测
   （`--work 07_tmp/r61_smoke`，不得占用主探针目录），并证明 `fewshot_status/count` 注入。
4. 收尾：账本（PROJECT_STATUS R61 段）+ 本检查点重写（最后一行 DONE）+ R61 报告
   `08_wiki/reports/r61-writing-batch-fewshot-golden.md` + 提交推送 + V3.0 第十五部分 8 项报告。
   报告须如实写：黄金集 12 张重裁实际执行者是本地模型、DeepSeek 只做第二来源；verdict 校准
   8 篇 0 篇可以发布；生产评审仍未切，原因是修订回路未就绪。
5. R62 首项（本轮不做）：独立评审修订回路 —— 用 `verdict_text` 作修订反馈，本地修订→重核事实→
   重评→再独立评审，最多 N 次；只有「可以发布」给独立标签，「不建议发布」硬拒；回路可用后再切
   生产 review 配置（base_url=https://api.deepseek.com、model_id=deepseek-v4-pro、
   api_key_env=DEEPSEEK_API_KEY、api_key_file=.secrets/deepseek_api_key、verdict_contract=labels_v1），
   并重跑探针建立独立基线。6 字 prompt 不改、不猜语义、不伪造。

旧账（本轮不做）：T-02 第二来源（DeepSeek 交叉验证已留证）、T-16 展现量/标签覆盖、
T-17 管线增量化、health 标定（26<30）。

NEXT: 探针（23/40）结束 → 跑四组测试 → 建 few-shot 库 + snapshot 烟测 → G4 读数/账本/R61 报告/检查点 DONE；R62 首项 = 独立评审修订回路，回路可用后再切生产评审。
