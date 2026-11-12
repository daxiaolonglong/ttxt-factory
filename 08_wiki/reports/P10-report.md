# P10 生产可靠性修复报告(审查报告第一批)

- 阶段:P10(外部代码审查后的修复波)
- 日期:2026-11-12
- 结论:**第一批修复全部落地并通过失败测试**;写作门禁在真实模型上验证有效;第二/三批仍待做,不冒充完成。

## 一、修复对照(审查报告 → 修复)

| 审查问题 | 修复内容 | 证据 |
|---|---|---|
| 写作无完整性检查、无硬门禁、修订耗尽仍导出、截断即导出 | `workflow.py` 重写:`finish_reason=length` 自动加大 token 重试一次;`_is_complete`(长度 ≥400 + 句末标点收尾);评审输出 `critical`;硬门禁 = 完整 ∧ score≥0.6 ∧ critical 为空;未过 → `writing_history.status='rejected'`,仅导出 `drafts/<类目>/rejected/` | 6 个门禁失败测试;真实 Q4 运行两篇均被正确拒绝(0.6 分/编造风险、0.3 分),成品目录无文件 |
| 证据链 quote 恒为 NULL | `verify_claims` 取来源文章中与声明重叠度最高的句子写入 quote(启发式,文档如实标注) | 测试断言 quote 非空;真实运行 quote 为原文句子 |
| 演示来源会污染正式类目 | `runner.resolve_category`:demo 来源强制 demo、拒绝其他类目;正式来源必须等于声明类目;入库前校验 | 4 个路由测试 |
| 锁误删风险(不看 pid) | 锁文件存 pid;陈旧判定 = mtime 超时 **且** pid 已死(ctypes OpenProcess 存活判定);release 校验所有权才删除 | 存活 pid 不误删、死 pid 接管、他人持有不删 3 个测试 |
| 崩溃遗留 running 永挂 | `queue.requeue_stale_running`(模型租约 900s)+ `store.recover_stale_crawl_jobs`(采集 30 分钟)+ `ttxt recover [--run]`;gate 每次执行前自动恢复 | 4 个恢复测试;CLI 实测 0 stale、3 失败任务正确重排 |
| 采集 attempts 不递增、无重排 | `mark_job_started` attempts+1;`fail_crawl_job` 未达上限 → queued,否则 failed | 3 个测试 |
| robots 异常时默认放行 | 404/410 → 允许(无规则);5xx/网络异常 → `unknown`,来源暂停不写数据;支持 `*` 通配符与 `$` 锚 | 404/500/连接错误 3 个测试;E2E 故障注入改走 robots_unknown 路径 |
| 指标缺失当 0 | `score_with_metrics`:任一互动字段为 None → `unscored + incomplete_data`(缺失 ≠ 0) | 2 个测试 |
| housekeep 限额未全执行 | 新增 pip 缓存/浏览器目录超限清理、数据库大小告警,`caps_status` 进报告;`--clean` 全量执行 | 1 个测试;实测报告含 caps_status |
| e2e 用系统 %TEMP% | 恢复演练目录改 `99_temp/e2e-restore-*` | E2E 运行通过 |
| Playwright 写系统 %TEMP% | `_browser_get` 将 TMP/TEMP 重定向到 `99_temp/browser-tmp`(finally 恢复) | 代码 + housekeep TTL 兜底 |
| CMD 黑窗 | 新增 `scripts/start-dashboard.vbs`(pythonw 隐藏窗口)+ `scripts/stop-dashboard.cmd` | 文件就绪 |
| 文档不同步/重复 | `db_schema.md` 重写(删除旧版重复表块,p10-v1);README/architecture 登记 P9/P10;README 区分本 DSH 每日 18:00 巡检与你 ChatGPT 每周任务 | 文档已同步 |

## 二、Schema 变更

- `SCHEMA_VERSION` p8-v1 → **p10-v1**:`writing_history.status`(draft/passed/rejected),迁移幂等。

## 三、测试与真实验收

| 检查 | 结果 |
|---|---|
| `ttxt selftest` | **82 passed**(修复前 56;新增 26 个失败场景测试) |
| `ttxt doctor` | 10 PASS / 0 FAIL |
| 真实写作门禁(Q4 模型) | 第 1 篇:完整但 critical"无来源断言/编造风险" → **rejected** → rejected 目录;第 2 篇(E2E):0.3 分 → **rejected**;成品目录均无文件 |
| 头条回归 | `--limit 3`:robots 200 → 3 条全部 duplicates,0 新增 |
| 恢复演练 | `ttxt recover`:模型 0 stale、采集 0 stale、3 个历史失败任务重排 queued |
| E2E 全链路 | 10 步全过:故障注入走 robots_unknown;备份恢复 33=33;26 个模型任务 0 重叠;schema p10-v1 |
| 磁盘 | 0.364 GB / 20 GB;housekeep 全限额执行(pip 95.7MB<1GB、浏览器 0、DB 无告警) |

## 四、诚实遗留(不冒充完成)

- 3 个历史失败采集任务已重排 queued(其中 `e2e_unreachable` 来源已移除,重跑会再次失败直至耗尽重试;可人工忽略或由巡检跟踪)。
- 写作门禁当前是**严格但保守**的启发式(完整性 + 模型评审 + 证据重叠),仍不等同于人工事实核查;通过率低说明演示语料与本地模型质量下"宁缺毋滥"。
- 第二批(授权指标导入、快照/预测/知识反馈、关键词自动反哺、后台调度、面板任务下发)与第三批(多类目长跑、故障全矩阵、磁盘边界验收)**未做**,报告不虚标。
- 3 篇历史截断草稿保留在 `04_writing/drafts/demo/`(旧版产物,不合格),已在本文与 Wiki 标注,不删除。

## 五、回滚位置

- 修复前:`11_backups/P10-pre-20261112-135642.zip`
- 修复后:`11_backups/P10-final-20261112-142130.zip`、`E2E-20261112-142100.zip`
