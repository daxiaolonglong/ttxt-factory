# P2 验收报告 — Scrapling 采集部署

- 阶段:P2
- 日期:2026-11-12
- 结论:**通过**(33 条真实条目入库,19 项测试全绿,doctor 10/10,磁盘 0.358 GB / 20 GB)
- 来源策略:经你确认,先用公开测试语料验证链路;真实头条来源清单待你提供后接入。

## 一、事实(已完成内容)

### 1. 依赖安装
- `scrapling[fetchers]` **0.5.1** 安装到 `D:\ttxt\.venv`(含 curl_cffi 0.17.0、playwright 1.65.1、patchright 1.64.4、lxml、cssselect 等)
- pip 缓存固定在 `D:\ttxt\.pip-cache`(95.7 MB,低于 1 GB 上限)
- 浏览器二进制**未安装**(HTTP Fetcher 不需要;按需再评估)

### 2. 采集框架(全部位于 `ttxt/crawler/`)
| 模块 | 职责 |
|---|---|
| `sources.py` | 来源配置加载(`02_crawler/sources.json`,3 个演示来源) |
| `robots.py` | robots.txt 获取/解析(Allow/Disallow 长匹配、Crawl-delay),失败按允许处理并记录 |
| `fetcher.py` | Scrapling `Fetcher.get` 封装,指数退避重试(默认 3 次),5xx 视为失败 |
| `extract.py` | HTML 选择器抽取(条目/标题/作者/来源选择器)、RSS 2.0 与 Atom 解析 |
| `clean.py` | 文本归一化、摘要、URL 规范化(去 tracking 参数/fragment、保留尾斜杠)、sha256 内容哈希 |
| `store.py` | 类目 `articles.db`(schema v1)、`INSERT OR IGNORE` 去重、crawl_jobs 状态记录 |
| `runner.py` | 任务编排:robots 检查 → 限速抓取 → 抽取 → 去重入库 → 任务记录(成功/失败/根因) |

### 3. CLI 新增
- `ttxt sources`:列出来源
- `ttxt crawl --source <id> [--category] [--limit] [--max-pages]`:执行采集,输出 JSON 摘要,失败返回非零

### 4. 演示采集结果(隔离命名空间 `demo`,不污染真实类目库)
| 来源 | 类型 | 页数 | 抽取 | 入库 | 重复 | 耗时 |
|---|---|---|---|---|---|---|
| quotes_toscrape(Scrapling 官方演示站) | html | 2 | 20 | **20** | 0 | 4.2 s |
| sspai_rss(少数派公开 RSS) | rss | 1 | 10 | **10** | 0 | 1.1 s |
| ruanyifeng_atom(阮一峰 Atom) | rss | 1 | 3 | **3** | 0 | 1.0 s |
| **合计** | | | | **33** | 0 | |

- 真实文章元数据抽样(少数派):标题、作者、ISO 发布时间、原文链接均正确入库。
- `crawl_jobs` 记录:**3 done / 1 failed**;失败任务为首次 quotes 采集(根因见下),保留为失败处理证据。
- 去重验证:同内容重复插入返回 `duplicates` 计数(单元测试覆盖)。

## 二、发现的问题与根因

1. **quotes 第 2 页触发 curl SSRF 拦截**
   - 现象:`FetchError: Redirect to internal IP 127.0.0.1 rejected (SSRF protection)`。
   - 根因:URL 规范化去掉尾斜杠,生成 `/page/2`;站点 301 到 `http://…/page/2/`,curl_cffi 对重定向链做 SSRF 保护并拒绝。实测 `/page/2/`(带尾斜杠)直接返回 200/10 条。
   - 修改:`clean.normalize_url` 保留尾斜杠(仅折叠连续斜杠),同步更新单测;重跑后 2 页 20 条全部成功。
2. **任务失败时统计口径不准**
   - 现象:首次失败任务摘要 `items_extracted=0`,实际已抽取首页内容。
   - 根因:计数只在循环结束后写入。
   - 修改:每页抽取后即时更新 `items_extracted`,失败时也能反映真实进度。

## 三、测试结果

| 检查 | 结果 |
|---|---|
| `ttxt selftest` | **19 passed**(P1 10 + P2 9,0.64 s) |
| URL 规范化 / 内容哈希 / robots 长匹配 / HTML 抽取 / RSS / Atom / 去重入库 / 来源配置 | 全部通过 |
| `ttxt doctor` | 10 PASS / 0 FAIL(含 Strata `loaded=True`) |
| `ttxt crawl` 实测 | 成功 3 次;失败 1 次(已修复并重跑成功) |

## 四、磁盘变化

- 项目总计 **0.358 GB / 20 GB**:venv 288 MB、pip 缓存 95.7 MB、demo 库 57 KB、其余 < 0.1 MB。
- D: 空闲 **109.6 GB**(min_free 5 GB)。浏览器目录尚未使用。

## 五、剩余阻塞与下一阶段

- **真实来源清单待你提供**(P2 链路已证明可扩展:新来源只需在 `sources.json` 增加配置)。
- P3 将基于 demo 数据实现清洗管道、时间归一化、simhash 相似聚类与 schema 扩展;真实类目库不受 demo 数据影响。

## 六、回滚位置

- `D:\ttxt\11_backups\P2-20261112-123059.zip`(代码、测试、wiki、配置、system.db 备份)
- 上一回滚点:`11_backups\P1-20261112-122441.zip`
