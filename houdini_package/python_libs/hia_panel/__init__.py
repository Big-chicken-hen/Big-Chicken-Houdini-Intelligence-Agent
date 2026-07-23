"""Big-Chicken Houdini Intelligence Agent Python Panel package.

The Qt panel is imported lazily so the state and response helpers remain
testable with the project-standard Python, where PySide6 is intentionally not
installed.
"""

from __future__ import annotations

from typing import Any

from .network_response import format_bridge_error, normalize_bridge_response
from .turn_state import ControlAvailability, PanelTurnState, TurnPhase, TurnStateToken

__all__ = [
    "ControlAvailability",
    "HoudiniIntelligencePanel",
    "PanelTurnState",
    "TurnPhase",
    "TurnStateToken",
    "format_bridge_error",
    "normalize_bridge_response",
]


def __getattr__(name: str) -> Any:
    """Preserve the panel export without importing PySide6 eagerly."""

    if name == "HoudiniIntelligencePanel":
        from .panel import HoudiniIntelligencePanel

        return HoudiniIntelligencePanel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
