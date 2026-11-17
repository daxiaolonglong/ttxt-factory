# 会话检查点（压缩/新会话的唯一接手文件）

**这个文件的作用是"无损压缩"**：上下文被压缩、或换新窗口时，只相信这里写的事实，不靠对话记忆。
规则：每完成一个阶段、每次提交推送后、每次产生大输出（全量测试日志、全盘扫描、长报告）之后，必须重写本文件。
最后更新：2026-11-18 02:32（阶段0 已闭合；**推送被 GitHub 凭据卡住**，见"接手三行"第 3 条）

## 接手三行

1. 阶段0（磁盘止血，账本 T-19）**已完成并有回执**：scratch 56 GB → 1.544 GB、夹具改会话级裁剪副本、WAL 按字节封顶可回收、doctor 磁盘项改 FAIL、FTS 整库重建改 `DROP+CREATE`；测试 `10_tests/test_p38_disk_budget_fixtures.py` **19 项通过**（28.82s），全量 **761 passed / 0 failed（279.20s）**。下一个动作：**不要重做磁盘调查**，直接进阶段1（把爆款先验与证据密度前置门禁接进写作，断点在 `ttxt/writing/workflow.py:411 rag.search(..., limit=5)`）。
2. 本地已有两个提交未上远端：`2797133`(P37 欠账) + `c5fff11`(P38 磁盘止血)，再加本轮未提交的 wiki/守卫改动（`08_wiki/PROJECT_STATUS.md`、`08_wiki/session_checkpoint.md`、`10_tests/conftest.py`、`10_tests/test_p38_disk_budget_fixtures.py`、`07_rules/CORE_RULES.md`、`00_control/scheduler_state.json`）。
3. **唯一阻断 = GitHub 写权限**：`git push origin main` 在非交互环境里报 `fatal: could not read Username for 'https://github.com'`（GCM 2.6.1 无可复用凭据，`git credential fill` 也拿不到；`git ls-remote` 能读是因为仓库公开）。远端 `main` 仍是 `39e2bf2`。负责人需二选一：①在终端跑一次 `git push` 完成浏览器登录，或 ②给一个 fine-grained PAT 放到 `.secrets/github_token`（`.gitignore` 第 53 行已忽略 `.secrets/`），再由我配 credential helper。**不得**把 token 写进任何日志/回执/提交/对话回显。

## 已实测、不需要再确认的事实（2026-11-18 本机读数）

- 磁盘：`07_tmp` **1,701,387,400 B(1.586 GB / 限额 2 GB)**，其中 `pytest_basetemp` ~904 MB(只留最近 4 份)、`pytest_trim` **54,835,874 B**、`stray-20261118/` ~97 MB(从仓库根挪进来的历史残留)；项目总量 `du -sb .` = **10,446,716,575 B(9.73 GB / 预算 20 GB)**；D: 空闲 **214,712,131,584 B(200 GB)**。
- 根因已修：旧夹具对 `01_categories/*/articles.db` **逐测试整库 copyfile**(social 1.7 GB ⇒ 每轮套件 ≈28 GB)；现在是会话级裁剪缓存 `07_tmp/pytest_trim/<slug>-<size>-<mtime_ns>.db`，硬断言 单库 ≤40 MB / 单测合计 ≤200 MB。连 `mode=ro` 的 SQLite 连接都会在生产目录造出 `-shm/-wal`，所以生产库在测试里只许 `shutil.copyfile` 读字节。
- **规模判定（不做的决定也要留证）**：在 scratch 副本上按生产规模实测 social 40,526 篇，FTS 载荷 **395.0 MB → 382.2 MB**、`rebuild_index` **274.7 秒**、重建后 freelist 只 **12.8 MB**、`VACUUM` 按 page_count 只少 **36.1 MB** ⇒ **为省磁盘做生产整库重建不划算**，省磁盘靠 T-17 增量化。小库比例不许外推到生产（规则 61 已写死；此前"159.7 MB→14.6 MB"是夹具小语料的比例，已按实测更正）。
- 已回收：`housekeep.reclaim_scratch_runs --apply` = **55.129 GB / 671 目录**，回执 `07_tmp/reclaim-20261118T003610/ledger.json`；生产死边车 `01_categories/social/articles.db-wal` **826,224,832 B** 已 checkpoint 合并(`wal_frames_in_log=0`，库体积不变)。
- 本轮新增守卫：`conftest.no_stray_temp_at_repo_root`(会话级，`tmp<8位>`/`pytest-of-*` 在仓库根新增即红) + `test_repo_root_has_no_stray_temp_dirs`(现场断言为 0)。
- 写作链路仍未接爆款先验：`ttxt/writing/` 不 import `viral/keywords`；选料只有 `ttxt/writing/workflow.py:411 rag.search(..., limit=5)`(BM25)；`_model_meta()`(:357-375) 硬编码 `same_model_review: True`；`_save_evaluation`(:232-246) `evaluator` 固定 `"model_review"`；通过判定 `_passed`(:331-337)。social：40,506 语料、阈值已标定(0.0347/0.0083/0.0003，样本 80)、`writing_history` 0 行、草稿 0 篇。
- 可复用 API（已核对存在）：`ttxt/crawler/store.py:202 migrate_articles_db(category, db_path=)`、`:306 read_only_connect`(:323 `temp_store=MEMORY`)、`ttxt/analysis/rag.py:22 rebuild_index` / `:45 search(..., db_path=)`、`ttxt/analysis/viral.py:29 CALIBRATION_META_KEY` / `:43 load_calibration(con)`、`ttxt/analysis/keywords.py:87 _measured_label_map` / `:120 score_keywords`、`ttxt/model_gate/client.py:95 chat(base_url=, model_id=)`。
- 运行态：supervisor/scheduler/worker 与模型服务在跑(127.0.0.1:8080 → `qwen3.8-flash-next-iq3_s`，`/v1/models` 只有它)；`ttxt supervisor --stop / --clear-stop / --status` 可控；跑全量套件前先 `--stop`，跑完 `--clear-stop` 并用 `ttxt doctor` 确认恢复。本机 RAM 64 GB 但**可用仅 ~4 GB**(模型服务占 93%)⇒ 大库 `VACUUM` 必须走磁盘临时库，别用 `temp_store=MEMORY`。

## 红线（接手后继续有效）

不删 `01_categories/ 09_logs/ 资产库/ 11_backups/`；清理临时文件必须走 `ttxt housekeep` 且逐条打日志+回执（挪动历史残留可以，但要挪进 `07_tmp` 而不是删）；`10_tests/conftest.py` 的 `production_state_untouched`、`session_db_opens`、`no_stray_temp_at_repo_root` 三道守卫不许削弱；事实门禁只许收紧不许放宽（规则 55/57）；未标定类目（health）不得当已标定用；未经批准不发布、不做破坏性操作；每阶段完成即提交并推送，推送失败必须如实报告并用 `git ls-remote` 核验；DeepSeek 评审密钥只允许待在 `.secrets/deepseek_api_key`，不得进文件/日志/提交/回显；127.0.0.1:8080 写作模型的配置/端口/量化一律不动，只新增 `review` 段。
