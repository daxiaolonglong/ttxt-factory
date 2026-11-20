# 会话检查点（压缩/新会话的唯一接手文件）

**这个文件的作用是"无损压缩"**：上下文被压缩、或换新窗口时，只相信这里写的事实，不靠对话记忆。
规则：每完成一个阶段、每次提交推送后、每次产生大输出（全量测试日志、全盘扫描、长报告）之后，必须重写本文件。
最后更新：2026-11-21 03:46 — **R60 全部四步已闭合并推送**；远端 `main` = `edfeb34`。

## 接手三行

1. **R60 已完成**（计划 `C:\Users\yangl\.qoder\plans\rolling-meadow-owl.md`）：G6 `e1998b6`、
   G3 `89c8724`、G1 全链 `3ba7697→…→9ce51d6→d6a17ad→edfeb34`、G2 核对确认 R28 已在生产生效。
   账本见 `08_wiki/PROJECT_STATUS.md` R60 段，细节见 `08_wiki/reports/r60-favorites-mining-banding.md`。
2. **收藏已打通**：收藏只在管理页逐行"查看数据"弹层"互动"区；生产采集+标题合并+`register_pass`
   登记（全库判重、只登记一次、只进 `account.own_work_category`=social）已验收：social 11697
   `favorites=1 reads=14 impressions=693`，登记行 41128 `source=own_account favorites=0 reads=3`，
   其余四类目零污染；`favorites is not null` 计数=2>0。job `account_metrics_2h` `enabled=true`。
3. **下一轮是 R61**：G4 写作批次 social+finance 各 ≥20 复验通过率；G5 verified_viral/junk 样本
   → 写作 few-shot 库（不微调）；黄金集 61 条冲突卡按模板重判。旧账：T-02 第二来源、
   T-16 展现量/标签覆盖、T-17 管线增量化、health 标定（26<30）。**不要按本文件旧版续跑 R60。**

## 已实测事实（2026-11-21 03:45）

- **G1 落库读数**（SQL 直查）：social `article_metrics` 中 `data_source='toutiao_account'` 共 2 行
  （11697、41128），`own_account` 文章 1 行；history/health/technology/finance 四类目
  `own_account=0`、`toutiao_account=0`。污染清理共删 15 行（history 363/364、health 236/237、
  technology 1235/1236、finance 14186/14187、social 41123/41124-41127 及其 metrics/snapshot）。
- **三个已修根因**（全有回归测试）：①`match_and_write` 缺 `con.commit()` → 登记回滚循环 +
  注册行对 `write_metric_rows` 不可见被静默拒收；②登记写在逐类目匹配循环里 → 每个类目库各建
  一份自有行 + 后台双行(feed 行/弹层行两个 url)同轮再各造一份；③后台 feed 多条响应把 4 篇重复
  成 24 条 → `dedup_captured_items` 按 host+path 去查询参数判重、字段取先到非 None 并集，
  无 url 条目不许仅凭标题并库。判重口径：url/item_id/**同名(含目标类目自身)**任一命中即不登记。
- **G2 核对**：`mine_keywords` R28 已接 `ttxt/analysis/analyze.py:42`；social 库 `origin='mined'`
  35 词全 `active` 且 `keyword_performance.collected` 20–138 真实非空；24h `crawl_jobs` mined
  入队 0（mined 词靠来源轮换采集，auto_feedback 只服务零覆盖 gap 词）。
- **门禁四数字**：日进量 2286（24h `fetched_at`）；窗口成功率 133/175≈76%（`crawl.done`/
  `crawl.browser_lock.acquired`）；favorites 覆盖=自有作品 2/2 全量；mined 35 词生效。
- **测试**：`10_tests/test_r60_account_metrics.py` **16 passed**（弹层解析/合并/去重/登记判重/
  白名单/调度接线全链）。全量 suite 本轮未重跑，不得声称全绿。
- **CLI**：`account-fetch [--category X] [--apply] [--force]`；`--force` 只供人工排障，
  日志留 `account.fetch.force_override` 审计，调度器永不传。本轮排障共 6 次 force。
- **工作区**：提交推送后 `git status` 应仅剩本文件；`03_intelligence/account_metrics/` 证据 JSON
  按 housekeep 7 天保留。

## 红线（接手后继续有效）

只读自有头条账号，不碰他人数据；账号 profile / cookie / state 不入 git（`02_crawler/.account/`、
`00_control/account_metrics_state.json`）；两小时硬下限不得下调；`--force` 不得进调度；
调度器绝不触发写作/发布/模型；800 字门禁与规则 57/64 不得放宽；127.0.0.1:8080 Strata
(qwen3.8-flash-next-iq3_s, 147456 ctx) 配置/端口/量化不动；DeepSeek 评审密钥只允许
`.secrets/deepseek_api_key`；每完成一项独立提交并推送 origin/main（已预授权），推送失败如实报告并用 `git ls-remote` 核验。

## 还欠着的账

G1/G2/G3/G6 已全部闭合（见上）。R61：写作批次复验、样本回流 few-shot、黄金集重判。
旧账：T-02 第二来源、T-16 展现量/标签覆盖、T-17 管线增量化、health 标定（26<30）。
新增小账：自有作品登记类目由 `account.own_work_category` 指定（现 social），若日后发布类目
变化需改配置；同名多篇的自有作品不自动登记（宁缺毋滥），走后台导出备份通道 c。

DONE
