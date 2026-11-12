# P12 批次2 证据报告 —— 受限 Worker 真正消费采集队列(真实运行)

时间窗口:2026-11-12 22:44–23:03(+08:00)。全部数字来自对生产库 `00_control/system.db`、类目库、Strata `/health`、`nvidia-smi` 的只读读数与真实进程运行,不含任何模拟。

## 一、本批改动(实际修改的文件)

| 文件 | 改动 |
|---|---|
| `ttxt/db.py` | `crawl_jobs` 增加 `owner`/`lease_expires_at` 列;新增 `ensure_crawl_job_columns()` 并在 `connect()` 内幂等执行(见 §四 缺陷记录) |
| `ttxt/crawler/store.py` | `claim_next_crawl_job()`(原子认领+租约+同源互斥+attempts 前置递增)、`renew_lease()`、`recover_stale_crawl_jobs()`(优先按 `lease_expires_at` 判定,旧行回退 `started_at`) |
| `ttxt/crawler/runner.py` | `run_next()`/`run_queued()` 改走认领通道;来源缺失/停用/类目拒绝 → 终态 `aborted` |
| `ttxt/crawler/worker.py` | **新增**:单实例文件锁(`msvcrt.locking`)、停止文件、启动即恢复、只消费 `job_type='crawl'`、不导入任何模型/写作/发布模块 |
| `ttxt/__main__.py` | `worker` 子命令(`--owner --max-jobs --max-runtime-s --idle-stop-s --category --lease-s --stop --clear-stop`) |
| `ttxt/doctor.py` | Strata 健康读数改为 `served_model=`;配置模型名≠服务实际模型名时输出 `WARN strata.model_drift` |
| `02_crawler/sources.json` | `quotes_toscrape` 停用(练习站不进正式素材);`sspai_rss`、`ruanyifeng_atom` 转正 `demo:false`(真实来源) |
| `10_tests/test_p12_worker_lease.py` | **新增 14 个测试**(并发认领唯一胜者、同源互斥、attempts 真实封顶、租约恢复、强杀恢复、锁拒绝、停止文件、AST 验证无模型导入等) |

## 二、真实运行验收清单(全部为实测)

| 验收点 | 实测证据 |
|---|---|
| Worker 消费队列 | `worker --owner p12b2-verify --max-jobs 4`:done=4,`stop_reason=max_jobs` |
| 停用来源→终态 | job 16/17(`quotes_toscrape` 已停用)→ `aborted`,`last_error=来源已停用`,不再重排 |
| 类目守卫 | job 18/19 入队时 category=technology 但当时仍 `demo:true` → `aborted:类目路由拒绝`,**注册库未被演示素材污染** |
| 真实入库 | job 20/21:`sspai_rss` 抓取 10 插入 10、`ruanyifeng_atom` 抓取 3 插入 3,`robots_allowed=true`;technology 库 19→32 |
| 原子认领+租约 | job 22 `attempts=1`(认领即增)、`owner=p12b2-killtest`、`lease_expires_at=22:59:23` 全部落库 |
| 单实例锁 | 运行中第二个实例:`拒绝启动:已有 Worker 持有 worker.lock`,exit 2 |
| 强杀恢复 | 采集第 ~30s 强杀 Worker 进程 → job 22 保持 `running`+租约;租约过期后新实例启动即 `recovered=1, requeued=1`,`last_error=recovered: 进程中断(租约过期, owner=p12b2-killtest)`;锁由 OS 随进程释放,新实例正常获锁 |
| 断点续采 | job 22 重新入队后 `worker --max-jobs 1` 完整跑通:11 页/10 插入,`status=done, attempts=2`(尝试次数如实保留) |
| 停止文件 | `--stop` 后 Worker 立即 `stop_reason=stop_file`,job 22 未被认领(queued 原样);`--clear-stop` 恢复 |
| 零模型调用 | 运行前后 `model_jobs` 均 26(done 26);Strata `/health` 前后完全一致(`model=qwen3.8-flash-next-iq3_s`,context 147456);GPU 前后均 15998/16384 MiB,**未启停/切换 Strata** |
| 同源互斥 | 单元测试 `test_claim_blocks_same_source_allows_other_source`(真实库两线程);生产侧单实例锁使并发窗口不存在,双重防线 |
| job_type 过滤 | 认领 SQL 限定 `job_type='crawl'`;`test_claim_ignores_non_crawl_job_type` |
| 不自动发布 | `worker.py` AST 守卫测试:无任何 model/writing/publish 导入 |

## 三、真实指标汇总(本窗口)

- technology 类目库:**19 → 42 篇**(+23:RSS 13 + 头条 10;头条 10 篇为强杀后续采补上)。
- 队列终态(23:03):`done 15 / aborted 7 / queued 0 / running 0 / failed 0`;`failed=0`(全部错误都走了终态 `aborted` 或已恢复重排)。
- 头条采集一次完整耗时 **91.28s**(11 页,逐篇限速 2s,浏览器会话复用);`article_errors=0`。
- 测试:**134 passed**(120 原有 + 14 新增),改动 `db.connect` 后全套重跑零回归。
- 磁盘:`housekeep` 读数 project_total_gb=0.686 / budget 20.0,within_budget=true。

## 四、缺陷记录(生产运行中发现,已修)

**现象**:首次运行 `worker` 报 `sqlite3.OperationalError: no such column: lease_expires_at`,4 个任务一个都没被认领(exit 1)。
**根因**:租约列的幂等迁移原来只挂在 `init_system_db()` 里,而生产 `system.db` 是旧结构,Worker→store→recover 路径不经过 `init_system_db`。单元测试全部用 tmp 库先 init,所以 134 个测试全绿也拦不住 —— **这正是"单测不等于验收"的实证**。
**修复**:迁移下沉到 `db.connect()` 内的 `ensure_crawl_job_columns()`(凡含 `crawl_jobs` 的库自动补列,不含则跳过,绝不重建表/丢数据),`init_system_db` 仍显式再调一次自证。修复后同一命令直接通过,全套测试重跑 134 绿。

## 五、与体检报告(quality-20261112)异常项的对齐

- §2「幽灵任务/停用来源不终态」→ 本批 `aborted` 终态路径已在生产实测(job 16/17)。
- §2「7 个 queued 任务从未被执行」→ 执行通道(Worker)已上线并实测;面板/调度器仍只入队,Worker 仍需人工启动(本期不做守护进程自启)。
- §9「未注册 demo 类目」→ 本批把 `sspai_rss`/`ruanyifeng_atom` 转正、`quotes_toscrape` 停用;`demo` 命名空间保留,正式素材不再流入注册库。
- 体检报告 §1 模型名与我 22:47 复测读数不一致 → 已在报告 §13 补记(只读复测、不改原文),`doctor` 增加 `served_model` 与 `strata.model_drift` 观测。批次 4 验收时模型名一律引用当时 `/v1/models` 实时值。
- finance/health/history/social 仍零语料 —— 批次 3(来源白名单)处理。

## 六、边界声明

未安装 CloakBrowser;未启停/切换 Strata;未新增模型;未删除任何数据;未自动发布任何内容;所有写入均在 `D:\ttxt` 内;`worker.lock`/`worker_stop` 已加入 `.gitignore`;临时探针脚本在 `99_temp/`(不入库)。
