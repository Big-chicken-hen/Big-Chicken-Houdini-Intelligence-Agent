"""Connection and Thread/Turn state without a duplicate chat store."""

from __future__ import annotations

import copy
import hashlib
import json
import ntpath
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from .codex_stdio import CodexStdioClient, RequestId
from .errors import BridgeError, CodexRPCError
from .events import EventBuffer
from .project_thread_contract import (
    PROJECT_HIA_DISABLE_CONFIG,
    PROJECT_HIA_ENABLE_CONFIG,
    PROJECT_READ_ONLY_ROLES,
    PROJECT_ROLE_ORDER,
    project_role_config,
    role_instructions,
    supervisor_intake_prompt,
)
from .project_threads import (
    PROJECT_TEAM_MODES,
    ProjectTeamSettings,
    ProjectThreadCoordinator,
)
from .thread_transfer import ThreadTransferManager


MODEL_LIST_PAGE_SIZE = 100
MODEL_LIST_MAX_PAGES = 16
MODEL_LIST_MAX_ENTRIES = 512
MODEL_IDENTIFIER_MAX_LENGTH = 256
REASONING_EFFORT_MAX_LENGTH = 64
SERVICE_TIER_MAX_LENGTH = 256
MODEL_SERVICE_TIER_MAX_ENTRIES = 32
MODEL_DISPLAY_NAME_MAX_LENGTH = 512
MODEL_DESCRIPTION_MAX_LENGTH = 8192
MODEL_CURSOR_MAX_LENGTH = 4096
THREAD_LIST_LIMIT = 20
THREAD_NAME_MAX_LENGTH = 512
THREAD_PREVIEW_MAX_LENGTH = 8192
THREAD_CWD_MAX_LENGTH = 32_767
GOAL_OBJECTIVE_MAX_LENGTH = 4_000
FOCUS_STATE_MAX_BYTES = 1_048_576
GOAL_BINDING_PATTERN = re.compile(r"[0-9a-f]{64}")
GOAL_STATUSES = frozenset(
    {
        "active",
        "paused",
        "blocked",
        "usageLimited",
        "budgetLimited",
        "complete",
    }
)
MAX_LOCAL_IMAGES = 16
LOCAL_IMAGE_PATH_MAX_LENGTH = 32_767
LOCAL_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
HIA_MCP_V2_BACKEND = "hia_v2"
FXHOUDINI_MCP_BACKEND = "fxhoudini"
STOP_INTERRUPT_GRACE_SECONDS = 1.0
STOP_RECOVERY_TOTAL_SECONDS = 50.0
STOP_RESTART_GRACE_SECONDS = 0.25
STOP_REINITIALIZE_MAX_SECONDS = 10.0

_COMMAND_WRITE_PATTERN = re.compile(
    r"(?i)(?:"
    r"\b(?:set|add|clear)-content\b|\b(?:set|clear)-item\b|"
    r"\bout-file\b|\bnew-item\b|"
    r"\bremove-item\b|\bcopy-item\b|\bmove-item\b|\brename-item\b|"
    r"\btee-object\b[^\r\n]*\s-filepath\b|"
    r"\b(?:invoke-webrequest|invoke-restmethod|iwr|irm)\b"
    r"[^\r\n]*\s-outfile\b|"
    r"(?:^|[;&|]\s*|\bcmd(?:\.exe)?\s+/[ck]\s+)"
    r"\s*(?:curl|wget)(?:\.exe)?\b[^\r\n]*"
    r"\s(?:-o|--output(?:-document)?)(?:\s+|=)|"
    r"(?:^|[;&|]\s*|\bcmd(?:\.exe)?\s+/[ck]\s+)"
    r"\s*(?:del|erase|rm|rd|rmdir|mkdir|md|copy|move|xcopy|robocopy)\b|"
    r"\b(?:write|append)all(?:text|bytes)\b|\bwrite_(?:text|bytes)\b|"
    r"\b(?:copyfile|copy2|copytree)\s*\(|"
    r"\b(?:unlink|remove|rmtree|makedirs|mkdir|rename|replace)\s*\(|"
    r"\bopen\s*\([^\r\n]*[, ]\s*['\"]?[wax](?:\+)?['\"]?"
    r"|(?<![<>=])>>?(?![=])"
    r")"
)
_MOVE_PATTERN = re.compile(
    r"(?i)\bmove-item\b|"
    r"(?:^|[;&|]\s*|\bcmd(?:\.exe)?\s+/[ck]\s+)\s*move\b|"
    r"\brobocopy\b[^\r\n]*\s/(?:mov|move)\b"
)
_COPY_PATTERN = re.compile(
    r"(?i)\bcopy-item\b|\b(?:copyfile|copy2|copytree)\s*\(|"
    r"(?:^|[;&|]\s*|\bcmd(?:\.exe)?\s+/[ck]\s+)"
    r"\s*(?:copy|xcopy|robocopy)\b"
)
_RENAME_PATTERN = re.compile(r"(?i)\brename-item\b|\brename\s*\(")
_WINDOWS_PATH_PATTERN = re.compile(
    r'''(?ix)
    "(?P<double>[a-z]:[\\/][^"]*)"
    |'(?P<single>[a-z]:[\\/][^']*)'
    |(?<![a-z])(?P<bare>[a-z]:[\\/][^\s|;&><,"']*)
    '''
)
_DESTINATION_FLAG_PATTERN = re.compile(
    r'''(?ix)
    -(?:destination|dest)\s+
    (?:"(?P<double>[^"]+)"|'(?P<single>[^']+)'|(?P<bare>[^\s|;&]+))
    '''
)
_TARGET_FLAG_PATTERN = re.compile(
    r'''(?ix)
    -(?:literalpath|path|filepath|outfile|output|output-document|o)(?:\s+|=)
    (?:"(?P<double>[^"]+)"|'(?P<single>[^']+)'|(?P<bare>[^\s|;&]+))
    '''
)
_REDIRECTION_TARGET_PATTERN = re.compile(
    r'''(?ix)
    (?<![<>=])>>?(?![=])\s*
    (?:"(?P<double>[^"]+)"|'(?P<single>[^']+)'|(?P<bare>[^\s|;&]+))
    '''
)
_SYSTEM_LOCATION_REFERENCE_PATTERN = re.compile(
    r"(?i)(?:"
    r"\$(?:\{(?:env:)?(?:systemdrive|userprofile|home|appdata|localappdata|"
    r"programfiles|programfiles\(x86\)|systemroot|windir)\}|"
    r"(?:env:)?(?:systemdrive|userprofile|home|appdata|localappdata|programfiles|"
    r"programfiles\(x86\)|systemroot|windir)\b)|"
    r"%(?:systemdrive|userprofile|home|appdata|localappdata|programfiles|"
    r"programfiles\(x86\)|systemroot|windir)%|"
    r"~[\\/]|"
    r"\[environment\]::getfolderpath\s*\(\s*['\"](?:desktop|"
    r"userprofile|applicationdata|localapplicationdata|programfiles)"
    r"['\"]\s*\)"
    r")"
)


def _system_drive() -> str:
    for candidate in (
        os.environ.get("SystemDrive"),
        os.environ.get("SystemRoot"),
        os.environ.get("WINDIR"),
        str(Path.home()),
    ):
        drive = ntpath.splitdrive(str(candidate or ""))[0]
        if re.fullmatch(r"[A-Za-z]:", drive):
            return drive.casefold()
    return "c:"


def _match_value(match: re.Match[str]) -> str:
    return next(
        (value for value in match.groupdict().values() if value is not None),
        "",
    ).strip().rstrip(",)")


def _command_texts(params: dict[str, Any]) -> tuple[str, ...]:
    texts: list[str] = []
    actions = params.get("commandActions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, dict):
                continue
            command = action.get("command")
            if isinstance(command, str) and command.strip():
                texts.append(command)
    if texts:
        return tuple(texts)
    command = params.get("command")
    if isinstance(command, str) and command.strip():
        texts.append(command)
    return tuple(texts)


def _normalized_windows_path(value: str, cwd: str | None = None) -> str | None:
    candidate = value.strip().strip("\"'")
    if not candidate or "\x00" in candidate:
        return None
    if not ntpath.isabs(candidate):
        if not isinstance(cwd, str) or not ntpath.isabs(cwd):
            return None
        candidate = ntpath.join(cwd, candidate)
    return ntpath.normcase(ntpath.normpath(candidate))


def _normalized_thread_cwd(value: str) -> str | None:
    candidate = value.strip()
    if candidate.startswith("\\\\?\\UNC\\"):
        candidate = "\\\\" + candidate[8:]
    elif candidate.startswith("\\\\?\\"):
        candidate = candidate[4:]
    try:
        return os.path.normcase(str(Path(candidate).resolve(strict=False)))
    except (OSError, RuntimeError, ValueError):
        return None


def _thread_cwd_filters(value: str) -> list[str]:
    """Return ordinary and Windows extended spellings for one project cwd."""

    candidate = value.strip()
    if candidate.startswith("\\\\?\\UNC\\"):
        ordinary = "\\\\" + candidate[8:]
        extended = candidate
    elif candidate.startswith("\\\\?\\"):
        ordinary = candidate[4:]
        extended = candidate
    elif candidate.startswith("\\\\"):
        ordinary = candidate
        extended = "\\\\?\\UNC\\" + candidate[2:]
    elif re.match(r"^[A-Za-z]:[\\/]", candidate):
        ordinary = candidate
        extended = "\\\\?\\" + candidate
    else:
        return [candidate]

    return list(dict.fromkeys((ordinary, extended)))


def _path_is_within(value: str, root: str) -> bool:
    try:
        return ntpath.commonpath((value, root)) == root
    except ValueError:
        return False


def _path_is_system_write_target(
    value: str,
    *,
    cwd: str | None,
    project_root: Path,
    system_drive: str,
) -> bool:
    target = _normalized_windows_path(value, cwd)
    project = _normalized_windows_path(str(project_root))
    if target is None or project is None:
        return False
    if _path_is_within(target, project):
        return False
    return ntpath.splitdrive(target)[0].casefold() == system_drive


def _command_requires_system_drive_approval(
    params: dict[str, Any],
    *,
    project_root: Path,
    system_drive: str,
) -> bool:
    cwd = params.get("cwd") if isinstance(params.get("cwd"), str) else None
    for command in _command_texts(params):
        if _COMMAND_WRITE_PATTERN.search(command) is None:
            continue

        targets = [
            _match_value(match)
            for match in _REDIRECTION_TARGET_PATTERN.finditer(command)
        ]
        if _MOVE_PATTERN.search(command) is not None:
            targets.extend(
                _match_value(match)
                for match in _WINDOWS_PATH_PATTERN.finditer(command)
            )
            targets.extend(
                _match_value(match)
                for match in _TARGET_FLAG_PATTERN.finditer(command)
            )
            targets.extend(
                _match_value(match)
                for match in _DESTINATION_FLAG_PATTERN.finditer(command)
            )
        elif _COPY_PATTERN.search(command) is not None:
            destinations = [
                _match_value(match)
                for match in _DESTINATION_FLAG_PATTERN.finditer(command)
            ]
            if destinations:
                targets.extend(destinations)
            else:
                paths = [
                    _match_value(match)
                    for match in _WINDOWS_PATH_PATTERN.finditer(command)
                ]
                if paths:
                    targets.append(paths[-1])
        elif _RENAME_PATTERN.search(command) is not None:
            targets.extend(
                _match_value(match)
                for match in _WINDOWS_PATH_PATTERN.finditer(command)
            )
            targets.extend(
                _match_value(match)
                for match in _TARGET_FLAG_PATTERN.finditer(command)
            )
        else:
            flagged = [
                _match_value(match)
                for match in _TARGET_FLAG_PATTERN.finditer(command)
            ]
            targets.extend(flagged)
            if not flagged and not targets:
                targets.extend(
                    _match_value(match)
                    for match in _WINDOWS_PATH_PATTERN.finditer(command)
                )

        if any(
            _SYSTEM_LOCATION_REFERENCE_PATTERN.search(target)
            for target in targets
        ):
            return True
        if (
            not targets
            and _SYSTEM_LOCATION_REFERENCE_PATTERN.search(command) is not None
        ):
            return True

        if any(
            _path_is_system_write_target(
                target,
                cwd=cwd,
                project_root=project_root,
                system_drive=system_drive,
            )
            for target in targets
            if target
        ):
            return True
        if not targets and isinstance(cwd, str) and _path_is_system_write_target(
            ".",
            cwd=cwd,
            project_root=project_root,
            system_drive=system_drive,
        ):
            return True
    return False


def _permission_write_targets(params: dict[str, Any]) -> tuple[str, ...]:
    permissions = params.get("permissions")
    if not isinstance(permissions, dict):
        return ()
    file_system = permissions.get("fileSystem")
    if not isinstance(file_system, dict):
        return ()

    targets: list[str] = []
    legacy_write = file_system.get("write")
    if isinstance(legacy_write, list):
        targets.extend(value for value in legacy_write if isinstance(value, str))

    entries = file_system.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("access") != "write":
                continue
            path = entry.get("path")
            if not isinstance(path, dict):
                continue
            path_type = path.get("type")
            if path_type == "path" and isinstance(path.get("path"), str):
                targets.append(path["path"])
            elif path_type == "glob_pattern" and isinstance(
                path.get("pattern"), str
            ):
                targets.append(path["pattern"])
            elif path_type == "special" and isinstance(path.get("value"), dict):
                special = path["value"]
                kind = special.get("kind")
                if kind == "root":
                    targets.append(system_drive + "\\")
                elif kind == "unknown" and isinstance(special.get("path"), str):
                    targets.append(special["path"])
    return tuple(targets)


def _requires_system_drive_approval(
    method: Any,
    params: Any,
    *,
    project_root: Path,
    system_drive: str | None = None,
) -> bool:
    if not isinstance(method, str) or not isinstance(params, dict):
        return False
    resolved_system_drive = (system_drive or _system_drive()).casefold()
    if method == "item/commandExecution/requestApproval":
        return _command_requires_system_drive_approval(
            params,
            project_root=project_root,
            system_drive=resolved_system_drive,
        )
    if method == "item/fileChange/requestApproval":
        grant_root = params.get("grantRoot")
        return isinstance(grant_root, str) and _path_is_system_write_target(
            grant_root,
            cwd=None,
            project_root=project_root,
            system_drive=resolved_system_drive,
        )
    if method == "item/permissions/requestApproval":
        cwd = params.get("cwd") if isinstance(params.get("cwd"), str) else None
        return any(
            _path_is_system_write_target(
                target,
                cwd=cwd,
                project_root=project_root,
                system_drive=resolved_system_drive,
            )
            for target in _permission_write_targets(params)
        )
    return False


def _offered_execpolicy_amendment(params: Any) -> list[str] | None:
    if not isinstance(params, dict):
        return None
    amendment = params.get("proposedExecpolicyAmendment")
    if (
        not isinstance(amendment, list)
        or not amendment
        or not all(isinstance(part, str) and part.strip() for part in amendment)
    ):
        return None
    available = params.get("availableDecisions")
    if available is not None and (
        not isinstance(available, list)
        or "acceptWithExecpolicyAmendment" not in available
    ):
        return None
    return list(amendment)


class BridgeSession:
    """Own the Codex child and only the connection identifiers needed by UI."""

    def __init__(
        self,
        project_root: Path,
        client: CodexStdioClient,
        events: EventBuffer,
        *,
        mcp_backend: str = HIA_MCP_V2_BACKEND,
        focus_state_path: Path | None = None,
        project_team_state_path: Path | None = None,
    ) -> None:
        if mcp_backend not in {HIA_MCP_V2_BACKEND, FXHOUDINI_MCP_BACKEND}:
            raise ValueError(f"Unsupported Houdini MCP backend: {mcp_backend}")
        self._project_root = project_root
        self._focus_state_path = focus_state_path
        self._project_team_settings = ProjectTeamSettings(
            project_team_state_path
        )
        project_threads_state_path = (
            project_team_state_path.with_name("project-threads.json")
            if project_team_state_path is not None
            else None
        )
        thread_transfer_state_path = (
            project_team_state_path.with_name("thread-transfer.json")
            if project_team_state_path is not None
            else None
        )
        self._project_threads = ProjectThreadCoordinator(
            project_root,
            client,
            events,
            state_path=project_threads_state_path,
        )
        (
            self._focus_enabled_threads,
            self._focus_goal_bindings,
        ) = self._load_focus_state()
        self._client = client
        self._events = events
        self._mcp_backend = mcp_backend
        self._lock = threading.RLock()
        self._turn_condition = threading.Condition(self._lock)
        self._connected = False
        self._initialize_result: Any = None
        self._account_result: dict[str, Any] | None = None
        self._account_error: dict[str, Any] | None = None
        self._thread_id: str | None = None
        self._thread_permission_profile: str | None = None
        self._turn_id: str | None = None
        self._turn_status: str | None = None
        self._turn_active = False
        self._turn_created = False
        self._turn_generation = 0
        self._start_source_turn_id: str | None = None
        self._last_tool_name: str | None = None
        self._last_tool_status: str | None = None
        self._stop_recovery_thread: threading.Thread | None = None
        self._thread_runtime: dict[str, dict[str, Any]] = {}
        self._normal_transfers: set[str] = set()
        self._closed = False
        self._thread_transfer = ThreadTransferManager(
            client,
            events,
            descriptor=self._transfer_descriptor,
            rehydrate_descriptor=self._rehydrate_transfer_descriptor,
            begin=self._begin_thread_transfer,
            commit=self._commit_thread_transfer,
            rollback=self._rollback_thread_transfer,
            finish=self._finish_thread_transfer,
            state_path=thread_transfer_state_path,
        )
        self._project_threads.set_event_replay(self._on_client_event)
        self._client.set_event_sink(self._on_client_event)

    @property
    def client(self) -> CodexStdioClient:
        return self._client

    def start(self) -> dict[str, Any]:
        try:
            self._client.start()
            initialize_result = self._client.initialize()
            with self._lock:
                self._initialize_result = initialize_result
                self._connected = True
                self._thread_id = None
                self._thread_permission_profile = None
                self._write_focus_state_locked()
            try:
                account = self._client.request(
                    "account/read",
                    {"refreshToken": False},
                )
                with self._lock:
                    self._account_result = self._sanitize_account_result(account)
                    self._account_error = None
            except BridgeError as exc:
                with self._lock:
                    self._account_result = None
                    self._account_error = exc.to_dict()["structured_error"]
            self._project_threads.reconcile_restarted_goals()
            self._thread_transfer.recover_pending_deletes()
            self._thread_transfer.maybe_schedule_all()
            snapshot = self.snapshot()
            self._events.publish("session_state", session=snapshot)
            return snapshot
        except Exception:
            self._client.close()
            raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._client.close()
        with self._lock:
            self._connected = False

    def snapshot(self) -> dict[str, Any]:
        # Do not nest the Session lock with the project coordinator condition.
        # Project event handling can inspect Session state while the coordinator
        # is active, so identity is resolved between two short Session reads.
        while True:
            with self._lock:
                thread_id = self._thread_id
            project_identity = (
                self._project_threads.project_identity_for_thread(thread_id)
                if isinstance(thread_id, str)
                else None
            )
            with self._lock:
                if thread_id != self._thread_id:
                    continue
                account = copy.deepcopy(self._account_result)
                account_error = copy.deepcopy(self._account_error)
                connected = self._connected and self._client.is_running
                permission_profile = (
                    project_identity["project_role"]
                    if project_identity is not None
                    else (
                        "single"
                        if thread_id is not None
                        and self._thread_permission_profile == "single"
                        else None
                    )
                )
                return {
                "connected": connected,
                "mcp_backend": self._mcp_backend,
                "codex_pid": self._client.process_id,
                "authentication": self._authentication_status(account, account_error),
                "account": account,
                "account_error": account_error,
                "thread_id": thread_id,
                "permission_profile": permission_profile,
                "project_id": (
                    project_identity["project_id"]
                    if project_identity is not None
                    else None
                ),
                "project_role": (
                    project_identity["project_role"]
                    if project_identity is not None
                    else None
                ),
                "turn_id": self._turn_id,
                "turn_status": self._turn_status,
                "turn_active": self._turn_active,
                "focus_mode": self._focus_mode_locked(),
                "last_tool_name": self._last_tool_name,
                "last_tool_status": self._last_tool_status,
            }

    def _load_focus_state(self) -> tuple[set[str], dict[str, str]]:
        path = self._focus_state_path
        if path is None or not path.is_file():
            return set(), {}
        try:
            if path.stat().st_size > FOCUS_STATE_MAX_BYTES:
                return set(), {}
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            return set(), {}
        values = payload.get("enabled_thread_ids") if isinstance(payload, dict) else None
        raw_bindings = payload.get("goal_bindings") if isinstance(payload, dict) else None
        if not isinstance(values, list) or not isinstance(raw_bindings, dict):
            return set(), {}
        bindings = {
            thread_id: binding
            for thread_id, binding in raw_bindings.items()
            if (
                isinstance(thread_id, str)
                and self._identifier_is_valid(thread_id)
                and self._goal_binding_is_valid(binding)
                and thread_id in values
            )
        }
        return set(bindings), bindings

    def _focus_mode_locked(self, thread_id: str | None = None) -> bool:
        selected = thread_id if thread_id is not None else self._thread_id
        return (
            isinstance(selected, str)
            and selected in self._focus_enabled_threads
            and selected in self._focus_goal_bindings
        )

    @staticmethod
    def _goal_binding_is_valid(value: Any) -> bool:
        return isinstance(value, str) and GOAL_BINDING_PATTERN.fullmatch(value) is not None

    @classmethod
    def _goal_binding(cls, goal: dict[str, Any]) -> str | None:
        objective = goal.get("objective")
        token_budget = goal.get("tokenBudget")
        if (
            not isinstance(objective, str)
            or not objective.strip()
            or "\x00" in objective
            or len(objective) > GOAL_OBJECTIVE_MAX_LENGTH
            or (
                token_budget is not None
                and (
                    not isinstance(token_budget, int)
                    or isinstance(token_budget, bool)
                    or token_budget <= 0
                )
            )
        ):
            return None
        normalized = {
            "objective": objective.replace("\r\n", "\n").replace("\r", "\n").strip(),
            "token_budget": token_budget,
        }
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _disable_focus_locked(self, thread_id: str) -> bool:
        changed = (
            thread_id in self._focus_enabled_threads
            or thread_id in self._focus_goal_bindings
        )
        if changed:
            self._focus_enabled_threads.discard(thread_id)
            self._focus_goal_bindings.pop(thread_id, None)
            self._write_focus_state_locked()
        return changed

    def _reconcile_focus_goal(self, thread_id: str, goal: dict[str, Any] | None) -> bool:
        with self._lock:
            if not self._focus_mode_locked(thread_id):
                return False
            binding = (
                self._goal_binding(goal)
                if isinstance(goal, dict) and goal.get("status") == "active"
                else None
            )
            if binding != self._focus_goal_bindings.get(thread_id):
                self._disable_focus_locked(thread_id)
                return False
            return True

    def _write_focus_state_locked(self) -> None:
        path = self._focus_state_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            payload = {
                "version": 1,
                "active_thread_id": self._thread_id,
                "enabled_thread_ids": sorted(self._focus_enabled_threads),
                "goal_bindings": {
                    thread_id: self._focus_goal_bindings[thread_id]
                    for thread_id in sorted(self._focus_enabled_threads)
                    if thread_id in self._focus_goal_bindings
                },
            }
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as exc:
            raise BridgeError(
                "FOCUS_STATE_UNAVAILABLE",
                "Target focus mode could not be persisted inside the project",
                http_status=503,
            ) from exc

    def _developer_instructions(self) -> str:
        if self._mcp_backend == HIA_MCP_V2_BACKEND:
            backend_instructions = (
                "用 HIA MCP V2 与 HOM。已知小改回读目标值/连接后直改；"
                "hia_context 设 include_context_pack=false。"
                "仅非平凡或不确定改动用 hia_context/hia_inspect 读目标、输入输出、两层上游、"
                "公共控制、材质入口、引用和允许 scope。优先现有节点和标准原生节点网络；"
                "HOM 只做批量编排，场景内 Python SOP 或直接几何须说明必要性。"
                "复杂资产按主形体、结构、细节用少量语义批次；无子任务也不得并成全资产大批次；"
                "每批同一 scope 内一次 hia_execute_hom，"
                "随后 fresh cook、hia_scene_diff/hia_validate；"
                "Goal 按与用户完成声明和风险相称的场景证据验收；简单操作可用目标值或连接回读，"
                "plan/revision/tool completed 不算通过。"
                "Full蓝图列承诺、可识别特征和禁项，禁静默缩水；"
                "用户负约束必须原样保留并进入验收；禁止用语义等价或换皮替代绕过（如禁 Box 也禁盒状代用品），"
                "节点存在/tool success 不能证明几何要求通过。"
                "小型多部件装配也要量端点、宿主、接触、净空和穿插；截图/AABB/clean cook 不算精确证明。"
                "视觉任务只在里程碑用 hia_capture_viewport，不固定尺寸；未收到 image content 只能报视觉未验证，"
                "主形体仍像通用 primitive 拼装时继续建模，不得转入材质或声称完成；"
                "动画/模拟用代表帧或短序列；材质验收读取绑定、MaterialX 连接和正确输入。"
                "Stop 后已发 HOM 仍可能收尾；失败先读主错误和 rollback.status，禁止原样自动重试；"
                "真实 HOM 异常仅失败本次；按回滚证据在当前或下一 Goal 轮检查并用修正批次继续。"
                "验收证据中的 dirty、automatic_retry_safe=false、unknown/partial/NO_OBSERVED_EFFECT "
                "只报告，不Undo、不终止Goal；但 submission_state=unknown 且 hom_may_still_execute=true "
                "必须保持串行 barrier，直到当前或重连后的实时 hia_context 完成。"
                "源码更新提示，用加载版本；验新源码再重启。"
                "仅主任务串行调用 hia_*/HOM 写 HIP；子任务只研究、草拟、只读审阅；"
                "查询合批串行，QUEUE_FULL 不立即重试。"
                "checkpoint_label 仅用于 Goal 的有意义成功阶段。"
            )
        else:
            backend_instructions = (
                "当前场景的创建、修改、连接、材质和动画默认使用已注册的 FXHoudini MCP 与 HOM；"
                "复杂资产分主形体/结构/细节/材质；execute_python 每批仅一阶段/子系统，"
                "无子任务也禁全资产脚本；截图仍像通用 primitive 就继续修正。"
                "细粒度工具用于读取、单项修改和最终验证，不要逐节点循环。"
                "相同调用失败后先读真实错误再改用兼容方法；capture_screenshot 只做阶段性验证。"
                "模型行为约束：主代理负责当前 HIP 写入，子代理只做研究、草案和审阅；"
            )
        common_instructions = (
            "节点、参数、版本、复杂流程或失败原因不确定时，受影响写入前做一次本地知识批量检索并复用；"
            "复杂、参考驱动、材质、FX、模拟、渲染或版本不确定且资料会影响决定时，"
            "用 web/search 查当前 SideFX 与原始来源；没有网页工具时才批量只读抓取公开页。"
            "本地检索或 Context Pack 不可用只报告并继续，不得作为阻断；"
            "简单参数/连接/删除/重命名/布局不强制知识检索或网页研究。"
            "实时 MCP 不可用时直接说明，不得改成离线 HIP。"
            "只有用户明确要求离线、独立 HIP、批处理或后台渲染时才用 PATH 中的 hython.exe。"
            "普通场景请求不先搜索项目源码/文档；仅诊断或修改 Panel、Bridge、MCP/项目代码时读取。"
            "Houdini Full 发布详细用户可见蓝图；Supervisor 只带硬约束、"
            "完整当前阶段卡和最新证据，禁回塞全蓝图；按需查看子任务并公开采纳。"
            "上下文仅用 app-server 自动整理，不手动 compact，不创建本地摘要或记忆。"
            "实时代码禁止 hou.hipFile.clear/load/save，不替换当前场景；新资产放入唯一新根。"
            "不要调用 request_user_input；信息不足时采用合理默认值，无法执行才报告原因。"
            "安全已保存 HIP 的自动截图优先写同级 .hia/screenshots，否则回退 HIA_CACHE_DIR/screenshots；"
            "预览写 previews，中间图写 tmp，附件/知识/模型/索引仍留项目 .runtime；文件名加时间戳和短随机后缀。"
            "用户明确指定的最终渲染、EXR、视频、USD、模拟缓存或导出是用户交付物，"
            "可写所选普通本地项目外目录；未指定才用 HIA_RENDER_OUTPUT_DIR，并始终报告最终路径。"
            "禁止屏幕接管。"
        )
        if self._mcp_backend == HIA_MCP_V2_BACKEND:
            common_instructions = (
                "节点、参数、版本、复杂流程或失败原因不确定时，受影响写入前用 hia_local_help_search 一次合批并复用；"
                "复杂、参考驱动、材质、FX、模拟、渲染或版本不确定且资料会影响决定时，"
                "用 web/search 查当前 SideFX 与原始来源；"
                "检索/Context Pack 不可用只报告，不阻断；"
                "简单参数/连接/删除/重命名/布局不强制知识检索或网页研究。"
                "MCP 不可用就说明，不转离线 HIP；hython 仅用于用户明确的离线/独立 HIP/批处理/后台渲染。"
                "普通场景不查项目源码。"
                "Houdini Full 发布详细用户可见蓝图；Supervisor 只带硬约束、"
                "完整当前阶段卡和最新证据，禁回塞全蓝图。"
                "禁止 hou.hipFile.clear/load/save 和替换当前场景；新资产置于唯一新根。"
                "不调用 request_user_input；信息不足合理默认，无法执行才报告。"
                "安全已存 HIP 截图写同级 .hia/screenshots，否则用 HIA_CACHE_DIR/screenshots；"
                "预览写 previews，中间图写 tmp，附件/知识/模型/索引留 .runtime。"
                "用户指定的最终渲染/EXR/视频/USD/模拟缓存/导出可写所选普通项目外目录；"
                "否则用 HIA_RENDER_OUTPUT_DIR；始终报告最终路径。禁止屏幕接管。"
            )
        return backend_instructions + common_instructions

    def _permission_profile(self, thread_id: str) -> str:
        role = self._project_threads.role_for_thread(thread_id)
        if role is not None:
            return role
        # The persistent mode chooses only the next newly-created entry.  An
        # existing non-project Thread must never be converted into a project
        # root merely because that default changed later.
        return "single"

    def _thread_profile_params(
        self,
        profile: str,
        *,
        service_tier: str | None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        read_only = profile in PROJECT_READ_ONLY_ROLES
        params: dict[str, Any] = {
            "cwd": str(self._project_root),
            "approvalPolicy": "never" if read_only else "on-request",
            "approvalsReviewer": "user",
            "sandbox": "read-only" if read_only else "workspace-write",
            "ephemeral": False,
            "developerInstructions": (
                self._developer_instructions()
                if profile == "single"
                else role_instructions(profile)
            ),
            "serviceTier": service_tier,
        }
        params["config"] = (
            project_role_config(read_only=read_only)
            if profile in PROJECT_ROLE_ORDER
            else dict(PROJECT_HIA_ENABLE_CONFIG)
        )
        if thread_id is not None:
            params["threadId"] = thread_id
            params.pop("ephemeral", None)
        return params

    def _transfer_descriptor(self, thread_id: str) -> dict[str, Any] | None:
        project = self._project_threads.transfer_descriptor(thread_id)
        if project is not None:
            with self._lock:
                project.update(
                    profile=project["role"],
                    selected=self._thread_id == thread_id,
                    focus_enabled=thread_id in self._focus_enabled_threads,
                )
                binding = self._focus_goal_bindings.get(thread_id)
                if binding is not None:
                    project["focus_binding"] = binding
            return project
        with self._lock:
            runtime = copy.deepcopy(self._thread_runtime.get(thread_id))
        if runtime is None:
            return None
        profile = runtime.get("profile", "single")
        params = self._thread_profile_params(
            profile,
            service_tier=runtime.get("service_tier"),
        )
        return {
            "thread_id": thread_id,
            "title": runtime.get("title"),
            "model": runtime.get("model"),
            "effort": runtime.get("effort"),
            "service_tier": runtime.get("service_tier"),
            "cwd": params["cwd"],
            "approval_policy": params["approvalPolicy"],
            "approvals_reviewer": params["approvalsReviewer"],
            "sandbox": params["sandbox"],
            "developer_instructions": params["developerInstructions"],
            "config": params.get("config"),
            "thread_source": runtime.get("thread_source"),
            "profile": profile,
            "selected": self._thread_id == thread_id,
            "focus_enabled": thread_id in self._focus_enabled_threads,
            **(
                {"focus_binding": self._focus_goal_bindings[thread_id]}
                if thread_id in self._focus_goal_bindings
                else {}
            ),
        }

    def _rehydrate_transfer_descriptor(
        self, persisted: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        descriptor = copy.deepcopy(dict(persisted))
        profile = descriptor.get("profile")
        if profile not in {*PROJECT_ROLE_ORDER, "single"}:
            raise BridgeError(
                "THREAD_TRANSFER_RECOVERY_INVALID",
                "Persisted Thread profile is not supported",
                http_status=502,
            )
        expected = self._thread_profile_params(
            profile,
            service_tier=descriptor.get("service_tier"),
        )
        for descriptor_key, param_key in (
            ("cwd", "cwd"),
            ("approval_policy", "approvalPolicy"),
            ("approvals_reviewer", "approvalsReviewer"),
            ("sandbox", "sandbox"),
            ("config", "config"),
        ):
            if descriptor.get(descriptor_key) != expected.get(param_key):
                raise BridgeError(
                    "THREAD_TRANSFER_RECOVERY_INVALID",
                    f"Persisted {descriptor_key} no longer matches its Thread profile",
                    http_status=502,
                )
        descriptor["developer_instructions"] = expected["developerInstructions"]
        return descriptor

    def _begin_thread_transfer(self, thread_id: str) -> bool:
        if self._project_threads.role_for_thread(thread_id) is not None:
            return self._project_threads.begin_thread_transfer(thread_id)
        with self._lock:
            if thread_id not in self._thread_runtime or thread_id in self._normal_transfers:
                return False
            if self._thread_id == thread_id and self._turn_active:
                return False
            self._normal_transfers.add(thread_id)
            return True

    def _commit_thread_transfer(
        self,
        old_thread_id: str,
        new_thread_id: str,
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        project = self._project_threads.transfer_descriptor(old_thread_id)
        new_project = self._project_threads.transfer_descriptor(new_thread_id)
        project_metadata: dict[str, Any] = {}
        if project is not None:
            project_metadata = self._project_threads.replace_thread_id(
                old_thread_id, new_thread_id
            )
        elif new_project is not None:
            project_metadata = {
                key: new_project[key]
                for key in ("project_id", "role")
                if isinstance(new_project.get(key), str)
            }
        with self._lock:
            old_runtime = self._thread_runtime.pop(old_thread_id, None)
            previous_selected = self._thread_id
            old_focus = old_thread_id in self._focus_enabled_threads
            old_binding = self._focus_goal_bindings.pop(old_thread_id, None)
            previous_new_runtime = self._thread_runtime.get(new_thread_id)
            restored_runtime = old_runtime or {
                "profile": descriptor.get("profile", "single"),
                "model": descriptor.get("model"),
                "effort": descriptor.get("effort"),
                "service_tier": descriptor.get("service_tier"),
                "title": descriptor.get("title"),
                "thread_source": descriptor.get("thread_source"),
            }
            self._thread_runtime[new_thread_id] = restored_runtime
            if previous_selected == old_thread_id or descriptor.get("selected") is True:
                self._thread_id = new_thread_id
            restore_focus = old_focus or descriptor.get("focus_enabled") is True
            if restore_focus:
                self._focus_enabled_threads.discard(old_thread_id)
                self._focus_enabled_threads.add(new_thread_id)
            restored_binding = old_binding or descriptor.get("focus_binding")
            if isinstance(restored_binding, str):
                self._focus_goal_bindings[new_thread_id] = restored_binding
            try:
                self._write_focus_state_locked()
            except Exception:
                self._thread_runtime.pop(new_thread_id, None)
                if previous_new_runtime is not None:
                    self._thread_runtime[new_thread_id] = previous_new_runtime
                if old_runtime is not None:
                    self._thread_runtime[old_thread_id] = old_runtime
                self._thread_id = previous_selected
                self._focus_enabled_threads.discard(new_thread_id)
                if old_focus:
                    self._focus_enabled_threads.add(old_thread_id)
                self._focus_goal_bindings.pop(new_thread_id, None)
                if old_binding is not None:
                    self._focus_goal_bindings[old_thread_id] = old_binding
                if project is not None:
                    self._project_threads.replace_thread_id(
                        new_thread_id, old_thread_id
                    )
                raise
        return project_metadata

    def _rollback_thread_transfer(
        self,
        new_thread_id: str,
        old_thread_id: str,
        descriptor: Mapping[str, Any],
    ) -> None:
        self._commit_thread_transfer(new_thread_id, old_thread_id, descriptor)

    def _finish_thread_transfer(self, *thread_ids: str) -> None:
        self._project_threads.finish_thread_transfer(*thread_ids)
        with self._lock:
            self._normal_transfers.difference_update(thread_ids)

    @staticmethod
    def _sanitize_account_result(account_result: Any) -> dict[str, Any]:
        if not isinstance(account_result, dict):
            return {}
        account = account_result.get("account")
        sanitized_account = None
        if isinstance(account, dict):
            sanitized_account = {
                key: account[key]
                for key in ("type", "planType", "credentialSource")
                if key in account
            }
        return {
            "requiresOpenaiAuth": account_result.get("requiresOpenaiAuth") is True,
            "account": sanitized_account,
        }

    @staticmethod
    def _authentication_status(
        account_result: dict[str, Any] | None,
        account_error: dict[str, Any] | None,
    ) -> str:
        if account_error is not None:
            return "account_error"
        if isinstance(account_result, dict):
            if isinstance(account_result.get("account"), dict):
                return "authenticated"
            if account_result.get("requiresOpenaiAuth") is True:
                return "login_required"
        return "unavailable"

    def start_thread(
        self,
        model: str | None = None,
        service_tier: str | None = None,
        team_override: str | None = None,
    ) -> dict[str, Any]:
        model = self._validated_optional_selection(
            model,
            "model",
            MODEL_IDENTIFIER_MAX_LENGTH,
        )
        service_tier = self._validated_optional_selection(
            service_tier,
            "service_tier",
            SERVICE_TIER_MAX_LENGTH,
        )
        if team_override is not None and team_override not in PROJECT_TEAM_MODES:
            raise BridgeError(
                "INVALID_TEAM_OVERRIDE",
                "team_override must be single or team",
                details={"field": "team_override"},
            )
        with self._lock:
            self._require_no_active_turn_locked()
        routing = team_override or self._project_team_settings.mode()
        profile = "supervisor" if routing == "team" else "single"
        params = self._thread_profile_params(profile, service_tier=service_tier)
        if model is not None:
            params["model"] = model
        result = self._client.request("thread/start", params)
        thread_id = self._extract_thread_id(result)
        with self._lock:
            self._thread_id = thread_id
            self._thread_permission_profile = profile
            thread = result.get("thread") if isinstance(result, dict) else None
            self._thread_runtime[thread_id] = {
                "profile": profile,
                "model": result.get("model", model) if isinstance(result, dict) else model,
                "effort": result.get("reasoningEffort") if isinstance(result, dict) else None,
                "service_tier": (
                    result.get("serviceTier", service_tier)
                    if isinstance(result, dict)
                    else service_tier
                ),
                "title": thread.get("name") if isinstance(thread, dict) else None,
                "thread_source": (
                    thread.get("threadSource") if isinstance(thread, dict) else None
                ),
            }
            self._reset_turn_locked()
            self._write_focus_state_locked()
        self._events.publish("thread_selected", action="start", thread_id=thread_id)
        return {
            "thread_id": thread_id,
            "focus_mode": False,
            "routing": routing,
            "result": result,
        }

    def resume_thread(
        self,
        thread_id: str,
        service_tier: str | None = None,
    ) -> dict[str, Any]:
        thread_id = self._validated_identifier(thread_id, "thread_id")
        service_tier = self._validated_optional_selection(
            service_tier,
            "service_tier",
            SERVICE_TIER_MAX_LENGTH,
        )
        with self._lock:
            selected_project_role = self._project_threads.role_for_thread(
                self._thread_id
            )
            if not (self._turn_active and selected_project_role is not None):
                self._require_no_active_turn_locked()
            if thread_id in self._normal_transfers:
                raise BridgeError(
                    "THREAD_TRANSFER_ACTIVE",
                    "This Thread is transferring after automatic compaction",
                    http_status=409,
                )
        if self._project_threads.thread_transfer_in_progress(thread_id):
            raise BridgeError(
                "THREAD_TRANSFER_ACTIVE",
                "This project role is transferring after automatic compaction",
                http_status=409,
            )
        profile = self._permission_profile(thread_id)
        resumed = self._client.request(
            "thread/resume",
            self._thread_profile_params(
                profile,
                service_tier=service_tier,
                thread_id=thread_id,
            ),
        )
        resolved_id = self._extract_thread_id(resumed)
        read_result = self._project_thread_messages(resumed, resolved_id)
        project_turn = self._project_threads.turn_state_for_thread(resolved_id)
        with self._lock:
            self._thread_id = resolved_id
            self._thread_permission_profile = profile
            thread = resumed.get("thread") if isinstance(resumed, dict) else None
            previous = self._thread_runtime.get(resolved_id, {})
            self._thread_runtime[resolved_id] = {
                "profile": profile,
                "model": (
                    resumed.get("model", previous.get("model"))
                    if isinstance(resumed, dict)
                    else previous.get("model")
                ),
                "effort": (
                    resumed.get("reasoningEffort", previous.get("effort"))
                    if isinstance(resumed, dict)
                    else previous.get("effort")
                ),
                "service_tier": (
                    resumed.get("serviceTier", service_tier or previous.get("service_tier"))
                    if isinstance(resumed, dict)
                    else service_tier or previous.get("service_tier")
                ),
                "title": (
                    thread.get("name", previous.get("title"))
                    if isinstance(thread, dict)
                    else previous.get("title")
                ),
                "thread_source": (
                    thread.get("threadSource", previous.get("thread_source"))
                    if isinstance(thread, dict)
                    else previous.get("thread_source")
                ),
            }
            self._reset_turn_locked()
            if project_turn["turn_active"]:
                self._turn_active = True
                self._turn_id = project_turn["turn_id"]
                self._turn_status = project_turn["turn_status"]
                self._turn_created = project_turn["turn_id"] is not None
            self._write_focus_state_locked()
        self._thread_transfer.maybe_schedule_all()
        self._events.publish("thread_selected", action="resume", thread_id=resolved_id)
        project_identity = self._project_threads.project_identity_for_thread(
            resolved_id
        )
        return {
            "thread_id": resolved_id,
            "permission_profile": (
                project_identity["project_role"]
                if project_identity is not None
                else "single"
            ),
            "project_id": (
                project_identity["project_id"]
                if project_identity is not None
                else None
            ),
            "project_role": (
                project_identity["project_role"]
                if project_identity is not None
                else None
            ),
            "focus_mode": self._focus_mode_locked(resolved_id),
            "read": read_result,
            **project_turn,
        }

    def read_thread(self, thread_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            selected = thread_id or self._thread_id
        selected = self._validated_identifier(selected, "thread_id")
        result = self._client.request(
            "thread/read",
            {"threadId": selected, "includeTurns": True},
        )
        return {
            "thread_id": selected,
            "result": self._project_thread_messages(result, selected),
        }

    def list_threads(self) -> dict[str, Any]:
        """Return one recent page for this project without local persistence."""

        result = self._client.request(
            "thread/list",
            {
                "cwd": _thread_cwd_filters(str(self._project_root)),
                "archived": False,
                "limit": THREAD_LIST_LIMIT,
                "modelProviders": [],
                "useStateDbOnly": True,
                "sortKey": "recency_at",
                "sortDirection": "desc",
            },
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise self._invalid_thread_response("Response data must be an array")

        project_root = _normalized_thread_cwd(str(self._project_root))
        threads: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in result["data"][:THREAD_LIST_LIMIT]:
            if not isinstance(entry, dict):
                raise self._invalid_thread_response("Thread entry must be an object")
            if self._project_threads.owns_thread_record(entry):
                continue
            thread_id = self._validated_thread_response_string(
                entry.get("id"), "id", MODEL_IDENTIFIER_MAX_LENGTH, allow_empty=False
            )
            cwd = self._validated_thread_response_string(
                entry.get("cwd"), "cwd", THREAD_CWD_MAX_LENGTH, allow_empty=False
            )
            entry_root = _normalized_thread_cwd(cwd)
            if entry_root != project_root:
                continue
            if thread_id in seen:
                raise self._invalid_thread_response(
                    "Response contains a duplicate thread id", field="id"
                )
            seen.add(thread_id)

            raw_name = entry.get("name")
            name = None
            if raw_name is not None:
                name = self._validated_thread_response_string(
                    raw_name,
                    "name",
                    THREAD_NAME_MAX_LENGTH,
                    allow_empty=True,
                )
            preview = self._sanitized_thread_preview(entry.get("preview"))
            updated_at = entry.get("updatedAt")
            if not isinstance(updated_at, int) or isinstance(updated_at, bool) or updated_at < 0:
                raise self._invalid_thread_response(
                    "Thread updatedAt must be a non-negative integer",
                    field="updatedAt",
                )
            record: dict[str, Any] = {
                "thread_id": thread_id,
                "name": name,
                "preview": preview,
                "updated_at": updated_at,
            }
            recency_at = entry.get("recencyAt")
            if recency_at is not None:
                if (
                    not isinstance(recency_at, int)
                    or isinstance(recency_at, bool)
                    or recency_at < 0
                ):
                    raise self._invalid_thread_response(
                        "Thread recencyAt must be a non-negative integer or null",
                        field="recencyAt",
                    )
                record["recency_at"] = recency_at
            threads.append(record)
        return {"threads": threads}

    def project_team_snapshot(self) -> dict[str, Any]:
        """Return the single authoritative Panel project snapshot."""

        settings = self._project_team_settings.snapshot()
        return self._project_threads.snapshot(
            mode=self._project_team_settings.mode(),
            writable=True,
            settings_state_status=settings["state_status"],
        )

    def update_project_team_mode(
        self,
        *,
        mode: Any,
    ) -> dict[str, Any]:
        """Persist only the project routing preference."""

        with self._lock:
            result = self._project_team_settings.set_mode(mode)
            self._project_threads.note_settings_change()
        self._events.publish(
            "project_team_updated",
            mode=result["settings"]["mode"],
            state_status=result["state_status"],
        )
        return self.project_team_snapshot()

    def append_project_guidance(
        self,
        *,
        project_id: Any,
        thread_id: Any,
        text: Any,
        model: Any = None,
        effort: Any = None,
        service_tier: Any = None,
        local_image_paths: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(text, str):
            raise BridgeError(
                "EMPTY_INPUT",
                "Natural-language input must not be empty",
            )
        if len(text) > 65_536:
            raise BridgeError(
                "INPUT_TOO_LARGE",
                "Input exceeds the 65536 character limit",
            )
        thread_id = self._validated_identifier(thread_id, "thread_id")
        image_paths = self._validated_local_image_paths(
            local_image_paths,
            thread_id,
        )
        if not text.strip() and not image_paths:
            raise BridgeError(
                "EMPTY_INPUT",
                "Natural-language input or at least one image is required",
            )
        accepted = self._project_threads.append_guidance(
            project_id,
            thread_id,
            text,
            model=model,
            effort=effort,
            service_tier=service_tier,
            local_image_paths=tuple(image_paths),
        )
        return {
            **accepted,
            "project_team": self.project_team_snapshot(),
        }

    def append_active_supervisor_guidance(
        self,
        *,
        text: Any,
        project_id: Any = None,
        model: Any = None,
        effort: Any = None,
        service_tier: Any = None,
        local_image_paths: Any = None,
    ) -> dict[str, Any]:
        """Append a user delta to the unique active project's Supervisor."""

        target = self._project_threads.active_supervisor_target(project_id)
        if not isinstance(text, str):
            raise BridgeError("EMPTY_INPUT", "Natural-language input must not be empty")
        if len(text) > 65_536:
            raise BridgeError("INPUT_TOO_LARGE", "Input exceeds the 65536 character limit")
        image_paths = self._validated_local_image_paths(
            local_image_paths,
            target["thread_id"],
        )
        if not text.strip() and not image_paths:
            raise BridgeError(
                "EMPTY_INPUT",
                "Natural-language input or at least one image is required",
            )
        accepted = self._project_threads.accept_supervisor_guidance(
            target["project_id"],
            text,
            model=model,
            effort=effort,
            service_tier=service_tier,
            local_image_paths=tuple(image_paths),
        )
        return {
            **accepted,
            "guidance_target": target,
            "project_team": self.project_team_snapshot(),
        }

    def rename_thread(self, thread_id: str, name: str) -> dict[str, Any]:
        thread_id = self._validated_identifier(thread_id, "thread_id")
        with self._lock:
            if thread_id in self._normal_transfers:
                raise BridgeError(
                    "THREAD_TRANSFER_ACTIVE",
                    "This Thread is transferring after automatic compaction",
                    http_status=409,
                )
        if self._project_threads.thread_transfer_in_progress(thread_id):
            raise BridgeError(
                "THREAD_TRANSFER_ACTIVE",
                "This project role is transferring after automatic compaction",
                http_status=409,
            )
        name = self._validated_optional_selection(
            name, "thread_name", THREAD_NAME_MAX_LENGTH
        )
        if name is None:
            raise BridgeError("INVALID_THREAD_NAME", "thread_name is required")
        result = self._client.request(
            "thread/name/set", {"threadId": thread_id, "name": name}
        )
        with self._lock:
            runtime = self._thread_runtime.get(thread_id)
            if runtime is not None:
                runtime["title"] = name
        return {"thread_id": thread_id, "name": name, "result": result}

    def delete_thread(self, thread_id: str) -> dict[str, Any]:
        """Permanently delete one explicitly selected Codex Thread."""

        thread_id = self._validated_identifier(thread_id, "thread_id")
        with self._lock:
            recovery_worker = self._stop_recovery_thread
            if self._turn_status == "stopRecovering" or (
                recovery_worker is not None and recovery_worker.is_alive()
            ):
                raise BridgeError(
                    "STOP_RECOVERY_IN_PROGRESS",
                    "The selected Thread is still recovering after Stop",
                    http_status=409,
                    details={
                        "thread_id": self._thread_id,
                        "turn_status": self._turn_status,
                    },
                )
            self._require_no_active_turn_locked()
            if thread_id in self._normal_transfers:
                raise BridgeError(
                    "THREAD_TRANSFER_ACTIVE",
                    "This Thread is transferring after automatic compaction",
                    http_status=409,
                )
        if self._project_threads.thread_transfer_in_progress(thread_id):
            raise BridgeError(
                "THREAD_TRANSFER_ACTIVE",
                "This project role is transferring after automatic compaction",
                http_status=409,
            )

        result = self._client.request(
            "thread/delete",
            {"threadId": thread_id},
        )

        cleanup_warning: dict[str, Any] | None = None
        with self._lock:
            was_selected = self._thread_id == thread_id
            self._focus_enabled_threads.discard(thread_id)
            self._focus_goal_bindings.pop(thread_id, None)
            if was_selected:
                self._thread_id = None
                self._thread_permission_profile = None
                self._reset_turn_locked()
            self._thread_runtime.pop(thread_id, None)
            try:
                self._write_focus_state_locked()
            except BridgeError as exc:
                cleanup_warning = exc.to_dict()["structured_error"]
        event_fields: dict[str, Any] = {
            "thread_id": thread_id,
            "was_selected": was_selected,
        }
        response: dict[str, Any] = {
            "thread_id": thread_id,
            "deleted": True,
            "was_selected": was_selected,
            "result": result,
        }
        if cleanup_warning is not None:
            event_fields["cleanup_warning"] = cleanup_warning
            response["cleanup_warning"] = cleanup_warning
        self._events.publish("thread_deleted", **event_fields)
        return response

    def get_goal(self, expected_thread_id: str) -> dict[str, Any]:
        thread_id = self._selected_thread_id(expected_thread_id)
        result = self._client.request(
            "thread/goal/get",
            {"threadId": thread_id},
        )
        self._selected_thread_id(thread_id)
        goal = self._project_goal(result, thread_id, allow_none=True)
        focused = self._reconcile_focus_goal(thread_id, goal)
        with self._lock:
            goal_binding = (
                self._focus_goal_bindings.get(thread_id) if focused else None
            )
        return {
            "thread_id": thread_id,
            "goal": goal,
            "focus_mode": focused,
            "goal_binding": goal_binding,
        }

    def set_goal(
        self,
        *,
        expected_thread_id: str,
        objective: str,
        status: str,
        token_budget: int | None,
    ) -> dict[str, Any]:
        thread_id = self._selected_thread_id(expected_thread_id)
        if (
            not isinstance(objective, str)
            or not objective.strip()
            or len(objective) > GOAL_OBJECTIVE_MAX_LENGTH
        ):
            raise BridgeError("INVALID_GOAL", "Goal objective is invalid")
        if status not in GOAL_STATUSES:
            raise BridgeError("INVALID_GOAL", "Goal status is invalid")
        if token_budget is not None and (
            not isinstance(token_budget, int)
            or isinstance(token_budget, bool)
            or token_budget <= 0
        ):
            raise BridgeError(
                "INVALID_GOAL",
                "Goal token_budget must be a positive integer or null",
            )
        params = {
            "threadId": thread_id,
            "objective": objective,
            "status": status,
            "tokenBudget": token_budget,
        }
        result = self._client.request("thread/goal/set", params)
        self._selected_thread_id(thread_id)
        goal = self._project_goal(result, thread_id, allow_none=False)
        self._reconcile_focus_goal(thread_id, goal)
        return {
            "thread_id": thread_id,
            "goal": goal,
            "focus_mode": self._focus_mode_for_thread(thread_id),
        }

    def clear_goal(self, expected_thread_id: str) -> dict[str, Any]:
        thread_id = self._selected_thread_id(expected_thread_id)
        result = self._client.request(
            "thread/goal/clear",
            {"threadId": thread_id},
        )
        cleared = result.get("cleared") if isinstance(result, dict) else None
        if not isinstance(cleared, bool):
            raise self._invalid_goal_response(
                "Goal clear response must contain a boolean cleared field",
                field="cleared",
            )
        self._selected_thread_id(thread_id)
        if cleared:
            with self._lock:
                self._disable_focus_locked(thread_id)
        return {
            "thread_id": thread_id,
            "cleared": cleared,
            "focus_mode": self._focus_mode_for_thread(thread_id),
        }

    def _focus_mode_for_thread(self, thread_id: str) -> bool:
        with self._lock:
            return self._focus_mode_locked(thread_id)

    def set_focus_mode(
        self,
        expected_thread_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise BridgeError(
                "INVALID_FOCUS_MODE",
                "Target focus mode enabled must be a boolean",
            )
        thread_id = self._selected_thread_id(expected_thread_id)
        if enabled:
            result = self._client.request(
                "thread/goal/get",
                {"threadId": thread_id},
            )
            goal = self._project_goal(result, thread_id, allow_none=True)
            if goal is None or goal.get("status") != "active":
                raise BridgeError(
                    "ACTIVE_GOAL_REQUIRED",
                    "Target focus mode requires an active Codex Goal",
                    http_status=409,
                )
            goal_binding = self._goal_binding(goal)
            if goal_binding is None:
                raise self._invalid_goal_response(
                    "Goal stable fields are invalid",
                    field="tokenBudget",
                )
        self._selected_thread_id(thread_id)
        with self._lock:
            if enabled:
                self._focus_enabled_threads.add(thread_id)
                self._focus_goal_bindings[thread_id] = goal_binding
            else:
                self._focus_enabled_threads.discard(thread_id)
                self._focus_goal_bindings.pop(thread_id, None)
            self._write_focus_state_locked()
        return {
            "thread_id": thread_id,
            "focus_mode": enabled,
        }

    def list_models(self) -> dict[str, Any]:
        """Return a bounded, sanitized catalog of non-hidden Codex models."""

        models: list[dict[str, Any]] = []
        seen_models: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        raw_entry_count = 0

        for page_number in range(1, MODEL_LIST_MAX_PAGES + 1):
            params: dict[str, Any] = {
                "includeHidden": False,
                "limit": MODEL_LIST_PAGE_SIZE,
            }
            if cursor is not None:
                params["cursor"] = cursor
            response = self._client.request("model/list", params)
            if not isinstance(response, dict):
                raise self._invalid_model_response("Response root must be an object")
            data = response.get("data")
            if not isinstance(data, list):
                raise self._invalid_model_response("Response data must be an array")

            raw_entry_count += len(data)
            if raw_entry_count > MODEL_LIST_MAX_ENTRIES:
                raise BridgeError(
                    "MODEL_CATALOG_LIMIT_EXCEEDED",
                    "Codex model catalog exceeded the Bridge entry limit",
                    http_status=502,
                    details={"max_entries": MODEL_LIST_MAX_ENTRIES},
                )
            for entry in data:
                sanitized = self._sanitize_model_entry(entry)
                if sanitized is None:
                    continue
                model_id = sanitized["model"]
                if model_id in seen_models:
                    raise self._invalid_model_response(
                        "Response contains a duplicate model identifier",
                        field="model",
                    )
                seen_models.add(model_id)
                models.append(sanitized)

            next_cursor = response.get("nextCursor")
            if next_cursor is None:
                return {"models": models}
            next_cursor = self._validated_response_string(
                next_cursor,
                "nextCursor",
                MODEL_CURSOR_MAX_LENGTH,
                allow_empty=False,
            )
            if next_cursor in seen_cursors:
                raise self._invalid_model_response(
                    "Response contains a repeated pagination cursor",
                    field="nextCursor",
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor

            if page_number == MODEL_LIST_MAX_PAGES:
                raise BridgeError(
                    "MODEL_CATALOG_LIMIT_EXCEEDED",
                    "Codex model catalog exceeded the Bridge page limit",
                    http_status=502,
                    details={"max_pages": MODEL_LIST_MAX_PAGES},
                )

        raise AssertionError("unreachable model pagination state")

    def _recover_project_root_start(
        self, project_spec: Mapping[str, Any] | None
    ) -> str | None:
        if not isinstance(project_spec, Mapping):
            return None
        project_id = project_spec.get("project_id")
        if not isinstance(project_id, str):
            return None
        try:
            return self._project_threads.recover_root_turn_request(project_id)
        except Exception:
            return None

    def start_turn(
        self,
        text: str,
        model: str | None = None,
        effort: str | None = None,
        local_image_paths: list[str] | None = None,
        service_tier: str | None = None,
        team_override: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(text, str):
            raise BridgeError("EMPTY_INPUT", "Natural-language input must not be empty")
        if len(text) > 65536:
            raise BridgeError("INPUT_TOO_LARGE", "Input exceeds the 65536 character limit")
        model = self._validated_optional_selection(
            model, "model", MODEL_IDENTIFIER_MAX_LENGTH
        )
        effort = self._validated_optional_selection(
            effort, "effort", REASONING_EFFORT_MAX_LENGTH
        )
        service_tier = self._validated_optional_selection(
            service_tier, "service_tier", SERVICE_TIER_MAX_LENGTH
        )
        if team_override is not None and team_override not in PROJECT_TEAM_MODES:
            raise BridgeError(
                "INVALID_TEAM_OVERRIDE",
                "team_override must be single or team",
                details={"field": "team_override"},
            )

        with self._lock:
            thread_id = self._validated_identifier(self._thread_id, "thread_id")
            if thread_id in self._normal_transfers:
                raise BridgeError(
                    "THREAD_TRANSFER_ACTIVE",
                    "This Thread is transferring after automatic compaction",
                    http_status=409,
                )
            project_identity = self._project_threads.project_identity_for_thread(
                thread_id
            )
            if project_identity is not None:
                raise BridgeError(
                    "PROJECT_THREAD_GUIDANCE_REQUIRED",
                    "Use append_guidance for a Thread that belongs to a project",
                    http_status=409,
                    details={
                        "project_id": project_identity["project_id"],
                        "project_role": project_identity["project_role"],
                        "thread_id": thread_id,
                        "turn_id": self._turn_id,
                        "turn_active": self._turn_active,
                        "turn_status": self._turn_status,
                    },
                )
            if self._project_threads.workflow_active():
                raise BridgeError(
                    "PROJECT_THREAD_WORKFLOW_ACTIVE",
                    "The project executor has exclusive scene-write ownership",
                    http_status=409,
                )
            profile = self._thread_permission_profile or self._permission_profile(
                thread_id
            )
            current_routing = "team" if profile == "supervisor" else "single"
            requested_routing = team_override or current_routing
            if requested_routing != current_routing:
                raise BridgeError(
                    "THREAD_ROUTING_MISMATCH",
                    "This Thread was created for a different routing type; create a new entry instead",
                    http_status=409,
                    details={
                        "field": "team_override",
                        "current_routing": current_routing,
                        "requested_routing": requested_routing,
                    },
                )
            routing = current_routing
            image_paths = self._validated_local_image_paths(
                local_image_paths, thread_id
            )
            if not text.strip() and not image_paths:
                raise BridgeError(
                    "EMPTY_INPUT",
                    "Natural-language input or at least one image is required",
                )
            self._require_no_active_turn_locked()
            self._turn_generation += 1
            generation = self._turn_generation
            self._start_source_turn_id = self._turn_id or self._start_source_turn_id
            self._turn_id = None
            self._turn_status = "starting"
            self._turn_active = True
            self._turn_created = False

        project_spec: dict[str, Any] | None = None
        turn_model, turn_effort, turn_tier = model, effort, service_tier
        if routing == "team":
            task = (
                text
                if text.strip()
                else "Complete the Houdini task described by the attached reference images."
            )
            with self._lock:
                root_runtime = copy.deepcopy(self._thread_runtime.get(thread_id, {}))
            project_model = model if model is not None else root_runtime.get("model")
            project_effort = effort if effort is not None else root_runtime.get("effort")
            project_tier = (
                service_tier
                if service_tier is not None
                else root_runtime.get("service_tier")
            )
            turn_model, turn_effort, turn_tier = (
                project_model,
                project_effort,
                project_tier,
            )
            try:
                project_spec = self._project_threads.prepare_project(
                    thread_id,
                    task,
                    model=project_model,
                    effort=project_effort,
                    service_tier=project_tier,
                    local_image_paths=tuple(image_paths),
                )
            except Exception:
                with self._lock:
                    if generation == self._turn_generation and not self._turn_created:
                        self._turn_active = False
                        self._turn_id = None
                        self._turn_status = None
                        self._start_source_turn_id = None
                raise

        try:
            turn_input: list[dict[str, Any]] = []
            if project_spec is not None:
                turn_input.append(
                    {
                        "type": "text",
                        "text": supervisor_intake_prompt(task),
                        "text_elements": [],
                    }
                )
            elif text.strip():
                turn_input.append(
                    {
                        "type": "text",
                        "text": text,
                        "text_elements": [],
                    }
                )
            turn_input.extend(
                {"type": "localImage", "path": path} for path in image_paths
            )
            read_only = routing == "team"
            params: dict[str, Any] = {
                "threadId": thread_id,
                "input": turn_input,
                "cwd": str(self._project_root),
                "approvalPolicy": "never" if read_only else "on-request",
                "sandboxPolicy": (
                    {"type": "readOnly", "networkAccess": False}
                    if read_only
                    else {"type": "workspaceWrite", "networkAccess": False}
                ),
                "serviceTier": turn_tier,
            }
            if project_spec is not None:
                params["outputSchema"] = project_spec["output_schema"]
            if turn_model is not None:
                params["model"] = turn_model
            if turn_effort is not None:
                params["effort"] = turn_effort
            if project_spec is not None:
                self._project_threads.begin_root_turn_request(
                    project_spec["project_id"]
                )
            result = self._client.request("turn/start", params)
            turn_id = self._extract_turn_id(result)
        except CodexRPCError as exc:
            self._recover_project_root_start(project_spec)
            confirmed_not_created = False
            with self._lock:
                if (
                    generation == self._turn_generation
                    and self._turn_active
                    and not self._turn_created
                ):
                    self._turn_active = False
                    self._turn_id = None
                    self._turn_status = None
                    self._start_source_turn_id = None
                    confirmed_not_created = True
            if project_spec is not None and confirmed_not_created:
                self._project_threads.fail_project_start(
                    project_spec["project_id"], exc
                )
            if confirmed_not_created:
                details = dict(exc.details or {})
                details.update(
                    {
                        "turn_created": False,
                        "turn_active": False,
                        "thread_id": thread_id,
                        "turn_id": None,
                        "turn_status": None,
                    }
                )
                raise BridgeError(
                    exc.code, exc.message, exc.http_status, details
                ) from exc
            raise
        except Exception as exc:
            self._recover_project_root_start(project_spec)
            with self._lock:
                observed_turn = bool(
                    generation == self._turn_generation
                    and self._turn_created
                    and self._identifier_is_valid(self._turn_id)
                )
                if (
                    generation == self._turn_generation
                    and self._turn_active
                    and not self._turn_created
                ):
                    self._turn_status = "startUnknown"
            result_unknown = observed_turn or (
                isinstance(exc, BridgeError)
                and exc.code == "CODEX_REQUEST_TIMEOUT"
            )
            if project_spec is not None and not result_unknown:
                self._project_threads.fail_project_start(
                    project_spec["project_id"], exc
                )
            raise

        if project_spec is not None:
            try:
                self._project_threads.attach_root_turn(
                    project_spec["project_id"], turn_id
                )
            except Exception as exc:
                self._project_threads.fail_project_start(
                    project_spec["project_id"], exc
                )
                raise

        publish_selection = False
        with self._lock:
            if generation == self._turn_generation:
                if self._turn_id not in {None, turn_id}:
                    if self._turn_active:
                        self._turn_status = "startUnknown"
                    raise BridgeError(
                        "INVALID_CODEX_RESPONSE",
                        "Turn acknowledgement conflicts with the observed Turn",
                        http_status=502,
                        details={
                            "acknowledged_turn_id": turn_id,
                            "observed_turn_id": self._turn_id,
                        },
                    )
                self._turn_created = True
                self._turn_id = turn_id
                self._start_source_turn_id = None
                if self._turn_active and self._turn_status == "starting":
                    self._turn_status = "inProgress"
                runtime = self._thread_runtime.get(thread_id)
                if runtime is not None:
                    runtime["profile"] = profile
                    if model is not None:
                        runtime["model"] = model
                    if effort is not None:
                        runtime["effort"] = effort
                    if service_tier is not None:
                        runtime["service_tier"] = service_tier
                publish_selection = True
        if publish_selection:
            self._events.publish(
                "turn_selected", thread_id=thread_id, turn_id=turn_id
            )
        response = {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "result": result,
            "routing": routing,
        }
        if project_spec is not None:
            response["project_id"] = project_spec["project_id"]
        return response

    def steer_turn(
        self,
        text: str,
        local_image_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Append user input to the selected active Turn without changing its lifecycle."""

        if not isinstance(text, str):
            raise BridgeError("EMPTY_INPUT", "Natural-language input must not be empty")
        if len(text) > 65536:
            raise BridgeError("INPUT_TOO_LARGE", "Input exceeds the 65536 character limit")

        while True:
            with self._lock:
                thread_id = self._validated_identifier(
                    self._thread_id, "thread_id"
                )
            project_identity = self._project_threads.project_identity_for_thread(
                thread_id
            )
            with self._lock:
                if thread_id != self._thread_id:
                    continue
                turn_id = self._turn_id
                if project_identity is not None:
                    raise BridgeError(
                        "PROJECT_THREAD_GUIDANCE_REQUIRED",
                        "Project role input must use the project guidance endpoint",
                        http_status=409,
                        details={
                            "project_id": project_identity["project_id"],
                            "project_role": project_identity["project_role"],
                            "thread_id": thread_id,
                            "turn_id": turn_id,
                            "turn_active": self._turn_active,
                            "turn_status": self._turn_status,
                        },
                    )
                if not self._turn_active or not self._identifier_is_valid(turn_id):
                    raise BridgeError(
                        "NO_ACTIVE_TURN",
                        "No steerable active Turn is available",
                        http_status=409,
                        details={
                            "turn_active": self._turn_active,
                            "thread_id": thread_id,
                            "turn_id": turn_id,
                            "turn_status": self._turn_status,
                        },
                    )
                image_paths = self._validated_local_image_paths(
                    local_image_paths,
                    thread_id,
                )
                if not text.strip() and not image_paths:
                    raise BridgeError(
                        "EMPTY_INPUT",
                        "Natural-language input or at least one image is required",
                    )
                generation = self._turn_generation
                break

        turn_input: list[dict[str, Any]] = []
        if text.strip():
            turn_input.append(
                {
                    "type": "text",
                    "text": text,
                    "text_elements": [],
                }
            )
        turn_input.extend(
            {"type": "localImage", "path": path} for path in image_paths
        )
        try:
            result = self._client.request(
                "turn/steer",
                {
                    "threadId": thread_id,
                    "expectedTurnId": turn_id,
                    "input": turn_input,
                },
            )
        except CodexRPCError as exc:
            turn_kind = self._non_steerable_turn_kind(exc.details)
            if turn_kind is not None:
                raise BridgeError(
                    "TURN_NOT_STEERABLE",
                    f"The active {turn_kind} Turn cannot accept appended input",
                    http_status=409,
                    details={
                        "turn_kind": turn_kind,
                        "turn_active": True,
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                    },
                ) from exc
            active_turn_mismatch = self._rpc_active_turn_mismatch(exc.details)
            if (
                active_turn_mismatch is not None
                and active_turn_mismatch[0] == turn_id
            ):
                expected_turn_id, found_turn_id = active_turn_mismatch
                with self._turn_condition:
                    same_turn = (
                        generation == self._turn_generation
                        and self._thread_id == thread_id
                        and self._turn_id == expected_turn_id
                        and self._turn_active
                    )
                    if same_turn:
                        self._turn_generation += 1
                        self._start_source_turn_id = expected_turn_id
                        self._turn_id = found_turn_id
                        self._turn_status = "inProgress"
                        self._turn_active = True
                        self._turn_created = True
                        self._turn_condition.notify_all()
                    details = {
                        "thread_id": thread_id,
                        "expected_turn_id": expected_turn_id,
                        "active_turn_id": found_turn_id,
                        "turn_active": True if same_turn else None,
                        "turn_status": "inProgress" if same_turn else "changed",
                    }
                raise BridgeError(
                    "STALE_ACTIVE_TURN",
                    "The active Turn changed before appended input was accepted",
                    http_status=409,
                    details=details,
                ) from exc
            if self._rpc_reports_no_active_steer(exc.details):
                with self._turn_condition:
                    same_turn = (
                        generation == self._turn_generation
                        and self._thread_id == thread_id
                        and self._turn_id == turn_id
                    )
                    if same_turn and self._turn_active:
                        self._turn_status = "completed"
                        self._turn_active = False
                        self._turn_created = True
                        self._turn_condition.notify_all()
                    details = {
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "turn_active": (
                            self._turn_active if same_turn else None
                        ),
                        "turn_status": (
                            self._turn_status if same_turn else "changed"
                        ),
                    }
                    snapshot = self.snapshot() if same_turn else None
                if snapshot is not None:
                    self._events.publish("session_state", session=snapshot)
                raise BridgeError(
                    "NO_ACTIVE_TURN",
                    "The previous Turn ended before appended input was accepted",
                    http_status=409,
                    details=details,
                ) from exc
            raise

        acknowledged_turn_id = self._extract_steer_turn_id(result)
        if acknowledged_turn_id != turn_id:
            raise BridgeError(
                "INVALID_CODEX_RESPONSE",
                "turn/steer acknowledgement does not match the active Turn",
                http_status=502,
                details={
                    "expected_turn_id": turn_id,
                    "acknowledged_turn_id": acknowledged_turn_id,
                },
            )
        return {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "result": result,
        }

    def _validated_local_image_paths(
        self,
        value: Any,
        thread_id: str,
    ) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise BridgeError(
                "INVALID_LOCAL_IMAGES",
                "local_image_paths must be an array",
            )
        if len(value) > MAX_LOCAL_IMAGES:
            raise BridgeError(
                "TOO_MANY_LOCAL_IMAGES",
                f"A Turn may include at most {MAX_LOCAL_IMAGES} images",
                details={"max_images": MAX_LOCAL_IMAGES},
            )

        attachments_root = (
            self._project_root / ".runtime" / "attachments"
        ).resolve()
        thread_directory = (attachments_root / thread_id).resolve()
        if thread_directory.parent != attachments_root:
            raise BridgeError(
                "INVALID_LOCAL_IMAGE_PATH",
                "The current Thread identifier cannot name an attachment directory",
            )

        validated: list[str] = []
        for raw_path in value:
            if (
                not isinstance(raw_path, str)
                or not raw_path
                or len(raw_path) > LOCAL_IMAGE_PATH_MAX_LENGTH
                or "\x00" in raw_path
            ):
                raise BridgeError(
                    "INVALID_LOCAL_IMAGE_PATH",
                    "Each local image path must be a valid absolute path",
                )
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                raise BridgeError(
                    "INVALID_LOCAL_IMAGE_PATH",
                    "Each local image path must be absolute",
                )
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(thread_directory)
            except (OSError, RuntimeError, ValueError) as exc:
                raise BridgeError(
                    "INVALID_LOCAL_IMAGE_PATH",
                    "Local images must exist inside the current Thread attachment directory",
                ) from exc
            if not resolved.is_file() or resolved.suffix.lower() not in LOCAL_IMAGE_SUFFIXES:
                raise BridgeError(
                    "INVALID_LOCAL_IMAGE",
                    "Local images must be existing PNG, JPG, JPEG, or WEBP files",
                )
            validated.append(str(resolved))
        return validated

    def interrupt_turn(self) -> dict[str, Any]:
        interrupt_deadline = time.monotonic() + STOP_INTERRUPT_GRACE_SECONDS
        with self._lock:
            selected_thread_id = self._thread_id
            selected_turn_id = self._turn_id
            selected_active = self._turn_active and all(
                self._identifier_is_valid(value)
                for value in (selected_thread_id, selected_turn_id)
            )
        project_interrupt = self._project_threads.interrupt_project(
            exclude_turn_id=selected_turn_id if selected_active else None
        )
        with self._lock:
            thread_id = self._thread_id
            turn_id = self._turn_id
            if not self._turn_active or not all(
                self._identifier_is_valid(value) for value in (thread_id, turn_id)
            ):
                if project_interrupt["interrupted"]:
                    snapshot = self.snapshot()
                    return {
                        "thread_id": thread_id,
                        "turn_id": None,
                        "result": None,
                        "project_interrupt": project_interrupt,
                        "restarted_app_server": False,
                        "recovery_pending": False,
                        "houdini_may_still_be_finishing": False,
                        "session": snapshot,
                    }
                raise self._no_active_turn_error_locked()
            if self._turn_status == "stopRequested":
                snapshot = self.snapshot()
                return {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "result": None,
                    "project_interrupt": project_interrupt,
                    "restarted_app_server": False,
                    "recovery_pending": False,
                    "houdini_may_still_be_finishing": self._tool_may_still_be_running_locked(),
                    "session": snapshot,
                }
            generation = self._turn_generation
            self._turn_status = "stopRequested"
            houdini_may_still_be_finishing = (
                self._tool_may_still_be_running_locked()
            )

        self._events.publish("session_state", session=self.snapshot())
        result: Any = None
        request_with_timeout = getattr(self._client, "request_with_timeout", None)
        try:
            if callable(request_with_timeout):
                result = request_with_timeout(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                    timeout_seconds=STOP_INTERRUPT_GRACE_SECONDS,
                )
            else:  # Test doubles and older in-process clients remain compatible.
                result = self._client.request(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                )
        except (BridgeError, CodexRPCError):
            # A lost/late interrupt acknowledgement is not terminal evidence.
            # The exact Turn is checked below before any process replacement.
            pass

        with self._turn_condition:
            while (
                generation == self._turn_generation
                and self._thread_id == thread_id
                and self._turn_id == turn_id
                and self._turn_active
            ):
                remaining = interrupt_deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._turn_condition.wait(remaining)
            if (
                generation != self._turn_generation
                or self._thread_id != thread_id
                or self._turn_id != turn_id
                or not self._turn_active
            ):
                snapshot = self.snapshot()
                return {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "result": result,
                    "project_interrupt": project_interrupt,
                    "restarted_app_server": False,
                    "recovery_pending": False,
                    "houdini_may_still_be_finishing": False,
                    "session": snapshot,
                }

        snapshot = self._start_app_server_recovery_after_stop(
            thread_id,
            turn_id,
            generation,
        )
        return {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "result": result,
            "project_interrupt": project_interrupt,
            "restarted_app_server": False,
            "recovery_pending": snapshot.get("turn_status") == "stopRecovering",
            "houdini_may_still_be_finishing": houdini_may_still_be_finishing,
            "session": snapshot,
        }

    def _start_app_server_recovery_after_stop(
        self,
        thread_id: str,
        turn_id: str,
        generation: int,
    ) -> dict[str, Any]:
        """Release the stopped Turn and recover its exact Thread once in background."""

        with self._turn_condition:
            if not (
                generation == self._turn_generation
                and self._thread_id == thread_id
                and self._turn_id == turn_id
                and self._turn_active
                and self._turn_status == "stopRequested"
            ):
                return self.snapshot()
            existing = self._stop_recovery_thread
            if existing is not None and existing.is_alive():
                return self.snapshot()
            self._connected = False
            self._initialize_result = None
            self._turn_generation += 1
            recovery_generation = self._turn_generation
            self._start_source_turn_id = turn_id
            self._turn_id = None
            self._turn_status = "stopRecovering"
            self._turn_active = False
            self._turn_created = True
            self._turn_condition.notify_all()
            worker = threading.Thread(
                target=self._recover_app_server_after_stop,
                args=(thread_id, recovery_generation),
                name="hia-stop-recovery",
                daemon=True,
            )
            self._stop_recovery_thread = worker
            snapshot = self.snapshot()

        self._events.publish("session_state", session=snapshot)
        try:
            worker.start()
        except RuntimeError:
            with self._turn_condition:
                if (
                    recovery_generation == self._turn_generation
                    and self._thread_id == thread_id
                    and self._turn_status == "stopRecovering"
                ):
                    self._turn_status = "stopRecoveryFailed"
                    self._stop_recovery_thread = None
                    failed_snapshot = self.snapshot()
                else:
                    failed_snapshot = snapshot
            self._events.publish("session_state", session=failed_snapshot)
            return failed_snapshot
        return snapshot

    def _recover_app_server_after_stop(
        self,
        thread_id: str,
        recovery_generation: int,
    ) -> None:
        """Replace Codex, resume one exact Thread, and never replay its Turn."""

        deadline = time.monotonic() + STOP_RECOVERY_TOTAL_SECONDS

        try:
            with self._lock:
                if self._closed:
                    self._clear_stop_recovery_worker()
                    return
            restart = getattr(self._client, "restart", None)
            initialize_with_timeout = getattr(
                self._client,
                "initialize_with_timeout",
                None,
            )
            request_with_timeout = getattr(
                self._client,
                "request_with_timeout",
                None,
            )
            if not all(
                callable(value)
                for value in (restart, initialize_with_timeout, request_with_timeout)
            ):
                raise BridgeError(
                    "CODEX_RESTART_UNAVAILABLE",
                    "Codex app-server restart is unavailable",
                    http_status=503,
                )
            restart(
                grace_seconds=STOP_RESTART_GRACE_SECONDS,
                deadline=deadline,
            )
            remaining = min(
                STOP_RECOVERY_TOTAL_SECONDS,
                deadline - time.monotonic(),
            )
            if remaining <= 0:
                raise BridgeError(
                    "CODEX_STOP_RECOVERY_TIMEOUT",
                    "Codex app-server stop recovery timed out",
                    http_status=504,
                )
            initialize_result = initialize_with_timeout(
                min(STOP_REINITIALIZE_MAX_SECONDS, remaining)
            )
            remaining = min(
                STOP_RECOVERY_TOTAL_SECONDS,
                deadline - time.monotonic(),
            )
            if remaining <= 0:
                raise BridgeError(
                    "CODEX_STOP_RECOVERY_TIMEOUT",
                    "Codex app-server stop recovery timed out",
                    http_status=504,
                )
            with self._turn_condition:
                recovery_still_current = (
                    not self._closed
                    and recovery_generation == self._turn_generation
                    and self._thread_id == thread_id
                    and self._turn_status == "stopRecovering"
                )
                if not recovery_still_current and not self._closed:
                    self._initialize_result = initialize_result
                    self._connected = True
                    self._turn_condition.notify_all()
                    cancelled_snapshot = self.snapshot()
                else:
                    cancelled_snapshot = None
            if not recovery_still_current:
                if cancelled_snapshot is not None:
                    self._events.publish(
                        "session_state",
                        session=cancelled_snapshot,
                    )
                self._clear_stop_recovery_worker()
                return
            profile = self._permission_profile(thread_id)
            resumed = request_with_timeout(
                "thread/resume",
                self._thread_profile_params(
                    profile,
                    service_tier=None,
                    thread_id=thread_id,
                ),
                timeout_seconds=remaining,
            )
            if self._extract_thread_id(resumed) != thread_id:
                raise BridgeError(
                    "INVALID_CODEX_RESPONSE",
                    "Restarted Codex resumed a different Thread",
                    http_status=502,
                )
        except Exception:
            with self._turn_condition:
                if (
                    not self._closed
                    and recovery_generation == self._turn_generation
                    and self._thread_id == thread_id
                    and self._turn_status == "stopRecovering"
                ):
                    self._connected = False
                    self._initialize_result = None
                    self._turn_status = "stopRecoveryFailed"
                    self._turn_active = False
                    self._turn_condition.notify_all()
                    failed_snapshot = self.snapshot()
                else:
                    failed_snapshot = None
            if failed_snapshot is not None:
                self._events.publish("session_state", session=failed_snapshot)
            self._clear_stop_recovery_worker()
            return

        with self._turn_condition:
            if not (
                not self._closed
                and recovery_generation == self._turn_generation
                and self._thread_id == thread_id
                and self._turn_status == "stopRecovering"
            ):
                self._clear_stop_recovery_worker()
                return
            self._initialize_result = initialize_result
            self._connected = True
            self._thread_permission_profile = (
                profile
                if self._permission_profile(thread_id) == profile
                else None
            )
            self._turn_generation += 1
            self._turn_id = None
            self._turn_status = "interrupted"
            self._turn_active = False
            self._turn_created = True
            self._turn_condition.notify_all()
            recovered_snapshot = self.snapshot()
        self._events.publish("session_state", session=recovered_snapshot)

        self._clear_stop_recovery_worker()

    def _clear_stop_recovery_worker(self) -> None:
        with self._lock:
            if self._stop_recovery_thread is threading.current_thread():
                self._stop_recovery_thread = None

    def _tool_may_still_be_running_locked(self) -> bool:
        return (
            isinstance(self._last_tool_name, str)
            and bool(self._last_tool_name)
            and self._last_tool_status not in {"completed", "failed"}
        )

    def resolve_approval(self, request_id: RequestId, decision: str) -> dict[str, Any]:
        if decision not in {"allow", "deny", "allow_rule"}:
            raise BridgeError(
                "INVALID_APPROVAL_DECISION",
                "Approval decision must be 'allow', 'deny', or 'allow_rule'",
            )
        request = self._client.pending_server_request(request_id)
        if request is None:
            raise BridgeError(
                "APPROVAL_NOT_FOUND",
                "The approval request is no longer pending",
                http_status=404,
            )
        method = request["method"]
        params = request.get("params", {})
        if decision == "allow_rule":
            amendment = _offered_execpolicy_amendment(params)
            if (
                method != "item/commandExecution/requestApproval"
                or amendment is None
            ):
                raise BridgeError(
                    "INVALID_APPROVAL_DECISION",
                    "This approval request does not offer a persistent command rule",
                )
            response = {
                "decision": {
                    "acceptWithExecpolicyAmendment": {
                        "execpolicy_amendment": list(amendment),
                    }
                }
            }
        elif method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            response = {"decision": "accept" if decision == "allow" else "decline"}
        elif method == "item/permissions/requestApproval":
            response = {
                "permissions": params.get("permissions", {}) if decision == "allow" else {},
                "scope": "turn",
            }
        else:
            raise BridgeError(
                "UNSUPPORTED_APPROVAL",
                f"Unsupported approval method: {method}",
            )
        resolved_method = self._client.respond_to_server_request(request_id, response)
        self._events.publish(
            "approval_resolved",
            request_id=request_id,
            method=resolved_method,
            decision=decision,
        )
        return {
            "request_id": request_id,
            "method": resolved_method,
            "decision": decision,
        }

    @staticmethod
    def _validated_optional_selection(
        value: Any,
        field: str,
        max_length: int,
    ) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > max_length
            or any(ord(character) < 32 for character in value)
        ):
            raise BridgeError(
                f"INVALID_{field.upper()}",
                f"{field} must be a non-empty string of at most {max_length} characters",
                details={"field": field, "max_length": max_length},
            )
        return value

    @staticmethod
    def _invalid_model_response(
        message: str,
        *,
        field: str | None = None,
    ) -> BridgeError:
        details = {"field": field} if field is not None else None
        return BridgeError(
            "INVALID_MODEL_LIST_RESPONSE",
            message,
            http_status=502,
            details=details,
        )

    @staticmethod
    def _invalid_thread_response(
        message: str,
        *,
        field: str | None = None,
    ) -> BridgeError:
        details = {"field": field} if field is not None else None
        return BridgeError(
            "INVALID_THREAD_LIST_RESPONSE",
            message,
            http_status=502,
            details=details,
        )

    @staticmethod
    def _invalid_goal_response(
        message: str,
        *,
        field: str | None = None,
    ) -> BridgeError:
        details = {"field": field} if field is not None else None
        return BridgeError(
            "INVALID_GOAL_RESPONSE",
            message,
            http_status=502,
            details=details,
        )

    def _selected_thread_id(self, expected_thread_id: str) -> str:
        expected_thread_id = self._validated_identifier(
            expected_thread_id,
            "thread_id",
        )
        with self._lock:
            thread_id = self._thread_id
        thread_id = self._validated_identifier(thread_id, "thread_id")
        if thread_id != expected_thread_id:
            raise BridgeError(
                "THREAD_SELECTION_CHANGED",
                "The selected Thread no longer matches this Goal request",
                http_status=409,
                details={
                    "expected_thread_id": expected_thread_id,
                    "current_thread_id": thread_id,
                },
            )
        return expected_thread_id

    @classmethod
    def _project_goal(
        cls,
        result: Any,
        expected_thread_id: str,
        *,
        allow_none: bool,
    ) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            raise cls._invalid_goal_response("Goal response must be an object")
        goal = result.get("goal")
        if goal is None and allow_none:
            return None
        if not isinstance(goal, dict):
            raise cls._invalid_goal_response(
                "Goal response must contain a Goal object",
                field="goal",
            )
        thread_id = goal.get("threadId")
        if not cls._identifier_is_valid(thread_id) or thread_id != expected_thread_id:
            raise cls._invalid_goal_response(
                "Goal threadId does not match the selected Thread",
                field="threadId",
            )
        objective = goal.get("objective")
        if not isinstance(objective, str) or "\x00" in objective:
            raise cls._invalid_goal_response(
                "Goal objective is invalid",
                field="objective",
            )
        status = goal.get("status")
        if status not in GOAL_STATUSES:
            raise cls._invalid_goal_response(
                "Goal status is invalid",
                field="status",
            )
        return dict(goal)

    @classmethod
    def _validated_thread_response_string(
        cls,
        value: Any,
        field: str,
        max_length: int,
        *,
        allow_empty: bool,
    ) -> str:
        if (
            not isinstance(value, str)
            or (not allow_empty and not value.strip())
            or len(value) > max_length
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise cls._invalid_thread_response(
                f"Response field {field} is invalid", field=field
            )
        return value

    @classmethod
    def _sanitized_thread_preview(cls, value: Any) -> str:
        """Bound display-only preview text without rejecting the whole page."""

        if not isinstance(value, str):
            raise cls._invalid_thread_response(
                "Response field preview is invalid", field="preview"
            )
        prefix = value[:THREAD_PREVIEW_MAX_LENGTH]
        without_controls = "".join(
            " " if ord(character) < 32 or ord(character) == 127 else character
            for character in prefix
        )
        return " ".join(without_controls.split())[:THREAD_PREVIEW_MAX_LENGTH]

    @classmethod
    def _validated_response_string(
        cls,
        value: Any,
        field: str,
        max_length: int,
        *,
        allow_empty: bool,
    ) -> str:
        if (
            not isinstance(value, str)
            or (not allow_empty and not value.strip())
            or len(value) > max_length
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise cls._invalid_model_response(
                f"Response field {field} is invalid",
                field=field,
            )
        return value

    @classmethod
    def _sanitize_model_entry(cls, entry: Any) -> dict[str, Any] | None:
        if not isinstance(entry, dict):
            raise cls._invalid_model_response("Model entry must be an object")
        hidden = entry.get("hidden")
        if not isinstance(hidden, bool):
            raise cls._invalid_model_response(
                "Model hidden flag must be a boolean",
                field="hidden",
            )

        model = cls._validated_response_string(
            entry.get("model"),
            "model",
            MODEL_IDENTIFIER_MAX_LENGTH,
            allow_empty=False,
        )
        display_name = cls._validated_response_string(
            entry.get("displayName"),
            "displayName",
            MODEL_DISPLAY_NAME_MAX_LENGTH,
            allow_empty=True,
        )
        description = cls._validated_response_string(
            entry.get("description"),
            "description",
            MODEL_DESCRIPTION_MAX_LENGTH,
            allow_empty=True,
        )
        is_default = entry.get("isDefault")
        if not isinstance(is_default, bool):
            raise cls._invalid_model_response(
                "Model isDefault flag must be a boolean",
                field="isDefault",
            )

        input_modalities = entry.get("inputModalities", ["text", "image"])
        if (
            not isinstance(input_modalities, list)
            or len(input_modalities) > 8
            or any(modality not in {"text", "image"} for modality in input_modalities)
        ):
            raise cls._invalid_model_response(
                "Model inputModalities is invalid",
                field="inputModalities",
            )

        raw_service_tiers = entry.get("serviceTiers", [])
        if (
            not isinstance(raw_service_tiers, list)
            or len(raw_service_tiers) > MODEL_SERVICE_TIER_MAX_ENTRIES
        ):
            raise cls._invalid_model_response(
                "Model serviceTiers is invalid",
                field="serviceTiers",
            )
        service_tiers: list[dict[str, str]] = []
        seen_service_tiers: set[str] = set()
        for raw_service_tier in raw_service_tiers:
            if not isinstance(raw_service_tier, dict):
                raise cls._invalid_model_response(
                    "Service tier option must be an object",
                    field="serviceTiers",
                )
            service_tier_id = cls._validated_response_string(
                raw_service_tier.get("id"),
                "serviceTiers.id",
                SERVICE_TIER_MAX_LENGTH,
                allow_empty=False,
            )
            service_tier_name = cls._validated_response_string(
                raw_service_tier.get("name"),
                "serviceTiers.name",
                MODEL_DISPLAY_NAME_MAX_LENGTH,
                allow_empty=True,
            )
            service_tier_description = cls._validated_response_string(
                raw_service_tier.get("description"),
                "serviceTiers.description",
                MODEL_DESCRIPTION_MAX_LENGTH,
                allow_empty=True,
            )
            if service_tier_id in seen_service_tiers:
                raise cls._invalid_model_response(
                    "Model contains a duplicate service tier",
                    field="serviceTiers",
                )
            seen_service_tiers.add(service_tier_id)
            service_tiers.append(
                {
                    "id": service_tier_id,
                    "name": service_tier_name,
                    "description": service_tier_description,
                }
            )

        raw_default_service_tier = entry.get("defaultServiceTier")
        default_service_tier = None
        if raw_default_service_tier is not None:
            default_service_tier = cls._validated_response_string(
                raw_default_service_tier,
                "defaultServiceTier",
                SERVICE_TIER_MAX_LENGTH,
                allow_empty=False,
            )
            if default_service_tier not in seen_service_tiers:
                raise cls._invalid_model_response(
                    "Model defaultServiceTier is not advertised in serviceTiers",
                    field="defaultServiceTier",
                )

        raw_efforts = entry.get("supportedReasoningEfforts")
        if not isinstance(raw_efforts, list) or len(raw_efforts) > 32:
            raise cls._invalid_model_response(
                "Model supportedReasoningEfforts is invalid",
                field="supportedReasoningEfforts",
            )
        efforts: list[dict[str, str]] = []
        seen_efforts: set[str] = set()
        for raw_effort in raw_efforts:
            if not isinstance(raw_effort, dict):
                raise cls._invalid_model_response(
                    "Reasoning effort option must be an object",
                    field="supportedReasoningEfforts",
                )
            effort = cls._validated_response_string(
                raw_effort.get("reasoningEffort"),
                "supportedReasoningEfforts.reasoningEffort",
                REASONING_EFFORT_MAX_LENGTH,
                allow_empty=False,
            )
            effort_description = cls._validated_response_string(
                raw_effort.get("description"),
                "supportedReasoningEfforts.description",
                MODEL_DESCRIPTION_MAX_LENGTH,
                allow_empty=True,
            )
            if effort in seen_efforts:
                raise cls._invalid_model_response(
                    "Model contains a duplicate reasoning effort",
                    field="supportedReasoningEfforts",
                )
            seen_efforts.add(effort)
            efforts.append(
                {
                    "reasoningEffort": effort,
                    "description": effort_description,
                }
            )

        default_effort = cls._validated_response_string(
            entry.get("defaultReasoningEffort"),
            "defaultReasoningEffort",
            REASONING_EFFORT_MAX_LENGTH,
            allow_empty=False,
        )
        if hidden:
            return None
        return {
            "model": model,
            "displayName": display_name,
            "description": description,
            "isDefault": is_default,
            "inputModalities": list(input_modalities),
            "serviceTiers": service_tiers,
            "defaultServiceTier": default_service_tier,
            "supportedReasoningEfforts": efforts,
            "defaultReasoningEffort": default_effort,
        }

    @staticmethod
    def _validated_identifier(value: Any, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise BridgeError(
                "MISSING_IDENTIFIER",
                f"{name} is required",
                details={"field": name},
            )
        if len(value) > 512 or any(ord(character) < 32 for character in value):
            raise BridgeError(
                "INVALID_IDENTIFIER",
                f"{name} is invalid",
                details={"field": name},
            )
        return value

    @staticmethod
    def _identifier_is_valid(value: Any) -> bool:
        return (
            isinstance(value, str)
            and bool(value.strip())
            and len(value) <= 512
            and not any(ord(character) < 32 for character in value)
        )

    def _require_no_active_turn_locked(self) -> None:
        if not self._turn_active:
            return
        raise BridgeError(
            "TURN_ALREADY_ACTIVE",
            "A Turn is already active for the selected Thread",
            http_status=409,
            details={
                "turn_created": False,
                "turn_active": True,
                "thread_id": self._thread_id,
                "turn_id": self._turn_id,
                "turn_status": self._turn_status,
            },
        )

    def _no_active_turn_error_locked(self) -> BridgeError:
        return BridgeError(
            "NO_ACTIVE_TURN",
            "No interruptible active Turn is available",
            http_status=409,
            details={
                "turn_active": self._turn_active,
                "thread_id": self._thread_id,
                "turn_id": self._turn_id,
                "turn_status": self._turn_status,
            },
        )

    def _reset_turn_locked(self) -> None:
        self._turn_generation += 1
        self._turn_id = None
        self._turn_status = None
        self._turn_active = False
        self._turn_created = False
        self._start_source_turn_id = None
        self._last_tool_name = None
        self._last_tool_status = None

    @staticmethod
    def _extract_thread_id(result: Any) -> str:
        if not isinstance(result, dict) or not isinstance(result.get("thread"), dict):
            raise BridgeError("INVALID_CODEX_RESPONSE", "Thread response has no thread object", 502)
        thread_id = result["thread"].get("id")
        return BridgeSession._validated_identifier(thread_id, "thread_id")

    @staticmethod
    def _project_thread_messages(
        result: Any,
        expected_thread_id: str,
    ) -> dict[str, Any]:
        """Return only the complete stable chat fields consumed by the Panel."""

        thread_id = BridgeSession._extract_thread_id(result)
        if thread_id != expected_thread_id:
            raise BridgeError(
                "INVALID_CODEX_RESPONSE",
                "Thread response does not match the requested thread",
                502,
            )
        thread = result["thread"]
        turns = thread.get("turns")
        if not isinstance(turns, list):
            raise BridgeError(
                "INVALID_CODEX_RESPONSE",
                "Thread response has no turns array",
                502,
            )

        projected_turns: list[dict[str, Any]] = []
        for turn in turns:
            if not isinstance(turn, dict) or not isinstance(turn.get("items"), list):
                raise BridgeError(
                    "INVALID_CODEX_RESPONSE",
                    "Thread response contains an invalid turn",
                    502,
                )
            projected_turn: dict[str, Any] = {"items": []}
            turn_id = turn.get("id")
            if (
                BridgeSession._identifier_is_valid(turn_id)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", turn_id)
                is not None
                and len(turn_id) <= 256
            ):
                projected_turn["id"] = turn_id
            projected_items: list[dict[str, Any]] = []
            for item in turn["items"]:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                safe_item_id = (
                    item_id
                    if (
                        BridgeSession._identifier_is_valid(item_id)
                        and re.fullmatch(
                            r"[A-Za-z0-9][A-Za-z0-9._:-]*",
                            item_id,
                        )
                        is not None
                        and len(item_id) <= 256
                    )
                    else None
                )
                if item.get("type") == "userMessage":
                    content = item.get("content")
                    if not isinstance(content, list):
                        continue
                    projected_content: list[dict[str, str]] = []
                    for entry in content:
                        if not isinstance(entry, dict):
                            continue
                        if entry.get("type") == "text" and isinstance(
                            entry.get("text"), str
                        ):
                            projected_content.append(
                                {"type": "text", "text": entry["text"]}
                            )
                        elif entry.get("type") == "localImage" and isinstance(
                            entry.get("path"), str
                        ):
                            projected_content.append(
                                {"type": "localImage", "path": entry["path"]}
                            )
                    projected_item: dict[str, Any] = {
                        "type": "userMessage",
                        "content": projected_content,
                    }
                    if safe_item_id is not None:
                        projected_item["id"] = safe_item_id
                    projected_items.append(projected_item)
                elif item.get("type") == "agentMessage" and isinstance(
                    item.get("text"), str
                ):
                    marker_values = {
                        str(item.get(name) or "").strip().casefold()
                        for name in ("channel", "phase")
                    }
                    if marker_values & {
                        "analysis",
                        "commentary",
                        "internal",
                        "reasoning",
                    }:
                        continue
                    projected_item = {
                        "type": "agentMessage",
                        "text": item["text"],
                    }
                    if safe_item_id is not None:
                        projected_item["id"] = safe_item_id
                    projected_items.append(projected_item)
            projected_turn["items"] = projected_items
            projected_turns.append(projected_turn)

        return {
            "thread": {
                "id": thread_id,
                "turns": projected_turns,
            }
        }

    @staticmethod
    def _extract_turn_id(result: Any) -> str:
        if not isinstance(result, dict) or not isinstance(result.get("turn"), dict):
            raise BridgeError("INVALID_CODEX_RESPONSE", "Turn response has no turn object", 502)
        turn_id = result["turn"].get("id")
        return BridgeSession._validated_identifier(turn_id, "turn_id")

    @staticmethod
    def _extract_steer_turn_id(result: Any) -> str:
        if (
            not isinstance(result, dict)
            or not BridgeSession._identifier_is_valid(result.get("turnId"))
        ):
            raise BridgeError(
                "INVALID_CODEX_RESPONSE",
                "turn/steer response has no turnId",
                502,
            )
        return result["turnId"]

    @staticmethod
    def _non_steerable_turn_kind(error: Any) -> str | None:
        pending = [error]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                marker = value.get("activeTurnNotSteerable")
                if isinstance(marker, dict) and marker.get("turnKind") in {
                    "review",
                    "compact",
                }:
                    return marker["turnKind"]
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return None

    @staticmethod
    def _rpc_reports_no_active_steer(error: Any) -> bool:
        if not isinstance(error, dict):
            return False
        rpc_error = error.get("rpc_error")
        message = rpc_error.get("message") if isinstance(rpc_error, dict) else None
        if (
            not isinstance(rpc_error, dict)
            or rpc_error.get("code") != -32600
            or not isinstance(message, str)
        ):
            return False
        normalized = " ".join(message.casefold().split())
        return normalized.rstrip(".") == "no active turn to steer"

    @staticmethod
    def _rpc_active_turn_mismatch(error: Any) -> tuple[str, str] | None:
        if not isinstance(error, dict):
            return None
        rpc_error = error.get("rpc_error")
        message = rpc_error.get("message") if isinstance(rpc_error, dict) else None
        if (
            not isinstance(rpc_error, dict)
            or rpc_error.get("code") != -32600
            or not isinstance(message, str)
        ):
            return None
        normalized = " ".join(message.split()).rstrip(".")
        match = re.fullmatch(
            r"expected active turn id (.+?) but found (.+)",
            normalized,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        identifiers = tuple(
            value.strip().strip("`'\"") for value in match.groups()
        )
        if any(
            not BridgeSession._identifier_is_valid(value)
            or any(character.isspace() for character in value)
            for value in identifiers
        ):
            return None
        return identifiers[0], identifiers[1]

    def _on_client_event(self, event: dict[str, Any]) -> None:
        # Observe native compaction before project routing can consume the event.
        self._thread_transfer.observe(event)
        with self._lock:
            selected_thread_id = self._thread_id
            normal_transfer_ids = set(self._normal_transfers)
        event_params = event.get("params")
        transfer_delete = bool(
            event.get("type") == "codex_notification"
            and event.get("method") == "thread/deleted"
            and isinstance(event_params, dict)
            and (
                event_params.get("threadId") in normal_transfer_ids
                or self._project_threads.thread_transfer_in_progress(
                    event_params.get("threadId")
                )
            )
        )
        if self._project_threads.handle_client_event(
            event, selected_thread_id=selected_thread_id
        ):
            self._thread_transfer.after_event(event)
            return
        if transfer_delete:
            self._thread_transfer.after_event(event)
            return
        event_type = event.get("type")
        if event_type == "server_request":
            method = event.get("method")
            params = event.get("params")
            if not _requires_system_drive_approval(
                method,
                params,
                project_root=self._project_root,
            ):
                try:
                    self.resolve_approval(event.get("request_id"), "allow")
                except Exception:
                    # If the one-shot automatic response cannot be delivered,
                    # keep the original request visible so it is never lost.
                    pass
                else:
                    self._thread_transfer.after_event(event)
                    return
        elif event_type == "codex_notification":
            method = event.get("method")
            params = event.get("params")
            params = params if isinstance(params, dict) else {}
            with self._lock:
                if method == "turn/started":
                    turn = params.get("turn")
                    thread_id = params.get("threadId")
                    turn_id = turn.get("id") if isinstance(turn, dict) else None
                    selected_project_role = (
                        thread_id == self._thread_id
                        and self._project_threads.role_for_thread(thread_id)
                        is not None
                    )
                    project_turn = (
                        self._project_threads.turn_state_for_thread(thread_id)
                        if selected_project_role and isinstance(thread_id, str)
                        else {}
                    )
                    current_project_turn = (
                        project_turn.get("turn_id") == turn_id
                        if isinstance(project_turn, dict)
                        else False
                    )
                    if (
                        (self._turn_active or selected_project_role)
                        and thread_id == self._thread_id
                        and self._identifier_is_valid(turn_id)
                        and turn_id != self._start_source_turn_id
                        and (
                            self._turn_id in {None, turn_id}
                            or current_project_turn
                        )
                    ):
                        self._turn_id = turn_id
                        self._turn_status = "inProgress"
                        self._turn_active = True
                        self._turn_created = True
                        self._last_tool_name = None
                        self._last_tool_status = None
                elif method == "turn/completed":
                    turn = params.get("turn")
                    thread_id = params.get("threadId")
                    turn_id = turn.get("id") if isinstance(turn, dict) else None
                    selected_project_role = (
                        thread_id == self._thread_id
                        and self._project_threads.role_for_thread(thread_id)
                        is not None
                    )
                    if (
                        isinstance(turn, dict)
                        and thread_id == self._thread_id
                        and self._identifier_is_valid(turn_id)
                        and turn_id != self._start_source_turn_id
                        and (
                            turn_id == self._turn_id
                            or (selected_project_role and self._turn_id is None)
                        )
                    ):
                        self._turn_id = turn_id
                        status = turn.get("status")
                        self._turn_status = (
                            status
                            if status in {"completed", "interrupted", "failed"}
                            else "completed"
                        )
                        self._turn_active = False
                        self._turn_created = True
                        self._turn_condition.notify_all()
                elif method in {"item/started", "item/completed"}:
                    item = params.get("item")
                    thread_id = params.get("threadId")
                    turn_id = params.get("turnId")
                    if (
                        isinstance(item, dict)
                        and thread_id == self._thread_id
                        and turn_id == self._turn_id
                        and item.get("type") == "mcpToolCall"
                    ):
                        tool_name = item.get("tool")
                        if isinstance(tool_name, str) and tool_name:
                            normalized_tool_name = " ".join(
                                tool_name.split()
                            )[:128]
                            status = item.get("status")
                            normalized_status = (
                                " ".join(status.split())[:64]
                                if isinstance(status, str) and status
                                else (
                                    "started"
                                    if method == "item/started"
                                    else "completed"
                                )
                            )
                            result = item.get("result")
                            structured = (
                                result.get("structuredContent")
                                if isinstance(result, dict)
                                else None
                            )
                            if (
                                method == "item/completed"
                                and normalized_status == "failed"
                            ):
                                structured_error = (
                                    structured.get("structured_error")
                                    if isinstance(structured, dict)
                                    else None
                                )
                                details = (
                                    structured_error.get("details")
                                    if isinstance(structured_error, dict)
                                    else None
                                )
                                if (
                                    normalized_tool_name
                                    in {
                                        "hia_execute_hom",
                                        "hia_run_effect_experiment",
                                    }
                                    and
                                    isinstance(details, dict)
                                    and details.get("hom_may_still_execute")
                                    is True
                                ):
                                    normalized_status = "outcomeUnknown"
                            runtime_barrier_completed = (
                                normalized_tool_name == "hia_context"
                                and normalized_status == "completed"
                                and isinstance(structured, dict)
                                and structured.get("ok") is True
                            )
                            if (
                                self._last_tool_status != "outcomeUnknown"
                                or normalized_status == "outcomeUnknown"
                                or runtime_barrier_completed
                            ):
                                self._last_tool_name = normalized_tool_name
                                self._last_tool_status = normalized_status
                elif method in {"thread/goal/updated", "thread/goal/cleared"}:
                    thread_id = params.get("threadId")
                    goal = params.get("goal")
                    if (
                        isinstance(thread_id, str)
                        and self._focus_mode_locked(thread_id)
                    ):
                        should_disable = method == "thread/goal/cleared"
                        if isinstance(goal, dict):
                            if "status" in goal and goal.get("status") != "active":
                                should_disable = True
                            stable_fields = {
                                key for key in ("objective", "tokenBudget") if key in goal
                            }
                            if stable_fields:
                                binding = (
                                    self._goal_binding(goal)
                                    if stable_fields == {"objective", "tokenBudget"}
                                    else None
                                )
                                should_disable = should_disable or (
                                    binding
                                    != self._focus_goal_bindings.get(thread_id)
                                )
                        if should_disable:
                            try:
                                self._disable_focus_locked(thread_id)
                            except BridgeError:
                                pass
                elif method == "thread/deleted":
                    thread_id = params.get("threadId")
                    if isinstance(thread_id, str):
                        self._thread_runtime.pop(thread_id, None)
                        self._focus_enabled_threads.discard(thread_id)
                        self._focus_goal_bindings.pop(thread_id, None)
                        if self._thread_id == thread_id:
                            self._thread_id = None
                            self._thread_permission_profile = None
                            self._reset_turn_locked()
                        try:
                            self._write_focus_state_locked()
                        except BridgeError:
                            pass
        elif event_type == "process_exit":
            with self._turn_condition:
                self._connected = False
                self._turn_condition.notify_all()
        self._thread_transfer.after_event(event)
        fields = {key: value for key, value in event.items() if key != "type"}
        self._events.publish(str(event_type), **fields)
