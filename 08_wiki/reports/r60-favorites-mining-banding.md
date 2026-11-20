# R60 报告：收藏数据打通 + 关键词自增长核对 + 评分区分度

计划：`C:\Users\yangl\.qoder\plans\rolling-meadow-owl.md`。执行时间 2026-11-20～21。
一切结论只认实测证据（库内 SQL 读数、日志事件、回执文件），不采信任何报告的自我声明。

## 结论一句话

**目标 1 里欠了 20 多轮的"收藏"数据，这一轮真正打通并落库了**：收藏不在公开页、也不在
后台列表 feed 里，而在管理页每行"查看数据"弹层的"互动"区。四个步骤全部闭合并推送。

## G1 自有账号后台读数回流（含收藏）—— 已完成

- **取证过程**（三轮证伪后定位）：
  1. mp.toutiao.com 管理页 XHR feed（`profile_v4/manage/content/all`）返回 camelCase 嵌套条目，
     **不含收藏**；列表行文本只有"展现/阅读/点赞/评论"。
  2. `statistics/content` 页无头环境永不触发列表 API，只有壳。
  3. 逐行点击"查看数据"→ 弹层"互动"区实测出现 **`收藏1`**（证据文件
     `03_intelligence/account_metrics/diag-fetch-20261121T031315+0800.json`）。
- **生产实现**：`_collect_stat_modals` 每行重新导航管理页再点开（弹层遮罩会挡住后续点击）；
  `parse_stat_modal` 只在"互动"窗口内解析互动字段（"查看评论"动作链接会伪造评论数，
  窗口外一律 None，宁缺毋滥）；`merge_modal_items` 按标题把弹层收藏并回 feed 条目（feed 供 url）。
- **落库验收**（`--apply`，来源白名单 `toutiao_account=self_reported`，规则 41 等级 rank4）：
  - social 11697《张顺复盘AI视频创业》：`reads=14 favorites=1 likes=0 comments=0 impressions=693`；
  - social 41128《AI改简历做副业》（自有作品登记行，`source=own_account`）：
    `reads=3 favorites=0 impressions=995`；
  - 其余四类目 `own_account=0 acct_rows=0`（无污染）。
- **发布↔库内映射**：`register_pass` 登记与匹配解耦 —— 先扫全部类目库判重（url/item_id/同名
  任一命中即视为已有），全库都没有才登记进目标类目（`account.own_work_category`，默认 social），
  正文列如实标注"正文未随后台 feed 返回,不做伪造"。

### G1 踩掉的三个真 bug（都补了回归测试）

1. **`match_and_write` 从不 commit**：登记插入每轮回滚 → 重复登记 + 注册的行对
   `write_metric_rows` 不可见（`row_errors: 文章不在类目库`），此前几轮"写成功"里含被静默拒收的行。
2. **跨类目注册污染**：登记写在匹配循环里，5 个类目库各建一份自有行（history 363/364、
   health 236/237、technology 1235/1236、finance 14186/14187）+ 同轮内后台双行（feed 行 +
   弹层行两个 url）再各造一份（social 41124-127）。共删除 15 行污染数据，根因修复见
   `d6a17ad`、`edfeb34`。
3. **跨响应重复**：4 篇作品在多条 XHR 响应里重复成 24 条 → `dedup_captured_items`
   按 host+path（去查询参数）并集字段，无 url 的弹层条目不许仅凭标题并库。

### G1 合规护栏（实测有效）

只读自有账号；独立 profile `02_crawler/.account/profile` + 独立浏览器锁；
两次成功取数硬下限 7200s（`--force` 只供人工排障，日志留 `account.fetch.force_override` 审计，
调度器永不传）；调度 job `account_metrics_2h` 已验收后置 `enabled=true`。

## G2 关键词自增长 —— 已存在且在生产生效（本轮核对，未新写）

- `mine_keywords()` R28 已实现并接在 `analyze.py:42` 正式链路；库内 `origin='mined'` 共 **35 条**，
  全部 `status=active` 且 `keyword_performance.collected` 有真实采集数（20～138 不等）。
- mined 词不需要 auto_feedback 入队（该闭环只服务"零覆盖 gap 词"）；mined 词采集数非空即
  证明"从语料长出词 → 词真的去采集"这条链在跑。**mined 词入队数（24h 新增 crawl_jobs）= 0**，
  如实记录：挖词在持续产出，入队走的是来源轮换而不是 gap 补采。

## G3 评分区分度 —— 已闭合（`89c8724`）

绝对阈值 0.5 是误读（score_v2 是连续分）；改分位档 p90/p60/p25 后高分档占比进入区间，
423 篇 technology 白名单外文章标 `backlog_no_metrics`，不参与评分、不造补采。

## 本轮四个门禁数字

| 指标 | 实测 | 说明 |
|---|---|---|
| 日进量（24h 入库） | **2,286 篇** | 远超 30/天红线，基线 32/天的 70 倍 |
| 窗口成功率 | **133/175 ≈ 76%** | crawl.done / browser_lock.acquired（24h 日志） |
| favorites 覆盖率 | **2 行非空**（自有作品全量覆盖） | 自有账号 2 篇作品 = 全部有收藏读数；公开文章无收藏是页面事实，非实现缺口 |
| mined 词 | **35 词全带真实采集数**；24h 新增入队 0 | 挖词链路在生产生效 |

## 提交链（均已推送 origin/main）

`3ba7697` G1 模块 → `f90074e` TMP 钉死 → `303f36f` 真实形态 → `fb0d74e` force 排障口+取证 →
`da070a3` 逐行点击探针 → `9ce51d6` 弹层生产采集+收藏+登记 → `d6a17ad` 登记/匹配解耦+去重 →
`edfeb34` 判重覆盖目标类目+同名即拦。测试 `10_tests/test_r60_account_metrics.py` **16 passed**。

## 遗留（如实）

- 自有作品登记进哪个类目由配置指定（默认 social）；《AI改简历》这类若发布在别的类目需改配置。
- 同名多篇的自有作品不会被登记（宁缺毋滥），需要人工或后台导出通道兜底（备份通道 c 已就绪）。
- R61 账不变：写作批次复验、few-shot 样本回流、黄金集重判。
