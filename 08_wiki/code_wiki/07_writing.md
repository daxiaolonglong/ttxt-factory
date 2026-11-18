# 07 · 写作子包 ttxt/writing/（6 个文件）

闭环：选题取材 → 大纲 → 正文 → 事实声明核验（L4）→ 反向评审 → 修改 → 导出。全部模型调用经 `model_gate`（唯一例外：独立评审通道，见 7.3）。

## 7.1 workflow.py — 主编排（844 行）

模块 docstring（L1-10）即门禁规则书。

- 常量：`MAX_CLAIMS=5`（L32）、`MATERIAL_POOL_LIMIT=15` / `MATERIAL_TOP_K=5`（L34-35，BM25 取 15 篇池、重排后喂 5 篇）、`CRITICAL_HINTS`（L36）、`TERMINAL_CHARS`（L37）。
- 默认门禁参数（L629-641，可被 `config.writing` 覆盖）：`gate_min_score=0.6`、`gate_min_chars=400`、`gate_max_revisions=2`、`draft_max_tokens=900`、`draft_retry_max_tokens=2048`、`material_min_full=6`、`material_min_sources=3`。

### 入口 `write_article(category, topic, model_call=None, ...) -> dict`（L613）

流水线（L629-844）：

1. **选题取材**：`rag.search(category, topic, limit=15)` → `_rank_materials()`（L472：BM25 归一 + `VIRAL_WEIGHT×`真实爆款先验重排，全文优先；类目未标定退化为纯 BM25 并记 `calibration_status="uncalibrated"`；独立性按**作者账号**轴、缺作者退域名、缺作者不计数——`_independence_key` L456）。
2. **取材前置门禁** `_material_gate`（L593）：全文候选 < material_min_full 或独立来源 < material_min_sources ⇒ 直接返回 `status="insufficient_materials"`、`model_calls: 0`——**不烧模型**（L658-679）。
3. **大纲**（`prompts.outline_prompt`，max_tokens=400）→ **初稿**（`draft_prompt`）；`finish_reason=length` 时 token ×1.5（上限 2048）重试一次（L696-704）。
4. **事实声明抽取**：`claims_prompt` → `parse_claims()`（L88，≤5 条）→ `verify_claims()`（L174，逐条走 `verifier.verify_claim`）。
5. **版本保存**：`_new_draft_id`（L189，`{category}-{时间戳}-{sha1[:6]}`）；`_save_version()`（L195）写 `writing_history`（含实际出稿 model_id）。
6. **评审** `_verified_review()`（L442）= 模型评审（`prompts.review_prompt`，temp=0）+ `parse_review()`（L119，容错 JSON/正则提分，critical 关键词回填）+ `verifier.deterministic_findings()` 确定性结论合并（`_merge_findings` L363）；`_save_evaluation()`（L240）写 `evaluation_history`（evaluator=真实模型 id）。
7. **硬门禁** `_passed()`（L347）：完整性（`_is_complete` L338：长度≥gate_min_chars 且末行句末标点）且 score≥gate_min_score 且 critical 为空。
8. **修订循环**（L726-748）：`revise_prompt` → 版本+1 → **重新抽 claims/重新核验**（旧证据不覆盖新文本，L737 注释）→ 重新评审；≤ gate_max_revisions 轮。
9. **独立评审**（仅 passed 后跑，L750-782）：`review_channel.review(draft)`；`_apply_reviewer()`（L396）按真实模型 id 判独立性；独立评审 blocking ⇒ passed 改 False。
10. **落账与导出**：`_set_draft_status()`（L216，按版本回写：更早版本一律 'REJECTED'；三态口径 `REJECTED / AUTO_PASSED / INDEPENDENT_REVIEW_PASSED`；`_review_account` L414 fail-closed）；`_save_evidence()`（L272）写 `evidence_links`；`export.write_export()` 落盘。

### 模型出口

- `default_model_call(prompt, max_tokens=800, temperature=0.3, enable_thinking=False)`（L40）：唯一默认出口 `gate.submit_and_run("writing", {...})`（L49）。测试/辅助场景可通过 `model_call` 参数注入替身（如 `ttxt assist` 类测试 test_p27_assist_draft.py）。

### 产物落盘

- 草稿 Markdown：`04_writing/drafts/<类目>/<draft_id>.md`（passed）或 `drafts/<类目>/rejected/<draft_id>.md`（rejected——成品目录不得出现被拒稿）。
- DB 产物（类目库）：`writing_history` / `evaluation_history` / `evidence_links` 三表。

## 7.2 verifier.py — L4 事实核验层（约 485 行，纯确定性规则，不调模型）

docstring（L1-46）是完整口径（P13 各批次修复史）。

- 锚点正则：`NUMBER_RE/CN_NUMBER_RE/DATE_RE/PERSON_RE/ORG_RE/CAUSAL_RE`（L68-103）；`ANAPHORIC_ORG_CORES`（L84，泛指机构降级 advisory）；`OPINION_ATTRIBUTION_RE`（L112，观点路由唯一依据=「归属+谓语」）；`HEDGE_RE`（L122）；`SELF_ATTRIBUTION_RE`（L176，自述归属不做存在性核验）。
- `norm()`（L128，全角→半角保 offset）；`extract_anchors()`（L143，五类锚点）。
- **T-15 归属核验**：`attribution_check(claim, window)`（L189）——第三/四方归属必须在绑定引句 ±1 句窗口内**逐字存在**，缺失即 `attribution_not_in_source`。
- `_anchor_in`（L214，数字边界防吞 `(?<![\d.])needle(?!\d)`）；`anchor_window`（L231，引句所在句 ±1 句连续窗口）；`bind_quote(claim, source_text)`（L277，词面重叠最优连续句切片；`QUOTE_MAX_CHARS=220`、`QUOTE_MIN_OVERLAP=0.34`）。
- **`verify_claim(claim, article) -> dict`**（L300）：credibility 四态 `verified / unverified / no_source / opinion`；数字/日期窗口锚定、人物/机构全文锚定、纯中文数量词与泛指机构降 advisory、因果仅验与引句共现。
- `quote_verbatim`（L410，验收复核连续切片）；`check_title`（L417，标题兑现：锚点+词面覆盖≥0.5）。
- **`deterministic_findings(evidence, title, body) -> (issues, critical)`**（L436）：转成门禁条目（伪造归属/无来源/未通过核验/移花接木嫌疑/标题一致性）。

## 7.3 review.py — 独立评审通道（账本 T-04，约 121 行）

三硬约束（docstring）：提示词固定 6 字不可配置化；密钥只从环境变量/`.secrets` 文件；通道不可用 ⇒ 独立评审不成立（fail-closed）。

- `REVIEW_PROMPT = "审评这篇文章"`（L22）；`BLOCKING_MARKERS`（L24）/`PUBLISHABLE_MARKERS`（L25）；两类措辞都缺 ⇒ 都不给标签（`verdict_markers` L70）。
- `review_config()`（L32，读 `config.review`：base_url=DeepSeek、model_id、api_key_env=DEEPSEEK_API_KEY、api_key_file=.secrets/deepseek_api_key、max_tokens=8000——推理模型需大 max_tokens，实测 900/4000 会 finish=length 且正文为空）。
- `api_key(section)`（L37）：环境变量 → api_key_file；取不到**抛异常、不返回空串**。
- `served_model(section)`（L52）：`/v1/models` 探测真实 id；配置 model_id 不在列表则抛。
- `review(draft, section)`（L83）：直连 `model_client.chat(...)`——**注意：这是外部独立评审服务，不经本机 gate 串行锁**（gate 只管本机 Strata）；返回 `{reviewer_model, verdict_text, verdict, usage, elapsed_s}`。

## 7.4 export.py / prompts.py

- `export.render_markdown(...)`（L13）：正文 + 证据链表格（quote/offset/overlap/credibility/维度缺失）+「未经授权不得发布」尾注；`write_export(..., status)`（L73）：passed→`drafts/<类目>/`，否则 `drafts/<类目>/rejected/`。
- `prompts.py`：`outline_prompt`（L6，4-6 条大纲）、`draft_prompt`（L14，500-800 字、首行标题、句末标 [编号]、不得编造）、`claims_prompt`（L29，JSON 数组 claim/article_id）、`review_prompt`（L40，score/issues/critical，评分权重 40/30/20/10）、`revise_prompt`（L51）。

## 7.5 门禁链总览

```
取材门禁(零模型) → 完整性门禁(长度+句末标点; length 自动加倍重试一次)
→ verifier 确定性 critical ∪ 模型评审 critical → score≥0.6
→ 修订 ≤2 轮(每轮重新核验) → 独立评审(外部模型; blocking 可翻盘)
→ passed/rejected 双目录导出 + 三态版本账本(fail-closed)
```
