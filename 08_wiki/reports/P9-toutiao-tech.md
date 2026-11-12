# P9 真实来源接入报告 — 今日头条科技频道

- 日期:2026-11-12
- 结论:**成功**。robots 合规前提下采集 10 篇头条科技文章,入库、清洗、聚类、分析、面板全部打通。

## 一、合规检查(采集前实测)

头条 robots.txt(`https://www.toutiao.com/robots.txt`,HTTP 200)关键内容:

```
Disallow: /search
Disallow: /item/
Disallow: /group/
Disallow: /trending/
Disallow: /amos_land_page/
Disallow: /novel/book/
Disallow: /seo-api/
Disallow: source
...
```

- 频道页 `/ch/news_tech/` 与文章页 `/article/<数字>/` **不在禁止列表**,robots 允许。
- 采集器对列表页与每个文章 URL 分别做 robots 检查;本次 robots_skipped=0。
- 使用本机 Edge 正常渲染页面(JS 页面必需),**未登录、未处理验证码、未绕过任何访问控制**;逐篇限速 2 秒。

## 二、实现

| 组件 | 变更 |
|---|---|
| `ttxt/crawler/toutiao.py` | 列表去重(过滤评论锚点)、文章 URL 规范化、正文/作者/时间抽取、逐篇 robots 检查与失败隔离 |
| `ttxt/crawler/fetcher.py` | 新增浏览器渲染模式(Edge `executable_path`,headless,network_idle) |
| `02_crawler/sources.json` | 新增 `toutiao_tech` 来源(render=browser, extractor=toutiao, 限速 2s) |
| `00_control/config.json` | 新增 `browser` 配置段(Edge 路径、超时、headless) |
| 测试 | 新增 4 项适配器单测(列表去重、URL 规范化、正文与元数据、来源配置) |

## 三、采集结果(2026-11-12 12:58–13:00)

- 列表页发现 15 个唯一文章链接;采集前 10 篇正文
- **入库 10 篇,重复 0,单篇失败 0,robots 跳过 0**,耗时 137 秒(平均约 10 秒/篇,浏览器渲染)
- 真实元数据抽样:
  - 《手机圈最大反转…》— 老傅聊数码 — 2026-11-09 12:12 — 1855 字
  - 《太突然!苹果官网宣布:将永久关闭》— 南京广播电视台 — 2026-11-12 10:01 — 1857 字
  - 《马斯克回应聘用黄仁勋:我的荣幸》— 金融界 — 2026-11-12 10:31 — 219 字(短讯)
- 清洗:10 篇 → 10 簇;10 条发布时间全部归一化为 ISO(带 +08:00)
- 分析:4 个种子关键词中"效率工具"零覆盖 → 反哺候选;爆款标签 10 篇全部 `unscored/missing_data`(无授权指标,不臆测);FTS5 索引 10 条
- 面板实测:`/api/categories` 显示 `technology=10`;`/api/category/technology/articles` total=10

## 四、磁盘与资源

- 浏览器使用本机 Edge,未下载 Chromium;Playwright 临时配置目录为系统临时目录(进程结束即清理),项目内无浏览器缓存增长
- 采集期间 Strata 未受任何影响(未重启、未占用 GPU)
- 备份:`11_backups` 已新增 P9 快照(含类目库)

## 五、已知限制

- 频道列表由头条实时推荐,内容构成会变化;部分为自媒体标题党内容,分析时应结合质量评审。
- 浏览器渲染约 8–10 秒/篇;`--limit` 越大耗时线性增加,建议单次 10–20 篇。
- 无授权指标数据,爆款标签保持 `missing_data`。
- 若头条调整 robots 或页面结构,需重新适配;采集器会按 robots 自动跳过禁止路径。

## 六、回滚

- 代码回滚:恢复 `ttxt/crawler/toutiao.py`、`fetcher.py`、`sources.json`、`config.json` 及测试文件
- 数据:删除 `01_categories/technology/articles.db` 中 source='toutiao_tech' 的记录(或从备份恢复)
- 最新快照见 `11_backups/P9-*.zip`
