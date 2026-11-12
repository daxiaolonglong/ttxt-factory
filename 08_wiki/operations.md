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

# 面板(只读,仅 127.0.0.1)
.venv\Scripts\python.exe -X utf8 -m ttxt serve --port 8765
# 或直接双击: scripts\start-dashboard.cmd(自动打开浏览器)

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
- 面板只读、只绑定回环地址。

## 3. 故障恢复

| 场景 | 处理 |
|---|---|
| 采集失败 | 查看 `crawl_jobs.last_error`;修复来源或网络后重跑(`crawl` 幂等,不重复入库) |
| 模型未就绪 | `model --status` 查看队列;`wait_ready` 会自动等待;任务保持 queued,不会并发 |
| 模型任务失败 | 自动重试 3 次后 failed;`last_error` 记录根因;`model --drain` 重跑队列 |
| 锁文件残留 | 超过 30 分钟自动清理;或确认无任务后手动删除 `00_control\model_gate\gate.lock` |
| 数据库损坏 | `doctor` 报 integrity;从 `11_backups` 解压恢复对应 `db/<类目>/articles.db` |
| 磁盘不足 | `housekeep --clean` 清理 temp/日志;检查 99_temp TTL;必要时清理 pip 缓存 |
| 需要回退代码 | 解压对应阶段备份覆盖;数据用其中 `db/` 恢复 |

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
