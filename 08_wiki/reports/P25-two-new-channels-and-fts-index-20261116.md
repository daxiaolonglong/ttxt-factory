# P25:新增头条频道排干队列 + FTS 索引根因 + 旧资产取证(2026-11-16 12:4x → 13:3x)

本轮承接 P24 待办第 1 项(注册 `toutiao_finance` / `toutiao_history` 并把闭环复制过去)与第 6 项
(入队归因不可证)。中途用 `ttxt doctor` 新加的体检项抓到一处真实缺陷(检索索引滞后),按规则 36
就地修根因。**全部写入仅在 `D:\ttxt`**;写库前先备份(见第二节)。

## 一、先把需求复述成本轮验收口径

用户目标原文:**爬 → 洗 → 去重 → 看真实互动 → 学什么会爆 → 把关键词回喂给爬虫 → 写出爆款文 → 一轮比一轮聪明**。

| 本轮要动的环节 | "怎样算做到了"的口径(规则 26) | 实测结论 |
|---|---|---|
| 爬 | 新频道注册前必须有"生产适配器两级取证件";队列里排队任务全部由有界 Worker 消费到 `queued=0`(本轮新增归因除外) | 做到了:71/72/73 三个任务 `done`,队尾新增 1 个是 analyze 反哺自动入队(job 75,可归因) |
| 看真实互动 | 新类目 `article_metrics` 从 0 变成有实测行,且逐篇 render 计数核对过文章 id | 做到了:finance 0 → 10、history 0 → 10,`render_mismatch=0`、`render_unattributed=0` |
| 学什么会爆 | 实测样本 ≥ `MIN_CALIBRATION_SAMPLE=30` 才许标定,不足必须如实拒绝 | 未做到(如实报告):finance/history 各 10 条 < 30 ⇒ 仍是 `default_thresholds`;technology 样本 85 → **101** 重新标定 |
| 语料即时可用 | 采集任务跑完,文章当场可 `ttxt search`;`doctor.db.fts_coverage` PASS | 修根因后做到:403/403 行全部进索引 |
| 一轮比一轮聪明 | 每步有凭据:入队要能说是谁触发的 | 做到:`crawl.enqueued` 带 `actor`(dashboard / scheduler / keyword_plan / manual) |

## 二、写库前备份(回滚位置)

- `11_backups/p25-pre-20261116-125627.zip`,**6,578,403 字节**(6.3 MB),日志事件 `backup.created`;
  内容 = `00_control/system.db` + 6 个类目库的 `Connection.backup()` 一致性副本 + 代码/面板/角色基线。
- 回滚方法照旧:`operations.md` 的恢复段(解包 → 覆盖 `00_control/*.db` 与 `01_categories/*/articles.db`)。
- 保留份数由 `00_control/config.json → disk.backup_keep = 30` 决定;本轮 4 个 zip 全部保留。

## 三、注册两个新头条频道,并让有界 Worker 排干队列

**先取证再注册**(P24 第六节的证件沿用):`02_crawler/sources.json` 新增 `toutiao_finance`(slug `finance`)
与 `toutiao_history`(slug `history`),`render: browser`、`extractor: toutiao`、`rate_limit_s: 2.0`,
`max_articles: 10`;取证过程写进该条目的 `notes`(静态 200 且落地 `channel=finance/history` + 生产适配器
pages 4 / 逐篇链接 17 / 正文 1450–2985 字 / 当天发布 / robots 放行),`updated_at: 2026-11-16`。

入队(规则:面板与调度是常驻写入口,人工入队必须留痕):

| 任务 | 来源/类目 | 谁触发 | 证据 |
|---|---|---|---|
| 71 | toutiao_tech / technology | 上一轮面板(当时**不可证**,见第七节) | `crawl_jobs` + `crawl.done` 02:26:00 |
| 72 | toutiao_finance / finance | `actor=manual`(`crawl.enqueued` 12:58:04) | `09_logs/ttxt-20261116.jsonl` |
| 73 | toutiao_history / history | `actor=manual`(同上,同一秒) | 同上 |

**为什么不按原计划各入队 3 次**(改判依据,规则 37/13):`toutiao.collect` 只有 1 个频道列表页 + 最多
`max_articles` 篇文章页(`ttxt/crawler/toutiao.py:120-139`),连开 3 个任务等于把同一个频道页和同一批 17 条链接
抓三遍,第 2、3 个任务几乎全落在去重上 —— 白烧限速预算和浏览器 profile 时间。改成各 1 次,先看真实新增行数。

Worker 回执(`ttxt worker --owner p25 --max-jobs 3 --idle-stop-s 45 --lease-s 1800`,`worker.stop reason=max_jobs done=3`):

| 任务 | pages_fetched | listing_links | extracted | inserted | duplicates | elapsed_s | 管线 |
|---|---|---|---|---|---|---|---|
| 71 technology | 11 | 17 | 10 | 9 | 1 | — | cleaned 177 / clusters 177 / near_dup 0 |
| 72 finance | 11 | 17 | 10 | 10 | 0 | 58.69 | cleaned 106 / clusters 106 / near_dup 0 |
| 73 history | 11 | 17 | 10 | 10 | 0 | 56.42 | cleaned 30 / clusters 30 / near_dup 0 |

库内读数(前后对比,`07_tmp/r29_channels/post_p25_state.json`):technology 168 → **177**、finance 96 → **106**、
history 20 → **30**;全库 **374 → 403 行**。`doctor.db.duplicate_rows` PASS(命名空间内每 URL 一行,类目之间无交集)。

## 四、新类目指标回流:10 条实测,标定门槛仍不达标(如实拒绝)

| 类目 | candidates | fetched | robots_refused | fetch_errors | no_metrics | render_ok / mismatch / unattributed | 实测样本 | 标定 |
|---|---|---|---|---|---|---|---|---|
| finance(13:03:42,`--apply`) | 10 | 10 | 0 | 0 | 0 | 10 / 0 / 0 | 0 → **10** | 拒绝:`sample 0 → 10 < 30`,仍 `default_thresholds` |
| history(13:04:24,`--apply`) | 10 | 10 | 0 | 0 | 0 | 10 / 0 / 0 | 0 → **10** | 同上 |
| technology(13:07:53,`--apply`) | 20 | 16 | 0 | **4** | 0 | 16 / 0 / 0 | 85 → **101** | 重标定 |

- 日志凭据:`metrics.public.summary` 三条(13:03:42 / 13:04:24 / 13:07:53);
  `finance`/`history` 的 `candidates` 只有 10 而不是 20 —— 候选筛选只认 `host_allowed(url)`,
  这两个类目里非头条主机(eastmoney/tmtpost/chinanews)的行一律 `host_skipped`,不猜指标。
- 标签分层:finance `measured 10`(verified_viral 1 / normal 4 / low_quality 5 / unscored 96);
  history `measured 10`(normal 6 / potential 1 / low_quality 3 / unscored 20)。
- technology 重标定(`ttxt metrics --category technology --calibrate`):阈值 **0.0562 / 0.0272 / 0.0115**,
  样本 **101**,方法仍是 measured 分数的 p90/p60/p25,`calibrated_at 2026-11-16T13:09:10+08:00`
  (存在 `articles_meta.viral_calibration`,上一版是 0.0551/0.0261/0.0123 样本 85)。
  标签随之重算:`verified_viral` 9 → **10**、potential 30、normal 35、low_quality 26、unscored 76。
- `favorites` 仍为 NULL(全类目 0 行):中文"收藏"标签与 `repinCount` 的配对取证仍未做成,不猜(规则 15)。
- **`fetch_errors` 4 条(404)没查明根因**:两次 technology 批次都是 4/20 同一批 URL 返回 404,
  而同一 URL 早前能抓到 `RENDER_DATA`。本轮只记录现象与 URL(见 `09_logs` 里 `metrics.public.fetch_error`),
  列为第 9 轮待办。

## 五、预测闭环扩到 finance / history

- `ttxt predict predict --category finance --limit 20` → **recorded 20 / already 0 / skipped_no_baseline 0**,
  基线桶 `number 9 / question 1 / all 10`;history → **recorded 20**,基线桶 `plain 5 / question 5 / all 10`。
- `ttxt predict settle --category technology` → **settled 3 / pending 21**(本轮实测回填的 16 篇里 3 篇此前有预测)。
- `ttxt predict report --category technology` → **settled_samples 19 / MAE 0.018 / mean_bias −0.0127 / asset_written true**
  (P24 是 16 样本 MAE 0.0168;基线仍是"同类目同标题形态桶均值",不是模型)。
- finance/history 各 20 条预测全部 `pending`(实测回填要等下一轮 `--fetch-public` 覆盖到这些 URL)。

## 六、根因(修):`articles_fts` 只在 analyze 里建 ⇒ 当天采回来的文章搜不到

**现象**:`ttxt doctor` 新加的 `db.fts_coverage` 第一次跑就报 **374 行只有 354 行进索引**(social 22/33、technology 159/168)。

**根因**:`articles_fts` 只在 `ttxt analyze → rag.rebuild_index`(`ttxt/analysis/rag.py:22-42`)里写;
采集任务只跑清洗管线 `pipeline.run_pipeline`(`ttxt/crawler/pipeline.py`),那里做清洗、时间归一化、simhash 聚类,
**没有任何一步写 FTS**。所以"采完当天搜不到新文章、写作取材也取不到"不是偶发,是设计缺口。

**修法**(规则 36:根因完整,不打补丁):`pipeline.run_pipeline` 末尾调用 `rag.rebuild_index(category, db_path=path)`,
并把 `indexed` 计数放进管线回执(采集任务回执、Worker 输出、面板管线日志都能看到)。
依赖方向是单向的 —— `analysis.rag` 只依赖 `crawler.simhash` 的分词,不回头依赖采集层,所以用函数内导入避免模块级循环导入。
`analyze` 仍然重建索引(它是每日调度里的兜底),但不再是唯一入口。

**红→绿**(规则 24):新测试 `10_tests/test_p25_fts_after_crawl.py` 三项,改前 **3 failed**
(`assert (1, 0) == (1, 1)` —— 一行都没进索引;`KeyError: 'indexed'`;搜索命中 0 条),
改后与既有清洗/覆盖率/完整性/观测测试同跑 **46 passed / 22.43 s**(`07_tmp/r29_channels/p25_fts_red.txt`、`p25_fts_green.txt`)。

**生产复核**:analyze 之后 `doctor.db.fts_coverage` = **403/403 PASS**;`db.cleaned_coverage` 同步 403/403。

**顺带量到一块必须在下轮处理的石头**(实测 + 外推,规则 15 标注):
在 07_tmp 的 technology 一致性副本上实测 `rebuild_index` **177 行 / 317,017 字 / 0.60 秒 = 295 行每秒、52.9 万字每秒**;
按旧资产 53,678 行、正文均值 2,208 字(≈1.185 亿字)**外推**每任务 **3.0–3.7 分钟**。
`run_pipeline` 本来就是每任务全表重算(清洗 + 聚类 + 索引),403 行时无所谓,5 万行时就撑不住
⇒ 第 9 轮待办 2(增量化),验收口径写死在待办里。凭据:`07_tmp/r29_channels/fts_cost_out.txt`。

## 七、根因(修):入队无审计,归因只能靠"时间挨得近"

**现象**:P24 报告写"job 70/71 疑似面板触发",依据只是 `demo-feedback-*.json`(08:28:43)与 job 71(08:28:54)
相隔 11 秒 —— 这是**推断不是证据**。

**修法**:`store.create_crawl_job` 现在打 `crawl.enqueued` 日志,带 `job_id / source / category / actor / db / keywords`;
`actor` 取 meta 里的 `dispatched_via`(缺了就退到 `reason`,再缺就是 `manual`),**生产实测的取值是五个**:
`dashboard`(`ttxt/dashboard/api.py:291`)、`scheduler`(`ttxt/scheduler.py:103`)、`keyword_plan`(`ttxt/crawler/keyword_crawl.py:218`)、
`keyword_feedback`(`ttxt/analysis/keywords.py:163`,analyze 关键词反哺)、`manual`(CLI / 脚本)。生产验证:job 72/73 两条 `actor: "manual"`
(本轮脚本入队,见 `07_tmp/r29_channels/enqueue_p25_new_channels.py`)。
今后面板点一下就能在日志里对上号,不用再靠时间猜。

**顺带**:analyze 的关键词反哺本轮自动入队 **job 75**(health,gap 关键词)—— 这就是"排队非空"的唯一来源,
日志里可归因;不是残留。

## 八、新增体检项 `doctor.db.fts_coverage`(第六节就是它抓出来的)

`ttxt/doctor.py` +47 行:每类目对比 `COUNT(articles)` 与 `COUNT(articles_fts)`,不一致 **FAIL** 并列出缺口的
`MIN(id)/MAX(id)` 区间;全类目一致 → PASS 并报总行数。它属于"写路径与索引是否同步"这一类对账,
和 `db.cleaned_coverage`、`db.duplicate_rows` 同一族。**教训**(写进规则 47):加一条写路径就必须同时加一条
"这条路径写完,下游派生物是否齐全"的体检,否则缺陷要等人肉搜索才发现。

## 九、旧资产取证:P26 迁移是最高价值的一步(本轮只读,没动一个字节)

规则 34(先看仓库文件再下结论):本轮把 `资产库/` 只读翻了一遍,探针与报告在
`07_tmp/r29_legacy_asset_probe/`(`probe_legacy_db.py`、`probe_legacy_quality.py`、`probe_legacy_shape.py`
与三份 `.txt` 报告)。**没有写入、没有删除、没有导入。**

`资产库/01_语料库/factory.db`(1,668 MB)实测:

- **53,678 篇**文章,`COUNT(DISTINCT url) = 53,678`(URL 全唯一),且 **全部**是
  `https://www.toutiao.com/article/<数字>/` 形态 —— 与新系统 `toutiao.canonical_article_url` 的身份形态一致,
  既能直接去重比对,也还能回访再取指标。
- 真实公开计数来自 `search_ssr`:`metrics_source = {"read":"search_ssr","interaction":"search_ssr","followers":"info_api"}`
  共 53,289 行(389 行 NULL);`read_count > 0` **19,970 行**(33,708 行为 0 ⇒ **必须当缺失,不能当 0**),
  最大 587,996、均值 406.3;`digg>0` 18,505、`comment>0` 8,716、`collect>0` 20,133。
- 新鲜度:51,651 篇发布在 2026 年(2026-08 8,155 / 09 18,322 / 10 18,141 / 11 2,357),最新 2026-11-06。
- 正文:`article_bodies` 190,555 行、平均 2,208 字、最长 8,000 字(**8,000 是旧采集的截断上限,不是原文长度**);
  与 articles 按 gid 关联 **53,678 全命中**;正文 ≥400 字且有阅读数 **33,412 行**;有阅读且有赞 **17,313 行**。
- 关键词:`keywords` 表 **28,171** 条,`ACTIVE 16,796 / DEAD 6,186 / SLEEP 4,871 / WEAK 185 / NEW 133`;
  28,038 条 `success_count>0`;3,881 条有 hot/elite 记录;文章上挂了 10,559 个不同关键词。
  词面质量混杂(例:`闷声赚钱获客` score 95.3 与 `AI脱衣案例` 92.6 并列),**必须过一遍人工/规则筛**再喂选词。
- 旧模型:`model_runs` 最新 2026-11-04T18:39:05Z(`v20261105_0239`,train_n 6,704 / pos_n 5,786 /
  acc 0.5153 / precision 0.2084 / recall 0.9713 / F1 0.3432 / **AUC 0.8803**,deployed=1);
  资产文件 `05_V4.5数据资产分层/elite_nb_current.json`(45.8 MB)、`hot_predictor_current.json`。
- 类目映射只有两个标签:`primary_type = 真人故事 41,057 / 赚钱案例 12,621`(其余为空)。

**为什么这是最高价值**(对比现网):新系统 403 行 / 121 条实测 / finance+history 各 10 条(不到标定门槛);
旧库一次就能给出 1.7 万条"有正文 + 有真实互动"的头条样本和 2.8 万条关键词战绩。
用户目标 2(什么关键词出爆款)与目标 3(预测准确率)现在缺的就是这种量级的样本。

**P26 的硬前置(下一轮必须按顺序做,不许先写代码)**:
1. 先出红名单测试(迁移幂等、去重、`read_count=0 → NULL`、`data_source/credibility` 分层、类目路由拒绝未映射);
2. 只读映射表:`primary_type → 类目`(假设 `赚钱案例 → finance`、`真人故事 → social`,写进 `00_control/` 配置而不是代码里猜),
   未映射的 `primary_type` **拒绝入库**并计数;
3. 身份与去重:先按 `url_normalized` 与 `content_hash` 对现网 403 行做只读碰撞预演,报"会新增多少 / 会撞多少 / 近似重复多少";
4. 新鲜度隔离:`published_at` 超过阈值的旧文不得挤占 RAG 检索(检索必须能按发布时间与来源过滤);
5. 管线增量化(第六节石头),否则每任务 3–4 分钟重建;
6. 分批导入 + 每批备份 + 每批对账(行数 / FTS / 指标 / 标定样本变化)。

**不许做的事**:`资产库/` 一律不删不改(只读打开,`mode=ro`);任何删除必须先出逐项清单(规则 29/43)。

## 十、测试与回归

| 项 | 结果 | 凭据 |
|---|---|---|
| 本轮 P25 新测试 | **11 项**:`test_p25_observability.py` 4(入队审计 + `fts_coverage` 体检)、`test_p25_toutiao_channels.py` 4(新频道注册/取证/路由/限速)、`test_p25_fts_after_crawl.py` 3(第六节) | 前 8 项:改前 red → 改后 green(`07_tmp/r28_channels/p25_obs_green.txt`);后 3 项:`p25_fts_red.txt` → `p25_fts_green.txt` |
| 上一节遗留口径更正 | P24→P25 交接清单写"新增 9 项、总数应 420",**实际新增 8 项、419 passed** —— 数字是清单写错,不是测试丢了 | `--collect-only` 计数 419 |
| 全量回归(加 FTS 修复后) | **422 passed / 1 warning / exit 0 / 138.26 s**(= 411 P24 基线 + 本轮 11 项);FTS 修复前一次为 419 passed / exit 0 / 132.74 s | `07_tmp/r29_channels/p25_full_suite.txt` |
| 测试隔离护栏 | 全量跑一次通过,无"测试改动生产状态"告警;basetemp 仍在 `07_tmp/pytest_basetemp`,只留最近 4 份(本轮回收 `pytest-129` 26,303,108 字节,日志 `basetemp.purged`) | `p25_fts_green.txt` warning 行 |

## 十一、体检与磁盘(收账)

`ttxt doctor`:**34 ok / 0 failed / 5 WARN**(WARN 与 P24 同一批,没有新增):
`namespaces.disk`(demo 有库未注册)、`db.identity_suspect` / `db.url_identity`(demo 的 quotes_toscrape 30 行把来源页当文章地址,
历史残留、该来源已停用、**不得删数据**)、`db.orphan_references`(9 条 `evidence_links.article_id` 为空,等 DDL 批准)、
`crawler.insert_judgements`(近 24h:身份塌陷 3、正文升级 16,集中在 social/chinanews_society)。
新增/关键 PASS:`db.fts_coverage 403/403`、`db.cleaned_coverage 403/403`、`db.duplicate_rows`、`db.cross_overlap`、
`crawler.stack.browser_disk 608MB/3.0GB`、`disk.free 97.4GB`、`disk.root_budget 6.43GB/20GB`、
`disk.scratch 07_tmp=0.635GB cap=2GB`、`disk.accounting 未登记=0.001GB`。

磁盘逐项(只测量,不删除):`资产库` 4,653 MB、`02_crawler` 562.5 MB(几乎全是 Edge profile 组件缓存:
`vti-b-p32-visual.quant.ort` 168.6 MB、`component_crx_cache` 155.5 MB、Widevine 21.9 MB)、
`07_tmp` 526 MB(→ 体检后 0.635 GB,含本轮 pytest basetemp)、`.venv` 274.9 MB、`tools` 186 MB、
`.pip-cache` 91.3 MB、`11_backups` 38.8 MB → 4 个 zip。
另有一处**不属于项目根目录**的遗留:`C:\Users\yangl\AppData\Local\Temp\pytest-of-yangl` 50.8 MB(项目外,按规则 22 不动,写进待办让人决定)。

## 十二、诚实说明(本轮没做到的)

1. finance / history **没能标定**(样本 10 < 30),闭环只复制了"采集 + 指标 + 预测",标定与回填精度还差样本量;
2. technology 的 4 条 404 未查明根因(只取证了现象与 URL);
3. `favorites`(收藏)语义配对仍未证实,继续 NULL;
4. 面板触发的入队归因是**修好后才生效**的:job 71 到底谁点的仍不可证;
5. 没跑 `ttxt schedule --once`、没上常驻 Worker、没做 `crawl-enrich`(health 25 行仍全 `short`)、
   没改 DDL(9 条空 `evidence_links.article_id` 仍等批准)、`keywords.py` 仍不读 `viral_labels`;
6. 预测基线仍是桶均值,MAE 19 个样本,不构成质量宣称;`quality_claim` 仍是 **NOT_AVAILABLE**(黄金集 61 张卡片人工裁定 0/61,只有本人能做)。

## 十三、第 9 轮待办(P26,按优先级,每项都带"怎样算做到")

⓪ **给关键词反哺加"入队冷却"(13:3x 新发现,取证见第十五节第 4 条;先做,半天)** — 做到:同一 gap 关键词在 24h 内不重复入队,
"连跑两次 analyze 只入队一次"有红→绿测试,入队日志带第几次尝试;health 的 `睡眠`(永远 25 条重复)不再每轮排队。
> **2026-11-16 14:3x 已做到**(取证 `07_tmp/p26_cooldown/`、说明 `window-progress-20261116.md` §十):5 项红→绿测试、全量回归通过、`crawl.enqueued` 日志带 `attempt`。
> 一条口径差异先说明白:冷却窗口按**「类目 + 来源」**算,不是逐关键词算 —— 入队动作本身就是"一个来源一条任务、一条任务带走全部 gap 词",
> 按词计窗口不会改变任何行为。另一条:生产上第一次出现"冷却中"文案要等修复前排的 job 76/77 排干,现在它们仍走 p11 去重通道,这条我不伪造。
1. **旧资产迁移(唯一主线,见第九节六步)** — 做到:finance/social 各有 ≥30 条 `measured` 且完成标定;
   `ttxt analyze` 后 `db.fts_coverage` 仍 PASS;`ttxt search` 能按新鲜度过滤;旧库文件 sha256 前后一字不差。
2. **管线增量化** — 做到:`run_pipeline` 只处理"未清洗/正文变化"的行与"未进索引"的行;
   实测 5 万行库上单任务耗时 < 60 秒(现在外推 3.0–3.7 分钟);测试必须含"连跑两次不多建行"。
3. **`keywords.py` 读 `viral_labels`**(用户目标 2 的关键缺口) — 做到:关键词评分里有一项来自爆款标签的权重,
   并有红→绿测试证明"同一个词在有爆款标签/无标签两种数据下分数不同"。
4. **technology 404 根因** — 做到:同一 URL 多次抓取的外壳差异取证(已有部分)+ 结论:是文章删除、限流还是外壳轮换,并给出对策。
5. `crawl-enrich`(health 25 行全 `short`,均值 119.4 字) — 做到:`db.completeness.health` 出现 `full` 行且 `short` 下降。
6. `ttxt schedule --once` 上线(队列已有 analyze 反哺的 job 75 待消费) — 做到:`scheduler_state.json` 时间不再是 11-12 19:57。
7. C-5 可信度分档 + 9 条空 `evidence_links.article_id`(需 DDL 批准);`_element_text` 标题缺陷 + `PROCESSOR_VERSION` bump。
8. `07_tmp` 清理(526 MB)+ 项目外 `pytest-of-yangl` 50.8 MB — **必须先出逐项清单交本人批准**(规则 29/43)。
9. 黄金集 61 张卡片人工裁定(只有本人能做):需要时我把 61 条压成一页对照清单。

## 十四、工具与踩坑(沉淀)

- 本机 `git` 不在 PATH:必须 `$env:PATH = "D:\ttxt\tools\git\cmd;" + $env:PATH` 或用 `scripts\git.cmd` / `scripts\push.cmd`。
- 只读打开旧库做 GROUP BY/排序会报 `unable to open database file`(SQLite 临时文件落盘失败):
  先 `PRAGMA temp_store=MEMORY`(`07_tmp/r29_legacy_asset_probe/*` 已按此写)。
- **后台 pwsh 作业只要 stderr 有输出就回传 `exit code 1`**,与被调程序的真实退出码无关。
  最小复现:`python -c "import sys;sys.stderr.write('noise\n');print('ok')"`(前台 exit 0,后台 exit 1)。
  所以 `ttxt worker` / `ttxt metrics`(Playwright 会往 stderr 写告警)显示 exit 1 **不代表失败**,
  判成败看回执 JSON、日志事件与库内读数。本轮 `worker` 的真实结论由 `worker.stop reason=max_jobs done=3` + 三条 `crawl.done` 认定。
- 控制台读 UTF-8 文件会乱码(`Get-Content` 显示 mojibake),取证一律用文件 + UTF-8 读写,不在控制台判断内容。

## 十五、收账补记(13:31 → 13:45;本文前十四节的账不改写,按规则 44 就地更正)

收账时把 analyze 反哺出来的排队任务也排干了,并顺手抓到第二处同源缺陷。**下面这组数字才是本轮最终读数**,
前文出现的 403 / 177 / "队列非空 = job 75" 是中途读数。

1. **排干 job 74 / 75**(`ttxt worker --owner p25b --max-jobs 2 --idle-stop-s 45`,回执 `stop_reason=max_jobs done=2`,elapsed 64.19 s):

| 任务 | 来源/类目 | pages | links | extracted | inserted | duplicates | elapsed_s | plan(关键词/理由) |
|---|---|---|---|---|---|---|---|---|
| 74 | toutiao_tech / technology | 11 | 16 | 10 | **9** | 1 | 59.61 | `效率工具` / `keyword_gap_feedback` |
| 75 | who_cn_news / health | 1(RSS) | — | 25 | **0** | **25** | 4.58 | `睡眠` / `keyword_gap_feedback` |

   这一轮再次印证规则 47:同一个 worker 作业 shell 回传 `exit code 1`,而回执是 `ok: true` × 2 —— 因为 Playwright 往 stderr 写了 `INFO: Fetched (200) ...`(`07_tmp/r29_channels/p25_drain2.txt`)。

2. **最终库内读数**:`ttxt analyze --category all` 后 —— technology **186** / finance 106 / history 30 / social 33 / health 25 / demo 32 = **412 行**;
   `db.fts_coverage` **412/412 PASS**(analyze 回执里 `indexed: 186 / 106 / 33 / …` 就是新加的计数)、`db.cleaned_coverage` **412/412 PASS**;
   `ttxt doctor` **34 ok / 0 failed / 5 WARN**(与第四节同一批已知 WARN,无新增);
   `disk.root_budget 6.534GB/20GB`、`disk.scratch 07_tmp=0.634GB/cap=2GB`。

3. **第二处同源缺陷(已修,红→绿)**:管线把 `indexed` 放进了回执,但 `ttxt/crawler/runner.py:452-458` 往 `summary["pipeline"]` /
   `crawl_jobs.meta_json` 拷贝时是**键白名单**,新键被静默丢掉 ⇒ 生产 Worker 回执里看不见索引计数(实测 job 74 的 `pipeline` 段没有 `indexed`)。
   修法 = 白名单加 `indexed`。红:`test_c4_clean_coverage.py::test_crawl_summary_and_job_meta_carry_pipeline_stats`
   改前 `KeyError: 'indexed'`(1 failed / 7 passed),改后与 FTS 测试同跑 **11 passed**
   (`07_tmp/r29_channels/p25_indexed_red.txt`)。**教训**:`stats` 类回执用白名单拷贝,加派生计数必须同时改白名单,
   否则"修了但账上看不到",与规则 46 同族。

4. **新发现:关键词反哺入队是无界循环**(本轮取证,未修,列为第 9 轮待办 ⓪):
   `crawl.enqueued` 里 `actor=keyword_feedback` 今天出现 4 次 —— 13:03:23(job 74,technology)/ 13:04:55(job 75,health)/
   13:34:25(**job 76**,technology)/ 13:34:27(**job 77**,health),关键词同样是 `效率工具` / `睡眠`。
   health 那一支跑完 `inserted 0 / duplicates 25`(who.int 的 25 条早已全在库)⇒ **gap 词永远补不上,但每轮 analyze 都再排一次队**,
   白烧限速预算与浏览器时间;technology 一支有效(job 74 净增 9 行)。
   验收口径:**同一 gap 关键词在冷却期(建议 24h)内不得重复入队**,且入队日志要能看出"第几次尝试";
   红名单测试必须先证明"连跑两次 analyze 只入队一次"。

5. 收账时队列读数:`aborted 7 / done 68 / queued 2`(job 76 / 77)。这两个是上面那条循环的产物,
   **本轮故意不再排干**(排干就是重复烧同一个白烧的动作),留给第 9 轮按冷却机制修好后再消费。
6. 最终全量回归:**422 passed / 1 warning / exit 0 / 137.57 s**(补 `indexed` 断言不新增测试项),凭据 `07_tmp/r29_channels/p25_full_suite2.txt`;
   本轮 P25 新增测试 **11 项**不变。

