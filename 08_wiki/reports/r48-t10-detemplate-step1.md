# R48 · T-10 去模板化第一小步:类目预算 + 生成后检查 + 定向重写一次

清单条目:**T-10(生产写作:去模板化)**,本轮只做第一小步。本轮不碰:门禁阈值、A+B 口径、事实可核率口径、RAG 取样规则、写作主流程结构。

## 1. 连接词与套话预算(先分布、后阈值,不拍脑袋)

- 度量沿用 R40 已落地的确定性模块 `ttxt/writing/style.py::template_signals`(套话/连接词命中、并列句式、整句重复、段首重复、句长 CV),零改动。
- 语料分布:探针快照 `03_intelligence/template_probe-20261119.json`(38 版本 = 14 独立 draft,history 类目无历史写作不计)。
- 规则(`scripts/template_budget_compute.py`,可复算):每类目取该类目历史版本 **p75**(线性插值);历史版本数 < 8 的类目改用全体合并分布 p75 并如实标注 `pooled_p75_fallback`;读数四舍五入 2 位冻结到 `00_control/template_budgets.json`,链路只读该文件。

| 类目 | 套话/百句预算 | 并列句式/百句预算 | 依据 | 样本 |
|---|---|---|---|---|
| technology | 5.26 | 5.26 | category_p75 | 19 |
| finance | 7.91 | 5.14 | pooled_p75_fallback | 4 |
| social | 13.64 | 4.55 | category_p75 | 9 |
| history | 7.91 | 5.14 | pooled_p75_fallback | 0 |
| health | 7.91 | 3.26 | pooled_p75_fallback | 6 |

整篇判定另有度量下限:全文 < 12 句时密度读数不可靠,判 `insufficient_sentences`,不做判定也不重写(防小样本误伤)。

## 2. 生成后自动检查(不新增门禁、不静默放过)

插入点:`write_article` 所有既有门禁通过之后、独立评审之前(`ttxt/writing/workflow.py`),`if passed and decheck_enabled` 才进入 —— passed 判定完全不受 `template_check` 影响(独立审查第 1 项核验全部 passed 赋值点)。

- 整篇读数在预算内 → `through`,零额外模型调用;
- 超预算 → 段级定位超阈值段落,经**与生成同一模型通道**(model_gate 串行闸 → 本地 127.0.0.1:8080)按 `prompts.detemplatize_prompt`(事实/数字/[编号]逐字保留、只动套话与并列模板)**定向重写一次**;重写另存新版本(不计入门禁修订计数 `revisions`);
- 重写后仍超 → `still_over_budget`,回执 `template_check`/`template_flags` + 导出文件"去模板化标记"段,**只标记、不拦截**;
- 模型异常/空产出/产出长度缩水 <60% → `rewrite_failed`,保留原稿并标记(疑似截断不采用);
- 预算文件缺失/损坏/缺类目 → `not_available`,不拦截既有放行链路,但回执与导出都露一行"没尺子"(独立审查建议已采纳);
- 开关:`00_control/config.json: writing.template_decheck_enabled`(默认 true,false=完全跳过,对照/回退用)。

## 3. 真实对照(同一批选题,改前/改后)

对照脚本 `scripts/template_decheck_compare.py`:素材库用 sqlite `backup` 快照副本(正式类目库零写入),两模式同库同选题,读数取最终版本正文;被既有门禁拦下(status=rejected)的稿子照测照记、不剔除。

**批次历程(如实,三轮)**:
- batch1(3 个新选题):6 run 全部被既有事实绑定门禁拒绝(草稿 v3、"原文引句未绑定"类 critical —— 再次暴露 T-10"事实准确"仍是主阻断);且读数查询表名写错(`writing_versions` 不存在,实表为 `writing_history`),读数缺失。
- batch2(修正表名后重跑 6 选题):脚本又暴露两个 bug —— `SIG_KEYS` 用了不存在的信号键 `duplicate_sentence_kinds`、取材门禁提前返回(无 `draft_id`)的 run 取读数 KeyError。全部 run 照记为 error,无有效读数。
- batch3(修复两 bug 后,同一批 6 选题重跑,JSON `03_intelligence/template_decheck_compare-20261119-b3.json`):12 run 全部正常回账。选题 = technology 双11成绩单 / technology 人工智能 / finance 财报与市场波动 / technology 神舟二十四号 / social 延迟退休 / health 睡眠运动健康。

**batch3 结果(不挑样本、rejected 照测照记)**:
- 12 run 中 2 run 被取材门禁写稿前提前返回(双11成绩单×2,`insufficient_materials`,无稿无读数);10 run 有稿有读数。
- 走到全部门禁通过仅 2/12:改前 technology 人工智能(passed)、改后 finance 财报与市场波动(passed)。同一选题两模式 pass/fail 互换(人工智能 改前 passed/改后 rejected;财报 改前 rejected/改后 passed)⇒ **单轮生成随机波动主导 pass/fail,改前改后差异不能归因于本次改动**。
- **去模板化检查本批实际触发 1 次、重写 0 次**:改后模式仅 1 篇 passed 稿进入检查,判定 `within_budget`(零额外模型调用、零标记)。超预算→定向重写一次的链路只在测试里(全假模型链路级断言)被证明,本批真实链路未触发。

**batch3 读数(最终版本正文;每 run 是独立生成的一次成稿,不是同一文本改前/改后)**:

| 读数 | 改前(n=5) | 改后(n=5) |
|---|---|---|
| 套话/百句 均值(中位) | 8.36(7.69) | 4.02(5.00) |
| 并列句式/百句 均值 | 2.87 | 2.75 |
| 并列句式 绝对数 合计 | 3 | 3 |
| 句长 CV 均值 | 0.370 | 0.409 |
| 套话命中 合计 | 9 | 4 |
| 整句重复种类 合计 | 0 | 0 |
| 段首重复种类 合计 | 0 | 1 |

**诚实结论**:读数差异(套话均值 8.36→4.02)出现在重写 0 次的前提下,只能归于生成随机性,**不得宣称去模板化带来改善**。本轮可证的是:(a) 度量→类目预算→判定在真实链路端到端可用(唯一 passed 稿判 within_budget,零成本通过);(b) 超阈值定向重写一次的机制由链路级测试证明;(c) 主阻断仍是上游取材/事实绑定门禁(10/12 未到 passed),去模板化在当前通过率下触发面极小 —— 这与清单 T-10"事实准确"排序一致,提升触发率的前提是上游通过率,不是去模板化本身。

## 4. 测试与验收

- 新增 `10_tests/test_r48_template_decheck.py` 15 项:预算可复算钉子(冻结文件必须能用同一实现从 probe 快照复算)、fail-open 三态(not_available/rewrite_failed 保留原稿/缩水拒绝)、重写恰好一次(rewrite_count/调用计数/无循环)、链路级行为(版本数、导出正文不含套话、导出与回执的标记存在、`revisions` 不增、passed 口径不因 template_check 改变、预算缺失不得拦截也不得调重写但必须露一行)。全部为行为断言,无 inspect.getsource。
- 隔离运行:15 passed(修夹具证据链一轮后);独立只读审查(非执行方)10 项全 PASS,建议项(not_available 也要在导出露一行)已当场落实并补断言。
- 收尾按新标准(2026-11-19 起):**本轮为定向测试** —— 与改动相关的 `test_r48_template_decheck.py` + `test_r40_style_template.py` + `test_p6_writing.py` + `test_p39_material_prior_gate.py` 合跑 **37 passed**(model_gate 锁空闲时跑,对照进程结束后执行);全量测试与独立只读审查留到阶段收口再跑。实现阶段的独立只读审查(非执行方)10 项 PASS 记录仍有效,其覆盖范围是实现代码,未覆盖对照脚本(脚本属只读取数工具,不参与生产链路)。

## 5. 红线自查

门禁阈值(FACT_CHECKABILITY_MIN=0.80、gate_min_score、取材门禁)、A+B 口径、事实可核率口径、RAG 取样、`viral.py`/`prediction.py`:零改动(独立审查以 git diff 核验);模型通道一律本地 127.0.0.1:8080(复用 default_model_call→model_gate);`writing_versions` 教训:版本账本实表是 `writing_history`(draft_id, version)。

## 6. 复算命令

```powershell
$env:PYTHONPATH='D:\ttxt'
d:\ttxt\.venv\Scripts\python.exe scripts/template_budget_compute.py     # 复算冻结预算(重写 00_control/template_budgets.json,应与仓库一致)
d:\ttxt\.venv\Scripts\python.exe scripts/template_decheck_compare.py --batch 07_tmp/r48_batch2.json --work 07_tmp/r48_compare3 --out 03_intelligence/template_decheck_compare-20261119-b3.json
d:\ttxt\.venv\Scripts\python.exe -m pytest 10_tests/test_r48_template_decheck.py -q   # 须 model_gate 空闲时跑
```
