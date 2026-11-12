# P12 批次4 黄金核验集人工复核清单(golden_set_v1)

- 生成时间:2026-11-13T00:07:11+08:00(机器预裁,非人工裁定)
- 卡片总数:57;构造预期与机器预裁一致:52/57(仅反映规则与构造意图吻合度)
- **人工裁定数:0。以下每卡片 human_adjudicated=false,必须由用户逐条回复后才是黄金集。**
- 回复格式:`gNNN: 通过` 或 `gNNN: 驳回`(可一次多条)。
- 语义:「通过」=认可机器核验结论;「驳回」=认为机器结论错误。
- 注意:规则只验证「草稿陈述与来源文本一致」,**不是真正的事实核查**。
- 已知不一致来源(如实记录):claim 整句只要出现 hedge 词(可能/或将/或许等)即路由为 opinion,先于事实核验 —— 推测句不做数字/人物核验是设计边界,不是绑定错误。
- 人物/机构抽取依赖后缀与谓词启发式,存在漏检与误检;因果仅验证连接词与绑定引句共现,不验证因果方向。

| 卡片 | 构造类型 | 构造预期 | 机器结论 | 一致 | 缺失维度 | claim(节选) | 来源URL | 人工裁定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| g001 | positive | verified | verified | ✓ | - | 一个被华为单点验证的新形态，正在变成全行业的“同题作文”。 | https://www.toutiao.com/article/7705970580805173796/ | ☐ |
| g002 | positive | verified | verified | ✓ | - | 现在18家大国企几乎全军覆没，痛心 | https://www.toutiao.com/article/7707095896844009993/ | ☐ |
| g003 | positive | verified | verified | ✓ | - | 打脸了，72小时卖了10000台，酷派阔屏新机爆了 | https://www.toutiao.com/article/7706849492141605430/ | ☐ |
| g004 | positive | verified | verified | ✓ | - | 两个89%，放在一起看很有意思。 | https://www.toutiao.com/article/7706085211007746575/ | ☐ |
| g005 | positive | verified | verified | ✓ | - | 11月11日，苹果官网突然宣布， | https://www.toutiao.com/article/7707099307568005682/ | ☐ |
| g006 | positive | verified | opinion | ✗ | - | 凤凰网科技讯 北京时间11月12日，英伟达董事会可能会罢免CEO黄仁勋(Jensen Hua | https://www.toutiao.com/article/7707106918711493135/ | ☐ |
| g007 | positive | verified | verified | ✓ | - | 60个人直接向一个人汇报，英伟达的“去中心化”出了事才露底 | https://www.toutiao.com/article/7695994490426884658/ | ☐ |
| g008 | positive | verified | verified | ✓ | - | 为什么上太空要带一名女航天员？ | https://www.toutiao.com/article/7706932199543358015/ | ☐ |
| g009 | positive | verified | verified | ✓ | - | 在日本，还有人为高昂投入能否收回成本而犹豫；而答案，其实藏在城市脚下几十米深的地方。 | https://www.toutiao.com/article/7706104421939200575/ | ☐ |
| g010 | positive | verified | verified | ✓ | - | 一个行业能否长久立足，核心并不在于技术是否先进，而是看它是否贴合人们实实在在的刚需，是否需要 | https://www.toutiao.com/article/7700287582881546787/ | ☐ |
| g011 | positive | verified | verified | ✓ | - | 打过仗的人都懂一个道理：阵地丢了，很多时候不是因为敌人太强，而是自家后方早就烂了。 | https://www.toutiao.com/article/7705009467955364390/ | ☐ |
| g012 | positive | verified | verified | ✓ | - | 兄弟们，今天是双 11 最后一天了，都买了些什么呀？ | https://www.toutiao.com/article/7707114305468842547/ | ☐ |
| g013 | positive | verified | verified | ✓ | - | 2001年12月31日，一批"大哥大"老用户都有点舍不得。 | https://www.toutiao.com/article/7706793505557742095/ | ☐ |
| g014 | positive | verified | verified | ✓ | - | 11月8日，黄仁勋承认了一段婚外情，董事会随后公开表示对黄仁勋有信心，并宣布启动独立调查。 | https://www.toutiao.com/article/7707284879213330982/ | ☐ |
| g015 | positive | verified | verified | ✓ | - | 开车的老司机都知道，车上四样东西最不能省，轮胎算一个。 | https://www.toutiao.com/article/7706841999491531327/ | ☐ |
| g016 | positive | verified | verified | ✓ | - | 这个专家的核心逻辑，其实就两点。 | https://www.toutiao.com/article/7707098300847112730/ | ☐ |
| g017 | number | unverified | unverified | ✓ | numbers:13.7 | 这两年手机越做越像——全是13.7英寸、全是20:9细长条、全是左上角挖孔。 | https://www.toutiao.com/article/7705970580805173796/ | ☐ |
| g018 | number | unverified | unverified | ✓ | numbers:25家 | 现在25家大国企几乎全军覆没，痛心 | https://www.toutiao.com/article/7707095896844009993/ | ☐ |
| g019 | number | unverified | unverified | ✓ | numbers:79 | 打脸了，79小时卖了10000台，酷派阔屏新机爆了 | https://www.toutiao.com/article/7706849492141605430/ | ☐ |
| g020 | number | unverified | unverified | ✓ | numbers:96% | 两个96%，放在一起看很有意思。 | https://www.toutiao.com/article/7706085211007746575/ | ☐ |
| g021 | number | unverified | unverified | ✓ | numbers:24;dates:24月11日 | 24月11日，苹果官网突然宣布， | https://www.toutiao.com/article/7707099307568005682/ | ☐ |
| g022 | number | unverified | unverified | ✓ | numbers:73 | 73个人直接向一个人汇报，英伟达的“去中心化”出了事才露底 | https://www.toutiao.com/article/7695994490426884658/ | ☐ |
| g023 | number | unverified | unverified | ✓ | numbers:2032;dates:2032年10月 | 2032年10月，全球妇女峰会召开之际，全国妇联兼职副主席、航天员王亚平接受中新社专访，直言 | https://www.toutiao.com/article/7706932199543358015/ | ☐ |
| g024 | number | unverified | unverified | ✓ | numbers:94% | 其实，台湾已有将近一半的电缆埋入地下，台北市的地下化比例高达94%。 | https://www.toutiao.com/article/7706104421939200575/ | ☐ |
| g025 | date | unverified | unverified | ✓ | numbers:2025;dates:2025年 | vivo被曝2025年入局阔直板，而且不走大屏路线，直接做6.5英寸以下的小尺寸阔直板。 | https://www.toutiao.com/article/7705970580805173796/ | ☐ |
| g026 | date | unverified | unverified | ✓ | numbers:2025;dates:2025年 | 2025年，汽车圈都在吹一体化压铸这股风，讲得天花乱坠。 | https://www.toutiao.com/article/7707095896844009993/ | ☐ |
| g027 | date | unverified | unverified | ✓ | numbers:2016;dates:2016年 | 第二个出自斯坦福的年度AI指数报告：2016年以来，迁往美国的AI研究人员数量下降了89%， | https://www.toutiao.com/article/7706085211007746575/ | ☐ |
| g028 | date | unverified | unverified | ✓ | numbers:2025;dates:2025年11月20日 | Apple支持社区将于2025年11月20日永久关闭 | https://www.toutiao.com/article/7707099307568005682/ | ☐ |
| g029 | date | unverified | unverified | ✓ | numbers:2024;dates:2024年3月 | 黄仁勋2024年3月在Lex Fridman播客里亲口说过，他目前没有、也无意制定传统意义上 | https://www.toutiao.com/article/7695994490426884658/ | ☐ |
| g030 | date | unverified | unverified | ✓ | numbers:2023;dates:2023年10月 | 2023年10月，全球妇女峰会召开之际，全国妇联兼职副主席、航天员王亚平接受中新社专访，直言 | https://www.toutiao.com/article/7706932199543358015/ | ☐ |
| g031 | person | unverified | unverified | ✓ | persons:张伟 | 张伟表示,手机圈最大反转：不是折叠屏赢了，是“宽屏直板”把所有人叫醒了 | https://www.toutiao.com/article/7705970580805173796/ | ☐ |
| g032 | person | unverified | unverified | ✓ | persons:李慧敏 | 李慧敏表示,现在18家大国企几乎全军覆没，痛心 | https://www.toutiao.com/article/7707095896844009993/ | ☐ |
| g033 | person | unverified | unverified | ✓ | persons:王建国 | 王建国表示,打脸了，72小时卖了10000台，酷派阔屏新机爆了 | https://www.toutiao.com/article/7706849492141605430/ | ☐ |
| g034 | person | unverified | opinion | ✗ | - | 赵岩表示,他发现：美国最大的王牌，可能毁在自己人手里！ | https://www.toutiao.com/article/7706085211007746575/ | ☐ |
| g035 | person | unverified | unverified | ✓ | persons:陈慕言 | 陈慕言表示,11月11日，苹果官网突然宣布， | https://www.toutiao.com/article/7707099307568005682/ | ☐ |
| g036 | person | unverified | opinion | ✗ | - | 刘子墨表示,凤凰网科技讯 北京时间11月12日，英伟达董事会可能会罢免CEO黄仁勋(Jens | https://www.toutiao.com/article/7707106918711493135/ | ☐ |
| g037 | org | unverified | unverified | ✓ | persons:能研究院;orgs:昆仑智能研究院 | 昆仑智能研究院称,手机圈最大反转：不是折叠屏赢了，是“宽屏直板”把所有人叫醒了 | https://www.toutiao.com/article/7705970580805173796/ | ☐ |
| g038 | org | unverified | unverified | ✓ | persons:能研究院;orgs:昆仑智能研究院 | 昆仑智能研究院称,现在18家大国企几乎全军覆没，痛心 | https://www.toutiao.com/article/7707095896844009993/ | ☐ |
| g039 | org | unverified | unverified | ✓ | persons:能研究院;orgs:昆仑智能研究院 | 昆仑智能研究院称,打脸了，72小时卖了10000台，酷派阔屏新机爆了 | https://www.toutiao.com/article/7706849492141605430/ | ☐ |
| g040 | org | unverified | opinion | ✗ | - | 昆仑智能研究院称,他发现：美国最大的王牌，可能毁在自己人手里！ | https://www.toutiao.com/article/7706085211007746575/ | ☐ |
| g041 | org | unverified | unverified | ✓ | persons:能研究院;orgs:昆仑智能研究院 | 昆仑智能研究院称,11月11日，苹果官网突然宣布， | https://www.toutiao.com/article/7707099307568005682/ | ☐ |
| g042 | causal | unverified | unverified | ✓ | causal:由于 | 由于手机圈最大反转：不是折叠屏赢了，是“宽屏直板”把所有人叫醒了 | https://www.toutiao.com/article/7705970580805173796/ | ☐ |
| g043 | causal | unverified | unverified | ✓ | causal:由于 | 由于11月11日，苹果官网突然宣布， | https://www.toutiao.com/article/7707099307568005682/ | ☐ |
| g044 | causal | unverified | unverified | ✓ | causal:由于 | 由于最近几年，人工智能、自动化设备快速普及，不少岗位都在悄悄发生变化。 | https://www.toutiao.com/article/7700287582881546787/ | ☐ |
| g045 | causal | unverified | unverified | ✓ | causal:由于 | 由于今年双11小米还是挺给力的，除了红米K100系列，小米17 Max也开启了降价促销。 | https://www.toutiao.com/article/7706427598393442867/ | ☐ |
| g046 | causal | unverified | unverified | ✓ | causal:由于 | 由于北京时间11月12日，韩国产业通商资源部在AI半导体 M.AX 联盟下半年总会上发布《K | https://www.toutiao.com/article/7707287338497344040/ | ☐ |
| g047 | causal | unverified | unverified | ✓ | orgs:由于公司;causal:由于 | 由于公司现任执行副总裁兼首席财务官科莱特·克雷斯（Colette Kress）出任临时CEO | http://finance.eastmoney.com/news/1345,202611123929926773.html | ☐ |
| g048 | unrelated | unverified | unverified | ✓ | numbers:一 | 一个被华为单点验证的新形态，正在变成全行业的“同题作文”。 | http://finance.eastmoney.com/news/1345,202611123929926773.html | ☐ |
| g049 | unrelated | unverified | unverified | ✓ | numbers:18家 | 现在18家大国企几乎全军覆没，痛心 | https://www.toutiao.com/article/7705970580805173796/ | ☐ |
| g050 | unrelated | unverified | unverified | ✓ | numbers:72,10000 | 打脸了，72小时卖了10000台，酷派阔屏新机爆了 | https://www.toutiao.com/article/7705970580805173796/ | ☐ |
| g051 | unrelated | unverified | unverified | ✓ | numbers:89%,两,一 | 两个89%，放在一起看很有意思。 | http://finance.eastmoney.com/news/1345,202611123929926773.html | ☐ |
| g052 | unrelated | unverified | unverified | ✓ | numbers:11;dates:11月11日 | 11月11日，苹果官网突然宣布， | https://www.toutiao.com/article/7705970580805173796/ | ☐ |
| g053 | unrelated | unverified | opinion | ✗ | - | 凤凰网科技讯 北京时间11月12日，英伟达董事会可能会罢免CEO黄仁勋(Jensen Hua | https://www.toutiao.com/article/7700287582881546787/ | ☐ |
| g054 | opinion | opinion | opinion | ✓ | - | 我认为,手机圈最大反转：不是折叠屏赢了，是“宽屏直板”把所有人叫醒了 | https://www.toutiao.com/article/7705970580805173796/ | ☐ |
| g055 | opinion | opinion | opinion | ✓ | - | 我认为,作品声明：内容存在故事情节、虚构演绎成分 | https://www.toutiao.com/article/7707095896844009993/ | ☐ |
| g056 | opinion | opinion | opinion | ✓ | - | 我认为,实话实说，确实挺打脸的，起码我的脸被打到了，因为这个成绩太意外了。 | https://www.toutiao.com/article/7706849492141605430/ | ☐ |
| g057 | opinion | opinion | opinion | ✓ | - | 我认为,他发现：美国最大的王牌，可能毁在自己人手里！ | https://www.toutiao.com/article/7706085211007746575/ | ☐ |
