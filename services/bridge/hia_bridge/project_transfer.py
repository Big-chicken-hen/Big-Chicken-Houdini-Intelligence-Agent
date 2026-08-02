"""Transactional migration for the five native project-role Threads.

The module is intentionally not wired into the project runner.  A caller must
explicitly enable it, persist the returned project identity during ``commit``,
and decide when a completed context-compaction cycle warrants a migration.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Protocol

from .project_contracts import ProjectState, Role, RoleThread
from .project_permissions import permission_profile, validate_role_permissions


class AppServerClient(Protocol):
    def request(self, method: str, params: Mapping[str, Any]) -> Any: ...


class ProjectTransferError(RuntimeError):
    """A migration was rejected without changing the old project identity."""


@dataclass(frozen=True)
class PreparedProjectTransfer:
    project_id: str
    role: Role
    old_thread_id: str
    new_thread_id: str
    thread_source: str
    model: str | None
    effort: str | None
    service_tier: str | None
    selected_backend: str

    def restart_descriptor(self) -> dict[str, Any]:
        """Return the minimum non-chat descriptor needed to resume a commit."""

        return {
            "schema": "hia-project-thread-transfer/1",
            "project_id": self.project_id,
            "role": self.role.value,
            "old_thread_id": self.old_thread_id,
            "new_thread_id": self.new_thread_id,
            "thread_source": self.thread_source,
            "model": self.model,
            "effort": self.effort,
            "service_tier": self.service_tier,
            "selected_backend": self.selected_backend,
        }

    @classmethod
    def from_restart_descriptor(
        cls, value: Mapping[str, Any]
    ) -> "PreparedProjectTransfer":
        if value.get("schema") != "hia-project-thread-transfer/1":
            raise ProjectTransferError("unsupported project transfer descriptor")

        def required(name: str) -> str:
            item = value.get(name)
            if not isinstance(item, str) or not item.strip():
                raise ProjectTransferError(f"transfer descriptor {name} is invalid")
            return item

        def optional(name: str) -> str | None:
            item = value.get(name)
            if item is None:
                return None
            if not isinstance(item, str):
                raise ProjectTransferError(f"transfer descriptor {name} is invalid")
            return item

        try:
            role = Role(required("role"))
        except ValueError as exc:
            raise ProjectTransferError("transfer descriptor role is invalid") from exc
        return cls(
            project_id=required("project_id"),
            role=role,
            old_thread_id=required("old_thread_id"),
            new_thread_id=required("new_thread_id"),
            thread_source=required("thread_source"),
            model=optional("model"),
            effort=optional("effort"),
            service_tier=optional("service_tier"),
            selected_backend=required("selected_backend"),
        )


@dataclass(frozen=True)
class TransferResult:
    state: ProjectState
    old_thread_deleted: bool
    cleanup_error: str | None = None


class ProjectThreadTransfer:
    """Prepare and commit one exact native project-role Thread replacement."""

    def __init__(
        self,
        client: AppServerClient,
        *,
        enabled: bool = False,
        selected_backend: str = "hia_mcp_v2",
    ) -> None:
        self._client = client
        self._enabled = enabled
        self._selected_backend = selected_backend
        permission_profile(Role.EXECUTION, selected_backend)

    def prepare(self, state: ProjectState, role: Role) -> PreparedProjectTransfer:
        """Fork and verify a replacement while preserving the old identity."""

        self._require_enabled()
        binding = self._binding(state, role)
        source = self._expected_source(state.project_id, role)
        self._validate_goal_owner_state(state)

        old_read = self._read_thread(binding.thread_id)
        self._validate_project_thread(old_read, binding.thread_id, source)
        old_context = self._context(old_read)

        profile = permission_profile(role, self._selected_backend)
        params: dict[str, Any] = {
            "threadId": binding.thread_id,
            "approvalPolicy": profile.approval_policy,
            "approvalsReviewer": "user",
            "sandbox": profile.sandbox,
            "ephemeral": False,
            "threadSource": source,
            "config": dict(profile.config),
        }
        if binding.model is not None:
            params["model"] = binding.model
        if binding.service_tier is not None:
            params["serviceTier"] = binding.service_tier

        fork_result = self._client.request("thread/fork", params)
        new_id = self._response_thread_id(fork_result, "thread/fork")
        if new_id == binding.thread_id:
            raise ProjectTransferError("thread/fork returned the old Thread identity")

        try:
            self._validate_fork_profile(role, binding, fork_result)
            new_read = self._read_thread(new_id)
            self._validate_project_thread(new_read, new_id, source)
            new_thread = self._thread(new_read)
            forked_from = new_thread.get("forkedFromId")
            if forked_from is not None and forked_from != binding.thread_id:
                raise ProjectTransferError("forked Thread lineage does not match source")
            if self._context(new_read) != old_context:
                raise ProjectTransferError("forked Thread context does not match source")
            self._validate_goal_relationship(state, role, binding.thread_id, new_id)
        except Exception as exc:
            cleanup = self._delete_precisely(new_id)
            suffix = f"; replacement cleanup failed: {cleanup}" if cleanup else ""
            if isinstance(exc, ProjectTransferError):
                raise ProjectTransferError(f"{exc}{suffix}") from exc
            raise ProjectTransferError(f"fork verification failed: {exc}{suffix}") from exc

        return PreparedProjectTransfer(
            project_id=state.project_id,
            role=role,
            old_thread_id=binding.thread_id,
            new_thread_id=new_id,
            thread_source=source,
            model=binding.model,
            effort=binding.effort,
            service_tier=binding.service_tier,
            selected_backend=self._selected_backend,
        )

    def restore(
        self, state: ProjectState, descriptor: Mapping[str, Any]
    ) -> PreparedProjectTransfer:
        """Re-verify an already prepared replacement after Bridge restart."""

        self._require_enabled()
        prepared = PreparedProjectTransfer.from_restart_descriptor(descriptor)
        self._validate_prepared_against_state(state, prepared)
        old_read = self._read_thread(prepared.old_thread_id)
        new_read = self._read_thread(prepared.new_thread_id)
        self._validate_project_thread(
            old_read, prepared.old_thread_id, prepared.thread_source
        )
        self._validate_project_thread(
            new_read, prepared.new_thread_id, prepared.thread_source
        )
        if self._context(new_read) != self._context(old_read):
            raise ProjectTransferError("restored fork context no longer matches source")
        self._validate_goal_relationship(
            state,
            prepared.role,
            prepared.old_thread_id,
            prepared.new_thread_id,
        )
        return prepared

    def commit(
        self,
        state: ProjectState,
        prepared: PreparedProjectTransfer,
        persist: Callable[[ProjectState], None],
    ) -> TransferResult:
        """Persist the new identity, then precisely delete only the old Thread."""

        self._require_enabled()
        self._validate_prepared_against_state(state, prepared)
        old_read = self._read_thread(prepared.old_thread_id)
        new_read = self._read_thread(prepared.new_thread_id)
        self._validate_project_thread(
            old_read, prepared.old_thread_id, prepared.thread_source
        )
        self._validate_project_thread(
            new_read, prepared.new_thread_id, prepared.thread_source
        )
        if self._context(new_read) != self._context(old_read):
            raise ProjectTransferError("replacement context changed before commit")
        self._validate_goal_relationship(
            state,
            prepared.role,
            prepared.old_thread_id,
            prepared.new_thread_id,
        )
        roles = dict(state.roles)
        roles[prepared.role] = RoleThread(
            role=prepared.role,
            thread_id=prepared.new_thread_id,
            model=prepared.model,
            effort=prepared.effort,
            service_tier=prepared.service_tier,
        )
        changes: dict[str, Any] = {
            "roles": roles,
            "revision": state.revision + 1,
        }
        if prepared.role is Role.SUPERVISOR:
            changes["goal_thread_id"] = prepared.new_thread_id
        updated = replace(state, **changes)
        try:
            persist(updated)
        except Exception as exc:
            cleanup = self._delete_precisely(prepared.new_thread_id)
            suffix = f"; replacement cleanup failed: {cleanup}" if cleanup else ""
            raise ProjectTransferError(
                f"project identity commit failed; old Thread retained: {exc}{suffix}"
            ) from exc

        cleanup = self._delete_precisely(prepared.old_thread_id)
        return TransferResult(
            state=updated,
            old_thread_deleted=cleanup is None,
            cleanup_error=cleanup,
        )

    def rollback(self, prepared: PreparedProjectTransfer) -> str | None:
        """Discard only the uncommitted replacement; never touch the old Thread."""

        self._require_enabled()
        return self._delete_precisely(prepared.new_thread_id)

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise ProjectTransferError("project Thread migration is disabled")

    @staticmethod
    def _expected_source(project_id: str, role: Role) -> str:
        return f"hia-project/{project_id}/{role.value}"

    @staticmethod
    def _binding(state: ProjectState, role: Role) -> RoleThread:
        if not isinstance(role, Role):
            raise ProjectTransferError("only a native project role may be migrated")
        binding = state.roles.get(role)
        if binding is None:
            raise ProjectTransferError("project role Thread does not exist")
        turn = state.turns.get(role)
        if turn is not None and turn.active:
            raise ProjectTransferError("an active role Turn cannot be migrated")
        return binding

    @staticmethod
    def _response_thread_id(result: Any, operation: str) -> str:
        thread = result.get("thread") if isinstance(result, Mapping) else None
        thread_id = thread.get("id") if isinstance(thread, Mapping) else None
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ProjectTransferError(f"{operation} returned no Thread identity")
        return thread_id

    def _read_thread(self, thread_id: str) -> Mapping[str, Any]:
        result = self._client.request(
            "thread/read", {"threadId": thread_id, "includeTurns": True}
        )
        if not isinstance(result, Mapping):
            raise ProjectTransferError("thread/read returned an invalid response")
        return result

    @staticmethod
    def _thread(result: Mapping[str, Any]) -> Mapping[str, Any]:
        thread = result.get("thread")
        if not isinstance(thread, Mapping):
            raise ProjectTransferError("thread descriptor is missing")
        return thread

    def _validate_project_thread(
        self, result: Mapping[str, Any], thread_id: str, expected_source: str
    ) -> None:
        thread = self._thread(result)
        if thread.get("id") != thread_id:
            raise ProjectTransferError("thread/read identity mismatch")
        source = thread.get("threadSource", thread.get("source"))
        if source != expected_source:
            raise ProjectTransferError(
                "Thread is not the exact native project-role source"
            )
        status = thread.get("status")
        status_type = status.get("type") if isinstance(status, Mapping) else None
        if status_type not in {"idle", "notLoaded"}:
            raise ProjectTransferError("only an idle project-role Thread may migrate")
        session_source = thread.get("source")
        if (
            thread.get("nativeSubagent") is True
            or thread.get("parentThreadId")
            or (
                isinstance(session_source, Mapping)
                and "subAgent" in session_source
            )
        ):
            raise ProjectTransferError("native subagent Threads cannot be migrated")

    def _context(self, result: Mapping[str, Any]) -> Any:
        thread = self._thread(result)
        turns = thread.get("turns")
        if not isinstance(turns, list):
            raise ProjectTransferError("full Thread context was not returned")
        return turns

    def _validate_fork_profile(
        self, role: Role, binding: RoleThread, result: Any
    ) -> None:
        if not isinstance(result, Mapping):
            raise ProjectTransferError("thread/fork returned an invalid descriptor")
        config = result.get("config")
        if not isinstance(config, Mapping):
            raise ProjectTransferError(
                "forked Thread effective tool inventory was not observable"
            )
        observable_profile = {
            "sandbox": result.get("sandbox"),
            "approvalPolicy": result.get("approvalPolicy"),
            "config": config,
        }
        validate_role_permissions(role, observable_profile, self._selected_backend)
        if binding.model is not None and result.get("model") != binding.model:
            raise ProjectTransferError("forked Thread model changed")
        if binding.effort is not None and result.get("reasoningEffort") != binding.effort:
            raise ProjectTransferError("forked Thread reasoning effort changed")
        if (
            binding.service_tier is not None
            and result.get("serviceTier") != binding.service_tier
        ):
            raise ProjectTransferError("forked Thread service tier changed")

    def _validate_goal_owner_state(self, state: ProjectState) -> None:
        supervisor = state.roles.get(Role.SUPERVISOR)
        if supervisor is None or supervisor.thread_id != state.goal_thread_id:
            raise ProjectTransferError("native Goal owner relationship is invalid")

    def _goal(self, thread_id: str) -> Any:
        result = self._client.request("thread/goal/get", {"threadId": thread_id})
        if not isinstance(result, Mapping):
            raise ProjectTransferError("thread/goal/get returned an invalid response")
        return result.get("goal")

    def _validate_goal_relationship(
        self,
        state: ProjectState,
        role: Role,
        old_thread_id: str,
        new_thread_id: str,
    ) -> None:
        self._validate_goal_owner_state(state)
        old_goal = self._goal(old_thread_id)
        new_goal = self._goal(new_thread_id)
        if role is Role.SUPERVISOR:
            if not isinstance(old_goal, Mapping) or new_goal != old_goal:
                raise ProjectTransferError("forked Supervisor did not preserve native Goal")
        else:
            supervisor_goal = self._goal(state.goal_thread_id)
            if not isinstance(supervisor_goal, Mapping):
                raise ProjectTransferError("Supervisor no longer owns the native Goal")
            if old_goal is not None or new_goal is not None:
                raise ProjectTransferError("worker role Thread must not own a native Goal")

    def _validate_prepared_against_state(
        self, state: ProjectState, prepared: PreparedProjectTransfer
    ) -> None:
        if prepared.selected_backend != self._selected_backend:
            raise ProjectTransferError("transfer backend changed")
        if prepared.project_id != state.project_id:
            raise ProjectTransferError("transfer belongs to a different project")
        binding = self._binding(state, prepared.role)
        expected_source = self._expected_source(state.project_id, prepared.role)
        if prepared.old_thread_id != binding.thread_id:
            raise ProjectTransferError("old project identity changed before commit")
        if prepared.new_thread_id == prepared.old_thread_id:
            raise ProjectTransferError("replacement Thread identity is invalid")
        if prepared.thread_source != expected_source:
            raise ProjectTransferError("transfer role/project source changed")
        if (
            prepared.model,
            prepared.effort,
            prepared.service_tier,
        ) != (binding.model, binding.effort, binding.service_tier):
            raise ProjectTransferError("transfer model profile changed")
        self._validate_goal_owner_state(state)

    def _delete_precisely(self, thread_id: str) -> str | None:
        try:
            self._client.request("thread/delete", {"threadId": thread_id})
        except Exception as exc:
            return str(exc)
        return None
