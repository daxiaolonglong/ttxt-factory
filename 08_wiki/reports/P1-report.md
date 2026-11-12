# P1 验收报告 — 脚手架 / 规则 / Wiki / system.db

- 阶段:P1
- 日期:2026-11-12
- 结论:**通过**(doctor 10/10 PASS,pytest 10 passed,磁盘 0.026 GB / 20 GB)

## 一、事实(已完成内容)

### 1. 目录骨架(全部位于 `D:\ttxt`)
15 个顶层目录:`00_control`(含 `model_gate/`)、`01_categories`、`02_crawler`(spiders/extractors/cleaners/schedulers/checkpoints/.browser)、`03_intelligence`(4 子目录)、`04_writing`(3 子目录)、`05_dashboard`(web/api)、`06_agents`、`07_rules`、`08_wiki`、`09_logs`、`10_tests`、`11_backups`、`99_temp`。
5 个类目目录:`technology/finance/social/history/health`,各含 `rag/`、`keywords/`、`metrics/`。

### 2. 虚拟环境与缓存
- `D:\ttxt\.venv`(Python 3.12.10,venv 内 pip 25.0.1)
- pytest 9.2.0(仅开发依赖)
- pip 缓存固定为 `D:\ttxt\.pip-cache`(实测 2.0 MB)

### 3. 配置与数据库
- `00_control\config.json`:Strata 地址、面板端口 8765、目录映射、磁盘限额、采集参数、model_gate 参数。
- `00_control\category_registry.json`:类目注册与物理隔离规则。
- `00_control\system.db`:schema v1,7 张表(`schema_meta/categories/sources/crawl_jobs/model_jobs/system_log/app_state`),种子 5 类目,WAL 模式。

### 4. 代码与文档
- `ttxt/`:config、db、logging(JSONL 轮转)、housekeep(目录/磁盘/限额/清理)、doctor、CLI(init/doctor/housekeep/status/selftest/serve)。
- `07_rules\CORE_RULES.md`:22 条核心规则(边界/工程纪律/资源运维)。
- `README.md`、`08_wiki\architecture.md`、`08_wiki\db_schema.md`。
- `scripts\setup_env.ps1`、`scripts\ttxt.cmd`、`scripts\backup.py`(SQLite .backup + 源码 zip,保留 7 份)。
- `10_tests\`:conftest + 10 项 P1 冒烟测试。

## 二、发现的问题

1. **Strata 首次扫描时 8080 未就绪**:模型加载需 1–2 分钟。已在 doctor 中设计为 `WARN` 不阻塞;复查时 `/health` 200、模型 `qwen3.8-flash-next-iq3_s`、`loaded=True`。无遗留问题。
2. **测试断言写法冗余**:`test_project_root_and_required_dirs` 初版含无效条件判断。已修正为单条断言,行为等价。
3. **DSH 框架自身的临时溢出文件**:工具大输出会在 `%TEMP%\dsh-spill-*` 生成文件(框架行为,非项目写入)。项目自身所有写入实测均在 `D:\ttxt`(housekeep 各目录统计为证)。

## 三、根因与修改

- 问题 1:模型首次加载耗时属正常;doctor 已做容错,不修改任何 Strata 配置。
- 问题 2:笔误;已用 edit 修正,不影响测试结果。
- 问题 3:无项目侧修复项;已在报告中如实记录,后续阶段继续以 `paths.within_project` + 目录统计双重校验。

## 四、测试结果

| 检查 | 方式 | 结果 |
|---|---|---|
| 环境体检 | `ttxt doctor` | **10 PASS / 0 FAIL**(project.root、dirs、config、registry、paths、db.system、disk.free=110GB、disk.budget=0.026/20GB、venv、strata.health) |
| 冒烟测试 | `ttxt selftest` | **10 passed**(0.28 s) |
| 限额巡检 | `ttxt housekeep --clean` | temp 0 删除;日志 580 B;项目总计 0.026 GB |
| 备份 | `scripts\backup.py P1` | `11_backups\P1-20261112-122441.zip`(51.1 KB) |

## 五、磁盘变化

- 项目占用:0.026 GB(venv 25.4 MB + pip 缓存 2.0 MB + 其余源码/DB < 0.1 MB)
- D: 空闲 110.0 GB,远超 min_free 5 GB;预算 20 GB 内。

## 六、剩余阻塞

- **P2 采集来源清单待你提供/批准**(计划审查要点 ②)。在此之前可先用公开测试语料验证 Scrapling 采集链路,真实来源验收待清单确认。

## 七、回滚位置

- 快照:`D:\ttxt\11_backups\P1-20261112-122441.zip`(含 `ttxt/`、`scripts/`、`10_tests/`、`07_rules/`、`08_wiki/`、`README`、`requirements*`、`config.json`、`category_registry.json`、`system.db` 的 SQLite 备份)。
- 回滚方式:解压覆盖对应文件,并用其中的 `db/system.db` 覆盖 `00_control/system.db`。
