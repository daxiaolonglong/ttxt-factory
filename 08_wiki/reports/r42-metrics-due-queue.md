# R42 表现取数时点:按 `due_at` 排队到点必采

本轮只修取数时点:**不改评分、不动门禁、不改取样规则、不改 A+B 读数**。
`favorites` 仍然采不到就记 NULL,没有任何一处填 0。

---

## 一、改了什么

### 1. `due_at` 队列(`ttxt/analysis/metrics_queue.py`,新建)

类目库内新增 `metrics_due_queue`,主键 `(category, article_id)`,状态机:

| state | 含义 | `next_attempt_at` |
|---|---|---|
| `scheduled` | 还没到 `due_at`,不许采 | = `due_at` |
| `ready` | 已到期且仍在 `[due_at, window_end]` 内 | 立即可采 |
| `backlog` | 窗口已过、仍缺到期读数(积压) | 立即可采 |
| `done` | 已有窗口内读数 / 已确认下架 / 白名单外 / 404 | — |
| `exhausted` | 失败达到上限且窗口已过 | — |

- 状态判定只看 `articles.published_at` + `performance_windows`(T+N),发布时刻不可解析的文章不入队。
- 队列每轮先 `sync_queue` 再 `claim_batch`:新增到期文章进 `ready`,窗口过掉还没读数的进 `backlog`,已有 `on_time/late` 的直接 `done`,窗口已不存在的行清掉。
- **优先级**:`ready` 先于 `backlog`;`ready` 按 `due_at` 升序(谁先到期谁先采),`backlog` 按 `window_end` 倒序(积压里先补最新的)。

### 2. 到点必采(调度 + CLI)

- 调度器新增 job kind `metrics_due`,生产配置启用 `metrics_due_60m`(每 60 分钟、单轮上限 12 篇、`sleep_s 2.5`、`wait_for_queue_s 180` 让开 crawl Worker)。
- 旧的 `metrics_fetch_6h` 置 `enabled: false`(代码保留,只用于人工对照/回滚),避免两套时点口径并行。
- CLI:`ttxt metrics --category <c|all> --due-enqueue / --due-run / --due-report [--apply] [--evidence <path>]`。
- 没有到期任务时直接返回 `no_due`,**不开浏览器**(空转不占 profile)。

### 3. 重采频率与失败上限(本轮定死)

| 结果 | 处理 |
|---|---|
| 窗口内成功一次(`metrics_ok`) | 立即 `done`,**不再重采** —— 口径是窗口内取最早,重复采没有意义 |
| 404 / 410 | 进 90 天下架台账并 `done`(沿用 R41 的 `metrics_link_status`) |
| 白名单外主机 | `done` + `unsupported_host`,不再每轮重抢 |
| 网络/渲染失败 | 退避 **1h / 6h / 24h**,最多 **3 次**;3 次后窗口已过 ⇒ `exhausted` |
| dry-run(`apply=False`) | 不推进 attempts、不写窗口,只给计划 |

窗口内取最早的规则、T+N 定义、状态机(`on_time/late/early/pending/overdue/unavailable`)一个字节没改,全部复用 R41 的 `publication.classify`。

---

## 二、修完前后对比(真实数据,类目 technology)

本轮实跑一轮:claim 12 篇 → 实际写入 9 篇(3 篇是白名单外域名,已按 `unsupported_host` 终结,不再反复重抢)。

| 指标 | 修前 | 修后 | 变化 |
|---|---|---|---|
| 读数条数 | 117 | 126 | +9(新增 9 篇到期采集) |
| **晚采比例 `late_ratio`** | 0.700855 | **0.650794** | **−0.0501** |
| **on_time 覆盖率 `on_time_coverage`** | 0.074786 | **0.094017** | **+0.0192** |
| 读数中 on_time 占比 | 0.299145 | 0.349206 | +0.0501 |
| `on_time` 窗口数 | 35 | 44 | +9 |
| `late` | 82 | 82 | 0(口径不同,不与 on_time 混算) |
| `overdue` | 31 | 22 | −9 |
| 可补救 `recoverable`(窗口内且无到期读数) | 21 | 12 | −9 |

字段缺失率(分母=有读数窗口):

| 字段 | 修前 | 修后 |
|---|---|---|
| reads | 0.059829 | 0.055556 |
| impressions | 0.059829 | 0.055556 |
| likes | 0.0 | 0.0 |
| comments | 0.0 | 0.0 |
| **favorites** | **1.0** | **1.0**(100% NULL,未填 0) |

队列状态变化:

| state | 修前 | 修后 |
|---|---|---|
| scheduled | 263 | 262 |
| ready | 21 | 9 |
| backlog | 67 | 63 |
| done | 117 | 134 |
| exhausted | 0 | 0 |

> 其他类目(finance/social/history/health)本轮未实采,前后读数完全一致,这是预期 —— 各自 `ready` 队列由调度器下一轮按 `due_at` 推进。它们当前 `ready` 分别 19 / 21 / 19 / 0 篇,`backlog` 4,275 / 13,651 / 42 / 27 篇。

---

## 三、复现命令

```powershell
$env:PYTHONPATH='D:\ttxt'
# 1) 看修前口径
.\.venv\Scripts\python.exe -m ttxt metrics --category all --due-report --evidence 03_intelligence/publication_chain/timing-before-20261119.json
# 2) 同步队列(不采)
.\.venv\Scripts\python.exe -m ttxt metrics --category all --due-enqueue
# 3) 真的按 due 采一轮(写库 + 刷新 T+N 窗口)
.\.venv\Scripts\python.exe -m ttxt metrics --category technology --due-run --apply --limit 12 --evidence 03_intelligence/publication_chain/due-run-technology-20261119.json
# 4) 再看修后口径
.\.venv\Scripts\python.exe -m ttxt metrics --category all --due-report --evidence 03_intelligence/publication_chain/timing-after-20261119.json
```

产物:`03_intelligence/publication_chain/timing-before-20261119.json`、`queue-sync-20261119.json`、`due-run-technology-20261119.json`、`timing-after-20261119.json`、`timing-diff-20261119.json`。

---

## 四、本轮明确未动

- 评分(连续化)、去模板化:未改一行。
- A+B 门禁、事实可核率、取样规则、SEEDS 与旧集读数:原样(窗口集 13/61 = 21.31% `NOT_AVAILABLE`、事实可核率 57/61 = 0.934 MET)。
- 草稿 → 发布链接的绑定:停在等待发布方式确定。**未接入任何发布接口,未使用任何用户账号执行发布动作**。
- `favorites`:采集不到就 NULL,前后都是 100% 缺失,没有一处填 0。

---

## 五、测试

- 新增 `10_tests/test_r42_metrics_due_queue.py` **11 项全过**(状态跟随 due_at / claim 优先级 / 下架台账不采 / 退避与耗尽 / 404 终结 / apply 后刷新窗口且 favorites 恒 NULL / 白名单外终结 / dry-run 不推进 / 显式 id 顺序 / 窗口内取最早不变 / timing_report 口径)。
- 配套修正:`test_p22_render_counters`(Namespace 兼容 `due_*`)、`test_p35_metrics_scheduler`(断言切到 `metrics_due_60m`)、`test_p38_disk_budget_fixtures`(清临时目录)、`conftest.py` 裁剪副本把 `metrics_due_queue` 当非内容表(并给裁剪逻辑加版本号,旧缓存自动重建)。
- 全量:`920 passed`。
