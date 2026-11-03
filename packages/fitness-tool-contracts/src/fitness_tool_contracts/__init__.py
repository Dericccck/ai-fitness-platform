"""Lightweight versioned fitness Tool Gateway contracts."""

from .canonical import canonical_json_bytes, gateway_confirmation_body, training_execution_payload
from .catalog import CONTRACT_VERSION, TOOL_SPECS, ToolSpec, get_tool_spec
from .commands import PreparedGatewayCommand, build_gateway_command

__all__ = [
    "CONTRACT_VERSION",
    "TOOL_SPECS",
    "PreparedGatewayCommand",
    "ToolSpec",
    "build_gateway_command",
    "canonical_json_bytes",
    "gateway_confirmation_body",
    "get_tool_spec",
    "training_execution_payload",
]
