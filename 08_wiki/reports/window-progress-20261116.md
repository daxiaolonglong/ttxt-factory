
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

## 十六、P31 阶段二:关键词覆盖核查与加固(19:18-19:36)

- **上一轮的假设被实测推翻**:"评分只匹配正文所以漏计标题"在现网上不成立 —— 532/532 条文章的标题都原样出现在清洗正文里;"清洗吃掉命中"和"大小写漏匹配"也都是 0 条(`07_tmp/p31_phase2/probe_title_gap.py`、`probe_gap_kinds.py`、`probe_title_inside.py`)。
- **「效率工具」0 覆盖是事实不是 bug**:technology 231 条里该短语正文 0 次、标题 0 次;散词是工具 46、效率 34、应用 44。死 seed 词要靠换词解决,不能靠放宽匹配凑数(伪造证据,与"不许改标签让门槛变绿"同一条红线)。health「睡眠」同样是零覆盖。
- 代码只做加固:`score_keywords` 改成"正文 + 标题"匹配,质量与平均长度仍只看正文,空关键词一律零覆盖。只读对照 `compare_haystack.py`:六个库**覆盖数差异 0 项** ⇒ 现网零影响,防的是将来清洗器剥掉标题后覆盖静默掉一半。
- 新增 `10_tests/test_p31_keyword_match_haystack.py` 9 条(先红:3 failed/6 passed → 全绿),钉死:不得模糊凑数、标题在正文内只算一次、子串与大小写语义、空正文质量记 0、类目隔离。
- **真坑**:托管跑起来后 Worker 长期持有 `worker.lock` 区域锁,`conftest._fingerprint` 和 p14 生产状态守卫读它的内容直接 `PermissionError`,每次全量回归都炸。已改成对 `*.lock` 只比"字节数 + mtime",规则 51 追加第四条坑。
- 验收:关键词相关测试合计 63 passed;全量 **493 passed in 109.01s**(484 + 9),且这次是**托管运行状态下**跑通的;只读体检 exit=0、36 ok/0 failed、`ops.supervisor` 心跳 6s 前;生产目录无新增 `-wal/-shm`,磁盘 97.1 GB;本轮**没写生产库**(类目库全用 `mode=ro`)。
- 顺带发现(本轮未修):清洗正文里还留着平台样板 —— 「作品声明」83 条、「关注」86、「声明」90、「更多」93 ⇒ 污染覆盖与相似度;修它需要改清洗器 + `PROCESSOR_VERSION` 递增 + 全量重清洗。
- 阶段一对报告的更正:id 90 在停托管前已 `done`,"任务在飞时靠租约回收"仍**未验证**;补上计划任务实跑 `Last Result: 0` 与今日语料 +210(托管批次回执 inserted 122)。
- 报告:`08_wiki/reports/P31-keyword-coverage-diagnosis-20261116.md`。回滚:`11_backups/p31-before-phase2-20261116-191859.zip`,代码回滚点 `dc825c5`。
