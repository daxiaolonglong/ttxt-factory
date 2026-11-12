# 架构与环境(P0 基线)

> 本文件随阶段推进持续更新。P0 实测基线:2026-11-12。

## 1. 实测环境(P0)

| 项 | 值 |
|---|---|
| OS | Windows 10 专业版 22H2 (19045) |
| CPU | Xeon E5-2696 v3,18C/36T @2.30GHz |
| RAM | 63.9 GB |
| GPU | Tesla V100-SXM2-16GB(驱动 537.13,严禁升级)+ AMD HD 6570(显示) |
| 显存现状 | Strata 引擎占用 15.1 GB;禁止第二个模型 |
| 磁盘 | C: 240GB(59.4 空闲)/ D: 234.7GB(**110 空闲**,含 111GB Q4 模型包)/ E: 450GB / F: 504GB |
| Python | 3.12.10 + pip 25.0.1(用户级);venv 在 `D:\ttxt\.venv` |
| 其他工具 | 无 git / node / npm / pnpm / uv / conda;SQLite 用 Python 内置 |
| 浏览器 | 仅 Microsoft Edge(x86);HTTP 采集优先 |
| Strata | `http://127.0.0.1:8080`,OpenAI 兼容 `/v1`;`/health` 实测 200:`qwen3.8-flash-next-iq3_s`,max_context 147456,无需 API Key |
| DSH | home `C:\Users\yangl\.dsh`,活动 preset `cordis`;项目级 skill 发现待 P7 实测 |

## 2. 组件与数据流

```
面板(05_dashboard,127.0.0.1:8765)  ← 只读 DB/日志 + 任务下发
       │
控制中心(00_control:system.db/config/registry)
       │
采集(02_crawler:Scrapling HTTP 优先 → 浏览器按需)→ 清洗/去重/聚类
       │
类目隔离库(01_categories/<cat>/articles.db + rag/keywords/metrics)
       │
分析(03_intelligence:指标快照→爆款四级标签+置信度→趋势/生命周期→关键词评分→知识资产/FTS5 RAG)
       │
唯一模型入口(00_control/model_gate:SQLite 队列 + 串行锁 + /health 等待)
       │
写作(04_writing:选题→大纲→正文→事实核查(证据链)→反向评审→修改≤2 轮→版本)
       │
六角色(06_agents)由总调度装载,全部经 model_gate 串行
       │
真实表现回流(仅授权导出/接口)→ 预测对照校准
```

## 3. 关键技术决策

| 编号 | 决策 | 理由 |
|---|---|---|
| D1 | 全 Python 标准库优先;面板用 `http.server` + 原生 JS 轮询 | 无 Node,减少组件与依赖 |
| D2 | 数据库 SQLite(WAL,每类目独立 articles.db) | 无额外常驻服务,类目物理隔离 |
| D3 | 唯一模型闸门 model_gate,SQLite 持久队列 + 串行锁 | 杜绝模型并发;崩溃可恢复 |
| D4 | 采集 HTTP 优先;CloakBrowser 暂缓 | 成本/许可/缓存管理;Scrapling 已够用 |
| D5 | 来源清单由用户批准写入 sources.yaml;robots 遵守 | 合法合规,不绕过访问控制 |
| D6 | 项目磁盘预算 20GB,每阶段实测 | D: 空闲 110GB 与 111GB 模型包共享 |
| D7 | 指标仅授权来源,带 completeness/credibility | 杜绝伪造指标 |
| D8 | Agency Agents 角色本地化进 `06_agents`,不装全局 skill | 零 DSH 全局配置改动 |

## 4. 阶段状态

| 阶段 | 状态 |
|---|---|
| P0 环境扫描 + 架构审查 | ✅ 已批准 |
| P1 脚手架 | ✅ 已完成(报告:`reports/P1-report.md`) |
| P2 Scrapling 采集 | ✅ 已完成(报告:`reports/P2-report.md`) |
| P3 数据库 / 清洗 / 去重 | ✅ 已完成(报告:`reports/P3-report.md`) |
| P4 关键词与爆款分析 | ✅ 已完成(报告:`reports/P4-report.md`) |
| P5 Strata 串行模型接入 | ✅ 已完成(报告:`reports/P5-report.md`) |
| P6 写作与反向评审 | ✅ 已完成(报告:`reports/P6-report.md`) |
| P7 面板与自动化 | ✅ 已完成(报告:`reports/P7-report.md`) |
| P8 全链路验收 | ✅ 已完成(报告:`reports/P8-report.md`) |

## 5. 当前状态(2026-11-12)

- 首轮 P0–P8 全部完成;端到端 10 步验收通过(见 `reports/P8-report.md`)。
- 14 条验收:11 条完全通过;4/5/9 依赖用户提供授权指标数据,系统保持 `missing_data` 诚实状态。
- 待外部输入:真实采集来源清单;头条账号授权指标导出/接口。
- 运维命令见 `operations.md`;回滚快照见 `11_backups/P8-*.zip`。
