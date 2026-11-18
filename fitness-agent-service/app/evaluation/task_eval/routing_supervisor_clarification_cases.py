"""Case-bound Supervisor fallback observations with a locally unavailable model.

Only generic clarification delivery is tested; actual semantic sufficiency,
booking state and model-available behavior remain unknown.
"""

from __future__ import annotations

from typing import Any, cast

from app.agent.routing import HybridIntentRouter, deterministic_route_decision
from app.agent.supervisor import Supervisor, SupervisorRequest
from app.agent.tool_registry import ToolRegistry
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import GatewayRequestContext
from app.infrastructure.model_gateway import ModelConfigurationError, ModelGateway, ModelTurn

from .dataset import Dataset
from .safety_injection_cases import _case_hash

_CASES = {
    "ROUT-009": (
        "dev",
        "2597a6cd5692d430f3fcd725b7a38aec793f64237b07cd2b8ea4fb6678a8c0e8",
        "帮我改一下时间",
    ),
    "ROUT-010": (
        "dev",
        "cd7eb59ad1b086d5a1cf7bc4ac0fd607a05764fe19bfd82faa76f9a37bdbf5dd",
        "我想练腿",
    ),
    "ROUT-011": (
        "dev",
        "c0d7478f196d70889d44f8a18fc2ba5ee2cbb68ebdd02d2e57a6a796410e567a",
        "帮我看看数据",
    ),
    "ROUT-012": (
        "dev",
        "e277689477b3d318e2c8056f7a63d05da4712fd3e6ad214f87db656e2c655578",
        "周六下午",
    ),
    "ROUT-013": (
        "test",
        "9b1a0d5f016ef2f3ab375dcd636086811c0c2fab85b52c92828153676443c5bf",
        "帮我在附近门店约课",
    ),
    "ROUT-016": (
        "dev",
        "114be97ebd7397d0fd95a476e4f33a0dfa650ae7362dd973bd0f90818713b155",
        "查我的剩余课时，然后帮我约课",
    ),
    "ROUT-017": (
        "dev",
        "d5826fcbdda7698b1cd8efa847bf66a9c988ef9e31101058c2cdd7df660b9188",
        "查看我本周预约并取消周五的那节",
    ),
    "ROUT-018": (
        "test",
        "3430bf166ab1bb2b861e16b182c626a8bec95a771b5ae49381f5c4f2cb9a6aa6",
        "查本月新增会员并建议跟进措施",
    ),
}
_GENERIC_FALLBACK = (
    "我还不能确定你要处理的是预约、训练计划、经营数据还是客服问题，请再具体说明一下。"
)
_IDENTITY = AgentIdentity("actor_a", frozenset({"org_a"}), frozenset({"STUDENT"}), 1, 9999999999)


class _UnavailableModel:
    def __init__(self) -> None:
        self.router_calls = 0
        self.domain_calls = 0

    async def chat_json_with_usage(self, *args: Any, **kwargs: Any) -> Any:
        self.router_calls += 1
        raise ModelConfigurationError("synthetic model unavailable")

    async def chat_with_tools(self, *args: Any, **kwargs: Any) -> ModelTurn:
        self.domain_calls += 1
        return ModelTurn(content="合成模型不应被调用", tool_calls=())


async def _run(message: str, case_id: str) -> dict[str, Any]:
    deterministic = deterministic_route_decision(message)
    if deterministic is not None and (
        deterministic.confidence >= 0.98
        or len(deterministic.intents) > 1
        or any(intent.is_write for intent in deterministic.intents)
    ):
        return {
            "component_status": "UNKNOWN",
            "reason": "MODEL_UNAVAILABLE_PRECONDITION_NOT_APPLICABLE",
        }
    model = _UnavailableModel()
    supervisor = Supervisor(
        cast(ModelGateway, model),
        ToolRegistry(),
        intent_router=HybridIntentRouter(cast(ModelGateway, model)),
    )
    try:
        result = await supervisor.invoke(
            SupervisorRequest(
                user_message=message,
                gateway_context=GatewayRequestContext(
                    "synthetic-context", "synthetic-request", "synthetic-trace"
                ),
                conversation_id=f"synthetic-{case_id}",
                identity=_IDENTITY,
            )
        )
    except Exception:  # noqa: BLE001 - do not disclose untrusted error content
        return {"component_status": "ERROR", "reason": "PROBE_ERROR"}
    observed = {
        "router_stub_calls": model.router_calls,
        "domain_stub_calls": model.domain_calls,
        "route": result.route,
        "route_source": result.route_source,
        "supervisor_status": result.status,
        "tool_steps": result.tool_steps,
        "matches_generic_fallback": result.answer == _GENERIC_FALLBACK,
    }
    if model.router_calls != 1:
        return {
            "component_status": "UNKNOWN",
            "reason": "MODEL_UNAVAILABLE_PRECONDITION_NOT_OBSERVED",
            "observed": observed,
        }
    passed = (
        model.domain_calls == 0
        and result.status == "CLARIFICATION_REQUIRED"
        and result.route == "CLARIFICATION"
        and result.route_source == "FALLBACK"
        and result.tool_steps == 0
        and result.answer == _GENERIC_FALLBACK
    )
    return {
        "component_status": "PASS" if passed else "FAIL",
        "reason": "GENERIC_FALLBACK_DELIVERY_ONLY",
        "observed": observed,
    }


async def evaluate_supervisor_clarification_observations(
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
        row.update(await _run(spec[2], case.case_id))
        rows.append(row)
    return {
        "scope": "case_bound_supervisor_model_unavailable_clarification",
        "source": "draft_cases_and_offline_unavailable_model_stub",
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.sha256,
        "split": split,
        "limitations": [
            "Only model-unavailable generic clarification delivery through the Python Supervisor is observed; the real model is not called.",
            "A generic clarification does not establish that the needed booking, customer, medical or business details were requested.",
            "The empty tool registry and synthetic identity do not verify authorization, business state, realistic tool execution or model-available routing.",
            "Component PASS is not complete task success; task outcomes remain UNKNOWN and the draft cases need review.",
        ],
        "cases": rows,
    }
