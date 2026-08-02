"""Non-bypassable app-server permission profiles for project roles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .project_contracts import READ_ONLY_ROLES, Role


HIA_SERVER_KEYS = (
    "mcp_servers.hia_mcp_v2.enabled",
    "mcp_servers.houdini_intelligence.enabled",
)


@dataclass(frozen=True)
class RolePermissionProfile:
    role: Role
    sandbox: str
    approval_policy: str
    config: Mapping[str, Any]

    @property
    def scene_write(self) -> bool:
        return self.role is Role.EXECUTION


def permission_profile(
    role: Role, selected_backend: str = "hia_mcp_v2"
) -> RolePermissionProfile:
    if selected_backend not in {"hia_mcp_v2", "houdini_intelligence"}:
        raise ValueError("selected_backend is invalid")
    read_only = role in READ_ONLY_ROLES
    config: dict[str, Any] = {
        key: (not read_only and selected_backend in key) for key in HIA_SERVER_KEYS
    }
    config["multi_agent_mode"] = (
        "explicitRequestOnly" if role is Role.EXECUTION else "proactive"
    )
    return RolePermissionProfile(
        role=role,
        sandbox="read-only" if read_only else "workspace-write",
        approval_policy="never" if read_only else "on-request",
        config=config,
    )


def validate_role_permissions(
    role: Role,
    descriptor: Mapping[str, Any],
    selected_backend: str = "hia_mcp_v2",
) -> None:
    """Reject permission drift after start, resume, fork, or restart."""

    expected = permission_profile(role, selected_backend)
    if descriptor.get("sandbox") != expected.sandbox:
        raise ValueError(f"{role.value} sandbox permission drift")
    if descriptor.get("approvalPolicy") != expected.approval_policy:
        raise ValueError(f"{role.value} approval policy drift")
    config = descriptor.get("config")
    if not isinstance(config, Mapping):
        raise ValueError(f"{role.value} config is missing")
    for key, value in expected.config.items():
        if config.get(key) != value:
            raise ValueError(f"{role.value} config permission drift: {key}")


def require_complete_project_roles(roles: Mapping[Role, Any]) -> None:
    expected = set(Role)
    actual = set(roles)
    if actual != expected:
        missing = sorted(role.value for role in expected - actual)
        extra = sorted(str(role) for role in actual - expected)
        raise ValueError(
            f"scene execution requires exactly five roles; missing={missing}, extra={extra}"
        )
