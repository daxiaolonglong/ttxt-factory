"""R47 修正:对照脚本统计函数(percentile / ranks_with_ties / spearman /
actual_percentiles / top_decile)的单元测试 —— 并列值、小样本基准、分位无偏性。

这些函数是 Spearman / lift 读数的地基,读数要能当证据,地基必须先有测试。
"""
from __future__ import annotations

import statistics

import pytest

from scripts.score_v2_production_compare import (
    actual_percentiles,
    percentile,
    ranks_with_ties,
    spearman,
    top_decile,
)


def test_percentile_linear_interpolation():
    assert percentile([], 0.5) == 0.0
    assert percentile([7.0], 0.9) == 7.0
    assert percentile([1, 2, 3, 4], 0.0) == 1.0
    assert percentile([1, 2, 3, 4], 1.0) == 4.0
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([0, 10], 0.9) == 9.0


def test_ranks_with_ties_average_rank():
    # 并列取平均名次:两个最小值并列名次 (1+2)/2=1.5
    assert ranks_with_ties([5.0, 1.0, 1.0, 3.0]) == [4.0, 1.5, 1.5, 3.0]
    # 全部并列 ⇒ 全部平均名次
    assert ranks_with_ties([2.0, 2.0, 2.0]) == [2.0, 2.0, 2.0]
    # 无并列即 1..n
    assert ranks_with_ties([10.0, 20.0, 30.0]) == [1.0, 2.0, 3.0]


def test_spearman_perfect_monotone_and_inverse():
    assert spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_with_ties_matches_known_value():
    # xs 有并列:rank([1,1,2])=[1.5,1.5,3](均值 2),ys rank=[10,20,30]→[1,2,3](均值 2)
    # dx=[−0.5,−0.5,1],dy=[−1,0,1]:Σ(dx·dy)=0.5+0+1=1.5;Σdx²=1.5;Σdy²=2
    # 脚本口径 ρ=Σ(dx·dy)/sqrt(Σdx²·Σdy²)=1.5/sqrt(3)=√3/2 ≈ 0.8660254
    value = spearman([1, 1, 2], [10, 20, 30])
    assert value is not None
    assert value == pytest.approx(1.5 / ((1.5 * 2.0) ** 0.5))
    # 一方全并列 ⇒ 无定义,如实返回 None(不硬造 0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    # n<3 不给 Spearman
    assert spearman([1, 2], [1, 2]) is None


def test_actual_percentiles_unbiased_mean_half():
    # 无偏基准:(rank−0.5)/n 的全体均值**恰为 0.5**,任意 n 成立
    for n in (2, 12, 100):
        pcts = actual_percentiles([float(i) for i in range(n)])
        assert abs(statistics.mean(pcts) - 0.5) < 1e-12
    # n=12 基准:旧口径 rank/n 期望 0.5+1/24≈0.5417,(rank−0.5)/n 才是 0.5
    pcts12 = actual_percentiles([float(i) for i in range(12)])
    assert pcts12[0] == 0.5 / 12
    assert pcts12[-1] == 11.5 / 12
    assert abs(statistics.mean(pcts12) - 0.5) < 1e-12
    # 并列组的分位均值仍保持整体 0.5
    tied = actual_percentiles([5.0, 5.0, 5.0, 5.0])
    assert abs(statistics.mean(tied) - 0.5) < 1e-12


def test_top_decile_baseline_and_random_expectation():
    # 少于 10 条不评估(小样本 lift 无解释力)
    assert top_decile([(float(i), 0.5) for i in range(9)]) is None
    # 完美排序:前 10% 全是实测最高 10% ⇒ lift 明显 >1
    n = 100
    perfect = top_decile([(float(i), (i + 0.5) / n) for i in range(n)])
    assert perfect is not None
    assert perfect["selected"] == 10
    assert perfect["lift"] > 1.8
    # 反向排序:前 10% 是实测最低 10% ⇒ lift 明显 <1
    reversed_pairs = [(float(n - i), (i + 0.5) / n) for i in range(n)]
    bad = top_decile(reversed_pairs)
    assert bad is not None and bad["lift"] < 0.2
    # "分数与实测无关"的构造(分数与分位完全脱钩循环排列):lift 应贴近 1
    # 取偏移半个圆周的配对,选中的 top-decile 分数对应的分位均匀铺开
    shifted = [(float((i + n // 2) % n), (i + 0.5) / n) for i in range(n)]
    neutral = top_decile(shifted)
    assert neutral is not None
    assert 0.9 <= neutral["lift"] <= 1.1
