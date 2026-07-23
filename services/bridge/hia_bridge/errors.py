"""Structured bridge errors suitable for HTTP JSON responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BridgeError(Exception):
    """A safe, JSON-serializable error with an HTTP status."""

    code: str
    message: str
    http_status: int = 400
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            error["details"] = self.details
        return {"ok": False, "structured_error": error}


class ProtocolRejected(BridgeError):
    """Raised before a method outside the frozen allowlist is sent."""

    def __init__(self, method: str, direction: str) -> None:
        super().__init__(
            code="METHOD_NOT_ALLOWLISTED",
            message=f"Method is not allowlisted for {direction}: {method}",
            http_status=400,
            details={"method": method, "direction": direction},
        )


class CodexRPCError(BridgeError):
    """An error response returned by codex app-server."""

    def __init__(self, method: str, error: Any) -> None:
        message = "Codex app-server returned an error"
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            message = error["message"]
        super().__init__(
            code="CODEX_RPC_ERROR",
            message=message,
            http_status=502,
            details={"method": method, "rpc_error": error},
        )
