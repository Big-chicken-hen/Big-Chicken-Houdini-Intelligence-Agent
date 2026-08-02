"""Bridge process entry point."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
import hmac
import json
import os
import re
import secrets
import signal
import sys
import threading
from pathlib import Path
from typing import Callable, Mapping, Sequence

from hia_core.houdini_contract import B2_SCHEMA_VERSION, SchemaRegistry
from hia_core.path_policy import PROJECT_ROOT, PathPolicyError, validate_project_subpath

from .codex_stdio import CodexStdioClient
from .errors import BridgeError
from .events import EventBuffer
from .http_server import BridgeApplication, LoopbackHTTPServer
from .ordinary_transfer import OrdinaryThreadTransfer
from .protocol import ProtocolPolicy
from .project_contracts import ProjectState, ProjectStatus, Role
from .project_lifecycle import LifecycleEvent, ProjectEvent
from .project_registry import ProjectRecord, ProjectRegistry
from .project_app_server import ProjectEffectClient
from .project_artifacts import ProjectArtifactStore
from .project_effects import ProjectEffectExecutor
from .project_runner import ProjectRunner
from .project_service import ProjectTeamService, ProjectTeamSettings
from .project_thread_factory import ProjectThreadFactory
from .project_transfer import ProjectThreadTransfer
from .project_workflow import ProjectWorkflowHost
from .scene_queue import B2_READ_ONLY_PROFILE, SceneQueue
from .session import BridgeSession


PINNED_CODEX_RELATIVE_PATH = Path(
    ".runtime/toolchains/codex/0.144.3/codex.exe"
)
CODEX_HOME_RELATIVE_PATH = Path(".runtime/codex-home")
CACHE_RELATIVE_PATH = Path(".runtime/cache")
FOCUS_STATE_RELATIVE_PATH = Path(".runtime/bridge/focus-mode.json")
PROJECT_REGISTRY_RELATIVE_PATH = Path(
    ".runtime/bridge/project-team-registry.json"
)
LEGACY_PROJECT_REGISTRY_RELATIVE_PATH = Path(
    ".runtime/bridge/project-threads.json"
)
PROJECT_SETTINGS_RELATIVE_PATH = Path(
    ".runtime/bridge/project-team-settings.json"
)
PROJECT_ARTIFACTS_RELATIVE_PATH = Path(
    ".runtime/bridge/project-team-artifacts.json"
)
ORDINARY_TRANSFER_RELATIVE_PATH = Path(
    ".runtime/bridge/ordinary-thread-transfers.json"
)
HIA_MCP_V2_SERVICE_RELATIVE_PATH = Path("services/hia_mcp_v2")
HIA_MCP_V2_RUNTIME_RELATIVE_PATH = Path(".runtime/hia-mcp-v2")
HIA_MCP_V2_EXECUTOR_RELATIVE_PATH = Path(
    "houdini_package/python_libs/hia_mcp_runtime/executor.py"
)
FXHOUDINI_MCP_PYTHON_RELATIVE_PATH = Path(
    ".runtime/fxhoudinimcp/1.3.0/venv/Scripts/python.exe"
)
FXHOUDINI_MCP_SOURCE_RELATIVE_PATH = Path(
    ".runtime/fxhoudinimcp/1.3.0/source/python"
)
HIA_MCP_V2_BACKEND = "hia_v2"
FXHOUDINI_MCP_BACKEND = "fxhoudini"
HIA_MCP_V2_SERVER_ID = "hia_mcp_v2"
FXHOUDINI_MCP_SERVER_ID = "houdini_intelligence"
FXHOUDINI_MCP_CHILD_ENVIRONMENT = (
    "PATH",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "TEMP",
    "TMP",
    "HIA_PROJECT_ROOT",
    "HOUDINI_HOST",
    "HOUDINI_PORT",
    "FXHOUDINIMCP_TOKEN",
)
HIA_MCP_V2_HOST = "127.0.0.1"
HIA_MCP_V2_EXECUTE_ROUTE = "/hia-mcp-v2/v1/execute"
HIA_MCP_V2_HEALTH_ROUTE = "/hia-mcp-v2/v1/health"
HIA_MCP_V2_CHILD_ENVIRONMENT = (
    "PATH",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "TEMP",
    "TMP",
    "HIA_PROJECT_ROOT",
    "HIA_CACHE_DIR",
    "HIA_RENDER_OUTPUT_DIR",
    "HIA_EXPECTED_PYTHON_EXE",
    "HIA_MCP_V2_HOST",
    "HIA_MCP_V2_PORT",
    "HIA_MCP_V2_TOKEN",
    "HIA_MCP_V2_ROUTE",
    "HIA_MCP_V2_RUNTIME_DIR",
    "HIA_MCP_V2_EXECUTOR_PATH",
    "HIA_LAUNCHER_SESSION_ID",
)
_HIA_CHATGPT_HTTP_PROVIDER_ID = "hia_chatgpt_http"
_HIA_CHATGPT_HTTP_PROVIDER_NAME = "HIA ChatGPT HTTP"
_HIA_CHATGPT_HTTP_BASE_URL = "https://chatgpt.com/backend-api/codex"
_HIA_CHATGPT_HTTP_WIRE_API = "responses"
_LAUNCH_SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_LAUNCHER_SESSION_PATTERN = re.compile(r"^[0-9A-Fa-f]{32}$")
_BRIDGE_URL_PATTERN = re.compile(
    r"^http://127\.0\.0\.1:([1-9][0-9]{0,4})$"
)
_CODEX_CHILD_ENVIRONMENT_ALLOWLIST = (
    "ALL_PROXY",
    "COMSPEC",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_ARCHITEW6432",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TZ",
    "WINDIR",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Big-Chicken Houdini Intelligence Agent local Bridge")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument(
        "--codex-exe",
        default=str(PROJECT_ROOT / PINNED_CODEX_RELATIVE_PATH),
    )
    parser.add_argument(
        "--codex-home",
        default=str(PROJECT_ROOT / CODEX_HOME_RELATIVE_PATH),
    )
    parser.add_argument(
        "--mcp-backend",
        choices=(HIA_MCP_V2_BACKEND, FXHOUDINI_MCP_BACKEND),
        default=HIA_MCP_V2_BACKEND,
    )
    return parser


def _same_windows_path(left: Path, right: Path) -> bool:
    return str(left).replace("/", "\\").rstrip("\\").casefold() == str(
        right
    ).replace("/", "\\").rstrip("\\").casefold()


def _cache_directory(project_root: Path, configured: str | None) -> Path:
    """Resolve the single project-local cache root without a drive assumption."""

    resolved_project_root = project_root.resolve()
    expected = (resolved_project_root / CACHE_RELATIVE_PATH).resolve()
    try:
        common = Path(os.path.commonpath((str(resolved_project_root), str(expected))))
    except ValueError as exc:
        raise BridgeError("INVALID_CACHE_DIR", "HIA_CACHE_DIR escaped the project") from exc
    if not _same_windows_path(common, resolved_project_root):
        raise BridgeError("INVALID_CACHE_DIR", "HIA_CACHE_DIR escaped the project")
    if configured is None:
        return expected
    if not isinstance(configured, str) or not configured.strip() or "\x00" in configured:
        raise BridgeError("INVALID_CACHE_DIR", "HIA_CACHE_DIR is invalid")
    candidate = Path(configured)
    if not candidate.is_absolute():
        raise BridgeError("INVALID_CACHE_DIR", "HIA_CACHE_DIR must be absolute")
    try:
        candidate = candidate.resolve()
    except OSError as exc:
        raise BridgeError("INVALID_CACHE_DIR", "HIA_CACHE_DIR cannot be resolved") from exc
    if not _same_windows_path(candidate, expected):
        raise BridgeError(
            "INVALID_CACHE_DIR",
            "HIA_CACHE_DIR must be the project .runtime/cache directory",
        )
    return expected


def _project_evidence_roots(
    project_root: Path, render_output_directory: str
) -> tuple[Path, ...]:
    """Allow project runtime artifacts and only the configured render root."""

    runtime_root = validate_project_subpath(
        project_root.resolve() / ".runtime",
        project_root=project_root,
    ).resolve()
    if (
        not isinstance(render_output_directory, str)
        or not render_output_directory.strip()
        or "\x00" in render_output_directory
    ):
        raise BridgeError(
            "INVALID_RENDER_OUTPUT_DIR", "HIA_RENDER_OUTPUT_DIR is invalid"
        )
    configured = Path(render_output_directory)
    if not configured.is_absolute():
        raise BridgeError(
            "INVALID_RENDER_OUTPUT_DIR", "HIA_RENDER_OUTPUT_DIR must be absolute"
        )
    try:
        configured = configured.resolve()
    except OSError as exc:
        raise BridgeError(
            "INVALID_RENDER_OUTPUT_DIR", "HIA_RENDER_OUTPUT_DIR cannot be resolved"
        ) from exc
    if configured == Path(configured.anchor):
        raise BridgeError(
            "INVALID_RENDER_OUTPUT_DIR", "HIA_RENDER_OUTPUT_DIR cannot be a drive root"
        )
    roots: list[Path] = []
    for candidate in (runtime_root, configured):
        if not any(_same_windows_path(candidate, existing) for existing in roots):
            roots.append(candidate)
    return tuple(roots)


@dataclass(frozen=True)
class ProjectRuntime:
    service: ProjectTeamService
    workflow: ProjectWorkflowHost
    registry: ProjectRegistry
    runner: ProjectRunner
    effect_client: ProjectEffectClient
    thread_factory: ProjectThreadFactory
    thread_transfer: ProjectThreadTransfer
    artifacts: ProjectArtifactStore
    events: EventBuffer
    _transfer_lock: threading.RLock = field(default_factory=threading.RLock)
    _transfers_inflight: set[tuple[str, Role]] = field(default_factory=set)

    def observe_state(self, state: ProjectState) -> None:
        """Migrate a role only after its third persisted real compaction."""

        for role, binding in state.roles.items():
            turn = state.turns.get(role)
            if binding.compaction_count < 3 or (turn is not None and turn.active):
                continue
            key = (state.project_id, role)
            with self._transfer_lock:
                if key in self._transfers_inflight:
                    continue
                self._transfers_inflight.add(key)
            threading.Thread(
                target=self._transfer_role,
                args=(state.project_id, role),
                name=f"hia-transfer-{role.value}",
                daemon=True,
            ).start()

    def observe_codex_event(self, event: Mapping[str, object]) -> bool:
        """Persist one deduplicated real automatic-compaction notification."""

        if event.get("type") != "codex_notification":
            return False
        method = event.get("method")
        params = event.get("params")
        if not isinstance(params, Mapping):
            return False
        if method == "thread/compacted":
            thread_id = params.get("threadId")
            turn_id = params.get("turnId")
        elif method == "item/completed":
            item = params.get("item")
            if not isinstance(item, Mapping) or item.get("type") != "contextCompaction":
                return False
            thread_id = params.get("threadId")
            turn_id = params.get("turnId")
        else:
            return False
        if not isinstance(thread_id, str) or not isinstance(turn_id, str):
            return False
        identity = self.service.role_identity_for_thread(thread_id)
        if identity is None:
            return False
        project_id, role = identity
        with self._transfer_lock:
            for _ in range(3):
                record = self.registry.require(project_id)
                binding = record.state.roles.get(role)
                if binding is None or binding.thread_id != thread_id:
                    return True
                item = params.get("item")
                item_id = item.get("id") if isinstance(item, Mapping) else None
                event_id = (
                    f"item:{turn_id}:{item_id}"
                    if isinstance(item_id, str) and item_id
                    else f"legacy:{turn_id}"
                )
                if event_id in binding.compaction_event_ids:
                    return True
                compaction_ids = list(binding.compaction_event_ids)
                legacy_id = f"legacy:{turn_id}"
                item_prefix = f"item:{turn_id}:"
                if event_id == legacy_id and any(
                    value.startswith(item_prefix) for value in compaction_ids
                ):
                    return True
                if event_id.startswith(item_prefix) and legacy_id in compaction_ids:
                    compaction_ids[compaction_ids.index(legacy_id)] = event_id
                else:
                    compaction_ids.append(event_id)
                roles = dict(record.state.roles)
                roles[role] = replace(
                    binding,
                    compaction_count=len(compaction_ids),
                    compaction_event_ids=tuple(compaction_ids),
                )
                updated = replace(
                    record.state,
                    roles=roles,
                    revision=record.state.revision + 1,
                )
                try:
                    self.registry.put(
                        ProjectRecord(
                            updated,
                            record.authoritative_task_text,
                            record.attachments,
                        ),
                        expected_revision=record.state.revision,
                    )
                except ValueError as exc:
                    if "revision mismatch" not in str(exc):
                        raise
                    continue
                self.events.publish(
                    "project_thread_compaction_recorded",
                    project_id=project_id,
                    role=role.value,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    compaction_count=roles[role].compaction_count,
                )
                return True
        raise ValueError("project compaction could not be persisted after concurrent updates")

    def _transfer_role(self, project_id: str, role: Role) -> None:
        key = (project_id, role)
        old_thread_id: str | None = None
        try:
            record = self.registry.require(project_id)
            binding = record.state.roles.get(role)
            if binding is None or binding.compaction_count < 3:
                return
            turn = record.state.turns.get(role)
            if turn is not None and turn.active:
                return
            old_thread_id = binding.thread_id
            prepared = self.thread_transfer.prepare(record.state, role)

            def persist(updated: ProjectState) -> None:
                self.registry.put(
                    ProjectRecord(
                        updated,
                        record.authoritative_task_text,
                        record.attachments,
                    ),
                    expected_revision=record.state.revision,
                )

            result = self.thread_transfer.commit(record.state, prepared, persist)
            if result.old_thread_deleted:
                self.events.publish(
                    "thread_transferred",
                    old_thread_id=prepared.old_thread_id,
                    new_thread_id=prepared.new_thread_id,
                    project_id=project_id,
                    role=role.value,
                )
            else:
                self.events.publish(
                    "thread_transfer_cleanup_failed",
                    old_thread_id=prepared.old_thread_id,
                    new_thread_id=prepared.new_thread_id,
                    project_id=project_id,
                    role=role.value,
                    error=result.cleanup_error,
                )
        except Exception as exc:
            self.events.publish(
                "thread_transferred",
                old_thread_id=old_thread_id,
                new_thread_id=None,
                project_id=project_id,
                role=role.value,
                error=str(exc),
            )
        finally:
            with self._transfer_lock:
                self._transfers_inflight.discard(key)
            self.events.publish(
                "project_team_updated",
                project_team=self.service.snapshot(),
            )

    def recover(self) -> tuple[str, ...]:
        recoverable: list[str] = []
        failures: list[dict[str, str]] = []
        for record in self.registry.list():
            if record.state.status is ProjectStatus.INTERRUPTED:
                if not record.state.pending_effects:
                    record = self.runner.dispatch(
                        record.state.project_id,
                        LifecycleEvent(ProjectEvent.RESTART_REQUESTED),
                    )
                if (
                    record.state.pending_effects
                    and record.state.pending_effects[0].kind == "verify_recovery"
                ):
                    recoverable.append(record.state.project_id)
                else:
                    failures.append(
                        {
                            "project_id": record.state.project_id,
                            "error": "interrupted project has unresolved non-recovery work",
                        }
                    )
                continue
            if not record.state.pending_effects and not record.state.plan_stale:
                continue
            try:
                self.thread_factory.validate_recovery_identity(record.state)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                record = self.runner.persist_recovery_failure(
                    record.state.project_id,
                    error,
                )
                failures.append(
                    {
                        "project_id": record.state.project_id,
                        "error": error,
                    }
                )
                continue
            recoverable.append(record.state.project_id)
        scheduled = self.workflow.recover(recoverable)
        self.events.publish(
            "project_team_updated",
            project_team=self.service.snapshot(),
            recovered_project_ids=list(scheduled),
            recovery_failures=failures,
        )
        return scheduled

    def close(self, timeout_seconds: float = 5.0) -> bool:
        completed = self.workflow.close(timeout_seconds)
        self.events.publish(
            "project_workflow_closed",
            completed=completed,
        )
        return completed


def _build_project_runtime(
    *,
    client: CodexStdioClient,
    events: EventBuffer,
    project_root: Path,
    selected_backend: str,
    server_transports: Mapping[str, Mapping[str, object]],
    allowed_evidence_roots: Sequence[Path],
    model_catalog: Callable[[], Mapping[str, object]] | None = None,
    thread_deleter: Callable[[str], object] | None = None,
) -> ProjectRuntime:
    """Compose the project runtime once around the owned app-server client."""

    registry = ProjectRegistry(project_root / PROJECT_REGISTRY_RELATIVE_PATH)
    runner = ProjectRunner(registry)
    effect_client = ProjectEffectClient(client, events)
    thread_factory = ProjectThreadFactory(
        effect_client,
        project_root,
        selected_backend,
        server_transports,
    )
    thread_transfer = ProjectThreadTransfer(
        effect_client,
        enabled=True,
        selected_backend=selected_backend,
        server_transports=server_transports,
    )
    artifacts = ProjectArtifactStore(
        project_root / PROJECT_ARTIFACTS_RELATIVE_PATH
    )
    service_holder: list[ProjectTeamService] = []
    runtime_holder: list[ProjectRuntime] = []

    def publish_snapshot(record: object) -> None:
        if service_holder:
            events.publish(
                "project_team_updated",
                project_team=service_holder[0].snapshot(),
            )
        if runtime_holder and isinstance(record, ProjectRecord):
            runtime_holder[0].observe_state(record.state)

    def interrupt_project(project_id: str) -> None:
        record = registry.require(project_id)
        interrupted = effect_client.interrupt_threads(
            binding.thread_id for binding in record.state.roles.values()
        )
        events.publish(
            "project_interrupt_requested",
            project_id=project_id,
            turns=[
                {"thread_id": thread_id, "turn_id": turn_id}
                for thread_id, turn_id in interrupted
            ],
        )

    def executor_factory(_: str) -> ProjectEffectExecutor:
        return ProjectEffectExecutor(
            client=effect_client,
            registry=registry,
            thread_factory=thread_factory,
            artifacts=artifacts,
            allowed_evidence_roots=allowed_evidence_roots,
        )

    workflow = ProjectWorkflowHost(
        registry=registry,
        runner=runner,
        executor_factory=executor_factory,
        interrupt_hook=interrupt_project,
        on_snapshot=publish_snapshot,
    )
    service = ProjectTeamService(
        client=effect_client,
        project_root=project_root,
        registry=registry,
        legacy_registry_path=project_root / LEGACY_PROJECT_REGISTRY_RELATIVE_PATH,
        settings=ProjectTeamSettings(
            project_root / PROJECT_SETTINGS_RELATIVE_PATH
        ),
        thread_factory=thread_factory,
        model_catalog=model_catalog,
        workflow=workflow,
        thread_deleter=thread_deleter,
        artifacts=artifacts,
    )
    service_holder.append(service)
    runtime = ProjectRuntime(
        service=service,
        workflow=workflow,
        registry=registry,
        runner=runner,
        effect_client=effect_client,
        thread_factory=thread_factory,
        thread_transfer=thread_transfer,
        artifacts=artifacts,
        events=events,
    )
    runtime_holder.append(runtime)
    return runtime


def _required_launch_secret(name: str) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or _LAUNCH_SECRET_PATTERN.fullmatch(value) is None:
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            f"Required launch credential is missing or invalid: {name}",
        )
    return value


def _required_bridge_url() -> tuple[str, int]:
    value = os.environ.get("HIA_BRIDGE_URL")
    if not isinstance(value, str):
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            "Required loopback Bridge URL is missing",
        )
    match = _BRIDGE_URL_PATTERN.fullmatch(value)
    if match is None:
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            "Bridge URL must be an exact credential-free IPv4 loopback origin",
        )
    port = int(match.group(1))
    if not 1 <= port <= 65_535:
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            "Bridge URL port is outside the valid range",
        )
    return value, port


def _required_loopback_port(name: str) -> int:
    value = os.environ.get(name, "")
    if not value.isascii() or not value.isdecimal():
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            f"Required Houdini MCP loopback port is missing or invalid: {name}",
        )
    port = int(value)
    if not 1 <= port <= 65_535:
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            "Houdini MCP loopback port is outside the valid range",
        )
    return port


def _required_houdini_mcp_port() -> int:
    return _required_loopback_port("HIA_HOUDINI_MCP_PORT")


def _required_hia_mcp_v2_environment(project_root: Path) -> dict[str, str]:
    host = os.environ.get("HIA_MCP_V2_HOST")
    if host != HIA_MCP_V2_HOST:
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            "HIA MCP V2 host must be exactly 127.0.0.1",
        )
    port = _required_loopback_port("HIA_MCP_V2_PORT")
    token = _required_launch_secret("HIA_MCP_V2_TOKEN")
    route = os.environ.get("HIA_MCP_V2_ROUTE")
    if route != HIA_MCP_V2_EXECUTE_ROUTE:
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            "HIA MCP V2 execute route is missing or invalid",
        )
    runtime_directory = os.environ.get("HIA_MCP_V2_RUNTIME_DIR")
    expected_runtime_directory = project_root / HIA_MCP_V2_RUNTIME_RELATIVE_PATH
    if not isinstance(runtime_directory, str) or not _same_windows_path(
        Path(runtime_directory),
        expected_runtime_directory,
    ):
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            f"HIA MCP V2 runtime directory must be {expected_runtime_directory}",
        )
    executor_path = os.environ.get("HIA_MCP_V2_EXECUTOR_PATH")
    expected_executor_path = project_root / HIA_MCP_V2_EXECUTOR_RELATIVE_PATH
    if not isinstance(executor_path, str) or not _same_windows_path(
        Path(executor_path),
        expected_executor_path,
    ):
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            f"HIA MCP V2 executor path must be {expected_executor_path}",
        )
    launcher_session_id = os.environ.get("HIA_LAUNCHER_SESSION_ID", "")
    if _LAUNCHER_SESSION_PATTERN.fullmatch(launcher_session_id) is None:
        raise BridgeError(
            "INVALID_LAUNCH_ENVIRONMENT",
            "HIA launcher session ID is missing or invalid",
        )
    return {
        "HIA_MCP_V2_HOST": host,
        "HIA_MCP_V2_PORT": str(port),
        "HIA_MCP_V2_TOKEN": token,
        "HIA_MCP_V2_ROUTE": route,
        "HIA_MCP_V2_RUNTIME_DIR": str(expected_runtime_directory),
        "HIA_MCP_V2_EXECUTOR_PATH": str(expected_executor_path),
        "HIA_LAUNCHER_SESSION_ID": launcher_session_id,
    }


def _prepend_environment_path(
    environment: dict[str, str],
    name: str,
    entries: Sequence[Path],
) -> None:
    existing = environment.get(name, "")
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in [*(str(entry) for entry in entries), *existing.split(os.pathsep)]:
        if not raw:
            continue
        key = raw.replace("/", "\\").rstrip("\\").casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(raw)
    environment[name] = os.pathsep.join(ordered)


def _allowlisted_child_environment(
    source: Mapping[str, str],
) -> dict[str, str]:
    """Copy only reviewed OS/network values into the owned Codex child."""

    by_casefold = {name.casefold(): value for name, value in source.items()}
    environment: dict[str, str] = {}
    for name in _CODEX_CHILD_ENVIRONMENT_ALLOWLIST:
        value = by_casefold.get(name.casefold())
        if isinstance(value, str) and "\x00" not in value:
            environment[name] = value
    return environment


def _redact_value(value: object, sensitive_values: Sequence[str]) -> object:
    if isinstance(value, str):
        redacted = value
        for sensitive in sorted(
            (item for item in sensitive_values if item),
            key=len,
            reverse=True,
        ):
            redacted = redacted.replace(sensitive, "[REDACTED]")
        return redacted
    if isinstance(value, dict):
        return {
            (
                _redact_value(key, sensitive_values)
                if isinstance(key, str)
                else key
            ): _redact_value(item, sensitive_values)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, sensitive_values) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, sensitive_values) for item in value)
    return value


def _toml_basic_string(value: str) -> str:
    if not isinstance(value, str) or not value or any(
        ord(character) < 0x20 for character in value
    ):
        raise BridgeError(
            "INVALID_CODEX_EXECUTABLE",
            "The MCP Python executable cannot be represented safely in TOML",
        )
    # JSON basic strings are a strict, safely escaped subset of TOML basic
    # strings for an ordinary Windows executable path.
    return json.dumps(value, ensure_ascii=True)


def _codex_app_server_command(
    codex_exe: Path,
    mcp_python: str,
    *,
    backend: str = FXHOUDINI_MCP_BACKEND,
    project_root: Path = PROJECT_ROOT,
) -> list[str]:
    """Build the strict, process-local HIA app-server command."""

    provider = f"model_providers.{_HIA_CHATGPT_HTTP_PROVIDER_ID}"
    if backend == HIA_MCP_V2_BACKEND:
        server = f"mcp_servers.{HIA_MCP_V2_SERVER_ID}"
        mcp_overrides = [
            f"mcp_servers.{FXHOUDINI_MCP_SERVER_ID}.enabled=false",
            f"{server}.command=" + _toml_basic_string(mcp_python),
            f"{server}.args="
            + json.dumps(["-B", "-m", HIA_MCP_V2_SERVER_ID]),
            f"{server}.cwd=" + _toml_basic_string(str(project_root)),
            f"{server}.env_vars="
            + json.dumps(list(HIA_MCP_V2_CHILD_ENVIRONMENT)),
            f"{server}.enabled=true",
            f"{server}.required=true",
            f"{server}.startup_timeout_sec=15",
            f"{server}.tool_timeout_sec=65",
            f"{server}.default_tools_approval_mode="
            + _toml_basic_string("approve"),
        ]
    elif backend == FXHOUDINI_MCP_BACKEND:
        server = f"mcp_servers.{FXHOUDINI_MCP_SERVER_ID}"
        mcp_overrides = [
            f"{server}.command=" + _toml_basic_string(mcp_python),
            f"{server}.required=true",
            f"{server}.default_tools_approval_mode="
            + _toml_basic_string("approve"),
        ]
    else:
        raise BridgeError(
            "INVALID_MCP_BACKEND",
            f"Unsupported Houdini MCP backend: {backend}",
        )

    command = [
        str(codex_exe),
        "app-server",
        "--strict-config",
    ]
    for override in mcp_overrides:
        command.extend(("-c", override))
    command.extend(
        [
            "-c",
            "model_provider=" + _toml_basic_string(_HIA_CHATGPT_HTTP_PROVIDER_ID),
            "-c",
            f"{provider}.name="
            + _toml_basic_string(_HIA_CHATGPT_HTTP_PROVIDER_NAME),
            "-c",
            f"{provider}.base_url="
            + _toml_basic_string(_HIA_CHATGPT_HTTP_BASE_URL),
            "-c",
            f"{provider}.wire_api="
            + _toml_basic_string(_HIA_CHATGPT_HTTP_WIRE_API),
            "-c",
            f"{provider}.requires_openai_auth=true",
            "-c",
            f"{provider}.supports_websockets=false",
        ]
    )
    return command


def _project_mcp_server_transports(
    mcp_python: str,
    *,
    backend: str,
    project_root: Path,
) -> dict[str, dict[str, object]]:
    """Return complete per-Thread transports for both HIA inventories.

    Codex 0.144.3 does not deep-merge an ``enabled``-only per-Thread MCP
    override with the process configuration.  Every role therefore receives
    complete transports, while its permission profile deterministically sets
    ``enabled`` and ``required`` for each exact server.
    """

    if backend not in {HIA_MCP_V2_BACKEND, FXHOUDINI_MCP_BACKEND}:
        raise BridgeError("INVALID_MCP_BACKEND", f"Unsupported MCP backend: {backend}")
    common: dict[str, object] = {
        "command": mcp_python,
        "cwd": str(project_root),
        "startup_timeout_sec": 15,
        "tool_timeout_sec": 65,
        "default_tools_approval_mode": "approve",
    }
    return {
        HIA_MCP_V2_SERVER_ID: {
            **common,
            "args": ["-B", "-m", HIA_MCP_V2_SERVER_ID],
            "env_vars": list(HIA_MCP_V2_CHILD_ENVIRONMENT),
        },
        FXHOUDINI_MCP_SERVER_ID: {
            **common,
            "args": ["-B", "-m", "fxhoudinimcp"],
            "env_vars": list(FXHOUDINI_MCP_CHILD_ENVIRONMENT),
        },
    }


def _validated_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    project_root = Path(args.project_root)
    if not _same_windows_path(project_root, PROJECT_ROOT):
        raise BridgeError(
            "INVALID_PROJECT_ROOT",
            f"Project root must be exactly {PROJECT_ROOT}",
        )
    codex_exe = validate_project_subpath(args.codex_exe, project_root=project_root)
    expected_codex = project_root / PINNED_CODEX_RELATIVE_PATH
    if not _same_windows_path(codex_exe, expected_codex):
        raise BridgeError(
            "INVALID_CODEX_EXECUTABLE",
            f"Codex executable must be {expected_codex}",
        )
    if not codex_exe.is_file():
        raise BridgeError(
            "CODEX_EXECUTABLE_MISSING",
            f"Pinned Codex executable does not exist: {codex_exe}",
        )
    codex_home = validate_project_subpath(args.codex_home, project_root=project_root)
    expected_home = project_root / CODEX_HOME_RELATIVE_PATH
    if not _same_windows_path(codex_home, expected_home):
        raise BridgeError(
            "INVALID_CODEX_HOME",
            f"CODEX_HOME must be {expected_home}",
        )
    temp_directory = validate_project_subpath(
        codex_home / "tmp",
        project_root=project_root,
    )
    return project_root, codex_exe, codex_home, temp_directory


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session: BridgeSession | None = None
    server: LoopbackHTTPServer | None = None
    scene_queue: SceneQueue | None = None
    project_runtime: ProjectRuntime | None = None
    ordinary_transfer: OrdinaryThreadTransfer | None = None
    sensitive_values: list[str] = []
    try:
        project_root, codex_exe, codex_home, temp_directory = _validated_paths(args)
        backend = args.mcp_backend
        codex_home.mkdir(parents=True, exist_ok=True)
        temp_directory.mkdir(parents=True, exist_ok=True)
        cache_directory = _cache_directory(
            project_root,
            os.environ.get("HIA_CACHE_DIR"),
        )
        configured_focus_state = os.environ.get("HIA_FOCUS_STATE_PATH", "")
        focus_state_path = validate_project_subpath(
            configured_focus_state,
            project_root=project_root,
        )
        expected_focus_state = project_root / FOCUS_STATE_RELATIVE_PATH
        if not _same_windows_path(focus_state_path, expected_focus_state):
            raise BridgeError(
                "INVALID_FOCUS_STATE_PATH",
                f"HIA_FOCUS_STATE_PATH must be {expected_focus_state}",
            )
        configured_render_output = os.environ.get("HIA_RENDER_OUTPUT_DIR")
        render_output_directory = (
            configured_render_output.strip()
            if isinstance(configured_render_output, str)
            and configured_render_output.strip()
            else str(cache_directory)
        )
        for directory in (
            cache_directory,
            cache_directory / "screenshots",
            cache_directory / "previews",
            cache_directory / "tmp",
        ):
            directory.mkdir(parents=True, exist_ok=True)

        token = _required_launch_secret("HIA_BRIDGE_TOKEN")
        sensitive_values.append(token)
        scene_executor_token = _required_launch_secret(
            "HIA_SCENE_EXECUTOR_TOKEN"
        )
        sensitive_values.append(scene_executor_token)
        requested_bridge_url, requested_bridge_port = _required_bridge_url()
        sensitive_values.append(requested_bridge_url)
        if hmac.compare_digest(token, scene_executor_token):
            raise BridgeError(
                "INVALID_LAUNCH_ENVIRONMENT",
                "Bridge and scene executor credentials must be independent",
            )

        policy = ProtocolPolicy.from_project_root(project_root)
        child_environment = _allowlisted_child_environment(os.environ)
        resolved_python = str(Path(sys.executable).resolve())
        resolved_python_path = Path(resolved_python)
        if not resolved_python_path.is_absolute() or not resolved_python_path.is_file():
            raise BridgeError(
                "INVALID_CODEX_EXECUTABLE",
                "The active Bridge Python executable is not an absolute existing file",
            )

        if backend == HIA_MCP_V2_BACKEND:
            hia_service_root = validate_project_subpath(
                project_root / HIA_MCP_V2_SERVICE_RELATIVE_PATH,
                project_root=project_root,
            )
            if not (
                hia_service_root / "hia_mcp_v2" / "__main__.py"
            ).is_file():
                raise BridgeError(
                    "HIA_MCP_V2_MISSING",
                    "Project-local HIA MCP V2 stdio package is incomplete",
                )
            hia_environment = _required_hia_mcp_v2_environment(project_root)
            houdini_mcp_token = hia_environment["HIA_MCP_V2_TOKEN"]
            houdini_mcp_port = int(hia_environment["HIA_MCP_V2_PORT"])
            houdini_launcher_session_id = hia_environment[
                "HIA_LAUNCHER_SESSION_ID"
            ]
            houdini_executor_path = Path(
                hia_environment["HIA_MCP_V2_EXECUTOR_PATH"]
            )
            sensitive_values.append(houdini_mcp_token)
            mcp_python = resolved_python
            path_entries = (resolved_python_path.parent,)
            python_path_entries = (hia_service_root, project_root / "src")
            mcp_environment = hia_environment
        else:
            fx_mcp_python = validate_project_subpath(
                project_root / FXHOUDINI_MCP_PYTHON_RELATIVE_PATH,
                project_root=project_root,
            )
            fx_mcp_source = validate_project_subpath(
                project_root / FXHOUDINI_MCP_SOURCE_RELATIVE_PATH,
                project_root=project_root,
            )
            if not fx_mcp_python.is_file() or not fx_mcp_source.is_dir():
                raise BridgeError(
                    "FXHOUDINIMCP_MISSING",
                    "Project-local fxhoudinimcp 1.3.0 runtime is incomplete",
                )
            houdini_mcp_token = _required_launch_secret("FXHOUDINIMCP_TOKEN")
            houdini_mcp_port = _required_houdini_mcp_port()
            houdini_launcher_session_id = None
            houdini_executor_path = None
            sensitive_values.append(houdini_mcp_token)
            mcp_python = str(fx_mcp_python)
            path_entries = (fx_mcp_python.parent, resolved_python_path.parent)
            python_path_entries = (
                project_root / "services" / "houdini_mcp",
                fx_mcp_source,
                project_root / "src",
            )
            mcp_environment = {
                "HOUDINI_HOST": "127.0.0.1",
                "HOUDINI_PORT": str(houdini_mcp_port),
                "FXHOUDINIMCP_TOKEN": houdini_mcp_token,
            }

        _prepend_environment_path(child_environment, "PATH", path_entries)
        _prepend_environment_path(
            child_environment,
            "PYTHONPATH",
            python_path_entries,
        )
        child_environment.update(
            {
                "CODEX_HOME": str(codex_home),
                "TEMP": str(temp_directory),
                "TMP": str(temp_directory),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "HIA_PROJECT_ROOT": str(project_root),
                "HIA_CACHE_DIR": str(cache_directory),
                "HIA_RENDER_OUTPUT_DIR": render_output_directory,
                "HIA_EXPECTED_PYTHON_EXE": resolved_python,
                **mcp_environment,
            }
        )
        events = EventBuffer()
        client = CodexStdioClient(
            _codex_app_server_command(
                codex_exe,
                mcp_python,
                backend=backend,
                project_root=project_root,
            ),
            cwd=project_root,
            environment=child_environment,
            policy=policy,
            request_timeout=45.0,
        )
        session = BridgeSession(
            project_root,
            client,
            events,
            mcp_backend=backend,
            focus_state_path=focus_state_path,
        )
        project_runtime_arguments = dict(
            client=client,
            events=events,
            project_root=project_root,
            selected_backend=(
                HIA_MCP_V2_SERVER_ID
                if backend == HIA_MCP_V2_BACKEND
                else FXHOUDINI_MCP_SERVER_ID
            ),
            server_transports=_project_mcp_server_transports(
                mcp_python,
                backend=backend,
                project_root=project_root,
            ),
            allowed_evidence_roots=_project_evidence_roots(
                project_root, render_output_directory
            ),
            thread_deleter=(
                session.delete_thread
                if callable(getattr(session, "delete_thread", None))
                else None
            ),
        )
        session_model_catalog = getattr(session, "list_models", None)
        if callable(session_model_catalog):
            project_runtime_arguments["model_catalog"] = session_model_catalog
        project_runtime = _build_project_runtime(**project_runtime_arguments)
        observer_setter = getattr(session, "set_project_event_observer", None)
        if callable(observer_setter):
            observer_setter(project_runtime.observe_codex_event)
        rebind_transferred = getattr(session, "rebind_transferred_thread", None)
        ordinary_transfer = OrdinaryThreadTransfer(
            client,
            project_root=project_root,
            ledger_path=project_root / ORDINARY_TRANSFER_RELATIVE_PATH,
            publish=events.publish,
            rebind=(
                rebind_transferred
                if callable(rebind_transferred)
                else lambda _old, _new: False
            ),
        )
        transfer_setter = getattr(session, "set_ordinary_thread_transfer", None)
        if callable(transfer_setter):
            transfer_setter(ordinary_transfer)
        project_team = project_runtime.service
        scene_launch_id = f"launch-{secrets.token_hex(16)}"
        scene_generation = 1
        houdini_process_nonce = f"houdini-{secrets.token_hex(16)}"
        scene_registry = SchemaRegistry.b2_read_only(
            project_root / "schemas" / "houdini-mcp" / B2_SCHEMA_VERSION
        )
        scene_queue = SceneQueue(
            scene_launch_id,
            scene_generation,
            expected_schema_digest=scene_registry.manifest_digest,
            expected_catalog_digest=None,
            profile=B2_READ_ONLY_PROFILE,
            expected_process_nonce=houdini_process_nonce,
        )
        application = BridgeApplication(
            session,
            events,
            token,
            scene_queue=scene_queue,
            scene_registry=scene_registry,
            scene_executor_token=scene_executor_token,
            houdini_mcp_port=houdini_mcp_port,
            houdini_mcp_token=houdini_mcp_token,
            houdini_mcp_backend=backend,
            houdini_launcher_session_id=houdini_launcher_session_id,
            houdini_executor_path=houdini_executor_path,
            project_team=project_team,
        )
        server = LoopbackHTTPServer(
            ("127.0.0.1", requested_bridge_port),
            application,
        )

        host, port = server.server_address
        bridge_url = f"http://{host}:{port}"
        if bridge_url != requested_bridge_url:
            raise BridgeError(
                "BRIDGE_BIND_MISMATCH",
                "Bridge did not bind the exact launch-scoped loopback origin",
            )
        client.set_environment_overlay(
            {
                "HIA_BRIDGE_URL": bridge_url,
                "HIA_BRIDGE_TOKEN": token,
            }
        )
        session.start()
        project_runtime.recover()
        if ordinary_transfer is not None:
            ordinary_transfer.recover()

        def request_shutdown(*_: object) -> None:
            threading.Thread(
                target=server.shutdown,
                name="hia-signal-shutdown",
                daemon=True,
            ).start()

        for signal_name in ("SIGINT", "SIGTERM"):
            signal_value = getattr(signal, signal_name, None)
            if signal_value is not None:
                signal.signal(signal_value, request_shutdown)

        bootstrap = {
            "ok": True,
            "bridge_pid": os.getpid(),
            "codex_pid": client.process_id,
            "codex_version": policy.version,
            "transport": "stdio-jsonl",
            "mcp_backend": backend,
            "scene": {
                "profile": "p2-v-b2-read-only",
                "launch_id": scene_launch_id,
                "generation": scene_generation,
                "process_nonce": houdini_process_nonce,
                "schema_version": scene_registry.schema_version,
                "schema_digest": scene_registry.manifest_digest,
            },
        }
        print(json.dumps(bootstrap, ensure_ascii=False, separators=(",", ":")), flush=True)
        server.serve_forever(poll_interval=0.25)
        return 0
    except (BridgeError, PathPolicyError) as exc:
        if hasattr(exc, "to_dict"):
            payload = exc.to_dict()
        else:
            payload = {
                "ok": False,
                "structured_error": {"code": "PATH_POLICY_ERROR", "message": str(exc)},
            }
        print(
            json.dumps(
                _redact_value(payload, sensitive_values),
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1
    except Exception as exc:
        payload = {
            "ok": False,
            "structured_error": {
                "code": "BRIDGE_START_FAILED",
                "message": f"{type(exc).__name__}: {exc}",
            },
        }
        print(
            json.dumps(
                _redact_value(payload, sensitive_values),
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1
    finally:
        try:
            if server is not None:
                server.server_close()
        finally:
            try:
                if project_runtime is not None:
                    project_runtime.close()
            finally:
                try:
                    if ordinary_transfer is not None:
                        ordinary_transfer.close()
                finally:
                    try:
                        if scene_queue is not None:
                            scene_queue.shutdown()
                    finally:
                        if session is not None:
                            session.close()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
