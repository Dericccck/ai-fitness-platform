"""Approved remote tool catalogue, independent of model aliases and local policy."""

import hashlib
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter

from . import inputs as i
from . import models as m
from .canonical import canonical_json_bytes

CONTRACT_VERSION = "1"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_type: Any
    write: bool = False

    @cached_property
    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    @cached_property
    def output_schema(self) -> dict[str, Any]:
        data = TypeAdapter(self.output_type).json_schema()
        defs = data.pop("$defs", {})
        result = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": CONTRACT_VERSION, "type": "string"},
                "data": data,
                "error_code": {"type": "null"},
                "execution_state": {"const": "SUCCEEDED", "type": "string"},
            },
            "required": ["schema_version", "data", "error_code", "execution_state"],
        }
        if defs:
            result["$defs"] = defs
        return result

    @cached_property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "name": self.name,
                    "version": CONTRACT_VERSION,
                    "input": self.input_schema,
                    "output": self.output_schema,
                }
            )
        ).hexdigest()

    @property
    def required_scope(self) -> str:
        return "fitness:write" if self.write else "fitness:read"

    def validate_output(self, value: Any) -> Any:
        return TypeAdapter(self.output_type).validate_python(value)

    def output_json(self, value: Any) -> Any:
        return TypeAdapter(self.output_type).dump_python(
            self.validate_output(value), mode="json", by_alias=True
        )


_SPECS = (
    ToolSpec(
        "fitness.user.get_current.v1",
        "Read the authenticated user's profile",
        i.EmptyInput,
        m.GatewayUser,
    ),
    ToolSpec(
        "fitness.organization.get.v1",
        "Read an authorized organization",
        i.OrganizationToolInput,
        m.GatewayOrganization,
    ),
    ToolSpec(
        "fitness.course.list.v1",
        "List authorized courses",
        i.CourseListToolInput,
        list[m.GatewayCourse],
    ),
    ToolSpec(
        "fitness.contract.list.v1",
        "List authorized contracts",
        i.ContractListToolInput,
        list[m.GatewayContract],
    ),
    ToolSpec(
        "fitness.appointment.list.v1",
        "List authorized appointments",
        i.AppointmentListToolInput,
        list[m.GatewayAppointment],
    ),
    ToolSpec(
        "fitness.booking.availability.check.v1",
        "Read booking availability; this does not create a booking",
        i.BookingAvailabilityToolInput,
        m.GatewayBookingAvailability,
    ),
    ToolSpec(
        "fitness.training.plan.get.v1",
        "Read an authorized training plan",
        i.TrainingPlanToolInput,
        m.GatewayTrainingPlan,
    ),
    ToolSpec(
        "fitness.training.day.executions.list.v1",
        "List training day execution records",
        i.ListTrainingDayExecutionsToolInput,
        list[m.GatewayTrainingDayExecution],
    ),
    ToolSpec(
        "fitness.support.ticket.list.v1",
        "List authorized support tickets",
        i.CustomerServiceTicketListToolInput,
        list[m.GatewayCustomerServiceTicket],
    ),
    ToolSpec(
        "fitness.support.ticket.get.v1",
        "Read an authorized support ticket",
        i.CustomerServiceTicketGetToolInput,
        m.GatewayCustomerServiceTicket,
    ),
    ToolSpec(
        "fitness.operations.metric.aggregate.v1",
        "Execute one fixed metric aggregation; no comparison orchestration",
        i.OperationsMetricInput,
        m.GatewayOperationsMetric,
    ),
    ToolSpec(
        "fitness.booking.create.v1",
        "Create a booking only with a trusted confirmation credential",
        i.BookingCreateArguments,
        m.GatewayBookingCreated,
        True,
    ),
    ToolSpec(
        "fitness.booking.reschedule.v1",
        "Reschedule one confirmed booking",
        i.BookingRescheduleArguments,
        m.GatewayBookingCreated,
        True,
    ),
    ToolSpec(
        "fitness.booking.cancel.v1",
        "Cancel one confirmed booking",
        i.BookingCancelArguments,
        m.GatewayBookingCancelled,
        True,
    ),
    ToolSpec(
        "fitness.training.plan.create_draft.v1",
        "Persist a confirmed training draft",
        i.DraftCreateArguments,
        m.GatewayTrainingPlan,
        True,
    ),
    ToolSpec(
        "fitness.training.plan.submit_review.v1",
        "Submit a confirmed training plan for review",
        i.TrainingPlanToolInput,
        m.GatewayTrainingPlan,
        True,
    ),
    ToolSpec(
        "fitness.training.plan.review.v1",
        "Review a training plan; Java enforces professional authorization",
        i.ReviewArguments,
        m.GatewayTrainingPlan,
        True,
    ),
    ToolSpec(
        "fitness.training.plan.publish.v1",
        "Publish a confirmed reviewed plan",
        i.TrainingPlanToolInput,
        m.GatewayTrainingPlan,
        True,
    ),
    ToolSpec(
        "fitness.training.day.record_execution.v1",
        "Persist a confirmed training day result",
        i.ExecutionArguments,
        m.GatewayTrainingDayExecution,
        True,
    ),
    ToolSpec(
        "fitness.support.ticket.create.v1",
        "Create a confirmed support ticket",
        i.TicketCreateArguments,
        m.GatewayCustomerServiceTicket,
        True,
    ),
)
TOOL_SPECS: dict[str, ToolSpec] = {spec.name: spec for spec in _SPECS}


def get_tool_spec(name: str) -> ToolSpec:
    return TOOL_SPECS[name]


def success_envelope(spec: ToolSpec, value: Any) -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_VERSION,
        "data": spec.output_json(value),
        "error_code": None,
        "execution_state": "SUCCEEDED",
    }


def error_envelope(code: str, state: Literal["REJECTED", "NOT_SENT", "UNKNOWN"]) -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_VERSION,
        "data": None,
        "error_code": code,
        "execution_state": state,
    }
