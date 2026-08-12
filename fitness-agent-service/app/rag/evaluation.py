"""与模型供应商解耦的离线检索评测基础能力。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalEvalCase:
    """一条带标注的查询，以及期望命中的已授权文档或分块 ID。"""

    case_id: str
    query: str
    relevant_ids: frozenset[str]
    forbidden_ids: frozenset[str] = frozenset()
    retrieved_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalEvalResult:
    """一条评测用例的指标结果。"""

    case_id: str
    recall_at_k: float
    reciprocal_rank: float
    forbidden_hits: int = 0


def evaluate_case(
    case: RetrievalEvalCase,
    retrieved_ids: Sequence[str],
    *,
    k: int,
) -> RetrievalEvalResult:
    """根据稳定 ID 计算 Recall@K 和 MRR，而不是依赖模型生成的解释。"""

    if k < 1:
        raise ValueError("evaluation k must be positive")
    top_ids = list(retrieved_ids[:k])
    unique_top_ids = set(top_ids)
    hits = len(unique_top_ids.intersection(case.relevant_ids))
    recall = hits / len(case.relevant_ids) if case.relevant_ids else 0.0
    reciprocal_rank = next(
        (
            1.0 / index
            for index, identifier in enumerate(top_ids, start=1)
            if identifier in case.relevant_ids
        ),
        0.0,
    )
    forbidden_hits = len(unique_top_ids.intersection(case.forbidden_ids))
    return RetrievalEvalResult(case.case_id, recall, reciprocal_rank, forbidden_hits)


def aggregate_results(results: Sequence[RetrievalEvalResult]) -> dict[str, float]:
    """汇总用例级指标，供 CI 阈值检查和实验结果对比使用。"""

    if not results:
        return {"recall_at_k": 0.0, "mrr": 0.0, "case_count": 0.0}
    return {
        "recall_at_k": sum(item.recall_at_k for item in results) / len(results),
        "mrr": sum(item.reciprocal_rank for item in results) / len(results),
        "case_count": float(len(results)),
        "forbidden_hits": float(sum(item.forbidden_hits for item in results)),
    }


@dataclass(frozen=True)
class RetrievalEvalThresholds:
    """CI 使用的最低质量门槛和零容忍安全门禁。"""

    min_recall_at_k: float
    min_mrr: float
    max_forbidden_hits: int = 0

    def validate(self, metrics: dict[str, float]) -> list[str]:
        """返回可执行的失败原因，不静默接受质量回退。"""

        failures: list[str] = []
        if metrics["recall_at_k"] < self.min_recall_at_k:
            failures.append(f"recall@k {metrics['recall_at_k']:.4f} < {self.min_recall_at_k:.4f}")
        if metrics["mrr"] < self.min_mrr:
            failures.append(f"mrr {metrics['mrr']:.4f} < {self.min_mrr:.4f}")
        if metrics["forbidden_hits"] > self.max_forbidden_hits:
            failures.append(
                f"forbidden hits {int(metrics['forbidden_hits'])} > {self.max_forbidden_hits}"
            )
        return failures


def case_from_mapping(data: dict[str, Any]) -> RetrievalEvalCase:
    """加载明确声明已授权 ID 和禁止 ID 的 JSON 回归用例。"""

    return RetrievalEvalCase(
        case_id=str(data["case_id"]),
        query=str(data["query"]),
        relevant_ids=frozenset(str(item) for item in data["relevant_ids"]),
        forbidden_ids=frozenset(str(item) for item in data.get("forbidden_ids", [])),
        retrieved_ids=tuple(str(item) for item in data.get("retrieved_ids", [])),
    )
