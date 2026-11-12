# P11 报告:生产闭环第二批(指标回流 / 预测校准 / 关键词反哺 / 本地调度 / 面板任务下发)

日期:2026-11-12(Asia/Shanghai)
范围:P10 审查结论「首轮框架基本完成,生产闭环尚未完成」所指的闭环缺口,即 P10 报告计划的第二批(fix-only 之外的全部闭环环节)。
结论:**本批全部环节已实装,并经真实运行验收(非模拟)**;生产闭环的机制性断点已打通,唯一外部依赖是头条后台真实指标导出。

---

## 1. 本批交付内容

| 环节 | 实现 | 关键文件 |
|---|---|---|
| 指标导入授权 | 白名单来源(`00_control/config.json → metrics.authorized_sources`),未授权来源直接 `PermissionError` 拒绝;负数/跨类目/全缺指标逐行拒绝,缺失≠0 | `ttxt/analysis/metrics_import.py`(222 行) |
| 指标快照时序 | `--snapshot` 把当前 `article_metrics` 冻结进 `metric_snapshots`,并联动重打爆款标签 | 同上 + `ttxt/analysis/viral.py` |
| 预测→回填→知识资产 | 无实测基线拒绝预测;`settle` 仅用 measured 回填;样本 < `prediction.min_samples`(5) 不写知识资产 | `ttxt/analysis/prediction.py`(187 行) |
| 关键词自动反哺 | 零覆盖 active 词→标 `gap`;`gap` 词重新有覆盖→恢复 active;首个启用非 demo 来源自动入队(去重);每类目写 JSON 证据 | `ttxt/analysis/keywords.py`(184 行) |
| 本地调度器 | 配置驱动(crawl_enqueue / analyze / recover / housekeep 四类);**配置层面禁止 write/publish/model 类任务**;状态文件幂等;任务失败互相隔离 | `ttxt/scheduler.py`(约 225 行) |
| 面板任务下发 | 仅 `POST /api/tasks/crawl|analyze`,四重护栏:`X-TTXT-Task: 1` + 回环 Host + Origin 同源 + 64KB JSON 对象;写作/发布/模型 404 不可下发 | `ttxt/dashboard/server.py`(209 行)、`ttxt/dashboard/api.py`(315 行) |
| CLI | `ttxt crawl-queue [--run]`、`ttxt metrics ...`、`ttxt predict ...`、`ttxt schedule [--once|--daemon]` | `ttxt/__main__.py` |

Schema 未变(仍 `p10-v1`),无需迁移。

## 2. 本批发现并修复的真实缺陷(非测试装饰,均为闭环断点)

| # | 缺陷 | 后果 | 发现方式 | 修复 |
|---|---|---|---|---|
| B1 | `scheduler._parse_iso` 时间戳 aware/naive 混用 | 首次运行写状态后,第二次起 `TypeError: can't subtract offset-naive and offset-aware datetimes`,调度器永远无法跑第二轮 | 单测第二轮必现 | 统一归一化为本地 naive |
| B2 | `_LOOPBACK_HOSTS` 写死 `"[::1]"`,而 Host 解析已剥掉方括号 | IPv6 回环请求永远 403,护栏与实现不自洽 | 代码审查 + 单测矩阵 | 集合改为 `"::1"`,与解析器一致 |
| B3 | `score_keywords` 只统计 `status='active'` 的关键词 | 关键词一旦标 `gap` 就永远不会被重新计数,`auto_feedback` 的 gap→active 恢复通道**永久不可达**——闭环断在评分层 | 单测 `test_feedback_gap_enqueues_once_with_evidence` 失败 | 覆盖统计改为全部关键词;状态迁移仍归 `auto_feedback` 独占 |

B3 是本批最重要的发现:它说明"评分层只盯 active"会让反哺闭环变成单向门,此前 P4 测试未覆盖恢复路径所以从未暴露。

## 3. 自动化测试证据

- 全量测试:**101 passed**(基线 82 + P11 新增 19,`10_tests/test_p11_closed_loop.py`,15.4s)。
- P11 覆盖:指标诚实性(拒绝伪造/负数/跨类目/全缺)、预测三重门禁、反哺 gap→入队→去重→恢复→demo-only 全链路、调度器禁用类拦截 / is_due / 停用跳过 / 故障隔离 / 时区安全、面板护栏矩阵(缺头 403 / 恶意 Origin 403 / 非回环 Host 403 / 非法 JSON 400 / write 404 / 非任务路径 405)。
- P4 原 8 项在 keywords 修复后仍全绿(无回归)。

## 4. 真实运行验收(生产库,非模拟)

证据固化于 `08_wiki/reports/p11-evidence/`。

### 4.1 面板 HTTP 护栏矩阵:11/11(`http_guard_matrix.txt`)

- GET sources/tasks:200;
- `POST /api/tasks/crawl`(toutiao_tech)→ **202 job_id=13**(仅入队,不执行);
- `POST /api/tasks/analyze`(demo)→ 200;
- 缺任务头 403、恶意 Origin 403、非回环 Host 403、畸形 JSON 400、`/api/tasks/write` **404(写作/发布/模型不可下发)**、非任务路径 405;
- `[::1]` Host/Origin 200(B2 回归护栏;面板按设计仅绑 IPv4 回环,故以头域覆写验证解析层修复)。

### 4.2 调度器首轮实跑:`ttxt schedule --once` 4/4 ok(`cli_scheduler_metrics.log`)

- `crawl_enqueue_4h`:enqueued 2(sspai_rss、ruanyifeng_atom)/ deduped 2(quotes_toscrape、toutiao_tech 已有排队);
- `analyze_daily`:technology / finance / social / history / health / demo 全部 ok;
- `recover_daily`:failed 重排 1(job 12)、stale 0;
- `housekeep_weekly`:项目 0.364 GB / 预算 20 GB,空闲 109.5 GB,各限额达标;
- `ttxt schedule` 状态显示四任务 `last_run_at=19:57:35, due=false`(**B1 时区修复的生产级证明**:第二轮读状态不再崩溃**)。

### 4.3 关键词反哺自动触发(6 类目证据文件)

`03_intelligence/keyword_feedback/` 共 8 份(含 demo 两份)。technology 实例:`gap_keywords:["效率工具"]`,命中已排队 job 13 → **去重复用不重复入队**,证据文件完整记录。

### 4.4 指标与预测链的诚实姿态(生产库当前无授权指标行)

- `ttxt metrics --list-sources`:`toutiao_admin_export(self_reported)`、`manual_verified(verified)`;
- `ttxt metrics --snapshot --category demo`:`snapshots=0`,relabel 33 篇全部 `missing_data` —— **生产库目前没有真实指标行,系统如实报告,不伪造**;
- `ttxt predict predict --category demo`:`"类目内没有任何 measured 实测样本,拒绝凭空预测"`;settle 0/0;report `asset_written=false`(`cli_predict_chain.log`)。
- 结论:预测→校准→知识资产链条机制就绪,等待外部输入(头条后台导出)后自动激活。

### 4.5 采集队列现状(`queue_after_scheduler.txt`)

7 个 pending:job 1(quotes_toscrape)、6/8/12(e2e_unreachable 幽灵任务,见 §6)、13(toutiao_tech,面板下发)、14/15(调度器入队)。面板与调度器均**只入队**,执行仍须 `crawl-queue --run`/人工确认。

## 5. 硬约束遵守核验(本批)

- 全部新文件仅在 `D:\ttxt`(含测试临时体在 `07_tmp` / `%TEMP%\ttxt_p11_http`);
- Strata 未启停/切换;调度器配置层禁止 model 类任务,面板不可下发模型;无任何并发模型调用;
- 指标白名单硬拒未授权来源;空指标/负数逐行拒绝;预测无基线拒绝——**无编造指标**;
- 无自动发布:面板无发布端点,write/publish 404;
- 每个环节均产生 JSON 证据文件(反馈证据、调度状态、HTTP 矩阵、CLI 日志)。

## 6. 已知边界与 P12 候选(如实列出,不掩盖)

1. **幽灵采集任务**:job 6/8/12 引用已删除来源 `e2e_unreachable`,执行必然失败,recover 又会重排,可能空转。P12:来源删除时撤销其 pending 任务,或 runner 对"来源不存在"直接终态 failed(不重排)。
2. **生产库无指标行**:预测/校准环等待头条后台导出;导入命令已在 `operations.md` 就位。
3. **pytest-of-yangl 目录**:本机 `TEMP` 指向 `D:\ttxt`,pytest 临时目录落在项目根,housekeep 限额未统计它。P12:纳入 housekeep 清理桶。
4. 面板仅绑 IPv4 回环(设计选择);`[::1]` 护栏由单测+头域矩阵覆盖。
5. 证据链仍是启发式(双字重叠 ≥0.2 计支持),不等于事实核查(已在 Wiki 声明)。
6. **第三批(未做,已在计划)**:多类目长周期运行、崩溃/断网故障全矩阵、写作质量回归、磁盘边界压测。

## 7. 备份与回滚

- 本批完成后快照:`11_backups/P11-final-<时间戳>.zip`(见 README 阶段表);
- 恢复路径不变:`db/system.db → 00_control/`,`db/<类目>/articles.db → 01_categories/<类目>/`。

## 8. 验收结论

「指标回流 → 预测校准 → 知识进化」与「分析 → 关键词反哺 → 采集」两条闭环已在代码、测试、真实运行三个层面贯通;调度与面板在护栏内可无人值守运行(仅入队/分析/恢复/清理)。生产闭环剩余缺口全部是**外部数据依赖或第三批计划项**,并已如实登记,无 PASS 报告掩盖的问题。
