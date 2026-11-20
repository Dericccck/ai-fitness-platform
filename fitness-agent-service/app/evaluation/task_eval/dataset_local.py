"""Exploratory evaluation of dataset-linked fixtures on this machine.

Component results must not be promoted to complete Agent-task results.
All services, identities, corpora and notifications here are local test doubles.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import book_030_local, booking_cases, booking_java_cases, capacity_draft
from .component_customer_service_cases import evaluate_customer_service_case_observations
from .component_notification_worker import evaluate_notification_worker
from .component_operations_cases import evaluate_operations_case_observations
from .component_outreach_cases import evaluate_outreach_case_observations
from .component_routing import evaluate_routing
from .customer_service_boundary_cases import evaluate_customer_service_boundaries
from .dataset import Dataset
from .knowledge_dataset_local import evaluate_knowledge_dataset
from .knowledge_draft_corpus import CORPUS_SHA256, evaluate_draft_corpus
from .knowledge_extended_local import evaluate_knowledge_extended
from .outreach_draft_fixtures import evaluate as evaluate_draft_outreach
from .routing_intent_cases import evaluate_intent_observations
from .routing_remaining_cases import evaluate_remaining_routing_observations
from .routing_role_boundary_cases import evaluate_routing_role_boundaries
from .routing_supervisor_clarification_cases import evaluate_supervisor_clarification_observations
from .safety_chest_cases import evaluate_chest_safety_observations
from .safety_component_cases import evaluate_safety_case_observations
from .safety_injection_cases import evaluate_injection_observations
from .safety_restricted_cases import evaluate_safety_restricted_observations
from .safety_weight_loss_case import evaluate_weight_loss_observation

_NOTIFICATION_REFERENCES = {
    "OPER-009": ["测试投递记录不得出现通知"],
    "OPER-010": ["按策略延后或不投递，不伪造已送达"],
    "OPER-011": ["超过频率限制时不投递"],
    "OPER-012": ["通过测试适配器观察允许投递"],
    "OPER-013": ["不发送过期候选提醒"],
}
_NOTIFICATION_CASE_SHA256 = {
    "OPER-009": "04745bf8b23952eccf329d10d94c9028666d636a092d7788144030da98641f06",
    "OPER-010": "13892c0584490670aa4d48bc12f28931ccb316cb13f65b97bf190046cc482f96",
    "OPER-011": "f82c9088baf4fa40df23fb3813862b6ddff6b8ea46508d3f322cec6a0019d288",
    "OPER-012": "3736db5281aa9f55cd300390f2aac74a33c8e6325d6ee954c882d8b47ed114b0",
    "OPER-013": "47264063f8838bca83592bd869fb8ea49efb5eca24c1107ef601f46efdf76e07",
}


def _notification_case_matches(case: Any) -> bool:
    raw = json.dumps(
        case.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest() == _NOTIFICATION_CASE_SHA256.get(case.case_id)


def _mark(row: dict[str, Any], status: str, reason: str) -> None:
    """Do not let a later observation hide an earlier error or failure."""
    order = {"SKIPPED": 0, "INCONCLUSIVE": 1, "ERROR": 2, "FAIL": 3}
    if status not in order:
        status, reason = "ERROR", "INVALID_ADAPTER_STATUS"
    row.setdefault("reasons", []).append(reason)
    if order[status] > order[row["task_status"]]:
        row.update(task_status=status, reason=reason)


async def evaluate_dataset(dataset: Dataset, *, split: str = "all") -> dict[str, Any]:
    # A fresh scope prevents Java test observations from a previous report from
    # being reused, while sharing one suite invocation within this report.
    with booking_java_cases.java_suite_scope():
        return await _evaluate_dataset(dataset, split=split)


async def _evaluate_dataset(dataset: Dataset, *, split: str = "all") -> dict[str, Any]:
    if split not in {"dev", "test", "all"}:
        raise ValueError("invalid split")
    selected = [case for case in dataset.cases if split == "all" or case.split == split]
    if not selected:
        raise ValueError("no cases selected")

    scoped = [case for case in selected if case.delivery_scope == "in_scope"]
    scoped_dataset = replace(dataset, cases=scoped)

    async def operations_cases() -> dict[str, Any]:
        splits = ("dev", "test") if split == "all" else (split,)
        groups = await asyncio.gather(
            *(
                evaluate_operations_case_observations(scoped_dataset, split=value)
                for value in splits
            )
        )
        return {"cases": [row for group in groups for row in group["cases"]]}

    async def safety_cases() -> dict[str, Any]:
        splits = ("dev", "test") if split == "all" else (split,)
        groups = await asyncio.gather(
            *(evaluate_safety_case_observations(scoped_dataset, split=value) for value in splits)
        )
        return {"cases": [row for group in groups for row in group["cases"]]}

    async def outreach_cases() -> dict[str, Any]:
        splits = ("dev", "test") if split == "all" else (split,)
        groups = await asyncio.gather(
            *(evaluate_outreach_case_observations(scoped_dataset, split=value) for value in splits)
        )
        return {"cases": [row for group in groups for row in group["cases"]]}

    async def chest_cases() -> dict[str, Any]:
        splits = ("dev", "test") if split == "all" else (split,)
        groups = await asyncio.gather(
            *(evaluate_chest_safety_observations(scoped_dataset, split=value) for value in splits)
        )
        return {"cases": [row for group in groups for row in group["cases"]]}

    async def intent_cases() -> dict[str, Any]:
        splits = ("dev", "test") if split == "all" else (split,)
        groups = await asyncio.gather(
            *(evaluate_intent_observations(scoped_dataset, split=value) for value in splits)
        )
        return {"cases": [row for group in groups for row in group["cases"]]}

    async def injection_cases() -> dict[str, Any]:
        splits = ("dev", "test") if split == "all" else (split,)
        groups = await asyncio.gather(
            *(evaluate_injection_observations(scoped_dataset, split=value) for value in splits)
        )
        return {"cases": [row for group in groups for row in group["cases"]]}

    async def weight_loss_cases() -> dict[str, Any]:
        splits = ("dev", "test") if split == "all" else (split,)
        groups = await asyncio.gather(
            *(evaluate_weight_loss_observation(scoped_dataset, split=value) for value in splits)
        )
        return {"cases": [row for group in groups for row in group["cases"]]}

    async def service_boundary_cases() -> dict[str, Any]:
        splits = ("dev", "test") if split == "all" else (split,)
        groups = await asyncio.gather(
            *(evaluate_customer_service_boundaries(scoped_dataset, split=value) for value in splits)
        )
        return {"cases": [row for group in groups for row in group["cases"]]}

    async def role_boundary_cases() -> dict[str, Any]:
        splits = ("dev", "test") if split == "all" else (split,)
        groups = await asyncio.gather(
            *(evaluate_routing_role_boundaries(scoped_dataset, split=value) for value in splits)
        )
        return {"cases": [row for group in groups for row in group["cases"]]}

    async def clarification_cases() -> dict[str, Any]:
        splits = ("dev", "test") if split == "all" else (split,)
        groups = await asyncio.gather(
            *(
                evaluate_supervisor_clarification_observations(scoped_dataset, split=value)
                for value in splits
            )
        )
        return {"cases": [row for group in groups for row in group["cases"]]}

    async def remaining_routing_cases() -> dict[str, Any]:
        splits = ("dev", "test") if split == "all" else (split,)
        groups = await asyncio.gather(
            *(
                evaluate_remaining_routing_observations(scoped_dataset, split=value)
                for value in splits
            )
        )
        return {"cases": [row for group in groups for row in group["cases"]]}

    draft_knowledge_ids = frozenset(
        c.case_id for c in scoped if c.case_id in {f"KNOW-{i:03d}" for i in range(1, 11)}
    )

    draft_outreach_ids = frozenset(
        c.case_id for c in scoped if c.case_id in {f"OPER-{i:03d}" for i in range(9, 17)}
    )

    async def draft_outreach_cases() -> dict[str, Any]:
        if not draft_outreach_ids:
            return {"cases": []}
        return await evaluate_draft_outreach(dataset, case_ids=draft_outreach_ids)

    async def draft_knowledge_cases() -> dict[str, Any]:
        if not draft_knowledge_ids:
            return {"cases": []}
        return await evaluate_draft_corpus(dataset, case_ids=draft_knowledge_ids)

    results = await asyncio.gather(
        evaluate_knowledge_dataset(scoped_dataset),
        evaluate_notification_worker(),
        evaluate_knowledge_extended(scoped_dataset),
        operations_cases(),
        safety_cases(),
        outreach_cases(),
        chest_cases(),
        intent_cases(),
        injection_cases(),
        weight_loss_cases(),
        service_boundary_cases(),
        role_boundary_cases(),
        clarification_cases(),
        remaining_routing_cases(),
        draft_knowledge_cases(),
        draft_outreach_cases(),
        return_exceptions=True,
    )
    draft_outreach_error = not isinstance(results[15], dict)
    draft_outreach = results[15] if isinstance(results[15], dict) else {"cases": []}
    draft_outreach_by_id = {item["case_id"]: item for item in draft_outreach["cases"]}
    draft_knowledge_error = not isinstance(results[14], dict)
    draft_knowledge = results[14] if isinstance(results[14], dict) else {"cases": []}
    draft_knowledge_by_id = {item["case_id"]: item for item in draft_knowledge["cases"]}
    knowledge_error = not isinstance(results[0], dict)
    notification_error = not isinstance(results[1], dict)
    extended_error = not isinstance(results[2], dict)
    operations_error = not isinstance(results[3], dict)
    safety_error = not isinstance(results[4], dict)
    outreach_error = not isinstance(results[5], dict)
    chest_error = not isinstance(results[6], dict)
    intent_error = not isinstance(results[7], dict)
    injection_error = not isinstance(results[8], dict)
    weight_loss_error = not isinstance(results[9], dict)
    service_boundary_error = not isinstance(results[10], dict)
    role_boundary_error = not isinstance(results[11], dict)
    clarification_error = not isinstance(results[12], dict)
    remaining_routing_error = not isinstance(results[13], dict)
    knowledge: dict[str, Any] = results[0] if isinstance(results[0], dict) else {"cases": []}
    notifications: dict[str, Any] = results[1] if isinstance(results[1], dict) else {"cases": []}
    extended: dict[str, Any] = results[2] if isinstance(results[2], dict) else {"cases": []}
    operations: dict[str, Any] = results[3] if isinstance(results[3], dict) else {"cases": []}
    safety: dict[str, Any] = results[4] if isinstance(results[4], dict) else {"cases": []}
    outreach: dict[str, Any] = results[5] if isinstance(results[5], dict) else {"cases": []}
    chest: dict[str, Any] = results[6] if isinstance(results[6], dict) else {"cases": []}
    intents: dict[str, Any] = results[7] if isinstance(results[7], dict) else {"cases": []}
    injection: dict[str, Any] = results[8] if isinstance(results[8], dict) else {"cases": []}
    weight_loss: dict[str, Any] = results[9] if isinstance(results[9], dict) else {"cases": []}
    service_boundary: dict[str, Any] = (
        results[10] if isinstance(results[10], dict) else {"cases": []}
    )
    role_boundary: dict[str, Any] = results[11] if isinstance(results[11], dict) else {"cases": []}
    clarification: dict[str, Any] = results[12] if isinstance(results[12], dict) else {"cases": []}
    remaining_routing: dict[str, Any] = (
        results[13] if isinstance(results[13], dict) else {"cases": []}
    )
    knowledge_by_id = {item["case_id"]: item for item in knowledge["cases"]}
    notification_by_id = {item["case_id"]: item for item in notifications["cases"]}
    extended_by_id = {item["case_id"]: item for item in extended["cases"]}
    operations_by_id = {item["case_id"]: item for item in operations["cases"]}
    safety_by_id = {item["case_id"]: item for item in safety["cases"]}
    outreach_by_id = {item["case_id"]: item for item in outreach["cases"]}
    chest_by_id = {item["case_id"]: item for item in chest["cases"]}
    intent_by_id = {item["case_id"]: item for item in intents["cases"]}
    injection_by_id = {item["case_id"]: item for item in injection["cases"]}
    weight_loss_by_id = {item["case_id"]: item for item in weight_loss["cases"]}
    service_boundary_by_id = {item["case_id"]: item for item in service_boundary["cases"]}
    role_boundary_by_id = {item["case_id"]: item for item in role_boundary["cases"]}
    clarification_by_id = {item["case_id"]: item for item in clarification["cases"]}
    remaining_routing_by_id = {item["case_id"]: item for item in remaining_routing["cases"]}
    customer_service_by_id: dict[str, Any] = {}
    customer_service_error = False
    try:
        for current_split in ("dev", "test") if split == "all" else (split,):
            for item in evaluate_customer_service_case_observations(
                scoped_dataset, split=current_split
            )["cases"]:
                customer_service_by_id[item["case_id"]] = item
    except Exception:  # noqa: BLE001 - preserve unrelated cases and do not disclose inputs
        customer_service_error = True
    restricted_by_id: dict[str, Any] = {}
    restricted_error = False
    try:
        for current_split in ("dev", "test") if split == "all" else (split,):
            for item in evaluate_safety_restricted_observations(
                scoped_dataset, split=current_split
            )["cases"]:
                restricted_by_id[item["case_id"]] = item
    except Exception:  # noqa: BLE001 - preserve unrelated cases and do not disclose inputs
        restricted_error = True
    route_by_id: dict[str, Any] = {}
    for current_split in ("dev", "test") if split == "all" else (split,):
        route_by_id.update(
            {
                item["case_id"]: item
                for item in evaluate_routing(dataset, split=current_split)["cases"]
            }
        )

    rows = []
    for case in selected:
        row: dict[str, Any] = {
            "case_id": case.case_id,
            "family_id": case.family_id,
            "split": case.split,
            "primary_scenario": case.primary_scenario,
            "review_status": case.review.status,
            "delivery_scope": case.delivery_scope,
            "task_status": "SKIPPED",
            "reason": "NO_LOCAL_CASE_ADAPTER",
            "observations": {},
        }
        if case.delivery_scope != "in_scope":
            row["reason"] = "OUT_OF_SCOPE"
            rows.append(row)
            continue
        if case.case_id in draft_outreach_ids and (
            draft_outreach_error or case.case_id not in draft_outreach_by_id
        ):
            _mark(row, "ERROR", "DRAFT_OUTREACH_COMPONENT_ERROR")
        if case.case_id in draft_knowledge_ids and (
            draft_knowledge_error or case.case_id not in draft_knowledge_by_id
        ):
            _mark(row, "ERROR", "DRAFT_KNOWLEDGE_COMPONENT_ERROR")
        if knowledge_error and case.primary_scenario == "knowledge":
            _mark(row, "ERROR", "RETRIEVAL_ADAPTER_ERROR")
        if notification_error and case.case_id in {f"OPER-{i:03d}" for i in range(9, 14)}:
            _mark(row, "ERROR", "NOTIFICATION_ADAPTER_ERROR")
        if extended_error and case.case_id in {f"KNOW-{i:03d}" for i in range(19, 31)}:
            _mark(row, "ERROR", "KNOWLEDGE_COMPONENT_ERROR")
        if operations_error and case.case_id in {
            *(f"OPER-{i:03d}" for i in range(1, 9)),
            "SAFE-003",
        }:
            _mark(row, "ERROR", "OPERATIONS_COMPONENT_ERROR")
        if safety_error and case.case_id in {
            "SAFE-001",
            "SAFE-002",
            "SAFE-004",
            "SAFE-006",
            "SAFE-007",
            "SAFE-008",
            "SAFE-009",
            "SAFE-012",
        }:
            _mark(row, "ERROR", "SAFETY_COMPONENT_ERROR")
        if outreach_error and case.case_id in {"OPER-014", "OPER-015", "OPER-016"}:
            _mark(row, "ERROR", "OUTREACH_COMPONENT_ERROR")
        if customer_service_error and case.case_id == "OPER-019":
            _mark(row, "ERROR", "CUSTOMER_SERVICE_COMPONENT_ERROR")
        if restricted_error and case.case_id == "SAFE-015":
            _mark(row, "ERROR", "RESTRICTED_SAFETY_COMPONENT_ERROR")
        if chest_error and case.case_id == "SAFE-013":
            _mark(row, "ERROR", "CHEST_SAFETY_COMPONENT_ERROR")
        if intent_error and case.case_id in {f"ROUT-{i:03d}" for i in range(9, 21)}:
            _mark(row, "ERROR", "INTENT_ROUTING_COMPONENT_ERROR")
        if injection_error and case.case_id in {"SAFE-010", "SAFE-011"}:
            _mark(row, "ERROR", "INJECTION_COMPONENT_ERROR")
        if weight_loss_error and case.case_id == "SAFE-014":
            _mark(row, "ERROR", "WEIGHT_LOSS_COMPONENT_ERROR")
        if service_boundary_error and case.case_id in {"OPER-017", "OPER-018", "OPER-020"}:
            _mark(row, "ERROR", "CUSTOMER_SERVICE_BOUNDARY_ERROR")
        if role_boundary_error and case.case_id in {"ROUT-019", "ROUT-020"}:
            _mark(row, "ERROR", "ROUTING_ROLE_BOUNDARY_ERROR")
        if clarification_error and case.case_id in {
            "ROUT-009",
            "ROUT-010",
            "ROUT-011",
            "ROUT-012",
            "ROUT-013",
            "ROUT-016",
            "ROUT-017",
            "ROUT-018",
        }:
            _mark(row, "ERROR", "CLARIFICATION_COMPONENT_ERROR")
        if remaining_routing_error and case.case_id in {"ROUT-005", "ROUT-007", "ROUT-014"}:
            _mark(row, "ERROR", "REMAINING_ROUTING_COMPONENT_ERROR")
        if case.case_id == "BOOK-009":
            # The old group-capacity double was superseded by the user's
            # clarification: this case concerns one private-coach time slot.
            # A separately invoked real diagnostic is never silently reused here.
            row["reason"] = "PRIVATE_COACH_SLOT_ADAPTER_NOT_EXECUTED"
        if case.case_id == "BOOK-022":
            try:
                observation = capacity_draft.observe(case)
                if (
                    not isinstance(observation, dict)
                    or observation.get("case_id") != case.case_id
                    or observation.get("status") != "INCONCLUSIVE"
                    or observation.get("observation_scope") != "synthetic_sqlite_double_only"
                    or observation.get("fixture_sha256") != capacity_draft._SHA256
                    or not isinstance(observation.get("violations"), list)
                    or any(
                        not isinstance(value, str) or not value
                        for value in observation["violations"]
                    )
                    or not isinstance(observation.get("response"), str)
                ):
                    raise ValueError("INVALID_CAPACITY_OBSERVATION")
                snapshots = []
                for name in ("before", "before_decision", "after"):
                    value = observation.get(name)
                    if (
                        not isinstance(value, dict)
                        or set(value) != {"occupied", "credits", "bookings"}
                        or any(type(number) is not int or number < 0 for number in value.values())
                    ):
                        raise ValueError("INVALID_CAPACITY_OBSERVATION")
                    snapshots.append(capacity_draft.Snapshot(**value))
                violations = list(observation["violations"])
                violations.extend(
                    value
                    for value in capacity_draft.validate(
                        case.case_id,
                        snapshots[0],
                        snapshots[1],
                        snapshots[2],
                        observation["response"],
                    )
                    if value not in violations
                )
                observation = {
                    **observation,
                    "violations": violations,
                    "component_status": "FAIL" if violations else "PASS",
                    "product_status": "UNKNOWN",
                    "model_status": "UNKNOWN",
                }
            except Exception:  # noqa: BLE001 - retain other cases without exposing input
                _mark(row, "ERROR", "CAPACITY_DRAFT_COMPONENT_ERROR")
            else:
                row["observations"]["draft_capacity"] = observation
                if violations:
                    _mark(row, "FAIL", "CAPACITY_DRAFT_COMPONENT_FAIL")
                else:
                    _mark(row, "INCONCLUSIVE", "CAPACITY_DRAFT_DOUBLE_ONLY_NO_PRODUCT_CONTRACT")
        if case.case_id == "BOOK-030" and os.environ.get("TASK_EVAL_BOOK030_BROKER") == "1":
            try:
                broker_observation = await book_030_local.observe(case)
                if (
                    not isinstance(broker_observation, dict)
                    or broker_observation.get("case_id") != case.case_id
                    or broker_observation.get("component_status") != "OBSERVED"
                    or broker_observation.get("task_status") != "UNKNOWN"
                    or broker_observation.get("model_status") != "UNKNOWN"
                    or broker_observation.get("event_source")
                    != "java_booking_outbox_publisher_via_disposable_rabbitmq"
                    or broker_observation.get("notification_scope")
                    != "disposable_postgres_only_no_external_notification_adapter"
                    or broker_observation.get("audit_complete") is not False
                    or not isinstance(broker_observation.get("report_content"), str)
                    or hashlib.sha256(
                        broker_observation["report_content"].encode("utf-8")
                    ).hexdigest()
                    != broker_observation.get("report_sha256")
                    or not all(
                        isinstance(broker_observation.get(key), str)
                        and len(broker_observation[key]) == 64
                        and all(c in "0123456789abcdef" for c in broker_observation[key])
                        for key in ("report_sha256", "event_body_sha256")
                    )
                ):
                    raise ValueError("INVALID_BROKER_OBSERVATION")
                checked_event = book_030_local._check_report(
                    json.loads(broker_observation["report_content"])
                )
                if checked_event["event_body_sha256"] != broker_observation["event_body_sha256"]:
                    raise ValueError("INVALID_BROKER_OBSERVATION")
            except Exception:  # noqa: BLE001 - preserve unrelated cases without leaking logs
                _mark(row, "ERROR", "BOOK030_BROKER_COMPONENT_ERROR")
            else:
                row["observations"]["book030_broker"] = broker_observation
                _mark(row, "INCONCLUSIVE", "BOOK030_SCRIPTED_AGENT_COMPONENT_ONLY")
        if booking_cases.supports(case):
            try:
                trial = await asyncio.wait_for(
                    booking_cases.run(case), timeout=case.timeouts.trial_seconds
                )
            except Exception:  # noqa: BLE001 - retain other cases without leaking inputs
                _mark(row, "ERROR", "BOOKING_ADAPTER_ERROR")
            else:
                # A draft or incomplete case never becomes release-ready through local execution.
                status = trial["status"]
                _mark(
                    row,
                    "INCONCLUSIVE" if status == "PASS" else status,
                    trial.get("reason") or "SCRIPTED_MODEL_AND_DRAFT_CASE",
                )
                row["observations"]["booking_protocol"] = trial
        if case.case_id in knowledge_by_id:
            observation = knowledge_by_id[case.case_id]
            row["observations"]["retrieval"] = observation
            if observation["status"] in {"OBSERVED", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "RETRIEVAL_ONLY_NO_AGENT_ANSWER")
            elif observation["status"] == "ERROR":
                _mark(row, "ERROR", "RETRIEVAL_ADAPTER_ERROR")
        if case.case_id in draft_knowledge_by_id:
            observation = draft_knowledge_by_id[case.case_id]
            row["observations"]["draft_knowledge_retrieval"] = {
                **observation,
                "origin": "synthetic_rag_component_with_local_substitutes",
                "corpus_sha256": CORPUS_SHA256,
                "status": "draft_unreviewed_not_release_evidence",
            }
            if observation["retrieval"]["status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "DRAFT_KNOWLEDGE_RETRIEVAL_ONLY")
            else:
                _mark(row, "ERROR", "DRAFT_KNOWLEDGE_COMPONENT_ERROR")
        if case.case_id in extended_by_id:
            observation = extended_by_id[case.case_id]
            row["observations"]["training_or_movement"] = observation
            if observation["status"] in {"OBSERVED", "FAIL", "REJECTED"}:
                _mark(row, "INCONCLUSIVE", "TRAINING_OR_MOVEMENT_COMPONENT_ONLY")
            elif observation["status"] == "ERROR":
                _mark(row, "ERROR", "KNOWLEDGE_COMPONENT_ERROR")
        if case.case_id in route_by_id:
            observation = route_by_id[case.case_id]
            row["observations"]["routing"] = observation
            if observation["status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "FIRST_STAGE_ROUTING_ONLY")
            elif observation["status"] == "ERROR":
                _mark(row, "ERROR", "ROUTING_ADAPTER_ERROR")
        if case.case_id in intent_by_id:
            observation = intent_by_id[case.case_id]
            row["observations"]["intent_routing"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "INTENT_ROUTING_COMPONENT_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "INTENT_ROUTING_COMPONENT_ERROR")
        if case.case_id in role_boundary_by_id:
            observation = role_boundary_by_id[case.case_id]
            row["observations"]["routing_role_boundary"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "ROUTING_ROLE_BOUNDARY_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "ROUTING_ROLE_BOUNDARY_ERROR")
        if case.case_id in clarification_by_id:
            observation = clarification_by_id[case.case_id]
            row["observations"]["supervisor_clarification"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "SUPERVISOR_CLARIFICATION_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "CLARIFICATION_COMPONENT_ERROR")
        if case.case_id in remaining_routing_by_id:
            observation = remaining_routing_by_id[case.case_id]
            row["observations"]["remaining_routing"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "REMAINING_ROUTING_COMPONENT_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "REMAINING_ROUTING_COMPONENT_ERROR")
        if case.case_id in operations_by_id:
            observation = operations_by_id[case.case_id]
            row["observations"]["operations_guard"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "OPERATIONS_GUARD_PROBE_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "OPERATIONS_COMPONENT_ERROR")
        if case.case_id in safety_by_id:
            observation = safety_by_id[case.case_id]
            row["observations"]["safety_guard"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "SAFETY_GUARD_PROBE_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "SAFETY_COMPONENT_ERROR")
        if case.case_id in injection_by_id:
            observation = injection_by_id[case.case_id]
            row["observations"]["injection_guard"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "INJECTION_GUARD_COMPONENT_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "INJECTION_COMPONENT_ERROR")
        if case.case_id in weight_loss_by_id:
            observation = weight_loss_by_id[case.case_id]
            row["observations"]["weight_loss_routing"] = observation
            if observation["component_status"] == "OBSERVED":
                _mark(row, "INCONCLUSIVE", "WEIGHT_LOSS_ROUTING_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "WEIGHT_LOSS_COMPONENT_ERROR")
        if case.case_id in outreach_by_id:
            observation = outreach_by_id[case.case_id]
            row["observations"]["outreach_guard"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "OUTREACH_COMPONENT_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "OUTREACH_COMPONENT_ERROR")
        if case.case_id in draft_outreach_by_id:
            observation = draft_outreach_by_id[case.case_id]
            row["observations"]["draft_outreach"] = {
                **observation,
                "origin": "synthetic_draft_in_memory_outreach_component",
                "fixture_sha256": draft_outreach["fixture_sha256"],
                "status": "draft_unreviewed_not_release_evidence",
            }
            if observation["component_grade"]["status"] in {"PASS", "FAIL", "UNKNOWN"}:
                _mark(row, "INCONCLUSIVE", "DRAFT_OUTREACH_COMPONENT_ONLY")
            else:
                _mark(row, "ERROR", "DRAFT_OUTREACH_COMPONENT_ERROR")
        if case.case_id in customer_service_by_id:
            observation = customer_service_by_id[case.case_id]
            row["observations"]["customer_service_route"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "CUSTOMER_SERVICE_ROUTING_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "CUSTOMER_SERVICE_COMPONENT_ERROR")
        if case.case_id in service_boundary_by_id:
            observation = service_boundary_by_id[case.case_id]
            row["observations"]["customer_service_boundary"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "CUSTOMER_SERVICE_BOUNDARY_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "CUSTOMER_SERVICE_BOUNDARY_ERROR")
        if case.case_id in restricted_by_id:
            observation = restricted_by_id[case.case_id]
            row["observations"]["restricted_safety"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "RESTRICTED_SAFETY_COMPONENT_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "RESTRICTED_SAFETY_COMPONENT_ERROR")
        if case.case_id in chest_by_id:
            observation = chest_by_id[case.case_id]
            row["observations"]["chest_safety"] = observation
            if observation["component_status"] in {"PASS", "FAIL"}:
                _mark(row, "INCONCLUSIVE", "CHEST_SAFETY_COMPONENT_ONLY")
            elif observation["component_status"] == "ERROR":
                _mark(row, "ERROR", "CHEST_SAFETY_COMPONENT_ERROR")
        if case.case_id in notification_by_id:
            # Bind this controlled component fixture to its declared event reference.
            event_refs = [step.event_ref for step in case.steps if step.type == "trigger_event"]
            if (
                case.primary_scenario == "operations"
                and case.subcategory == "outreach"
                and case.trigger.type == "event"
                and case.expected.reference_points == _NOTIFICATION_REFERENCES.get(case.case_id)
                and event_refs == [f"planned-{case.case_id.lower()}.event"]
                and _notification_case_matches(case)
            ):
                observation = notification_by_id[case.case_id]
                row["observations"]["notification_worker"] = observation
                if observation["component_status"] in {"PASS", "FAIL"}:
                    _mark(row, "INCONCLUSIVE", "NOTIFICATION_COMPONENT_ONLY")
                else:
                    _mark(row, "ERROR", "NOTIFICATION_ADAPTER_ERROR")
        rows.append(row)

    in_scope = [row for row in rows if row["delivery_scope"] == "in_scope"]
    status_counts = dict(Counter(row["task_status"] for row in in_scope))
    scenario_counts = {
        scenario: dict(
            Counter(row["task_status"] for row in in_scope if row["primary_scenario"] == scenario)
        )
        for scenario in sorted({row["primary_scenario"] for row in in_scope})
    }
    component_failures = sum(
        1
        for row in in_scope
        for name, observation in row["observations"].items()
        if (name == "routing" and observation.get("status") == "FAIL")
        or (name == "intent_routing" and observation.get("component_status") == "FAIL")
        or (name == "routing_role_boundary" and observation.get("component_status") == "FAIL")
        or (name == "supervisor_clarification" and observation.get("component_status") == "FAIL")
        or (name == "remaining_routing" and observation.get("component_status") == "FAIL")
        or (name == "retrieval" and observation.get("status") == "FAIL")
        or (
            name == "draft_knowledge_retrieval"
            and observation.get("retrieval", {}).get("status") == "FAIL"
        )
        or (name == "training_or_movement" and observation.get("status") in {"FAIL", "REJECTED"})
        or (name == "operations_guard" and observation.get("component_status") == "FAIL")
        or (name == "safety_guard" and observation.get("component_status") == "FAIL")
        or (name == "injection_guard" and observation.get("component_status") == "FAIL")
        or (name == "outreach_guard" and observation.get("component_status") == "FAIL")
        or (
            name == "draft_outreach"
            and observation.get("component_grade", {}).get("status") == "FAIL"
        )
        or (name == "customer_service_route" and observation.get("component_status") == "FAIL")
        or (name == "customer_service_boundary" and observation.get("component_status") == "FAIL")
        or (name == "restricted_safety" and observation.get("component_status") == "FAIL")
        or (name == "chest_safety" and observation.get("component_status") == "FAIL")
        or (name == "notification_worker" and observation.get("component_status") == "FAIL")
        or (name == "draft_capacity" and observation.get("component_status") == "FAIL")
    )
    app_root = Path(__file__).resolve().parents[2]
    source_paths = (
        "agent/supervisor.py",
        "agent/routing.py",
        "agent/operations_tools.py",
        "rag/service.py",
        "evaluation/task_eval/booking_cases.py",
        "evaluation/task_eval/booking_java_cases.py",
        "evaluation/task_eval/booking_boundary_cases.py",
        "evaluation/task_eval/booking_mutation_cases.py",
        "evaluation/task_eval/booking_positive_cases.py",
        "evaluation/task_eval/booking_reconciliation_cases.py",
        "evaluation/task_eval/booking_contracts.py",
        "evaluation/task_eval/booking_dev_fixtures.py",
        "evaluation/task_eval/booking_dev_011.py",
        "evaluation/task_eval/booking_test_008.py",
        "evaluation/task_eval/booking_dev_026.py",
        "evaluation/task_eval/capacity_draft.py",
        "evaluation/task_eval/book_030_local.py",
        "evaluation/task_eval/booking_duplicate_event.py",
        "evaluation/task_eval/booking_rabbit_delivery.py",
        "evaluation/task_eval/cross_service_booking.py",
        "evaluation/task_eval/booking_local.py",
        "evaluation/task_eval/knowledge_dataset_local.py",
        "evaluation/task_eval/knowledge_draft_corpus.py",
        "evaluation/task_eval/component_retrieval.py",
        "evaluation/task_eval/retrieval_lab.py",
        "evaluation/task_eval/graders/retrieval.py",
        "evaluation/task_eval/knowledge_extended_local.py",
        "evaluation/task_eval/routing_intent_cases.py",
        "evaluation/task_eval/routing_role_boundary_cases.py",
        "evaluation/task_eval/routing_supervisor_clarification_cases.py",
        "evaluation/task_eval/routing_remaining_cases.py",
        "evaluation/task_eval/component_notification_worker.py",
        "evaluation/task_eval/component_operations_cases.py",
        "evaluation/task_eval/component_outreach_cases.py",
        "evaluation/task_eval/outreach_draft_fixtures.py",
        "evaluation/task_eval/component_customer_service_cases.py",
        "evaluation/task_eval/customer_service_boundary_cases.py",
        "evaluation/task_eval/safety_component_cases.py",
        "evaluation/task_eval/safety_injection_cases.py",
        "evaluation/task_eval/safety_weight_loss_case.py",
        "evaluation/task_eval/safety_chest_cases.py",
        "evaluation/task_eval/safety_restricted_cases.py",
        "evaluation/task_eval/dataset_local.py",
    )
    source_sha256 = {
        path: hashlib.sha256((app_root / path).read_bytes()).hexdigest() for path in source_paths
    }
    service_root = app_root.parent
    known_fixture_paths = (
        "evals/task_eval/dev_fixtures/book-018-v1.json",
        "evals/task_eval/dev_fixtures/book-026-v1.json",
        "evals/task_eval/dev_fixtures/book-008-v1.json",
        "evals/task_eval/dev_fixtures/book-capacity-draft-v1.json",
        "evals/task_eval/draft_knowledge_fixture_v1/corpus.json",
        "evals/task_eval/dev_fixtures/outreach-009-016-draft-v1.json",
    )
    known_fixture_sha256 = {
        path: hashlib.sha256((service_root / path).read_bytes()).hexdigest()
        for path in known_fixture_paths
    }
    return {
        "manifest": {
            "schema_version": "1.0",
            "run_id": uuid4().hex,
            "created_at": datetime.now(UTC).isoformat(),
            "mode": "dataset_local_exploratory",
            "dataset_version": dataset.dataset_version,
            "dataset_sha256": dataset.sha256,
            "source_sha256": source_sha256,
            "launcher_sha256": hashlib.sha256(
                (service_root / "scripts/task_eval_cross_service_booking.sh").read_bytes()
            ).hexdigest(),
            "known_fixture_sha256": known_fixture_sha256,
            "fixture_sha256": "unknown",
            "fixture_note": "source hashes are not a resolved-fixture snapshot and do not make runs comparable",
            "split": split,
            "limitations": [
                "All cases are exploratory drafts; no release gate or real-model quality score.",
                "Synthetic models, business state, knowledge corpus, notification storage and delivery are test doubles.",
                "Component observations do not establish end-to-end task success or production authorization.",
            ],
        },
        "cases": rows,
        "summary": {
            "planned_cases": len(in_scope),
            "out_of_scope_cases": len(rows) - len(in_scope),
            "task_status_counts": status_counts,
            "by_scenario": scenario_counts,
            "component_failures": component_failures,
            "observed_component_cases": sum(
                bool(row["observations"]) and row["task_status"] != "SKIPPED" for row in in_scope
            ),
        },
    }


def write_dataset_report(directory: str | Path, report: dict[str, Any]) -> None:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    files = {
        "manifest.json": json.dumps(report["manifest"], ensure_ascii=False, indent=2) + "\n",
        "cases.jsonl": "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in report["cases"]
        ),
        "summary.json": json.dumps(report["summary"], ensure_ascii=False, indent=2) + "\n",
        "report.md": "# 本地案例探索报告\n\n"
        + "合成夹具和测试替身；组件观察不代表完整任务或真实模型通过。\n\n"
        + "```json\n"
        + json.dumps(report["summary"], ensure_ascii=False, indent=2)
        + "\n```\n",
    }
    if any((path / name).exists() for name in files):
        raise ValueError("output directory already contains a report")
    for name, content in files.items():
        with (path / name).open("x", encoding="utf-8") as stream:
            stream.write(content)
