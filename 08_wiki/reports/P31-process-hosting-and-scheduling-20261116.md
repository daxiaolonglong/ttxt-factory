# P31 阶段一:把调度与采集 Worker 真正"养住"(2026-11-16 18:18–19:05)

本轮只干一件事:**让 `schedule --daemon` 和 `worker` 有人托管、能自动恢复,并且停摆必须在体检里看得出来**。
不做写作、不做发布、不调模型。

## 一、根因(实测,不是推测)

1. 代码与配置都在:`config.scheduler.enabled=true`,4 个任务(`crawl_enqueue_4h` / `analyze_daily` / `recover_daily` / `housekeep_weekly`)。
2. 但**没有任何进程托管**,Windows 层也没有自启动项(注册前 `schtasks /query` 里没有任何 ttxt 任务)。
3. `00_control/scheduler_state.json` 冻结在 `2026-11-12T19:57:35+08:00` —— 到本轮开始时约 **94 小时**没有任何自动任务执行。
4. 进程快照只看到面板:`27156`(venv 外壳)+ `27264`(真解释器)。当时判断"面板起了两份实例"是**错的**:见第三节第 2 条。
5. 旧 `ttxt doctor` 里没有任何"有没有人在跑"的检查 ⇒ 停摆是**静默**的,这是它最危险的性质。

## 二、改了什么

| 文件 | 作用 |
| --- | --- |
| [`ttxt/supervisor.py`](../../ttxt/supervisor.py)(新) | Windows 进程托管:看护 `schedule --daemon` 与 `worker`,崩溃重启、退避、放弃阈值、优雅收尾、状态落盘 |
| [`ttxt/__main__.py`](../../ttxt/__main__.py) | 新增 `ttxt supervisor --run / --ensure / --status / --stop / --clear-stop`;参数默认取 `config.supervisor` |
| [`ttxt/doctor.py`](../../ttxt/doctor.py) | 新增 `supervisor_checks()` ⇒ `ops.supervisor`、`ops.supervisor_children`、`ops.scheduler_jobs`、`ops.scheduler_heartbeat` |
| [`00_control/config.json`](../../00_control/config.json) | 新增 `supervisor` 段(间隔、退避、重启阈值、心跳新鲜度阈值),唯一口径来源 |
| [`10_tests/test_p31_supervisor.py`](../../10_tests/test_p31_supervisor.py)(新) | 17 条回归:允许托管的进程种类、单实例、崩溃记录与重启、放弃阈值、停止标志收尾、体检两行的两种方向 |
| `scripts/start-supervisor.cmd` / `start-supervisor-hidden.vbs` / `stop-supervisor.cmd` / `supervisor-status.cmd` | 人用的开关 |
| `scripts/install-supervisor-tasks.cmd` / `uninstall-supervisor-tasks.cmd` | 计划任务安装/卸载(命令与本轮实际注册的完全一致) |
| [`scripts/supervisor_ensure.py`](../../scripts/supervisor_ensure.py)(新) | 计划任务入口:自带 `sys.path`,与 cwd 无关 |
| `.gitignore` | 忽略托管运行期状态(`supervisor.lock` / `.pid` / `_state.json` / `_stop`) |

设计上的硬边界(写死在代码里,不是口头承诺):
`ALLOWED_CHILD_KINDS = {"schedule", "worker"}`;`FORBIDDEN_CHILD_KINDS = {write, publish, model, model_drain, draft, serve}`;`build_child_commands(extra_argv=["write"])` 直接 `ValueError`。
托管**绝不跟运维对着干**:见到 `supervisor_stop` 就收尾;Worker 自己看到 `worker_stop` 时托管记录 `give_up_reason="blocked_by_stop_flag"`,不会反复重启把运维的停止冲掉。

## 三、踩到并修掉的两个真坑(都有取证)

1. **计划任务的工作目录**。第一版用 `schtasks /create … pythonw.exe -m ttxt supervisor --ensure` 注册成功、也真的按时跑了,但 `Last Result=1`,什么都没起来。取证(`07_tmp/p31_phase1/ensure_traceback.txt`)显示 `ModuleNotFoundError: No module named 'tttxt'` —— 计划任务默认工作目录是 `C:\Windows\System32`,`-m ttxt` 找不到包。
   修法:任务入口换成自带 `sys.path` 的 `scripts\supervisor_ensure.py`,并用 `New-ScheduledTaskAction -WorkingDirectory D:\ttxt`;托管启动子进程时显式 `cwd=cfg.PROJECT_ROOT`。补了回归 `test_children_run_from_the_project_root`。
2. **venv 的 `python.exe/pythonw.exe` 是"外壳"**,它会派生真正的解释器子进程(`27156 → 27264`、`23976 → 32484`、`22396 → 9236`)。所以:
   - 进程列表里的"两份面板"其实是**一个实例的两个进程**(更正上一轮的判断);
   - 实测强杀外壳后真解释器**不会**变成孤儿(`杀外壳后真解释器也没了`),所以 `terminate()` 足够,不需要 Job Object;
   - 判断"进程还在不在"必须用 Win32 `OpenProcess + WaitForSingleObject` 查 PID,不能数进程个数、也不能看锁文件名。锁文件里的 PID 写在 `supervisor.pid` 边车:`msvcrt` 区域锁占用的字节范围连自己进程都读不出来(踩过 `PermissionError`)。

## 四、验收证据(全部实测)

- **拉起**:在 `cwd=C:\` 下 `pythonw scripts\supervisor_ensure.py` ⇒ 托管 pid **31012**,子进程 scheduler **23976**(真解释器 32484)、worker **22396**(真 9236),各一个。
- **单实例**:手工再起一个 Worker ⇒ `拒绝启动:已有 Worker 持有 worker.lock,拒绝第二个实例并发跑浏览器`(exit=2);再 `--ensure` ⇒ `{started:false, reason:"already-running", pid:31012}`。
- **崩溃自愈**:强杀调度子进程外壳 23976 ⇒ 约 5 秒后新起 13816;状态文件记录 `runs=2 restarts=1 exited_at=18:58:40`;复核确认**没有第二个 daemon**。
- **停摆结束**:`ttxt schedule --status` 里 `crawl_enqueue_4h` / `analyze_daily` / `recover_daily` 的 `last_run_at` 已是 `2026-11-16T18:54:25~27`(此前 11-12 19:57);`housekeep_weekly` 未到期(下一个 11-19)属正常。
- **真链路跑通**:`crawl_jobs` 从 72 done 变成 **82 done(+10)**,新任务 `meta.dispatched_via="scheduler"`、`owner="supervisor"`、带终态回执(`robots_allowed=true`、`inserted/duplicates/pipeline` 统计)。实例:eastmoney_rss 新入 30、tmtpost_rss 17、ifanr_feed 11、chinanews_society 20。`model_jobs` 仍 70 done **没有新增** ⇒ 没有越界调模型。
- **优雅停止**:`--stop` ⇒ `stop_reason=stop_flag`、`stopped_at` 写入、两子进程 `terminated=True / killed=False`、无残留进程、`stale_lock=false`。
- **体检双向可见**:托管在跑 ⇒ `[PASS] ops.supervisor` + `[PASS] ops.scheduler_heartbeat`;停掉后 ⇒ `[FAIL] ops.supervisor: 没有常驻托管进程(没有 PID 记录;supervisor_stop 存在(人为停止))—— 调度与 Worker 停摆`,doctor 退出码 1。
- **回归**:**484 passed in 108.89s**(基线 467 + 本轮新增 17),`--basetemp=D:\ttxt\07_tmp\pytest_basetemp`;跑全量前先把托管停下来,没有并发动生产库。
- **卫生**:新增/改动文件全部 UTF-8 无 BOM、LF(与仓库既有脚本一致);无密钥泄漏(`config.json` 里只有 `api_key_env` 这种环境变量名);D: 可用 104.3 GB;生产库目录没有新增 WAL/SHM 边车。

## 五、限制与未验证(必须说清楚)

- 计划任务是 **`Logon Mode: Interactive only`**(非管理员不能保存密码):只在**已登录**状态下自动恢复,**注销或未登录时不会跑**;`/sc onlogon`(登录瞬间拉起)实测 `Access is denied`,需要管理员,本轮**未安装**。
- 兜底周期 5 分钟 ⇒ 托管死掉后最长 5 分钟没人采集/入队;要更短需改任务的 `/ri`。
- **未验证**:整机重启后面板与托管的自动恢复(没做重启实验);`max_restarts` 触发"放弃"之后的生产表现(只有单测覆盖);被强杀时正在跑的采集任务(id 90 `chinanews_culture`)靠租约过期回收 —— 只观察到 `running→租约 19:14:07`,尚未观察它最终回到终态。
- 面板仍只有 `serve --port 8765` 一个实例(不是两个),面板本身没有托管,也不该被托管(`serve` 在禁止清单里)。

## 六、回滚位置

- 备份:`11_backups/p31-before-phase1-20261116-181802.zip`(11.5 MB,本轮开始前)。
- 代码:`75b5faa` 是本轮之前的 HEAD;`supervisor.py` 不被任何旧模块 import,`doctor.py` 的新检查包在 `except Exception` 里,不会让体检崩掉。
- 运行侧:`scripts\stop-supervisor.cmd`(优雅停) + `scripts\uninstall-supervisor-tasks.cmd`(删计划任务)。
- 数据侧:本轮只新增了采集入库(来自计划任务的正常采集),没有 schema 迁移,没有删除任何语料。
