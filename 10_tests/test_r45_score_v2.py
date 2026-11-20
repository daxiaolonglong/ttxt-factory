"""R45:score_v2 的红线(只新增,不改旧实现)。"""
from __future__ import annotations

import inspect
import math
import random

import pytest

from ttxt.analysis import prediction, score_v2, viral


def _row(title: str, content: str, category: str = "social") -> dict:
    return {"title": title, "content": content, "category": category}


def test_features_are_text_only_and_have_no_outcome_fields():
    features = score_v2.extract_features("3个数字｜引号“爆”!", "第一段。\n\n第二段。", "finance")
    assert set(features) == set(score_v2.FEATURE_NAMES)
    assert not (set(features) & score_v2.OUTCOME_FIELDS)
    assert features["has_digit"] == 1.0
    assert features["has_quote"] == 1.0
    assert features["has_exclaim"] == 1.0
    assert features["has_colon"] == 1.0
    assert features["cat_finance"] == 1.0
    assert "cat_social" not in features, "social 是参照组,不能同时出现哑变量(否则共线)"


def test_leakage_guard_rejects_outcome_fields():
    with pytest.raises(score_v2.LeakageError):
        score_v2.assert_no_leakage(["has_digit", "reads"])
    with pytest.raises(score_v2.LeakageError):
        score_v2.predict({"intercept_raw": 0.0, "feature_names": ["reads"], "coefficients_raw": {"reads": 1.0}}, {"reads": 10})


def test_extract_features_is_deterministic_and_ignores_metrics():
    first = score_v2.extract_features("标题", "正文\n\n第二段", "social")
    second = score_v2.extract_features("标题", "正文\n\n第二段", "social")
    assert first == second
    # 参数表里没有任何结果类字段
    params = set(inspect.signature(score_v2.extract_features).parameters)
    assert not (params & score_v2.OUTCOME_FIELDS)


def test_time_split_keeps_train_strictly_before_test():
    rows = [
        {"id": i, "title": f"t{i}", "content": "x", "category": "social",
         "published_at": f"2026-11-{1 + i // 10:02d}T0{i % 10}:00:00+08:00"}
        for i in range(20)
    ]
    rows.append({"id": 99, "title": "bad", "content": "x", "category": "social", "published_at": "not-a-time"})
    train, test = score_v2.time_split(rows, train_ratio=0.7)
    assert len(train) + len(test) == 20, "无法解析发布时间的行必须丢弃,不能进任意一侧"
    assert max(r["published_at"] for r in train) <= min(r["published_at"] for r in test)


def test_fit_refuses_small_sample():
    rows = [score_v2.extract_features(f"标题{i}", "正文", "social") for i in range(10)]
    with pytest.raises(score_v2.InsufficientSample):
        score_v2.fit(rows, [math.log10(10 + i) for i in range(10)])


def test_quantile_fit_recovers_positive_effect_direction():
    rng = random.Random(7)
    rows, targets = [], []
    # 两个标题字符数必须相同,否则 title_len_x10 与 has_digit 完全共线,效应会被摊到两列上
    for _ in range(400):
        has_digit = rng.random() < 0.5
        title = "9个信号" if has_digit else "一些信号"
        rows.append(score_v2.extract_features(title, "正文\n\n第二段", "social"))
        targets.append((1.0 if has_digit else 0.0) * 0.5 + rng.random() * 0.02)
    model = score_v2.fit(rows, targets)
    assert model["coefficients_raw"]["has_digit"] == pytest.approx(0.5, abs=0.06)
    assert model["version"] == "score_v2"


def test_v2_is_continuous_while_old_paths_are_bucketed_and_blind():
    # 用 50 个不同标题长度造出连续特征;目标随长度变化,否则全类目独立时系数≈0、预测退化成一个值
    rows, targets = [], []
    for i in range(400):
        title = "标题" + "字" * (i % 50)
        rows.append(score_v2.extract_features(title, "正文\n\n第二段", "social"))
        targets.append((i % 50) * 0.1)
    model = score_v2.fit(rows, targets)
    scores = {round(score_v2.predict(model, features), 6) for features in rows}
    assert len(scores) > 3, "score_v2 必须是连续分,不能退化成少数几档"

    titles = ["2026年三大变化", "为什么这件事重要", "“罕见”信号出现", "9个细节"]
    buckets = {prediction._bucket(viral.compute_features(title, "正文", 2)) for title in titles}
    assert len(buckets) <= 3, "旧分档口径本身只有 ≤3 个取值(并列读数,不修改)"

    # 旧 score_with_metrics 完全不读文本:同指标、不同标题得到同一个分数
    metrics = {"reads": 1000, "likes": 10, "comments": 2, "favorites": None, "impressions": 5000}
    left = viral.score_with_metrics(viral.compute_features("2026年三大变化", "正文", 6), metrics)
    right = viral.score_with_metrics(viral.compute_features("为什么这件事重要", "正文", 9), metrics)
    assert left == right, "旧实现跳过文本特征(这是 R43 的错位事实,只并列记录,不改)"


def test_tau_default_weights_match_r45_bit_for_bit():
    """R51:τ=0.5 的 IRLS 权重必须与 R45 生产拟合逐项等值(逐位),锁死已发布模型不变。

    R45 用 ``1/max(|r|,1e-3)`` 拟合出 ``03_intelligence/score_v2_model.json``;本测试证明
    修 tau 后的 ``_pinball_weights(·, 0.5)`` 与旧式逐项相同 ⇒ 后续确定性求解链不动,
    生产模型系数逐位不变。
    """
    residuals = [0.0, -0.0, 1e-9, -1e-9, 0.5, -0.5, 123.456, -123.456,
                 1e-3, -1e-3, 2e-4, -2e-4, 0.25, -0.75]
    legacy = [1.0 / max(abs(r), 1e-3) for r in residuals]
    assert score_v2._pinball_weights(residuals, 0.5) == legacy


def test_tau_is_a_real_parameter_and_moves_the_quantile():
    """R51:tau 不再是假参数 —— τ≠0.5 必须真实改变拟合结果,且分位数次序正确。"""
    rng = random.Random(11)
    rows = [score_v2.extract_features("某个标题", "正文\n\n第二段", "social") for _ in range(400)]
    targets = [rng.random() for _ in range(400)]
    half = score_v2.fit(rows, targets, tau=0.5)
    high = score_v2.fit(rows, targets, tau=0.75)
    low = score_v2.fit(rows, targets, tau=0.25)
    assert (half["tau"], high["tau"], low["tau"]) == (0.5, 0.75, 0.25)
    # 特征列全常数 ⇒ 预测只剩截距,即拟合分位数本身:次序必须 low < median < high
    p_low = score_v2.predict(low, rows[0])
    p_half = score_v2.predict(half, rows[0])
    p_high = score_v2.predict(high, rows[0])
    assert p_low < p_half < p_high, "τ 改变拟合分位数:R51 前 tau 从不进权重,三条预测会全等"
    assert half["coefficients_raw"] != high["coefficients_raw"] or p_half != p_high
