"""Read-only access to the frozen Codex app-server allowlist."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hia_core.codex_protocol import (
    SUPPORTED_CODEX_VERSION,
    load_protocol_documents,
    validate_protocol_contract,
)

from .errors import ProtocolRejected


@dataclass(frozen=True)
class ProtocolPolicy:
    """Deny-by-default method sets loaded from the versioned contract."""

    version: str
    client_requests: frozenset[str]
    client_notifications: frozenset[str]
    server_requests: frozenset[str]
    server_notifications: frozenset[str]

    @classmethod
    def from_project_root(cls, project_root: Path) -> "ProtocolPolicy":
        validate_protocol_contract(project_root)
        _, allowlist = load_protocol_documents(project_root)
        allowed = allowlist["allowed"]

        def methods(category: str) -> frozenset[str]:
            return frozenset(item["method"] for item in allowed[category])

        return cls(
            version=SUPPORTED_CODEX_VERSION,
            client_requests=methods("client_requests"),
            client_notifications=methods("client_notifications"),
            server_requests=methods("server_requests"),
            server_notifications=methods("server_notifications"),
        )

    def require_client_request(self, method: str) -> None:
        if method not in self.client_requests:
            raise ProtocolRejected(method, "client request")

    def require_client_notification(self, method: str) -> None:
        if method not in self.client_notifications:
            raise ProtocolRejected(method, "client notification")

    def allows_server_request(self, method: str) -> bool:
        return method in self.server_requests

    def allows_server_notification(self, method: str) -> bool:
        return method in self.server_notifications
