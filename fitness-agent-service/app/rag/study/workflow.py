"""研究工件状态机：采集、独立评审、分歧裁决、按用户/问题族冻结。"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

from .contracts import (
    Label,
    Protocol,
    Question,
    Review,
    ScopeGrant,
    Triage,
    digest,
    normalized_query,
    now,
    seal,
    unique,
    unseal,
)


def prepare(
    protocol_data: dict[str, Any],
    question_data: list[dict[str, Any]],
    scope_data: list[dict[str, Any]],
    triage_data: list[dict[str, Any]],
) -> dict[str, Any]:
    protocol = Protocol.model_validate(protocol_data)
    questions = unique([Question.model_validate(q) for q in question_data], "case_id")
    scopes = unique([ScopeGrant.model_validate(s) for s in scope_data], "participant_id")
    triage = unique([Triage.model_validate(t) for t in triage_data], "case_id")
    if not questions or len(questions) > 5000 or questions.keys() != triage.keys():
        raise ValueError("必须完整分类全部采集问题，单研究1至5000条；不能静默丢弃问题")
    cases = []
    for case_id, question in questions.items():
        item = triage[case_id]
        if (
            question.source_kind != protocol.source_kind
            or question.consent_version != protocol.consent_version
            or question.participant_id not in scopes
            or not item.redaction_reviewed
            or not item.source_reviewed
        ):
            raise ValueError("问题来源、同意版本、授权或采集复核未完成")
        if item.retrieval_query != question.query and len(item.context_resolution_note) < 5:
            raise ValueError("改写检索问题必须说明依据，保留原始问题与前文")
        cases.append({**question.model_dump(), **item.model_dump()})
    return seal(
        "prepared",
        {
            "protocol": protocol.model_dump(),
            "cases": cases,
            "scopes": {key: row.model_dump() for key, row in scopes.items()},
            "created_at": now(),
            "source_attestation": "来源与授权由研究负责人核验；程序不证明人类身份或采集真实性",
        },
    )


def review_packet(pool_artifact: dict[str, Any], reviewer: str, kind: str) -> dict[str, Any]:
    pool = unseal(pool_artifact, "pool")
    if kind not in {"human", "scripted_fixture"} or len(reviewer) < 2:
        raise ValueError("评审者信息无效")
    if pool["prepared"]["protocol"]["source_kind"] != "synthetic" and kind != "human":
        raise ValueError("真实来源问题需要人工评审")
    # 顺序与检索排名无关；不暴露基线/候选方案、分数或其他评审者的标注。
    cases = []
    for row in pool["cases"]:
        source = row["case"]
        cases.append(
            {
                "case_id": source["case_id"],
                "query": source["query"],
                "context": source["context"],
                "retrieval_query": source["retrieval_query"],
                "topic": source["topic"],
                "candidates": sorted(row["candidate_ids"]),
                "catalog": sorted(row["catalog"], key=lambda item: item["id"]),
            }
        )
    return {
        "packet_type": "annotation",
        "pool_sha256": pool_artifact["sha256"],
        "source_kind": pool["prepared"]["protocol"]["source_kind"],
        "reviewer_id": reviewer,
        "reviewer_kind": kind,
        "cases": cases,
        "instructions": "独立确认类别并查找相关证据；可搜索完整授权目录补充候选外证据。记录标注理由和搜索过程。分块是计分单位。",
    }


def validate_review(
    pool_artifact: dict[str, Any], review_data: dict[str, Any], *, subset: set[str] | None = None
) -> Review:
    pool = unseal(pool_artifact, "pool")
    review = Review.model_validate(review_data)
    if review.pool_sha256 != pool_artifact["sha256"] or not review.independent_review_declared:
        raise ValueError("评审不属于该候选包或尚未声明独立复核")
    if (
        pool["prepared"]["protocol"]["source_kind"] != "synthetic"
        and review.reviewer_kind != "human"
    ):
        raise ValueError("真实来源问题不能使用脚本标注代替人工评审")
    rows = {row["case"]["case_id"]: row for row in pool["cases"]}
    labels = unique(review.labels, "case_id")
    if labels.keys() != (subset if subset is not None else rows.keys()):
        raise ValueError("评审必须覆盖规定的问题集合")
    for case_id, label in labels.items():
        allowed = {entry["id"] for entry in rows[case_id]["catalog"]}
        if not set(label.relevant_ids) <= allowed:
            raise ValueError("相关证据不在该问题的已授权知识快照内")
    return review


def reconcile(
    pool_artifact: dict[str, Any], a_data: dict[str, Any], b_data: dict[str, Any]
) -> dict[str, Any]:
    a, b = validate_review(pool_artifact, a_data), validate_review(pool_artifact, b_data)
    if a.reviewer_id == b.reviewer_id:
        raise ValueError("需要不同评审者的独立标注")
    b_labels = {label.case_id: label for label in b.labels}
    agreed, conflicts = [], []
    for left in a.labels:
        right = b_labels[left.case_id]
        if left.category == right.category and set(left.relevant_ids) == set(right.relevant_ids):
            agreed.append(left.model_dump())
        else:
            conflicts.append(
                {"case_id": left.case_id, "a": left.model_dump(), "b": right.model_dump()}
            )
    return seal(
        "reconciliation",
        {
            "pool_sha256": pool_artifact["sha256"],
            "reviewers": [a.reviewer_id, b.reviewer_id],
            "reviewer_kinds": [a.reviewer_kind, b.reviewer_kind],
            "review_sha256": [digest(a_data), digest(b_data)],
            "agreed": agreed,
            "conflicts": conflicts,
            "exact_agreement_rate": len(agreed) / len(a.labels),
        },
    )


def assign_splits(cases: list[dict[str, Any]], protocol: Protocol) -> dict[str, tuple[str, str]]:
    """用户、完全相同表达、预声明同义问题族的连通分量不能跨开发/测试集。"""
    parents = {row["case_id"]: row["case_id"] for row in cases}

    def find(key: str) -> str:
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    owners: dict[str, str] = {}
    for row in cases:
        keys = [
            "user:" + row["participant_id"],
            "query:" + normalized_query(row["retrieval_query"]),
        ]
        if row["family_id"]:
            keys.append("family:" + row["family_id"])
        for key in keys:
            if key in owners:
                parents[find(row["case_id"])] = find(owners[key])
            owners[key] = row["case_id"]
    groups: dict[str, list[str]] = {}
    for row in cases:
        groups.setdefault(find(row["case_id"]), []).append(row["case_id"])
    components = sorted((sorted(ids) for ids in groups.values()), key=lambda ids: ids[0])
    if len(components) < 2:
        raise ValueError("至少需要两个互不重叠的用户/问题族，才能独立划分开发集与测试集")
    random.Random(protocol.split_seed).shuffle(components)
    count = max(1, min(len(components) - 1, round(len(components) * protocol.test_fraction)))
    result = {}
    for index, ids in enumerate(components):
        group_id = digest(ids)[:20]
        for case_id in ids:
            result[case_id] = ("test" if index < count else "dev", group_id)
    return result


def freeze(
    pool_artifact: dict[str, Any],
    reconciliation_artifact: dict[str, Any],
    adjudication_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pool = unseal(pool_artifact, "pool")
    reconciled = unseal(reconciliation_artifact, "reconciliation")
    if reconciled["pool_sha256"] != pool_artifact["sha256"]:
        raise ValueError("复核结果不属于该候选包")
    labels = [Label.model_validate(label) for label in reconciled["agreed"]]
    adjudicator = None
    conflict_ids = {item["case_id"] for item in reconciled["conflicts"]}
    if conflict_ids:
        if adjudication_data is None:
            raise ValueError("标注分歧尚未裁决，不能冻结")
        adjudicator = validate_review(pool_artifact, adjudication_data, subset=conflict_ids)
        if adjudicator.reviewer_id in reconciled["reviewers"]:
            raise ValueError("分歧裁决需要第三位评审者")
        labels.extend(adjudicator.labels)
    by_id = unique(labels, "case_id")
    if set(by_id) != {row["case"]["case_id"] for row in pool["cases"]}:
        raise ValueError("冻结必须包含全部问题及其分类结果")
    protocol = Protocol.model_validate(pool["prepared"]["protocol"])
    splits = assign_splits(pool["prepared"]["cases"], protocol)
    rows = []
    for entry in pool["cases"]:
        case = entry["case"]
        label = by_id[case["case_id"]]
        if not set(label.relevant_ids) <= {item["id"] for item in entry["catalog"]}:
            raise ValueError("冻结标注含快照外证据")
        split, group = splits[case["case_id"]]
        rows.append(
            {
                "case": case,
                "label": label.model_dump(),
                "split": split,
                "group_id": group,
                "catalog_sha256": entry["catalog_sha256"],
            }
        )
    if not any(row["split"] == "test" and row["label"]["category"] == "answerable" for row in rows):
        raise ValueError("独立测试集没有可回答知识问题，需要继续采集数据")
    return seal(
        "frozen",
        {
            "prepared": pool["prepared"],
            "pool_sha256": pool_artifact["sha256"],
            "reconciliation_sha256": reconciliation_artifact["sha256"],
            "adjudication_sha256": digest(adjudication_data) if adjudication_data else None,
            "reviewers": reconciled["reviewers"],
            "reviewer_kinds": reconciled["reviewer_kinds"],
            "adjudicator": adjudicator.reviewer_id if adjudicator else None,
            "exact_agreement_rate": reconciled["exact_agreement_rate"],
            "cases": rows,
            "category_counts": dict(Counter(row["label"]["category"] for row in rows)),
            "frozen_at": now(),
            "label_coverage": (
                "合成预设标签，未经领域专家验证"
                if "scripted_fixture" in reconciled["reviewer_kinds"]
                else "已授权可检索分块目录上的人工判断；不自动宣称穷尽真实世界全部相关资料"
            ),
        },
    )
