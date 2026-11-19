# R45:score_v2 —— 让评分看得见文本(新版并行,旧版不动)

日期:2026-11-19
产物:`ttxt/analysis/score_v2.py`(新模块)、`scripts/score_v2_study.py`(拟合与并排评估)、
`03_intelligence/score_v2_study-20261119.json`(全部读数)、`10_tests/test_r45_score_v2.py`(7 项红线)。

## 一句话结论

新增版本化连续评分 `score_v2`(分位数回归 τ=0.5),特征**只含发布前可得的标题/正文结构/类目**,不含任何
结果类字段;按 `published_at` 时间切分(训练 23,279 篇全部早于验证 9,978 篇)。在**真实**验证集上:

- `score_v2` Spearman **+0.1354** [0.1161, 0.1547],top-decile lift **1.73** [1.535, 2.009],连续取值 **9,932** 个;
- 旧分档 `old_bucket` Spearman **−0.0651** [−0.0846, −0.0455],lift **1.04** [0.732, 1.047],取值 **15** 个;
- `old_reads_score` 的 Spearman 0.8366 是**泄漏**(用 reads 定义分数再去预测 reads),**不作为预测力证据**。

即:R43 指出的两条错位——`score_with_metrics` 不读文本、分档只有 3 个取值——在新版里都变成了可见的实数差异,
而旧实现**一行未改**,只作并列读数。

## 一、防泄漏口径(写死在代码里)

特征仅 12 项,全部在发布时点已知:

| 特征 | 含义 |
|---|---|
| `has_digit` / `has_quote` / `has_exclaim` / `has_colon` | 标题是否含数字 / 引号 / 感叹号 / 冒号或竖线 |
| `title_len_x10` | 标题字数 ÷ 10 |
| `log10_body_chars` | `log10(1 + 正文字数)` |
| `log10_paragraphs` | `log10(1 + 正文非空段数)` |
| `avg_para_x10` | 平均段长 ÷ 10 |
| `cat_finance` / `cat_health` / `cat_history` / `cat_technology` | 类目哑变量(`social` 为参照组,不出现) |

防泄漏由代码强制,不靠口头约定:

- `OUTCOME_FIELDS = {reads, read_count, likes, like_count, comments, comment_count, favorites,
  favorite_count, impressions, impression_count, shares, share_count, score, viral_score}`;
- `extract_features` 只读 `title / content / category`,结尾调 `assert_no_leakage`;
- `fit` 对每个特征名再查一遍 `assert_no_leakage`;
- `predict` 若模型特征名命中结果类字段直接抛 `LeakageError`。

测试 `test_features_are_text_only_and_have_no_outcome_fields` / `test_leakage_guard_rejects_outcome_fields`
钉死这三条。

## 二、只用过去:时间切分

`score_v2.time_split` 按 `published_at` 升序,训练集**严格早于**验证集;无法解析发布时间的行直接丢弃,不猜、不塞。
本次实际边界:

- 有 reads:33,574 篇;发布时间可解析:33,257 篇(丢弃 317 篇);
- 训练 23,279 篇,`train_published_max = 2026-10-06T16:25:37+08:00`;
- 验证 9,978 篇,`test_published_min = 2026-10-06T16:25:55+08:00`。

训练集各类目 `log10(reads)` 中位数(去偏基准):health 3.5339、social 1.1761、history 3.8036、finance 1.0792、
technology 3.6551。目标 `y = log10(reads) − 训练集类目中位数`,避免把类目量级差当成文本效应。

## 三、每项特征的系数(原始单位,τ=0.5)

系数含义:`y`(`log10(reads)` 相对类目中位数)每增加一单位特征的变化量。正值=更可能高于同类中位。

| 特征 | 系数 | 方向解读 |
|---|---:|---|
| `has_quote` | **+0.3342** | 标题含引号,效应最强 |
| `has_exclaim` | **+0.2625** | 含感叹号 |
| `has_digit` | **+0.1675** | 含数字 |
| `log10_body_chars` | **+0.1412** | 正文越长 |
| `title_len_x10` | **+0.1385** | 标题越长(每 +10 字) |
| `log10_paragraphs` | **+0.1356** | 段数越多 |
| `has_colon` | **+0.0748** | 含冒号/竖线 |
| `avg_para_x10` | −0.0012 | 平均段长,几乎为 0 |
| `cat_history` | +0.0890 | 相对 social |
| `cat_finance` | +0.0618 | 相对 social |
| `cat_technology` | −0.1890 | 相对 social |
| `cat_health` | −0.3438 | 相对 social |
| intercept | −1.2614 | 标准化截距还原后的基线偏移 |

系数方向与 R43 的独立统计一致(数字/引号/感叹号/冒号/标题长/正文长/段数均为正),不是重新拟合出来的巧合。

## 四、与旧评分并排的真实对照

### 池化(测试集 9,978,类目内十分位)

| 口径 | Spearman | 95%CI | top-decile lift | lift 95%CI | n | 不同取值数 |
|---|---:|---|---:|---|---:|---:|
| **score_v2** | **+0.1354** | [0.1161, 0.1547] | **1.7308** | [1.535, 2.0086] | 9,978 | **9,932** |
| old_bucket(旧分档) | −0.0651 | [−0.0846, −0.0455] | 1.0426 | [0.7319, 1.0471] | 9,978 | **15** |
| old_reads_score(泄漏对照) | 0.8366 | [0.8306, 0.8424] | 8.998 | [8.5519, 9.088] | 9,978 | 1,091 |

> `old_reads_score = reads/(reads+1000)`,是按 reads 定义的单调映射;其 ρ≈1 是构造使然,**不是预测力**。
> lift 池化只合并测试集 n≥200 的类目(social、finance、technology),类目内分十分位后按样本量加权。

### 分类目

| 类目 | n | v2 ρ [CI] | v2 lift [CI] | old_bucket ρ | old_bucket lift | 结论 |
|---|---:|---|---|---:|---|---|
| social | 7,621 | +0.1201 [0.0979, 0.1422] | 1.4929 [1.268, 1.7532] | −0.0810 | 1.123 | v2 显著为正,旧桶反向 |
| finance | 2,010 | +0.1923 [0.1498, 0.2341] | 2.7869 [2.2727, 3.5135] | −0.0857 | 0.6557 | v2 显著为正,旧桶反向 |
| technology | 228 | +0.1339 [0.0041, 0.2594] | 1.0435 [0, 2.4783] | −0.0916 | 1.5652 | ρ 边缘(bootstrap 下界 0) |
| history | 85 | +0.1551 [−0.0601, 0.3564] | 0.9659 [0, 2.6562] | +0.0416 | 1.9318 | **样本不足**:CI 跨 0,不外推 |
| health | 34 | +0.1661 [−0.1823, 0.4774] | 0.0 [0, 0] | +0.1018 | 0.0 | **样本不足**:CI 跨 0,不外推 |

样本不足的 history(85)、health(34)如实标"不足",不凑数。health 的 lift 为 0 是因为测试集只有 3 篇进
top-decile 且无命中,样本量下不具备解释力。

## 五、两条错位修正的实证

1. **`score_with_metrics` 不读文本** → v2 的分数随标题文本变化:
   测试 `test_v2_is_continuous_while_old_paths_are_bucketed_and_blind` 同时断言
   ①v2 在 50 个不同标题长度上给出 >3 个不同分数;②旧分档 ≤3 档;③同指标、不同标题,旧
   `score_with_metrics` 返回**完全相同**的分。
2. **分档只有 3 个取值** → 在真实 9,978 篇验证集上,旧分档只落 **15** 个桶均值,v2 落 **9,932** 个不同值。

R40 的 n=19 / ρ=−0.06 / 3 取值作为前提原样保留;v2 的 ρ=+0.1354 是另一条并行读数,不推翻前序结论。

## 六、旧实现零改动声明

- `ttxt/analysis/viral.py`(含 `score_with_metrics` / `compute_features`)**未修改**;
- `ttxt/analysis/prediction.py`(含 `_bucket` / `_measured_baselines`)**未修改**;
- A+B 门禁 13/61 = 21.31% `NOT_AVAILABLE`、事实可核率 57/61 = 0.934、取样规则**全部未动**;
- 未触碰任何发布接口,未使用用户账号发布。

本轮唯一的模块级修复是针对 `score_v2` 自身的数值稳定性:`_standardize` 对近似常数(方差浮点残差 ~1e-32)
的列按常数处理(标准差低于 1e-9 置 0、该列缩放值置 0、系数返回 0),避免除以 1e-16 把噪声放大成 ±1 导致
系数变 1e16。真实数据上无近常数特征,重跑读数与修复前完全一致。

## 七、复算命令

```powershell
$env:PYTHONPATH='D:\ttxt'
# 拟合 + 并排评估,重写 03_intelligence/score_v2_study-20261119.json
d:\ttxt\.venv\Scripts\python.exe scripts/score_v2_study.py
# 7 项红线
d:\ttxt\.venv\Scripts\python.exe -m pytest 10_tests/test_r45_score_v2.py -q
# 全量
d:\ttxt\.venv\Scripts\python.exe -m pytest -q
```

## 八、已知限制

- 只有 3 个类目(decimal 意义上 social/finance/technology)样本足以支撑 v2 的分类目结论;history/health 不足。
- 特征是**相关性**意义上有效,不是因果:提高标题含引号比例不一定提高阅读量,只能说明已发布样本里同向。
- 目标仍是 `log10(reads)` 且 reads 含 legacy 批量导入(占样本绝大多数),来源构成差异已在 R43 用
  legacy-only 复核排除,但 v2 未再按来源分层。
- `code` 中的特征是中文标题/正文的正则式统计,跨语言/跨平台未验证。
