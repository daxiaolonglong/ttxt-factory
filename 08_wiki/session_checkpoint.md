# 会话检查点（压缩/新会话的唯一接手文件）

**这个文件的作用是"无损压缩"**：上下文被压缩、或换新窗口时，只相信这里写的事实，不靠对话记忆。
规则：每完成一个阶段、每次提交推送后、每次产生大输出（全量测试日志、全盘扫描、长报告）之后，必须重写本文件。
最后更新：2026-11-21 22:48 — **R66 代码、全量回归、同题烟测与证据均已推送；真实 L4 仍未通过。**
远端 `main` 以 `git ls-remote` 实测为准：R65 = `0c19819`；R66 代码/检查点/证据已推
`0c19819..62db747`（快进，未 force）；最新核验 `62db747` = R66 证据提交（回执+报告）。
本检查点更新随后单独提交推送，其哈希以 `git ls-remote` 为准。

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

（R63 收尾完成：报告 `08_wiki/reports/r63-writing-tightening-smoke.md`，`ca902a0` 已推送；
早停与 MAX_REPAIR_ROUNDS 两条提速尝试已回退，§8 提速项待用户拍板。）

## R64 素材相关度闸 + 数据驱动选题（2026-11-21 19:46，全程零模型调用）

- 已推送 `ca902a0..9016a1b` 两个提交（快进、未 force；`git ls-remote` 已核）：
  `a02691d` R64-1 相关度闸（`rag.topic_relevance` + `_rank_materials(topic=…)` 接线 + 门禁文案归因 + P41 用例）、
  `9016a1b` R64-2 选题层（新增 `ttxt/analysis/topics.py`、`ttxt topics` CLI、P42 18 用例）。
  报告：`08_wiki/reports/r64-material-relevance-data-driven-topics.md`。
- **R64-1 前移的口径红线未动**：`material_min_full=6`/`material_min_sources=3` 仍只从 `config.writing` 读，
  没有新增阈值；判据写在 `rag.topic_relevance`（标题命中≥1 且每千字≥2.0，或每千字≥8.0；切不出 token ⇒ 相关并 `skipped`）。
- 生产快照 A/B（5 类目 48 个 active keyword，同一候选池，`07_tmp/r64_snap/gate_ab.json`）：
  过滤前 **48/48 放行**，过滤后 **39 放行 / 9 拦**（美女/财报/神器/降价/科学家/特朗普/健康/多人/文明）；
  窗口内离题候选剔除 **220** 条（上限 720），仅 15/48 主题零剔除，16/48 的相关全文被砍半以上
  （警方 15→6、特朗普 14→2、多人 15→2）⇒ 此前门禁从不防"离题稿凑数"。
- 选题层实跑（快照只读，`07_tmp/r64_snap/evidence/*.json`）：social 13→12、finance 14→12、
  technology 10→7、health 6→4、history 5→4、demo 0→0（无 active 关键词如实报空）。
  共 **39 个系统提出且当场语料撑得起的主题**（对比 R63 人工整标题出题 0/10）。
- 已钉死的设计事实：整条爆款标题**不是主题**（FTS 按标题 token AND ⇒ 只召回自己，相关全文 1/独立来源 1），
  且不进候选池、只作参考（`used_for="reference_only_not_topic"`）；爆款→主题的正规通道是
  `analyze → analysis/keywords.mine_keywords`（写 `keywords.origin='mined'`，`analyze.py:42` 是唯一调用点）。
- 测试基线：全量 `10_tests` **1092 passed（331.57s，exit 0）**。首跑的 1 failed 是仓库根残留探针垃圾
  `D:/ttxt/tmpq092s9ax`（合成 `fewshot_library.json`）触发 p38 守卫，已删该垃圾目录
  （生产 `00_control/fewshot_library.json` 11854 字节、`01_categories/`、`资产库/`、`11_backups/` 未动）。
- 运维坑（本轮新增）：本 worktree 分支是 `r63-writing-tightening`，主 worktree `D:/ttxt` 的本地 `main`
  停在 R62 的 `2752746`（落后 origin/main）⇒ **`git push origin main` 会推错分支被拒**；
  推送一律用 `git push origin HEAD:main`，并以 `git ls-remote origin refs/heads/main` 核验。
- 下一个缺口（候选广度）：active keyword 每类目仅 5–14，`mined` 稳定卡 10（=`MINE_TOP_N`，
  每轮只出 10 个且不跨轮累积）⇒ 主题池上不去。

## R65 关键词 harvested 闭环 + 采不动的词认账退场（2026-11-21 21:13，全程零模型调用）

- **断点（只读生产实测，`D:/ttxt/07_tmp/r65_harvest/harvest.json`）**：5 类目 56,685 篇、38 个
  `origin='mined'` 挖词里 **29 个自被提出起一篇新文章都没采回**（social 10/10 零带回，`闷声发财`
  标题命中 399 却 0 带回；finance 9/10、history 5/7、health 1/1；对照组 technology `中国` 107→45、
  history `毛主席` 32→16 说明"带回"真会发生）。根因：挖词天生有覆盖 ⇒ 永远进不了零覆盖 `gap`
  ⇒ `auto_feedback` 从未为它入过定向任务；`collected` 是全库覆盖，证明不了词表扩张。
- **新口径**：`keyword_performance.harvested` = 标题命中（别名归一）且 `articles.fetched_at >
  keywords.created_at` 的篇数；时间读不动 ⇒ `NULL`（unknown，不进退场判定）。只加列加指标，
  `collected/score/权重/阈值/冷却` 一律未改；迁移幂等只加列、不动既有评分行。
- **闭环**：`auto_feedback` 的 `keyword_filter` = gap 词 ∪ `harvested=0` 的挖词；逐词台账
  `dispatch_state.keyword_feedback.per_keyword.directed_attempts`（只有真实入队才 +1）；
  复审轮仍 0 且满 `MINE_FEEDBACK_MAX_ATTEMPTS=3` ⇒ `status='exhausted'`（留关键词行留评分行）；
  复活凭证只有 `harvested>0`。退场只关采集侧：`keyword_crawl` 选词排除，`topics.keyword_candidates`
  仍收它（采不到新文章 ≠ 写不出来）；`mine_keywords` 的 prune 保护 `harvested>0` 的旧挖词。
- **真实库验证（生产零写入）**：`07_tmp/_r65_prod_verify.py` 把 5 个类目库 `mode=ro`+`backup()`
  到副本跑 `migrate→score_keywords→auto_feedback`（`D:/ttxt/07_tmp/r65_verify/verify.json`）：
  实现代码读数与探针**完全一致**（38 挖词 / 29 零带回 / 9 有带回），且 social `gap=0`
  也能定向 10 个词（旧逻辑这里一条任务都不入）、finance 9、history 5、health 1、
  technology gap 1 + 定向 4。生产 5 库当前仍无 `harvested` 列（实测 PRAGMA 7 列）
  ⇒ `run_analysis` 首步 `migrate_articles_db`（`analyze.py:32`）会在下一次调度自动加列回填，无需人工改库。
- 报告：`08_wiki/reports/r65-keyword-harvest-loop.md`（含 §7 未覆盖项）。
- 测试基线：全量 `10_tests` **1101 passed（291.63s，exit 0）** = R64 基线 1092 + R65 新增 9，零回归。
- 已钉死的新事实：`harvested` 是"词表有没有真的把语料撑大"的唯一凭证；`collected` 不是。
  判定发生在能看到上一次补采回执之后的复审轮，不在入队当轮判死。

## R66 Qoder 接手与写作质量修复（2026-11-21，代码+全量+烟测已完成；真实 L4 未通过）

- **环境已核实**：本次 Qoder 工作树 `C:\Users\yangl\.qoder\worktrees\app\d88451\ttxt` 原为 detached HEAD
  `0c19819`（=origin/main=R65，干净）；已新建分支 `r66-writing-quality`。主工作树 `D:/ttxt` 本地 main
  仍停 `2752746`（落后 11），推送必须 `git push origin HEAD:main` 并 `git ls-remote` 核验。
  Strata `/health` 实测 200；`D:/ttxt/.secrets/deepseek_api_key` 存在；系统 Python 3.12.10 无 pytest，
  需 `PYTHONPATH=D:/ttxt/.venv/Lib/site-packages:.`（`D:/ttxt/.venv` 内 pytest 可用）。
- **R63 唯一 critical 根因证据（已复算）**：稿中 `有人50岁退、有人55岁退，受地方政策…` 在来源
  `social/articles.db id=18622` 上有逐字支撑（“有人50岁就顺利办退休…有的人却被告知要等到55岁”），
  但整句与来源句词面重叠仅 **0.30 < QUOTE_MIN_OVERLAP=0.34** ⇒ `quote_unbound`；repair 两轮未修好；
  revise 按提示词“删数字留动作”把句子改成 `有人退、有人退`，独立评审判内容截断。
  实测把句子拆成单锚点 `有人50岁退。` / `有人55岁退。` 后，`verify_claim` 均 **verified**（引句 18622）。
- **代码已推送（`fa1318a`，分支 `r66-writing-quality`）**：
  1. `ttxt/writing/style.py` 新增 `_split_two_anchor_sentence` / `_split_multi_anchor_line` /
     `_hard_numbers`：仅当一句里恰好两个 ASCII 数字锚点、分处不同分句、非日期句时，做标点级拆分
     （“一句一锚点”），不增删字词；引用标记 `[n]` 随每个事实句保留；标题/表格/列表/多锚点政策句
     一律不动；拆不动原样返回；接入 `copyedit_draft`，记为 `multi_anchor_split` 供回执审计。
  2. `ttxt/writing/prompts.py`：`revise_prompt` 保留“删数字、保留动作与条件”的既有条文，新增
     禁止“有人退、有人退”这类残缺/相邻重复/无主干碎片，要求一句一个数字锚点；
     `repair_sentence_prompt` 增加同一禁令。未改任何门禁阈值、verifier 判定、评审配额。
  3. 新增 `10_tests/test_r66_one_anchor_split.py`（6 条）：拆分后两句都能对来源 18622 逐字绑定；
     标题不拆、日期句/多锚点句不拆、提示词条文不回退。
- **全量回归（服务停用窗口实跑，409.26s，日志落盘 22:28:51）**：停 supervisor/scheduler/worker 后
  `python -m pytest 10_tests -q -p _r66_warm --basetemp=D:/ttxt/07_tmp/pytest_basetemp/r66_full`
  → **1107 passed，2 warnings，409.26s，exit 0**。R65 基线 1101 + R66 新增 6，零回归；两条 warning
  为环境类（浏览器锁测试子进程编码、`.pytest_cache` 在 worktree 不可写）。同一停服窗口随后跑烟测，
  22:43:34 按 `worker --clear-stop → supervisor --clear-stop → supervisor --ensure` 恢复在线。
- **同题烟测（22:30:47–22:42:40，712.4s，延迟退休 养老金 / social）**：
  rejected；score **0.95**；fact checkability **9/9 = 1.0**；8 版 4 修；copyedit 1 处 =
  `multi_anchor_split`；3 轮独立评审（flash 25.4s → v4-pro 70.8s → flash 28.6s）**均「修改后再评」**。
  **R63「有人退、有人退」critical 消失**；拆分实锤：
  `同样是灵活就业身份，有人50岁顺利办退休领养老金[3]。有人却被告知要等到55岁才能办[3]。`
  repair：claims 43 / failed 12 / fixed 10 / unfixed 2（均 `grafted` 锚点越窗）；
  `anchor_erased=4`（守卫仍挡“删事实换放行”）。回执
  `03_intelligence/r66_smoke_receipt_one_anchor.json`；报告
  `08_wiki/reports/r66-one-anchor-split-smoke.md`。
- **生产零写入已核**：烟测前后生产 `00_control/system.db`、social `articles.db`、`09_logs` 全量
  size/mtime 指纹 `production_guard.unchanged=true`；生产 `model_jobs` 仍 2242 行全 done、social 仍
  40746 行；模型任务只写 `07_tmp/r66_smoke/00_control/system.db` 副本（2268 行）。隔离靠驱动器
  运行时 patch `cfg.LOG_DIR` / `db.CONTROL_DIR` / `db.CATEGORIES_DIR` / `gate.GATE_LOCK` /
  `queue.system_db_path`，**不是代码级保护**。
- **剩余 blocker（新实测事实）**：第 1、3 轮独立评审点名「引用不规范/来源说明缺失、没有参考文献」；
  第 2 轮指出正文弹性退休表述重复错位（「符合条件可最多提前3年」写了两遍，延后 3 年却说成提前）、
  「原55岁女干部」术语不严谨、标题与内容不匹配；本地 `issues` 记标题词面覆盖 44%、「打工人」偏口语、
  第二段偏长。前 5 篇素材（selected_ids 3747/12004/18622/22884/3978）**全部是 toutiao.com 自媒体**，
  `selected_distinct_hosts=1`；只在文末补「参考资料」会把自媒体来源暴露得更清楚，不能解决权威性。
  下一步必须补权威来源采集（政策原文/人社部/中国政府网/中新网政策稿）+ 权威优先排序 + 引用格式，
  再评估独立评审；同时按第 2 轮意见修正政策措辞与标题兑现；不得编造参考文献。
  **另实测**：最终稿引用 [1]–[5]，但导出 `evidence` 9 条只来自 3747（8 条）/18622（1 条），
  [2]/[4]/[5] 无对应证据行 ⇒ 引用编号与证据表不闭合，补文末来源时必须同时打通
  `materials → [n] → evidence → 文末来源` 映射。
- **安全缺口未修**：`ttxt/config.py:10` 仍硬编码 `PROJECT_ROOT=Path("D:/ttxt")`；R63 已发生
  worktree 烟测误写生产库 6 行，本轮零写入只是驱动器打补丁。R67 做 env 覆盖 + worktree 写保护。

NEXT: ①R67 修 `ttxt/config.py` 根目录（env 覆盖 + worktree 写保护），杜绝探针/烟测再误写生产库；
②权威来源采集与引用规范（官方政策原文/人社部/中新网），再跑同题烟测验独立评审是否放行；
③R65 遗留：挖词边界证据与 `MINE_TOP_N=10` 跨轮累积，另开一轮，不与写作修复混做。
真实 L4 仍未达成，不得以本轮修复或 1107 passed 宣布工厂完成。
