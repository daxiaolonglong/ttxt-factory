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
面板(05_dashboard,127.0.0.1:8765)  ← 读 DB/日志 + 受护栏的任务下发(仅 crawl 入队/analyze;写作/发布/模型 404)
       │
本地调度器(ttxt scheduler:仅 crawl_enqueue/analyze/recover/housekeep;配置禁止 write/publish/model)
        │
控制中心(00_control:system.db/config/registry/scheduler_state.json)
       │
采集(02_crawler:Scrapling HTTP 优先 → 浏览器按需)→ 清洗/去重/聚类
       │
类目隔离库(01_categories/<cat>/articles.db + rag/keywords/metrics)
       │
分析(03_intelligence:指标快照→爆款四级标签+置信度→趋势/生命周期→关键词评分→知识资产/FTS5 RAG
      →关键词反哺:零覆盖词标 gap 并自动入队采集(去重),gap↔active 状态机,每类目 JSON 证据)
       │
唯一模型入口(00_control/model_gate:SQLite 队列 + 串行锁 + /health 等待)
       │
写作(04_writing:选题→大纲→正文→事实核查(证据链)→反向评审→修改≤2 轮→版本)
       │
六角色(06_agents)由总调度装载,全部经 model_gate 串行
       │
真实表现回流(仅 config 白名单来源导入,未授权拒绝)→ 预测对照校准
(无实测基线拒绝预测;回填样本 < min_samples 不写知识资产)
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
| P9 头条科技来源接入 | ✅ 已完成(报告:`reports/P9-toutiao-tech.md`) |
| P10 生产可靠性修复 | ✅ 第一批完成(报告:`reports/P10-report.md`) |
| P11 生产闭环第二批 | ✅ 已完成(报告:`reports/P11-report.md`) |
| P12 可靠性与硬化(第二批) | ✅ 已完成(报告:`reports/P12-batch2..5*.md`) |
| P13 资产审计 / 质量闭环审计 | ✅ 已出报告(`reports/P13-asset-audit.md`、`reports/P13-quality-audit-20261113.md`) |
| P13 最小修复 C-7 + C-8(模型闸门锁、测试隔离) | ✅ 已完成(报告:`reports/P13-fix-C7C8-20261114.md`,198 passed) |
| P13 最小修复 C-2(同类目 URL 重复插入) | ✅ 已完成(报告:`reports/P13-fix-C2-url-dedupe-20261115.md`,214 passed / exit 0) |
| P13 最小修复 C-3(跨类目归属)、C-1(正文完整性判定) | ⏳ 待做;三者全部通过前不恢复采集 |

## 5. 当前状态(2026-11-12)

- P0–P11:框架 + 采集 + 数据库 + 分析 + 串行模型 + 写作门禁 + 面板 + 头条科技真实来源 + 可靠性修复 + **生产闭环(指标回流/预测校准/关键词反哺/本地调度/面板下发)**。
- 诚实状态:预测与校准环等待头条后台真实指标导出(机制就绪,无数据不伪造);第三批(多类目长跑、故障全矩阵、写作质量回归、磁盘压测)未做。P11 发现并修复三个闭环级缺陷(调度器时区崩溃、IPv6 回环护栏不自洽、gap 关键词评分死锁),见 `reports/P11-report.md` §2。
- 运维命令见 `operations.md`;回滚快照见 `11_backups/`。

## 6. 当前状态(2026-11-15 实测)

- 测试基线:`python -m pytest 10_tests -q` → **214 passed,44.26s,exit 0**(198 旧 + 16 C-2 新增,0 skip / 0 fail)。
- 数据现状(只读核验):`01_categories/` 6 个类目库 + `00_control/system.db` 字节与 sha256 未变;technology 137 行、demo 45 行(其中 30 行为列表页形态,合法保留)。
- C-2 已修:类目库内同 URL 不再插第二次,重复如实计数,并发实测 4 线程只 1 行(证据 `reports/P13-fix-C2-url-dedupe-20261115.md` §5–§6)。
- 未做/待办:**C-3 跨类目归属**、**C-1 正文完整性判定**;历史 URL 重复行(technology ×3、finance ×2)未清理,需单独批准的修复任务;html 列表页抽取器仍不逐条取详情链接(`extract.py:55`),是列表页数据只按 `content_hash` 去重的根因。
- 采集侧现状:Scrapling 0.5.1 + curl_cffi 0.17.0 + playwright 1.65.1 已装,HTTP 优先(D4);CloakBrowser 未安装(暂缓)。C-3/C-1 通过前不恢复真实采集与 Worker/Scheduler。
- 磁盘与临时文件:项目内临时文件统一到 `07_tmp`(C-8 保留 10 份 basetemp 并自动回收);本轮实测项目占用下降约 79 MB,无暴涨。
