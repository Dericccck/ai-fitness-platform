"""Fixed Gateway routing and deterministic bodies shared with confirmation signing."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from .canonical import canonical_json_bytes, training_execution_payload
from .catalog import get_tool_spec


@dataclass(frozen=True)
class PreparedGatewayCommand:
    method: Literal["GET", "POST"]
    path: str
    query: dict[str, Any]
    body: bytes | None = None

    @property
    def canonical_body(self) -> bytes | None:
        return self.body


def _instant(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def build_gateway_command(tool_id: str, arguments: dict[str, Any]) -> PreparedGatewayCommand:
    spec = get_tool_spec(tool_id)
    data = spec.input_model.model_validate(arguments)
    base = "/internal/agent-tools/v1"
    query: dict[str, Any] = {}
    body: bytes | None = None
    method: Literal["GET", "POST"] = "POST" if spec.write else "GET"
    if tool_id == "fitness.user.get_current.v1":
        path = f"{base}/me"
    elif tool_id == "fitness.organization.get.v1":
        path = f"{base}/organizations/{data.organization_id}"
    elif tool_id == "fitness.course.list.v1":
        path = f"{base}/courses"
        query = {"organizationId": data.organization_id, "limit": data.limit}
    elif tool_id in {"fitness.contract.list.v1", "fitness.appointment.list.v1"}:
        leaf = "contracts" if tool_id == "fitness.contract.list.v1" else "appointments"
        path = f"{base}/{leaf}"
        query = {
            "organizationId": data.organization_id,
            "userId": data.user_id,
            "limit": data.limit,
        }
        if leaf == "appointments":
            query.update({"from": _instant(data.from_time), "to": _instant(data.to_time)})
    elif tool_id == "fitness.booking.availability.check.v1":
        path = f"{base}/booking/availability"
        query = {
            "organizationId": data.organization_id,
            "studentId": data.student_id,
            "coachId": data.coach_id,
            "courseId": data.course_id,
            "start": _instant(data.start_time),
            "end": _instant(data.end_time),
            "excludeAppointmentId": data.exclude_appointment_id,
        }
    elif tool_id == "fitness.operations.metric.aggregate.v1":
        path = f"{base}/operations/metrics"
        query = {
            "organizationId": data.organization_id,
            "metric": data.metric,
            "from": data.from_date.isoformat() if data.from_date else None,
            "to": data.to_date.isoformat() if data.to_date else None,
            "limit": data.limit,
            "bucket": data.bucket,
        }
    elif tool_id == "fitness.support.ticket.list.v1":
        path = f"{base}/customer-service/tickets"
        query = {
            "organizationId": data.organization_id,
            "subjectUserId": data.subject_user_id,
            "status": data.status,
            "limit": data.limit,
        }
    elif tool_id == "fitness.support.ticket.get.v1":
        path = f"{base}/customer-service/tickets/{data.ticket_id}"
        query = {"organizationId": data.organization_id}
    elif tool_id == "fitness.training.plan.get.v1":
        path = f"{base}/training/plans/{data.plan_id}"
    elif tool_id == "fitness.training.day.executions.list.v1":
        path = f"{base}/training/plans/{data.plan_id}/executions"
    else:
        if tool_id == "fitness.booking.create.v1":
            path = f"{base}/appointments"
        elif tool_id in {"fitness.booking.reschedule.v1", "fitness.booking.cancel.v1"}:
            leaf = "reschedule" if tool_id.endswith("reschedule.v1") else "cancel"
            path = f"{base}/appointments/{data.payload.appointment_id}/{leaf}"
        elif tool_id == "fitness.support.ticket.create.v1":
            path = f"{base}/customer-service/tickets"
        elif tool_id == "fitness.training.plan.create_draft.v1":
            path = f"{base}/training/plans/drafts"
        elif tool_id in {
            "fitness.training.plan.submit_review.v1",
            "fitness.training.plan.publish.v1",
        }:
            leaf = "submit-review" if tool_id.endswith("submit_review.v1") else "publish"
            path = f"{base}/training/plans/{data.plan_id}/{leaf}"
            body = canonical_json_bytes({})
        elif tool_id == "fitness.training.plan.review.v1":
            path = f"{base}/training/plans/{data.plan_id}/review"
        elif tool_id == "fitness.training.day.record_execution.v1":
            path = f"{base}/training/plans/{data.plan_id}/days/{data.day_id}/execution"
            body = canonical_json_bytes(
                training_execution_payload(data.day_id, arguments["payload"])
            )
        else:
            raise ValueError("No approved Gateway route")
        # Preserve the approved payload's exact JSON values and omissions. Validation
        # above checks semantics but must not reinsert defaults or normalize signed bytes.
        if body is None:
            body = canonical_json_bytes(arguments["payload"])
    # HTTP clients normalize literal dot path segments; accepting an opaque ID of
    # '.' or '..' would escape the approved endpoint even without a slash.
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise ValueError("Resource ID must not be a dot path segment")
    return PreparedGatewayCommand(
        method, path, {key: value for key, value in query.items() if value is not None}, body
    )
