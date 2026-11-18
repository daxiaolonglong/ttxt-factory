# ttxt Code Wiki — 首页

> 头条智能工厂（TouTiao Smart Factory）代码级 Wiki。基于 2026-11-18 代码库快照生成。
> 面向开发者：讲清楚每个模块"是什么、关键类与函数在哪、依赖谁、怎么跑"。
> 运维口径见 [../operations.md](../operations.md)，表结构见 [../db_schema.md](../db_schema.md)。

## 项目一句话

纯本地、单机、类目物理隔离的"采集 → 知识资产 → 写作 → 评审 → 发布"闭环工厂：Python 标准库为骨架，Scrapling/patchright 为抓取层，SQLite（每类目一库）为存储层，模型调用全部收口到 `model_gate` 串行闸门（本机 Strata OpenAI 兼容服务），Web 面板只读 + 受限任务下发。

## 阅读顺序建议

| 顺序 | 文档 | 内容 |
|---|---|---|
| 1 | [01_architecture.md](01_architecture.md) | 整体架构、主数据流、模块依赖图、关键设计决策 |
| 2 | [02_cli.md](02_cli.md) | `ttxt` CLI 全部 21 个子命令与治理口径 |
| 3 | [03_core.md](03_core.md) | config / db / logging / agents / doctor / housekeep |
| 4 | [04_scheduler_supervisor.md](04_scheduler_supervisor.md) | 本地调度器与进程托管（守护体系） |
| 5 | [05_crawler.md](05_crawler.md) | 采集子包（21 个文件：抓取、清洗、去重、队列、守卫） |
| 6 | [06_analysis.md](06_analysis.md) | 情报子包（关键词、爆款、指标导入、FTS/RAG、预测） |
| 7 | [07_writing.md](07_writing.md) | 写作闭环与质量门禁（L1-L4） |
| 8 | [08_model_gate.md](08_model_gate.md) | 模型调用唯一通道（串行锁 + 持久队列） |
| 9 | [09_dashboard.md](09_dashboard.md) | 本地 Web 面板与安全护栏 |
| 10 | [10_legacy.md](10_legacy.md) | 旧资产库迁移通道 |
| 11 | [11_data_layer.md](11_data_layer.md) | 数据层：system.db / 类目 articles.db / 文件目录约定 |
| 12 | [12_dependencies.md](12_dependencies.md) | 内外部依赖关系与配置体系 |
| 13 | [13_running_and_testing.md](13_running_and_testing.md) | 环境搭建、运行方式、测试体系 |

## 仓库目录结构（顶层）

```
D:\ttxt
├── 00_control/        # 控制平面：config.json、category_registry.json、system.db、
│                      #   scheduler/supervisor 状态与锁、model_gate/ 锁目录
├── 01_categories/     # 每个类目一个物理隔离命名空间：<slug>/{articles.db, rag/, keywords/, metrics/}
├── 02_crawler/        # 采集数据面：sources.json（来源注册表）、page_urls.json（页面URL台账）、
│                      #   checkpoints/、.browser/（共享浏览器 profile）、worker_stop 标志
├── 03_intelligence/   # 分析证据：metrics_public/、keyword_crawl/ 等取证落盘
├── 04_writing/        # 写作产物：drafts/<类目>/*.md 与 drafts/<类目>/rejected/*.md
├── 05_dashboard/      # Web 面板静态前端：web/index.html + web/app.js（原生 JS）
├── 06_agents/         # 六角色调度文件（*.md）
├── 07_rules/          # CORE_RULES.md 硬约束 + ledger/ 规则台账
├── 07_tmp/            # 测试 scratch（pytest_basetemp、pytest_trim、reclaim-*；只计量不轻删）
├── 08_wiki/           # 文档：architecture.md / db_schema.md / operations.md / code_wiki/（本目录）
├── 09_logs/           # 结构化 JSONL 日志（ttxt-YYYYMMDD.jsonl）+ supervisor 输出日志
├── 10_tests/          # pytest 测试套件（59 个 test_*.py + conftest.py 基座）
├── 11_backups/        # 类目库备份（legacy 迁移前在线备份等）
├── 99_temp/           # 普通临时区（housekeep 按 TTL/限额清理）
├── 资产库/            # 只读旧资产（01_语料库/factory.db 等，5.3 万篇历史语料）
├── scripts/           # 24 个 PowerShell 巡检/取证脚本（ops_*/check_*/sample_*/fetch_*/verify_*）
├── ttxt/              # ★ 唯一 Python 代码包（46 个 .py，六大子包）
├── pyproject.toml     # 项目元数据（name=ttxt, version=0.1.0）
├── requirements.txt   # 运行依赖：scrapling[fetchers]>=0.3
├── requirements-dev.txt # 开发依赖：pytest>=8.0
├── conftest.py        # 根级 pytest 配置转发（加载 10_tests/conftest.py 的 C-8 隔离基座）
├── setup-dashboard.ps1 / start-dashboard.cmd/.bs / tt.cmd 等 # 便捷启动入口
└── .venv/  .pip-cache/ .dsh/ .secrets/ ...
```

## 代码包结构（ttxt/）

```
ttxt/
├── __main__.py      # CLI 入口（python -m ttxt），21 个子命令
├── config.py        # 路径常量、config.json/注册表加载
├── db.py            # system.db 连接与 schema v1
├── logging.py       # JSONL 结构化日志
├── agents.py        # 六角色文件装载/导出
├── doctor.py        # 环境体检（约 30 项检查）
├── housekeep.py     # 磁盘预算、限额巡检、清理、WAL 检查点
├── scheduler.py     # B4 本地调度器（保守白名单任务）
├── supervisor.py    # P31 进程托管（schedule+worker 看护）
├── crawler/         # 采集子包（sources/fetcher/robots/url_safety/extract/toutiao/
│                    #   runner/worker/store/pipeline/clean/cleaner/timeparse/simhash/
│                    #   clusters/dedupe/keyword_crawl/metrics_pages/browser_lock/stackcheck）
├── analysis/        # 情报子包（analyze/keywords/viral/metrics_import/rag/
│                    #   knowledge/prediction/source_identity）
├── writing/         # 写作子包（workflow/verifier/review/export/prompts）
├── model_gate/      # 模型闸门（gate/queue/client）
├── dashboard/       # Web 面板（server/api）
└── legacy/          # 旧资产迁移（factory/migrate）
```

## 快速上手（最短路径）

```powershell
.\setup.ps1                      # 建 .venv、装依赖、ttxt init、跑冒烟测试
.venv\Scripts\Activate.ps1
ttxt doctor                      # 环境体检（任一 FAIL 返回码 1）
ttxt sources                     # 查看来源注册表
ttxt crawl --source sspai_rss    # 手工采集一个来源
ttxt analyze --category all      # 跑分析闭环
ttxt serve                       # 打开面板 http://127.0.0.1:8765
```

详见 [13_running_and_testing.md](13_running_and_testing.md)。
