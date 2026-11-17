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

# ---- 每轮闭环标准动作(2026-11-16 P25 定版,顺序不能反) ----
# 0) 写库前备份:python scripts\backup.py <label>   → 11_backups(保留 30 份)
# 1) 排干队列:有界 Worker(跑完自己退,三选一即停;lease 默认 1800s)
.venv\Scripts\python.exe -X utf8 -m ttxt worker --owner <名字> --max-jobs 3 --idle-stop-s 45
.venv\Scripts\python.exe -X utf8 -m ttxt worker --stop          # 请求优雅停止
# 2) 公开页计数回流(默认干跑,--apply 才写库;robots 不放行就拒)
#    P35 起这一步由调度器 metrics_fetch 每 6 小时自动做,人工命令只用来核查/补量
.venv\Scripts\python.exe -X utf8 -m ttxt metrics --category <类目> --fetch-public --limit 20 --apply
# 3) 标定(实测样本 < 30 一律如实拒绝,不猜阈值)
.venv\Scripts\python.exe -X utf8 -m ttxt metrics --category <类目> --calibrate
# 4) 收账:analyze(索引与关键词反哺兜底)→ predict settle/report → ttxt doctor
# 归因:日志 crawl.enqueued 的 actor = dashboard | scheduler | keyword_plan | manual
# 判成败:只看回执 JSON、日志事件与库内读数。**后台 pwsh 作业只要 stderr 有输出就回 exit 1**,
#   与被调程序真实退出码无关(Playwright 常写 stderr);见规则 47 与 P25 报告第十四节。

# 本地调度器(任务定义在 00_control/config.json → scheduler.jobs;状态存 scheduler_state.json)
.venv\Scripts\python.exe -X utf8 -m ttxt schedule            # 查看到期状态
.venv\Scripts\python.exe -X utf8 -m ttxt schedule --once     # 跑一遍到期任务(入队/分析/恢复/清理/指标回流)
.venv\Scripts\python.exe -X utf8 -m ttxt schedule --daemon   # 常驻循环(默认 300s 一轮)
.venv\Scripts\python.exe -X utf8 -m ttxt schedule --run-job metrics_fetch_6h   # 手动跑指定任务并记账(不等到期)
# 互动数据自动回流(P35):调度器里的 metrics_fetch 任务每 6 小时一轮,把公开页计数自动接回闭环
#   (采集 → 重标定爆款标签 → 关键词评分 → 关键词反哺入队),不再需要人工跑第 2)步。
#   每类目每轮 limit 12 篇、每篇间隔 sleep_s 2.5s(不得低于 metrics.public_pages 的下限);apply=false 是干跑。
#   **它和 Worker 是两个进程,共用同一份 02_crawler/.browser/profile,不能同时开浏览器**:
#   开跑前先看 crawl_jobs 里有没有 queued/running,有就等,等满 wait_for_queue_s(300s)还忙 ⇒ 本轮
#   记 skipped_queue_busy 直接让路,不硬抢。它排在 crawl_enqueue_4h **之前**是有意安排的:
#   同一个 tick 两个任务都到期时,先采指标(此时队列必然空闲),再入队采集。
#   每轮结果进 scheduler_state.json(last_status:ok / partial / error / skipped_queue_busy / skipped_browser_busy / dry_run)
#   + 证据文件 03_intelligence/metrics_public/<类目>-<轮次时间戳>.json;某类目 0 候选 ⇒ 不开浏览器,只记 no_candidates。
#   类目明细里的键:fetched / written / updated / snapshots / known_gone_skipped / link_status_recorded|cleared /
#   row_errors / outcome_counts / labels / analysis / measured / calibration_hint。
#   **重标定用的是现有阈值,不自动重算阈值**(爆款的定义不能悄悄漂移):实测样本涨了而阈值快照还是旧的,
#   回执里会给 calibration_hint,由人决定要不要跑第 3)步 ttxt metrics --category <类目> --calibrate。
# 旧资产库一次性迁入类目库(T-14 / P37,2026-11-17):
#   .venv\Scripts\python.exe -X utf8 -m ttxt legacy-migrate                      # 干跑:零字节,回执只报"会写什么"
#   .venv\Scripts\python.exe -X utf8 -m ttxt legacy-migrate --apply              # 真写:每类目写前先备份到 11_backups
#   策略在 `00_control/legacy_migration.json`(旧 primary_type → 类目、最小正文长度、0 当缺失的字段),**不在代码里**;
#   旧库 `资产库/01_语料库/factory.db` 永远只读(回执里记 sha256 前后一致,实测 `a8a1f4c3…`)。
#   回执 `09_logs/legacy_migrate/<时间戳>-{dry_run,apply}.json`:逐批对账 inserted/duplicates/metrics_written/
#   metrics_orphaned(挂不上文章就不挂,不重新归因)/indexed/index_errors;幂等:重跑 inserted 0、duplicates 全量。
#   迁移当场只重建 FTS,**不跑聚类**(O(n²),见 T-17),所以迁完要单独 `ttxt clean --category <类目>`
#   (实测 social 40,506 行 47m51.9s)。回滚 = 用 `11_backups/<类目>-before-<时间戳>.db` 覆盖回去。
# 完整性标签整改(T-13,只改标签不删语料,可精确回滚):
#   .venv\Scripts\python.exe -X utf8 -m ttxt relabel-completeness --category <类目> --out 07_tmp/relabel-<类目>.json      # 出计划
#   .venv\Scripts\python.exe -X utf8 -m ttxt relabel-completeness --category <类目> --apply --out 07_tmp/relabel-<类目>.json
#   .venv\Scripts\python.exe -X utf8 -m ttxt relabel-completeness --category <类目> --rollback 07_tmp/relabel-<类目>.json # 按报告逐行改回
# 浏览器 profile 跨进程互斥(P36,2026-11-17):上面"不能同时开浏览器"从 P36 起是**操作系统级真锁**,不再靠排队自觉。
#   锁文件 `02_crawler/.browser/profile.lock` 在 profile 目录**之外**(Chromium 清 profile 清不掉它),
#   Windows 用 `msvcrt.locking` 锁 1MB 偏移处的一个字节(POSIX 走 `fcntl.flock`,本机未实测);
#   进程正常退出或被 taskkill 强杀,内核都自动释放 ⇒ 不看 PID、不看时间戳、不做"陈旧锁清账"。
#   实现 `ttxt/crawler/browser_lock.py`;起浏览器只有 `fetcher.browser_session()` 一个入口,
#   Worker(runner)与指标采集(scheduler.metrics_fetch)都走它 ⇒ 同一把锁,嵌套只起一个浏览器。
#   等锁预算:`browser.lock_wait_s`(默认 600s)/ `browser.lock_poll_s`(2s),单个任务可给 `lock_wait_s`
#   (metrics_fetch_6h 给 300s)。等不到锁:指标采集该类目不启动浏览器、不发请求,记 `skipped_browser_busy`
#   (带持锁者 pid/tag),本轮后续类目直接让路;Worker 侧任务保持 running 干等,锁放掉就继续,不用人工干预。
#   旧的"启动失败就无条件删 lockfile/Singleton*"已删除:现在先用内核的共享冲突证明无人持有
#   (`browser_lock.held_by_live_process`),证明不了就保留并记 error,绝不删还在用的锁文件。
#   静态 HTTP 抓取(http/rss 来源)完全不碰这把锁:锁被别人占着它照跑。
#   生产实测(2026-11-17 12:19–12:34,队列空闲、真进程持锁):metrics_fetch 等满 300.0s ⇒ `skipped_browser_busy`,
#   30 次采样 chrome 进程数恒为 0、profile 目录 2221 个文件 / 662,727,551 字节与 mtime 前后一模一样;
#   同一时段静态任务 8.03s 完成;放锁后 Worker 那条已等了 152s 的浏览器任务立刻起浏览器并 done(10 条入库)。
#   **改锁相关代码必须重启被托管的进程才生效**:`taskkill /T /F /PID <worker_pid>` 和 `/PID <scheduler_pid>`
#   (整棵进程树,venv 的 python.exe 是外壳),supervisor 约 30 秒内用新代码重新拉起,不必动 supervisor。
#   回滚:`git revert` 这三处(fetcher/scheduler/metrics_pages)+ 删 `browser_lock.py`,或直接 `11_backups\p36-before-profile-lock-*.zip`。

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
| 进程单实例锁残留(`00_control/*.lock`) | `doctor` 会报「PID 是残留(进程已死)」;这类锁由内核句柄持有,进程一死就释放,直接 `ttxt supervisor --ensure` 重新拉起即可 |
| 浏览器 profile 互斥(P36) | 没有"陈旧锁接管"这回事:持锁进程一死(包括被强杀)内核自动释放锁。`02_crawler/.browser/profile.lock` **不是垃圾文件**,还在被占用时删它等于放行两个浏览器抢同一个 profile;要立刻让路就停掉持锁进程(`taskkill /T /F /PID`),日志里 `crawl.browser_lock.timeout` 会给出持锁者 pid/tag |
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
| 浏览器目录 | 3 GB(2026-11-17 实测 `02_crawler/.browser/profile` = 2221 个文件 / 662.7 MB;渲染来源与指标回流都在用,不是"未启用") |

## 6. 角色与技能

- 角色文件:`06_agents/*.md`(6 个);`ttxt agents --context <role>` 装载"角色 + 核心规则 + 类目隔离"。
- DSH 项目技能:`ttxt agents --export-skills` → `D:\ttxt\.dsh\skills\ttxt-*`,被 DSH 实时发现(无需重启,零全局配置)。
