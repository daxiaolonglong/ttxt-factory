# P12 批次 5 证据报告 —— 指标可信度分级 + 爆款校准 + 长跑验收

生成时间:2026-11-13(Asia/Shanghai)
证据文件:`08_wiki/reports/p12b5-acceptance.json`(多轮合并,`previous_generated_at` 保留每一轮)
验收脚本:`scripts/p12b5_acceptance.py`(场景 A–I,`--only` 过滤,任何场景失败只记 `<key>_FAILED`,绝不中断整体)

## 一、本批真实改动(文件级)

| 文件 | 状态 | 内容 |
| --- | --- | --- |
| `ttxt/analysis/viral.py` | 修改(+104/-9) | 指标可信度分级(tier 字段消费)、按类目校准阈值 `calibrate_thresholds`,样本 < `MIN_CALIBRATION_SAMPLE=30` 时拒绝校准、回退 `default_thresholds` 并如实记录 `sample_size` |
| `ttxt/__main__.py` | 修改(+11) | 新增 `--calibrate` CLI 子命令,串行走模型闸门外路径(纯统计,不触模型) |
| `10_tests/test_p12_viral_calibration.py` | 新增(5 个测试) | 分级/校准/样本不足回退的回归 |
| `ttxt/crawler/runner.py` | 修改(+18,验收暴露的真实缺陷修复) | toutiao 全损(全部文章页抓取失败、0 产出)不再被吞成 `ok/done`,改为 raise → `fail_crawl_job` 可重试通道;部分失败仍完成但 `article_errors` 进观测并 WARN |
| `10_tests/test_p12_crawler_hardening.py` | 修改(+2 回归测试) | 全损重排(3 次上限落终态)与部分失败完成 |
| `scripts/p12b5_acceptance.py` | 新增 | 长跑验收 harness(场景 A–I) |
| `08_wiki/reports/p12b5-acceptance.json` | 新增 | 全部场景机器证据 |

## 二、指标可信度分级与校准(真实运行结果)

- `python -m ttxt metrics --category <cat> --calibrate` 实跑(2026-11-13 02:2x):
  - technology:`"真实测得样本不足,保持默认阈值并如实标注未校准"`,relabel 114 篇全部 `missing_data`,`calibration: {status: default_thresholds, sample: 0}`
  - finance:同样输出,relabel 50 篇全部 `missing_data`,`sample: 0`
  - 状态为 `insufficient_sample`(不视为错误,也不伪造校准结论)。`metric_snapshots`/`prediction_records` 为空是事实,不是缺陷粉饰。
- 指标入库门禁(场景 C 实测):
  - 未授权来源:`PermissionError: 未授权数据来源 'made_up_api':不在 config.metrics.authorized_sources 白名单内,禁止入库(防止编造指标)。`
  - 跨类目行:`第 1 行:文章不在 finance 类目库(跨类目或不存在),拒绝写入`,写入 0 行。
- 诚实声明:场景 C 的指标内容为**合成夹具,仅测门禁机制**;`metric_snapshots`/`prediction_records` 中至今没有真实的 `toutiao_admin_export` 数据(需 ≥30 行真实导出才能出校准结论)。

## 三、长跑验收场景证据(A–I,全部真实执行)

| 场景 | 结果 | 关键证据(JSON 内字段) |
| --- | --- | --- |
| A 重复入队 | 通过 | 第二次入队 `deduped:true`,返回同一 `job_id=34` |
| B 不存在来源 | 通过 | `rejected:true`,`来源不存在:no_such_source_xyz` |
| C 指标门禁 | 通过 | 见上;合成夹具已声明 |
| D 网络失败记录 | 通过 | 真实 404:`FetchError: HTTP 404(终态,不重试)`(ifanr 死链);robots 不可解析:`unknown:true, allowed:false` |
| E robots 拒绝 | 通过 | `ftchinese.com/interactive` `allowed:false, denied:true`(未抓取) |
| F worker 全队列 | 通过 | 7 个 job 全部 `ok:true`(34-40),107.2s,toutiao 103.19s 浏览器路径 inserted 10;RSS 去重 14+10+3+30+20+25;`model_jobs_delta=0` |
| G worker 硬杀 + 租约回收 | 通过 | job 45:杀 worker pid 28332 → 状态仍 running → 租约强制过期 → `recover_stale_crawl_jobs` 标 failed(`recovered: 进程中断(租约过期, owner=p12b5-hardkill-test)`)→ 重排 → recoverer 以 attempts=2 重跑至 done;`rerun_claimed_job_id=45`(与自身 job 一致,无窃取) |
| H 采集中途浏览器崩溃 | 见第五节(九轮) | job 47 暴露真实缺陷;修复后 job 53 真实闭环:全损→重排→重认领同一 job→done(attempts 2) |
| I housekeep + 磁盘预算 | 通过 | exit 0;`07_tmp` 1,733,550 B 前后不变;pw-artifacts 3 个目录不变;项目总量 1.094 GB < 预算 20 GB |

Strata 纪律:`strata_unchanged: true`,`model_jobs_delta_all_scenarios: 0` —— 全部验收场景未触碰 Strata(`model_jobs` 始终 70)。

## 四、验收暴露并确认的真实缺陷(含根因)

1. **toutiao 全损被吞成"成功"(已修复)。** job 47(2026-11-13 02:12,采集中途强杀 9 个 msedge 进程):列表已抓到 `listing_links:10`,10 篇文章页全部抓取失败 `article_errors:10`,产出 0 条,任务却 `ok:true / status:done / inserted:0`。根因:`runner.py` 中 toutiao 适配器吞掉逐篇错误,`_execute_job` 在 `insert_items` 后无条件 `summary["ok"] = True`(修复前第 228 行)。崩溃永远进不了重试/租约回收通道。修复:全损(0 产出且 errors≥links)raise → `fail_crawl_job` transient 通道;部分失败仍完成但 WARN。回归测试 2 条(`test_p12_crawler_hardening.py`),并有真实运行证据(第五节第九轮,job 53:同类 8/8 全损走重试通道,最终 done/attempts 2)。
2. **`run_next` 认领最低 id 排队任务,跨场景互相窃取(已确认,未改动代码)。** 早前多轮 G/H 证据中 `rerun_claimed_job_id ≠ 自身 job_id`(requeue 复活旧 job 41/43 被认领)。这是认领策略的固有行为:租约协议保证互斥,但不保证"认领自己刚入队的任务"。验收 harness 已按此加固:任何认领证据必须与自身 `job_id` 一致才采信;每个入队场景先断言队列为空。生产含义:同队列混跑多来源时,恢复顺序按入队 id 而非场景 —— 记录为已知限制。
3. **sspai 软 404。** 死链返回 `status:200`(soft-404),终态判定无法拦截;真实 404 由 ifanr 死链验证(`HTTP 404(终态,不重试)`)。已如实记录,未假装 sspai 通过终态路径。
4. **FTS 索引滞后真实存在且可复现,并随验收采集持续扩大。** technology 文章 137 vs `articles_fts` 62;finance 50 vs 47。`crawl` 后不会自动 `analyze`。任何报告不得把 FTS 行数当管道健康度。
5. **浏览器进程识别修正(验收工具自身缺陷)。** 采集浏览器是 **Edge**(`config.browser.executable_path` 指向 msedge.exe),旧 kill 过滤器(chrome|chromium|…)永远匹配不到。修正后过滤器:`Name -match 'chrome|chromium|firefox|camoufox|headless|msedge'` 且 `CommandLine -match '02_crawler'`(用户自己的 Edge user-data-dir 不含 02_crawler,已验证不误伤)。

## 五、场景 H 九轮真实过程(不修饰)

1. 第一轮:外部轮询强杀 —— 进程名过滤器不含 msedge,`killed_browser_count:0`(证据保留为 `killed_browser_pid:null`,结论不采信)。
2. 第二轮:过滤器修正后,外部扫描太慢,杀掉的是**采集完成后收尾中的 2 个进程**;任务 done 属于赛跑结果,不能证明崩溃恢复 → 判定场景设计有缺陷,重设计。
3. 第三轮(重设计:kill 线程内置 worker 内部,25s 定时,确定性落在采集中途):杀掉完整浏览器进程树 9 个 pid(`kill_pids:[8272,14292,15740,9428,12300,28476,9124,7844,19340]`,`browser_kill_at 02:12:53`)——由此暴露第四节缺陷 #1(job 47:`listing_links:10, article_errors:10, inserted:0` 却 `ok:true/done`)。
4. 第四轮(job 48,修复后):杀 11 个 msedge pid(02:17:42),列表 8 条链接,2 篇在杀前完成,`article_errors:6/8` → **部分失败按设计如实完成**:`done`,`inserted:2`,错误计数进 meta 并 WARN,无粉饰、无数据丢失。
5. 第五轮(job 49,修复后):杀 9 个 msedge pid(02:23:25),`article_errors:5/7`,同样部分完成 `inserted:2`。全损与部分失败两种分支自此都有真实运行证据。
6. 第六轮(job 50,detect-then-kill 重设计:探测到 02_crawler 浏览器后再等 3s 强杀,02:26:24 杀 8 个 pid,命中列表抓取中段):真实完整闭环 ——
   `worker_summary.ok:false`,error `FetchError: fetch failed after 3 attempts: TargetClosedError: BrowserContext.new_page: Target page, context or browser has been closed`,`requeued:true`;
   `status_after_crash:"queued"`(attempts 1,last_error 如实入库)→ recoverer `run_next` 认领**同一 job 50** → `ok:true, pages_fetched:8, inserted:7` → `final_status:"done"`,`final_attempts:2`,`inserted:7/dup:0`。
   这与 Scrapling 0.5.1 **无 auto_restart** 的核验一致:会话层不自愈,恢复发生在 ttxt 任务层(新会话重跑)。
7. 第七轮(job 51,post_kill_s=14 定时):杀 9 个 pid(02:29:08),列表 5 条,1 篇在杀前完成,`article_errors:4/5` → 又一次**部分失败如实完成**(`inserted:1`)。定时方案落文章阶段不稳定,遂改确定性方案。
8. 第八轮(job 52,deterministic:worker 内挂 `logging.Handler` 捕获"列表页 200(channel=tech)"后再等 3s 强杀):**本轮新修分支在真实运行中被触发** ——
   `last_error_after_crash:"RuntimeError: toutiao 全部文章页抓取失败(8/8),疑似浏览器会话崩溃,走可重试通道"`,`status_after_crash:"queued"`(attempts 1)→ recoverer 认领**同一 job 52** → `ok:true, pages_fetched:8` → `final_status:"done"`,`final_attempts:2`,`inserted:6/dup:1`。
   缺陷 #1 的修复自此拥有真实运行证据(修复前 job 47 同样 8/8 全损却假 `done`;修复后走重试通道并最终成功)。
   注:本轮外层 harness 用 locale(gbk)解码 worker 输出,遇中文错误文本抛 `UnicodeDecodeError`,导致该轮 `kill_pids` 证据行丢失(链路与 DB 证据完整);已修复(Popen 钉 `utf-8` + 子进程 `PYTHONIOENCODING=utf-8`)。
9. 第九轮(job 53,编码修复后复跑确定性方案,完整证据):`kill_listing_was_done:true`,02:37:21 杀 9 个 pid;`worker_summary`:`ok:false`,`listing_links:8`,`article_errors:8`,`items_extracted:0`,`error:"RuntimeError: toutiao 全部文章页抓取失败(8/8),疑似浏览器会话崩溃,走可重试通道"`,`requeued:true`,`elapsed_s:76.44` → `status_after_crash:"queued"`(attempts 1)→ recoverer 认领**同一 job 53** `ok:true, pages_fetched:8` → `final_status:"done"`,`final_attempts:2`,`inserted:7/dup:0`。
   **H 场景闭环链至此完整:全损(修复分支被真实触发)→ 重排 → 重认领 → 成功完成,且 kill 证据无缺失。** 各轮独立 job id 均记录于 JSON(job 47-53 全部保留可查,第 1、2 轮为设计缺陷轮,证据同样保留)。

## 六、诚实性披露(全部为验收方法本身的手段/限制)

- 场景 C 指标为合成夹具,仅测门禁;真实校准结论需 ≥30 行真实头条导出,当前样本 0。
- G/H 的"租约过期"通过 SQL 把 `lease_expires_at` 拨到过去(`strftime(...,'-1 seconds')`)加速时钟,未等待真实 30 分钟。
- 进程强杀按 `CommandLine` 关键字匹配,非进程树拓扑证明;已验证不匹配用户浏览器命令行,但该方法只对本项目进程安全。
- 场景 H 的 kill 内置于 worker(自杀),这是为消除外部赛跑;若 worker 与 kill 线程同进程崩溃,证据仅依赖 stdout 协议行。
- `07_tmp/` 下保留本轮全部诊断探针(`p12b5_browser_probe*.py`、`p12b5_dbstats.py`、`probe*.err`),未删除、未入库,供复核。
- 验收 harness 自身缺陷也已披露:第八轮外层以 locale(gbk)解码 worker 输出遇中文错误抛 `UnicodeDecodeError`,该轮 `kill_pids` 证据行丢失(DB/链路证据完整);修复为 Popen `encoding=utf-8` + 子进程 `PYTHONIOENCODING=utf-8` 后第九轮复跑补齐。
- **不存在 pre-batch-5 备份**;批次 5 改动开始前的最近备份是 `11_backups/P12-batch4-20261113-011253.zip`(即回滚点)。本批完成后立即补建 `P12-batch5` 备份。

## 七、测试与运行事实

- 全量回归(批次 5 全部改动完成后复跑):`10_tests` **161 passed in 26.75s**(含本批新增 5+2 条)。
- `ttxt doctor`:**10 ok, 0 failed**(Strata 只读健康:served_model=qwen3.8-flash-next-iq3_s,loaded=True;disk.used=1.215GB/20GB)。
- 验收脚本各轮 exit 0;每轮 JSON 合并保留(见 `previous_generated_at` 链)。
- 类目库计数(最终验收结束时,2026-11-13 02:41):technology 137 篇 / fts 62;finance 50 / 47;health 25 / 25;demo 45 / 45(FTS 滞后见缺陷 #4,未粉饰)。
- `crawl_jobs` 终态分布(验收结束时):done 46 / aborted 7,**无遗留 queued/running 任务**;H 各轮 job 42-53 全部 done,其中 50/52/53 `attempts=2`(崩溃恢复的直接 DB 佐证)。
- 面板(127.0.0.1:8765)全程未动;Strata(127.0.0.1:8080)全程未动、未重启、未改参数,`model_jobs_delta=0`。

## 八、备份与回滚

- 回滚点(批次 5 改动前已有):`D:\ttxt\11_backups\P12-batch4-20261113-011253.zip`
- 批次 5 完成后已新建:`D:\ttxt\11_backups\P12-batch5-20261113-024308.zip`(2.07 MB,含 SQLite `.backup` + 源码 zip)。本次创建未删除任何既有备份,现存 7 份 = E2E、P10-pre、P10-final、P11-final、P12-pre、P12-batch4、P12-batch5;更早的 P9-final 包此前已按 keep=7 策略滚动删除(如实记录,下次新建备份将开始删除 E2E 包)。
- 原始资产、类目库、控制库、被驳回草稿:本批未删除任何文件(FTS/DB 只追加或状态列变更)。
