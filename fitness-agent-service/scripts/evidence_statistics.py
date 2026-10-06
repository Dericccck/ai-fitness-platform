"""实验报告纯函数；不把小样本、合成样本或者统计相关性伪装为线上收益。"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from statistics import NormalDist, mean, median
from typing import Any


def percentile(values: Sequence[float], probability: float) -> float:
    if not values or not 0 <= probability <= 1:
        raise ValueError("分位数输入无效")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    left = int(position)
    fraction = position - left
    return ordered[left] * (1 - fraction) + ordered[min(left + 1, len(ordered) - 1)] * fraction


def describe(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p95": None}
    return {
        "n": len(values),
        "mean": mean(values),
        "median": median(values),
        "p95": percentile(values, 0.95),
        "min": min(values),
        "max": max(values),
    }


def paired_change(before: Sequence[float], after: Sequence[float]) -> dict[str, Any]:
    """正的 reduction 表示减少；配对 bootstrap CI 只反映这些固定样本的变异。"""
    if not before or len(before) != len(after):
        raise ValueError("必须使用等长非空配对样本")
    changes = [b - a for b, a in zip(before, after, strict=True)]
    rng = random.Random(20261006)
    distribution = [mean(rng.choices(changes, k=len(changes))) for _ in range(2000)]
    average_before, average_after = mean(before), mean(after)
    return {
        "before": describe(before),
        "after": describe(after),
        "mean_reduction": average_before - average_after,
        "relative_reduction_percent": (
            100 * (average_before - average_after) / average_before if average_before else None
        ),
        "paired_mean_reduction_ci95": [
            percentile(distribution, 0.025),
            percentile(distribution, 0.975),
        ],
        "ci_scope": "固定测试用例/时段的配对bootstrap；不代表真实用户总体或跨时段供应商性能",
    }


def wilson(successes: int, total: int, alpha: float = 0.05) -> tuple[float, float]:
    if (
        isinstance(successes, bool)
        or isinstance(total, bool)
        or not (
            isinstance(successes, int)
            and isinstance(total, int)
            and total > 0
            and 0 <= successes <= total
            and 0 < alpha < 1
        )
    ):
        raise ValueError("有效样本数和成功数必须为整数且范围正确")
    z = NormalDist().inv_cdf(1 - alpha / 2)
    p = successes / total
    divisor = 1 + z * z / total
    center = (p + z * z / (2 * total)) / divisor
    width = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / divisor
    return max(0.0, center - width), min(1.0, center + width)


def renewal_effect(data: dict[str, Any]) -> dict[str, Any]:
    """独立用户RCT的ITT分析；没有来源/成熟窗口/随机化声明时拒绝写因果提升。"""
    control, treatment = data.get("control", {}), data.get("treatment", {})
    if not control.get("eligible_users") or not treatment.get("eligible_users"):
        return {"status": "DATA_INSUFFICIENT", "renewal_uplift_pp": None}
    nc, nt = control["eligible_users"], treatment["eligible_users"]
    kc, kt = control.get("net_paid_renewals"), treatment.get("net_paid_renewals")
    lc, uc = wilson(kc, nc)
    lt, ut = wilson(kt, nt)
    pc, pt = kc / nc, kt / nt
    difference = pt - pc
    # Newcombe hybrid score interval: same formula as statsmodels method='newcomb'.
    interval = [
        difference - math.sqrt((pt - lt) ** 2 + (uc - pc) ** 2),
        difference + math.sqrt((ut - pt) ** 2 + (pc - lc) ** 2),
    ]
    prerequisites = (
        data.get("data_kind") == "real_observed"
        and data.get("independent_user_randomization") is True
        and data.get("assignment_before_intervention") is True
        and data.get("analysis") == "ITT"
        and data.get("same_mature_observation_window") is True
        and data.get("refund_window_closed") is True
        and data.get("sample_ratio_check_passed") is True
        and bool(data.get("source_reference"))
        and bool(data.get("observation_start"))
        and bool(data.get("observation_end"))
    )
    return {
        "status": "RCT_DECLARED_REQUIRES_AUDIT" if prerequisites else "DESCRIPTIVE_ONLY",
        "control_rate": pc,
        "treatment_rate": pt,
        "observed_difference_pp": 100 * difference,
        "relative_difference_percent": 100 * difference / pc if pc else None,
        "difference_ci95_pp": [100 * endpoint for endpoint in interval],
        "renewal_uplift_pp": None,
        "positive_interval": interval[0] > 0,
        "interpretation": "程序不能核验输入真实性；经来源/随机分组/干扰/流失审核后才可写因果收益。集群随机化不能使用此独立用户区间。",
    }


def approximate_users_per_group(
    baseline_rate: float, target_rate: float, *, alpha: float = 0.05, power: float = 0.8
) -> int:
    """两侧、等大小、独立用户两比例正态近似；只是实验规划，不是实测样本。"""
    if not (
        0 < baseline_rate < 1
        and 0 < target_rate < 1
        and baseline_rate != target_rate
        and 0 < alpha < 1
        and 0 < power < 1
    ):
        raise ValueError("实验规划参数无效")
    average = (baseline_rate + target_rate) / 2
    normal = NormalDist()
    numerator = (
        normal.inv_cdf(1 - alpha / 2) * math.sqrt(2 * average * (1 - average))
        + normal.inv_cdf(power)
        * math.sqrt(baseline_rate * (1 - baseline_rate) + target_rate * (1 - target_rate))
    ) ** 2
    return math.ceil(numerator / (target_rate - baseline_rate) ** 2)
