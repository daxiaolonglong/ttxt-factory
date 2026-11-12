# P7 验收报告 — 本地面板与六角色

- 阶段:P7
- 日期:2026-11-12
- 结论:**通过**(52 项测试全绿;面板实测真实数据;六角色技能被 DSH 实时发现;零全局配置改动)

## 一、事实(已完成内容)

### 1. 六角色(`06_agents/`)
| 角色 | 职责 |
|---|---|
| orchestrator | 总调度:任务路由、类目隔离、模型串行、验收汇总 |
| collector | 采集:来源管理、robots、限速重试、断点续采、去重入库 |
| data_engineer | 数据工程:清洗、时间归一化、simhash 聚类、schema 迁移、类目隔离 |
| viral_researcher | 爆款研究:指标、四级标签、关键词评分、知识资产、预测对照 |
| writer_reviewer | 写作评审:取材、写作、证据链、反向评审、修订、导出 |
| maintainer | 工程维护:测试、备份回滚、磁盘限额、故障恢复、Wiki 同步 |

### 2. 角色工具
- `ttxt agents`:列出角色
- `ttxt agents --show <role>`:打印角色文件
- `ttxt agents --context <role>`:装载"角色 + 核心规则 + 类目隔离"调度上下文
- `ttxt agents --export-skills`:导出为项目级 DSH 技能

### 3. DSH 项目级技能发现(实测)
- 从 DSH 安装包实测发现路径:`<projectRoot>/.dsh/skills`(rank 100),无 `.git` 时项目根 = 当前工作目录 → `D:\ttxt\.dsh\skills`。
- 导出后 **DSH 技能目录实时刷新**(无需重启),本会话技能目录已出现:
  `ttxt-orchestrator / ttxt-collector / ttxt-data-engineer / ttxt-viral-researcher / ttxt-writer-reviewer / ttxt-maintainer`。
- 未修改任何 DSH 全局配置,未写入 D 盘以外。

### 4. 本地面板(`ttxt serve`,127.0.0.1:8765)
- 后端:`ttxt/dashboard/api.py`(真实 SQLite/日志/模型状态)+ `server.py`(标准库 `ThreadingHTTPServer`,只读,仅绑定回环地址)。
- 前端:`05_dashboard/web/`(index.html + app.js + style.css,原生 JS,无外部 CDN)。
- 七个视图:系统总览 / 智能采集 / 数据分析 / 智能创作 / 知识进化 / 系统管理 / 动态日志。
- 类别切换清空并重载,仅显示当前类目数据(隔离)。
- 接口:`/api/overview`、`/api/categories`、`/api/jobs`、`/api/logs`、`/api/model`、`/api/category/<slug>/articles|keywords|analysis`。

### 5. 面板实测(真实进程)
- 监听确认:`127.0.0.1:8765`(仅回环);`GET /` 200。
- `/api/overview`:schema `p6-v1`;类目 `technology=0 / demo=33`;`crawl_jobs done=3 failed=1`;`model_jobs done=10`;Strata `qwen3.8-flash-next-iq3_s loaded=True`;磁盘 `0.359/20 GB`,`D: 空闲 109.6 GB`。
- `/api/category/demo/articles`:total=33。
- `/api/category/demo/analysis`:articles=33、平均 214.1 字、clusters=33。
- `/api/logs`:返回真实结构化日志(writing.done、gate.job_end 等)。

## 二、发现的问题与根因

1. **技能名含下划线不被收录**
   - 现象:首轮导出 6 个技能,DSH 只发现 3 个(带下划线的 `ttxt-data_engineer` 等缺失)。
   - 根因:DSH 技能名要求连字符格式。
   - 修改:角色名中的 `_` 统一转为 `-` 后重导出,6 个技能全部被发现(已由本会话技能目录实证)。
2. **测试 URL 未编码中文**
   - 现象:`test_category_articles` 因 `UnicodeEncodeError` 失败。
   - 修改:测试用 `urllib.parse.quote` 编码查询参数;前端使用 `encodeURIComponent`。
3. **面板只读设计**
   - 说明:P7 面板为只读浏览;任务下发继续走 CLI/角色流程,避免在未做权限与并发设计前开放写接口。已由测试覆盖 405 拒绝。

## 三、测试结果

| 检查 | 结果 |
|---|---|
| `ttxt selftest` | **52 passed**(P1 10 + P2 9 + P3 6 + P4 8 + P5 6 + P6 4 + P7 9) |
| 覆盖项 | 静态页、overview、类目隔离(technology=0 vs demo=33)、文章查询/搜索、关键词/分析、jobs/logs/model、404/400/405 与路径穿越拒绝、角色文件与项目技能导出 |
| 面板实测 | 真实进程 + 真实数据(见上) |
| DSH 技能发现 | 6/6 实时发现 |
| `ttxt doctor` | 10 PASS / 0 FAIL |

## 四、磁盘变化

- 项目总计 **0.359 GB / 20 GB**;dashboard 17.9 KB;agents 9.6 KB;`.dsh/skills` 约 10 KB
- D: 空闲 **109.6 GB**

## 五、剩余阻塞与下一阶段

- 真实来源清单与授权指标仍待提供。
- P8:全链路端到端实跑、故障注入、备份/恢复演练、14 条最终验收;周巡检任务需你明确确认后创建。

## 六、回滚位置

- `D:\ttxt\11_backups\P7-20261112-124617.zip`
- 历史回滚点:P6、P5、P4、P3、P2、P1 快照
