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
