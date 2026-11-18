# 会话检查点（压缩/新会话的唯一接手文件）

**这个文件的作用是"无损压缩"**：上下文被压缩、或换新窗口时，只相信这里写的事实，不靠对话记忆。
规则：每完成一个阶段、每次提交推送后、每次产生大输出（全量测试日志、全盘扫描、长报告）之后，必须重写本文件。
最后更新：2026-11-18 10:35（**阶段1 已闭合并推送成功**，远端 `main` = 本地 `HEAD` = `b14fffbd316a93b82739fa784c36ce0f944b8060`）

## 接手三行

1. **不要重做已闭合的事**：阶段0 磁盘止血（账本 T-19）和阶段1 取材接线（账本 **T-20**）都已完成、已测、已推送。磁盘读数见下节，取材接线见 `ttxt/writing/workflow.py` 的 `_rank_materials` / `_material_gate`，回归是 `10_tests/test_p39_material_prior_gate.py`（11 项）。
2. **下一个动作 = 阶段2（账本 T-04/T-05，L4 独立评审通道）**：现在 `_model_meta()` 仍硬编码 `same_model_review: True`、`_save_evaluation` 的 `evaluator` 固定 `"model_review"`（都在 `ttxt/writing/workflow.py`）。要做的是新增**只读** `review` 通道（DeepSeek-V4.1-Flash，提示词固定 6 字「审评这篇文章」），复用 `ttxt/model_gate/client.py:chat(base_url=, model_id=)`，并把「评审模型与写作模型同源 ⇒ 不得算 L4 通过」写成测试（`test_p12_fact_verifier.py`、`test_p13_batch2_history_accounting.py` 里有写死的同源断言要一起改）。**动手前先探端点是否可用**，两次失败就换思路。
3. **密钥红线**：DeepSeek 评审密钥只允许待在 `.secrets/deepseek_api_key`，不得进文件/日志/回执/提交/对话回显；127.0.0.1:8080 写作模型（`qwen3.8-flash-next-iq3_s`）的配置/端口/量化一律不动，只许在旁边新增 `review` 段。

## 已实测、不需要再确认的事实（2026-11-18 本机读数）

- **写作取材（本轮新增，生产实测 10:08–10:19）**：候选池 `MATERIAL_POOL_LIMIT=15 → 喂 MATERIAL_TOP_K=5`；`ttxt write --category social --topic 养老金` 退出码 0，回执 `04_writing/receipts/write-social-pension-20261118.json`：`pool 15 / candidates 15 / full 15 / distinct_sources 14 / distinct_hosts 1`，`calibrated` 但 **`measured_label_hits 0`**；选中 5 篇（id 19794/12222/1717/15408/11105）全是 full、来自 5 个不同作者。产物 `04_writing/drafts/social/rejected/social-20261118101123-549323.md`（v3、score 0.85、claim 引到 [1]–[5]、无三段式模板），social 首次落 `writing_history` **3 行**（942/977/979 字符，`model_id=qwen3.8-flash-next-iq3_s`）。**仍被事实门禁拒绝**：2 条 `原文引句未绑定` + 1 条 `dates 锚点不在引句窗口但在正文别处出现(移花接木嫌疑):2026年`。
- **独立来源口径**：实测**每个正式类目只有一个域名**（social/technology/finance/history 全是 toutiao.com，health 全是 who.int）⇒ 按域名计阈值 3 对任何选题都不可达；已改为**作者账号优先、缺作者退回域名、缺作者不计数**（social：40,526 行里 40,205 行有作者、18,949 个作者）。
- **语料与标定规模**：social 40,526（full 40,520 / short 6，calibrated，measured 80）、finance 12,748（calibrated，measured 46）、technology 330（calibrated，measured 200）、history 160（calibrated，measured 46）、health 26（**未标定**，26 < `viral.MIN_CALIBRATION_SAMPLE=30`，作者字段全空）。**先验在生产取材里目前命中 0**（标签覆盖率 0.2%），把标签做厚是 **T-16** 的活，不在本轮虚报。
- **测试**：全量 **774 passed / 0 failed（296.36s）**；写作相关 5 个文件 41 项。既有 8 处 `write_article` 调用与 `scripts/e2e.py` 显式放行密度（`material_min_full=0, material_min_sources=1`）—— 密度判定由 P39 独占，别在别处重复断言。
- **磁盘**：`du -sb .` = **11.341 GB / 预算 20 GB**（两次全量套件 + 新备份导致比阶段0 收口时多 ~1.6 GB），D: 空闲约 200 GB；`11_backups` = 1.245 GB（含 921 MB 的 `p38-stage1-pre-20261118-090040.zip` 全量包 + 本轮 2.16 MB 代码包 `p39-stage1-post-20261118.zip`），`disk.backup_keep` 已从 30 降到 **6**（全量包 880 MB，留 30 份必爆预算）。清理只走 `ttxt housekeep`。
- **运行态**：supervisor 已重新拉起（`ttxt supervisor --ensure`，pid 31012；scheduler 26884、worker 18484，`running: true`、无停止标志）。**`ops.scheduler_heartbeat` 仍 FAIL**：`metrics_fetch_6h` 12.4h、`crawl_enqueue_4h` 11.3h 未执行（原因是为跑全量套件把托管停过），接手后要确认它恢复，别当已知问题跳过。`strata.health`：`served_model=qwen3.8-flash-next-iq3_s`、`context=147456`、`loaded=True`。
- **可复用 API（已核对存在）**：`ttxt/analysis/rag.py search(..., db_path=)`、`ttxt/analysis/viral.py load_calibration(con)` / `CALIBRATION_META_KEY` / `MIN_CALIBRATION_SAMPLE` / `TIER_MEASURED`、`ttxt/analysis/keywords.py _measured_label_map` / `LABEL_VALUE` / `VIRAL_WEIGHT=0.30`、`ttxt/crawler/cleaner.py COMPLETENESS_FULL` / `completeness_of`、`ttxt/crawler/store.py read_only_connect`（**不设 row_factory**，要自己 `sqlite3.Row`；且 `mode=ro` 会在生产目录造出 `-wal/-shm` ⇒ 生产探针不要和 pytest 会话同时跑）、`ttxt/model_gate/client.py:chat(base_url=, model_id=)`。
- `writing_history` 真实列名只有：`draft_id, category, topic, version, content, model_id, created_at, status`（**没有 score/passed**，别按记忆写 SQL）。

## 红线（接手后继续有效）

不删 `01_categories/ 09_logs/ 资产库/ 11_backups/`（历史残留只能挪进 `07_tmp`）；清理临时文件必须走 `ttxt housekeep` 且逐条打日志+回执；`10_tests/conftest.py` 的 `production_state_untouched`、`session_db_opens`、`isolated_log_dir`、`session_categories_dir`、`no_stray_temp_at_repo_root` 守卫不许削弱；**事实与质量门禁只许收紧不许放宽**（规则 55/57）；未标定类目（health）不得当已标定用，缺数据不得当 0（规则 6）；未经批准不发布、不做破坏性操作；每阶段完成即提交并推送，推送失败必须如实报告并用 `git ls-remote` 核验；DeepSeek 评审密钥只允许 `.secrets/deepseek_api_key`；127.0.0.1:8080 写作模型配置/端口/量化不动。

## 还欠着的账（不变，别丢）

T-02 第二来源补到 A+B ≥50%、T-01/T-15 AI 重判分歧裁定、T-08/T-09 数据闭环、T-16 展现量与标签覆盖率（决定先验能否真生效）、T-17 管线增量化（social 全量 47m51s）、T-12 POSIX 锁、面板日志过滤与分页、health 标定（26 行 < 30）、social 20 篇 FTS 覆盖缺口。
