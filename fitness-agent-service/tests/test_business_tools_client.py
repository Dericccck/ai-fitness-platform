from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fitness_tool_contracts.catalog import TOOL_SPECS

from app.agent.fitness_tools import build_fitness_tool_registry
from app.core.config import Settings
from app.infrastructure.business_tools_client import (
    BUSINESS_METHOD_TO_TOOL,
    RoutedBusinessToolsClient,
)
from app.infrastructure.gateway_client import GatewayRequestContext


def clients(**overrides):
    settings = Settings(
        _env_file=None,
        mcp_enabled=True,
        mcp_url="https://mcp.internal/mcp",
        mcp_service_token="test-only-token",
        **overrides,
    )
    http = SimpleNamespace(
        get_current_user=AsyncMock(return_value="http"),
        create_booking=AsyncMock(return_value="http-write"),
    )
    mcp = SimpleNamespace(call_tool=AsyncMock(return_value="mcp"), is_available=lambda _: True)
    return settings, http, mcp, RoutedBusinessToolsClient(settings, http, mcp)


async def test_persisted_http_confirmation_survives_mcp_rollout():
    _, http, mcp, routed = clients()
    context = GatewayRequestContext(
        "signed",
        "original-operation",
        confirmation_token="approved",
        transport="http",
        contract_version="v1",
    )
    assert await routed.create_booking(context, {"organizationId": "org-1"}) == "http-write"
    http.create_booking.assert_awaited_once()
    mcp.call_tool.assert_not_called()


async def test_persisted_mcp_confirmation_survives_http_rollback():
    _, http, mcp, routed = clients()
    routed.settings.mcp_enabled = False
    context = GatewayRequestContext(
        "signed",
        "original-operation",
        confirmation_token="approved",
        transport="mcp",
        contract_version="1",
    )
    assert await routed.create_booking(context, {"organizationId": "org-1"}) == "mcp"
    assert mcp.call_tool.call_args.args[0] == "fitness.booking.create.v1"
    assert mcp.call_tool.call_args.args[2].request_id == "original-operation"
    http.create_booking.assert_not_called()


async def test_mcp_failure_never_falls_back_to_http():
    _, http, mcp, routed = clients()
    mcp.call_tool.side_effect = RuntimeError("response lost")
    with pytest.raises(RuntimeError):
        await routed.create_booking(GatewayRequestContext("signed"), {})
    http.create_booking.assert_not_called()


async def test_read_defaults_use_mcp_and_approved_mapping_only():
    _, _, mcp, routed = clients()
    assert await routed.get_current_user(GatewayRequestContext("signed")) == "mcp"
    assert mcp.call_tool.call_args.args[2].contract_version == "1"
    assert set(BUSINESS_METHOD_TO_TOOL.values()) == set(TOOL_SPECS)
    with pytest.raises(AttributeError):
        _unapproved = routed.authorize_student_training_context


def test_offline_catalog_and_degraded_local_capabilities():
    _, http, mcp, routed = clients()
    mcp.is_available = lambda _: False
    registry = build_fitness_tool_registry(http, availability_checker=routed.is_available)
    assert {spec["name"] for spec in registry.public_specs()} == {
        "fitness.training.plan.generate_draft.v1",
        "fitness.memory.list.v1",
        "fitness.memory.save.v1",
        "fitness.memory.revoke.v1",
    }
    assert len(build_fitness_tool_registry(http).public_specs()) == 24


def test_tool_rollout_mapping_and_local_tools():
    _, _, _, routed = clients(mcp_tool_ids=["fitness.operations.metric.aggregate.v1"])
    assert routed.route_for_tool("fitness.operations.metric.query.v1") == ("mcp", "1")
    assert routed.route_for_tool("fitness.user.get_current.v1") == ("http", "v1")
    assert routed.route_for_tool("fitness.memory.save.v1") == ("local", "v1")
