"""Deny-by-default, fake-only Houdini MCP protocol adapter."""

from .adapter import (
    CancellationHandoff,
    FROZEN_TOOL_NAMES,
    FROZEN_TOOL_PERMISSIONS,
    MCP_PROTOCOL_VERSION,
    BridgeTransport,
    BridgeTransportError,
    HoudiniMCPAdapter,
)

__all__ = [
    "CancellationHandoff",
    "FROZEN_TOOL_NAMES",
    "FROZEN_TOOL_PERMISSIONS",
    "MCP_PROTOCOL_VERSION",
    "BridgeTransport",
    "BridgeTransportError",
    "HoudiniMCPAdapter",
]

__version__ = "0.1.0"
