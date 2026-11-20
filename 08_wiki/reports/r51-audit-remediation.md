# R51:范围审计修复轮(P0 仓库卫生 + 评审端点策略拆分 + P1 五项 + P2 五项)

核对日期:2026-11-20。任务书=R51 十项范围审计(1 P0 + 5 P1 + 5 P2),外加两处用户拍板变更:
①P0 降级为纯仓库卫生(key 已在 DeepSeek 后台注销,泄漏已实质止损,历史重写一律不做);
②新增需求:评审要能走 DeepSeek(「DeepSeek是要留着审核我们模型写作的文章质量的」)。

红线全程未动:门禁判定逻辑、全部阈值、±1 窗口、0.34 重叠率、取材门禁、取样规则(SEEDS/select_seeds/MAX_SEEDS_PER_SOURCE)、分母 61、A+B 读数值、`viral.py`、`prediction.py`。

## P0(降级后):仓库卫生

- **改了什么**:commit `1eabed0`(`chore(security): untrack IDE state DBs and debug artifacts from _model_fix and probe file`,已推送,`main` 与 `origin/main` 同步):`git rm -r --cached` 摘除 `_model_fix/` 全部 17 个文件(含 8 个 `.vscdb` IDE 状态库)+ `_probe_write.txt`,合计 18 个;`.gitignore` 追加 `_model_fix/` 与 `_probe_write.txt`。
- **验证**:`git ls-files -- '_model_fix' '_model_fix/*' '_probe_write.txt'` = **0**;本地文件 `Test-Path` 均为 True(保留不删)。审计 P2-1 所列 `_model_fix/fix_model.bat`、`_model_fix/fix_stale_model.py`、`_probe_write.txt` 与 `.vscdb` 属同一批,**已在同一 commit 一次清干净**,未分两次提交。
- **凭据扫描**:全仓 `sk-` 形态扫描,命中仅 2 处非凭据(测试哨兵值 `sk-SENTINEL-DO-NOT-LEAK-*`、注释);真实 key 只在未跟踪的 `.secrets/deepseek_api_key`,不在版本库。**历史未重写,origin 历史未碰**。
- **未闭合**:origin 历史中 1eabed0 之前的旧提交仍含已注销 key 的文件形态存证——用户明示不做历史重写,风险已由后台注销止损。

## 变更二:评审可走云端(写作仍强制本地)

- **改了什么**:把「必须本地」拆成两条策略。
  - 写作侧:`ttxt/model_gate/client.py` 新增 `require_loopback()`,默认 strata 配置路径解析端点时非回环即抛 `ModelError`(显式传 base_url 的评审通道不受牵连)。写作端点不放宽。
  - 评审侧:`ttxt/writing/review.py` 删除 `_require_local`,新增 `_review_endpoint()`:允许回环或 **https** 云端;云端必须 https(密钥不经明文传输)、必须配 key(`api_key(required=True)`);缺 key/端点不可达 ⇒ 抛 `ReviewUnavailable`,**不静默降级回本地、不换模型**。`served_model()` 改为按配置 model_id 校验(配置 id 必须在 `/v1/models` 里,不再拿第一个顶替)。
  - 记账:评审回执与 `model_meta`/criteria_json 新增 `reviewer_endpoint`;`same_model_review` 判定逻辑未改(本就按真实模型 id 比较,云端化后自动如实翻转)。
- **用户拍板(2026-11-20)**:新 key 未建,**本轮不切云端**——`config.json.review` 保持 `http://127.0.0.1:8080` + `qwen3.8-flash-next-iq3_s`。成因一句话:`same_model_review=true` 的原因是写作与评审共用同一个本地模型(合规前提下无第二模型可用),即**当前评审就是自评**;切云端只需改 `base_url/model_id/api_key_env` 三字段+真实探测回执。
- **验证**:`10_tests/test_p40_independent_review.py` 24 passed——写作侧非回环必拒(monkeypatch 配置后 `_base_url/models/chat` 全抛)、评审侧云端端点被允许且回执端点记为云端(`reviewer_endpoint="https://api.deepseek.com"`、`reviewer_is_local False`、回执不含 "127.0.0.1"/"localhost"、key 不进回执)、云端缺 key 零网络失败、云端 http 被拒、不可达抛错不降级。

## P1-1:账本 A+B 口径(只动表述)

- **改了什么**:`08_wiki/PROJECT_STATUS.md` 插入「R51 口径勘误」:全账本出现的「A+B 13/61 / 21.31% / 27.87%」均为**卡片 tier 自报计数**(A7/B6/C48;旧集 A17/B0/C44),不是门禁通过率;门禁真实读数=人工裁定 0/61(< MIN_HUMAN_CASES=50)、占比 None ⇒ `NOT_AVAILABLE`、exit 2(复算 `scripts/golden_v2_harness.py`,读数见 `08_wiki/reports/P13-golden-v2-window-status.md` 的 `gate_failures`)。另插「R51 口径注」:n=19/ρ=−0.06/status=insufficient_sample 不得称「有/无相关」;6/25 分母是 25 张锚点卡片非 61。8 组 replace_all 措辞改写,全部数字原值保留,逐条历史不回改。
- **验证**:门禁代码零改动(该阶段只动 md);门禁复算读数未重跑——A+B 真实读数仍以 `gate_failures` 为准。

## P1-2:tau 真参数化 + tau=0.5 逐位不变

- **改了什么**:`ttxt/analysis/score_v2.py` fit 循环权重改为 pinball 加权 `_pinball_weights(residual, tau)`:正残差 `2τ/max(|r|,1e-3)`、负残差 `2(1−τ)/max(|r|,1e-3)`、零残差 `1/max(|r|,1e-3)`。
- **验证**:`test_tau_default_weights_match_r45_bit_for_bit`——τ=0.5 时与 R45 旧式 `1/max(|r|,1e-3)` 在 0.0/−0.0/1e-9/±0.5/±123.456/±1e-3/±2e-4/±0.25/−0.75 上**逐位相等** ⇒ 生产模型 `03_intelligence/score_v2_model.json` 系数不动成立;`test_tau_is_a_real_parameter_and_moves_the_quantile`——400 行合成数据 fit τ=0.25/0.5/0.75,截距预测 p_low<p_half<p_high。定向 18 passed(含 r47 apply 测试)。

## P1-3:lift 口径统一 + R45 表重算

- **改了什么**:`scripts/score_v2_study.py` 的类目内分位从 min-max `position/(n−1)` 改为 `actual_percentiles`(名次分位 (rank−0.5)/n,并列平均名次,直接 import 自 `score_v2_production_compare`——单一实现);lift 从 precision/base(pct≥0.9 命中率/全库命中率)改为 top-decile 实测分位均值/0.5(micro 聚合);产物注明口径。账本 R45 表重写并在表下加口径注:两套 lift 同名不同义,旧口径数字只存证不得引用、不得跨口径比较。
- **重算读数(2026-11-20 快照,dated 33,314;训练 23,319/验证 9,995,边界 2026-10-06T19:07:14<19:09:31;样本漂移 33,257→33,314 已注明)**:
  - score_v2 pooled:ρ **+0.1634** [0.1442,0.1824],lift **1.1608** [1.1218,1.2015],取值 9,930;
  - old_bucket:ρ +0.0032 [−0.0164,+0.0228] CI 跨 0,lift 0.9289;
  - old_reads_score(泄漏对照):ρ 0.9584、lift 1.9001;
  - 分类目:social 7,624 ρ+0.1506/finance 2,014 ρ+0.2194 显著为正;technology 233 ρ+0.1274 贴 0;history 90 ρ+0.2257 CI [0.0195,0.4135] 不再跨 0 但 n=90 仍标不足不外推;health 34 CI 跨 0。
  - 旧口径 lift 1.7308/1.0426/8.998 废弃;study 重拟合系数与正式模型差异仍源自样本增量,`score_v2_model.json` 未重训。

## P1-4:compare 产物补生成时间与互斥证明

- **改了什么**:`scripts/score_v2_production_compare.py` payload 补 `generated_at`、`model_saved_at`(读自模型文件外层);time_split 补 `train_holdout_disjoint_proof`:**按 (category,id) 对**证互斥(id 跨类目会撞,只按 id 的证明无效)。
- **验证**:产物重生成:`generated_at=2026-11-20`,model_saved_at=2026-11-19T12:24:16,train 23,319/holdout 9,995,`intersection=0, disjoint=true`;pooled v2 ρ **0.1632** [0.1461,0.1806] n=9,995(与 P1-3 重算同向;与 11-19 读数 0.1627 差异属样本漂移)。compare 的 top_decile/分位实现未动 ⇒ `test_r47_compare_metrics.py` 7 项不受影响。

## P1-5:相对日期按 published_at 折算

- **改了什么**:`ttxt/writing/verifier.py` 新增相对日期折算层(claim 侧参照核验时刻(≈写稿时刻,测试注入 `now`),来源侧参照 `published_at`):「明年2月」类锚点折成绝对 (年,月,日) 再与窗口内绝对 token 及来源侧相对 token(按发表时刻折算)比对;裸「2月10日」按来源发表年定年。**折算不出(来源缺 published_at、窗口只剩相对/无年份表达)⇒ `unverifiable`**:不进 missing、不判捏造、credibility 不得 verified,critical 措辞如实标「相对日期无法折算(unverifiable)」。`ttxt/writing/workflow.py` 取材查询与材料 dict 带上 `published_at` 供核验层使用(取样规则/排序/取材门禁统计未动)。绝对日期锚点与无相对词路径旧逻辑原样;±1 窗口、0.34 重叠率未动。
- **验证**:新增 `10_tests/test_r51_relative_dates.py` **9 项**(claim 相对↔来源绝对命中;2025 年来源的「明年2月」不得被 2026 年 claim 字面放行(跨年回归);缺 published_at ⇒ unverifiable 且不进 missing;critical 措辞;12-31 核验+来源 2027-01 发表裸月日跨年命中;裸月日缺发表年 ⇒ unverifiable;「去年12月」双侧折算;绝对锚点红线自检)。**回归 p12 19 + p13 两文件 60 = 79 绿,golden 卡片判定零翻转**。

## P2-补:评审产物显式独立性字段(用户拍板「现在是自评」钉死在产物里)

- **改了什么**:`workflow._model_meta`/`_apply_reviewer` 在 `model_meta` 里新增 `review_independence: "self_review"|"independent"` 与 `independent_review: bool`——随 criteria_json(`evaluation_history`)与回执 payload 入库;初始值 fail-closed 为 `self_review/false`,只有独立评审证据坐实才翻转(与 `same_model_review` 同一判定,零逻辑改动)。顶层回执的 `independent_review` 键仍是评审摘要 dict,不撞名。
- **验证**:`test_p40_independent_review.py` **24 passed**(改字段前后各跑一次),门禁、阈值、退出码零变化。当前配置下每条回执都会显式写着 `review_independence=self_review`。

## P2(五条)

### P2-1:调试残留摘除(与 P0 同批,一次清完)

- **做法与验证**:`git show --name-status 1eabed0` 核对摘除清单——`_model_fix/fix_model.bat`、`_model_fix/fix_stale_model.py`、8 个 `.vscdb`+`dryrun.vscdb`、`_probe_write.txt` 全部在该 commit 内 `D`(deleted from index),`.gitignore` 同 commit 追加 `_model_fix/` 与 `_probe_write.txt`;当前 `git ls-files` 对这两类路径读数 **0**;本地文件保留。未分两次提交,与 `.vscdb` 清理无打架。

### P2-2:硬编码本机路径改 `__file__` 推导 + 环境变量

- **改了什么**:`scripts/install_gh.py`(原 :8-9 写死 `D:\ttxt\.secrets\github_token`/`D:\ttxt\tools`)与 `scripts/p28_technology_dryrun.py`(原 :14-19 写死 `D:\ttxt` 三处)统一改为 `ROOT = Path(os.environ.get("TTXT_ROOT") or Path(__file__).resolve().parent.parent)`,所有路径从 ROOT 拼接;`sys.path.insert` 亦用 ROOT。
- **验证**:从 `cwd=C:\Users\yangl`(非仓库盘符目录)import 两个模块实测:`install_gh ROOT=D:\ttxt`、`TOKEN_FILE exists=True`、`TOOLS exists=True`;`p28 ROOT=D:\ttxt`、`PROD exists=True`——非 `D:\ttxt` 环境按 `__file__`/`TTXT_ROOT` 定位,不再依赖 cwd 与盘符。

### P2-3:读数可核对性(不引入 CI)

- **做法**:`01_categories/*/articles.db` 保持不入库(正确);账本 R51 段与本报告明确写:**账本内库内读数(883 passed 时代的语料计数、13/61、0.934、3/359 等)依赖本地 DB,干净 clone 不能复现**——复核须持账本登记时刻的库快照;不为此引入 GitHub Actions。
- **本轮 pytest 摘要即落在本报告「测试累计」节**,账本 R51 段引用本报告路径,作为可核对出口。

### P2-4:`writing_history.status` 大小写归一(只改数据与标签大小写,判定逻辑零改动)

- **写入侧统一小写**:`workflow._review_account` 三态返回值改小写(`rejected/auto_passed/independent_review_passed`,语义与选择条件一字未动);`_set_draft_status` 的 `CASE WHEN version < ? THEN 'rejected'`;`_save_version` 本就写 `draft`。同步更新 p6/p10/p13/p40 测试断言(断言的是标签大小写,不是判定)。
- **查询侧**:`grep` 全仓,生产代码没有任何按 `writing_history.status` 过滤/聚合的查询(dashboard/api、publication、脚本均不按 status 筛),故「查询侧统一 `lower(status)`」无需改动即成立;唯一按 status 的写路径 `_set_draft_status` 的 WHERE 只按 `(draft_id, version)`,不筛 status。如实记:此要求在当前代码形态下为空操作。
- **历史行幂等归一**:新增 `scripts/normalize_history_status.py`(入库):先 `sqlite3 backup API` 备份每个库到 `07_tmp/r51_status_norm_backup/`(含 WAL),再 `UPDATE writing_history SET status=lower(status) WHERE status<>lower(status)`,脚本内二次执行自证 0 行;外部再重跑一次同样 0 行(重跑不改结果)。
- **归一前后计数对照表(2026-11-20,变更行=6+9=15)**:

| 库 | 归一前 | 变更行 | 归一后 |
|---|---|---|---|
| demo | draft 3 / rejected 2 | 0 | 不变 |
| finance | passed 1 / rejected 3 | 0 | 不变 |
| health | rejected 6 | 0 | 不变 |
| history | (空表) | 0 | 不变 |
| social | **REJECTED 6 / rejected 3** | **6** | rejected 9 |
| technology | **AUTO_PASSED 1 / REJECTED 8 / draft 3 / passed 4 / rejected 3** | **9** | auto_passed 1 / draft 3 / passed 4 / rejected 11 |

- **判定没漂的证据**:归一后重跑读写这条链路的测试 8 文件(p6 写作、p10 门禁、p12 核验、p13 版本账目、p40 独立评审、p39 取材门禁、r41 发布链路、r51 相对日期)= **87 passed,0 failed**;备份库在 `07_tmp/r51_status_norm_backup/` 可回滚。

### P2-5:发布段边界说明

- **核对**:`ttxt/writing/publication.py` 全文只有三处 `CREATE TABLE`(publications/publication_observations/performance_windows),无 `requests/urllib/http` 任何引用;`ttxt/writing/` 目录清单确认无 `publish.py`。
- **落笔**:账本 R41 段②加「边界说明(R51 P2-5,防误读)」:`publication.py` 只建三张表、只做草稿↔URL 绑定与 T+N 指标回收,**没有任何 HTTP 发布调用**;`publications` 表存的是「别人已发布后我们登记的链接与观测」,不是「本系统已发布」的状态。

## 测试累计(定向,跑前确认 model_gate 锁空闲)

- r40 独立评审 **24**(评审云端化后跑一次、加 `review_independence` 字段后再跑一次,两次同数);
- r45+r47 18+7、r40/p6 合跑 35、r51 相对日期 **9**、p12 19、p13 60——全部 passed;
- P2-4 归一后链路复跑:p6+p10+p12+p13+p40+p39+r41+r51 合跑 **87 passed**(证明 status 归一读数没漂);
- **收口合跑(本轮最终读数)**:p6+p10+p12+p13两文件+p40+p39+r41+r45+r47两文件+r50+r51 共 13 文件 = **131 passed, 0 failed**(24.26s;本次运行期间 `09_logs` 未被外部进程触碰,无 conftest teardown ERROR);
- P2-2 验证:非仓库 cwd 下 import 两脚本,ROOT/路径解析全对(见 P2-2);
- 环境噪音:conftest 保护守卫三次报 `09_logs/ttxt-20261120.jsonl` 被测试期外进程(supervisor 常驻写日志)修改,测试本体全绿,非判定失败。

## 未闭合项(截至本报告)

1. 评审仍为自评:云端通道已放开但新 key 未建,`config.json.review` 保持本地 ⇒ `same_model_review=true`/`review_independence=self_review` 持续如实记账;切云端待用户给 key(届时只改三字段+真实探测回执)。
2. origin 历史含旧 key 存证(用户裁定不重写,后台已注销止损)。
3. 库内读数不可从干净 clone 复现(P2-3 已登记口径与核对出口,非缺陷)。
4. `07_tmp/r51_status_norm_backup/` 备份保留在本地(未跟踪),清场时机由用户定。
