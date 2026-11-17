# P36 报告:浏览器 Profile 的跨进程互斥(2026-11-17)

本轮只做一件事:让 **Worker(采集)** 与 **scheduler 的 `metrics_fetch`(互动指标回流)** 这两个进程
**不可能同时启动浏览器**,同时删掉过去那个危险动作 —— 「浏览器启动失败就无条件删除 profile 里的锁文件」。
采集口径、限速、指标公式、爆款阈值、关键词评分、写作与发布、表结构、来源与 allowlist **一律未改**。

一句话结论:**互斥是内核级的事实,不是我们自制的账本;等不到锁时系统给的是明确状态
(`skipped_browser_busy`),不是假成功,也不是硬抢。**

---

## 1. 为什么要改(实测证据,不是推测)

| 事实 | 实测结果 |
|---|---|
| 两个进程共用同一份 profile | Worker 与 `metrics_fetch` 是不同进程,`fetcher.browser_profile_dir()` 都指向 `02_crawler/.browser/profile` |
| 同时开浏览器会怎样 | 用独立临时 profile 实测:第二个进程 **1.9 秒**失败,`TargetClosedError: Target page, context or browser has been closed` |
| 运行中的 Chromium 到底留了哪些锁文件 | profile 目录里只有 **`lockfile`** 一个;`SingletonLock / SingletonCookie / SingletonSocket` **根本不存在** |
| `lockfile` 被活进程持有时 | 连「只读打开」都 `PermissionError`(内核共享冲突) |
| 强杀 Chromium 之后 | 没有残留锁文件需要清;不删任何文件,1.8 秒后重新打开 profile 成功 |

⇒ 旧代码 `_clear_profile_locks()` 每次启动失败都去删四个文件,其中三个**在这台机器上从来不存在**;
而真正会被删的 `lockfile`,恰恰可能是**另一个活进程正在用的**。这等于把「两个进程抢同一个 profile」的
最后一道防线拆掉。P36 把它删掉,换成内核锁。

## 2. 改了什么

| 文件 | 改动 |
|---|---|
| `ttxt/crawler/browser_lock.py` | **新增**。跨进程 profile 锁:锁文件 `02_crawler/.browser/profile.lock` 在 profile 目录**之外**;Windows 用 `msvcrt.locking` 锁文件偏移 1 MB 处的 1 个字节,POSIX 分支 `fcntl.flock`(本机未实测)。只用标准库,零新依赖 |
| `ttxt/crawler/fetcher.py` | 起浏览器只有 `browser_session()` 一个入口:先取锁再启动,嵌套只起一个浏览器;启动异常/关闭异常都释放锁;`_clear_profile_locks()` 删除,换成 `_clear_stale_locks()` —— 只有内核共享冲突**证明无人持有**才清残留,证明不了就保留并记 error;另有 `_foreign_browser_using()` 明确报错;静态 `_fetch_static()` 不碰锁 |
| `ttxt/crawler/metrics_pages.py` | `collect_with_browser(...)` 通过 `fetcher.browser_session(wait_s=lock_wait_s)` 取会话 |
| `ttxt/scheduler.py` | `_job_metrics_fetch` 捕获 `BrowserBusyError`:第一类目等不到锁 ⇒ 该类目不启动浏览器、不发请求,记 `skipped_browser_busy`(含持锁 pid/tag),本轮后续类目直接让路,不计入 `errors`;回执带 `browser_lock_wait_s` / `browser_busy_skipped` |
| `00_control/config.json` | `browser` 增 `lock_wait_s: 600`、`lock_poll_s: 2.0`;`metrics_fetch_6h` 增 `lock_wait_s: 300` |
| `10_tests/conftest.py` | 新增 autouse 夹具 `browser_profile_isolated`:测试中的 profile 重定向到 tmp,并**逐用例守卫生产 `02_crawler/.browser/profile.lock` 不得被创建/改动** |
| `10_tests/test_p36_browser_lock.py` | **新增 21 条测试**,跨进程部分全部用真 `subprocess` 多进程,不用 mock 冒充 |

Runner(`ttxt/crawler/runner.py`)**故意不改**:它的 `with fetcher.browser_session()` 已在 try 内,
锁超时异常走原有失败路径 ⇒ 任务回 `queued` 重试;默认 600 秒等锁预算远大于指标一轮(实测约 3.5 分钟),
正常情况就是「等一会儿」而不是失败。

## 3. 十三项验收 → 测试(全部真多进程 / 真异常,不用 mock 冒充跨进程)

13 条要求全部有对应用例,**21 passed in 21.21 s**;映射写在测试文件开头的 docstring 里。要点摘录:

- 两进程争抢 ⇒ 只有一个拿到锁(`test_two_processes_contending_only_one_gets_the_lock`);
- 持锁期间第二进程**开不出浏览器**(`test_busy_lock_blocks_second_process_from_opening_browser`);
- 持锁进程正常退出 ⇒ 锁交接(`test_lock_hands_over_after_holder_exits`);
- **强杀持锁进程**(`taskkill /T /F`,含 venv 外壳进程树)⇒ 内核自动释放(`test_kernel_releases_lock_after_forced_kill`);
- 超时是**明确状态**且带持锁诊断(`test_timeout_is_explicit_state_with_holder_diagnostics`);
- 启动失败 / 关闭抛异常 / 会话内异常都释放锁(三条独立用例);
- 还在被使用的锁文件**绝不删**(`test_live_lockfile_is_never_deleted`),只有能证明无人持有才清
  (`test_stale_lockfile_cleared_only_when_provably_unowned`);
- 旧的无条件删除不许复活(`test_old_indiscriminate_lock_deletion_is_gone`);
- 嵌套同一进程只起一个浏览器、只取一次锁(`test_nested_session_opens_single_browser_and_locks_once`);
- Worker 与指标采集共用同一把锁(`test_metrics_and_worker_share_one_lock`);
- 静态 HTTP 抓取完全不碰锁(`test_static_fetch_never_touches_browser_lock`);
- 结构性护栏:除 `browser_session()` 外不得有第二处起浏览器(`test_no_caller_can_bypass_browser_session`)、
  锁不得退化成看 PID/时间戳猜进程死活(`test_lock_does_not_rely_on_pid_or_timestamp`)、
  探测只探不占且不刷日志但结论必须真(`test_probe_free_is_silent_and_still_truthful`)。

**回归:全量 `650 passed in 161.85s`**(P35 基线 629 + 本轮 21)。测试结束后
`02_crawler/.browser/profile.lock` 未被测试创建(夹具守卫生效)。

## 4. 生产实测(2026-11-17 12:05–12:34,真调度器 + 真 Worker + 真队列 + 真浏览器)

先重启被托管进程让新代码生效:`taskkill /T /F /PID` 掉旧 worker(24140)与旧 scheduler(21176)整棵进程树,
supervisor(35268,未动)在 30 秒内拉起新代码:scheduler 17236(12:05:41)、worker 11084(12:05:46)。

### 4.1 基线:新代码下真实一轮指标回流(无竞争)

12:12:00–12:15:39,**218.2 秒**,状态 `ok`,5 个类目全部完成,`browser_busy_skipped: 0`;
profile 目录 1791 个文件 / 622,049,656 字节 → 2221 个文件 / 662,727,551 字节(浏览器正常工作产生的缓存增长)。
账本写到 `07_tmp/p36/prod_state.json`,**没有占用/改动生产 `scheduler_state.json`**,原定 15:53 的那轮不受影响。

### 4.2 竞争:另一进程持锁时,`metrics_fetch` 的表现在生产里被逐条观察到

| 时刻 | 观察到的事实(来源:日志事件、库内读数、进程采样) |
|---|---|
| 12:19:23 | 独立进程拿到生产锁(`tag=p36-prod-holder`,持锁 pid 37512) |
| 12:21:07 | 启动 `metrics_fetch_6h`(`run_specific`,账本走临时文件) |
| 12:19–12:26 | 旁路采样 **30 次:chrome 进程数恒为 0**;锁 30/30 处于被占用 |
| 12:26:07 | `crawl.browser_lock.timeout` `waited_s=300.0`,并带持锁者 pid/tag |
| 12:26:07 | 回执 `scheduler_status: skipped_browser_busy`;technology 明细:`等浏览器 profile 锁 300.0s 没等到(持有者 tag=p36-prod-holder pid=37512);该类目不启动浏览器、不发请求`;其余 4 类目 `本轮已确认浏览器 profile 被占用(technology),该类目不排队`;`errors: []`;`browser_lock_wait_s: 300`;`browser_busy_skipped: ["technology"]` |
| 12:26:08 | profile 目录与持锁前**逐字节一致**:2221 个文件 / 662,727,551 字节 / 最新 mtime `1794888935088070500` 未变 ⇒ 确实没启动浏览器、没发请求 |

### 4.3 放锁前后的采集侧表现(真 Worker 消费真队列)

| 任务 | 入队时刻(锁仍被占) | 结果 |
|---|---|---|
| 136 `ruanyifeng_atom`(**静态 http**) | 12:28:41 | **12:28:49 done,8.03 秒**(锁被别人占着照跑:抓到 3 条、3 条重复、入库 0) |
| 137 `toutiao_tech`(**浏览器渲染**) | 12:30:12 | 12:30:12–12:32:45 状态 `running` 但 **chrome 进程数始终 0**(在等锁,没硬抢);12:32:45 `crawl.browser_lock.acquired waited_s=152.0 tag=browser_session`;12:33:44 `released held_s=59.5`;12:33:52 `done`,pages_fetched 11、items_extracted 10、**inserted 10** |
| 放锁动作 | 12:32:4x(写放行门,持锁进程自己 release,不是我们替它清账) | 之后锁空闲,队列无失败任务(12:00 以后队列读数:done 2) |

⇒ 要求里的「Worker 与指标采集共用同一把锁」「静态 HTTP 不占锁」「超时是明确状态」「交接」在生产环境全部对上号。

## 5. 体检与备份

- `ttxt doctor`:**36 ok, 0 failed**。WARN 均为既有已知项(demo 类目未注册、history 有 1 篇谎报全文、
  demo 30 行把来源页当文章地址、9 条 `evidence_links.article_id` 为空)—— 本轮没有新增 WARN。
- 改前备份:`11_backups\p36-before-profile-lock-20261117-120307.zip`(21,990.4 KB)。

## 6. 顺带修掉的文档不实之处

- `08_wiki/operations.md` 里「锁文件残留:mtime 超 30 分钟且持有进程已死就会被接管」在代码里**没有对应实现**,
  已改为如实描述:P36 的 profile 锁没有「陈旧锁接管」,内核自动释放;`profile.lock` 不是垃圾文件。
- 限额表里「浏览器目录 3 GB(当前未启用浏览器)」是过时事实,已改为实测 662.7 MB 且**正在使用**。
- 探测(`probe_free`)过去会把「没拿到锁」写成 warn,高频探测会把真正等待者的一条 warn 淹掉;
  现在探测**完全静默**,只返回结论(有测试钉住)。

## 7. 如实说明:哪些已验证、哪些没验证

- **已验证(Windows,本机实测)**:跨进程互斥、强杀后内核释放、超时状态、锁放掉后的交接、
  启动/关闭异常下锁释放、活锁文件不被删、静态抓取不占锁、嵌套只起一个浏览器、生产环境两条链路(指标/采集)行为。
- **未验证**:POSIX(`fcntl.flock`)分支在这台 Windows 机器上**没有跑过**;跨平台上线前需要在 Linux/macOS 上补一次同样的多进程验收。
- **未做**:`lock_wait_s` 与任务超时的联动上界(现靠配置纪律,代码不自动夹取);锁的公平性(等待者按轮询抢占,不排队保序)—— 当前两个进程的使用场景不需要。
- 锁文件会**永久驻留**(约 90–100 字节 JSON 诊断信息),这是设计如此:它是否"被占用"由内核句柄决定,不看文件内容,也不做任何按时间的清账。

## 8. 回滚方法

- 代码级:`git revert` 本轮提交即可(涉及 `fetcher.py` / `scheduler.py` / `metrics_pages.py`,删掉 `browser_lock.py`),
  然后重启被托管进程(同上,`taskkill /T /F /PID` 让 supervisor 用旧代码拉起)。
- 整包级:解压 `11_backups\p36-before-profile-lock-20261117-120307.zip`,按运维手册第 4 节恢复 `db/`。
- 数据级:本轮**没有**任何 DDL、没有类目库结构变化、没有改过任何一条业务口径,故无需数据回滚。
- 运维注意:新代码**必须重启 scheduler/worker 才生效**;`profile.lock` 遗留在盘上无害,不要手工删它来「解锁」——
  真要立刻让路就停掉持锁进程(日志里 `crawl.browser_lock.timeout` 会给出 pid)。

## 9. 下一轮(未在本轮做)

social/health 类目的新来源;`health` 全文缺失与 `history` 谎报全文整改;9 条 `evidence_links.article_id` 补全;
`keyword_performance` 的 signal/measured_hits 落库;正文去模板化与 L4 写作质量;`_element_text` 标题缺陷;
demo 类目注册与 `factory.db` 迁移;`07_tmp` 约 0.9 GB 清理。
