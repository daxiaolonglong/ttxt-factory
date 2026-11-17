# 项目真实状态(唯一进度账本)

**这个文件是项目进度的唯一出处。** 谁(包括我)要谈"做到哪了",以此文件为准;不得用提交数、测试数、
轮次编号制造完成感。**P 编号只是历史批次标签,不是进度单位,后一批不能抵扣前一批的欠账。**

三态定义(只有三种,不许发明第四种):

- **已完成**:有代码 + 有测试 + 有**实际运行证据**(库内读数 / 日志 / 回执文件),验收标准全部通过。
- **进行中**:部分完成,但仍有验收项未通过,且欠账写清楚欠在哪。
- **未完成**:没有真实证据、存在阻断,或验收失败。

最后核对:2026-11-17 14:50(同日复核,仍是 T-01…T-14 这份清单),核对方式=逐条读仓库文件 + 库内 SQL 读数 + 度量器现场跑 +
真实 `ttxt write` 生产写作取证,不采信任何报告的自我声明(报告里与实测不符的,以实测为准并在本文件登记差异)。
**本轮同时更正我自己在 13:48 登记的一条假读数**:当时写「`expected_verification` 61 个全是 null」——
那个字段名不存在于 v2 schema,是我自己的审计脚本猜错了字段。真实读数见 T-02 行(已按实测重写)。

---

## 一、核心目标四能力的真实状态

| 能力 | 状态 | 实测证据 | 阻断在哪 / 解除条件 |
|---|---|---|---|
| **能采集**(合规、稳定、可归因) | **已完成** | robots 逐条遵守+限速+重试;去重幂等;断点续采;`crawl_jobs` 状态机;`crawl.enqueued` 带 actor 可归因;P32 逐跳 SSRF;P34 下架台账;P36 profile 跨进程互斥。今日队列 35 done / 0 failed;语料 technology 285 / finance 280 / history 118 / social 83 / health 25 | 解除条件已满足;残余缺陷见 T-13 |
| **能判断**(事实核验、爆款口径) | **进行中**(机器这一半已闭合,人那一半仍为 0) | 度量器现场读数 14:50:`adjudicated_count: 0`、`quality_claim: "NOT_AVAILABLE"`、门禁三条仍全失败(已人工裁定 0 < 50;每类目有效裁定 0 < 10;A/B 级独立证据占比 None < 0.5)。机器侧:`machine_vs_construction` 由 **0.902 → 1.000**(61/61),三条可绕过根因已修并取证(见 T-03) | 阻断 1:真实人工裁定 0 条(T-01,需要你打勾)。阻断 2:来源独立性结构性不达标——`corroborating_urls` **61/61 为空**,tier 分布 **A 17 / B 0 / C 44**,A+B 占比 17/61 = **27.9% < 50%**,即使 50 张裁完 A/B 门禁仍不可达(T-02) |
A+B 占比 17/61 = **27.9% < 50%**,即使 50 张裁完 A/B 门禁仍不可达(T-02)。阻断 3:health / social 无实测样本 ⇒ 爆款口径这两类目定不了(T-07) |
| **能写作**(合格文章持续产出) | **进行中**(链路能跑且门禁真的拦人,但没有 L4 验收) | 成品草稿实测 **3 篇**(technology 2、finance 1)、拒绝文件 4 份(finance 1、health 2、technology 1);今日真实写作两次:14:26 通过(4 条事实全部绑定引句,`verified`)、14:36 **被门禁拒绝**(3 个版本全 rejected,`model_id` 已留痕)。全项目 writing_history **22 行**,`model_id` 非空 **3 行**(旧的 13 行不回填,不伪造);**history / social 两个类目 writing_history 0 行,从未写过** | 阻断:无 L4 验收、无盲测(T-04)、无去模板化、无真实发布表现数据;今日被拒的根因是取材只有 1 篇材料却扛着「50多个国家」这类标题数字 ⇒ 引句绑不上(T-10) |
| **能从反馈持续进化** | **进行中** | 计数自动回流已调度化(P35):实测样本 technology 188 篇 / finance 34 / history 34,快照 204/34/34;爆款标签 275/280/118;关键词反哺自动入队已实测 | 阻断:health 25 篇 **0 全文 / 0 实测**、social 83 篇 **0 实测** ⇒ 两类目标定不了阈值;且**没有任何证据**证明回流的数据改善了判断质量(见 T-10) |

## 二、未完成任务清单(唯一清单;新增任务必须挂在这里,不能消失)

| 编号 | 任务 | 状态 | 实测证据 | 解除条件(怎么才算完) |
|---|---|---|---|---|
| **T-01** | 黄金集裁定(原「人工裁定」;口径已按项目负责人 15:06 决定改为 **AI 裁定**) | **进行中**(第一轮 AI 初审已落库;两版 AI 互相冲突,12 张待重判) | 第一轮 61 行 AI 初审已写入**独立 sidecar** `10_tests/golden/golden_set_v2_ai_adjudication.jsonl`(61/61 有 `ai_adjudicated/ai_verdict/ai_fact_support/ai_confirms_tier/ai_adjudicator/ai_at`,原始 61 行逐字转录在 `scripts/golden_v2_ai_adjudicate.py`,重跑 `--check` 结果一致);度量器已**同时报 `human_adjudicated_count`(仍 0)与 `ai_adjudicated_count`(61)**并写明 AI 达标 ≠ 人工达标,AI 口径不参与退出码(门禁仍退 2);sidecar 内无任何 `human_*` 字段、卡片文件一字节未改(测试钉住)。**读数**:①贴切 46 / 不贴切 15(按纸面标签)、按今日标签重述 51 / 10;②能 10 / 不能 51;③A 17 / C 44(与卡片声明 61/61 一致)。**判据取证**:AI 看的是 2026-11-16 15:12 打印的打勾单,纸面实印短词已抽证(`adjudications/printed_sheet_tokens_20261116.jsonl`);其中 6 张标签已被 T-03 改掉 ⇒ 15 张「不贴切」里 **10 张仍有效、5 张已过期**,过期部分只按 AI 自己写明的应判标签派生并写全理由;纸印「转述」+①贴切+②能 自相矛盾 1 张(`v2h01`,标 `needs_human_review`)。**第二轮(来源标注 chat.deepseek.com)按项目负责人指示未入库、未加第二口径**:只做对照 ⇒ **① 仅一致 49/61,分歧 12 张**;②③ 完全一致。分歧清单与钉死定义的重判模板见 `08_wiki/reports/P13-ai-round2-disputes.md`,取证与口径见 `08_wiki/reports/P13-batch3-golden-ai-adjudication.md` | 两版冲突的 12 张按重判模板重判后才算定稿 —— **第一轮已按钉死口径(schema §6)重判回传并落 `ai_recheck_*` 字段**:10 张伪造归属卡仍判「不贴切/应判对不上」(今日机器仍贴「转述」⇒ 见 T-15),`v2h01`(今日能核实)与 `v2h03`(今日对不上)判贴切;历史 `ai_verdict` 未改写,人工字段仍全空。**chat.deepseek.com 那版的 §6 重判尚未回传**,回传前分歧统计仍是 12 张;`ai_adjudicated_count ≥50`、每类目 ≥10 已满足,**但 A+B 独立证据占比仍 17/61 = 27.9% < 50%(见 T-02)**,故 `quality_claim_ai` 亦为 NOT_AVAILABLE;`human_adjudicated_count` 不因 AI 增长而变,AI 数量永远不得写成人工数量。**批次4 后果**:T-15 修复后这 10 张今日机器标签已变「对不上」,sidecar 里那 10 行的判据随之过期 ⇒ `golden_v2_ai_adjudicate.py` **exit 1 拒绝重生成**(20 条错误只涉及这 10 张,不伪造「按今日标签重述」的理由凑数),需按今日标签重裁一轮才对齐 |
| **T-04** | P12 批次4 **L4 盲测**(Q4→Q3 对照) | **进行中**(口径已由项目负责人定死,待接线) | 项目负责人 15:06 决定:「**L4 使用 DeepSeek-V4.1-Flash-1017 模型裁定,提示词就 6 个字:审评这篇文章**」。接线现状(现场取证):harness 侧确有该路线 `deepseek-account / deepseek-flash` = **DeepSeek-V4.1-Flash**;但 ttxt 用的模型服务(127.0.0.1:8080)`/v1/models` 只返回 `qwen3.8-flash-next-iq3_s`;Strata 两个启动 profile(`iq3_s-144k`、`ud-q4_k_xl-144k`)是**同一款 Qwen3.8-Flash-Next 的不同量化**,不是 DeepSeek;项目 `.secrets` 只有 `github_token`,无 DeepSeek 密钥 ⇒ **写作模型与评审模型目前同源,`same_model_review: true` 仍未解除** | 把 DeepSeek-V4.1-Flash 接成 L4 的独立评审通道(提示词固定 6 字「审评这篇文章」+ 正文,不给评分细则,避免诱导);L4 记录里 `evaluator` 必须 ≠ 写作模型 id,并写进测试:**评审模型与写作模型同源 ⇒ 不得算 L4 通过**。不许为了过门禁把 `same_model_review` 改成 false |
| **T-02** | 黄金卡**来源独立性**不达标(A/B 门禁结构性不可达) | **未完成**(13:48 登记的「expected 全 null」是我说错的字段名,已按实测重写) | 逐卡复核:21 个字段 61/61 齐全;`expected_by_construction` **全部有值**(verified 10 / unverified 41 / opinion 10),`source_excerpt` 61/61 是原文连续子串。**真正欠的是**:`corroborating_urls` **61/61 为空**;`source_independence.tier_counts` = **C 44 / A 17 / B 0** ⇒ A+B 占比 27.9% < 0.5,`human_confirmed_tiers` 空 | 为卡片补**第二来源**(独立域名、可回取、不用 remember 写过的 URL,按 schema §5),把 A+B 拉到 ≥50%;再由人工确认 tier。做不到就如实维持"不能宣称质量达标",不许改门禁分母 |
| **T-03** | 事实核验分级器可绕过根因 | **已完成**(代码+测试+真实运行证据) | 修的是 P13 批次1 §7 点名的两条,加本轮实测暴露的第三条:①裸 hedge 词整句豁免 ⇒ 现只按「明确归属+谓语」豁免,裸词只留 `checks.opinion_markers`;②数字/日期只查来源**全文** ⇒ 现锚定「绑定引句 ±1 句」连续窗口 `anchor_window`,窗口外命中记 `*_outside_quote` 并在 critical 写明「移花接木嫌疑」;③**新发现**:数字锚点按子串包含,`10` 被 `10000台` 吞掉 ⇒ `_anchor_in()` 带数字边界。同一份黄金集旧口径 55/61=0.902 → 新口径 **61/61=1.000**,6 张被绕过的卡(v2h01/v2h02/v2h03/v2h16/v2t13/v2t14)全部转正确、0 张回归;全量测试 **667 passed**;生产侧 `ttxt write` 两次取证见上表。精度侧另修两处误杀(纯中文数量词、泛指机构锚点降为建议项,黄金集两种口径都 61/61) | 已闭合;回归测试 `10_tests/test_p13_batch2_gate_bypass.py`(14 项)钉住,改回去就红。批次3 再加 `10_tests/test_p13_batch3_golden_ai_adjudication.py`(23 项)按卡片钉死结果:`v2t13` 必须缺 `numbers:10`、`v2t14` 必须缺 3126 的数字与日期、`v2h02` 必须缺因果 `导致`、`v2h03` 必须缺人物/机构锚点、`v2h16` 引句必须不能逐字回取 ⇒ 这 5 张一律 `unverified` 且在门禁里产生 critical,`v2h01` 必须 `verified` |
| **T-05** | `writing_history` 版本级回写虚高 + `model_id` 全 NULL | **进行中**(零改表的两项已闭合;第三项等 T-04 接线) | **不需要动表结构就能修的两项已修完**:①版本状态——旧代码 `UPDATE … WHERE draft_id=?` 把最终状态刷到所有版本(现口径按 `(draft_id, version)` 逐版回写,比当前版本更早的版本记 rejected),测试 `test_p13_batch2_history_accounting.py` 3 项钉住;②`model_id`——列本来就存在,写作链路一直传 None,现传 `_model_meta()["served_model"]`,现场取证:14:36 那次真实写作的 3 行都带上了 `model_id="qwen3.8-flash-next-iq3_s"`(全表 22 行中非空 3 行)。**旧的 13 行 NULL 与虚高状态不回填**——当时的真实出稿模型无从考证,编造等于伪造数据。仍欠:status 未分 AUTO_PASSED / INDEPENDENT_REVIEW_PASSED / REJECTED——「独立复核通过」这一态要靠 T-04 的独立评审模型(DeepSeek-V4.1-Flash)接线才能成立,现在没有第二个模型,不许伪造这一态 | 剩下的部分依赖 T-04 接线;若要 per-version 证据入库才真的需要 DDL,那时单独立项再请批准 |
| **T-06** | 无出处断言应当阻断写作通过;补 9 行证据出处 | **未完成** | `evidence_links` 实测:technology **11 行(0 无出处)**、finance 8 行(4 无出处)、health 8 行(5 无出处)、history/social 0 行;无出处合计 9 行,`doctor.db.orphan_references` 报同一事 | 9 行补 `article_id` 或删除断言;并把"断言无出处 ⇒ 不得通过"写进写作门禁(带测试) |
| **T-07** | health / social 语料与实测双缺 | **未完成** | health 25 篇:`completeness='full'` **0 篇**、`article_metrics` **0 行**;social 83 篇:`article_metrics` **0 行**;度量样本 <30 ⇒ 标定一律如实拒绝 | 补可采来源把 health 全文补齐;为两类目接入能出计数的公开页来源;之后 `ttxt metrics --calibrate` 真跑出阈值 |
| **T-08** | 关键词反哺是否真改善取材(反哺空转风险) | **进行中** | 11-16 实测:technology `效率工具` 3 次反哺抓取(入库 29 篇)后仍 **0 收集** —— 当时已登记为风险,至今未复测 | 复跑同关键词链路,给出「入库后收集/入选」真实计数;空转就改评分或冷却策略(带测试) |
| **T-09** | 自动回流「已改善判断」无任何证据 | **未完成** | 回流本身有证据(见上表);但"变聪明了"只有推测 | 用真实样本做前后对照(同一批关键词/同一批卡,回流前后各测一次),把差值写进账本;做不到就明确写"未证明" |
| **T-10** | 生产写作:成品少、去模板化未做、发布表现无数据 | **未完成** | 草稿文件实测:通过 **technology 2 篇(11-13、11-17 各 1)、finance 1 篇**;拒绝文件 finance 1、health 2、technology 1;demo 类目另有 3 通过 / 2 拒绝(**测试类目,不算生产产出**);history 与 social 无任何草稿。writing_history 里 health 6 版全 rejected(其中 2 版得分 0.85 仍被事实门禁拦下,属正确行为)。今日两次真实写作的教训:14:26 那篇材料只有 1 篇但标题不带数字,4 条事实全部绑上引句通过;14:36 的「50多个国家」选题材料同样只有 1 篇,**标题里的数字来源给不出**,三版全被门禁拦下且理由诚实(引句绑不上 + 数字在引句窗口外)——拦得对,但也暴露**取材密度**是写作批次的前置问题 | 先闭合 T-01/T-06,再谈写作批次(去模板化、按证据密度选题、每篇 ≥2 来源);验收必须含人工裁定与真实发布读数 |
| **T-11** | 崩溃/重启后**租约回收**没有真实样本 | **进行中** | P31 报告 §8.1:当时无在飞任务,未观测;至今仍未观测到一次真实回收 | 制造一次"任务在飞时进程被强杀",观测 `ttxt recover` 是否按 lease 重置并复跑(带日志证据) |
| **T-12** | 浏览器 profile 锁的 POSIX 分支未验证(P36) | **未完成** | 锁在 Windows 实测(含强杀释放);`fcntl.flock` 分支本机无法运行 | 在 Linux/macOS 上跑同一组多进程测试才算闭合 |
| **T-13** | 数据卫生缺陷(4 项既有 WARN) | **未完成** | `doctor`:demo 库未注册;`history` 谎报全文 1 篇;demo 30 行把来源页当文章地址;9 行证据无出处(同 T-06) | 逐项整改(数据保留,不许删语料);修完 `doctor` 不再出现该 WARN |
| **T-14** | `07_tmp` 约 0.9 GB 取证残留;`factory.db` 未迁移 | **进行中** | 磁盘实测项目总量 7.2 GB(限额 20 GB,不紧张) | 保留被报告引用的取证文件,其余按 TTL 清;`factory.db` 迁移单独立项再议 |
| **T-15** | **伪造归属句走「转述」通道,事实门禁根本不跑**(2026-11-17 批次3 建档、批次4 修复) | **已完成**(代码已修 + 已测;质量门禁照旧不过) | 根因(`d2b4a12`):`is_opinion()`(`ttxt/writing/verifier.py:147`)只认字面归属词,`verify_claim()`(:249 附近)据此直接路由 `opinion` ⇒ 10 张卡 `missing_dims` 全空、零 critical,**事实门禁根本不跑**(复现 `07_tmp/t15_before_after.py` 用 `git show HEAD:` 跑旧模块)。修复(只收紧):新增确定性 `attribution_check()`,**第三/四方归属必须在绑定引句 ±1 句窗口内逐字存在**,尺子同数字/日期(只认窗口不认全文,`v2t25` 归属落在窗口外同样不放行);缺失 ⇒ 不得路由 `opinion`、走事实门禁、记 `checks["attribution"]`、产 critical `attribution_not_in_source`、`missing_dims` 至少含 `attribution`;第一人称自述豁免存在性核验;**窗口内找到只等于取得转述资格,不等于已核实**。结果:10/10 → `fact`/`unverified`、各 2 条 critical、逃逸 0 张、**误伤 0 张**(真转述 v2t01「据数码闲聊站爆料」仍 `opinion` 且非 verified;`v2t01/v2h01/v2f01` 仍 verified、`v2t13` 仍缺 `numbers:10`)。答案钥匙由幂等脚本 `scripts/golden_v2_fix_t15_answer_key.py` 按构造真值迁移(10 行 `opinion→unverified`,自证 1271 个未改字段逐字一致、其余 51 张与 sidecar、纸面 token 的 sha256 已钉进测试)。代价(方向均为变严):AI sidecar 对这 10 张判据过期 ⇒ AI 生成器 **exit 1 拒绝重生成**(不伪造重述理由凑数);P29 审计 ①自洽 61→51、R3 矛盾 0→10、`revise` 拒重生成 v2 草案 ⇒ 这 10 行需按今日标签重裁一轮(归 T-01 后续)。度量器 `opinion_routed_cards` 仍报 10(口径是 sidecar 的 `kind` 而非今日标签),逃逸信号改由 `machine_label_drift`(恰好这 10 张)承载。规则 57 已钉死「转述通道不是免检通道」。证据:`08_wiki/reports/P13-batch4-attribution-routing.md`、回归 `10_tests/test_p13_batch4_attribution_routing.py`(33 项);全量 727 passed / 0 failed;度量器退出码仍 2,`quality_claim`/`quality_claim_ai` 仍 `NOT_AVAILABLE` | 真人裁定仍 0/61 —— **修好门禁 ≠ 质量达标**;后续:这 10 行按今日标签重裁一轮 AI 初审(补 `CURRENT_BASIS_REREAD`),让 AI 口径判据重新对齐 |

### 已闭合的历史欠账(有证据,登记在此以免重复劳动)

- 入队归因埋点 `crawl.enqueued`:已在 `ttxt/crawler/store.py:943` 落地,带 `job_id/source/category/actor/keywords`,
  并有 `10_tests/test_p25_observability.py`、`test_p26_keyword_cooldown.py` 钉住 —— **P13 批次4 的这一规格项已闭合**(P13 周报里"至今未落地"的说法对 11-16 之后已不成立,差异在此登记)。
- 采集链路无人值守真跑:P32 取证(线上 Worker 用新代码跑完 health job 91,2.06 秒);今日 35 done / 0 failed。
- 常驻进程托管与停摆可见性(P31):supervisor + 计划任务看门狗 + doctor 的 `ops.supervisor` / `ops.scheduler_heartbeat` 实测能抓出 94 小时停摆那类事故。
- 互动数据自动回流已进调度(P35):`scheduler_state.json` 里 `metrics_fetch_6h` 上次 09:53 `ok`。
- 浏览器 profile 跨进程互斥(P36,仅 Windows):13 项验收 + 生产实测,见 `08_wiki/reports/p36-browser-profile-lock-20261117.md`;POSIX 仍是 T-12。
- **事实门禁三条可绕过根因(T-03)已闭合**:同一份黄金集 0.902 → 1.000,6 张绕过卡纠正、0 回归;提交 `98be3c6`(分级器)+ 本轮精度修订。取证与口径见 T-03 行。
- **写作历史账目(T-05 零改表部分)已闭合**:版本级 status 各归各、`model_id` 真实留痕;回归测试 `10_tests/test_p13_batch2_history_accounting.py`(3 项)。
- **事实门禁不需要重启常驻进程**:grep 证据——`ttxt.writing.verifier` 只被 `ttxt/writing/workflow.py`、`scripts/*`、测试引用,supervisor/worker/scheduler/dashboard 都不加载它(supervisor 明确不承载写作与发布),写作只在 `ttxt write` 手工调用时跑。因此本轮没有做无意义重启;`ttxt doctor` 36 ok / 0 failed,四个常驻进程 + 模型服务仍在运行。

## 三、工作顺序(2026-11-17 由项目负责人定,后续轮次照此执行)

1. **项目真实状态对账** —— 本轮已完成,产物就是本文件。
2. **P13 质量闭环** —— T-03(修分级器)**已闭合**;T-01 的裁定口径已由项目负责人改为 **AI 裁定**(原话见 T-01 行),这条我这边能做;T-02(补第二来源把 A+B 拉到 ≥50%)也是我这边能做。**这两项不再卡在你身上。** 批次3 实测:两版 AI 初审互相冲突(① 只一致 49/61),12 张分歧按 `08_wiki/reports/P13-ai-round2-disputes.md` §4 的钉死定义退回重判 —— 重判由项目负责人拿去执行,我不自行裁决、不合并两版。
3. **P12 L4 验收** —— 口径已定:由 **DeepSeek-V4.1-Flash** 做裁定,提示词固定 6 个字「审评这篇文章」(不给细则,避免诱导)。要做的是把这条通道接成 ttxt 的**独立评审通道**;红线仍然有效:**不得改动现有写作模型(qwen3.8-flash-next-iq3_s)的配置与端口**,只新增一条只读评审通道,并把「评审模型与写作模型同源 ⇒ 不算 L4 通过」写成测试。
4. **数据闭环** —— T-07、T-08、T-09:只做能推动核心目标的数据工作。
5. **生产写作** —— T-10:去模板化、事实准确、爆款判断,最终用人工裁定 + 真实发布读数证明。

每一轮开工前必须回答一句:**"这一轮解决清单里哪一条、推进哪个核心阻断?"** 答不上来就不做。

## 四、五条硬性规则的执行方式(已入 `07_rules/CORE_RULES.md` 规则 54)

1. 未完成的旧任务不因新任务出现而消失 —— 本文件是唯一清单,只有我核到证据才允许改状态。
2. 一个功能提交不等于项目验收通过 —— 提交只对应清单里某一条的局部证据。
3. 后续任务必须说明它解决哪个核心阻断 —— 每轮报告第一行就写编号(T-xx)。
4. 每轮只做能产生实际价值的工作 —— 不新增治理文件;本文件是唯一账本,只更新不复制。
5. 每轮完成代码修改、测试、Diff 审查、备份、验收后直接推送 GitHub;**推送失败如实报告**。
   已核验过两次:`git ls-remote origin refs/heads/main` 与本地 `HEAD` 一致 —— 事实分级器修复的提交是 `98be3c6`,
   本轮(账目 + 精度修订 + 账本更正)的提交紧随其后推送,远端读数写在本轮报告里,不凭记忆填。
   本轮备份:`11_backups/factgate-final-20261117-145939.zip`(22193.8 KB);`ttxt doctor` 36 ok / 0 failed。
