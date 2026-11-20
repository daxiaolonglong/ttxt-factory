# ARCH_R54 — 爬虫底座架构与三个候选仓库的取舍（2026-11-20）

> 范围更正版：旧系统（`ttxt/`、`scripts/`、`01_categories/*/articles.db`、`02_crawler/` 等）**全部原样保留**，不删除、不归档、不迁移。`资产库/` 只读盘点。本轮只做：摸底架构 → 装通爬虫底座 → 真实跑通 1 篇抓取通路。

## 0. 现场盘点（已核实）

| 项 | 事实 |
|---|---|
| `.venv` | Python 3.12.10；已装 `scrapling==0.5.1`、`playwright==1.65.1`、`patchright==1.64.4`、`curl_cffi==0.17.0`、`jieba`、`lxml`、`pytest==9.2.0` 等约 30 包 |
| 浏览器二进制 | `%LOCALAPPDATA%\ms-playwright` 为空 → Scrapling/patchright 浏览器**尚未下载**，待 `scrapling install`（≈170–250MB，可接受） |
| `ttxt/crawler/` | 成熟自研栈：`fetcher.py`（HTTP 走 Scrapling `Fetcher`(curl_cffi)，浏览器走 Scrapling `StealthySession`）、`robots.py`（逐 URL robots 核验）、`dedupe.py`+`simhash.py`（去重）、`clean.py/cleaner.py/extract.py`（清洗抽取）、`store.py`（入库）、`pipeline.py/runner.py/worker.py`（调度）、`keyword_crawl.py`（关键词反哺采集）、`metrics_pages.py`（头条互动指标回收）、`toutiao.py`（头条适配器）、`browser_lock.py`/`url_safety.py` |
| `02_crawler/` | 只有 `sources.json`（来源注册表，含 robots 取证注释）与 `.browser/`（本机 Edge profile），代码实际在 `ttxt/crawler/` |
| 类目库（只读） | articles 行数：demo 32 / finance 13,652 / health 157 / history 321 / social 40,674 / technology 904；每库独立，含 `content_hash`、`simhash`、`url_normalized`、FTS、`article_metrics`/`metric_snapshots`/`performance_windows` 等表。库大小 social 1.73GB、finance 468MB |
| `资产库/`（只读） | 01_语料库 4045MB（含 factory.db）、02_关键词 322MB、03_RAG 151MB、04_RAG反向评审 9.7MB、05_V4.5数据资产分层 125MB、README_资产清单.md |
| 模型 | 仅本地 Strata `http://127.0.0.1:8080`，两个 qwen3.8 变体，同一时刻只跑一个；无任何云端兜底 |

**关键发现：Scrapling 不是"待引入"，而是已经在生产链路里**（`fetcher.py` 直接 `from scrapling.fetchers import Fetcher / StealthySession`）。本轮的真正缺口是：浏览器二进制未装、缺一个可复核的"抓取→清洗→去重→入库"最小通路验证。

## 1. 三个候选仓库：解决什么问题、与现栈关系

### Scrapling（D4Vinci/Scrapling，BSD-3，无账号无 key）
- 解决：反爬获取（curl_cffi TLS 指纹伪装 / patchright 隐身 Chromium + Cloudflare Turnstile 求解）、抗改版解析（adaptive 选择器：`auto_save` 存锚点，改版后 `adaptive=True` 自动重定位）、并发爬取。
- 依赖：`Fetcher` 仅需 curl_cffi；浏览器路径依赖 patchright 的 Chromium（`scrapling install` 下载 ≈170–250MB，Windows 原生支持）。
- 0.5.x 要点：`DynamicFetcher/PlayWrightFetcher` 已删除，浏览器统一 patchright 后端；提取用 Scrapy 风格 `get()/getall()`。
- 与现栈关系：**已叠加到位**（现栈用它做 HTTP+浏览器抓取）。不需要再引入第二套抓取框架。
- 本地坑：stealthy 路径用 patchright 的 browsers.json（chromium-1248），playwright 1.65.1 装的是 1254；若报 "Executable doesn't exist" 需补 `python -m patchright install chromium`。

### CloakBrowser（CloakHQ/CloakBrowser）
- 是什么：源码级 C++ 补丁的隐身 Chromium 二进制 + Playwright/Puppeteer 薄封装，Windows x64 原生支持，首启自动下载 ≈200MB。
- **硬阻塞：每次启动强制 license key，免费 key 也要邮箱注册（云账号），并发限 1。** 违反"不允许云账号/许可证"的硬约束。
- 功能与 Scrapling StealthySession（patchright）高度重叠。
- 结论：**不引入**。日后若 patchright 过不了某强对抗站，再评估——Scrapling 的 `executable_path`/`cdp_url` 可挂载任意 Chromium，届时无痛衔接。

### agency-agents（msitarzewski/agency-agents，MIT）
- 是什么：给 Claude Code/Cursor 等 CLI 用的 agent 角色提示词 .md 库（100+），可执行部分只有安装脚本，无 Python 运行时。
- 与"单本地模型串行"系统无运行时价值；个别 .md 可当提示词素材手工移植。
- 结论：**不引入**。

## 2. 减法清单（可以省掉的组件）

| 省掉 | 理由 |
|---|---|
| CloakBrowser | license key/邮箱注册违反硬约束；能力与 patchright 重叠 |
| agency-agents | 提示词库，非可运行框架；不解决本系统任何缺口 |
| 全新独立爬虫框架 | `ttxt/crawler/` 已含 robots/dedupe/clean/store/pipeline 全套，重写=重复劳动+数据污染风险 |
| camoufox / 额外浏览器内核 | 0.5 已统一到 patchright，多装一个 GB 级内核违背硬盘约束 |
| `02_crawler/` 里的旧 Edge profile 依赖（新通路） | 新验证通路走 Scrapling/patchright 自带 Chromium，不挂用户 Edge profile，避免 profile 锁与指纹串扰 |

## 3. 端到端闭环流程图

```
关键词库(02_关键词) ──┐
                      v
关键词反哺采集 keyword_crawl ──> robots 核验(url_safety/robots)
                      v
抓取 fetcher(Scrapling: Fetcher/StealthySession + patchright Chromium)
                      v
清洗抽取 clean/extract/cleaner ──> 去重 dedupe+simhash(content_hash/hamming)
                      v
入库 store ──> 类目库 01_categories/<cat>/articles.db（类目隔离，无跨类目共享库）
                      v
爆款判定 analysis(viral/prediction/评分) + 指标回收 metrics_pages(article_metrics/performance_windows)
                      v
关键词反哺 keyword_performance ──> 回写 02_关键词 / keywords 表
                      v
写作 04_writing(ttxt/writing, 本地 Strata 单模型) ──> RAG(资产库/03_RAG) 取材
                      v
评审 RAG反向评审(资产库/04) ──> 指标回收 T+N ──> 再反哺
```

## 4. 影响面清单

| # | 动作 | 路径 | 类型 | 风险 | 回滚 |
|---|---|---|---|---|---|
| 1 | `scrapling install`（≈170–250MB Chromium→%LOCALAPPDATA%\ms-playwright） | `%LOCALAPPDATA%\ms-playwright` | 新增（盘外系统目录，唯一例外，属浏览器二进制安装既定事实） | 磁盘占用；下载失败留缓存 | 删除 ms-playwright 目录即可 |
| 2 | 可能补 `python -m patchright install chromium` | 同上 | 新增 | 版本对不上导致 stealthy 启动失败 | 同上 |
| 3 | 新增最小验证脚本 | `scripts/r54_crawler_probe.py` | 新增文件 | 无（不改既有代码） | 删文件 |
| 4 | 抓取产物落临时目录 | `99_temp/r54_probe/` | 新增 | 无 | 定期清理约定：`99_temp/r54_probe/` 整目录可删 |
| 5 | 去重/入库验证写**独立临时表** | `01_categories/technology/articles.db` 新表 `r54_probe_articles`（用完保留但隔离，不动 `articles`） | 同库新表 | 低：不触 `articles` 既有行；WAL 模式并发安全 | `DROP TABLE r54_probe_articles` |
| 6 | 文档与报告 | `docs/ARCH_R54.md`、`08_wiki/reports/r54-crawler-base.md`、`07_rules/CORE_RULES.md` 追加段 | 新增/追加 | 无 | git revert |
| 7 | **不动** | `ttxt/crawler/*`、`02_crawler/sources.json`、`articles` 既有行与 schema、`资产库/**`、`00_control` 模型段、取样规则/门禁/阈值 | — | — | — |

红线：只写 `D:\ttxt`（浏览器二进制落系统目录属安装器默认行为，不落其他数据盘）；只本地模型；类目隔离；既有数据不可删改；临时文件与项目文件分离（`99_temp/` 为临时区，`07_tmp/` 为取证区，均可定期清理）。

## 5. 完成标准映射
- P1：本文档落盘，影响面逐条可核 → 已达成。
- P2：`scrapling install` 后跑通官方最小示例 + 真实抓 1 篇头条文章 → 清洗 → 去重判断 → 写 `r54_probe_articles` 临时表，产物在 `99_temp/r54_probe/`。
- P3：结论固化进 `08_wiki/reports/r54-crawler-base.md` 与 `07_rules/CORE_RULES.md`，英文 commit 推送。
