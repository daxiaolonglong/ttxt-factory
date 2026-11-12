# 运维手册

## 1. 常用命令(从 `D:\ttxt` 运行)

```powershell
# 环境体检(目录/配置/数据库/磁盘/Strata 只读健康)
.venv\Scripts\python.exe -X utf8 -m ttxt doctor

# 初始化(幂等)
.venv\Scripts\python.exe -X utf8 -m ttxt init

# 采集(示例)
.venv\Scripts\python.exe -X utf8 -m ttxt crawl --source quotes_toscrape --category demo --limit 20 --max-pages 2
.venv\Scripts\python.exe -X utf8 -m ttxt sources

# 清洗 + 时间归一化 + 相似聚类
.venv\Scripts\python.exe -X utf8 -m ttxt clean --category demo

# 关键词评分 / 爆款标签 / 知识资产 / FTS5 索引
.venv\Scripts\python.exe -X utf8 -m ttxt analyze --category demo

# 类目 RAG 检索
.venv\Scripts\python.exe -X utf8 -m ttxt search --category demo --query "智能音箱"

# 模型状态与串行任务
.venv\Scripts\python.exe -X utf8 -m ttxt model --ping
.venv\Scripts\python.exe -X utf8 -m ttxt model --status
.venv\Scripts\python.exe -X utf8 -m ttxt model --ask "只回复:OK" --max-tokens 16

# 写作闭环(仅导出草稿,不发布)
.venv\Scripts\python.exe -X utf8 -m ttxt write --category demo --topic "智能音箱与家庭自动化" --max-revisions 1

# 面板(仅 127.0.0.1;POST 受护栏,只可下发 crawl 入队 / analyze)
.venv\Scripts\python.exe -X utf8 -m ttxt serve --port 8765
# 或双击 scripts\start-dashboard.cmd(带窗口) / scripts\start-dashboard.vbs(无黑窗)
# 停止: scripts\stop-dashboard.cmd
# 护栏验收: powershell 执行 scripts\p11_http_check.ps1(11 项矩阵)

# ---- P11 闭环命令 ----
# 采集队列(默认只列队;--run 才真正执行,建议人工确认后再跑)
.venv\Scripts\python.exe -X utf8 -m ttxt crawl-queue --limit 20
.venv\Scripts\python.exe -X utf8 -m ttxt crawl-queue --run [--category <类目>] [--limit N]

# 真实指标回流(仅白名单来源;伪造/未授权会被硬拒)
.venv\Scripts\python.exe -X utf8 -m ttxt metrics --list-sources
.venv\Scripts\python.exe -X utf8 -m ttxt metrics --category technology --import <导出.json> --source toutiao_admin_export
.venv\Scripts\python.exe -X utf8 -m ttxt metrics --category technology --snapshot   # 冻结快照+重打标签

# 预测→回填→知识资产(无实测基线拒绝预测;样本不足不写资产)
.venv\Scripts\python.exe -X utf8 -m ttxt predict predict --category technology
.venv\Scripts\python.exe -X utf8 -m ttxt predict settle   --category technology
.venv\Scripts\python.exe -X utf8 -m ttxt predict report   --category technology

# 本地调度器(任务定义在 00_control/config.json → scheduler.jobs;状态存 scheduler_state.json)
.venv\Scripts\python.exe -X utf8 -m ttxt schedule            # 查看到期状态
.venv\Scripts\python.exe -X utf8 -m ttxt schedule --once     # 跑一遍到期任务(只入队/分析/恢复/清理)
.venv\Scripts\python.exe -X utf8 -m ttxt schedule --daemon   # 常驻循环(默认 300s 一轮)

# 崩溃恢复与任务重排
.venv\Scripts\python.exe -X utf8 -m ttxt recover
.venv\Scripts\python.exe -X utf8 -m ttxt recover --run

# 今日头条 · 科技频道(浏览器渲染,逐篇限速,robots 合规)
.venv\Scripts\python.exe -X utf8 -m ttxt crawl --source toutiao_tech --category technology --limit 10
.venv\Scripts\python.exe -X utf8 -m ttxt clean --category technology
.venv\Scripts\python.exe -X utf8 -m ttxt analyze --category technology

# 六角色
.venv\Scripts\python.exe -X utf8 -m ttxt agents
.venv\Scripts\python.exe -X utf8 -m ttxt agents --context orchestrator
.venv\Scripts\python.exe -X utf8 -m ttxt agents --export-skills

# 测试 / 备份 / 磁盘
.venv\Scripts\python.exe -X utf8 -m ttxt selftest
.venv\Scripts\python.exe -X utf8 scripts\backup.py <label>
.venv\Scripts\python.exe -X utf8 -m ttxt housekeep --clean
```

> 也可以直接双击/运行 `scripts\ttxt.cmd <命令>`(已固定 UTF-8 与 venv)。

## 2. 目录与边界

- 项目根:`D:\ttxt`;所有写入、缓存、日志、备份、venv 均在此。
- **禁止**:修改/重启 Strata;同时运行两个模型;自动发布文章;伪造指标;写入 D 盘外项目文件。
- 面板仅绑定回环地址;GET 只读,POST 仅 `crawl` 入队与 `analyze` 两类,带四重护栏(任务头/回环 Host/Origin 同源/64KB JSON);写作、发布、模型任务不可下发(404)。调度器同样禁止 write/publish/model 类任务。

## 3. 故障恢复

| 场景 | 处理 |
|---|---|
| 采集失败 | 查看 `crawl_jobs.last_error`;attempts 未达上限会自动重排 queued;`ttxt recover --run` 重跑(去重幂等,不重复入库) |
| robots 无法确认(5xx/网络异常) | 来源任务失败并暂停,不写任何数据;网络恢复后手动重跑 |
| 模型未就绪 | `model --status` 查看队列;`wait_ready` 会自动等待;任务保持 queued,不会并发 |
| 模型任务失败 | 自动重试 3 次后 failed;`last_error` 记录根因;`model --drain` 重跑队列 |
| 进程崩溃遗留 running 任务 | `ttxt recover`:模型侧超 900s 重置 queued,采集侧超 30 分钟标记 failed(根因:进程中断) |
| 锁文件残留 | 只有「mtime 超 30 分钟且持有进程已死」才会被接管;`ttxt recover` 与任务执行前自动清理 |
| 不合格草稿 | 写入 `04_writing/drafts/<类目>/rejected/`(含拒绝原因),成品目录不出现;通过门禁的草稿才在类目目录 |
| 数据库损坏 | `doctor` 报 integrity;从 `11_backups` 解压恢复对应 `db/<类目>/articles.db` |
| 磁盘不足 | `housekeep --clean` 清理 temp/日志/pip 缓存/浏览器缓存;检查 99_temp TTL |
| 需要回退代码 | 解压对应阶段备份覆盖;数据用其中 `db/` 恢复 |
| 面板 POST 返回 403 | 缺 `X-TTXT-Task: 1` 头 / Origin 非本站 / Host 非回环 —— 属护栏生效,不是故障;合法调用见 `reports/P11-report.md` §4.1 |
| 指标导入被拒(PermissionError/ValueError) | 来源不在 `config.json → metrics.authorized_sources` 白名单,或行内负数/跨类目/全缺指标;这是防伪造机制,按导出源真实数据重试 |
| 引用已删来源的排队任务反复失败 | 已知边界(P12 计划自动撤销);当前手工处理:`system.db` 中将该 `crawl_jobs` 置 cancelled,勿反复 `recover --run` |

## 4. 数据备份与恢复

- 备份:`scripts\backup.py <label>` → `11_backups/<label>-<时间>.zip`
- 内容:代码 + 测试 + Wiki + 面板 + 角色/技能 + 草稿 + system.db + 各类目 articles.db(SQLite `.backup` 快照)
- 恢复:解压后,`db/system.db` → `00_control/system.db`;`db/<类目>/articles.db` → `01_categories/<类目>/articles.db`
- 保留:7 份(配置 `disk.backup_keep`)

## 5. 限额

| 项 | 限额 |
|---|---|
| 项目总量 | 20 GB |
| 99_temp | 2 GB / TTL 24h |
| pip 缓存 | 1 GB |
| 日志 | 10 MB × 7 |
| 备份 | 7 份 |
| 浏览器目录 | 3 GB(当前未启用浏览器) |

## 6. 角色与技能

- 角色文件:`06_agents/*.md`(6 个);`ttxt agents --context <role>` 装载"角色 + 核心规则 + 类目隔离"。
- DSH 项目技能:`ttxt agents --export-skills` → `D:\ttxt\.dsh\skills\ttxt-*`,被 DSH 实时发现(无需重启,零全局配置)。
