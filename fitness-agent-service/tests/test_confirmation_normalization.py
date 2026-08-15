from typing import cast

import pytest

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.tool_registry import ToolConfirmationNormalizationError
from app.confirmation.normalization import (
    ConfirmationNormalizationContext,
    ConfirmationResourceSnapshot,
    canonical_json_bytes,
)
from app.infrastructure.gateway_client import GatewayClient

from .test_tool_registry import FakeGateway


def context() -> ConfirmationNormalizationContext:
    return ConfirmationNormalizationContext(
        request_id="action-request-1",
        thread_id="thread-1",
        subject_user_id="coach-1",
        actor_roles=("COACH",),
        actor_organization_ids=("org-1",),
        trace_id="trace-1",
    )


def plan_snapshot() -> ConfirmationResourceSnapshot:
    return ConfirmationResourceSnapshot(
        organization_id="org-1",
        resource_id="plan-1",
        version=3,
        attributes={
            "title": "基础力量",
            "status": "DRAFT",
            "student_id": "student-1",
            "coach_id": "coach-1",
            "goal_type": "力量",
            "days": [{"day_number": 1, "title": "下肢"}],
        },
    )


def test_canonical_json_is_stable_but_preserves_training_array_order() -> None:
    first = canonical_json_bytes({"b": 2, "a": {"y": 1, "x": 2}, "days": [1, 2]})
    second = canonical_json_bytes({"days": [1, 2], "a": {"x": 2, "y": 1}, "b": 2})
    reordered = canonical_json_bytes({"b": 2, "a": {"x": 2, "y": 1}, "days": [2, 1]})

    assert first == second
    assert first != reordered


def test_create_draft_summary_contains_complete_payload_and_stable_hash() -> None:
    registry = build_fitness_tool_registry(cast(GatewayClient, FakeGateway()))
    raw = {
        "organization_id": "org-1",
        "student_id": "student-1",
        "coach_id": "coach-1",
        "title": "基础力量",
        "goal_type": "力量",
        "days": [
            {
                "day_number": 1,
                "title": "下肢",
                "items": [{"exercise_name": "深蹲", "sort_order": 1, "sets": 3, "reps": "8-10"}],
            }
        ],
    }

    action = registry.normalize_confirmation(
        "fitness.training.plan.create_draft.v1",
        raw,
        context=context(),
        organization_id="org-1",
    )

    assert action.action == "CREATE_TRAINING_DRAFT"
    assert action.resource_id is None
    assert action.expected_resource_version is None
    assert action.display_summary["operation"] == "创建训练计划草案"
    assert action.display_summary["details"]["title"] == "基础力量"
    assert action.payload_hash
    assert action.canonical_payload.decode("utf-8").find("深蹲") >= 0


def test_booking_payload_uses_java_gateway_camel_case_contract() -> None:
    registry = build_fitness_tool_registry(cast(GatewayClient, FakeGateway()))
    action = registry.normalize_confirmation(
        "fitness.booking.create.v1",
        {
            "organization_id": "org-1",
            "student_id": "student-1",
            "contract_id": "contract-1",
            "coach_id": "coach-1",
            "course_id": "course-1",
            "start_time": "2026-08-20T10:00:00Z",
            "end_time": "2026-08-20T11:00:00Z",
        },
        context=context(),
        organization_id="org-1",
    )

    canonical = action.canonical_payload.decode("utf-8")
    assert '"organizationId":"org-1"' in canonical
    assert '"startTime":"2026-08-20T10:00:00Z"' in canonical
    assert '"organization_id"' not in canonical


def test_reschedule_payload_uses_java_gateway_camel_case_contract() -> None:
    registry = build_fitness_tool_registry(cast(GatewayClient, FakeGateway()))
    action = registry.normalize_confirmation(
        "fitness.booking.reschedule.v1",
        {
            "organization_id": "org-1",
            "appointment_id": "appointment-1",
            "coach_id": "coach-2",
            "expected_start_time": "2026-08-20T10:00:00Z",
            "start_time": "2026-08-21T10:00:00Z",
            "end_time": "2026-08-21T11:00:00Z",
        },
        context=context(),
        organization_id="org-1",
    )

    canonical = action.canonical_payload.decode("utf-8")
    assert action.action == "RESCHEDULE_APPOINTMENT"
    assert action.resource_id == "appointment-1"
    assert '"appointmentId":"appointment-1"' in canonical
    assert '"expectedStartTime":"2026-08-20T10:00:00Z"' in canonical
    assert '"appointment_id"' not in canonical


def test_plan_action_requires_trusted_snapshot_and_binds_version() -> None:
    registry = build_fitness_tool_registry(cast(GatewayClient, FakeGateway()))
    with pytest.raises(ToolConfirmationNormalizationError):
        registry.normalize_confirmation(
            "fitness.training.plan.publish.v1",
            {"plan_id": "plan-1"},
            context=context(),
            organization_id="org-1",
        )

    action = registry.normalize_confirmation(
        "fitness.training.plan.publish.v1",
        {"plan_id": "plan-1"},
        context=context(),
        organization_id="org-1",
        resource=plan_snapshot(),
    )

    assert action.resource_id == "plan-1"
    assert action.expected_resource_version == 3
    assert action.display_summary["resource"]["status"] == "DRAFT"

    with pytest.raises(ToolConfirmationNormalizationError):
        registry.normalize_confirmation(
            "fitness.training.plan.publish.v1",
            {"plan_id": "plan-other"},
            context=context(),
            organization_id="org-1",
            resource=plan_snapshot(),
        )


def test_reject_review_requires_reason_and_summary_binds_decision() -> None:
    registry = build_fitness_tool_registry(cast(GatewayClient, FakeGateway()))
    with pytest.raises(ToolConfirmationNormalizationError):
        registry.normalize_confirmation(
            "fitness.training.plan.review.v1",
            {"plan_id": "plan-1", "decision": "REJECT"},
            context=context(),
            organization_id="org-1",
            resource=plan_snapshot(),
        )

    action = registry.normalize_confirmation(
        "fitness.training.plan.review.v1",
        {"plan_id": "plan-1", "decision": "REJECT", "comment": "动作安排需要调整"},
        context=context(),
        organization_id="org-1",
        resource=plan_snapshot(),
    )

    assert action.display_summary["target_status"] == "REJECTED"
    assert action.display_summary["details"] == {
        "comment": "动作安排需要调整",
        "decision": "REJECT",
    }


def test_training_day_execution_binds_day_status_and_plan_version() -> None:
    registry = build_fitness_tool_registry(cast(GatewayClient, FakeGateway()))
    resource = ConfirmationResourceSnapshot(
        organization_id="org-1",
        resource_id="plan-1:day-1",
        version=3,
        attributes=plan_snapshot().attributes,
    )
    action = registry.normalize_confirmation(
        "fitness.training.day.record_execution.v1",
        {"plan_id": "plan-1", "day_id": "day-1", "status": "COMPLETED", "note": "完成"},
        context=context(),
        organization_id="org-1",
        resource=resource,
    )

    assert action.resource_id == "plan-1:day-1"
    assert action.display_summary["target_status"] == "COMPLETED"
    assert action.display_summary["details"] == {
        "day_id": "day-1",
        "note": "完成",
        "status": "COMPLETED",
    }


def test_memory_write_binds_subject_scope_and_optimistic_version() -> None:
    registry = build_fitness_tool_registry(cast(GatewayClient, FakeGateway()))
    action = registry.normalize_confirmation(
        "fitness.memory.revoke.v1",
        {"organization_id": "org-1", "memory_id": "memory-1", "expected_version": 4},
        context=context(),
        organization_id="org-1",
    )

    assert action.resource_type == "agent_memory"
    assert action.resource_id == "memory-1"
    assert action.expected_resource_version == 4
    assert action.display_summary["target_status"] == "REVOKED"
    assert action.display_summary["details"]["expected_version"] == 4
