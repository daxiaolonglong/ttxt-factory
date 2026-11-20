# R58 数据地基体检:产能实测 + 指标回采队列缺陷 + 样本量测算

核对时间:2026-11-20 19:05(+08:00)· 类目:technology · 数据库:`01_categories/technology/articles.db`
性质:只测不建(产能与队列)→ 项目负责人裁决后改为**只修队列,不加功能**。本报告含三批实测、队列真实状态、样本量与天数测算、队列修复口径与端到端证明、补采进度。

---

## 1. 产能实测:technology · 新词表 · 3 批 × 10 篇(串行,批间留间隔)

驱动:`scripts/r55_batch.py --apply`(只驱动既有链路:`keyword_crawl.plan_category/enqueue_plan` → `runner.run_next` → `store.insert_items`,跑前只读预检、跑后逐行指纹 diff)。
限速纪律:间隔 2 秒、不登录、不绕过验证;失败按已验证的重取 ≤3 次;实际未触发连续失败。

| 批次 | job | 耗时(s) | feed 条数 | 入库 | 关键词拒收 | 长度拒收 | 重复 | 错误 | 既有行变化 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 406 | 61.22 | 10 | 7 | 3 | 0 | 0 | 0 | 0 |
| 2 | 407 | 58.61 | 10 | 7 | 3 | 0 | 0 | 0 | 0 |
| 3 | 408 | 50.34 | 9 | 2 | 3 | 3 | 1 | 0 | 0 |
| 合计 | — | **170.17** | **29** | **16** | 9 | 3 | 1 | 0 | **0** |

- 入库率 16/29 = **55.2%**;平均 **10.6 s / 入库篇**,**5.9 s / feed 条**。
- 三批 `changed_existing=0`:采集闸与 `store.insert_items` 未动,既有行零改动(指纹逐行比对)。
- 本批新入库文章 id 连续段 **1133–1148**(按 `source='toutiao_tech' AND fetched_at>='2026-11-20T17:20'` 取证;先前用 id 窗口 999–1014 认定"本批"是错的,那 16 行是 12:33 的 `ithome_rss` 外部入库)。
- 单轮产能换算:keyword 驱动单源 ≈ **5.33 入库/批**;调度器 crawl 侧 6 轮/天 ⇒ ≈ **32 入库/天**(头条域 9 日均进量 51.9 条/天:11-20 79、11-19 53、11-18 26、11-17 78、11-16 67、11-15 10、11-13 73、11-12 29;technology 全源进量 11-20 288、11-19 357)。

### 磁盘增量(如实报,不用"D 盘大"糊过去)

| 对象 | 跑前 | 跑后 | 增量 |
|---|---|---|---|
| `articles.db` | 28,602,368 B | 28,950,528 B | **+348,160 B**(16 行 ⇒ ≈21.3 KiB/行) |
| `00_control/system.db` | — | — | **+8,192 B** |
| `03_intelligence/` | 4,605,917 B | 4,621,194 B | **+15,277 B** |
| `01_categories/` 合计 | — | — | +348,160 B |
| D 盘空闲 | 142,993,813,504 B(≈133.0 GB) | — | — |

未测项(如实标注):`02_crawler/`(3,484,875,975 B,含浏览器 profile)、`09_logs/`、`11_backups/`、`99_temp/` 只取到单点,**浏览器缓存增量未纳入本次对照**;要精确归因需要单独做缓存目录前后快照。

---

## 2. 指标回采对齐:这是硬缺陷,不是观测项

三批跑完后(2026-11-20 17:2x,修复前)technology 库真实状态:

| 指标 | 数值 |
|---|---|
| `articles`(全部有 `published_at`) | 1,014 |
| `performance_windows`(subject_type='article', 3d) | **468** |
| `metrics_due_queue` | **468**(done 263 / scheduled 145 / ready 58 / backlog 1 / exhausted 1) |
| 既无窗口行也无队列行 | **546 篇 = 53.8%** |
| 本批 16 篇在队列里 | **0/16** |
| `performance_windows` 最后更新 | 2026-11-19T17:21:37+08:00(backfill 自昨日起再未运行) |

根因(唯一致因):队列的唯一来源是 `performance_windows` 行,而窗口行原先只有 `ttxt publish backfill`(`publication.backfill_articles(apply=True)`)或采集成功后的 `refresh_article_window` 会写 —— **调度器里没有这一步**。`due_at = published_at + 3d`、`window_end = due_at + 24h` 的算法本身正确(实测 16/16 补齐后全对)。

调度速率不是瓶颈:`metrics_due_60m` = 12 条/轮 × 24 轮 = **288 次/天**,远大于头条域进量(~52 条/天)。

白名单错配(额度黑洞):队列 468 条里 **176 条域名不在公开白名单**(白名单 hosts = `toutiao.com` / `m.toutiao.com`),其中非终结 99 条仍每轮占额度、已终结 77 条记 `done` 却**零快照**。按域看:toutiao 415 篇(292 有窗口行、171 有读数)vs 其他域 599 篇(176 有窗口行、**0 有读数**)。

缺窗口行按类目(全库,体量决定 backfill 必须限量):

| 类目 | 缺行 | 其中白名单内 | 库文章数 | articles.db |
|---|---|---|---|---|
| technology | 546 | 123 | 1,014 | 28.9 MB |
| finance | 956 | 261 | 13,780 | 336 MB |
| social | 548 | 428 | 40,694 | 962 MB |
| history | 115 | 86 | 331 | 10.6 MB |
| health | 99 | 94 | 166 | 5.4 MB |

---

## 3. 够不够:用数字回答

样本现状(修复前 / 修复后同日):

| 口径 | 修复前 | 修复后(19:05) |
|---|---|---|
| `metric_snapshots` 行数 | 313 | **335** |
| 有快照的文章数 | 243 | **265** |
| 有 T+3 实测 `reads` 的文章 | 171 | **193** |
| 可用对照对(score_v2 × 实测 reads) | 171 | 193 |

- reads 分位(171 篇):[0, 346, 1709, 9307, 42367, 154282, 224809];≥1000 共 103 篇,≥10000 共 42 篇。
- `score_v2_labels` 654 行(score min −8.2 / max 0.878 / avg −0.364),`viral_labels` 654(unscored 411 / normal 90 / potential 74 / low_quality 59 / verified_viral 20)。
- 实测秩相关(171 对):**Spearman ρ = 0.083,95% CI [−0.068, 0.23]** —— 与 0 不可区分。
- 按评分三分位:低 n=57 中位 1456 / 中 n=57 中位 1709 / 高 n=57 中位 2735(≥10000 分别 13 / 12 / 17)⇒ **当前样本分不出高低**。
- 另一个卡点在评分本身:654 条 label 里 `score_v2 ≥ 0.5` 只有 **3 条**,评分分布退化;样本补够也测不出区分度(这是判断,不是改动指令)。

阈值与依据(不含糊):

| 目的 | 需要 | 依据 |
|---|---|---|
| 检出弱信号 ρ=0.20 | n ≈ 194 | Spearman 双侧 α=.05、power=.80 |
| 检出更弱信号 ρ=0.15 | n ≈ 344 | 同上 |
| 检出 ρ=0.10 | n ≈ 782 | 同上 |
| "爆款命中率"这类正负样本判断 | n ≥ 300(正样本 ≥30) | top-decile 命中率的最低可判下限 |
| 决策级(可指导词表/选题) | n ≈ 600 | ρ=0.15 可检出 + 分层后每层仍有 ≥50 |

按当前产能攒样本的天数:keyword 驱动单源 5.33 入库/批 × 6 轮/天 ≈ 32 入库/天;窗口成功率实测 58.6%(171/292 白名单窗口拿到读数)⇒ 有效对 **≈ 18.8 对/天**。

- 补到 **n=300**:(300−171)/18.8 ≈ **6.9 天** + T+3 窗口与 24h 宽限 ≈ 4 天 ⇒ **≈ 11 天**(下限)。
- 补到 **n=600**:(600−171)/18.8 ≈ **22.8 天** + 4 天 ⇒ **≈ 27 天**(决策级)。
- **不修队列:样本封顶 171 对**(最多再补 ~170 条已存在的 pending 窗口),之后无论跑多少批都不增长 —— 这就是"上一轮 19 对什么都证明不了"的原因。

结论:产能不是问题(2 秒纪律下 10.6 s/入库篇,链路 55% 入库率),**问题在回采入队这一步**;修好后按现节奏 ~11 天能到"能判强弱"的下限,~27 天到决策级。

---

## 4. 队列修复(项目负责人裁决后执行,功能一个没加)

### 4.1 新文章入队接进调度器(幂等)

- `publication.backfill_articles(..., only_missing=True, limit=backfill_limit, host_gate=True)`:只为还没有窗口行的文章补行,按 `id` 倒序(先近后远)。
- `metrics_queue.sync_queue(..., include_new_articles=True, gate_hosts=True, backfill_limit=…)`:apply 时先补行再入队;dry-run 不补行。
- 调度器 `metrics_due_60m` 新增开关:`backfill_new_articles=true`、`backfill_limit=200`、`gate_hosts=true`。
- 幂等前提:`only_missing` 让第二次候选集为空 + 窗口行 `(subject_type, subject_key, window_days)` 唯一键 + 队列 `(category, article_id)` 主键。

**live 库幂等实测**(technology,连跑两次 `--due-enqueue --apply --limit 200`):

| 读数 | 第 1 次 | 第 2 次 |
|---|---|---|
| `missing_windows_total` | 123 | **0** |
| `windows_inserted` | 123 | **0** |
| `host_skipped`(不入队) | 176 | 176 |
| `host_gate_skipped`(连窗口行都不补) | 423 | 423 |
| `archived_unsupported` | scheduled 96 | **{}** |
| `total` / states | 415 = scheduled 145 + ready 60 + backlog 24 + done 186 | 完全一致 |
| 重复窗口行 / 重复队列项 | 0 / 0 | 0 / 0 |

### 4.2 白名单校验提到入队之前

- 白名单外文章:连窗口行都不补(423 篇 technology),队列根本看不到它们;`sync_queue` 再对既有窗口行做 `host_allowed` 门(176 条 `host_skipped`)。
- 已经排进去的非终结条目 → `_archive_unsupported_on` 归档:`state=exhausted`、`attempts=MAX_ATTEMPTS`、`next_attempt_at=NULL`、`last_outcome=unsupported_host`,`claim_batch` 之后不再取到(**不重试**)。
- technology 归档 **96** 条;归档前原状态已 dump:`99_temp/r58/pre_archive_dump.json`(对照/回滚用)。归档后 `queue_nonwhitelist_nonterminal = 0`(此前 96)。

### 4.3 代理故障单独分层

- 事实核查:Windows 系统代理 `ProxyEnable=1`、`ProxyServer=127.0.0.1:57777`,端口在监听且 TcpTestSucceeded ⇒ **代理配置本身有效**;58 条 `ERR_PROXY_CONNECTION_FAILED` 全部落在 2026-11-20 17:37:08→17:38:29(81 秒),是一次瞬时抖动,但已把 58 条推到 attempts≥2、1 条转 exhausted。
- 新口径:`is_infra_failure()` 命中代理/网络类错误 ⇒ `last_outcome=infra_unavailable`,**不计入 attempts**(一次抖动不再把整批推到 exhausted),`next_attempt_at = now + 6h`;`infra_state()` 在冷却窗口内让 `run_due` 整轮 `skipped=infra_paused` —— 不开浏览器、不发请求;出现成功回执后自动恢复。回执新增 `infra_failures` / `infra_down`,调度器状态 `skipped_infra_paused`。
- 判定用 `last_attempt_at` 而非 `updated_at`(每轮 sync 会刷新 `updated_at`,会让冷却窗口每轮顺延、永远醒不过来)。

### 4.4 端到端证明(取当批新入库 id → 队列 → due_at → 真实采集 → 指标落库)

1. 新入库 16 篇(1133–1148):修复前 **0/16** 在队列;修复后 **16/16** 在队列,`due_at = published_at + 3d` **16/16 全对**(例:1137 published 2026-11-20T17:19 → due 2026-11-23T17:19,window_end 11-24T17:19)。
2. 真实采集:`ttxt metrics --due-run --category technology --limit 24 --sleep 2.5 --apply`
   ⇒ claimed 24(含全部 20 篇"此前从未入队、窗口已过"的文章)、fetched 22、written 22、snapshots 22、windows_refreshed 22、infra_failures 0、unsupported_host_skipped 0。
3. 指标落库抽查(快照表 + 窗口表都有值):
   - 610 → reads 1326(**on_time**);618 → 1839(**on_time**);788 → 1339(**on_time**)
   - 604 → 5289(late);623 → 7627(late);862 → 4887(late);1041 → 5786(late)
   - 621 → `article_gone`(404/410 走既有下架台账,窗口 unavailable、队列 done,**不硬试**)
4. 只写指标表不写文章表:`articles` 仍 1014 行、`MAX(id)=1148` 不变;`metric_snapshots` 313→335、有指标文章 243→265、`windows_with_reads` 171→193。

### 4.5 磁盘增量(补采这一轮,如实报)

- `articles.db` 28,950,528 → 29,040,640 = **+90,112 B**(窗口行 + 队列行 + 快照行);`system.db` +0;`03_intelligence` +0。
- 取证文件:**本轮走 CLI 补采没有取证增量** —— 取证由调度器 job 的 `_write_metrics_evidence` 写(`03_intelligence/metrics_public`),采集器本身不落盘。这是既有实现差异,不是本次改动引入,但意味着 CLI 补采的指标只有库内行、无 HTML 取证。
- D 盘空闲 132.98 GB → 132.84 GB(**−125 MB**):本链路只占其中 90 KB,其余来自并行采集与浏览器缓存。

### 4.6 测试

定向(未跑全量):`10_tests/test_r42_metrics_due_queue.py`(新增幂等钉测、白名单前置与归档、代理分层不计次与整轮暂停、回执分层计数)+ `test_r41_publication_chain.py` + `test_p35_metrics_scheduler.py` ⇒ **44 passed**。
既有 fixture 里的 `example.com` 样本改为真实白名单域:新口径下这些文章压根不入队,测试就测不到队列了。

---

## 5. 积压补采:进度与计划

546 篇缺口按裁决分流:

| 分层 | 数量 | 处置 |
|---|---|---|
| 白名单内、窗口已过 | 20 | **本轮已全部 claim**,18 采到指标 + 1 走下架台账(621)+ 1 未在 24 条批次内(925) |
| 白名单内、窗口未到(published 近 7 天) | 103 | 已补窗口行并入队为 scheduled,按 due 日到点自动采(12 条/轮 × 24 轮 = 288 次/天 ≫ 需求) |
| 白名单外 | 423 | 按裁决不入队、不补采(采集器不发请求);已排进去的 96 条已归档 |

即:**546 篇里真正可补采的是 123 篇白名单文章,已 100% 入队**;已过期那 20 篇的补采完成率 19/20(1 篇已下架)。
剩余队列积压(非 546 范畴):修复后 backlog 10、ready 52,按既有节奏自然消化;`exhausted` 96 条为归档条目,不再占额度。

分批控速:`--limit 24 --sleep 2.5`、单进程不并发;下架走 90 天台账不硬试。

---

## 6. 结论与建议(只给判断,不动策略)

1. **产能没问题,地基缺陷在入队**:修复前 53.8% 文章从未进回采队列且新文章永不入队;修复后新文章在同一次 `sync_queue` 调用内入队(按 job 节奏 ≤60 分钟),实测 16/16 补齐、`due_at` 全对。
2. **生效需要一次进程重启**:常驻 supervisor 仍跑旧代码(含新加的 job 开关),我没有擅自重启常驻树 —— 需要你确认后再重启,否则生产侧入队仍不会自动发生。
3. **样本结论**:按现产能 ~11 天到"能判强弱"下限、~27 天到决策级;不修则 171 对封顶。要不要提调度频率/加采集源,由你定;当前 288 次/天的回采额度远未用满,**加频率对样本增速几乎无益**,瓶颈在入库进量(≈32 条/天)与窗口成功率(58.6%)。
4. **评分侧才是下一个硬约束**:`score_v2 ≥ 0.5` 只有 3/654,评分分布退化;样本补到 300+ 也测不出区分度。建议下一轮先处理评分标定/区分度,再谈爆款判断。
5. **白名单外 423 篇 technology 文章永远拿不到指标**:要么在采集闸/词表侧限制非头条源入库(策略,你定),要么接受它们不进入指标样本;现状是它们已不再消耗回采额度。
