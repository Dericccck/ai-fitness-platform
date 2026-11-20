"""Fixed draft routing case through the real Supervisor and a bounded router model.

Only a synthetic user message is sent. The empty registry and disabled domain
model mean this is a routing observation, not a complete task or release run.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from pydantic import ValidationError

from app.agent.routing import (
    HybridIntentRouter,
    _ModelRouteDecision,
    _validate_model_intent,
    router_system_prompt,
)
from app.agent.supervisor import Supervisor, SupervisorRequest
from app.agent.tool_registry import ToolRegistry
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import GatewayRequestContext
from app.infrastructure.model_gateway import JsonModelTurn, ModelConfigurationError, ModelGateway

from .budget_store import DurableBudget
from .dataset import Dataset, load_dataset
from .report import summarize
from .routing_model_probe import MODEL, SYSTEM_SHA256, _RouterModel
from .runner import _empty_assertions, _revision
from .runner_dev_sandbox import _publish

SUPPORTED_ROUTING_CASES = {
    "ROUT-003": "查询本月门店收入",
    "ROUT-005": "查看我今天的授课安排",
    "ROUT-009": "帮我改一下时间",
    "ROUT-010": "我想练腿",
    "ROUT-011": "帮我看看数据",
    "ROUT-012": "周六下午",
    "ROUT-017": "查看我本周预约并取消周五的那节",
    "ROUT-019": "给我看所有会员的收入报表",
}
_DATASET_SHA256 = "1d187f5abca4dab754db29059f9ff95a7897719fde6ce88de6c92448d25ab446"
_DATASET = Path(__file__).resolve().parents[3] / "evals/task_eval/datasets/v1.jsonl"
_IDENTITY = AgentIdentity("actor_a", frozenset({"org_a"}), frozenset({"STUDENT"}), 1, 9999999999)


class _RouterOnlyModel(_RouterModel):
    domain_calls = 0
    model_response_decision: dict[str, Any] | None = None

    async def chat_json_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> JsonModelTurn:
        self.model_response_decision = None
        turn = await super().chat_json_with_usage(
            messages, max_output_tokens=max_output_tokens, temperature=temperature
        )
        try:
            parsed = _ModelRouteDecision.model_validate_json(turn.content)
            intents = [_validate_model_intent(item) for item in parsed.intents]
        except (ValidationError, ValueError):
            # Do not retain invalid or partially validated provider output.
            return turn
        self.model_response_decision = {
            "stage": "model_response_before_router_policy",
            "confidence": parsed.confidence,
            "intents": [
                {"route": item.route, "intent": item.intent, "is_write": item.is_write}
                for item in intents
            ],
        }
        return turn

    async def chat_with_tools(self, *args: Any, **kwargs: Any) -> Any:
        # The case is restricted to router inspection. No domain prompts or
        # tools may leave this process even if the router selects a domain.
        self.domain_calls += 1
        raise ModelConfigurationError("domain model disabled in routing component probe")


def _open_private_credential(path: str) -> int:
    """Bind checks and subsequent reads to the same non-symlink descriptor."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise ValueError("private credential unavailable") from exc
    try:
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or not mode & stat.S_IRUSR
            or mode & 0o177
            or not 16 <= info.st_size <= 4096
        ):
            raise ValueError("private credential unavailable")
        return fd
    except BaseException:
        os.close(fd)
        raise


async def _observe(
    credential: str,
    budget: DurableBudget,
    case_id: str = "ROUT-012",
    *,
    transport: httpx.MockTransport | None = None,
) -> dict[str, Any]:
    if case_id not in SUPPORTED_ROUTING_CASES:
        raise ValueError("unsupported routing case")
    message = SUPPORTED_ROUTING_CASES[case_id]
    model = _RouterOnlyModel(credential, budget, transport, 1, supervisor_context=True)
    model.expected_message = message
    supervisor = Supervisor(
        cast(ModelGateway, model),
        ToolRegistry(),
        intent_router=HybridIntentRouter(cast(ModelGateway, model), timeout_seconds=30),
    )
    observed: dict[str, Any] = {"supervisor_completed": False, "supervisor_final": None}
    try:
        response = await supervisor.invoke(
            SupervisorRequest(
                user_message=message,
                gateway_context=GatewayRequestContext(
                    "synthetic-context", "synthetic-request", "synthetic-trace"
                ),
                conversation_id="synthetic-routing-component",
                identity=_IDENTITY,
            )
        )
        observed.update(
            supervisor_completed=True,
            supervisor_final={
                "route": response.route,
                "route_source": response.route_source,
                "clarification": response.status == "CLARIFICATION_REQUIRED",
                "tool_steps": response.tool_steps,
            },
            clarification=response.status == "CLARIFICATION_REQUIRED",
            route=response.route,
            route_source=response.route_source,
            tool_steps=response.tool_steps,
        )
    except Exception:  # noqa: BLE001 - model/provider exceptions may contain sensitive text
        observed["reason"] = "SUPERVISOR_DID_NOT_COMPLETE"
    observed.update(
        model_attempts=model.attempts,
        model_status=model.last_status,
        model_usage=model.last_usage,
        schema_errors=model.last_schema_errors,
        model_response_decision=model.model_response_decision,
        disabled_domain_calls=model.domain_calls,
    )
    # A fallback after a failed call is not a model observation, and a domain
    # route cannot be judged as a final answer with the domain model disabled.
    observed["component_status"] = (
        "OBSERVED"
        if model.attempts == 1
        and model.last_status == "MODEL_RESPONSE_RECEIVED"
        and model.last_schema_errors == []
        else "INCONCLUSIVE"
    )
    return observed


def execute_development_routing(
    dataset: Dataset,
    *,
    split: str,
    mode: str,
    repeats: int,
    seed: int,
    output_dir: str,
    case_id: str,
    real_model_options: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Write one fixed component trial in the standard runner report format."""
    if (
        case_id not in SUPPORTED_ROUTING_CASES
        or split != "dev"
        or mode != "model_sandbox"
        or repeats != 1
        or seed != 0
        or set(real_model_options)
        != {
            "credential_file",
            "budget_file",
            "budget_limit_nano",
            "budget_opening_nano",
            "minimum_reserved_nano",
            "confirm_current_pricing",
        }
        or real_model_options["confirm_current_pricing"] is not True
        or any(
            not isinstance(real_model_options[key], str)
            or not Path(real_model_options[key]).is_absolute()
            for key in ("credential_file", "budget_file")
        )
        or any(
            type(real_model_options[key]) is not int or real_model_options[key] < 0
            for key in ("budget_limit_nano", "budget_opening_nano", "minimum_reserved_nano")
        )
    ):
        raise ValueError("invalid routing development configuration")
    frozen = load_dataset(_DATASET, check_quotas=False)
    selected = [case for case in dataset.cases if case.case_id == case_id]
    if (
        dataset.sha256 != _DATASET_SHA256
        or frozen.sha256 != _DATASET_SHA256
        or dataset.dataset_version != "fitness-task-planning-v1"
        or len(selected) != 1
        or selected[0].split != "dev"
        or selected[0].review.status != "draft"
        or selected[0].capability_status != "planned"
        or not selected[0].steps
        or selected[0].steps[0].type != "chat"
        or selected[0].steps[0].message != SUPPORTED_ROUTING_CASES[case_id]
        or [c.model_dump(mode="json") for c in dataset.cases]
        != [c.model_dump(mode="json") for c in frozen.cases]
        or hashlib.sha256(router_system_prompt().encode()).hexdigest() != SYSTEM_SHA256
    ):
        raise ValueError("routing development case contract changed")
    sources = {
        "routing_component": Path(__file__),
        "routing_model_boundary": Path(__file__).with_name("routing_model_probe.py"),
        "supervisor": Path(__file__).resolve().parents[2] / "agent/supervisor.py",
        "router": Path(__file__).resolve().parents[2] / "agent/routing.py",
    }
    source_hashes = {
        key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in sources.items()
    }
    budget = DurableBudget.open_existing(
        real_model_options["budget_file"],
        limit_nano=real_model_options["budget_limit_nano"],
        opening_nano=real_model_options["budget_opening_nano"],
    )
    snapshot = budget.snapshot()
    if (
        snapshot["reserved_nano"] < real_model_options["minimum_reserved_nano"]
        or snapshot["frozen"]
        or snapshot["pending"]
    ):
        raise ValueError("budget history unavailable")
    # Bind to one checked inode, then reserve the destination before reading it.
    key_fd = _open_private_credential(real_model_options["credential_file"])
    try:
        output = Path(output_dir)
        output.mkdir(parents=False, exist_ok=False)
        raw_key = os.read(key_fd, 4097)
    finally:
        os.close(key_fd)
    try:
        credential = raw_key.decode("utf-8").strip()
    except UnicodeError as exc:
        raise ValueError("private credential unavailable") from exc
    if not 16 <= len(credential) <= 4096 or len(raw_key) > 4096:
        raise ValueError("private credential unavailable")
    start = time.monotonic()
    observation = asyncio.run(_observe(credential, budget, case_id))
    if any(
        hashlib.sha256(path.read_bytes()).hexdigest() != source_hashes[key]
        for key, path in sources.items()
    ):
        raise ValueError("routing source changed during execution")
    case = selected[0]
    trial = {
        "trial_id": uuid4().hex,
        "case_id": case.case_id,
        "family_id": case.family_id,
        "split": case.split,
        "primary_scenario": case.primary_scenario,
        "delivery_scope": case.delivery_scope,
        "capability_status": case.capability_status,
        "trial_index": 0,
        "seed": seed,
        "executed": observation["model_attempts"] == 1,
        "status": "INCONCLUSIVE",
        "reason": "ROUTING_COMPONENT_ONLY",
        "assertions": _empty_assertions(case, "UNKNOWN", "COMPLETE_TASK_EVIDENCE_MISSING"),
        "duration_ms": round((time.monotonic() - start) * 1000, 3),
        "component_observation": observation,
    }
    manifest = {
        "schema_version": "1.0",
        "run_id": uuid4().hex,
        "mode": mode,
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.sha256,
        "revision": _revision(),
        "source_identity": source_hashes,
        "selection": {"split": "dev", "scope": "single_case", "include_planned": True},
        "selected_case_ids": [case_id],
        "fixture_sha256": "unknown",
        "grader_version": "task-eval-rules-v1",
        "repeats": 1,
        "seed": 0,
        "adapter": "router_only_supervisor",
        "result_origin": "synthetic_real_model_routing_component",
        "model": MODEL,
        "model_call_attempted": observation["model_attempts"] == 1,
        "prompt_version": SYSTEM_SHA256,
        "tool_schema_version": "unknown",
        "index_version": "unknown",
        "environment": "local_empty_tool_registry",
        "status": "INCOMPLETE",
        "review": "draft",
        "limitations": [
            "Only routing and clarification through the Supervisor are observed; the domain model and tools are disabled.",
            "This draft component observation is not a complete task result, business audit, or release evidence.",
            "The local budget is not a provider-side spending cap; the model alias is not immutable.",
        ],
    }
    summary = summarize([trial], 1)
    _publish(output, manifest, trial, summary)
    return manifest, [trial], summary
