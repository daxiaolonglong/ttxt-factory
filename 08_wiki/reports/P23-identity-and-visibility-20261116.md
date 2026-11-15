# P23 报告:身份判定收口 + 被拒插入可见 + 磁盘口径闭合(第 6 轮,2026-11-15 22:4x → 2026-11-16 03:4x)

> 本轮起因:用户自己写的日检 `08_wiki/reports/quality-20261115.md`(只读账本,不改)指出三件事:
> ①注册类目出现 3 次「身份塌陷」告警但来源不明;②体检只普查已存行,**没有统计被拒插入**,是检测盲区;
> ③预算表报 1.301 GB 而根目录实测 6.60 GB,差额全在没登记的目录里。三件都在本轮修完。

## 一、根因取证(先证明问题真实存在,再改代码)

| 步骤 | 读数 | 凭据 |
|---|---|---|
| 日志定位 | `09_logs/ttxt-20261115.jsonl` 第 59–63 行:15:53:09 三条 `insert.url_identity_conflict`,`social / chinanews_society`,URL `…/sh/2026/11-14/10732158｜10732151｜10732142.shtml` | 原日志 |
| 对到库行 | social 库 id **13 / 16 / 20**:`completeness='full'`,正文 **708 / 846 / 684 字**,标题逐字相同,无重复标题组、无重复 content_hash | `probe_conflict_out.txt` |
| 只读复现 | 21:23:36–21:23:40 重新抓这三页:`div.left_zw` 又取到 **708 / 846 / 684 字**(长度一致、字节不同);换 `div.content_maincontent_content` 每篇多 14 字 ⇒ **content_hash 跨轮抖动** | `probe_item_pages_out.txt` |
| 任务账本 | job 66:`items_extracted 11, inserted 2, duplicates 9, item_pages_fetched 20, items_enriched 20, item_page_errors 0, keyword_filtered_out 19, url_identity_conflict 3, elapsed 33.31 s` ⇒ 抓的确实是**文章页正文**,不是摘要退化;job 67 `bodies_upgraded 8`(P20 升级通道已在跑) | `probe_job_meta_out.txt` |

结论:这是**同一篇文章被重抓时的哈希抖动**,不是"同一 URL 上是另一篇文章"。
P20 只放行了"正文更长 ⇒ 就地升级",管不到"长度相同/更短"的反向情形 ⇒ 注册类目每天喊假警报,
真塌陷反而被噪声埋掉,而且与 C-7「正文最长者胜」的保留判据互相矛盾。

## 二、代码改动(改的是判定,不是数据)

| 文件 | 改动 |
|---|---|
| `ttxt/crawler/store.py` | 新增分支:同一 URL + **同一标题** + 正文**不比已存长** ⇒ **保留原行**,计数 `bodies_kept_existing`,`LOG.info("insert.body_kept_existing")`(带 `article_id/old_chars/new_chars`),仍计入 `duplicates`;`url_identity_conflict` 只留给**标题不同**(或本批内同 URL 两次正文不同、库里还没有行可比,日志加 `in_batch_clash`);docstring 记录 job 66 与 21:23 复现的取证编号 |
| `ttxt/config.py` | 新增 `SCRATCH_DIR = 07_tmp`、`ASSET_DIR = 资产库`(只测量,不自动删);**没有**把 `TEMP_DIR` 改指 `07_tmp` —— 那会让 `housekeep --clean` 的 TTL 删掉往轮取证件 |
| `ttxt/housekeep.py` | `MAJOR_PATHS` 补 `scratch(07_tmp)`、`assets(资产库)`、`.git`、`.secrets`;`report()` 按根目录逐子项核算,新增 `root_total_bytes/root_total_gb/within_root_budget/unaccounted_bytes/unaccounted_dirs/root_scan_error`,`caps` 加 `scratch_max_gb`,`caps_status` 加 `scratch_bytes/scratch_within` 并补 `temp_bytes/temp_within`;老键一个没删(面板与 `scripts/e2e.py` 依赖) |
| `ttxt/doctor.py` | 新增 `insert_judgement_rows(window_hours=24)`:扫结构化日志统计五类判定事件,塌陷非零即 WARN 并列出集中的「类目/来源」;`run_doctor` 里加 `disk.root_budget`、`disk.scratch`(超限额只告警)、`disk.accounting`(未登记 >0.25 GB 才 WARN),并 `try/except` 兜住判定统计 |
| `00_control/config.json` | `paths` 加 `"scratch": "07_tmp"`、`"assets": "资产库"`;`disk` 加 `"scratch_max_gb": 2` |

## 三、口径变更与既有测试同步(必须交代清楚)

`10_tests/test_c2_url_dedupe.py` 三处期望由 `url_identity_conflict` 改为 `bodies_kept_existing`
(`test_third_crawl_still_refused`、`test_url_identity_is_scoped_to_the_category_database`、
`test_tracking_param_variants_count_as_same_url`)。这三处用例构造的都是**同标题 + 等长但字节不同**的正文,
正是本轮证明属于"同一篇重抓"的那种形状;用例真正要证明的核心断言(**仍然拒绝插入、仍然只有一行**、
URL 规范化仍生效)一字未松,反而补了一条更强的断言:`等长重抓不得改写正文`。
其余同 URL 冲突用例(标题不同、本批内冲突、来源页 URL 塌陷、跨类目隔离)不受影响,已逐条核对。

## 四、红 → 绿凭据(先红后绿,不写完再补测试)

| 阶段 | 读数 |
|---|---|
| 改动 stash 后跑新测试(必须失败) | **9 failed / 2 passed**,报 `KeyError: 'scratch_bytes'`、`AttributeError: module 'ttxt.config' has no attribute 'SCRATCH_DIR'`、体检 stdout 仍 `summary: 29 ok, 0 failed` | `07_tmp/r26_identity_conflict/p23_red.txt` |
| 改动生效 | **11 passed / 7.19 s** | `p23_green2.txt` |
| 全量回归 | **406 passed,113.60 s,exit 0**(上轮基线 395 ⇒ +11;conftest 的生产库字节不变校验、basetemp 只留 4 份都通过) | `p23_full_suite.txt` |

新增测试文件 `10_tests/test_p23_identity_and_visibility.py`(11 项):等长抖动保留原行(行字节不变)、
更短正文保留原行、完全相同重抓仍是普通 duplicate、不同标题仍算塌陷、`insert.body_kept_existing` 日志事件带字段、
`insert_judgement_rows` 计数 + 来源聚合 + 24h 窗口过滤 + 坏行跳过、未登记大目录被列出、
scratch 桶按自己的限额计量、`config.SCRATCH_DIR` 指向真实暂存根、`run_doctor` 打印新检查项。

## 五、Worker 上线与实采复验(写库前先备份)

1. **备份**:`11_backups/P23-pre-worker-20261116-020806.zip`(**4,878,116 字节**)——
   用 `sqlite3.Connection.backup()` 取 system + 6 类目库的一致性副本(纯复制文件会丢 WAL 内容),再打包 config / registry / sources。
2. **受限 Worker**:`ttxt worker --owner r26-p23-worker --max-jobs 2 --max-runtime-s 900 --idle-stop-s 20`,exit 0,
   `worker.stop reason=max_jobs done=2`:job **54** `toutiao_tech/technology` = extracted 10 / inserted 9 / duplicates 1;
   job **63** `who_cn_news/health` = extracted 25 / inserted 0 / duplicates 25(WHO 语料自 11-12 未变,字节完全一致 ⇒ 普通 duplicate)。
   队列 **queued 2 → 0**,done 60 → 63;`worker_stop` 标志不存在;`00_control/worker.lock` 里的历史 PID 13652 是死进程,操作系统级锁本来就空着。
3. **实采复验**(正是出问题的来源):`ttxt crawl --source chinanews_society --category social`,exit 0:
   feed 30 条 ⇒ 抽 20、文章页 20 页 `item_page_errors 0`、inserted **11**、duplicates 9、
   **`url_identity_conflict` 计数为 0**(同一来源同一批次昨天报了 3 次),耗时 33.34 s(job 70 已入账)。
4. **数据未被改写**:social id 13/16/20 的 `content_hash`、`fetched_at = 2026-11-15T15:47:53`、`processor_version = p2-v1` 一字未动;
   同类目 `GROUP BY title HAVING n>1` 组数 **0**;social 22 → **33** 行,`full` 20 → 30,`short` 2 → 3。
5. 语料总量 **354 → 374 行**(technology 168 / finance 96 / health 25 / social 33 / history 20 / demo 32)。

## 六、磁盘口径闭合(体检读数)

| 项 | 修前(日检实测) | 修后(本轮体检) |
|---|---|---|
| 预算口径 | `housekeep` 报 1.301 GB,根目录实测 6.60 GB,**差 5.3 GB 无人可见** | `disk.root_budget: root_used=6.293GB budget=20.0GB (bucketed=6.292GB)`;`disk.accounting: 未登记=0.001GB` |
| 暂存限额 | `paths.temp`/`TEMP_DIR` 指向几乎为空的 `99_temp`(3.5 MB),真正的 `07_tmp`(0.753 GB,当日 +333 MB)从未计量 | `disk.scratch: 07_tmp=0.548GB cap=2GB;99_temp=0.003GB cap=2GB`(超限只 WARN,**删不删由人决定**) |
| 体检项数 | 29 项 / 0 失败 / 4 WARN | **33 项 / 0 失败 / 5 WARN**(`disk.root_budget`、`disk.scratch`、`disk.accounting`、`crawler.insert_judgements` 新增) |
| 判定统计 | 库里"没有"与"被拒"是同一个数,检测盲区 | `crawler.insert_judgements` WARN:近 24h 身份塌陷 3、来源页塌陷 0、正文升级 16、保留原行 0、无逐条链接 0;集中在 `social/chinanews_society×3`;整改方向写明「修抽取器(配 `link_selector` / 正文选择器),不是删数据」 |

`housekeep.report()` 现在要扫 `资产库`(4.544 GB)、`.venv`、`.git`,一次 **6.8 s**(以前 1 个数量级更快);
面板每次请求调一次,为了诚实账本接受;新单测把 `MAJOR_PATHS` 打桩到临时目录,不为此变慢。

## 七、诚实说明(不把没发生的写成发生的)

1. 今天 02:47 的 chinanews 复采**没有出现哈希抖动**(9 条 duplicate 全是字节完全一致),
   所以生产日志里 `insert.body_kept_existing` 目前**还没有真实样本**,`保留原行 0 条`。
   本轮修复由「21:23 复现取证 + 11 项单测」证明,不是由「今天没复发」证明(规则 42(7):结论只覆盖已探明范围)。
2. 体检 24h 窗口仍显示 3 条身份塌陷:11-15 15:53 那三条是历史事实,窗口滑过(11-16 15:53 之后)才归零。
3. `manual ttxt crawl` 与队列路径不完全一致:job 70 没有 `keyword_filtered_out`(关键词闸门只在带 plan 的队列任务里跑)——
   已记入待办,本轮不动。
4. `articles.text_length / cleaned_content / clean_version` 归清洗阶段所有(`pipeline.py:29-31` 全量重算),
   正文升级后这几列短暂描述旧正文,由采集入口当场跑的清洗管线补回(规则 31);体检 `db.cleaned_coverage`、
   `db.completeness` 会核对,不一致当场可见。

## 八、文档与规则同步

- `07_rules/CORE_RULES.md`:新增**规则 43**(身份判定收口 / 被拒插入必须计数 / 磁盘口径必须闭合且只告警不自动删)。
- `08_wiki/db_schema.md` §2:按实测校正版本口径 —— 文档长期写 `p10-v1`,代码与 6 个生产库都是 **`p12-v1`**;
  并写清 `schema_version`(库)与 `processor_version`(行)的分工、正文升级改哪些列、保留哪些列。
- `README.md`:目录表补 `07_tmp/`(暂存/取证件)与 `资产库/`(4.544 GB,`.gitignore` 排除),并写清磁盘口径原则。
- `08_wiki/reports/quality-20261115.md`:用户自己写的日检,**原样入库**(不改写别人的账本)。
- `08_wiki/reports/P17-session-handoff-20261115.md`:新增 §十四(本轮完成账本 + 第 7 轮待办)。

## 九、第 7 轮待办(优先级从上到下)

1. **指标回流扩到其余类目**:technology 已 `measured=32` 标定过;继续 `ttxt metrics --category … --fetch-public` 刷剩余 127 篇,
   finance / health / social / history 各攒 ≥30 measured 样本后 `--calibrate`(现在这 4 个类目 100% `missing_data`)。
2. **`prediction_records` 仍是 0**:technology 已满足 `prediction.min_samples=5`,跑基线预测 → 实测回填 → 准确率报告。
3. **R2-5 `ttxt crawl-enrich --category`**:health 25 行全是 `short`(avg **119.4 字**),是语料质量最短的一块。
4. **调度器**:`ttxt schedule --once` 单轮上线(`scheduler_state.json` 冻结在 11-12 19:57,`crawl_enqueue_4h` 已错过约 17 轮);
   Worker 常驻需用户批准(长期占网络与浏览器 profile)。
5. C-5 可信度分档;9 条 `evidence_links.article_id` 为空(需 DDL 批准);`_element_text` 标题缺陷 + `PROCESSOR_VERSION` bump。
6. 磁盘:`07_tmp` 已 0.548 GB(限额 2 GB),**清理必须先出清单交用户批准**,不许自动删(规则 29/43)。

## 十、凭据位置

`07_tmp/r26_identity_conflict/`:`probe_conflict.py/_out.txt`、`probe_item_pages.py/_out.txt`、`probe_job_meta.py/_out.txt`、
`p23_red.txt`、`p23_green.txt`、`p23_green2.txt`、`p23_full_suite.txt`、
`pre_p23_backup_and_state.py`、`pre_worker_state-20261116-020806.json`、`pre_worker_receipt.txt`、
`worker_run.txt`、`recrawl_social.txt`、`post_p23_state.py`、`post_worker_state.json`、`post_worker_receipt.txt`、
`doctor_after_p23.txt`、`doctor_after_worker.txt`(scratch 只测量,删除需先出清单并获批准)。
