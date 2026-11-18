# 12 · 依赖关系与配置体系

## 12.1 第三方运行依赖（requirements.txt）

```
scrapling[fetchers]>=0.3     # 唯一声明的运行依赖（底层拉起 curl-cffi、patchright/playwright、browserforge、lxml、camoufox 相关）
```

- 核心模块（config/db/logging/agents/doctor/housekeep/scheduler/supervisor）**只用标准库**；Windows API 经 `msvcrt`/`ctypes.kernel32`（锁、进程探活）。
- 第三方只出现在 crawler 子包：`scrapling`（StealthyFetcher HTTP-first）、`curl_cffi`（TLS 指纹底座）、`patchright`/`playwright`（persistent Edge 浏览器）、`browserforge`、`lxml`。
- **版本漂移防线**：`crawler/stackcheck.py` 用 `importlib.metadata` 记录版本、`inspect` scrapling 参数词表（`allowed_params`），doctor `crawler.stack.*` 离线对账——升级 scrapling 改签名会被体检抓到。
- 开发依赖（requirements-dev.txt）：仅 `pytest>=8.0`。
- 模型服务不在 requirements：Strata（127.0.0.1:8080）由用户管理，项目只经 HTTP 调用。

## 12.2 标准库重点用法

| 用途 | 模块 |
|---|---|
| 存储 | sqlite3（WAL/FTS5） |
| 锁与进程 | msvcrt.locking（supervisor/browser/worker）、O_CREAT\|O_EXCL+ctypes 探活（model_gate） |
| HTTP 兜底 | urllib.request（robots、model client）、email.utils（RFC822 时间） |
| 指纹/哈希 | hashlib（sha256/blake2b） |
| 指纹相似度 | 自实现 simhash64 + hamming（无外部库） |

## 12.3 项目内依赖矩阵（谁 import 谁）

| 模块 | 依赖（项目内） |
|---|---|
| db / logging | config |
| doctor | config, db, housekeep, scheduler, supervisor, crawler.{dedupe,stackcheck,store} |
| housekeep | config, logging, (惰性)db |
| scheduler | config, logging, (惰性)crawler.{runner,sources,store,fetcher,metrics_pages}, (惰性)analysis.{analyze,metrics_import}, (惰性)model_gate.queue |
| supervisor | config, logging, (惰性)crawler.worker.stop_path |
| crawler.* | config；store←cleaner；fetcher←browser_lock,url_safety；robots←url_safety；runner←clean,extract,fetcher,pipeline,robots,sources,store；metrics_pages←fetcher,robots,**analysis.metrics_import**（受控反向） |
| analysis.* | config, db；keywords→(惰性)crawler.keyword_crawl；rag←crawler.simhash.tokenize；viral/keywords 读写类目库 |
| writing.* | config, db, logging, model_gate.{client,gate}, analysis.{rag,keywords,viral}, crawler.{cleaner,store,simhash(verifier)}, writing.{export,prompts,review,verifier} |
| model_gate.* | config, logging, db |
| dashboard.* | config, db, housekeep, crawler.store, model_gate.{client,queue}, (惰性)crawler.{sources,runner}, analysis.analyze, scheduler |
| legacy.* | config, logging, crawler.{store,clean,cleaner,extract,timeparse}, analysis.{metrics_import,rag} |
| __main__ | 上述全部（子命令委托） |

设计要点：跨子包/易环路的依赖一律**惰性 import**（函数体内），CLI 无第三方路径不触发 scrapling 导入。

## 12.4 配置体系（00_control/config.json，version=1）

| 段 | 要点 |
|---|---|
| `strata` | base_url/health/models/chat 路径、request_timeout_s=600、api_key_env=STRATA_API_KEY、"绝不启停模型" note |
| `review` | 独立评审：base_url=https://api.deepseek.com、model_id=deepseek-flash、api_key_env/api_key_file、max_tokens=8000（推理模型）、temperature=0；提示词固定 6 字写在代码里不做配置项 |
| `dashboard` | host=127.0.0.1、port=8765 |
| `paths` | 15 个路径映射（全部项目内，doctor `paths.within_project` 校验） |
| `disk` | project_budget_gb=20、min_free_gb=5、temp/scratch/pip_cache/browser GB、log_max_mb=10、log_keep=7、backup_keep=6、db_max_mb{system 200/article 2048}、rag_max_mb=500、wal_max_mb=64 |
| `categories` | 5 个类目 slug |
| `crawler` | http_timeout_s=30、max_attempts=3、min_delay_s=1.0、respect_robots=true、user_agent |
| `browser` | enabled、executable_path=Edge、headless、timeout_ms=60000、network_idle、page_pool=2、disk_cache_mb=64、**lock_wait_s=600 / lock_poll_s=2.0**（profile 跨进程锁） |
| `model_gate` | poll_interval_s=2、health_timeout_s=5、job_timeout_s=600 |
| `writing` | 门禁参数（见 07 篇）+ material 门禁 note（独立来源按作者账号计的原因） |
| `metrics` | authorized_sources 白名单（4 个）、credibility_rank（5 级）、public_pages（source_id/hosts/limit 20/sleep 2.0） |
| `prediction` | min_samples=5 |
| `pipeline` | delta_enabled=true、fts_delta_full_rebuild_at_changed_rows=2000、fts_delta_ops_budget=20000、cluster_bucket_report_over=500（T-17 增量化，含实测耗时注释） |
| `scheduler` | enabled=true + 5 jobs（metrics_fetch 的 apply/wait_for_queue/lock_wait 参数与排序理由注释） |
| `supervisor` | scheduler_interval_s=300、worker_owner=supervisor、restart_delay 5/max_restarts 10/window 600/poll 1/heartbeat 10/heartbeat_fresh 900 |

配置读取模式统一：各模块带**词表白名单**读取 + 代码内默认值兜底（读失败回落默认，配置坏不拖垮系统）；`config.pipeline` 例外——`pipeline_settings()` 只认词表内键。

## 12.5 环境变量

| 变量 | 用途 |
|---|---|
| `STRATA_API_KEY` | 本机模型服务可选 Bearer 密钥 |
| `DEEPSEEK_API_KEY` | 独立评审密钥（或走 `00_control` 相对的 `.secrets/deepseek_api_key` 文件；密钥不得进日志/回执/提交） |
