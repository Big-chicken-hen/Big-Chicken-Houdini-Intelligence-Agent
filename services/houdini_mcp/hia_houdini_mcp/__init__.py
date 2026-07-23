"""Deny-by-default Houdini MCP protocol adapter and loopback transport."""

from .adapter import (
    CancellationHandoff,
    FROZEN_TOOL_NAMES,
    FROZEN_TOOL_PERMISSIONS,
    MCP_PROTOCOL_VERSION,
    BridgeTransport,
    BridgeTransportError,
    HoudiniMCPAdapter,
)
from .bridge_transport import (
    BRIDGE_TOKEN_ENV,
    BRIDGE_URL_ENV,
    LoopbackBridgeTransport,
)

__all__ = [
    "CancellationHandoff",
    "FROZEN_TOOL_NAMES",
    "FROZEN_TOOL_PERMISSIONS",
    "MCP_PROTOCOL_VERSION",
    "BridgeTransport",
    "BridgeTransportError",
    "HoudiniMCPAdapter",
    "BRIDGE_TOKEN_ENV",
    "BRIDGE_URL_ENV",
    "LoopbackBridgeTransport",
]

__version__ = "0.1.0"
