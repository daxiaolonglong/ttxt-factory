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
#   或双击 scripts\start-dashboard.vbs(隐藏窗口,无黑窗)
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

# 6) 闭环操作(P11):查看排队/调度状态(均只入队/查看,不自行采集或发布)
D:\ttxt\scripts\ttxt.cmd crawl-queue --limit 20
D:\ttxt\scripts\ttxt.cmd schedule
D:\ttxt\scripts\ttxt.cmd metrics --list-sources
D:\ttxt\scripts\ttxt.cmd predict report --category technology
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
| P9 头条科技来源接入 | ✅ 已完成 | `08_wiki/reports/P9-toutiao-tech.md`、`11_backups/P9-final-*.zip` |
| P10 生产可靠性修复 | ✅ 第一批完成 | `08_wiki/reports/P10-report.md`、`11_backups/P10-final-*.zip` |
| P11 生产闭环第二批 | ✅ 已完成 | `08_wiki/reports/P11-report.md`、`08_wiki/reports/p11-evidence/`、`11_backups/P11-final-*.zip` |
| P12 可靠性与质量加固 | ⚠️ 基础设施批次已提交;L4 质量验收未闭合 | `08_wiki/reports/P12-*.md`(**外部执行凭据**,本机未复跑浸泡/盲测) |
| P13 质量闭环(评审返工) | 🟡 批次0 完成;批次1 进行中;`quality_claim = NOT_AVAILABLE` | `08_wiki/reports/P13-batch0-baseline-verification.md`、`08_wiki/reports/P13-batch1-golden-set-v2.md`、`10_tests/golden/golden_set_v2.schema.md`、`11_backups/P13-batch0-baseline-*.zip` |

> 生产闭环已贯通:指标回流(白名单)→ 快照 → 预测校准 → 知识资产;分析 → 关键词反哺 → 自动入队采集;本地调度器无人值守(仅入队/分析/恢复/清理)。等待的外部输入只剩头条后台真实指标导出。
> P12 各基础设施批次(A–I)已提交,但**质量结论仍未验收**:真实指标 0 行(样本 < `MIN_ROWS=30`,禁止校准)、黄金集人工裁定 0 条、72h/7 天浸泡仅有外部凭据。质量闭环现由 **P13** 接管,验收口径见 `08_wiki/reports/P13-batch1-golden-set-v2.md`;任何人不得引用旧的 57 条机器预裁定数据宣称 L4 达标。
> 日常命令见 [`08_wiki/operations.md`](08_wiki/operations.md)。
> 定时巡检:本 DSH 环境已建立「每日 18:00(Asia/Shanghai)数据质量巡检」与「每周一 09:30(Asia/Shanghai)P13 质量闭环巡检」两个计划任务(各自独立新会话,只读+建议,不自行执行需批准的动作);你此前在 ChatGPT 建立的每周一巡检属于另一工具,不属于本项目配置。

## 关键约束

- 所有写入仅在 `D:\ttxt`;磁盘预算 20 GB,每阶段实测。
- Strata 与 V100 归用户管理,系统只读状态 + 串行调用。
- 面板仅绑定 `127.0.0.1`;POST 带护栏且只可下发采集入队与分析,写作/发布/模型不可下发,无发布端点;调度器同样禁止这三类任务。
- 规则全文见 [`07_rules/CORE_RULES.md`](07_rules/CORE_RULES.md)。

## 版本控制与 GitHub

| 项 | 值 |
|---|---|
| 远端仓库 | <https://github.com/daxiaolonglong/ttxt-factory>（private） |
| 默认分支 | `main` |
| git 客户端 | 便携版 MinGit 2.56.1，位于 `tools/git/`，经 `scripts\git.cmd` 调用（不污染系统 PATH） |
| 入库范围 | 源码、配置、文档、测试、Wiki、技能文件；`.venv/`、`*.db`、`01_categories/*/rag`、`keywords`、`metrics`、`09_logs/`、`11_backups/`、`99_temp/`、`tools/` 由 `.gitignore` 排除 |
| 凭据 | `scripts\login.cmd` 保存一次令牌到 `.secrets/github_token`（已被 git 忽略，ACL 仅当前用户可读）；推送脚本自动读取，无需每次输入 |

```powershell
# 第一次：保存令牌（只需一次，输入不回显；也支持 --token ghp_xxx）
scripts\login.cmd

# 以后提交并推送，不用再贴令牌
scripts\push.cmd -m "feat: 采集去重阈值调整"

# 查看当前凭据来自哪里 / 删除本地凭据
scripts\login.cmd --check
scripts\login.cmd --forget

# 仅看状态不推送
scripts\push.cmd --dry-run

# 首次连接新仓库（建仓 + 首次提交 + 推送，可重复执行）
scripts\login.cmd
.venv\Scripts\python.exe -X utf8 scripts\github_connect.py --repo ttxt-factory
```

> 令牌读取顺序：环境变量 `GITHUB_TOKEN`/`GH_TOKEN` → `.secrets/github_token`；可用 `TTXT_GITHUB_TOKEN_FILE` 指定别处。
> 本机 `schannel` TLS 在受限环境下不可用，仓库已写死 `http.sslBackend=openssl`；换机器克隆后同样执行
> `git config http.sslBackend openssl`。令牌建议最小权限（仅 `repo`）并设有效期，用完可在 GitHub 侧随时撤销。

### DSH 插件：变更请求与工作树（GitHub PR 面板）

本机 DSH `desktop` profile 已安装并启用官方实验组合包 `@deepseek-ai/dsh-experimental-forge-bundle`：
Git 工作区里的会话在侧栏可查看关联 GitHub PR 的检查状态、评审意见与 diff，模型也多一个 `create_worktree` 工具。

| 项 | 值 |
|---|---|
| GitHub CLI | 便携版 gh 2.103.3 位于 `tools/gh/`，由 [`scripts/install_gh.py`](scripts/install_gh.py) 安装 |
| gh 凭据 | `%APPDATA%\GitHub CLI\hosts.yml`（明文令牌，与 `.secrets/github_token` 同一枚） |
| git 指向 | profile patch 里 `forge-github`、`worktree` 两行配置绝对路径，不写 PATH、不写注册表 |
| 生效条件 | 需重启 DSH（安装结果为 restart-required） |

已知限制：

- `gh auth status` 会提示缺少 `read:org` scope（现有令牌只有 `repo`）；读取 PR、检查、diff 均正常，
  仅在读组织级数据时需要 `gh auth refresh -h github.com -s read:org` 重新授权。
- 面板里“新建工作树”按钮仍调用 PATH 上的 `git`（本机未把 git 加入 PATH），会失败；
  模型的 `create_worktree` 工具已指向 `tools/git`，可正常使用。

