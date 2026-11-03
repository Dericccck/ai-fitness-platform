import json
import math

import pytest
from fitness_tool_contracts import TOOL_SPECS, build_gateway_command, canonical_json_bytes
from fitness_tool_contracts.canonical import CanonicalEncodingError
from jsonschema import Draft202012Validator
from pydantic import ValidationError


def test_catalog_is_exact_and_every_schema_is_valid():
    assert len(TOOL_SPECS) == 20
    assert sum(spec.write for spec in TOOL_SPECS.values()) == 9
    assert "fitness.operations.metric.aggregate.v1" in TOOL_SPECS
    assert "fitness.operations.metric.query.v1" not in TOOL_SPECS
    for spec in TOOL_SPECS.values():
        Draft202012Validator.check_schema(spec.input_schema)
        Draft202012Validator.check_schema(spec.output_schema)
        assert len(spec.fingerprint) == 64
        assert spec.fingerprint == spec.fingerprint


def test_confirmed_payload_keeps_omissions_and_chinese_bytes():
    payload = {
        "organizationId": "org-1",
        "studentId": "user-1",
        "contractId": "c-1",
        "coachId": "coach-1",
        "courseId": "course-1",
        "startTime": "2026-10-27T01:00:00Z",
        "endTime": "2026-10-27T02:00:00Z",
    }
    command = build_gateway_command("fitness.booking.create.v1", {"payload": payload})
    assert command.body == canonical_json_bytes(payload)
    assert b'"mark"' not in command.body
    assert command.method == "POST"
    payload2 = {
        "organizationId": "org-1",
        "category": "GENERAL",
        "subject": "预约问题",
        "description": "需要协助",
    }
    command2 = build_gateway_command("fitness.support.ticket.create.v1", {"payload": payload2})
    assert command2.body == canonical_json_bytes(payload2)
    assert "预约问题".encode() in command2.body
    assert "subjectUserId" not in json.loads(command2.body)


def test_empty_body_and_training_execution_mapping():
    for name in ("fitness.training.plan.submit_review.v1", "fitness.training.plan.publish.v1"):
        command = build_gateway_command(name, {"plan_id": "p-1"})
        assert command.body == b"{}"
        assert "p-1" in command.path
    command = build_gateway_command(
        "fitness.training.day.record_execution.v1",
        {"plan_id": "p-1", "day_id": "d-1", "payload": {"status": "COMPLETED", "note": "已完成"}},
    )
    assert command.body == canonical_json_bytes(
        {"dayId": "d-1", "status": "COMPLETED", "note": "已完成"}
    )
    assert command.path.endswith("/p-1/days/d-1/execution")


def test_read_time_zone_alias_and_path_constraints():
    command = build_gateway_command(
        "fitness.appointment.list.v1",
        {"organization_id": "org-1", "from_time": "2026-10-27T09:00:00"},
    )
    assert command.query["from"] == "2026-10-27T01:00:00Z"
    with pytest.raises(ValidationError):
        build_gateway_command("fitness.training.plan.get.v1", {"plan_id": "../secrets"})
    with pytest.raises(ValidationError):
        build_gateway_command("fitness.user.get_current.v1", {"token": "secret"})
    with pytest.raises(KeyError):
        build_gateway_command("unregistered_tool", {})


def test_non_finite_numbers_are_rejected_and_arrays_are_not_sorted():
    with pytest.raises(CanonicalEncodingError):
        canonical_json_bytes({"value": math.nan})
    assert canonical_json_bytes({"汉字": [2, 1]}) == '{"汉字":[2,1]}'.encode()


def test_gateway_method_optional_defaults_and_persisted_execution_payload():
    for name in (
        "fitness.course.list.v1",
        "fitness.contract.list.v1",
        "fitness.appointment.list.v1",
        "fitness.support.ticket.list.v1",
    ):
        command = build_gateway_command(name, {"organization_id": "org-1", "limit": None})
        assert "limit" not in command.query
    command = build_gateway_command(
        "fitness.training.day.record_execution.v1",
        {
            "plan_id": "p-1",
            "day_id": "d-1",
            "payload": {"day_id": "d-1", "status": "COMPLETED", "note": None},
        },
    )
    assert command.body == canonical_json_bytes(
        {"dayId": "d-1", "status": "COMPLETED", "note": None}
    )
    with pytest.raises(ValidationError):
        build_gateway_command(
            "fitness.training.day.record_execution.v1",
            {
                "plan_id": "p-1",
                "day_id": "d-1",
                "payload": {"day_id": "another-day", "status": "COMPLETED"},
            },
        )


@pytest.mark.parametrize("resource_id", [".", ".."])
def test_dot_ids_cannot_escape_fixed_gateway_routes(resource_id):
    with pytest.raises(ValueError):
        build_gateway_command("fitness.training.plan.get.v1", {"plan_id": resource_id})
