# 13 · 运行方式与测试体系

## 13.1 环境搭建

```powershell
# Windows + PowerShell。项目根硬编码为 D:\ttxt（config.PROJECT_ROOT）
.\setup.ps1        # 创建 .venv、pip 安装 requirements（.pip-cache 作缓存）、ttxt init、冒烟测试
.venv\Scripts\Activate.ps1
ttxt doctor        # 30 项体检；任一 FAIL 退出码 1
ttxt selftest      # 跑 10_tests（优先 .venv\Scripts\python.exe）
```

前置条件：Python 3.10+（`from __future__ import annotations` 与 pathlib 用法为准的最低线）；Edge 浏览器（browser 渲染走 `C:\Program Files (x86)\Microsoft\Edge\...`，config.browser.executable_path）；模型闭环需本机 Strata 服务在 127.0.0.1:8080（不要求常在线，doctor 只 WARN）。

## 13.2 日常运行

### 手工链路

```powershell
ttxt sources                        # 看来源
ttxt crawl --source eastmoney_rss   # 单来源采集（建 job 同步执行）
ttxt crawl-queue --run --limit 5    # 消费队列
ttxt clean --category all           # 清洗+聚类（--full 全量回退）
ttxt analyze --category all         # 分析闭环（关键词/爆款/知识资产/FTS）
ttxt search --category technology --query 人工智能
ttxt write --category technology --topic "..."   # 写作（仅 active 类目；insufficient→3）
ttxt model --ping / --status / --drain / --ask "..."
```

### 常驻自动化（三层）

```powershell
ttxt worker --owner cli-worker --max-jobs 0 --idle-stop-s 0   # 采集 Worker（单实例锁）
ttxt schedule --daemon --interval-sec 300                     # 调度器（5 个白名单任务）
ttxt supervisor --ensure                                      # 看护上面两个（幂等）
schtasks ... scripts/setup_scheduler_task.ps1                 # 计划任务每 5 分钟 --ensure 兜底
ttxt serve                                                    # Web 面板 127.0.0.1:8765（start-dashboard.cmd 包装）
```

停止：`ttxt worker --stop`（写 worker_stop，supervisor 见标志不再重启该子进程）；`ttxt supervisor --stop`（软停全部托管子进程）；恢复自动拉起前需 `--clear-stop`。

### 运维巡检（scripts/，24 个 PowerShell 脚本）

| 组 | 脚本 |
|---|---|
| 磁盘/环境 | `ops_disk_audit.ps1`、`ops_pip_cache_baseline.ps1`、`verify_pip_cache_sandbox.ps1`、`verify_p38_disk_budget.ps1` |
| 采集体检 | `ops_crawler_audit.ps1`、`ops_toutiao_stack_test.ps1`、`ops_toutiao_browser_sample.ps1`、`ops_search_engines.ps1`、`check_toutiao_selectors.ps1`、`fetch_toutiao_sample.ps1`、`sample_toutiao_listing_browser.ps1`、`extract_toutiao_render.ps1`、`verify_toutiao_feed_api.ps1`、`verify_toutiao_url_normalize.ps1` |
| 数据对账 | `ops_d1_ledger.ps1`、`ops_d2_dedupe.ps1`、`ops_dedupe_audit.ps1`、`ops_legacy_integrity.ps1`、`verify_c7_history.ps1`、`audit_163.ps1` |
| 运行守护 | `ops_p31_supervisor_probe.ps1`、`ops_p31_kill_test.ps1`、`ops_p31_verify.ps1`、`verify_p32_browser_ssrf.ps1`、`setup_scheduler_task.ps1` |

原则：脚本只做**只读取证与巡检**；改数据的操作一律走 CLI 的 dry-run/apply 通道。

## 13.3 测试体系（10_tests/，59 个 test_*.py）

### 基座（C-8 隔离）

- `10_tests/conftest.py`：注册夹具——临时类目库/临时 system.db、日志重定向、**生产库前后指纹比对**（测试不得碰生产库）、basetemp 限定 `07_tmp/pytest_basetemp`。
- 根级 `conftest.py`：用 importlib 动态加载上述 conftest，仅前转 `pytest_configure`——修复"无参数跑 pytest 时配置期钩子不触发、basetemp 回落仓库根"的问题；自带 `--basetemp` 早退保护保证幂等。
- 长跑清理：`housekeep.reclaim_scratch_runs`（按字节计量，单轮 basetemp 实测 28GB）；`ops_test_scratch.ps1`（ledger 模式）、`07_tmp/test_scratch_ledger.jsonl`、`verify_test_scratch_ledger.ps1`。

### 测试命名映射（test_pX_*.py ↔ 阶段/修复编号）

- `test_p1..p12`：各阶段功能（冒烟/采集/清洗/分析/闸门/写作/面板/头条/可靠性/闭环/worker lease/FTS temp_store）。
- `test_c1..c7b`：正确性修复专项（completeness 标签/URL 去重/跨类目去重/清洗覆盖/URL 身份/历史去重）。
- `test_p13_*`：门禁绕过、历史账本、golden 判例、归属路由；`test_p14_gate_lock`（闸门锁竞态）。
- `test_p15_test_isolation`：测试隔离断言（生产库指纹不变）。
- `test_p19/p26/p28/p31/p34`：关键词链路（反哺/冷却/爆款信号/匹配口径）。
- `test_p21/p22/p33/p34_metrics_*`：公开指标回收与台账。
- `test_p24_prediction_candidates`：预测候选不重挑。
- `test_p25_*`：头条频道采集、FTS-after-crawl、可观测性。
- `test_p30_source_identity`、`test_p23_identity_and_visibility`：来源身份。
- `test_p31_supervisor`、`test_p36_browser_lock`、`test_p32_ssrf`：守护与守卫。
- `test_p37_legacy_migration`、`test_p37_read_only_temp_store`：旧库迁移。
- `test_p38_disk_budget_fixtures`、`test_backup_rotation`：磁盘预算与备份轮转。
- `test_p39_material_prior_gate`、`test_p40_independent_review`、`test_p27_assist_draft`：写作门禁与评审通道。
- `test_p42_incremental_pipeline`：T-17 增量化。

### 运行

```powershell
ttxt selftest                                   # 推荐入口
.venv\Scripts\python.exe -m pytest 10_tests -q  # 直接跑
```

新增测试必须复用根级基座夹具（临时库+日志重定向+指纹比对），不得直连 `01_categories/` 生产库。

## 13.4 排障入口速查

| 症状 | 先看 |
|---|---|
| 环境/配置/库异常 | `ttxt doctor`（FAIL 行给出修复命令） |
| 任务没跑 | `ttxt schedule --status`、`00_control/scheduler_state.json` |
| worker/调度疑似停 | `ttxt supervisor --status`、`00_control/supervisor_state.json`、`09_logs/supervisor.*.log` |
| 模型不可用 | `ttxt model --ping`（strata.health）、gate.lock 持有人（doctor 无此项时读 `00_control/model_gate/gate.lock`） |
| 磁盘告警 | `ttxt housekeep`（report 双口径）、`housekeep --checkpoint-wals` |
| 跨类目污染报警 | doctor `db.cross_overlap` → `ttxt dedupe-history`（dry-run 出账本） |
| 数据改了想核对 | `09_logs/*.jsonl`（module/event）、`03_intelligence/` 证据、reclaim/legacy 回执 ledger |
