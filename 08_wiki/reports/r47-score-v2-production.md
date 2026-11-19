# R47:score_v2 接入正式评分/预测链路(只增不改,旧链路保留可回退)

日期:2026-11-19
承接:R45 已验证的 `score_v2`(连续文本评分,无泄漏、时间切分)。本轮把它接进正式链路,
并给出旧 `viral_labels` vs 新 `score_v2_labels` 在同一 holdout 上的真实对照读数。

产物:
- `ttxt/analysis/score_v2.py`(新增 `save_model`/`load_model`,三重校验)
- `ttxt/analysis/score_v2_apply.py`(新模块:`apply_category`/`status`/`scores`)
- `ttxt/crawler/store.py`(`SCORE_V2_SCHEMA` 新表 `score_v2_labels`,`migrate_articles_db` 幂等创建)
- `ttxt/analysis/analyze.py`(viral 阶段后新增 `score_v2` 阶段,fail-open)
- `ttxt/__main__.py`(`score-v2 apply|status` 子命令)
- `scripts/score_v2_fit_model.py`(拟合+落盘正式模型)、`scripts/score_v2_production_compare.py`(并列对照;
  只读审查后修正:全局切分 + 无偏分位,见第三节)
- `03_intelligence/score_v2_model.json`(正式模型产物)、`03_intelligence/score_v2_production_compare-20261119.json`(全部读数,schema v2)
- `10_tests/test_r47_score_v2_apply.py`(10 项红线)、`10_tests/test_r47_compare_metrics.py`(7 项统计函数单元测试)

## 一句话结论

`score_v2` 已从"离线研究"进入**正式评分链路**:每篇一行连续分落进新表 `score_v2_labels`,
旧 `viral_labels` / `prediction_records` **一行未改**,随时可回退。真实对照(holdout 9,986 篇,
与拟合**相同的全局时间切分**,名次分位 (rank−0.5)/n):新链路读数 Spearman **+0.1627**
[0.1434, 0.1845]、top-decile lift **1.11**、连续取值 **9,915** 个;旧链路落库 `viral_labels.score`
在同一 holdout 上 Spearman 0.98,但这是**既有泄漏读数**(旧 `score_with_metrics` 把结果类字段喂进
分数,且 social 恰为 1.0),只并列展示、不假装可比。门禁阈值、A+B、事实可核率、取样规则全部零改动。

## 一、接入方式(全部"只增")

```
analyze.run_analysis:
  seed → viral → [score_v2(新)] → mine_keywords → score_keywords → auto_feedback → knowledge → index
```

- **新表 `score_v2_labels`**(`article_id PK, score_v2, features_json, model_version, applied_at`):
  `apply_category` 每次先 `DELETE` 再全量 `INSERT`,表内永远是"当前库全量文章"的并列读数,无孤儿行、幂等;
  旧 `viral_labels` 的 `INSERT/DELETE` 路径一行没动(测试逐行 `SELECT *` 比对前后完全一致)。
- **正式模型只从 `03_intelligence/score_v2_model.json` 加载**,`load_model` 校验版本、特征名清单、
  结果类字段三重关,任一不过直接抛错 —— 宁可不打分,不拿口径不明的模型进链路。
- **fail-open(两类故障都收敛为 status,不抛异常)**:
  ① 模型缺失/损坏/校验不过 ⇒ `{"status":"model_unavailable",...}`;
  ② 库打开/迁移失败、连接失败、特征抽取或 DB 读写异常 ⇒ `{"status":"apply_failed",...}`
  (审查后补:此前只兜住①且连接/迁移在 try 之外,DB 异常会击穿主流程;现已把迁移与连接
  一并纳入收敛)。写入走"先清后插 + 单次 commit"事务,异常时未 commit、连接关闭即回滚,
  `score_v2_labels` 保持上一次落库状态、不留半成品 ⇒ analyze 的 `score_v2` 阶段不中断,
  旧链路与退出码零影响(两类各有一项注入测试)。
- **CLI**:`python -m ttxt score-v2 apply --category social` / `status --category social`。

## 二、正式模型口径(与 R45 验证口径完全一致,不另起炉灶)

`scripts/score_v2_fit_model.py`:五库 JOIN 有 reads 33,595 篇(今日 12:26 定时采集后快照,比 R45 当时多 21 行)
→ 时间可解析 33,278 → `time_split(0.7)` 训练 23,294 / holdout 9,984
(`train_published_max = 2026-10-06T17:22:51+08:00` < `holdout_published_min = 2026-10-06T17:23:48+08:00`);
目标 `log10(reads) − 训练集类目中位数`。
系数(原始单位,τ=0.5,取自模型产物):`has_quote +0.3344`、`has_exclaim +0.2623`、`has_digit +0.1669`、
`log10_body_chars +0.1409`、`title_len_x10 +0.1388`、`log10_paragraphs +0.1361`、`has_colon +0.0732`、
`avg_para_x10 −0.0012`、`cat_history +0.1044`、`cat_finance +0.0617`、`cat_health −0.3443`;
与 R45 验证系数同方向同量级(最大差异 `cat_history` +0.1044 vs R45 +0.0890,源于 +21 行样本增量)。

## 三、真实对照读数(holdout = 未来 30%,模型从未见过)

**只读审查后的两处口径修正(先修完,读数才作数)**:

1. **切分口径**:首版对照脚本按**类目内** `time_split(0.7)` 取 holdout,而拟合是**全局**切分 ——
   类目内切分把各类目最旧 70% 也放进了 holdout,其中大量文章是拟合训练时见过的,泄漏使小类目
   读数失真。现改为与 `scripts/score_v2_fit_model.py` 完全相同的**全局**合并切分,
   产物登记边界:`train_published_max = 2026-10-06T17:38:05+08:00` <
   `holdout_published_min = 2026-10-06T17:38:59+08:00`(全局 dated 33,286,训练 23,300 / holdout 9,986;
   比拟合快照 33,278 多 8 行,是对照运行前定时采集的新文章;模型只见文本、拟合时未含这些行,不影响防泄漏)。
2. **名次分位**:首版用 `rank/n`,期望 0.5+1/(2n),小样本把 lift 系统性抬高(n=12 抬 4.2%)。
   现用 `(rank−0.5)/n`(rank 为并列平均名次),全体均值**恰为 0.5**、任意 n 无偏,lift 的 1.0 基准才成立。

### 修正前 vs 修正后(v2;差异全部来自上面两处口径,不涉及任何链路改动)

| 口径 | 修正前(类目内切分 + rank/n) | 修正后(全局切分 + (rank−0.5)/n) |
|---|---|---|
| 池化 Spearman | 0.1595 [0.1419, 0.1777],n=9,987 | **0.1627 [0.1434, 0.1845]**,n=9,986 |
| 池化取值数 | 9,922 | 9,915 |
| social | 7,579 / ρ 0.1491 / lift 1.114 | 7,626 / ρ 0.1501 / lift 1.114 |
| finance | 2,293 / ρ 0.2004 / lift 1.325 | 2,010 / ρ 0.2183 / lift 1.351 |
| technology | 72 / ρ 0.0323 [−0.178, 0.250] / lift 1.131 | 230 / ρ 0.1280 [0.003, 0.266] / lift 1.047 |
| history | 31 / ρ 0.2827 [−0.014, 0.545] / lift 0.989 | 86 / ρ 0.2185 [−0.014, 0.418] / lift 0.942 |
| health | 12 / ρ −0.028(无 CI)/ lift 1.500 | 34 / ρ 0.1682 [−0.128, 0.498] / lift 1.284 |

- 分类目 n 变化(social 7,579→7,626、technology 72→230 等)= 切分口径变了,holdout 成员换成了
  "全局最新 30% 中属于该类目的行",与 R45 验证集的类目成员(7,621/2,010/228/85/34)对齐;
- 大类目 ρ/lift 几乎不动(修正前后 social 1.114 → 1.114),小类目变化主要是成员数变了而非公式偏差;
  health 旧 lift 1.5 里约 4% 是 `rank/n` 公式偏差,现如实 1.284;
- **结论方向不变**:v2 池化 ρ 略升、CI 仍不含 0;审查没有推翻结论,只是把小类目失真的读数换掉。

### 修正后池化(n=9,986)

| 读数 | Spearman [95%CI] | 不同取值数 |
|---|---|---:|
| **score_v2(新链路落库值)** | **+0.1627** [0.1434, 0.1845] | **9,915**(连续) |
| viral_labels.score(旧链路落库值,**既有泄漏**) | 0.9775 [0.9731, 0.9807] | 1,107 |

旧读数 Spearman≈0.98 不是"旧评分更准",而是 `score_with_metrics` 把 reads 等结果字段本身喂进了分数
(用结果预测结果;social 旧列 Spearman 恰为 **1.0**,构造使然),这是 R43/R45 指出的错位。
`score_v2` 的 +0.16 才是"只靠发布前文本"能达到的真实信号强度。

### 修正后分类目(top-decile lift,Spearman)

| 类目 | holdout | v2 Spearman [CI] | v2 lift | 旧 Spearman | 旧 lift |
|---|---:|---|---:|---:|---:|
| social | 7,626 | +0.1501 [0.129, 0.168] | 1.114 | 1.0(泄漏) | 1.900 |
| finance | 2,010 | +0.2183 [0.176, 0.258] | 1.351 | 0.985(泄漏) | 1.849 |
| technology | 230 | +0.1280 [0.003, 0.266] | 1.047 | 0.506(泄漏) | 1.261 |
| history | 86 | +0.2185 [−0.014, 0.418] | 0.942 | 0.527(泄漏) | 1.415 |
| health | 34 | +0.1682 [−0.128, 0.498] | 1.284 | 0.464(不足/泄漏) | 1.186 |

- social/finance(样本充足):`score_v2` Spearman 显著为正、lift>1,并列读数可用;
- technology:n=230,CI 下界 0.003(贴 0),边缘为正,不外推;
- history/health:CI 跨 0,样本不足,明确标不足、不外推(history lift 0.942<1,同样如实呈现)。
- 旧列 Spearman/lift 全部是泄漏读数(旧实现读过结果类字段),不作为预测力证据,仅并列。

### "离散 vs 连续"直接对照(全表)

各类目 `viral_labels` 旧 score 与 `score_v2_labels` 连续分的取值数对照见 JSON `full_table_distinct`
字段(如 social 旧落库全表 distinct=2,151、v2 全量刷新后按名次分位连续)。

## 四、红线测试(`test_r47_score_v2_apply.py` 10 项 + `test_r47_compare_metrics.py` 7 项)

红线(10 项):
- `test_save_load_roundtrip` / `test_load_model_rejects_bad_artifacts`:版本、特征名、结果字段、缺文件、
  坏 JSON 五种情况一律 `ModelFileError`/`LeakageError`,不合格模型存不进也读不出;
- `test_migration_creates_new_table_idempotently`:迁移幂等建 `score_v2_labels`;
- `test_apply_populates_new_table_and_never_touches_old_labels`:apply 后新表行数=文章数、连续分 distinct≥2、
  features 无结果字段,且**旧 `viral_labels` 逐行完全不变**;再刷一次幂等、无孤儿行;
- `test_apply_fail_open_when_model_unavailable`:模型缺失返回 status、不抛、不写新表、旧表原样;
- `test_apply_fail_open_when_db_write_broken`(审查后新增):注入坏表使 INSERT 必失败 ⇒
  返回 `status=apply_failed`、不抛异常、事务回滚不留半成品、旧表逐行不变;
- `test_apply_fail_open_when_db_unopenable`(审查备注①修复后新增):db_path 指向非 SQLite 文件
  (迁移/连接必失败)⇒ 同样收敛为 `apply_failed`、不抛出;
- `test_status_reads_back_and_handles_empty`:`status` 只读回灌;
- `test_run_analysis_runs_score_v2_stage_after_viral`(审查后替换 `inspect.getsource` 字符串断言为
  **行为断言**):真实跑 `run_analysis`,`stage_seconds` 里 `score_v2` 在 `viral` 之后、apply 确实被调用、
  旧 viral 阶段照常产出(护栏:接入是"只增",不是改写旧链路)。

统计函数单元测试(7 项,审查后新增,给 Spearman/lift 读数打地基):
- `percentile` 线性插值边界;`ranks_with_ties` 并列平均名次(含全并列);
- `spearman` 完全单调 ±1、并列值手工核对(ρ=1.5/√3=√3/2)、一方全并列返回 None、n<3 不给值;
- `actual_percentiles` 在 n=2/12/100 均值**恰为 0.5**(无偏基准),n=12 首末位 0.5/12、11.5/12;
- `top_decile` n<10 不评估(None)、完美排序 lift>1.8、反向排序 lift<0.2、构造无关配对 lift∈[0.9,1.1]。

配套:`10_tests/conftest.py` 裁剪副本新增清理 `score_v2_labels`(每篇一行的并列评分表,对测试语义为零,
不裁剪会让副本超 40 MB);`TRIM_LOGIC_VERSION` 2→3。`test_p4_analysis.py` 阶段集合断言纳入 `score_v2`。

## 五、零改动声明

- `ttxt/analysis/viral.py`、`ttxt/analysis/prediction.py` **一行未改**(旧评分/预测可回退);
- 门禁阈值、A+B 读数口径、事实可核率口径、取样规则:**一律未动**;
- 未触碰发布接口、未用用户账号发布。

**测试**:全量 **943 passed**(928 + 10 红线 + 7 统计函数单元测试;审查前为 935)。

## 六、复算

```powershell
$env:PYTHONPATH='D:\ttxt'
d:\ttxt\.venv\Scripts\python.exe scripts/score_v2_fit_model.py            # 拟合+落盘正式模型
d:\ttxt\.venv\Scripts\python.exe -m ttxt score-v2 apply --category social  # 刷某类目新表(示例)
d:\ttxt\.venv\Scripts\python.exe scripts/score_v2_production_compare.py   # 旧 vs 新 并列对照读数
d:\ttxt\.venv\Scripts\python.exe -m pytest 10_tests/test_r47_score_v2_apply.py 10_tests/test_r45_score_v2.py -q
d:\ttxt\.venv\Scripts\python.exe -m pytest -q
```
