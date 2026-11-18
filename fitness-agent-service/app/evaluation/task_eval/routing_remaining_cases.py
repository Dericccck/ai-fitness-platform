"""Focused local observations for three draft routing cases.

Case-bound component checks use synthetic identities and gateways. None of the
results establish the full task, real authorization, or business facts.
"""

from __future__ import annotations

from typing import Any, cast

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.operations_tools import build_operations_tool_definitions
from app.agent.routing import (
    deterministic_route_decision,
    forced_tool_for_intent,
    intent_allowed_tool_ids,
)
from app.agent.tool_registry import (
    ToolContext,
    ToolRegistry,
    ToolRegistryError,
    ToolRoleForbiddenError,
)
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import GatewayClient, GatewayRequestContext

from .dataset import Dataset
from .routing_supervisor_clarification_cases import _run as _clarification_run
from .safety_injection_cases import _case_hash

_CASES = {
    "ROUT-005": (
        "dev",
        "f5e88826ceb3154ac22f4d1f388e404df778a5b5e105d504b863b6ffe30e3961",
        "查看我今天的授课安排",
    ),
    "ROUT-007": (
        "test",
        "1a5fc577bda449477b0191ad2475fff1650541ea9c643c08dd8781b18e1c9b0d",
        "列出本周合同到期的会员",
    ),
    "ROUT-014": (
        "test",
        "394e993b2c83e81a81deb072cfd6e8f01562f6eeb5580454520bf8858ed288d1",
        "我的合同有问题",
    ),
}
_IDENTITY = AgentIdentity("actor_a", frozenset({"org_a"}), frozenset({"STUDENT"}), 1, 9999999999)
_FOREIGN = "synthetic-foreign-contract-marker"


class _Gateway:
    def __init__(self) -> None:
        self.reads: list[dict[str, Any]] = []
        self.operations_calls = 0

    async def list_contracts(
        self, context: Any, organization_id: str, *, user_id: str, limit: int
    ) -> list[dict[str, str]]:
        self.reads.append({"organization_id": organization_id, "user_id": user_id, "limit": limit})
        return [
            {
                "id": _FOREIGN
                if organization_id != "org_a" or user_id != "actor_a"
                else "synthetic-own"
            }
        ]

    async def query_operations_metric(self, *args: Any, **kwargs: Any) -> Any:
        self.operations_calls += 1
        raise RuntimeError("synthetic gateway must not be called")


async def _role_probe(message: str) -> dict[str, Any]:
    gateway = _Gateway()
    registry = build_fitness_tool_registry(cast(GatewayClient, gateway))
    metric_registry = ToolRegistry()
    for definition in build_operations_tool_definitions(cast(GatewayClient, gateway)):
        metric_registry.register(definition)
    context = ToolContext(
        gateway_context=GatewayRequestContext(
            "synthetic-context", "synthetic-request", "synthetic-trace"
        ),
        identity=_IDENTITY,
        user_message=message,
    )
    metric_outcome = "unexpected_success"
    try:
        await metric_registry.invoke(
            "fitness.operations.metric.query.v1",
            {
                "organization_id": "org_b",
                "metric": "REVENUE_AMOUNT",
                "from": "2026-08-01",
                "to": "2026-08-31",
            },
            context,
        )
    except ToolRoleForbiddenError:
        metric_outcome = "ROLE_FORBIDDEN"
    except Exception:  # noqa: BLE001 - unrelated errors do not establish role denial
        metric_outcome = "OTHER_ERROR"
    try:
        result = await registry.invoke(
            "fitness.contract.list.v1",
            {"organization_id": "org_b", "user_id": "other", "limit": 20},
            context,
        )
    except Exception:  # noqa: BLE001 - do not expose exception details
        return {"component_status": "ERROR", "reason": "CONTRACT_PROBE_ERROR"}
    foreign_seen = _FOREIGN in str(result)
    passed = (
        metric_outcome == "ROLE_FORBIDDEN"
        and gateway.operations_calls == 0
        and gateway.reads == [{"organization_id": "org_a", "user_id": "actor_a", "limit": 20}]
        and not foreign_seen
    )
    return {
        "component_status": "PASS" if passed else "FAIL",
        "reason": "STUDENT_METRIC_ROLE_AND_CONTRACT_BINDING_ONLY",
        "observed": {
            "metric_outcome": metric_outcome,
            "synthetic_metric_gateway_calls": gateway.operations_calls,
            "synthetic_contract_reads": gateway.reads,
            "synthetic_foreign_marker_returned": foreign_seen,
        },
    }


def _neutral_route(message: str) -> dict[str, Any]:
    decision = deterministic_route_decision(message)
    if decision is None or len(decision.intents) != 1:
        return {"component_status": "UNKNOWN", "reason": "DETERMINISTIC_SINGLE_INTENT_NOT_OBSERVED"}
    intent = decision.intents[0]
    try:
        allowed = intent_allowed_tool_ids(intent.route, intent.intent)
        forced = forced_tool_for_intent(intent.intent)
    except ValueError:
        return {"component_status": "FAIL", "reason": "INVALID_INTENT_TOOL_MAPPING"}
    if allowed is None:
        return {"component_status": "UNKNOWN", "reason": "INTENT_ALLOWLIST_UNAVAILABLE"}
    registry = build_fitness_tool_registry(cast(GatewayClient, _Gateway()))
    try:
        writes = sorted(tool for tool in allowed if not registry.get(tool).read_only)
    except ToolRegistryError:
        return {"component_status": "UNKNOWN", "reason": "ALLOWED_TOOL_NOT_REGISTERED"}
    observed = {
        "route": intent.route,
        "intent": intent.intent,
        "forced_tool": forced,
        "write_tools": writes,
    }
    passed = (
        intent.route == "CUSTOMER_SERVICE"
        and intent.intent == "QUERY_CONTRACT"
        and forced is None
        and not writes
    )
    return {
        "component_status": "PASS" if passed else "FAIL",
        "reason": "NEUTRAL_CONTRACT_ROUTE_AND_TOOL_ALLOWLIST_ONLY",
        "observed": observed,
    }


async def evaluate_remaining_routing_observations(
    dataset: Dataset, *, split: str
) -> dict[str, Any]:
    if split not in {"dev", "test"}:
        raise ValueError("split must be dev or test")
    rows = []
    for case in dataset.cases:
        spec = _CASES.get(case.case_id)
        if spec is None or spec[0] != split:
            continue
        if _case_hash(case) != spec[1]:
            rows.append(
                {
                    "case_id": case.case_id,
                    "component_status": "UNKNOWN",
                    "task_status": "UNKNOWN",
                    "reason": "CASE_CONTRACT_CHANGED",
                }
            )
            continue
        row = {"case_id": case.case_id, "task_status": "UNKNOWN"}
        try:
            if case.case_id == "ROUT-005":
                row.update(await _clarification_run(spec[2], case.case_id))
            elif case.case_id == "ROUT-007":
                row.update(await _role_probe(spec[2]))
            else:
                row.update(_neutral_route(spec[2]))
        except Exception:  # noqa: BLE001 - suppress exception content
            row.update(component_status="ERROR", reason="PROBE_ERROR")
        rows.append(row)
    return {
        "scope": "case_bound_remaining_routing_component_boundaries",
        "source": "draft_cases_and_synthetic_fixtures",
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.sha256,
        "split": split,
        "limitations": [
            "ROUT-005 actor is STUDENT although its fixture premise describes a coach; generic fallback does not establish coach schedule access.",
            "ROUT-007 uses scripted guessed tool calls and a synthetic student identity, not a signed identity, member-list feature or real Gateway authorization.",
            "ROUT-014 checks only one deterministic route and its tool allowlist; it does not establish a meaningful clarification or final answer.",
            "No real model, personal data, database or business service is involved; task outcomes remain UNKNOWN.",
        ],
        "cases": rows,
    }
