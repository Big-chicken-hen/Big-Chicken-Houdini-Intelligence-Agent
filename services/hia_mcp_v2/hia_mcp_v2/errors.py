"""Stable errors shared by the HIA MCP V2 protocol layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class HiaMcpError(Exception):
    code: str
    message: str
    details: Mapping[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class TransportError(HiaMcpError):
    """The authenticated loopback transport could not complete a call."""


class InputError(HiaMcpError):
    """A tool call does not match its published input contract."""
