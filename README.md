# 完美头条智能工厂

本地头条内容生产系统:采集 → 清洗 → 去重 → 类目隔离入库 → 爆款分析 → 关键词反哺 → 选题 → 写作 → 反向评审 → 人工确认 → 真实表现回流 → 知识进化。

## 环境摘要(P0 实测,2026-11-12)

- Windows 10 22H2(build 19045),Xeon E5-2696 v3(18C/36T),63.9 GB 内存
- GPU:Tesla V100-SXM2-16GB(驱动 537.13,**严禁升级**;Strata 引擎已占 15.1 GB,禁止第二个模型)
- 磁盘:D: 234.7 GB,剩余 ~110 GB(Q4 模型包 113 GB 与其共盘);项目预算上限 20 GB
- Python 3.12.10(用户级);无 git/node;SQLite 使用 Python 内置模块
- Strata 模型服务:`http://127.0.0.1:8080`(OpenAI 兼容 `/v1`,`/health` 实测 200,当前 `qwen3.8-flash-next-iq3_s`,无需 API Key)
- 本系统所有模型任务只经 `model_gate` 串行调用 Strata;不启动/停止/切换 Strata

## 快速开始

```powershell
# 0) 打开面板(双击脚本,或命令行)
scripts\start-dashboard.cmd
#   等价: .venv\Scripts\python.exe -X utf8 -m ttxt serve --port 8765
#    浏览器访问 http://127.0.0.1:8765/

# 1) 一次性初始化虚拟环境(已创建则跳过)
powershell -ExecutionPolicy Bypass -File D:\ttxt\scripts\setup_env.ps1

# 2) 初始化项目目录与数据库
D:\ttxt\scripts\ttxt.cmd init

# 3) 环境体检(含 Strata 只读健康检查)
D:\ttxt\scripts\ttxt.cmd doctor

# 4) 运行测试
D:\ttxt\scripts\ttxt.cmd selftest

# 5) 磁盘/限额报告与临时清理
D:\ttxt\scripts\ttxt.cmd housekeep
D:\ttxt\scripts\ttxt.cmd housekeep --clean
```

## 目录

| 目录 | 职责 |
|---|---|
| `00_control/` | `config.json`、`category_registry.json`、`system.db`、`model_gate/` |
| `01_categories/<类目>/` | 每类目独立 `articles.db` + `rag/` + `keywords/` + `metrics/` |
| `02_crawler/` | 采集调度、蜘蛛、抽取、清洗、断点、来源配置 |
| `03_intelligence/` | 爆款评分、趋势、关键词反哺、知识资产 |
| `04_writing/` | 选题、写作、事实核查、评审、版本 |
| `05_dashboard/` | 本地 Web 面板(127.0.0.1:8765) |
| `06_agents/` | 六角色文件(总调度/采集/数据工程/爆款研究/写作评审/工程维护) |
| `07_rules/` | 核心规则 |
| `08_wiki/` | 架构、数据库字典、运维手册 |
| `09_logs/` | 结构化 JSONL 日志(轮转) |
| `10_tests/` | pytest 单测/集成/回归/失败场景 |
| `11_backups/` | 阶段备份与回滚点 |
| `99_temp/` | 临时文件(限额 + TTL 清理) |

## 阶段状态

| 阶段 | 状态 | 验收证据位置 |
|---|---|---|
| P0 环境扫描 + 架构审查 | ✅ 已批准 | 本文件、`08_wiki/architecture.md` |
| P1 脚手架 / 规则 / Wiki / system.db | ✅ 已完成 | `08_wiki/reports/P1-report.md`、`11_backups/P1-*.zip` |
| P2 Scrapling 采集 | ✅ 已完成 | `08_wiki/reports/P2-report.md`、`11_backups/P2-*.zip` |
| P3 数据库 / 清洗 / 去重 | ✅ 已完成 | `08_wiki/reports/P3-report.md`、`11_backups/P3-*.zip` |
| P4 关键词与爆款分析 | ✅ 已完成 | `08_wiki/reports/P4-report.md`、`11_backups/P4-*.zip` |
| P5 Strata 串行模型接入 | ✅ 已完成 | `08_wiki/reports/P5-report.md`、`11_backups/P5-*.zip` |
| P6 写作与反向评审 | ✅ 已完成 | `08_wiki/reports/P6-report.md`、`11_backups/P6-*.zip` |
| P7 面板与自动化 | ✅ 已完成 | `08_wiki/reports/P7-report.md`、`11_backups/P7-*.zip` |
| P8 全链路验收 | ✅ 已完成 | `08_wiki/reports/P8-report.md`、`11_backups/P8-*.zip` |

> 首轮部署完成。真实采集来源清单与头条授权指标数据是剩余两项外部输入,见 `08_wiki/reports/P8-report.md`。
> 日常命令见 [`08_wiki/operations.md`](08_wiki/operations.md);每日 18:00 自动质量巡检已建立(DSH 计划任务,新会话独立执行)。

## 关键约束

- 所有写入仅在 `D:\ttxt`;磁盘预算 20 GB,每阶段实测。
- Strata 与 V100 归用户管理,系统只读状态 + 串行调用。
- 面板仅绑定 `127.0.0.1`,无发布端点。
- 规则全文见 [`07_rules/CORE_RULES.md`](07_rules/CORE_RULES.md)。

## 版本控制与 GitHub

| 项 | 值 |
|---|---|
| 远端仓库 | <https://github.com/daxiaolonglong/ttxt-factory>（private） |
| 默认分支 | `main` |
| git 客户端 | 便携版 MinGit 2.56.1，位于 `tools/git/`，经 `scripts\git.cmd` 调用（不污染系统 PATH） |
| 入库范围 | 源码、配置、文档、测试、Wiki、技能文件；`.venv/`、`*.db`、`01_categories/*/rag`、`keywords`、`metrics`、`09_logs/`、`11_backups/`、`99_temp/`、`tools/` 由 `.gitignore` 排除 |

```powershell
# 日常提交并推送（令牌仅从环境变量读，不落盘、不进 .git/config）
set GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxx
scripts\push.cmd -m "feat: 采集去重阈值调整"

# 仅查看状态
scripts\push.cmd --dry-run

# 首次连接新仓库（建仓 + 首次提交 + 推送，可重复执行）
.venv\Scripts\python.exe -X utf8 scripts\github_connect.py --repo ttxt-factory
```

> 本机 `schannel` TLS 在受限环境下不可用，仓库已写死 `http.sslBackend=openssl`；换机器克隆后同样执行
> `git config http.sslBackend openssl`。令牌建议最小权限（仅 `repo`）并设有效期，用完可在 GitHub 侧随时撤销。
