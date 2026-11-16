# P30 · 逐篇来源身份清单与"第二家独立媒体"口径落地(2026-11-16)

## 一、本轮口径(本人 2026-11-16 决定,逐字保留要点)

- **不把 `chinanews.com.cn` 整体升为 A 级**:"官网发布"不自动等于"该事件的一手来源"。
- 中新网自己记者的报道 = 一个独立媒体报道来源,可参与跨来源比对,但**本身不等于事件的一手原始证据**。
- **明确转载新华社、中国天气网等稿件的文章,不能再被当作第二家独立媒体的独立报道。**
- 只有同一事件由**两个真正独立的来源**分别报道,才考虑构造 B 级佐证卡。
- 不修改 `golden_set_v2.jsonl` 的真人签署状态,不伪造签名;目标仍是超过 50%,不降门槛。

本轮把这条口径变成代码 + 回归测试,不再靠人脑记忆执行。

## 二、新增的代码与测试(先红后绿)

| 文件 | 作用 |
| --- | --- |
| [ttxt/analysis/source_identity.py](../../ttxt/analysis/source_identity.py) | 纯函数判定:页头署名 `来源:xxx`、正文电头 `中新网X月X日电 / 新华社X电`、机构归一化、`own_report / reprint_external / unknown / conflicting_byline / aggregator` 五类身份、`independent()` 独立性判定、`find_pairs()` 带理由的跨机构同题配对 |
| [scripts/source_identity_probe.py](../../scripts/source_identity_probe.py) | 逐篇回文章页取证(逐 URL 过 robots、限速 1.6 秒、只读连库),落盘 JSONL 清单 |
| [scripts/cross_media_evidence.py](../../scripts/cross_media_evidence.py) | 把身份清单接进配对器,全类目跑一遍,成对/被拒都写清理由 |
| [10_tests/test_p30_source_identity.py](../../10_tests/test_p30_source_identity.py) | 14 条回归(红证据 `07_tmp/p30_source_identity/red_evidence.txt`,绿 `green_evidence.txt`) |

判定纪律写进代码注释与测试:**冲突时电头优先、域名让路;身份不明一律不算独立;聚合平台(今日头条自媒体)在血统查清前一律不算独立第二来源。**

## 三、53 篇中新网在库文章逐篇身份(实测清单已落盘)

清单:[03_intelligence/source_identity/chinanews-20261116.jsonl](../source_identity/chinanews-20261116.jsonl)
汇总:[chinanews-20261116.summary.json](../source_identity/chinanews-20261116.summary.json)
取证日志:`07_tmp/p30_source_identity/inventory_run.txt`(2026-11-16 17:46,53/53 全部 HTTP 200,robots 无一条拒绝)

| 身份 | 篇数 |
| --- | --- |
| own_report(中新网自己的报道,页头署名 + 电头一致) | **42** |
| reprint_external(转载稿,真正发布者是别人) | **11** |
| unknown / conflicting_byline | 0 |

11 篇转载稿的真正发布者:央视新闻客户端 6、中央广播电视总台中国之声 2、人民日报 1、中国天气网 1、「浙江宣传」微信公众号 1。

结论与口径一致:**同一域名里混着自有稿和转载稿** —— 抽查 6 篇时看到的 3 篇转载,在整库里是 11/53 ≈ 21%。
这 11 篇既不能算"中新网一手",也不能充当"第二家独立媒体"的独立报道(它们本来就来自别人)。
其中「浙江宣传」微信公众号、「国是直通车」这类公众号是否算合格新闻机构,最终仍由本人判定(`human_confirms_tier` 归人,AI 不代签、不代判)。

## 四、跨媒体同题配对(带血统判定)全类目结果

报告:[03_intelligence/source_identity/cross_media-20261116.json](../source_identity/cross_media-20261116.json)
日志:`07_tmp/p30_source_identity/cross_media.txt`

| 类目 | 在库文章 | 成对候选 | 被拒 |
| --- | --- | --- | --- |
| demo | 32 | 0 | 351(同域名 quotes.toscrape) |
| finance | 106 | **3** | 19 |
| health | 25 | 0 | 8(同域名 who.int) |
| history | 30 | 0 | 1 |
| social | 33 | 0 | 0 |
| technology | 206 | 0 | 38 |

拒因分布:同一域名 393、**来源不独立 15**(例如东方财富两个子站互相"佐证",直接拒)、标题相似度不足 5。
`来源不独立` 这一类正是本人要求剔除的东西,现在有代码在拒,并且会写理由。

## 五、3 组财经候选对的逐页署名复核:今天一张 B 卡都造不出来

清单:[finance-pairs-20261116.jsonl](../source_identity/finance-pairs-20261116.jsonl)
日志:`07_tmp/p30_source_identity/pairs_probe_render.txt`(2026-11-16 17:53)

静态抓取这 4 个页面会被"跳转到 127.0.0.1"的防机器人响应挡住(curl SSRF 保护),改用本机浏览器渲染才拿到页头署名 —— 这已经记进规则。

| 文章 | 页头署名 | 判定 |
| --- | --- | --- |
| 英伟达宣布CEO黄仁勋辞职…(eastmoney) | 财联社 | **转载稿** |
| "黄仁勋风波"催化行情!国产AI芯片份额猛增(eastmoney) | 东方财富研究中心 | 自有报道 |
| 黄仁勋被撤换 英伟达创14年来最长连跌纪录(eastmoney) | 国是直通车 | **转载稿** |
| 黄仁勋传(tmtpost) | 无署名 | unknown |

按口径:`unknown` 不得假定独立 ⇒ 3 组候选**全部不成立**,本轮**新增 B 级卡 0 张**。
这是"该修就修"的方向:先把造假第二来源的通道堵死,再去攒真的第二来源。

## 六、A/B 覆盖率重算(门槛不降)

- 黄金集 61 张不变:A 17 / **B 0** / C 44;A/B 覆盖率 **27.9%**,`corroborating_urls` 非空 0 张。
- 要超过 50%:需新增 A/B 卡 X 满足 (17+X)/(61+X) > 0.5 ⇒ **X ≥ 28 张**,一张都不能靠猜。
- 真人签署仍然 **0/61**;① 的 61/61 只是"机器处理标签贴不贴切"的一致性自检,**不是事实准确率**,这个区分保留,不改口径。

## 七、下一轮怎么做(可执行,robots 合规)

1. **每日定时采集**已注册的财经两家(东方财富、钛媒体)+ 科技三家(爱范儿、少数派、阮一峰),同一事件的报道会自然累积;配对器已在,累积出候选后**必须再过逐页署名复核**才允许造 B 卡。
2. **history / social 缺第二家新闻机构**:social 目前只有中新网一个来源;history 只有中新网 + 今日头条(聚合平台不算)。澎湃按 P19 取证结论需要 `__NEXT_DATA__` 结构化解析(哈希类名不可用),下一轮先取 robots 证据再配置,不凭猜测注册。
3. 聚合平台文章在血统查清前一律不参与 B 级构造(已写进 `independent()`)。
4. 需要浏览器渲染才能取到署名的域名:eastmoney.com、tmtpost.com(中新网静态可取)。
5. A 级仍只有 `who_cn_news`,重采 inserted 0 / duplicates 20 —— 想扩 A 级得找 WHO 之外的国际组织一手来源,不能靠中新网整体升级。

## 八、回归与体检

- `10_tests`:**467 passed**(原 453 + P30 新增 14),`--basetemp=D:\ttxt\07_tmp\pytest_basetemp`,测试期间未连接生产库。
- `ttxt doctor`(只读):**34 ok,0 failed**;`scripts/p28_wal_sidecar_cleanup.py` 跑过,无残留边车。
- 凭据目录:`07_tmp/p30_source_identity/`(red/green 证据、逐页取证日志、配对与拒因日志、doctor 输出)。
- `golden_set_v2.jsonl` 未改动一个字节;真人签署状态未触碰。
