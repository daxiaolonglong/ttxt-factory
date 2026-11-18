# 08 · 模型闸门 ttxt/model_gate/（4 个文件）

**纪律**：模型调用只经本包。Strata 由用户管理，系统只读取状态与排队调用，绝不启动/停止/重启/切换模型（config.strata.note）。任何时刻只处理一个模型任务（串行）。

## 8.1 client.py — Strata OpenAI 兼容客户端（约 141 行，仅标准库）

- 默认 base_url `http://127.0.0.1:8080`（L27）；密钥从 `api_key_env`（默认 `STRATA_API_KEY`）环境变量取，有则加 `Authorization: Bearer`（`_headers` L30）——无密钥也可用。
- `ModelError(RuntimeError)`（L16）统一错误类型。
- `health()`（L47，GET /health）、`models()`（L51，GET /v1/models）。
- **`wait_ready(timeout_s=180, poll_s=2)`**（L56）：健康等待——轮询 /health 直到 `loaded=true`，超时抛 ModelError。调用前排队等待模型就绪，而不是失败硬重试。
- `resolve_model(...)`（L72）：模块级 `_MODEL_CACHE` 缓存模型 id 列表，取 `[0]`（模型自动发现）。
- **`chat(messages, max_tokens=256, temperature=0.2, model_id=None, enable_thinking=None, ...)`**（L94）：POST /v1/chat/completions，payload 带 `chat_template_kwargs.enable_thinking`；**失败重试**：未显式指定 model_id 时清缓存、重新发现模型后重试一次（L120-128）；空补全抛错；返回 `{model, text, reasoning, finish_reason, usage, elapsed_s}`。

## 8.2 queue.py — 模型任务持久队列（system.db 的 model_jobs 表）

FIFO、崩溃可恢复。表字段：`category, job_type, payload_json, status, attempts, last_error, result_json, started_at, finished_at`。

- `enqueue(job_type, payload, category)`（L16）：INSERT status='queued'，返回 job_id。
- **`claim_next()`**（L29）：原子领取——`BEGIN IMMEDIATE` + SELECT 最早 queued + UPDATE 条件 `status='queued'` 校验 rowcount，attempts+1；多进程不会双认领。
- `finish(job_id, result)`（L54）：status='done' + result_json。
- `record_failure(job_id, error, max_attempts=3)`（L66）：attempts<3 回 'queued'，否则 'failed'。
- `get`（L82）/ `stats()`（L91，status→count）。
- `requeue_stale_running(lease_s=900)`（L100）：running 且 started_at 超租约 ⇒ 重置 queued（崩溃恢复；scheduler `recover` 任务与 gate 入口都会调）。

## 8.3 gate.py — 串行闸门（文件锁 + 队列消费，约 390 行）

- `GATE_LOCK = 00_control/model_gate/gate.lock`（L19）；`DEFAULT_TIMEOUT_S=600`、`DEFAULT_STALE_S=180`、`DEFAULT_HEARTBEAT_S=30`。
- 持有者探活：`_inspect_process/_pid_alive/_holder_alive`（L50-107，Win32 `OpenProcess/GetExitCodeProcess/GetProcessTimes`，ctypes 显式原型 L32-47）——含 **PID 复用检测**（启动时刻容差 2.0s）；查询失败一律保守判活。
- **`class FileLock`**（L124）：`O_CREAT|O_EXCL` 抢占；锁文件固定宽度载荷 `<pid> <epoch> <token> <holder_create_s>`（L154）；`acquire()`（L168）轮询 0.2s + `_clear_stale()`（L240，仅 mtime 超 stale_s **且** 持有者不可信才回收）；持锁期间心跳线程按 heartbeat_s 重写 mtime（`_heartbeat_loop` L221，token 不匹配置 `ownership_lost`）；`release()`（L273）按 token 校验后 unlink；支持 context manager。
- `_default_processor(row)`（L305）：解析 payload_json → `client.wait_ready()` → `client.chat(...)`。
- **对外 API（其他模块调用的入口）**：
  - `submit_and_run(job_type, payload, category=None, processor=None, wait_timeout_s=600, max_jobs=10) -> dict`（L340）：enqueue → 持锁 → `requeue_stale_running` → 循环 `claim_next` 直至跑完自己的 job；若被别的进程处理掉，补一条 "job handled by another process" 记录。
  - `drain(processor=None, wait_timeout_s=600, max_jobs=50) -> dict`（L374）：纯排空队列（CLI `model --drain` / recover 用）。

## 8.4 调用链与例外

```
writing.workflow.default_model_call ─┐
CLI ttxt model --ask ────────────────┼→ gate.submit_and_run / drain
                                     └→ FileLock 串行 → queue(claim/finish/failure) → client.chat → 127.0.0.1:8080
例外（有意设计）：writing/review.py 直连外部独立评审服务（DeepSeek），不经本机闸门；
但密钥纪律（env/.secrets）、served_model 校验与 fail-closed 语义明确。
```
