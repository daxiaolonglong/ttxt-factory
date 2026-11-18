# 09 · Web 面板 ttxt/dashboard/（3 个文件 + 前端）

定位：本地只读面板 + 受限任务下发。**写作、发布、模型绝不经面板触发**——在 server 路由层与 api 层双重落实。

## 9.1 server.py — HTTP 服务（约 210 行）

- `ThreadingHTTPServer`，仅绑定 `127.0.0.1`，默认端口 8765（`create_server` L197 / `serve` L201）；`WEB_DIR = 05_dashboard/web`（L15）。启动入口：CLI `ttxt serve`（`__main__.cmd_serve` L705）；`start-dashboard.cmd/.bs` 包装。
- **GET 路由**（`do_GET` L63）：
  - `/`、`/index.html`、`/static/*`（拒 `..` 与绝对路径 L73）
  - `/api/overview`、`/api/categories`、`/api/jobs?limit`、`/api/logs?limit`、`/api/model`、`/api/sources`、`/api/tasks?limit`
  - `/api/category/<slug>/articles?limit&offset&q`、`/keywords`、`/analysis`（slug 经 `store.SLUG_RE` 校验 L96）
  - 异常统一 500 JSON（面板显示真实错误，不吞）
- **POST 守卫**（`do_POST` L164）：只接受 `/api/tasks/*`，其余 405。`_task_allowed()`（L131）四重护栏：
  1. 必须带自定义头 `X-TTXT-Task: 1`（CSRF 防线，前端 app.js 显式配合）
  2. Host 必须在 `_LOOPBACK_HOSTS = {"127.0.0.1","localhost","::1"}`（防 DNS rebinding）
  3. Origin 存在时必须 == `http://<Host>`
  4. `_read_json_body()`（L148）：≤64KB、仅合法 JSON 对象
- **POST 端点仅两个**：`/api/tasks/crawl`（202 回执）→ `api.enqueue_crawl`；`/api/tasks/analyze` → `api.run_analyze_task`；未知任务 404 明示"写作/发布/模型不可下发"（L186-188）。

## 9.2 api.py — 数据接口（约 313 行，全部真实 SQLite/日志，无 mock）

- 只读：`categories()`（L23）、`overview()`（L32，crawl_jobs/model_jobs 计数 + `model_client.health()` + `housekeep.report()` 磁盘 + `store.SCHEMA_VERSION`）、`articles(slug, limit, offset, q)`（L68）、`keywords(slug)`（L92）、`analysis(slug)`（L114，viral tiers/labels、clusters、knowledge_assets、drafts、evaluations）、`jobs(limit)`（L178）、`logs(limit)`（L203，读 `09_logs` 最近 3 个 jsonl 倒序）、`model_status()`（L224，health + `model_queue.stats()`）。
- 下发（P11，注释 L229"绝不能触发写作、发布或模型调用"）：
  - `sources()`（L232，惰性导入 crawler.sources）
  - `task_queue(limit)`（L252，排队/运行 crawl_jobs + `scheduler.status_report()`）
  - `enqueue_crawl(source_id, category)`（L275）：校验来源存在 → `runner.resolve_category` → `find_pending_crawl_job` **去重** → `create_crawl_job(meta={"dispatched_via":"dashboard"})`；note 提示需 `ttxt crawl-queue --run`（或 worker）执行
  - `run_analyze_task(category)`（L296）：slug 合法 + 在配置类目或本地有库 → `analyze.run_analysis(slug)`，回执关键词/反哺/爆款/知识资产计数

## 9.3 前端 05_dashboard/web/

- `index.html`（100 行）：单页应用「头条智能工厂」；类目下拉 + 7 个 tab：系统总览 / 智能采集 / 数据分析 / 智能创作 / 知识进化 / 系统管理 / 动态日志；系统管理 tab 有任务下发条（`btn-enqueue-crawl`、`btn-run-analyze`，旁注"写作、发布、模型绝不经面板触发"）。
- `app.js`：原生 JS 无框架；`fetch(url,{cache:"no-store"})` 读接口；POST 带 `headers:{"Content-Type":"application/json","X-TTXT-Task":"1"}`（L13-16，匹配服务端护栏）；`setInterval` 轮询刷新（日志每 3 秒）。
