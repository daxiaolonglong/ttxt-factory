# P35 · 互动数据自动回流接进调度器 + 下架台账两个时间边界

日期:2026-11-17(周一)上午,执行人:AI(工程),复核意见来源:阿龙提供的 AI 技术审查 + 真人使用反馈
工作目录:`D:\ttxt`(全部改动只在本盘)
相关提交:代码 `b63a2a7`、补丁 `d062fb5`、本报告随后提交
备份:`11_backups/p35-before-auto-reflow-20261117-093939.zip`(17,801,410 字节,testzip OK)

---

## 一、这一轮要解决什么(根因)

1. **闭环里断掉的一段是"互动数据回流"**。P33/P34 把公开页计数采集做出来了,但它只能靠人敲
   `ttxt metrics --fetch-public --apply`。调度器里四个任务(入队、分析、恢复、清理)**没有一个会采指标**,
   所以"采集 → 清洗 → 分析 → 学爆款 → 反哺关键词"这条链子的中段是人工的:人不跑,爆款标签和关键词评分
   就一直停在旧样本上。根因不是缺代码,是缺**接进调度器的那一步**(配置里有 `metrics.public_pages`
   的来源/白名单/上限/间隔,却没有对应的 job)。
2. **台账时间边界①:未来的 `checked_at` 会被当成有效**。P34 的 `_status_fresh()` 只算
   `(now - checked).days < 90`;时间戳在未来时差值为负,永远小于 90 ⇒ 一条写错的时间戳能把"已下架"
   永久锁成有效期内,该重试的永远不重试。
3. **台账时间边界②:`checked_at` 用的是批次时间**。一轮 12~20 篇要跑几分钟到十几分钟,
   整批共用开始时刻,等于给最先探到 404 的几篇凭空多加几分钟"新鲜期",这几分钟里它们本该被重新取证。

## 二、改了什么

**`ttxt/crawler/metrics_pages.py`**
- `FUTURE_TOLERANCE_MIN = 15` + `_status_fresh()` 里的未来时间戳判定:晚于当前超过 15 分钟 ⇒ 该条状态
  不作数,并打 `metrics.link_status.future_stamp` 日志。容忍 15 分钟(而不是 0)是因为本机是 Hyper-V/WSL2
  来宾、Windows 默认不同步宿主时钟(历史上实机快过 7 天,见 P28 报告):十几秒到十几分钟的偏差是常态,
  必须照单全收,否则每轮都把台账白判废、反复重采同一批下架文章。
- `collect_public_metrics(..., clock=...)`:逐篇时钟。在**发起请求之前**给这一篇盖章,
  台账与回执优先写这个逐篇时间(`outcome["checked_at"]` → 台账 `checked_at`),取不到才退回批次时间
  (兼容 P34 已写的旧证据)。发请求前盖章是刻意的:90 天 TTL 只会提前失效,不会延后失效。
- 新增 `round_plan(category, limit=...)`:只读算出"这轮打算采谁"(候选数、选中 id、`host_skipped`、
  `known_gone_skipped`),零请求、零写库,给调度器当"要不要开浏览器"的闸门。

**`ttxt/scheduler.py`**
- 新增 `kind=metrics_fetch` runner `_job_metrics_fetch()`:复用现成的 `collect_with_browser()`,
  保留授权/robots/限速/404-410 跳过/每轮数量上限;没有另造一套调度。
- **默认 dry-run**:配置不写 `apply: true` 就绝不写生产库(dry-run 用采集器自己的 `would_write`,
  不拿 `written` 冒充)。
- **给 Worker 让路**:调度进程与 Worker 是两个进程、共用同一份 `02_crawler/.browser/profile`,
  不能同时开浏览器。开跑前查 `crawl_jobs`(**只数 `job_type='crawl'`**,Worker 永不认领别的类型,
  把别的任务算忙会把指标采集饿死);有 queued/running 就有界等待 `wait_for_queue_s`,
  等不到整轮记 `skipped_queue_busy`,一个请求都不发。
- **采集全部类目完成后才同步口径**(relabel → analyze)。`run_analysis` 里的关键词反哺会往队列塞任务,
  顺序反过来就可能出现"a 的反哺任务被 Worker 抢走、b 还在用浏览器"。
- 类目之间互不牵连:某类目异常 ⇒ 只记该类目的 `error` + 落失败证据文件,其它类目照跑;
  整轮状态取 `ok / partial / error / skipped_queue_busy`。
- `run_due` 记账改为**尊重 runner 声明的状态**:runner 返回 `scheduler_status ≠ ok` 时,
  `last_status` 就写那个值,不再一律记成 ok —— 失败不会被显示成成功。
- 新增 `run_specific()` + CLI `ttxt schedule --run-job <id>`:手动执行指定任务并记账(不等到期),
  状态非 ok 时返回码非 0(脚本可以据此判成败)。

**`00_control/config.json`**:`scheduler.jobs` 新增
`{"id":"metrics_fetch_6h","kind":"metrics_fetch","interval_min":360,"apply":true,"limit":12,"sleep_s":2.5,"wait_for_queue_s":300,"queue_poll_s":20,"retry_gone":false}`,
**排在 `crawl_enqueue_4h` 之前**:同一个 tick 两个任务都到期时,先采指标(此时队列必然空闲)再入队采集。

**`ttxt/__main__.py`**:`schedule --run-job`。
**Wiki**:`db_schema.md` 台账两条时间边界的口径;`operations.md` 调度器指标回流的读法、让路规则、
`--run-job`、回执键含义。

## 三、明确没改什么(边界)

未动采集核心(runner/worker/fetcher/dedupe)、未动数据库结构(零 DDL、零新表、零加字段)、
未动 Strata、未动写作模块。`FORBIDDEN_KINDS`(write/publish/model/model_drain/draft)不变 ——
调度器仍然绝不自动写作或发布;真人签署与发布决定照旧由阿龙本人做。
P34 已完成的 606 项测试没有重做或改写,只有追加。

## 四、测试(先红后绿)

新增两批,全部先跑出红(缺 `clock`/`round_plan`/runner 的 `TypeError`/`AttributeError`)再实现:
- `10_tests/test_p34_metrics_link_status.py` +4:未来时间戳拒绝(含 3 分钟容忍内不拒)、
  未来时间戳不会让下架文章继续被跳过、注入 `clock` 后同轮两篇的 `checked_at` 必须不同(台账同步)、
  `round_plan` 只读且与真实轮次一致(采完一篇后计划自动挪走它,不重写台账、不写指标)。
- `10_tests/test_p35_metrics_scheduler.py` +19:计划→采集→重标定→分析的顺序与参数、默认干跑不触发同步、
  0 候选不调采集器、队列忙 ⇒ 让路且零采集、队列排空后继续(打补丁验证真等过)、
  非 crawl 类型不阻塞、采集中途异常不触发下游、失败与让路都进 `scheduler_state.json`、
  类目隔离(partial,好类目仍完成闭环)、失败也落证据、成功证据对账、
  `metrics_fetch` 允许而 write/publish 仍禁止、**生产配置有且只有一个有限速/有上限/排在入队之前的指标任务**、
  全部采集排在任何口径同步之前、写库成功而同步失败时报 `written_sync_failed`(已写条数仍可见)、
  阈值快照落后要给 `calibration_hint`(样本不足/快照已是当前则不提示)。

全量:**629 passed**(改前 606,新增 23);`ttxt doctor`:36 ok / 0 failed,5 项已知 WARN 不变。

## 五、生产读数(对账表)

改前基线 `07_tmp/p35/p35_baseline_before.json`(09:39:39,零网络零写入);备份已在基线之前打好。

| 时点 | 动作 | 结果 |
|---|---|---|
| 09:38:01–09:41:51 | 调度器自动**干跑**(新代码,`apply:false`) | 状态 ok;technology/finance/history 各 fetched 12、would_write 12;social/health `no_candidates` 不开浏览器;technology `known_gone_skipped=4` |
| 09:49:13 | 重启调度子进程载入修复(pids 2260/38388;supervisor 35268 与 worker 未动) | 子进程由 supervisor 5 秒内拉起 |
| 09:49:46–09:53:09 | `ttxt schedule --run-job metrics_fetch_6h`(`apply:true`,真实一轮) | 状态 ok;队列 idle(queued 0/running 0,waited 0s);36 篇采集、36 写库、36 快照;每类目随后 relabel + analyze |

| 类目 | articles | article_metrics | metric_snapshots | measured 标签 | 下架台账 |
|---|---|---|---|---|---|
| technology | 262(不变) | 165 → **177** | 181 → **193** | 165 → **177** | 4 条照旧跳过(新登记 0、清除 0) |
| finance | 240 | 10 → **22** | 10 → **22** | 10 → **22** | 0(本轮无 404) |
| history | 106 | 10 → **22** | 10 → **22** | 10 → **22** | 0 |
| social | 72 | 0 | 0 | 0 | 0 候选(host_skipped 72) |
| health | 25 | 0 | 0 | 0 | 0 候选(host_skipped 25) |

全厂实测样本 **185 → 221(+36)**;下架台账仍为 P34 的 4 条(ids 70/149/156/233,`checked_at` 仍是旧证据时间,
因为本轮它们被跳过、没有重新取证 —— 这正是跳过机制该有的表现)。
证据文件已提交:`03_intelligence/metrics_public/{technology,finance,history}-20261117T093801.json`(干跑)
与 `-20261117T094946.json`(真实轮);关键词反哺回执 `03_intelligence/keyword_feedback/technology-feedback-20261117T095306+0800.json`
(本轮没有新增队列任务:`crawl_jobs` 总数 124 不变,跑前跑后队列都空闲,没有制造浏览器争用)。

每轮标准动作第 3 步(标定)也按规矩跑了:technology 阈值快照 165 样本 `[0.0525, 0.0258, 0.0123]`
→ 177 样本 `[0.0529, 0.026, 0.0125]`(status=calibrated);finance/history 各 22 条 < 30,
如实保持 `default_thresholds(sample=0)`,不猜阈值。technology 实测标签分布:verified_viral 19 /
potential 51 / normal 63 / low_quality 44。关键词评分同步刷新(technology 头部关键词 collected 113、score 0.8112)。

**爆款阈值不自动重算**:自动闭环只做 relabel(按现有阈值重贴标签),阈值漂移由人决定;
样本涨了而快照落后时,回执里给 `calibration_hint`(见 `d062fb5`)。

## 六、时间边界到底验证到什么程度(不含糊)

- 边界①(未来时间戳不作数):**只有测试证明**。生产台账里没有未来时间戳,也不人为造一个。
- 边界②(逐篇独立取证时间):测试已证;**生产尚未触发** —— 本轮 36 篇没有新增 404,没有新台账条目可看。
  下一轮真出现 404 时核对方法:同一轮不同文章的 `checked_at` 应当互不相同(秒级差别),
  而不是像 P34 那 4 条全是同一个时刻。核对命令(只读):
  `python -c "import json,sqlite3;from ttxt import config as c;con=sqlite3.connect(f'file:{c.category_db_path(\"technology\").as_posix()}?mode=ro',uri=True);print(json.loads(con.execute(\"select value from articles_meta where key='metrics_link_status'\").fetchone()[0]))"`
- 90 天 TTL 语义、404/410 才进台账、451/`fetch_failed`/`robots_refused` 不进不清、重新探到 200 自动清除:
  口径不变,测试继续锁着。

## 七、Git 与回滚

- `b63a2a7` 代码 + 配置 + 测试 + Wiki + 本轮证据(15 files, 5334 insertions)
- `d062fb5` calibration_hint 补丁(3 files, 51 insertions)
- 回滚代码:`git revert d062fb5 b63a2a7`(或 `git checkout` 到 `d80d5e0`),再在 `scheduler.jobs` 里删掉
  `metrics_fetch_6h` 就彻底停掉自动采集 —— 不需要停 supervisor。
- 回滚数据:解压 `11_backups/p35-before-auto-reflow-20261117-093939.zip`,把
  `01_categories/*/articles.db` 与 `00_control/system.db` 覆盖回去即可(本轮零 DDL,回滚不动结构)。
  备份是写库前打的,含 6 个类目库 + system.db + 配置 + 当时源码与测试。

## 八、运维怎么读这些状态

- `ttxt schedule`:看 `due` 与 `last_status`;`metrics_fetch_6h` 排在第一位,间隔 6 小时,
  下一次到期 15:53(本轮 09:53:09 完成)。
- `00_control/scheduler_state.json`:每任务 `last_run_at/last_status/last_result/last_error`。
  指标任务的 `last_result` 里有 `queue`(queued/running/busy/waited_s/wait_budget_s)、
  每类目明细(`fetched/written/updated/snapshots/known_gone_skipped/link_status_recorded|cleared/row_errors/outcome_counts/measured/calibration_hint`)、`errors`。
  状态值:`ok / partial / error / skipped_queue_busy`;干跑时类目级还有 `dry_run / no_candidates`。
- 每轮每类目一份证据:`03_intelligence/metrics_public/<类目>-<轮次时间戳>.json`(失败也落)。
- 失败会烧掉 6 小时间隔(与其它 job 一致,有意为之,防止紧重试反复打平台);要更密就调 `interval_min`。

## 九、未解决 / 未验证(如实列出)

1. 跨进程浏览器**只有"让路",没有"互斥"**:调度器看队列、有界等待、类目顺序约束,但没有真正的进程级锁。
   若入队在指标采集中途发生、Worker 抢先开浏览器,现在只能靠 Worker 自己的重试与 recover 兜。
2. 边界②的生产实证要等下一次真出现 404(见第六节)。
3. social/health 两类 0 候选:它们的 97 篇文章 URL 不在头条白名单域内 —— 这是**来源与 URL 数据问题**,
   不是调度问题;要采互动得先补来源(不动抽取器口径的情况下无法解决)。
4. demo 类目仍未注册(已知 WARN),`factory.db` 遗留库仍未迁移。
5. 租约回收、整机重启后的自动恢复仍未实测;`07_tmp` 约 0.9 GB 未清理;
   health 全文缺失、boilerplate 清洗(`PROCESSOR_VERSION`)、`keyword_performance` 的 signal/measured_hits 字段、
   9 条 `evidence_links.article_id` 为空、`_element_text` 标题缺陷 —— 均不在 P35 范围,原样留着。

## 十、下一轮建议(P36)

**在 `fetcher` 里做进程级浏览器 profile 锁**(文件/命名互斥 + 超时让路 + 崩溃残留清理),
把现在的"让路"升级成真正的互斥:metrics 与 crawl 的浏览器会话都排在锁后面,
调度器就不必依赖"排在入队之前"这种顺序约定,`skipped_queue_busy` 也会从常态变成异常态。
建议连带把 P34/P35 的浏览器路径都收进同一个锁,再补一条跨进程集成测试(两个进程抢同一 profile)。

## 十一、验收状态

- 审查提出的三件事全部落地:两个台账时间边界(带测试)、公开互动指标采集接进现有调度器
  (复用既有采集器、按类目独立、保留授权/robots/限速/404-410 跳过/每轮上限、失败留明确状态)、
  时间边界测试补齐。
- 闭环已自动:采集 → 入库 → 重标定爆款标签 → 关键词评分 → 反哺入队,每 6 小时一轮,无需人工。
- 生产已验证一轮真实回流:+36 条实测指标、+36 条快照、三类目 measured 样本同步上涨、
  关键词评分与阈值快照按规矩刷新;下架台账 4 篇照旧跳过,没有重复请求,没有浏览器争用,队列没被塞爆。
- 回归:629 项测试全绿(改前 606);`ttxt doctor` 36 ok / 0 failed;进程与 supervisor 未受扰动
  (只换过调度子进程,由 supervisor 自动拉起)。
- 待观察项:下一轮 `metrics_fetch_6h` 到期(今日 15:53)后 `last_status` 仍应为 ok,
  且届时若队列正忙应看到 `skipped_queue_busy` 而不是硬抢浏览器。
