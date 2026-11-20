# R54 爬虫底座落地报告(2026-11-20)

> 任务:按三个候选仓库官方文档 + 本地实际,把爬虫底座装起来并真实跑通 1 篇通路。架构与影响面清单见 `docs/ARCH_R54.md`;固化约束见 `07_rules/CORE_RULES.md` 规则 62。

## 1. 盘点结论(只读,已核)
- `.venv`:Python 3.12.10;`scrapling==0.5.1`、`playwright==1.65.1`、`patchright==1.64.4`、`curl_cffi==0.17.0`。浏览器二进制本轮之前**缺失**(`%LOCALAPPDATA%\ms-playwright` 不存在)。
- `ttxt/crawler/` 已有完整自研栈(fetcher/robots/dedupe/simhash/clean/store/pipeline/toutiao 适配器),**Scrapling 已在生产链路里**(`fetcher.py` 用 `Fetcher` + `StealthySession`),本轮是补齐运行时 + 验证通路,不是引入新框架。
- 类目库 articles 行数:demo 32 / finance 13,652 / health 157 / history 321 / social 40,674 / technology 904。类目隔离,不建跨类目库。
- `资产库/`(01_语料库 4.0GB / 02_关键词 322MB / 03_RAG 151MB / 04_RAG反向评审 9.7MB / 05_V4.5数据资产分层 125MB)与 `02_crawler/sources.json`、各 db 既有行:全程未触碰。

## 2. 三个候选仓库的最终结论
| 仓库 | 结论 | 依据 |
|---|---|---|
| Scrapling | **已在用,维持为唯一抓取底座** | 官方文档 0.5.x:浏览器统一 patchright 后端;实测 `fetcher.py` 已按此实现 |
| CloakBrowser | **不引入** | 官方 README:每次启动强制 license key(免费档也要邮箱注册,并发限 1)⇒ 违反"无云账号/许可证"硬约束;能力与 StealthySession/patchright 重叠。日后若遇 patchright 过不去的强对抗站,Scrapling `executable_path`/`cdp_url` 可挂任意 Chromium,届时再评 |
| agency-agents | **不引入** | 实为 Claude Code/Cursor 的 agent 角色提示词 .md 库(100+),可执行部分只有安装脚本,对"单本地模型串行"系统无运行时价值;个别 .md 可作提示词素材手工移植 |

## 3. 安装实测(Windows 纯本地,只写 D:\ttxt)
- `scrapling install` 本机不可用:①内部调 `python -m playwright install chromium`,要 mkdir `%LOCALAPPDATA%\ms-playwright`(C 盘,违反"只写 D:\ttxt";沙箱实测 EPERM);②装的是 playwright 1.65.1 对应的 **chromium-1254**,而 scrapling 0.5 隐身浏览器走 patchright,pin 的是 **chromium-1248**。
- 正确做法(已执行):`PLAYWRIGHT_BROWSERS_PATH=D:\ttxt\02_crawler\.browser\ms-playwright` + `TMP/TEMP=D:\ttxt\99_temp\pw_dl`,跑 `python -m patchright install chromium`。落盘:`chromium-1248 / chromium_headless_shell-1248 / ffmpeg-1013 / winldd-1007`,实测 **718MB**(比 ARCH 预估多,full chromium 与 headless shell 都装了;若确认只用其一半可裁,当前保留保稳)。该目录在 `02_crawler/.browser/` 下,已 .gitignore,受 housekeep browser_max_gb 管辖。
- 清理约定:浏览器二进制=一次性安装件,删除目录即完全回滚;`99_temp/pw_dl`(下载暂存)与 `99_temp/r54_probe/`(探针产物+浏览器 profile)整目录可删。C 盘零写入。

## 4. 抓取通路证据(scripts/r54_crawler_probe.py,一次运行四阶段 PASS)
```
[1_minimal_fetcher] status=200 quotes=10 authors=8                    ← Scrapling 官方最小示例(curl_cffi 静态)
[2_toutiao_fetch] url=toutiao.com/article/7709463360156205609/
                  listing_links=15 char_count=2544 author=探影局档案 published_at=2026-11-18 19:17
[3_dedupe] verdict=duplicate  url_hits=[721] hash_hits=[721] min_hamming=3 nearest_id=721
[4_write_probe_table] r54_probe_articles 0->1 行; articles 行数前后均 904(零触碰)
```
- 合规:频道页与文章页逐 URL 过 `url_safety.check_url` + `robots.gate`(均放行);限速 2s;不登录、不绕过访问控制;只抓 1 篇。
- 去重判断对真实数据有效:该篇与既有 id=721 同 URL 同 hash(hamming 3),verdict=duplicate —— 正确拦下,未新增重复。
- 落库红线执行:只新建独立表 `01_categories/technology/articles.db.r54_probe_articles`(1 行留证),回滚 = `DROP TABLE r54_probe_articles`;`articles` 既有行/schema 未动。
- 产物:`99_temp/r54_probe/summary.json`、`listing_evidence.json`(10 条频道链接)、`article_item.json`(标题/作者/发布时间/正文头 800 字)。
- 坑(已进规则 62):头条频道页客户端渲染偶发首帧 0 链接(13:43 一次,13:44 起两次正常)⇒ 探针加"重取≤3 次"兜底;`StealthySession` 关闭方法是 `close()` 不是 `stop()`。

## 5. 影响面核对(与 ARCH_R54 §4 一致,逐项可核)
| 动作 | 位置 | 回滚 |
|---|---|---|
| patchright chromium-1248 安装(718MB) | `02_crawler/.browser/ms-playwright/` | 删目录 |
| 探针脚本(新文件,未改既有代码) | `scripts/r54_crawler_probe.py` | 删文件 |
| 探针产物与 profile | `99_temp/r54_probe/`(gitignore) | 删目录 |
| 独立临时表 | technology/articles.db `r54_probe_articles` | `DROP TABLE r54_probe_articles` |
| 文档/报告/规则 | `docs/ARCH_R54.md`、本报告、`07_rules/CORE_RULES.md` 规则 62 | git revert |
| 未动 | `ttxt/crawler/*`、`articles` 既有行、`02_crawler/sources.json`、`资产库/**`、`00_control` 模型段 | — |

## 6. 下一步(不在本轮范围)
- 把探针里"频道页→文章页→去重→写正式表"的口径合并回 `ttxt/crawler/pipeline` 时,先出影响面清单再动(规则 10);正式入库走既有 `store.insert_items`,自带 URL/hash/simhash 三道闸,不需要新去重实现。
- CloakBrowser / agency-agents 的重新评估触发条件:patchright 过不去某强对抗来源(届时试 `executable_path` 挂载,而不是换框架)。
