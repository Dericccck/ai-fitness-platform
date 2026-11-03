"""Fixed business-method routing; internal authorization and reconciliation stay on HTTP."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from fitness_tool_contracts.catalog import CONTRACT_VERSION

from app.core.config import Settings
from app.infrastructure.gateway_client import GatewayClient, GatewayRequestContext
from app.infrastructure.mcp_client import McpBusinessToolsClient

# This is an approved mapping, never an import path or method name supplied by a model.
BUSINESS_METHOD_TO_TOOL: dict[str, str] = {
    "get_current_user": "fitness.user.get_current.v1",
    "get_organization": "fitness.organization.get.v1",
    "list_courses": "fitness.course.list.v1",
    "list_contracts": "fitness.contract.list.v1",
    "list_appointments": "fitness.appointment.list.v1",
    "check_booking_availability": "fitness.booking.availability.check.v1",
    "get_training_plan": "fitness.training.plan.get.v1",
    "list_training_day_executions": "fitness.training.day.executions.list.v1",
    "list_customer_service_tickets": "fitness.support.ticket.list.v1",
    "get_customer_service_ticket": "fitness.support.ticket.get.v1",
    "query_operations_metric": "fitness.operations.metric.aggregate.v1",
    "create_booking": "fitness.booking.create.v1",
    "reschedule_booking": "fitness.booking.reschedule.v1",
    "cancel_booking": "fitness.booking.cancel.v1",
    "create_training_draft": "fitness.training.plan.create_draft.v1",
    "submit_training_review": "fitness.training.plan.submit_review.v1",
    "review_training_plan": "fitness.training.plan.review.v1",
    "publish_training_plan": "fitness.training.plan.publish.v1",
    "record_training_day_execution": "fitness.training.day.record_execution.v1",
    "create_customer_service_ticket": "fitness.support.ticket.create.v1",
}
LOCAL_TOOLS = frozenset(
    {
        "fitness.training.plan.generate_draft.v1",
        "fitness.memory.list.v1",
        "fitness.memory.save.v1",
        "fitness.memory.revoke.v1",
    }
)


def remote_tool_id(tool_id: str) -> str:
    return (
        "fitness.operations.metric.aggregate.v1"
        if tool_id == "fitness.operations.metric.query.v1"
        else tool_id
    )


class RoutedBusinessToolsClient:
    """Freeze each confirmed action's transport; never fail a write over to HTTP."""

    def __init__(
        self,
        settings: Settings,
        http: GatewayClient,
        mcp: McpBusinessToolsClient | None,
    ) -> None:
        self.settings, self.http, self.mcp = settings, http, mcp
        self._methods: dict[str, Callable[..., Any]] = {}

    def route_for_tool(self, tool_id: str) -> tuple[str, str]:
        if tool_id in LOCAL_TOOLS:
            return "local", "v1"
        remote = remote_tool_id(tool_id)
        if self.settings.mcp_enabled and (
            not self.settings.mcp_tool_ids or remote in self.settings.mcp_tool_ids
        ):
            return "mcp", CONTRACT_VERSION
        return "http", "v1"

    def is_available(self, tool_id: str) -> bool:
        transport, _ = self.route_for_tool(tool_id)
        return transport != "mcp" or (
            self.mcp is not None and self.mcp.is_available(remote_tool_id(tool_id))
        )

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name not in BUSINESS_METHOD_TO_TOOL:
            raise AttributeError(name)
        if name not in self._methods:
            method = getattr(GatewayClient, name)
            signature = inspect.signature(method)
            tool_id = BUSINESS_METHOD_TO_TOOL[name]

            async def invoke(*args: Any, **kwargs: Any) -> Any:
                bound = signature.bind(self.http, *args, **kwargs)
                bound.apply_defaults()
                context: GatewayRequestContext = bound.arguments["context"]
                selected, version = self.route_for_tool(tool_id)
                transport = context.transport or selected
                if transport == "http":
                    return await getattr(self.http, name)(*args, **kwargs)
                if transport != "mcp" or self.mcp is None:
                    from app.infrastructure.gateway_client import GatewayConfigurationError

                    raise GatewayConfigurationError("业务工具传输未配置")
                arguments = {
                    key: value
                    for key, value in bound.arguments.items()
                    if key not in {"self", "context"}
                }
                # A confirmation's persisted version takes precedence over a rollout switch.
                call_context = (
                    context
                    if context.transport
                    else replace(
                        context,
                        transport="mcp",
                        contract_version=version,
                    )
                )
                return await self.mcp.call_tool(tool_id, arguments, call_context)

            self._methods[name] = invoke
        return self._methods[name]
