"""Native project-role Thread identity parsing.

Project membership is carried by the app-server ``threadSource`` field.  This
module deliberately does not read the project registry, persist state, or
guess membership from a title, cwd, or Thread id.
"""

from __future__ import annotations

from dataclasses import dataclass

from .project_contracts import Role


PROJECT_THREAD_SOURCE_PREFIX = "hia-project/"


@dataclass(frozen=True)
class ProjectThreadIdentity:
    project_id: str
    role: Role


def parse_project_thread_source(value: object) -> ProjectThreadIdentity | None:
    if not isinstance(value, str) or not value.startswith(PROJECT_THREAD_SOURCE_PREFIX):
        return None
    parts = value.split("/")
    if len(parts) != 3 or parts[0] != "hia-project" or not parts[1]:
        return None
    try:
        role = Role(parts[2])
    except ValueError:
        return None
    return ProjectThreadIdentity(project_id=parts[1], role=role)


__all__ = [
    "PROJECT_THREAD_SOURCE_PREFIX",
    "ProjectThreadIdentity",
    "parse_project_thread_source",
]
