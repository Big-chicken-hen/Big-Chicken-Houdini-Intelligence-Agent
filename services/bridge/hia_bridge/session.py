"""Connection and Thread/Turn state without a duplicate chat store."""

from __future__ import annotations

import copy
import ntpath
import os
import re
import threading
from pathlib import Path
from typing import Any

from .codex_stdio import CodexStdioClient, RequestId
from .errors import BridgeError, CodexRPCError
from .events import EventBuffer


MODEL_LIST_PAGE_SIZE = 100
MODEL_LIST_MAX_PAGES = 16
MODEL_LIST_MAX_ENTRIES = 512
MODEL_IDENTIFIER_MAX_LENGTH = 256
REASONING_EFFORT_MAX_LENGTH = 64
MODEL_DISPLAY_NAME_MAX_LENGTH = 512
MODEL_DESCRIPTION_MAX_LENGTH = 8192
MODEL_CURSOR_MAX_LENGTH = 4096
MAX_LOCAL_IMAGES = 16
LOCAL_IMAGE_PATH_MAX_LENGTH = 32_767
LOCAL_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
HIA_MCP_V2_BACKEND = "hia_v2"
FXHOUDINI_MCP_BACKEND = "fxhoudini"

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
    ) -> None:
        if mcp_backend not in {HIA_MCP_V2_BACKEND, FXHOUDINI_MCP_BACKEND}:
            raise ValueError(f"Unsupported Houdini MCP backend: {mcp_backend}")
        self._project_root = project_root
        self._client = client
        self._events = events
        self._mcp_backend = mcp_backend
        self._lock = threading.RLock()
        self._connected = False
        self._initialize_result: Any = None
        self._account_result: dict[str, Any] | None = None
        self._account_error: dict[str, Any] | None = None
        self._thread_id: str | None = None
        self._turn_id: str | None = None
        self._turn_status: str | None = None
        self._turn_active = False
        self._turn_created = False
        self._turn_generation = 0
        self._closed = False
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
        with self._lock:
            account = copy.deepcopy(self._account_result)
            account_error = copy.deepcopy(self._account_error)
            connected = self._connected and self._client.is_running
            return {
                "connected": connected,
                "mcp_backend": self._mcp_backend,
                "codex_pid": self._client.process_id,
                "authentication": self._authentication_status(account, account_error),
                "account": account,
                "account_error": account_error,
                "thread_id": self._thread_id,
                "turn_id": self._turn_id,
                "turn_status": self._turn_status,
                "turn_active": self._turn_active,
            }

    def _developer_instructions(self) -> str:
        if self._mcp_backend == HIA_MCP_V2_BACKEND:
            backend_instructions = (
                "当前场景的创建、修改、连接、材质、动画、Solaris 和 Karma 默认使用已注册的 HIA MCP V2 与 HOM；"
                "复杂操作优先用 hia_execute_hom 批量执行 Codex 生成的 HOM。"
                "用 hia_context/hia_inspect 读取，再用 hia_scene_diff/hia_validate 验证；"
                "hia_capture_viewport 仅按需视觉核对。"
                "只有主代理可以调用当前会话的 hia_* Houdini MCP 工具；"
                "子代理只做资料研究、技术方案、代码审查和规划，不得调用 hia_* 当前场景工具。"
                "hia_search_node_types/help 等同类读取由主代理串行或少量调用，不并发扇出，"
                "遇到 QUEUE_FULL 不立即重试；hia_execute_hom 等场景写入始终由主代理执行。"
            )
        else:
            backend_instructions = (
                "当前场景的创建、修改、连接、材质和动画默认使用已注册的 FXHoudini MCP 与 HOM；"
                "复杂操作优先用 execute_python 批量执行 Codex 生成的 HOM。"
                "细粒度工具用于读取、单项修改和最终验证，不要逐节点循环。"
                "相同调用失败后先读真实错误再改用兼容方法；capture_screenshot 只做阶段性验证。"
            )
        return backend_instructions + (
            "外部研究先定本阶段必需 URL，优先原生 web/search；没有网页工具时才把同阶段公开页合为"
            "一次 PowerShell 只读批量读取，不逐页审批；复用已取内容，不重复抓取相近页面。"
            "实时 MCP 不可用时直接说明，不得改成离线 HIP。"
            "只有用户明确要求离线、独立 HIP、批处理或后台渲染时才用 PATH 中的 hython.exe。"
            "普通场景请求不得先搜索 src、services、docs、contracts 或插件源码；"
            "仅用户明确要求诊断或修改 Panel、Bridge、MCP 或项目代码时读取。"
            "上下文仅用 app-server 自动整理，不手动 compact，不创建本地摘要或记忆。"
            "实时代码禁止 hou.hipFile.clear/load/save，不替换当前场景；新资产放入唯一新根。"
            "不要调用 request_user_input；信息不足时采用合理默认值，无法执行才报告原因。"
            "自行生成的截图写 HIA_CACHE_DIR/screenshots，预览写 previews，中间图写 tmp；"
            "文件名用时间戳加短随机后缀；支持输出路径时显式传入，"
            "不写仓库根、HIP 同目录、桌面或系统临时目录。"
            "禁止屏幕接管。"
        )

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

    def start_thread(self, model: str | None = None) -> dict[str, Any]:
        model = self._validated_optional_selection(
            model,
            "model",
            MODEL_IDENTIFIER_MAX_LENGTH,
        )
        with self._lock:
            self._require_no_active_turn_locked()
        params: dict[str, Any] = {
            "cwd": str(self._project_root),
            "approvalPolicy": "on-request",
            "sandbox": "workspace-write",
            "ephemeral": False,
            "developerInstructions": self._developer_instructions(),
        }
        if model is not None:
            params["model"] = model
        result = self._client.request("thread/start", params)
        thread_id = self._extract_thread_id(result)
        with self._lock:
            self._thread_id = thread_id
            self._reset_turn_locked()
        self._events.publish("thread_selected", action="start", thread_id=thread_id)
        return {"thread_id": thread_id, "result": result}

    def resume_thread(self, thread_id: str) -> dict[str, Any]:
        thread_id = self._validated_identifier(thread_id, "thread_id")
        with self._lock:
            self._require_no_active_turn_locked()
        resumed = self._client.request(
            "thread/resume",
            {
                "threadId": thread_id,
                "cwd": str(self._project_root),
                "approvalPolicy": "on-request",
                "sandbox": "workspace-write",
                "developerInstructions": self._developer_instructions(),
            },
        )
        resolved_id = self._extract_thread_id(resumed)
        read_result = self._client.request(
            "thread/read",
            {"threadId": resolved_id, "includeTurns": True},
        )
        with self._lock:
            self._thread_id = resolved_id
            self._reset_turn_locked()
        self._events.publish("thread_selected", action="resume", thread_id=resolved_id)
        return {
            "thread_id": resolved_id,
            "resume": resumed,
            "read": read_result,
        }

    def read_thread(self, thread_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            selected = thread_id or self._thread_id
        selected = self._validated_identifier(selected, "thread_id")
        result = self._client.request(
            "thread/read",
            {"threadId": selected, "includeTurns": True},
        )
        return {"thread_id": selected, "result": result}

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

    def start_turn(
        self,
        text: str,
        model: str | None = None,
        effort: str | None = None,
        local_image_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(text, str):
            raise BridgeError("EMPTY_INPUT", "Natural-language input must not be empty")
        if len(text) > 65536:
            raise BridgeError("INPUT_TOO_LARGE", "Input exceeds the 65536 character limit")
        model = self._validated_optional_selection(
            model,
            "model",
            MODEL_IDENTIFIER_MAX_LENGTH,
        )
        effort = self._validated_optional_selection(
            effort,
            "effort",
            REASONING_EFFORT_MAX_LENGTH,
        )
        with self._lock:
            thread_id = self._validated_identifier(self._thread_id, "thread_id")
            image_paths = self._validated_local_image_paths(
                local_image_paths,
                thread_id,
            )
            if not text.strip() and not image_paths:
                raise BridgeError(
                    "EMPTY_INPUT",
                    "Natural-language input or at least one image is required",
                )
            self._require_no_active_turn_locked()
            self._turn_generation += 1
            generation = self._turn_generation
            self._turn_id = None
            self._turn_status = "starting"
            self._turn_active = True
            self._turn_created = False

        try:
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
            params: dict[str, Any] = {
                "threadId": thread_id,
                "input": turn_input,
                "cwd": str(self._project_root),
                "approvalPolicy": "on-request",
                "sandboxPolicy": {
                    "type": "workspaceWrite",
                    "networkAccess": False,
                },
            }
            if model is not None:
                params["model"] = model
            if effort is not None:
                params["effort"] = effort
            result = self._client.request("turn/start", params)
            turn_id = self._extract_turn_id(result)
        except CodexRPCError as exc:
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
                    confirmed_not_created = True
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
                    exc.code,
                    exc.message,
                    exc.http_status,
                    details,
                ) from exc
            raise
        except Exception:
            with self._lock:
                if (
                    generation == self._turn_generation
                    and self._turn_active
                    and not self._turn_created
                ):
                    self._turn_status = "startUnknown"
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
                if self._turn_active and self._turn_status == "starting":
                    self._turn_status = "inProgress"
                publish_selection = True
        if publish_selection:
            self._events.publish(
                "turn_selected",
                thread_id=thread_id,
                turn_id=turn_id,
            )
        return {"thread_id": thread_id, "turn_id": turn_id, "result": result}

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

        with self._lock:
            thread_id = self._validated_identifier(self._thread_id, "thread_id")
            turn_id = self._turn_id
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
        with self._lock:
            if (
                generation != self._turn_generation
                or not self._turn_active
                or self._thread_id != thread_id
                or self._turn_id != acknowledged_turn_id
            ):
                raise BridgeError(
                    "TURN_CHANGED_DURING_STEER",
                    "The active Turn changed before turn/steer was acknowledged",
                    http_status=409,
                    details={
                        "expected_turn_id": turn_id,
                        "acknowledged_turn_id": acknowledged_turn_id,
                        "current_turn_id": self._turn_id,
                        "turn_active": self._turn_active,
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
        with self._lock:
            thread_id = self._thread_id
            turn_id = self._turn_id
            if not self._turn_active or not all(
                self._identifier_is_valid(value) for value in (thread_id, turn_id)
            ):
                raise self._no_active_turn_error_locked()
        result = self._client.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
        )
        return {"thread_id": thread_id, "turn_id": turn_id, "result": result}

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

    @staticmethod
    def _extract_thread_id(result: Any) -> str:
        if not isinstance(result, dict) or not isinstance(result.get("thread"), dict):
            raise BridgeError("INVALID_CODEX_RESPONSE", "Thread response has no thread object", 502)
        thread_id = result["thread"].get("id")
        return BridgeSession._validated_identifier(thread_id, "thread_id")

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

    def _on_client_event(self, event: dict[str, Any]) -> None:
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
                    return
        elif event_type == "codex_notification":
            method = event.get("method")
            params = event.get("params")
            params = params if isinstance(params, dict) else {}
            with self._lock:
                if method == "thread/started":
                    thread = params.get("thread")
                    if isinstance(thread, dict) and isinstance(thread.get("id"), str):
                        self._thread_id = thread["id"]
                elif method == "turn/started":
                    turn = params.get("turn")
                    thread_id = params.get("threadId")
                    turn_id = turn.get("id") if isinstance(turn, dict) else None
                    if (
                        self._turn_active
                        and thread_id == self._thread_id
                        and self._identifier_is_valid(turn_id)
                        and self._turn_id in {None, turn_id}
                    ):
                        self._turn_id = turn_id
                        self._turn_status = "inProgress"
                        self._turn_created = True
                elif method == "turn/completed":
                    turn = params.get("turn")
                    thread_id = params.get("threadId")
                    turn_id = turn.get("id") if isinstance(turn, dict) else None
                    if (
                        isinstance(turn, dict)
                        and thread_id == self._thread_id
                        and self._identifier_is_valid(turn_id)
                        and turn_id == self._turn_id
                    ):
                        status = turn.get("status")
                        self._turn_status = (
                            status
                            if status in {"completed", "interrupted", "failed"}
                            else "completed"
                        )
                        self._turn_active = False
                        self._turn_created = True
        elif event_type == "process_exit":
            with self._lock:
                self._connected = False
        fields = {key: value for key, value in event.items() if key != "type"}
        self._events.publish(str(event_type), **fields)
