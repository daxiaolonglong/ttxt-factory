# P12 批次4:L4 写作(逐事实证据链 + 确定性核验 + 真实写作验收)

生成时间:2026-11-13T01:10+08:00 · 执行人:自动代理 · 评审人:待用户(仓库记录)

> 重要界定:本批次的「事实核验」是 **逐事实绑定 + 确定性一致性核验,不是真正的事实核查**。
> 引句为来源文章中的连续原文切片(可机器复验),只能证明「陈述与来源文本一致」,
> 不能证明来源本身为真;人名/机构抽取为启发式;因果检查仅验证因果连接词与绑定
> 引句共现,不验证因果方向。任何文档不得将其表述为事实正确性验证。

## 1. 实现内容(本批实际修改)

| 文件 | 变更 |
|---|---|
| `ttxt/writing/verifier.py` | 新增:255 行确定性核验器(无模型调用)。逐 claim 绑定来源文章、在原文中找连续引句(QUOTE_MAX_CHARS=220、重叠度≥0.34)、分维度核验(numbers/dates/persons/orgs/causal)、观点/事实分流、标题回扣检查 |
| `ttxt/writing/workflow.py` | 每轮修订后 **重新抽取并重跑核验**(旧版证据陈旧缺陷);`_save_evidence` 写 9 列含 `source_url`/`checks_json`;`_model_meta` 记录 `same_model_review=true` + 评审局限性声明 + served_model |
| `ttxt/crawler/store.py` | 基线 DDL 含 `evidence_links.source_url/checks_json`;幂等 `migrate_articles_db` |
| `ttxt/writing/export.py` | 导出证据链表:陈述/类型/来源ID/URL/连续引句/offset/重叠度/核验/缺失维度 |
| `ttxt/__main__.py` | `write` 命令守卫:类目不在 `category_registry.active` → 退出码 2、零模型调用(demo 禁止消费正式素材) |
| `ttxt/db.py` | `connect()` 增加 `PRAGMA temp_store=MEMORY`(根因修复,见 §4) |
| `scripts/build_golden_set.py` | 确定性黄金卡片生成器(无模型调用) |
| `10_tests/test_p6_writing.py` | 适配新 schema(claims/证据重跑、evidence 9 列) |
| `10_tests/`(新增) | `test_p12_fact_verifier.py`(16)、`test_p12_db_temp_store.py`(2);全量 **154 passed in 22.55s**(批次3 后为 152) |

## 2. 黄金卡片集(机器一致性,非人工裁定)

- `10_tests/golden/golden_set_v1.json`:57 张卡片,全部 `human_adjudicated=false`、`human_verdict=null`。
- 机器一致率 **52/57(仅机器一致性,不构成任何通过率结论)**;构造预期是设计假定,不是人工结论。
- 5 张不一致已定位为规则边界(如缓和词路由先于事实门禁;person/org 启发式;因果仅共现),记录为规则局限而非绑定错误。
- 等待用户按 `P12-batch4-golden-review.md` 回复 `gNNN: 通过|驳回` 后才产生真正人工裁定数;当前人工裁定 = **0**。

## 3. 真实写作验收(Strata 未动,串行执行)

服务模型全程 `qwen3.8-flash-next-iq3_s`(n_ctx 147456;/v1/models 仅此一个;GPU 15998→16004 MiB 为负载波动,进程未重启/未换参)。

| # | run | 结果 | 关键真实证据 |
|---|---|---|---|
| 1 | technology | **passed** 0.95,修订2轮(3版本) | v1 被确定性门禁拦下:「英伟达因内部治理危机导致股价波动」无引句因果支持(critical);v2 修订中**新注入**「2016年关闭社区」日期错误,重核验再次 critical;v3 删除未证实归因 → 0.95 通过。评分 0.65→0.65→0.95 |
| 2 | finance 首跑 | rejected(**基础设施原因**,非内容判定) | materials=0:FTS 检索索引陈旧(见 §4),4 条断言全 no_source → critical |
| 3 | health 首跑 | rejected(同基础设施原因) | materials=0,5 条全 no_source |
| 4 | finance 重试 | **passed** 0.95,修订0轮(首版即过) | 4/4 evidence verified,引句 verbatim 复验全 True |
| 5 | health 重试 | **rejected(真实内容失败,保留)** | 模型评分 0.85 但三轮均有确定性 critical:「可穿戴设备…」断言在素材中无来源可绑定(原文引句未绑定);**事实门禁凌驾于模型分数之上** |

通过率(仅计内容有效样本 1/4/5):**2/3**;样本 2/3 归类为基础设施缺陷运行,不计入内容通过率。
technology 草稿 `04_writing/drafts/technology/technology-20261113000422-5a990d.md` 含真实证据表:2 个真实 URL(toutiao 文章 #4/#7),3 条引句在 DB 复验 verbatim=True(offset 处逐字相等,重叠度 0.41/0.56/0.48)。

**驳回草稿一律保留**:demo×2、finance×1、health×2,零删除。`published` 全部为 false(从未发布)。

## 4. 本批发现并修复的真实缺陷(含根因证据)

1. **FTS 检索索引陈旧**:批次3 增量入库后未重跑 analyze → technology 索引仅 10/62,finance/health 为 0/47、0/25 → 首跑 materials=0。修复:`analyze` 重建为 62/47/25(demo 45)。流程缺口记录:采集后必须 analyze,当前无自动化衔接(batch5 处理)。
2. **SQLite 临时库无法创建(受限环境)**:`analyze --category technology` 报 `unable to open database file`,定位为 `DELETE FROM articles_fts` 需要磁盘临时 B-tree 而 TMP 不可写(同语句改 `temp_store=MEMORY` 立即成功,复现于 DB 副本;INSERT 正常、仅 DELETE 失败)。修复:`db.connect()` 统一 `PRAGMA temp_store=MEMORY`,兼作「一切文件仅 D:\ttxt」边界防线(temp 不再外溢 C:\Temp);新增回归测试 2 项锁定。
3. **基线 DDL 缺 `cleaned_content`**:`init_articles_db` 建的库没有该列而 `rag`/写作依赖它(生产库靠 `migrate_articles_db` 补齐)。已确认并记录;测试入口统一走 migrate。
4. **写作守卫**:`write --category demo` → 退出码 2、零模型调用(实测);未注册命名空间不得消费正式素材。

## 5. 主题来源诚实标注(关键词反馈回路)

- technology:`keyword_performance` 有分(数据 1.0、人工智能 0.6571、开源 0.4857、效率工具 0.0)→ 选题可标注为关键词反馈驱动。
- finance/health:`keywords`/`keyword_performance` 行数 0 → 其主题**不是**关键词评分驱动,标注为 unscored(人工给定主题),不冒充回路产物。

## 6. 串行与 Strata 证据

- `00_control/system.db.model_jobs`:70 行全部 done,今日 44 行含 started/finished,按时间区间扫描 **零重叠**;测试结束后无 running 残留。
- 评审独立性受限声明已逐版本落库:`evaluation_history.criteria_json` 含 `same_model_review=true` + 局限性文案 + served_model。
- Strata:未停止/未重启/未换模型/未改参;/v1/models 前后均仅 qwen3.8-flash-next-iq3_s。

## 7. 未修异常(如实保留,batch5 处理)

- 测试与生产共用 `09_logs/ttxt-YYYYMMDD.jsonl` 与 `00_control/model_gate` 锁目录(01:06 的 gate.job_start/end(job 1-3)即 pytest 进程写入生产日志;按 DB 分区看仍各自串行)。建议命名空间隔离。
- `model_jobs.category` 64/70 为 NULL。
- **聚类陈旧**:`analyze` 不调用 `cluster_category`(仅 crawl pipeline 调用)→ technology 聚类表停留在 10 簇/62 篇,finance/health 为 0 簇。写作取材走 RAG 不受影响,但聚类/相似文章统计失真。
- 未注册 `demo` 素材仍会进入 clean/analyze;`07_tmp` 未注册;legacy 空 `sources` 表;同日日志轮转可能覆盖证据;评审解析失败与质量失败在指标上仍混淆。

## 8. 阻塞项

- **Q4→Q3 盲测对比:阻塞**。/v1/models 仅一个服务模型,切换权在用户;本批次未执行任何盲测,不产出任何 Q4/Q3 对比数字。
- 黄金集人工裁定等待用户回复。

## 9. 备份与回滚

- 批次4 备份:`11_backups\P12-batch4-20261113-011253.zip`(2088.2 KB,含全部类目 DB 的 SQLite .backup 一致性快照 + 源码/测试/报告);回滚链:P12-batch4 → P12-pre → P11-final → P10-final。
- 保留策略(`disk.backup_keep=7`,既有配置)在新增备份后移除了最旧的 `P9-20261112-130123.zip`(派生备份文件,非原始素材/DB;原始数据从未删除)。
- 原始素材/DB 从未删除;`rejected` 草稿全部保留;Strata 进程从未触碰。
