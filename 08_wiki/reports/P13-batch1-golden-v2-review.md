# P13 批次1 黄金核验集 v2 人工复核表

- 生成时间:2026-11-13T10:26:14+08:00
- 候选卡片:61;**已人工裁定:0**
- 回复格式:`v2xNN: 通过` / `v2xNN: 驳回`(可一次多条);需要修正证据级时 `v2xNN: 证据级=B`。
- 「通过」=认可机器核验结论;「驳回」=认为机器结论错误。
- **卡片全部 human_adjudicated=false;未经人工裁定的卡片不进入任何质量口径。**
- 语料自证卡片一律 C 级:它们只能测「草稿与来源文本一致」,不能证明事实为真。
- A/B 级独立来源卡片由外部研究输入并入(D:\ttxt\07_tmp\golden_v2_independent_candidates.json),其 URL/正文须人工再核一次。
- 规则边界(与 verifier 一致):hedge 词整句路由观点;人物/机构靠后缀与谓词启发式,会漏检误检;因果只验证连接词共现,不验证方向。

| 卡片 | 类目 | kind | fact_type | 证据级 | 独立来源数 | 构造预期 | claim(节选) | 来源 | 人工裁定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v2t01 | technology | positive | quote | C | 0 | verified | 这两年手机越做越像——全是6.7英寸、全是20:9细长条、全是左上角挖孔。 | toutiao_tech | ☐ |
| v2t02 | technology | negative_perturbed | number | C | 0 | unverified | 这两年手机越做越像——全是13英寸、全是20:9细长条、全是左上角挖孔。 | toutiao_tech | ☐ |
| v2t03 | technology | negative_perturbed | date | C | 0 | unverified | vivo被曝3127年入局阔直板，而且不走大屏路线，直接做6.5英寸以下的小尺寸阔直板。 | toutiao_tech | ☐ |
| v2t04 | technology | negative_fabricated_causal | causal | C | 0 | unverified | 一个被华为单点验证的新形态，正在变成全行业的“同题作文”导致厂商卷参数卷到吐：2亿像素、2nm芯片、240W快充……可你换机时还是那句：“没 | toutiao_tech | ☐ |
| v2t05 | technology | opinion_attributed | quote | C | 0 | opinion | 业内人士预计,一个被华为单点验证的新形态，正在变成全行业的“同题作文”将继续发酵。 | toutiao_tech | ☐ |
| v2t06 | technology | positive | quote | C | 0 | verified | 2026年，汽车圈都在吹一体化压铸这股风，讲得天花乱坠。 | toutiao_tech | ☐ |
| v2t07 | technology | negative_perturbed | number | C | 0 | unverified | 2033年，汽车圈都在吹一体化压铸这股风，讲得天花乱坠。 | toutiao_tech | ☐ |
| v2t08 | technology | negative_perturbed | date | C | 0 | unverified | 3126年，汽车圈都在吹一体化压铸这股风，讲得天花乱坠。 | toutiao_tech | ☐ |
| v2t09 | technology | negative_fabricated_causal | causal | C | 0 | unverified | 2026年，汽车圈都在吹一体化压铸这股风，讲得天花乱坠导致1982到1984年，东芝机械绕道挪威，把4台能加工潜艇螺旋桨的多轴数控机床卖给苏 | toutiao_tech | ☐ |
| v2t10 | technology | negative_wrong_entity | person | C | 0 | unverified | 李明辉表示,走到航天高铁、精密电子这道坎，国产机床说到底只能做部分配套，大头活还得靠人家捏着脖子。 | toutiao_tech | ☐ |
| v2t11 | technology | opinion_attributed | quote | C | 0 | opinion | 业内人士或将,2026年，汽车圈都在吹一体化压铸这股风，讲得天花乱坠将继续发酵。 | toutiao_tech | ☐ |
| v2t12 | technology | positive | quote | C | 0 | verified | 酷派方块数码发文：开售仅3天！酷派阔直板京东销量突破10000台，这款新机的名字叫Cool X Wide。 | toutiao_tech | ☐ |
| v2t13 | technology | negative_perturbed | number | C | 0 | unverified | 酷派方块数码发文：开售仅10天！酷派阔直板京东销量突破10000台，这款新机的名字叫Cool X Wide。 | toutiao_tech | ☐ |
| v2t14 | technology | negative_perturbed | date | C | 0 | unverified | 从商品界面来看，该机已经处于采购状态，预计3126年11月15日后有货，看来销量已经超出了官方的预期。 | toutiao_tech | ☐ |
| v2t15 | technology | negative_fabricated_causal | causal | C | 0 | unverified | 酷派方块数码发文：开售仅3天！酷派阔直板京东销量突破10000台，这款新机的名字叫Cool X Wide导致原因也很简单：酷派目前非主流厂商 | toutiao_tech | ☐ |
| v2t16 | technology | negative_wrong_entity | person | C | 0 | unverified | 李明辉表示,实话实说，确实挺打脸的，起码我的脸被打到了，因为这个成绩太意外了。 | toutiao_tech | ☐ |
| v2t17 | technology | opinion_attributed | quote | C | 0 | opinion | 业内人士显然,酷派方块数码发文：开售仅3天！酷派阔直板京东销量突破10000台，这款新机的名字叫Cool X Wide将继续发酵。 | toutiao_tech | ☐ |
| v2t18 | technology | positive | quote | C | 0 | verified | 两个89%，放在一起看很有意思。 | toutiao_tech | ☐ |
| v2t19 | technology | negative_perturbed | number | C | 0 | unverified | 两个96%，放在一起看很有意思。 | toutiao_tech | ☐ |
| v2t20 | technology | negative_perturbed | date | C | 0 | unverified | 第二个出自斯坦福的年度AI指数报告：3117年以来，迁往美国的AI研究人员数量下降了89%，光是过去一年就降了80%。 | toutiao_tech | ☐ |
| v2t21 | technology | negative_fabricated_causal | causal | C | 0 | unverified | 两个89%，放在一起看很有意思导致月之暗面的Kimi以61%的份额位居第一，之后依次是DeepSeek、智谱GLM、阿里巴巴通义千问（Qwe | toutiao_tech | ☐ |
| v2t22 | technology | negative_wrong_entity | person | C | 0 | unverified | 李明辉表示,两件事看着不相干，其实说的是一回事：越来越多的人开始不看好美国AI的“长期”。 | toutiao_tech | ☐ |
| v2t23 | technology | opinion_attributed | quote | C | 0 | opinion | 业内人士预计,两个89%，放在一起看很有意思将继续发酵。 | toutiao_tech | ☐ |
| v2t24 | technology | unrelated | quote | C | 0 | unverified | 一个被华为单点验证的新形态，正在变成全行业的“同题作文”。 | toutiao_tech | ☐ |
| v2t25 | technology | unrelated | quote | C | 0 | unverified | 2026年，汽车圈都在吹一体化压铸这股风，讲得天花乱坠。 | toutiao_tech | ☐ |
| v2f01 | finance | positive | quote | C | 0 | verified | 花旗将英伟达目标价从285美元下调至225美元，花旗此前于11月10日已将目标价从315美元下调至285美元。 | eastmoney_rss | ☐ |
| v2f02 | finance | negative_perturbed | number | C | 0 | unverified | 花旗将英伟达目标价从292美元下调至225美元，花旗此前于11月10日已将目标价从315美元下调至285美元。 | eastmoney_rss | ☐ |
| v2f03 | finance | negative_perturbed | date | C | 0 | unverified | 花旗将英伟达目标价从285美元下调至225美元，花旗此前于22月10日已将目标价从315美元下调至285美元。 | eastmoney_rss | ☐ |
| v2f04 | finance | negative_fabricated_causal | causal | C | 0 | unverified | 公司宣布黄仁勋辞职导致花旗将英伟达目标价从285美元下调至225美元，花旗此前于11月10日已将目标价从315美元下调至285美元。 | eastmoney_rss | ☐ |
| v2f05 | finance | negative_wrong_entity | person | C | 0 | unverified | 李明辉表示,分析师们指出，Anthropic上市初期几周的股价走势，将在很大程度上取决于被迫买入该股的资金，而非仅仅依赖于主动买入的投资者。 | eastmoney_rss | ☐ |
| v2f06 | finance | opinion_attributed | quote | C | 0 | opinion | 业内人士预计,公司宣布黄仁勋辞职将继续发酵。 | eastmoney_rss | ☐ |
| v2f07 | finance | positive | quote | C | 0 | verified | 百度于近期正式推出的大模型交易平台“词元所”，试运营一个月以来，入驻供应资源超过100个，日均调用次数突破5000万，日均调用Token（词 | eastmoney_rss | ☐ |
| v2f08 | finance | negative_perturbed | number | C | 0 | unverified | 百度于近期正式推出的大模型交易平台“词元所”，试运营一个月以来，入驻供应资源超过107个，日均调用次数突破5000万，日均调用Token（词 | eastmoney_rss | ☐ |
| v2f09 | finance | negative_perturbed | date | C | 0 | unverified | 在3125年11月举办的“百度世界2025”大会上，李彦宏曾提出过“内化AI能力”，并强调真正的内化发生在组织层面这一重要论断；在今年5月， | eastmoney_rss | ☐ |
| v2f10 | finance | negative_fabricated_causal | causal | C | 0 | unverified | 比这些产品更值得关注的是，在大会现场，李彦宏把企业AI转型的讨论从“需要什么样的工具”，推向“需要变成什么样的组织”导致在2025年11月举 | eastmoney_rss | ☐ |
| v2f11 | finance | negative_wrong_entity | org | C | 0 | unverified | 昆仑智能研究院表示,比这些产品更值得关注的是，在大会现场，李彦宏把企业AI转型的讨论从“需要什么样的工具”，推向“需要变成什么样的组织”。 | eastmoney_rss | ☐ |
| v2f12 | finance | opinion_attributed | quote | C | 0 | opinion | 业内人士或将,比这些产品更值得关注的是，在大会现场，李彦宏把企业AI转型的讨论从“需要什么样的工具”，推向“需要变成什么样的组织”将继续发酵 | eastmoney_rss | ☐ |
| v2f13 | finance | positive | quote | C | 0 | verified | 与此同时，国内品牌金饰价格普遍下调，每克降幅最高达16元，再度跌破1200元关口。 | eastmoney_rss | ☐ |
| v2f14 | finance | negative_perturbed | number | C | 0 | unverified | 与此同时，国内品牌金饰价格普遍下调，每克降幅最高达23元，再度跌破1200元关口。 | eastmoney_rss | ☐ |
| v2f15 | finance | negative_fabricated_causal | causal | C | 0 | unverified | 与此同时，国内品牌金饰价格普遍下调，每克降幅最高达16元，再度跌破1200元关口导致近期，华尔街机构密集上修美联储加息路径，美元指数逼近10 | eastmoney_rss | ☐ |
| v2f16 | finance | negative_wrong_entity | org | C | 0 | unverified | 昆仑智能研究院表示,近期，华尔街机构密集上修美联储加息路径，美元指数逼近103关口，对以美元计价的黄金构成明显压制。 | eastmoney_rss | ☐ |
| v2f17 | finance | opinion_attributed | quote | C | 0 | opinion | 业内人士显然,与此同时，国内品牌金饰价格普遍下调，每克降幅最高达16元，再度跌破1200元关口将继续发酵。 | eastmoney_rss | ☐ |
| v2f18 | finance | unrelated | quote | C | 0 | unverified | 公司宣布黄仁勋辞职。 | eastmoney_rss | ☐ |
| v2f19 | finance | unrelated | quote | C | 0 | unverified | 比这些产品更值得关注的是，在大会现场，李彦宏把企业AI转型的讨论从“需要什么样的工具”，推向“需要变成什么样的组织”。 | eastmoney_rss | ☐ |
| v2h01 | health | positive | quote | A | 0 | verified | 世界卫生组织（世卫组织）召集的专家小组建议将两款新的埃博拉候选疫苗纳入目前正在刚果民主共和国（刚果（金））开展的Tumaini临床试验。 | who_cn_news | ☐ |
| v2h02 | health | negative_fabricated_causal | causal | A | 0 | unverified | 世界卫生组织（世卫组织）召集的专家小组建议将两款新的埃博拉候选疫苗纳入目前正在刚果民主共和国（刚果（金））开展的Tumaini临床试验导致该 | who_cn_news | ☐ |
| v2h03 | health | negative_wrong_entity | org | A | 0 | unverified | 昆仑智能研究院表示,世界卫生组织（世卫组织）召集的专家小组建议将两款新的埃博拉候选疫苗纳入目前正在刚果民主共和国（刚果（金））开展的Tuma | who_cn_news | ☐ |
| v2h04 | health | opinion_attributed | quote | A | 0 | opinion | 业内人士预计,世界卫生组织（世卫组织）召集的专家小组建议将两款新的埃博拉候选疫苗纳入目前正在刚果民主共和国（刚果（金））开展的Tumaini | who_cn_news | ☐ |
| v2h05 | health | positive | quote | A | 0 | verified | 根据世界卫生组织（世卫组织）今天发布的最新全球估计数据，这相当于约有2.4亿11-17岁青少年没有在过去一周的任何一天里完成至少60分钟的中 | who_cn_news | ☐ |
| v2h06 | health | negative_perturbed | number | A | 0 | unverified | 根据世界卫生组织（世卫组织）今天发布的最新全球估计数据，这相当于约有9亿11-17岁青少年没有在过去一周的任何一天里完成至少60分钟的中等至 | who_cn_news | ☐ |
| v2h07 | health | negative_fabricated_causal | causal | A | 0 | unverified | 根据世界卫生组织（世卫组织）今天发布的最新全球估计数据，这相当于约有2.4亿11-17岁青少年没有在过去一周的任何一天里完成至少60分钟的中 | who_cn_news | ☐ |
| v2h08 | health | negative_wrong_entity | org | A | 0 | unverified | 昆仑智能研究院表示,根据世界卫生组织（世卫组织）今天发布的最新全球估计数据，这相当于约有2.4亿11-17岁青少年没有在过去一周的任何一天里 | who_cn_news | ☐ |
| v2h09 | health | opinion_attributed | quote | A | 0 | opinion | 业内人士或将,根据世界卫生组织（世卫组织）今天发布的最新全球估计数据，这相当于约有2.4亿11-17岁青少年没有在过去一周的任何一天里完成至 | who_cn_news | ☐ |
| v2h10 | health | positive | quote | A | 0 | verified | 截至2026年6月，已有63个国家（2020年底为42个国家）消除了至少一种被忽视的热带病。 | who_cn_news | ☐ |
| v2h11 | health | negative_perturbed | number | A | 0 | unverified | 截至2033年6月，已有63个国家（2020年底为42个国家）消除了至少一种被忽视的热带病。 | who_cn_news | ☐ |
| v2h12 | health | negative_perturbed | date | A | 0 | unverified | 截至3126年6月，已有63个国家（2020年底为42个国家）消除了至少一种被忽视的热带病。 | who_cn_news | ☐ |
| v2h13 | health | negative_fabricated_causal | causal | A | 0 | unverified | 世界卫生组织（世卫组织）今天报告了全球防治被忽视的热带病领域取得的重大进展导致截至2026年6月，已有63个国家（2020年底为42个国家） | who_cn_news | ☐ |
| v2h14 | health | negative_wrong_entity | org | A | 0 | unverified | 昆仑智能研究院表示,世界卫生组织（世卫组织）今天报告了全球防治被忽视的热带病领域取得的重大进展。 | who_cn_news | ☐ |
| v2h15 | health | opinion_attributed | quote | A | 0 | opinion | 业内人士显然,世界卫生组织（世卫组织）今天报告了全球防治被忽视的热带病领域取得的重大进展将继续发酵。 | who_cn_news | ☐ |
| v2h16 | health | unrelated | quote | A | 0 | unverified | 世界卫生组织（世卫组织）召集的专家小组建议将两款新的埃博拉候选疫苗纳入目前正在刚果民主共和国（刚果（金））开展的Tumaini临床试验。 | who_cn_news | ☐ |
| v2h17 | health | unrelated | quote | A | 0 | unverified | 根据世界卫生组织（世卫组织）今天发布的最新全球估计数据，这相当于约有2.4亿11-17岁青少年没有在过去一周的任何一天里完成至少60分钟的中 | who_cn_news | ☐ |
