
## 十四、P30:逐篇来源身份清单 + 独立性口径落地(17:00-17:55)
- 口径落地成代码:`ttxt/analysis/source_identity.py`(五类身份 + 独立性判定 + 带理由配对)、`scripts/source_identity_probe.py`(逐页取证)、`scripts/cross_media_evidence.py`(全类目配对)。
- 中新网 53 篇逐篇清单:42 自有 / 11 转载稿(央视 6、总台中国之声 2、人民日报 1、中国天气网 1、「浙江宣传」公众号 1),0 身份不明,robots 无拒绝;清单落盘 `03_intelligence/source_identity/`。
- 全类目配对:成对候选 3 组(全在财经 eastmoney×tmtpost),被拒 417 组并写明理由(同一域名 393、来源不独立 15、相似度不足 5)。
- 候选对逐页复核(需浏览器渲染):3 组候选里 2 篇是转载稿、1 篇无署名 ⇒ **本轮可成立 B 卡 0 张**,A/B 覆盖仍 27.9%,距 >50% 还差 ≥28 张真 B 卡。
- 回归 467 passed(新增 14 条)、doctor 34 ok 0 failed、边车已清理、`golden_set_v2.jsonl` 未改一字节、真人签署仍 0/61。
- 报告:`08_wiki/reports/P30-source-identity-and-independence-20261116.md`。
