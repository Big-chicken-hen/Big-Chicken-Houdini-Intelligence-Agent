"""HIA MCP V2: Codex's Houdini perception and execution layer."""

from .adapter import HiaMcpAdapter
from .tools import CAPABILITY_MATRIX, TOOL_NAMES, TOOL_SPECS
from .transport import LoopbackTransport

__all__ = [
    "CAPABILITY_MATRIX",
    "HiaMcpAdapter",
    "LoopbackTransport",
    "TOOL_NAMES",
    "TOOL_SPECS",
]

__version__ = "0.1.0"
