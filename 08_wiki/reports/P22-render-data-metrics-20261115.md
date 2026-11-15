# P22 · 头条公开文章页内嵌 RENDER_DATA 计数:阅读/点赞/评论/展现量真值入库(2026-11-15)

> 一句话:头条公开文章页**确实带着本文章的阅读数与展现量真值**(内嵌 `<script id="RENDER_DATA">`),
> P21 判定的"展现量公开页根本没有"是错的。本轮修正抓取与打分口径,technology 类目第一次拿到
> **32 篇真实测得样本**,爆款阈值第一次由真实数据标定,标签从 `unscored:159` 变成
> `verified_viral 3 / potential 9 / normal 11 / low_quality 9 / unscored 127`。

---

## 一、上一轮的错误判断(根因)

P21 报告与 `CORE_RULES` 规则 41 写着:「收藏、展现量公开页根本没有,`impressions` 只能由后台导出补」,
并把爆款判定永久卡在 `no_impressions`。这个结论来自 **DOM 选择器取证**(probe3 只翻了渲染后的可见 DOM),
**没有查页面内嵌的结构化 JSON** —— 取证手段不全就下了"根本没有"的结论。

真实情况(`07_tmp/r24_metrics_probe/probe6_render_data.py` → `probe6_render_data.json`):
头条渲染页里有 `<script id="RENDER_DATA" type="application/json">`,内容是 **percent-encoded 的 JSON**,解码后:

```
data.itemId = 7707118928425763355      ← 与本页 URL 的文章 id 完全一致(归属可证明)
data.itemCell.itemCounter = {
  buryCount: 0, commentCount: 47, diggCount: 437, readCount: 8937, shareCount: 1801,
  showCount: 111790, repinCount: 1927, forwardCount: 15, danmuCount: 0
}
```

交叉验证:`diggCount 437` / `commentCount 47` 与 P21 已核验的工具条 aria-label(**点赞 437 / 47 评论**)逐位相同
⇒ 这份 counter 就是本文章的计数,不是别的文章。

## 二、本轮实现(先红后绿)

| 文件 | 改动 |
| --- | --- |
| `ttxt/crawler/metrics_pages.py` | 新增 `article_id_from_url()`、`_render_payload()`、`_walk_counter_blocks()`、`_apply_counters()`;`extract_public_metrics(page, url=None, detail=None)` 改为 **RENDER_DATA 优先、DOM 文本补空**;`collect_public_metrics` 报告新增 `render_ok / render_mismatch / render_unattributed / render_errors / sanity_rejected` 与逐页 `render_counters`(仅证据) |
| `ttxt/crawler/metrics_pages.py` `_candidates()` | **候选轮转**(本轮实测发现的第二个根因):只按 id 倒序取前 N 会反复采同一批 ⇒ 标定样本永远攒不到 30。改为 `LEFT JOIN article_metrics` + `ORDER BY (captured_at IS NULL) DESC, captured_at ASC, id DESC` |
| `ttxt/analysis/viral.py` | `favorites` 结构性不可得 ⇒ **不得代入 0**,只在可观测率上按权重归一化 `Σ(w·r)/Σ(w)`;`features["metric_fields"]` 记录本次用了哪些字段;`reason` 追加归一化说明 |
| `ttxt/__main__.py` | `--calibrate` 改为**三步**:先按当前指标重算标签 → 标定 → 标定成功后按新阈值再重算(老逻辑标定成功时**不**重算,库里留着旧口径标签;而且标定前不重算导致样本数读成 0);帮助文本同步 |
| `10_tests/test_p22_render_counters.py` | 新增 28 条测试(前 25 条 + 候选轮转 2 条 + CLI 标定 1 条),全部对着**真实页面片段**写,不用编造数据 |

红线(逐条有断言锁住):
1. 内嵌计数只有 `data.itemId == 本页 URL 文章 id` 才许用;对不上 ⇒ 整块丢弃(`render_mismatch`);
   无 URL 上下文 / 解析不出 id ⇒ 一律不用(`render_unattributed`)。`aid` 是应用 id,不进 id 候选键。
2. 只认语义已证的 4 个字段:`readCount→reads`、`diggCount→likes`、`commentCount→comments`、`showCount→impressions`。
   `shareCount / repinCount / forwardCount / buryCount / danmuCount` 语义未证实 ⇒ 只进报告当证据,绝不入库、绝不改名冒充收藏。
3. `showCount < readCount` 物理不可能 ⇒ 拒写 `impressions` 并记 `sanity_rejected`。
4. JSON 里的 0 是**真 0**(存 0);取不到才写 NULL(规则 6:缺失 ≠ 0)。
5. 收藏仍然 NULL(公开页确无来源),不编造、不从点赞反推。

红→绿证据(`07_tmp/r25_render_counters/`):
- 实现前 `p22_red.txt`:**21 failed / 4 passed**(`extract_public_metrics() got an unexpected keyword argument 'url'`、`article_id_from_url` 不存在、tiers 全 `incomplete_data`)。
- 实现后 `p22_green2.txt`:**25 passed**。
- 候选轮转先红:`2 failed, 25 deselected`(picked `[3,2]` / `[4,3]`,应为 `[1,2]` / `[4,1,2]`)→ 实现后绿。
- CLI 标定先红:`1 failed`(`'insufficient_sample' == 'calibrated'`,31 条指标行没先重算标签)→ 实现后绿。
- 全量回归:`p22_full.txt` **392 passed** / `p22_full2.txt` **394 passed** / `p22_full3.txt` **395 passed in 77.17 s (exit 0)**。
  基线 367 → 392 → **395**。

## 三、真实数据:写库前后(technology,真实联网抓取)

写库前用 `sqlite3.Connection.backup()` 取一致性副本再打包(**不是**复制文件,复制会丢 WAL 内容):

| 备份 | 大小 |
| --- | --- |
| `11_backups/P22-pre-public-metrics-20261115-173836.zip` | 3,483,004 B |
| `11_backups/P22-pre-batch2-metrics-20261115-175009.zip` | 3,483,259 B |

**批次 1**(dry-run `07_tmp/r25_render_counters/live_dryrun_technology.txt` → apply `live_apply_technology.txt`,20 页):

| 读数 | 值 |
| --- | --- |
| fetched / fetch_errors / robots_refused | 20 / 0 / 0(host_skipped 14:非头条 host 拒绝) |
| **render_ok / mismatch / unattributed / errors / sanity_rejected** | **20 / 0 / 0 / 0 / 0** |
| written / updated / snapshots / filled_from_null / null_kept / protected | 8 / 12 / 20 / 56 / 0 / 0 |
| 逐页字段 | reads 20/20、impressions 20/20、likes 20/20、comments 20/20、**favorites 0/20(诚实 NULL)** |

交叉核对同一片文章两次读数自然增长:文章 292(`7707118928425763355`)probe 早上 `readCount 8937 / showCount 111790`
→ 本轮 `reads 8972 / impressions 112181`,点赞 437 / 评论 47 不变 ⇒ 数值可信。
CTR(阅读/展现)分布 0.06%–8.94%,例:292 → 8.00%,291 → 1.69%,258 → 0.08%(展现 1115 万、阅读 9307)。

**批次 2**(候选轮转修好后才可能轮到新文章;`live_dryrun_batch2.txt` → `live_apply_batch2.txt`,12 页):
picked ids `257,255,254,252,251,…,244`(**全是没测过的**,证明轮转生效),
`render_ok 12 / mismatch 0 / written 12 / filled_from_null 48`。

**阈值标定**(`live_calibrate_batch2.txt`):第一次 `insufficient_sample 20/30` 老实拒(`calibrate_receipt.txt`);
攒到 32 篇后 ⇒ **`status: calibrated`**,阈值 **`[0.0567, 0.0247, 0.0113]`**(p90/p60/p25 of measured scores,
`sample 32`,`calibrated_at 2026-11-15T17:56:49+08:00`),已持久化到 `articles_meta.viral_calibration`,同一命令内完成新阈值重算标签。

| 状态 | 写库前 | 写库后 |
| --- | --- | --- |
| `article_metrics` | 12 行(只有 likes/comments;reads、impressions 全 NULL) | **32 行**(reads/likes/comments/impressions 各 32 有值;**favorites 0 有值**) |
| `metric_snapshots` | 14 | **46** |
| `viral_labels` 分层 | `{missing_data 147, no_impressions 12}` | `{missing_data 127, measured 32}` |
| `viral_labels` 标签 | `{unscored 159}` | `{unscored 127, normal 11, potential 9, low_quality 9, verified_viral 3}` |
| 标定 | `default_thresholds / sample 0` | **`calibrated / sample 32`**,阈值 p90/p60/p25 |

`reason` 与 `features_json` 留痕(可复算):
`"真实曝光与互动数据 + 校准阈值;收藏指标该来源不可得,按可观测率归一化(未代入 0)"`、
`metric_fields: ["reads","likes","comments","impressions"]`。

## 四、仍未解决 / 边界

1. **`showCount` 的语义**:证据是 counter 命名 + CTR 合理性 + 与工具条数值同源,不是官方口径文档。
   头条创作后台导出(`toutiao_admin_export`,可信度 `verified`,rank 5)是**权威交叉验证**:导入后会覆盖公开页值,
   对不上就暴露 `showCount` 口径偏差 —— 这一步等用户导出后台数据。
2. **收藏 `favorites` 仍然全 NULL**:公开页无来源;只有后台导出能补。
3. `shareCount / repinCount / forwardCount / buryCount / danmuCount` 语义未证实 ⇒ 只做证据,不入库。
4. 其余 5 个类目未采:`finance / health / social / history / demo` 的头条 URL 数为
   0 / 少 / 少 / 少 / 少量;下一轮按同一命令逐类目跑,先看 `render_*` 计数再 `--apply`。
5. `metric_snapshots` 目前只有"两次采集"级别的时序;要判"爆款是在涨还是见顶"需要定时重采(等受限 Worker/Scheduler 启动)。
6. `10_tests` 全绿但 `D:\ttxt\pytest-of-yangl`(历史遗留目录)仍在仓库根,清理需用户批准(规则 22)。

## 五、回滚位置(只描述,不执行)

- 数据:`11_backups/P22-pre-public-metrics-20261115-173836.zip`(批次 1 前)、
  `11_backups/P22-pre-batch2-metrics-20261115-175009.zip`(批次 2 前);解出 `technology/articles.db` 覆盖即回到写库前。
  标定阈值:删 `articles_meta` 里 `key='viral_calibration'` 一行 + 重跑 `ttxt analyze --category technology` 即回默认阈值。
- 代码:Git 提交(本轮提交信息见 `git log`;远端 `github.com/daxiaolonglong/ttxt-factory` main)。
  回退:`git revert <本轮提交>`(只回代码,不回数据)。
- 派生索引不受影响;需要重建跑 `ttxt clean --category technology` + `ttxt analyze --category technology`。

## 六、下一轮待办(按优先级)

1. 其余类目跑同一流程(`--fetch-public` 先看 dry-run 的 `render_ok/mismatch`,再 `--apply`),把 measured 样本推到各类目 ≥30。
2. 用户导出头条创作后台数据 ⇒ `ttxt metrics --category technology --import FILE --source toutiao_admin_export`
   ⇒ 交叉验证 `showCount`、补 `favorites`,可信度 `verified` 覆盖公开页值。
3. 启动受限 Worker/Scheduler(排队任务 54 `toutiao_tech`、63 `who_cn_news` 是别的窗口的活儿,不要乱动),
   让指标定时重采形成真时序。
4. `_element_text` 标题缺陷 + `PROCESSOR_VERSION` 升版(现 `p2-v1`)。
5. C-5 可信度分级落库;`evidence_links.article_id` 9 行 NULL 需要 DDL 批准。
