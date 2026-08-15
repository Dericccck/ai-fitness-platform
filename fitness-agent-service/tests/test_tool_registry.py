from typing import Any, cast

import pytest
from pydantic import BaseModel

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.tool_registry import (
    DuplicateToolError,
    InvalidToolDefinitionError,
    ToolAuditEvent,
    ToolConfirmationRequiredError,
    ToolContext,
    ToolDefinition,
    ToolInputValidationError,
    ToolRegistry,
    ToolRegistryError,
    ToolRoleForbiddenError,
    UnknownToolError,
)
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import (
    GatewayClient,
    GatewayCourse,
    GatewayRequestContext,
    GatewayUser,
)


class RecordingAuditSink:
    def __init__(self) -> None:
        self.events: list[ToolAuditEvent] = []

    def record(self, event: ToolAuditEvent) -> None:
        self.events.append(event)


class FakeGateway:
    def __init__(self) -> None:
        self.current_user_calls = 0
        self.course_calls: list[tuple[str, int | None]] = []

    async def get_current_user(self, context: GatewayRequestContext) -> GatewayUser:
        assert context.signed_context == "signed-context"
        self.current_user_calls += 1
        return GatewayUser(id="user-1", name="学员", enabled=True)

    async def get_organization(
        self, context: GatewayRequestContext, organization_id: str
    ) -> dict[str, str]:
        return {"id": organization_id}

    async def list_courses(
        self,
        context: GatewayRequestContext,
        organization_id: str,
        *,
        limit: int | None = None,
    ) -> list[GatewayCourse]:
        self.course_calls.append((organization_id, limit))
        return [GatewayCourse(id="course-1", name="力量训练")]

    async def list_contracts(
        self,
        context: GatewayRequestContext,
        organization_id: str,
        *,
        user_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, str]]:
        return [{"id": "contract-1", "user_id": user_id or "self"}]

    async def list_appointments(
        self,
        context: GatewayRequestContext,
        organization_id: str,
        *,
        user_id: str | None = None,
        from_time: Any = None,
        to_time: Any = None,
        limit: int | None = None,
    ) -> list[dict[str, str]]:
        return [{"id": "appointment-1"}]

    async def check_booking_availability(
        self,
        context: GatewayRequestContext,
        organization_id: str,
        *,
        student_id: str | None,
        coach_id: str,
        course_id: str | None,
        start_time: Any,
        end_time: Any,
        exclude_appointment_id: str | None = None,
    ) -> dict[str, Any]:
        return {"organization_id": organization_id, "available": True}

    async def create_booking(
        self,
        context: GatewayRequestContext,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {"id": "appointment-1", "payload": payload}

    async def reschedule_booking(
        self,
        context: GatewayRequestContext,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {"id": "appointment-1", "payload": payload}

    async def get_training_plan(
        self, context: GatewayRequestContext, plan_id: str
    ) -> dict[str, Any]:
        return {"id": plan_id}

    async def create_training_draft(
        self, context: GatewayRequestContext, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return {"id": "plan-1"}

    async def submit_training_review(
        self, context: GatewayRequestContext, plan_id: str
    ) -> dict[str, Any]:
        return {"id": plan_id}

    async def review_training_plan(
        self, context: GatewayRequestContext, plan_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return {"id": plan_id}

    async def publish_training_plan(
        self, context: GatewayRequestContext, plan_id: str
    ) -> dict[str, Any]:
        return {"id": plan_id}


def tool_context() -> ToolContext:
    return ToolContext(
        gateway_context=GatewayRequestContext(
            signed_context="signed-context",
            request_id="request-1",
            trace_id="trace-1",
        )
    )


def test_fitness_registry_exposes_only_versioned_specs() -> None:
    registry = build_fitness_tool_registry(cast(GatewayClient, FakeGateway()))

    specs = registry.public_specs()

    assert [spec["name"] for spec in specs] == [
        "fitness.appointment.list.v1",
        "fitness.booking.availability.check.v1",
        "fitness.booking.create.v1",
        "fitness.booking.reschedule.v1",
        "fitness.contract.list.v1",
        "fitness.course.list.v1",
        "fitness.memory.list.v1",
        "fitness.memory.revoke.v1",
        "fitness.memory.save.v1",
        "fitness.organization.get.v1",
        "fitness.training.day.executions.list.v1",
        "fitness.training.day.record_execution.v1",
        "fitness.training.plan.create_draft.v1",
        "fitness.training.plan.generate_draft.v1",
        "fitness.training.plan.get.v1",
        "fitness.training.plan.publish.v1",
        "fitness.training.plan.review.v1",
        "fitness.training.plan.submit_review.v1",
        "fitness.user.get_current.v1",
    ]
    assert sum(spec["read_only"] is False for spec in specs) == 9
    assert sum(spec["requires_confirmation"] is True for spec in specs) == 9


async def test_registry_validates_input_calls_fixed_gateway_adapter_and_serializes_result() -> None:
    gateway = FakeGateway()
    registry = build_fitness_tool_registry(cast(GatewayClient, gateway))

    result = await registry.invoke(
        "fitness.course.list.v1",
        {"organization_id": "org-1", "limit": 5},
        tool_context(),
    )

    assert result == [
        {
            "id": "course-1",
            "name": "力量训练",
            "code": None,
            "price": None,
            "status": None,
        }
    ]
    assert gateway.course_calls == [("org-1", 5)]


async def test_registry_rejects_extra_input_and_records_safe_failure() -> None:
    sink = RecordingAuditSink()
    gateway = FakeGateway()
    registry = ToolRegistry(audit_sink=sink)
    fitness_registry = build_fitness_tool_registry(cast(GatewayClient, gateway))
    for spec in fitness_registry.public_specs():
        registry.register(fitness_registry.get(spec["name"]))

    with pytest.raises(ToolInputValidationError):
        await registry.invoke(
            "fitness.course.list.v1",
            {"organization_id": "org-1", "limit": 5, "user_id": "forbidden-extra"},
            tool_context(),
        )

    assert gateway.course_calls == []
    assert [event.status for event in sink.events] == ["started", "failed"]
    assert sink.events[-1].error_code == "INVALID_INPUT"


async def test_write_training_tool_requires_confirmation_before_gateway_call() -> None:
    gateway = FakeGateway()
    registry = build_fitness_tool_registry(cast(GatewayClient, gateway))

    with pytest.raises(ToolConfirmationRequiredError):
        await registry.invoke(
            "fitness.training.plan.create_draft.v1",
            {
                "organization_id": "org-1",
                "student_id": "student-1",
                "coach_id": "coach-1",
                "title": "基础力量",
                "goal_type": "力量",
                "days": [
                    {
                        "day_number": 1,
                        "title": "下肢",
                        "items": [
                            {
                                "exercise_name": "深蹲",
                                "sort_order": 1,
                                "sets": 3,
                                "reps": "8-10",
                            }
                        ],
                    }
                ],
            },
            tool_context(),
        )


async def test_registry_blocks_student_from_coach_only_generation_tool() -> None:
    gateway = FakeGateway()
    registry = build_fitness_tool_registry(cast(GatewayClient, gateway))
    context = ToolContext(
        gateway_context=GatewayRequestContext(signed_context="signed-context"),
        identity=AgentIdentity(
            subject="student-1",
            organization_ids=frozenset({"org-1"}),
            roles=frozenset({"STUDENT"}),
            issued_at=1,
            expires_at=2,
        ),
    )

    with pytest.raises(ToolRoleForbiddenError):
        await registry.invoke(
            "fitness.training.plan.generate_draft.v1",
            {
                "organization_id": "org-1",
                "student_id": "student-1",
                "coach_id": "coach-1",
                "goal_type": "力量",
                "training_days": 2,
                "level": "初级",
                "session_minutes": 45,
            },
            context,
        )


async def test_registry_rejects_unknown_tool_without_invoking_gateway() -> None:
    sink = RecordingAuditSink()
    gateway = FakeGateway()
    registry = build_fitness_tool_registry(cast(GatewayClient, gateway))
    registry_with_audit = ToolRegistry(audit_sink=sink)
    for spec in registry.public_specs():
        registry_with_audit.register(registry.get(spec["name"]))

    with pytest.raises(UnknownToolError):
        await registry_with_audit.invoke("fitness.course.list.v2", {}, tool_context())

    assert gateway.course_calls == []
    assert sink.events[-1].error_code == "UNKNOWN_TOOL"


def test_registry_rejects_duplicate_and_unsafe_write_definition() -> None:
    async def handler(_: BaseModel, __: ToolContext) -> None:
        return None

    definition = ToolDefinition(
        tool_id="fitness.example.read.v1",
        description="test",
        input_model=BaseModel,
        handler=handler,
        allowed_roles=frozenset({"STUDENT"}),
        read_only=True,
        requires_confirmation=False,
    )
    registry = ToolRegistry()
    registry.register(definition)

    with pytest.raises(DuplicateToolError):
        registry.register(definition)

    with pytest.raises(InvalidToolDefinitionError):
        registry.register(
            ToolDefinition(
                tool_id="fitness.example.write.v1",
                description="test",
                input_model=BaseModel,
                handler=handler,
                allowed_roles=frozenset({"STUDENT"}),
                read_only=False,
                requires_confirmation=False,
            )
        )


def test_tool_registry_error_hierarchy_is_stable() -> None:
    assert issubclass(ToolInputValidationError, ToolRegistryError)
