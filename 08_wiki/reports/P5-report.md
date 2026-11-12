# P5 验收报告 — Strata 单模型串行闸门

- 阶段:P5
- 日期:2026-11-12
- 结论:**通过**(39 项测试全绿;真实模型任务完成;跨进程并发实测严格串行,毫秒级时间戳零重叠)

## 一、事实(已完成内容)

### 1. 新增模块(`ttxt/model_gate/`)
| 模块 | 职责 |
|---|---|
| `client.py` | Strata OpenAI 兼容客户端:`/health`、`/v1/models`、`/v1/chat/completions`;模型自动发现;可选 API Key;超时/重试;解析 `content` 与 `reasoning_content` |
| `queue.py` | `system.db.model_jobs` 持久 FIFO 队列:enqueue / 原子 claim / finish / 失败重试(3 次后 failed)/ status |
| `gate.py` | 跨进程文件锁(`00_control/model_gate/gate.lock`)+ 单任务串行执行 + 队列排空;锁超时与陈旧锁清理 |

### 2. 关键发现与配置
- 本机模型是**推理型**:默认先输出 `reasoning_content`,短 `max_tokens` 会被推理耗尽导致 `content` 为空(finish_reason=length)。
- 实测 Strata 支持 `chat_template_kwargs: {"enable_thinking": false}`:关闭思考后同一提问仅需 2 个 completion tokens,直接返回 `OK`。
- 已在配置中设 `strata.enable_thinking_default=false`;`--think` 可按需开启;结果同时记录 `reasoning`(截断 4000 字)。

### 3. CLI 新增
- `ttxt model --ping`:健康检查与模型列表
- `ttxt model --status`:队列状态
- `ttxt model --ask "<prompt>" [--max-tokens N] [--category X] [--think]`:提交并串行执行
- `ttxt model --drain`:串行处理队列中全部任务

### 4. 真实模型任务
- 单任务:`只回复一句话:头条系统P5串行闸门验证成功` → 返回原文,**0.78 s**,`finish_reason=stop`
- 跨进程并发实测(两个 CLI 进程同时提交):
  - job 5:`12:39:12.433 → 12:39:13.032`
  - job 6:`12:39:13.240 → 12:39:13.845`
  - **job5 结束 ≤ job6 开始(间隔 208 ms,零重叠)**;锁执行后已释放
- 全部模型任务 `model_jobs` 状态 done,无并发窗口。

## 二、发现的问题与根因

1. **模型返回空内容(首轮实测)**
   - 现象:`finish_reason=length`、`content` 为空、completion_tokens 等于 max_tokens。
   - 根因:模型默认开启推理过程,32/16 tokens 全部消耗在 `reasoning_content`,未进入正式回答。
   - 修改:客户端支持 `enable_thinking` 开关(默认关闭,配置可调);空内容主动抛 `ModelError` 触发队列重试/失败记录,避免静默成功。
2. **秒级时间戳无法证明串行**
   - 现象:并发任务的时间戳落在同一秒,区间检查出现 `end == start`。
   - 修改:`model_jobs` 的创建/开始/结束时间改为毫秒级 ISO;重测后得到 208 ms 间隔、严格不重叠的证据。

## 三、测试结果

| 检查 | 结果 |
|---|---|
| `ttxt selftest` | **39 passed**(P1 10 + P2 9 + P3 6 + P4 8 + P5 6) |
| 覆盖项 | 队列生命周期、失败重试(queued→queued→failed)、文件锁互斥、三线程串行无重叠、健康等待超时、真实模型健康与对话 |
| `ttxt model --ping` | health ok、模型 `qwen3.8-flash-next-iq3_s` |
| 并发实测 | 2 进程 → 严格串行(见上) |
| `ttxt doctor` | 10 PASS / 0 FAIL |

## 四、磁盘变化

- 项目总计 **0.359 GB / 20 GB**;日志 8.6 KB;其余不变
- D: 空闲 **109.6 GB**

## 五、剩余阻塞与下一阶段

- 真实来源清单与授权指标仍待你提供(不影响 P6 使用演示语料)。
- P6:写作闭环(类目 RAG 取材 → 大纲 → 正文 → 事实声明与证据链核验 → 反向评审 → 修改≤2 轮 → 版本保存 → 仅导出待人工发布),全部模型调用经本闸门串行。

## 六、回滚位置

- `D:\ttxt\11_backups\P5-20261112-123924.zip`
- 历史回滚点:P4、P3、P2、P1 快照
