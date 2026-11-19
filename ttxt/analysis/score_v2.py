"""R45:版本化连续评分 score_v2(与旧评分并行,旧实现不动)。

设计约束(全部写死在代码里,不靠口头约定):
- **禁止泄漏**:特征只能是发布时点前可获得的信息(标题文本、正文结构、类目)。
  阅读量/点赞/评论/收藏/曝光等结果类字段一律不得进特征,``assert_no_leakage`` 会拒绝。
- **只用过去**:``fit`` 只接受调用方按时间切分后的训练集;``time_split`` 按 ``published_at``
  切,训练集严格早于验证集。
- **可解释**:分位数回归(默认 τ=0.5 中位数回归),IRLS 求解,输出每项特征在
  ``log10(reads)`` 上的原始单位系数;标准化系数只作为求解中间量。
- **纯 Python**:本机没有 numpy/scipy,线性代数用高斯消元自己实现。

与旧路径的关系:``ttxt.analysis.viral.score_with_metrics`` 与 ``prediction._bucket``
**一行不改**,本模块只新增 ``score_v2``,两者并列读数。
"""

from __future__ import annotations

import math
from datetime import datetime

VERSION = "score_v2"
DEFAULT_TAU = 0.5
# 类别参照组:样本量最大的 social,其哑变量不出现,系数解释为"相对 social"。
CATEGORY_REFERENCE = "social"
CATEGORY_DUMMIES = ("finance", "health", "history", "technology")

# 特征名顺序固定,模型文件与预测必须一致。
NUMERIC_FEATURES = (
    "has_digit",        # 标题含数字
    "has_quote",        # 标题含引号
    "has_exclaim",      # 标题含感叹号
    "has_colon",        # 标题含冒号/竖线
    "title_len_x10",    # 标题字数 / 10
    "log10_body_chars", # log10(1 + 正文字数)
    "log10_paragraphs", # log10(1 + 正文非空段数)
    "avg_para_x10",     # 平均段长 / 10
)
CATEGORY_FEATURES = tuple(f"cat_{name}" for name in CATEGORY_DUMMIES)
FEATURE_NAMES = NUMERIC_FEATURES + CATEGORY_FEATURES

# 结果类字段:出现在特征里就是泄漏,直接拒绝。
OUTCOME_FIELDS = frozenset(
    {
        "reads", "read_count", "likes", "like_count", "comments", "comment_count",
        "favorites", "favorite_count", "impressions", "impression_count",
        "shares", "share_count", "score", "viral_score",
    }
)


class LeakageError(ValueError):
    """特征里出现了结果类字段。"""


class InsufficientSample(ValueError):
    """样本量不足以拟合。"""


def assert_no_leakage(names) -> None:
    bad = [name for name in names if str(name).strip().lower() in OUTCOME_FIELDS]
    if bad:
        raise LeakageError(f"特征包含结果类字段(泄漏):{bad}")


_QUOTE_CHARS = ('"', "“", "”", "「", "」", "『", "』", "'")
_EXCLAIM_CHARS = ("!", "！")
_COLON_CHARS = (":", "：", "|", "｜")


def paragraph_count(content: str) -> int:
    return sum(1 for line in (content or "").splitlines() if line.strip())


def extract_features(title: str, content: str, category: str) -> dict:
    """从**发布时点已知**的文本与类目抽取特征;绝不读取任何指标字段。"""
    title = title or ""
    body = content or ""
    paragraphs = paragraph_count(body)
    body_chars = len(body.strip())
    features = {
        "has_digit": 1.0 if any(ch.isdigit() for ch in title) else 0.0,
        "has_quote": 1.0 if any(ch in title for ch in _QUOTE_CHARS) else 0.0,
        "has_exclaim": 1.0 if any(ch in title for ch in _EXCLAIM_CHARS) else 0.0,
        "has_colon": 1.0 if any(ch in title for ch in _COLON_CHARS) else 0.0,
        "title_len_x10": len(title) / 10.0,
        "log10_body_chars": math.log10(1 + body_chars),
        "log10_paragraphs": math.log10(1 + paragraphs),
        "avg_para_x10": (body_chars / paragraphs if paragraphs else 0.0) / 10.0,
    }
    for name in CATEGORY_DUMMIES:
        features[f"cat_{name}"] = 1.0 if (category or "") == name else 0.0
    assert_no_leakage(features)
    return features


def _vector(features: dict) -> list[float]:
    return [float(features.get(name, 0.0)) for name in FEATURE_NAMES]


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """高斯消元(部分主元);奇异时抛 InsufficientSample,不返回垃圾解。"""
    n = len(rhs)
    a = [list(matrix[i]) + [rhs[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise InsufficientSample("设计矩阵奇异(特征共线或样本不足),拒绝给出系数")
        a[col], a[pivot] = a[pivot], a[col]
        lead = a[col][col]
        for row in range(col + 1, n):
            factor = a[row][col] / lead
            if factor == 0.0:
                continue
            for k in range(col, n + 1):
                a[row][k] -= factor * a[col][k]
    solution = [0.0] * n
    for row in range(n - 1, -1, -1):
        total = a[row][n] - sum(a[row][k] * solution[k] for k in range(row + 1, n))
        solution[row] = total / a[row][row]
    return solution


def _weighted_least_squares(design: list[list[float]], y: list[float], weights: list[float], ridge: float) -> list[float]:
    k = len(design[0])
    normal = [[0.0] * k for _ in range(k)]
    rhs = [0.0] * k
    for row, target, weight in zip(design, y, weights):
        if weight <= 0.0:
            continue
        for i in range(k):
            rhs[i] += weight * row[i] * target
            for j in range(i, k):
                normal[i][j] += weight * row[i] * row[j]
    for i in range(k):
        for j in range(i + 1, k):
            normal[j][i] = normal[i][j]
        normal[i][i] += ridge
    return _solve(normal, rhs)


_STD_FLOOR = 1e-9


def _standardize(rows: list[list[float]]) -> tuple[list[list[float]], list[float], list[float]]:
    """列标准化。

    近似常数(方差浮点残差约为 1e-32)的列必须按常数处理并置 0,否则除以 1e-16 会把
    噪声放大成 ±1,系数被抬到 1e16 量级。标准差低于 ``_STD_FLOOR`` 一律视为常数。
    """
    n, k = len(rows), len(rows[0])
    means = [sum(row[j] for row in rows) / n for j in range(k)]
    stds = []
    for j in range(k):
        variance = sum((row[j] - means[j]) ** 2 for row in rows) / n
        sd = math.sqrt(variance)
        stds.append(sd if sd > _STD_FLOOR else 0.0)
    scaled = [
        [(row[j] - means[j]) / stds[j] if stds[j] else 0.0 for j in range(k)]
        for row in rows
    ]
    return scaled, means, stds


def fit(rows: list[dict], y: list[float], *, tau: float = DEFAULT_TAU,
        iterations: int = 60, ridge: float = 1e-6, min_train: int = 200) -> dict:
    """分位数回归(IRLS)。rows 是 ``extract_features`` 的输出,``y`` 是目标(已去偏的 log10 reads)。

    只拟合训练集;调用方负责按时间切分。返回的 model 字典可直接 JSON 序列化。
    """
    if len(rows) != len(y):
        raise ValueError("特征行数与目标长度不一致")
    if len(rows) < min_train:
        raise InsufficientSample(f"训练样本 {len(rows)} < 最少 {min_train},拒绝拟合")
    if not 0.0 < tau < 1.0:
        raise ValueError("tau 必须在 (0,1) 之间")
    raw = [_vector(features) for features in rows]
    for name in FEATURE_NAMES:
        assert_no_leakage([name])
    scaled, means, stds = _standardize(raw)
    design = [[1.0] + row for row in scaled]
    beta = [0.0] * len(design[0])
    beta[0] = _median(y)
    for _ in range(iterations):
        residual = [target - sum(b * v for b, v in zip(beta, row)) for row, target in zip(design, y)]
        weights = [1.0 / max(abs(r), 1e-3) for r in residual]
        beta_next = _weighted_least_squares(design, y, weights, ridge)
        delta = max(abs(a - b) for a, b in zip(beta_next, beta))
        beta = beta_next
        if delta < 1e-8:
            break
    coef_std = beta[1:]
    coef_raw = [c / s if s else 0.0 for c, s in zip(coef_std, stds)]
    intercept_raw = beta[0] - sum(c * m for c, m in zip(coef_raw, means))
    return {
        "version": VERSION,
        "method": "quantile_regression_irls",
        "tau": tau,
        "n_train": len(rows),
        "feature_names": list(FEATURE_NAMES),
        "category_reference": CATEGORY_REFERENCE,
        "coefficients_raw": {name: round(value, 6) for name, value in zip(FEATURE_NAMES, coef_raw)},
        "intercept_raw": round(intercept_raw, 6),
        "standardization": {
            "mean": {name: round(m, 6) for name, m in zip(FEATURE_NAMES, means)},
            "std": {name: round(s, 6) for name, s in zip(FEATURE_NAMES, stds)},
        },
    }


def predict(model: dict, features: dict) -> float:
    """按模型原始单位系数打分:预测的 log10(reads) 相对类目基线。"""
    total = float(model.get("intercept_raw", 0.0))
    coefficients = model.get("coefficients_raw") or {}
    for name in model.get("feature_names") or FEATURE_NAMES:
        if name in OUTCOME_FIELDS:
            raise LeakageError(f"模型特征包含结果类字段:{name}")
        total += float(coefficients.get(name, 0.0)) * float(features.get(name, 0.0))
    return total


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        raise InsufficientSample("空样本无法取中位数")
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _parse_time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def time_split(rows: list[dict], *, train_ratio: float = 0.7) -> tuple[list[dict], list[dict]]:
    """按 ``published_at`` 升序切分,训练集严格早于验证集(无未来数据)。

    无法解析发布时间的行直接丢弃 —— 不能猜,也不能塞进任意一侧。
    """
    dated = []
    dropped = 0
    for row in rows:
        moment = _parse_time(row.get("published_at"))
        if moment is None:
            dropped += 1
            continue
        dated.append((moment, row))
    dated.sort(key=lambda item: (item[0], str(item[1].get("id"))))
    cut = int(len(dated) * train_ratio)
    train = [{**row, "published_at": moment.isoformat()} for moment, row in dated[:cut]]
    test = [{**row, "published_at": moment.isoformat()} for moment, row in dated[cut:]]
    return train, test
