
## 十四、P30:逐篇来源身份清单 + 独立性口径落地(17:00-17:55)
- 口径落地成代码:`ttxt/analysis/source_identity.py`(五类身份 + 独立性判定 + 带理由配对)、`scripts/source_identity_probe.py`(逐页取证)、`scripts/cross_media_evidence.py`(全类目配对)。
- 中新网 53 篇逐篇清单:42 自有 / 11 转载稿(央视 6、总台中国之声 2、人民日报 1、中国天气网 1、「浙江宣传」公众号 1),0 身份不明,robots 无拒绝;清单落盘 `03_intelligence/source_identity/`。
- 全类目配对:成对候选 3 组(全在财经 eastmoney×tmtpost),被拒 417 组并写明理由(同一域名 393、来源不独立 15、相似度不足 5)。
- 候选对逐页复核(需浏览器渲染):3 组候选里 2 篇是转载稿、1 篇无署名 ⇒ **本轮可成立 B 卡 0 张**,A/B 覆盖仍 27.9%,距 >50% 还差 ≥28 张真 B 卡。
- 回归 467 passed(新增 14 条)、doctor 34 ok 0 failed、边车已清理、`golden_set_v2.jsonl` 未改一字节、真人签署仍 0/61。
- 报告:`08_wiki/reports/P30-source-identity-and-independence-20261116.md`。

## 十五、P31 阶段一:进程托管与调度停摆治理(18:18-19:05)
- 根因实测:`schedule --daemon` 与 `worker` 有代码有配置,但没有任何进程托管、也没有 OS 自启动;`scheduler_state.json` 冻结在 2026-11-12 19:57,停摆约 **94 小时**,而旧 doctor 完全看不出来(停摆是静默的)。
- 新增 `ttxt/supervisor.py`:只托管 `schedule`/`worker`(write/publish/model/draft/serve 写死在禁止清单里);区域锁单实例 + `supervisor.pid` 边车 + Win32 PID 存活判定;崩溃记录退出码并退避重启,超阈值放弃并写原因;`supervisor_stop` 优雅收尾,绝不与运维的停止标志对着干。
- CLI:`ttxt supervisor --run/--ensure/--status/--stop/--clear-stop`,默认值取 `config.supervisor`(新增段)。
- doctor 新增两行(另有 `ops.supervisor_children`、`ops.scheduler_jobs`):托管不在 ⇒ FAIL 并写清原因;任务超过两个周期没跑 ⇒ FAIL。实测两种方向都能看到。
- 计划任务已注册:`ttxt-supervisor-watchdog`(Daily 00:01 + 每 5 分钟,WorkingDirectory=D:\ttxt,入口 `scripts\supervisor_ensure.py`)。第一版踩坑:任务默认工作目录是 System32 ⇒ `-m ttxt` 报 `No module named ttxt`、`Last Result=1`;已修并补回归测试。
- 更正上一轮的误判:venv 的 `pythonw.exe` 是外壳,会派生真解释器子进程,所以 27156+27264 是**一个面板实例**,不是两个;实测杀外壳不会留下孤儿进程。
- 验收:托管 pid 31012;scheduler 与 worker 各一个;手工起第二个 Worker 被拒(exit 2);再 `--ensure` 返回 `already-running`;强杀调度子进程后 5 秒内自愈(runs=2/restarts=1);三个到期任务 `last_run_at` 已刷到 18:54;`crawl_jobs` 72→**82 done**、`owner=supervisor`、`dispatched_via=scheduler`、带 robots 与入库回执;`model_jobs` 没有新增(未越界)。
- 回归 **484 passed in 108.89s**(467 基线 + 17 新增);全量测试前先把托管停下,不并发动生产库。
- 限制:计划任务是 Interactive only ⇒ 只在已登录时自动恢复;`onlogon` 需管理员,未安装;整机重启后自动恢复**未验证**。
- 报告:`08_wiki/reports/P31-process-hosting-and-scheduling-20261116.md`。回滚:`11_backups/p31-before-phase1-20261116-181802.zip`,代码回滚点 `75b5faa`。
