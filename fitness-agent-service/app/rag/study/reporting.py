"""不包含问题/用户标识/文档正文的可分享汇总；失败和不可回答问题单列。"""

from __future__ import annotations

import random
from collections import Counter
from statistics import mean
from typing import Any

from .contracts import unseal
from .runtime import METHODS


def score(row: dict[str, Any], method: str, k: int) -> dict[str, float]:
    relevant = set(row["label"]["relevant_ids"])
    returned = list(dict.fromkeys(row["results"][method]))[:k]
    hits = len(relevant.intersection(returned))
    return {
        "recall": hits / len(relevant) if relevant else 0.0,
        "precision": hits / k,
        "hit_rate": float(hits > 0),
        "mrr": next((1 / (i + 1) for i, item in enumerate(returned) if item in relevant), 0.0),
    }


def summarize(artifact: dict[str, Any]) -> dict[str, Any]:
    run = unseal(artifact, "run")
    rows = run["cases"]
    answerable = [row for row in rows if row["label"]["category"] == "answerable"]
    absent = [row for row in rows if row["label"]["category"] == "no_answer"]
    k = run["protocol"]["top_k"]
    metrics: dict[str, Any] = {}
    for method in METHODS:
        scores = [score(row, method, k) for row in answerable]
        metrics[method] = {
            key: mean([item[key] for item in scores]) if scores else None
            for key in ("recall", "precision", "hit_rate", "mrr")
        }
        metrics[method]["no_answer_nonempty_context_rate"] = (
            mean([float(bool(row["results"][method])) for row in absent]) if absent else None
        )
    # 配对、按冻结连通组聚类自助抽样；同一用户/同义问题不能当独立样本。
    groups: dict[str, list[float]] = {}
    for row in answerable:
        groups.setdefault(row["group_id"], []).append(
            score(row, "hybrid_rerank", k)["recall"] - score(row, "vector", k)["recall"]
        )
    interval = None
    if len(groups) >= 2:
        rng = random.Random(run["protocol"]["split_seed"])
        values = list(groups.values())
        draws = sorted(
            mean([v for group in rng.choices(values, k=len(values)) for v in group])
            for _ in range(2000)
        )
        interval = [100 * draws[49], 100 * draws[1949]]
    before = metrics["vector"]["recall"]
    after = metrics["hybrid_rerank"]["recall"]
    errors = sum(bool(row["error"]) for row in rows)
    difference = after - before if after is not None and before is not None else None
    # 即使CI为正，小组数也不推荐稳定推广结论。不是样本量功效设计的替代品。
    supported = bool(interval and interval[0] > 0 and len(groups) >= 20 and errors == 0)
    return {
        "study_id": run["protocol"]["study_id"],
        "source_kind": run["protocol"]["source_kind"],
        "corpus_origin": run["protocol"]["corpus_origin"],
        "reviewer_kinds": run["reviewer_kinds"],
        "split": run["split"],
        "top_k": k,
        "run_sha256": artifact["sha256"],
        "frozen_sha256": run["frozen_sha256"],
        "created_at": run["created_at"],
        "case_count": len(rows),
        "answerable_count": len(answerable),
        "category_counts": dict(Counter(row["label"]["category"] for row in rows)),
        "failure_count": errors,
        "independent_group_count": len(groups),
        "methods": metrics,
        "comparison": {
            "baseline": "vector",
            "candidate": "hybrid_rerank",
            "recall_delta_percentage_points": 100 * difference if difference is not None else None,
            "recall_relative_change_percent": 100 * difference / before
            if difference is not None and before
            else None,
            "cluster_bootstrap_95_ci_percentage_points": interval,
            "stable_positive_difference_supported": supported,
            "interpretation": "同一冻结测试集上的架构消融比较；不是历史生产升级前后对比",
        },
        "runtime": run["runtime"],
        "limits": [
            "合成记录不是实际用户问题；来源声明和审核者姓名不是真实性证明",
            "Recall按可回答问题逐题宏平均；失败按0计入，不可回答问题不混入分母",
            "人工整理的检索输入：未评估Supervisor路由、多轮改写、生成正确性或拒答质量",
            "无答案返回上下文率不是错误回答率；此工具不调用回答模型",
            "目录权限验证仅约束本次采样，不能替代全面安全审计",
            "组合执行耗时包含多种方案，不能用来声称响应提升或TTFT下降",
            "上线收益、续费提升和并发容量须另行采集，不由召回实验推出",
        ],
    }


def markdown(summary: dict[str, Any]) -> str:
    def percent(value: Any) -> str:
        return "不适用" if value is None else f"{value * 100:.2f}%"

    lines = [
        "# RAG 问题研究评测报告",
        "",
        f"- 研究：{summary['study_id']}；来源：{summary['source_kind']}",
        f"- 语料：{summary['corpus_origin']}；评审：{', '.join(summary['reviewer_kinds'])}",
        (
            f"- 划分：{summary['split']}；总问题：{summary['case_count']}；"
            f"可回答：{summary['answerable_count']}；失败：{summary['failure_count']}"
        ),
        f"- 独立问题组：{summary['independent_group_count']}；Top-K：{summary['top_k']}",
        "",
        "|方案|Recall@K|Precision@K|命中率|MRR|无答案返回上下文率|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for method, metrics in summary["methods"].items():
        lines.append(
            "|"
            + method
            + "|"
            + "|".join(
                percent(metrics[key])
                for key in (
                    "recall",
                    "precision",
                    "hit_rate",
                    "mrr",
                    "no_answer_nonempty_context_rate",
                )
            )
            + "|"
        )
    comparison = summary["comparison"]
    lines += [
        "",
        "## 对比结论",
        "",
        comparison["interpretation"],
        "",
        f"召回差值（百分点）：{comparison['recall_delta_percentage_points']}。",
        f"相对变化（%）：{comparison['recall_relative_change_percent']}。",
        f"聚类自助法95%区间（百分点）：{comparison['cluster_bootstrap_95_ci_percentage_points']}。",
        f"是否支持稳定正向差异：{comparison['stable_positive_difference_supported']}。",
        "",
        "## 解读边界",
        "",
        *[f"- {item}" for item in summary["limits"]],
        "",
        "## 复查依据",
        "",
        f"运行摘要：`{summary['run_sha256']}`",
        "",
        f"冻结集摘要：`{summary['frozen_sha256']}`",
        "",
    ]
    return "\n".join(lines)
