# P29 账本:黄金裁定第二版按更正口径录入 + 独立证据覆盖率取证(2026-11-16)

本轮只解决两件事:① 把本人交回的**第二版辅助裁定**按更正后的 ① 口径入账,并把口径写死在代码里;② 把「独立证据覆盖率只有 28%」这件事**量到能做决定的程度**。P28(爆款表现喂回关键词评分)已定稿推送,本轮不重复检查。

## 1. 关键认知:① 这一列之前被读反了(不是 15 行判错)

本人交回时明确:P27 的打印表问的是「**机器使用的处理标签是否贴切**」,不是「机器判断的事实是否正确」。

- 第一版辅助稿 61 行里打了 15 个「不对」,其中 **10 行机器贴的是「转述」**——那是观点/归属路由标签,它并不宣称这句话已被来源证实,按"标签贴切性"就该判「对」。
- 口径现在写死在 `scripts/golden_v2_assist_audit.py:TICK1_SEMANTICS`,并且新增 **R3 闸门**:机器贴的词与卡片构造真值该贴的词**一致却打「不对」**(或不一致却打「对」)= 口径跑偏,`tick1_contradictions` 必须为空。实测 0 条。
- 因此 ① 61/61 的含义是**"机器贴的标签与构造真值自洽"的一致性自检**,不是"事实判断正确率"。这两个词在报告和界面里禁止混用(测试 `test_v2_totals_are_pinned_and_label_definition_is_self_consistent` 把 `tick1_matches_token == 61` 钉死)。

## 2. 第二版 sidecar 读数

文件:`10_tests/golden/adjudications/assist_draft_chatgpt_20261116_v2.jsonl`(61 行)
生成:`python -m scripts.golden_v2_revise_assist_draft`(`RECORDED_AT` 固定 ⇒ 重跑逐字节相同,测试锁幂等)

| 项 | 读数 |
| --- | --- |
| ① 标签贴切 | 对 61 / 不对 0 |
| ② 来源原文是否支持整句话 | 能 10 / 不能 51 |
| ③ 等级 | 同意 61 |
| 相对上一版的改动 | 只有交回的 15 行 `tick1_machine: 不对 → 对`,其余 46 行只新增审计元数据 |
| 机器词与构造真值一致 | 61/61 |
| ① 与贴切性自洽(R3) | 61/61,口径跑偏 0 行 |
| 等级字母与卡片不符 | 0 |

改动可核对:`python -m scripts.golden_v2_assist_audit --draft …_v2.jsonl --diff …20261116.jsonl`
→ `新增 0 / 删除 0 / 三个勾与行号被改动 15 处 / 涉及卡片 61 张`(`07_tmp/p29_adjudication_v2/audit_v2.txt`)。

五处理由**不是走形式**:`verify_explanations()` 拿卡里存的 `source_text` / `source_excerpt` 逐条核对,对不上直接拒绝落盘:

| 卡片 | 核对到的事实 |
| --- | --- |
| v2h01 | 工厂句与 WHO 原文原句逐字一致(正例),机器贴「能核实」 |
| v2t13 | 原文「开售仅3天」,工厂句写成「开售仅10天」 |
| v2h02 | 原文是两个独立事实陈述,工厂句把「导致」焊在中间 |
| v2h03 | 原文主体是世界卫生组织,工厂句换成「昆仑智能研究院」 |
| v2h16 | 工厂句讲埃博拉疫苗,绑定的来源却是青少年身体活动报告 |

## 3. 没被越过的那条线:AI 裁定 ≠ 真人签署

- v2 仍是 **AI 裁定**:`adjudicator_type=ai`、`human_signature=false`;新增 `browsed_by_ai` 只表示"是 AI 打开过来源网页",不等于人工核对。`web_pages_opened` 保持 `False`(真人一次都没打开)。
- `golden_set_v2.jsonl` **一个字节都没改**,门禁照旧:**已真人签署 0/61**、A/B 级独立证据 28%、每类目卡片不足 ⇒ harness 仍 exit 1。
- 新闸门把"伪造签署"也堵住:`--mode human` 看到 `adjudicator_type=ai` 直接拒绝(`…不是签字,是冒充`),即使把 `provenance=human_signed`、`human_signature=True` 伪造就一起拒。测试:`test_ai_draft_cannot_be_written_as_human_adjudication`、`test_forged_human_provenance_still_rejected`。
- 门禁**只能由自然人本人签**过,AI 不能代签,也不能靠再改草案关闭。

## 4. 独立证据覆盖率:第一次量到能决策(`scripts/golden_evidence_coverage.py`,只读)

黄金集 61 张卡:

- 覆盖 **10 个网址 / 3 个域名**:who.int 17 / toutiao.com 25 / eastmoney.com 19;
- 等级:**A 级 17 张、B 级 0 张、C 级 44 张**;A/B 占比 **17/61 = 27.9%**;
- `corroborating_urls` 非空(有独立第二来源)的卡:**0 张**;
- 要过 50% 闸门:需**净增 ≥27 张 A/B 卡**(17+X ≥ (61+X)/2)。

A 级素材已见顶:

- 全系统 A 级来源只有 `who_cn_news` 一个(`build_golden_set_v2.py:PRIMARY_SOURCES`),健康类目 25 篇全在其中;
- 本轮再采 `ttxt crawl --source who_cn_news --category health --limit 20` ⇒ **inserted 0 / duplicates 20**(`07_tmp/p29_adjudication_v2/crawl_health_who.txt`):重复刷同一个频道加不出任何证据。

B 级证据在当前语料里造不出来:

- 432 篇文章里 `cluster_id` **跨域名同题聚类 0 组**;
- 换更宽的尺子(归一化标题字符二元组 Jaccard ≥ 0.30)逐类目扫一遍:demo / finance / health / history / social / technology **各自 0 对**(`07_tmp/p29_adjudication_v2/cross_source.txt`)。
- 结论:**必须有"同一事件从两家互不相关的来源各采一篇"**,这正是关键词反哺入队要驱动的事,而不是把现有来源再刷一遍。

## 5. 下一轮(P30)的账,按便宜程度排

1. **chinanews 一手性小样本取证**:历史 20 + 社会 33 = 53 篇来自 chinanews.com.cn(中国新闻社官网通稿),当前一律按 C 级算,而这两类目在黄金集里**一张卡都没有**。先看稿件署名/通稿标记再决定是否进 `PRIMARY_SOURCES`;等级仍要 `human_confirms_tier` 由本人确认,不许 AI 代判。
2. **黄金集补历史/社会卡片**:目标净增 ≥27 张 A/B 卡,把覆盖率抬过 50%;卡片必须带 `fact_type` / `corroborating_urls` / 构造真值不变,`human_*` 继续留空。
3. **造真正的 B 级卡**:用关键词反哺去找"同事件跨源对"(财经 eastmoney × tmtpost、科技 ifanr × sspai × toutiao),入库后由 `cluster_id` 认题,卡片填 `corroborating_urls`。冷却窗口未到的词不许把 `cooldown_hours` 调 0。
4. **真人签署**:61 行签署表打印后逐条打勾 + 签名 + 日期,交回后才用 `golden_v2_assist_audit.py --mode human` 合并。这一步 AI 不能代劳。
5. P28 遗留不变:technology 新增 20 篇刷 `metrics --fetch-public --apply`;finance/history 需扩采集才过 30 条门槛;health 需 `crawl-enrich`。

## 6. 回归与体检

- 新增测试 `10_tests/test_p29_adjudication_v2.py`(11 条)。红凭据 `07_tmp/p29_adjudication_v2/red_evidence.txt`(`ModuleNotFoundError: scripts.golden_v2_revise_assist_draft`),定稿 `green_evidence.txt` **11 passed**;全量 **453 passed**(见 `07_tmp/p29_adjudication_v2/full_suite.txt`)。
- `ttxt doctor` 只读巡检 **34 ok / 0 failed**;只读取证后按规则 48 收边车(`scripts/p28_wal_sidecar_cleanup.py`,三次读数 `残留边车=无`,行数未变)。
- 纪律入 `07_rules/CORE_RULES.md` **规则 49**;写作评审角色 `06_agents/writer_reviewer.md` 加了两条(三口径固定、AI 不得代签)并按规则 48 重导项目技能(`ttxt agents --export-skills`)。
