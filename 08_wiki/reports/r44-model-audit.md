# R44:模型使用违规审计与纠正

生成时间:2026-11-19
结论:**1 处实际调用打到公网云端**(独立评审通道),其余模型调用点全部在本机 `127.0.0.1:8080`;已强制本地并让云端配置显式失败,附真实调用回执。

---

## 1. 审计范围与方法

逐个检查会调用模型的入口,以**实际配置值 + 历史回执 + 配置解析结果**为证,不以变量名推断:

- 写作生成(大纲 / 事实陈述 / 正文 / 改稿)
- 写作内环评审(`_verified_review`)
- 独立评审通道(`ttxt/writing/review.py`,P40 / T-04)
- CLI `ttxt model --ask`
- 评分(`ttxt/analysis/viral.py`、`prediction.py`)
- 关键词抽取/发现
- 子 Agent(`06_agents/`)
- `scripts/` 下的脚本
- Dashboard 探测

## 2. 调用点清单(纠正前)

| # | 调用点 | base_url 实际值 | 模型名 | 来源配置 | 结论 |
|---|---|---|---|---|---|
| 1 | `workflow.write_article` → `default_model_call` → `gate.submit_and_run("writing")` → `client.chat(base_url=None)` | `http://127.0.0.1:8080` | `/v1/models` 解析 = `qwen3.8-flash-next-iq3_s` | `config.json` → `strata.base_url` | **本地** |
| 2 | 写作内环评审 `_verified_review` → 同一个 `model_call` | `http://127.0.0.1:8080` | 同上 | 同上 | **本地** |
| 3 | 独立评审 `review.review()` → `client.chat(base_url=<review>)` | **`https://api.deepseek.com`** | **`deepseek-flash`** | `config.json` → `review.base_url/model_id`;密钥 `DEEPSEEK_API_KEY` 或 `.secrets/deepseek_api_key` | **云端(违规)** |
| 4 | CLI `ttxt model --ask` → `gate.submit_and_run("chat")` | `http://127.0.0.1:8080` | 同上 | `config.json` → `strata` | **本地** |
| 5 | 评分 `viral.score_with_metrics` / `prediction.predict_articles` | — | — | — | **无模型调用**(纯统计) |
| 6 | 关键词抽取/发现 | — | — | — | **无模型调用**(纯统计) |
| 7 | `06_agents/*.md` | — | — | — | **提示词文档,无调用代码** |
| 8 | `scripts/golden_v2_ai_round2_diff.py` | — | — | — | **无调用**;`chat.deepseek.com` 只是用户回传裁定单的来源标注 |
| 9 | `dashboard/api.py` → `model_client` | `http://127.0.0.1:8080` | — | `strata` | 仅 `/health` 探测,不发推理请求 |
| 10 | `scripts/e2e.py` / `scripts/p12b5_acceptance.py` | `127.0.0.1:8080` | — | 硬编码本地 | 探测,不发云端请求 |

## 3. 证据

### 3.1 配置文件(纠正前)

`00_control/config.json` 原 `review` 段:

```json
"review": {
  "enabled": true,
  "base_url": "https://api.deepseek.com",
  "model_id": "deepseek-flash",
  "api_key_env": "DEEPSEEK_API_KEY",
  "api_key_file": ".secrets/deepseek_api_key",
  "max_tokens": 8000,
  ...
}
```

### 3.2 历史真实调用回执(违规存证,保留不改)

`04_writing/receipts/p40-live-review-20261118.json` 明确记录:
`reviewer_endpoint = https://api.deepseek.com`、`reviewer_model_id = deepseek-flash` —— 说明不是"配了没用",而是**真的跑过云端**。

### 3.3 云端凭证在盘

`.secrets/deepseek_api_key` **存在**(环境变量 `DEEPSEEK_API_KEY` 未设,故当时走的是文件)。这是云端路径能成功的关键。

## 4. 漏入层级

**配置层,不是代码层。**
`model_gate/client.py` 的默认 base_url 是 `http://127.0.0.1:8080`,代码里没有任何云端默认值或兜底;是 `00_control/config.json` 的 `review` 段把评审端点指向了公网。因此纠正点在配置 + 代码护栏两处。

**附带发现(也属违规)**:`review.review()` 直接调用 `model_client.chat`,**绕过 `model_gate` 的单模型串行队列** —— 若写作与评审并发,会同时占用模型,违反"同一时间只跑一个模型"。

## 5. 纠正内容

1. `00_control/config.json` → `review.base_url` 改为 `http://127.0.0.1:8080`,`model_id` 改为本地实际模型 `qwen3.8-flash-next-iq3_s`,`api_key_env` 改为 `STRATA_API_KEY`,删除 `api_key_file`(不再指向云端密钥文件)。
2. `ttxt/writing/review.py` 新增 `_require_local()`:解析 URL 主机名,只有 `127.0.0.1 / localhost / ::1` 放行;任何非回环地址在**发请求之前**抛 `ReviewUnavailable("云端模型已禁用…")`,**不降级、不重试、不回退**。`served_model()` 与 `review()` 双处调用。
3. `api_key(section, required=False)` 允许本地端点不带密钥(本地 Strata 不校验);`required=True` 的原有红线行为不变。
4. 测试适配:`test_p40_independent_review.py` 中 3 处改为读取配置模型 id / `STRATA_API_KEY`;新增 `test_cloud_endpoint_is_rejected_without_network`(把 `models`/`chat` 换成"被调用即失败"的桩,证明云端配置不会产生任何网络请求);原 `test_production_config_points_at_an_independent_endpoint`(要求 https + 与写作端点不同)改为 `…_points_at_a_local_endpoint`(要求回环地址、review 段关键字段无 deepseek 残留)。

## 6. 真实调用验证回执

脚本 `07_tmp/r44_verify_local.py`,真实调用本地端点(非桩):

```text
strata.base_url = http://127.0.0.1:8080
review.base_url = http://127.0.0.1:8080
review.model_id = qwen3.8-flash-next-iq3_s
GET /v1/models → ["qwen3.8-flash-next-iq3_s"]

[云端配置] https://api.deepseek.com
→ ReviewUnavailable: 云端模型已禁用:评审端点必须是本地回环地址(127.0.0.1/localhost),当前配置为 https://api.deepseek.com

[真实本地调用] review.review(draft, max_tokens=96)
reviewer_model = qwen3.8-flash-next-iq3_s
base_url       = http://127.0.0.1:8080
finish_reason  = length(96 token 上限截断,回执目的)
usage          = prompt 34 / completion 96 / total 130
elapsed_s      = 5.047
返回片段        = "这句话是一个**高度概括、逻辑清晰且符合当前行业共识的论断**。它准确地捕捉了生成式AI…"
```

## 7. 代价与不变项

- **代价**:评审与写作现在共用同一台本地模型 ⇒ `same_model_review` 恒为 `true`,**L4 独立评审在合规前提下不可能通过**。这是"只用本地模型"的直接结果,不是待修 bug;账本中 `INDEPENDENT_REVIEW_PASSED` 继续不成立。
- **不变**:门禁 A+B 13/61 = 21.31% `NOT_AVAILABLE`;事实可核率 57/61 = 0.934 MET;取样规则;发布接口;未使用用户账号发布。
- **测试**:全量 **921 passed**(920 + 1 新增)。

## 8. 残余风险(需要用户决定)

- `.secrets/deepseek_api_key` 仍在磁盘。代码已拒绝非回环端点,该密钥对本报项目**已失效**,但它是用户私密文件,本轮**未删除**。建议用户自行删除或移出版本库。
- `04_writing/receipts/p40-live-review-20261118.json` 保留为违规存证,未改写历史。
