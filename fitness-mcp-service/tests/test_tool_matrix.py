"""All approved tools traverse the real MCP HTTP stack and fixed Gateway mapping."""

from __future__ import annotations

import httpx
import httpx2
import pytest
from fitness_tool_contracts import TOOL_SPECS, build_gateway_command, canonical_json_bytes
from jsonschema import validate
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from test_service import headers, running_service

PLAN = {
    "id": "p-1",
    "organizationId": "org-1",
    "studentId": "user-1",
    "coachId": "coach-1",
    "title": "基础力量",
    "goalType": "STRENGTH",
    "source": "AGENT",
    "status": "DRAFT",
    "version": 1,
    "createdBy": "user-1",
    "days": [],
}
BOOKING = {
    "id": "a-1",
    "organizationId": "org-1",
    "userId": "user-1",
    "coachId": "coach-1",
    "courseId": "course-1",
    "startTime": "2026-10-27T01:00:00Z",
    "endTime": "2026-10-27T02:00:00Z",
    "status": 1,
    "contractId": "contract-1",
    "remainingClassHours": 5,
}
TICKET = {
    "id": "t-1",
    "organizationId": "org-1",
    "subjectUserId": "user-1",
    "createdByUserId": "user-1",
    "category": "GENERAL",
    "source": "AGENT",
    "subject": "请求协助",
    "description": "课程问题",
    "status": "OPEN",
}
EXECUTION = {
    "id": "e-1",
    "planId": "p-1",
    "dayId": "d-1",
    "organizationId": "org-1",
    "studentId": "user-1",
    "status": "COMPLETED",
    "executionDate": "2026-10-27",
    "version": 1,
}
CREATE_BOOKING = {
    "organizationId": "org-1",
    "studentId": "user-1",
    "contractId": "contract-1",
    "coachId": "coach-1",
    "courseId": "course-1",
    "startTime": "2026-10-27T01:00:00Z",
    "endTime": "2026-10-27T02:00:00Z",
}
DRAFT = {
    "organizationId": "org-1",
    "studentId": "user-1",
    "coachId": "coach-1",
    "title": "基础力量",
    "goalType": "STRENGTH",
    "sessionMinutes": 45,
    "availableEquipment": [],
    "days": [
        {
            "dayNumber": 1,
            "title": "训练日",
            "items": [{"exerciseName": "深蹲", "sortOrder": 1, "sets": 3, "reps": "10"}],
        }
    ],
}
BASE = "/internal/agent-tools/v1"
CASES = [
    ("fitness.user.get_current.v1", {}, {"id": "user-1", "enabled": True}, "/me"),
    (
        "fitness.organization.get.v1",
        {"organization_id": "org-1"},
        {"id": "org-1"},
        "/organizations/org-1",
    ),
    (
        "fitness.course.list.v1",
        {"organization_id": "org-1", "limit": 2},
        [{"id": "course-1"}],
        "/courses",
    ),
    (
        "fitness.contract.list.v1",
        {"organization_id": "org-1", "user_id": "user-1"},
        [{"id": "contract-1", "organizationId": "org-1", "userId": "user-1"}],
        "/contracts",
    ),
    (
        "fitness.appointment.list.v1",
        {"organization_id": "org-1", "from_time": "2026-10-27T09:00:00+08:00"},
        [{"id": "a-1", "organizationId": "org-1", "userId": "user-1"}],
        "/appointments",
    ),
    (
        "fitness.booking.availability.check.v1",
        {
            "organization_id": "org-1",
            "student_id": "user-1",
            "coach_id": "coach-1",
            "course_id": "course-1",
            "start_time": "2026-10-27T01:00:00Z",
            "end_time": "2026-10-27T02:00:00Z",
        },
        {
            "organizationId": "org-1",
            "studentId": "user-1",
            "coachId": "coach-1",
            "courseId": "course-1",
            "startTime": "2026-10-27T01:00:00Z",
            "endTime": "2026-10-27T02:00:00Z",
            "available": True,
            "reasonCodes": [],
            "conflicts": [],
        },
        "/booking/availability",
    ),
    ("fitness.training.plan.get.v1", {"plan_id": "p-1"}, PLAN, "/training/plans/p-1"),
    (
        "fitness.training.day.executions.list.v1",
        {"plan_id": "p-1"},
        [EXECUTION],
        "/training/plans/p-1/executions",
    ),
    (
        "fitness.support.ticket.list.v1",
        {"organization_id": "org-1", "subject_user_id": "user-1", "status": "OPEN"},
        [TICKET],
        "/customer-service/tickets",
    ),
    (
        "fitness.support.ticket.get.v1",
        {"organization_id": "org-1", "ticket_id": "t-1"},
        TICKET,
        "/customer-service/tickets/t-1",
    ),
    (
        "fitness.operations.metric.aggregate.v1",
        {
            "organization_id": "org-1",
            "metric": "APPOINTMENT_COUNT",
            "from_date": "2026-10-01",
            "to_date": "2026-10-27",
            "bucket": "NONE",
        },
        {
            "metric": "APPOINTMENT_COUNT",
            "organizationId": "org-1",
            "from": "2026-10-01",
            "to": "2026-10-27",
            "rows": [{"dimension": "TOTAL", "label": "预约", "value": 3}],
            "generatedAt": "2026-10-27T01:00:00Z",
        },
        "/operations/metrics",
    ),
    ("fitness.booking.create.v1", {"payload": CREATE_BOOKING}, BOOKING, "/appointments"),
    (
        "fitness.booking.reschedule.v1",
        {
            "payload": {
                "organizationId": "org-1",
                "appointmentId": "a-1",
                "coachId": "coach-1",
                "expectedStartTime": "2026-10-27T01:00:00Z",
                "startTime": "2026-10-27T02:00:00Z",
                "endTime": "2026-10-27T03:00:00Z",
            }
        },
        BOOKING,
        "/appointments/a-1/reschedule",
    ),
    (
        "fitness.booking.cancel.v1",
        {
            "payload": {
                "organizationId": "org-1",
                "appointmentId": "a-1",
                "expectedStartTime": "2026-10-27T01:00:00Z",
            }
        },
        BOOKING | {"cancelled": True},
        "/appointments/a-1/cancel",
    ),
    ("fitness.training.plan.create_draft.v1", {"payload": DRAFT}, PLAN, "/training/plans/drafts"),
    (
        "fitness.training.plan.submit_review.v1",
        {"plan_id": "p-1"},
        PLAN | {"status": "PENDING_REVIEW"},
        "/training/plans/p-1/submit-review",
    ),
    (
        "fitness.training.plan.review.v1",
        {"plan_id": "p-1", "payload": {"decision": "APPROVE", "comment": None}},
        PLAN | {"status": "APPROVED"},
        "/training/plans/p-1/review",
    ),
    (
        "fitness.training.plan.publish.v1",
        {"plan_id": "p-1"},
        PLAN | {"status": "PUBLISHED"},
        "/training/plans/p-1/publish",
    ),
    (
        "fitness.training.day.record_execution.v1",
        {"plan_id": "p-1", "day_id": "d-1", "payload": {"status": "COMPLETED", "note": "完成"}},
        EXECUTION,
        "/training/plans/p-1/days/d-1/execution",
    ),
    (
        "fitness.support.ticket.create.v1",
        {
            "payload": {
                "organizationId": "org-1",
                "category": "GENERAL",
                "subject": "请求协助",
                "description": "课程问题",
            }
        },
        TICKET,
        "/customer-service/tickets",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arguments,response,path", CASES, ids=[case[0] for case in CASES])
async def test_all_twenty_fixed_tools(name, arguments, response, path):
    received = []

    def gateway(request):
        received.append(request)
        return httpx.Response(200, json=response)

    spec = TOOL_SPECS[name]
    extra = {"X-Confirmation-Token": "approved-single-action"} if spec.write else {}
    with running_service(gateway) as (url, _app):
        async with (
            httpx2.AsyncClient(headers=headers(**extra), timeout=10, trust_env=False) as pool,
            Client(streamable_http_client(url + "/mcp", http_client=pool)) as client,
        ):
            result = await client.call_tool(name, arguments)
            assert not result.is_error, result.structured_content
            validate(result.structured_content, spec.output_schema)
            assert result.structured_content["data"] == spec.output_json(response)
    assert len(received) == 1
    command = build_gateway_command(name, arguments)
    request = received[0]
    assert request.url.path == BASE + path
    assert request.method == ("POST" if spec.write else "GET")
    assert dict(request.url.params) == {key: str(value) for key, value in command.query.items()}
    assert request.content == (command.body or b"")
    if spec.write and "payload" in arguments and name != "fitness.training.day.record_execution.v1":
        assert request.content == canonical_json_bytes(arguments["payload"])
