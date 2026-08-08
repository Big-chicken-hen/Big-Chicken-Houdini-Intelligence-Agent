"""Connection and Thread/Turn state without a duplicate chat store."""

from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from .codex_stdio import CodexStdioClient
from .errors import BridgeError, CodexRPCError
from .events import EventBuffer
from .scene_writer import SceneWriterOwnership, SceneWriterReservation
from .thread_rotation import ThreadRotationProfile


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
THREAD_LIST_PAGE_SIZE = 100
THREAD_LIST_MAX_PAGES = 32
THREAD_LIST_MAX_ENTRIES = 4096
THREAD_CURSOR_MAX_LENGTH = 4096
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
        scene_writer: SceneWriterOwnership | None = None,
    ) -> None:
        if mcp_backend not in {HIA_MCP_V2_BACKEND, FXHOUDINI_MCP_BACKEND}:
            raise ValueError(f"Unsupported Houdini MCP backend: {mcp_backend}")
        self._project_root = project_root
        self._focus_state_path = focus_state_path
        (
            self._focus_enabled_threads,
            self._focus_goal_bindings,
        ) = self._load_focus_state()
        self._client = client
        self._events = events
        self._mcp_backend = mcp_backend
        self._scene_writer = scene_writer or SceneWriterOwnership()
        self._lock = threading.RLock()
        self._turn_condition = threading.Condition(self._lock)
        self._connected = False
        self._initialize_result: Any = None
        self._account_result: dict[str, Any] | None = None
        self._account_error: dict[str, Any] | None = None
        self._thread_id: str | None = None
        self._ordinary_thread_ids: set[str] = set()
        self._ordinary_thread_profiles: dict[str, ThreadRotationProfile] = {}
        self._turn_id: str | None = None
        self._turn_status: str | None = None
        self._turn_active = False
        self._turn_created = False
        self._turn_generation = 0
        self._start_source_turn_id: str | None = None
        self._last_tool_name: str | None = None
        self._last_tool_status: str | None = None
        self._scene_writer_reservation: SceneWriterReservation | None = None
        self._scene_writer_owner: str | None = None
        self._active_hia_item_ids: set[str] = set()
        self._anonymous_hia_items = 0
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
                self._thread_id = None
                self._ordinary_thread_ids.clear()
                self._ordinary_thread_profiles.clear()
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
                "确定操作不枚举，直接调用目标；HOM先编译，类别API仅在对象支持时调用。"
                "场景默认用 HIA MCP V2 与 HOM。明确小改只读目标值后直接执行；"
                "修改既有网络先用 hia_context/hia_inspect 读取目标、输入输出、两层上游、"
                "公共控制、材质入口、引用和允许 scope。优先现有节点和标准原生节点网络；"
                "HOM 只做短批量编排，场景内 Python SOP 或直接几何须说明必要性。"
                "同一 scope 内一次 hia_execute_hom，随后 fresh cook、hia_scene_diff/hia_validate；"
                "Goal 阶段只按完整结构化场景证据验收，plan/revision/tool completed 不算通过。"
                "视觉任务只在里程碑用 hia_capture_viewport，不固定尺寸；"
                "静态一帧，动画/模拟用代表帧或短序列；材质验收读取绑定、MaterialX 连接和正确输入。"
                "Stop 后已提交的 HOM 仍可能收尾；失败后先显式 inspect、diff、validate，"
                "根据新证据决定下一步，禁止自动重试；unknown/partial/NO_OBSERVED_EFFECT "
                "不 Undo 也不算完成；session/source drift 停写并正常重启，不热加载。"
                "仅主任务串行调用 hia_*/HOM 并写 HIP；子任务只研究、草拟、只读审阅；"
                "MCP 无 caller lineage，非代码级隔离；同类读取不并发扇出，"
                "多个关键词合并为一次批量查询；QUEUE_FULL 不立即重试。"
                "阶段结果只按实际场景证据报告；失败阶段先检查当前场景再修正。"
            )
        else:
            backend_instructions = (
                "当前场景的创建、修改、连接、材质和动画默认使用已注册的 FXHoudini MCP 与 HOM；"
                "复杂操作优先用 execute_python 批量执行 Codex 生成的 HOM。"
                "细粒度工具用于读取、单项修改和最终验证，不要逐节点循环。"
                "相同调用失败后先读真实错误再改用兼容方法；capture_screenshot 只做阶段性验证。"
                "模型行为约束：主代理负责当前 HIP 写入，子代理只做研究、草案和审阅；"
                "FX fallback 同样非代码级隔离。"
            )
        common_instructions = (
            "复杂创建、修改或修复在首次场景写入前先做一次相关本地知识批量检索并复用结果；"
            "复杂、参考驱动、材质、FX、模拟、渲染或版本不确定任务还必须研究当前 SideFX 官方与原始来源；"
            "需要发现来源时明确用原生 web search，需要读取用户给定公开 URL 时明确用 openPage；"
            "两者失败均直接报告，不得互相作为隐藏 fallback。"
            "本地检索不可用时明确说明，禁止静默跳过；"
            "简单参数/连接/删除/重命名/布局不强制知识检索或网页研究。"
            "实时 MCP 不可用时直接说明，不得改成离线 HIP。"
            "只有用户明确要求离线、独立 HIP、批处理或后台渲染时才用 PATH 中的 hython.exe。"
            "普通场景请求不先搜索项目源码/文档；仅诊断或修改 Panel、Bridge、MCP/项目代码时读取。"
            "主任务只保留原生 Goal、决定和子任务短摘要；子任务详情按需查看，"
            "不塞入主上下文，采纳结果由主任务公开说明。"
            "上下文仅用 app-server 自动整理，不手动 compact，不创建本地摘要或记忆。"
            "实时代码禁止 hou.hipFile.clear/load/save，不替换当前场景；新资产放入唯一新根。"
            "不要调用 request_user_input；信息不足时采用合理默认值，无法执行才报告原因。"
            "HIA 截图只能用 SceneViewer.flipbook 写入 HIA_CACHE_DIR/screenshots；不可用时明确失败；"
            "预览写 previews，中间图写 tmp，附件/知识/模型/索引仍留项目 .runtime；文件名加时间戳和短随机后缀。"
            "用户明确指定的最终渲染、EXR、视频、USD、模拟缓存或导出是用户交付物，"
            "可写所选普通本地项目外目录；未指定才用 HIA_RENDER_OUTPUT_DIR，并始终报告最终路径。"
            "禁止屏幕接管。"
        )
        if self._mcp_backend == HIA_MCP_V2_BACKEND:
            common_instructions = (
                "复杂创建、修改或修复在首次场景写入前先用 hia_local_help_search 做一次相关批量检索并复用结果；"
                "复杂、参考驱动、材质、FX、模拟、渲染或版本不确定任务必须研究当前 SideFX 官方与原始来源；"
                "发现来源明确用原生 web search，读取给定公开 URL 明确用 openPage；失败不得互相隐藏切换；"
                "检索不可用时明确说明，禁止静默跳过；"
                "简单参数/连接/删除/重命名/布局不强制知识检索或网页研究。"
                "MCP 不可用就说明，不转离线 HIP；hython 仅用于用户明确的离线/独立 HIP/批处理/后台渲染。"
                "普通场景不查项目源码。"
                "主任务只保留原生 Goal、决定和子任务短摘要；子任务详情按需查看，不塞入主上下文；主任务公开采纳。"
                "禁止 hou.hipFile.clear/load/save 和替换当前场景；新资产置于唯一新根。"
                "不调用 request_user_input；信息不足用默认，无法执行才报告。"
                "HIA 截图只能用 SceneViewer.flipbook 写入 HIA_CACHE_DIR/screenshots；不可用时明确失败；"
                "预览写 previews，中间图写 tmp，附件/知识/模型/索引留 .runtime。"
                "用户指定的最终渲染/EXR/视频/USD/模拟缓存/导出可写所选普通项目外目录；"
                "否则用 HIA_RENDER_OUTPUT_DIR；始终报告最终路径。禁止屏幕接管。"
            )
        return backend_instructions + common_instructions

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
        with self._lock:
            self._require_no_active_turn_locked()
        params: dict[str, Any] = {
            "cwd": str(self._project_root),
            "approvalPolicy": "never",
            "sandbox": "workspace-write",
            "ephemeral": False,
            "developerInstructions": self._developer_instructions(),
            "serviceTier": service_tier,
            "threadSource": "appServer",
        }
        if model is not None:
            params["model"] = model
        result = self._client.request("thread/start", params)
        thread_id = self._extract_thread_id(result)
        rotation_profile = self._rotation_profile_from_response(result)
        with self._lock:
            self._ordinary_thread_ids.add(thread_id)
            if rotation_profile is not None:
                self._ordinary_thread_profiles[thread_id] = rotation_profile
            self._thread_id = thread_id
            self._reset_turn_locked()
            self._write_focus_state_locked()
        self._events.publish("thread_selected", action="start", thread_id=thread_id)
        return {
            "thread_id": thread_id,
            "focus_mode": False,
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
            self._require_no_active_turn_locked()
            self._require_ordinary_thread(thread_id)
        resumed = self._client.request(
            "thread/resume",
            {
                "threadId": thread_id,
                "cwd": str(self._project_root),
                "approvalPolicy": "never",
                "sandbox": "workspace-write",
                "developerInstructions": self._developer_instructions(),
                "serviceTier": service_tier,
            },
        )
        resolved_id = self._extract_thread_id(resumed)
        self._require_ordinary_thread_result(resumed, resolved_id)
        rotation_profile = self._rotation_profile_from_response(resumed)
        read_result = self._thread_messages_projection(resumed, resolved_id)
        with self._lock:
            self._thread_id = resolved_id
            if rotation_profile is not None:
                self._ordinary_thread_profiles[resolved_id] = rotation_profile
            else:
                self._ordinary_thread_profiles.pop(resolved_id, None)
            self._reset_turn_locked()
            self._write_focus_state_locked()
        self._events.publish("thread_selected", action="resume", thread_id=resolved_id)
        return {
            "thread_id": resolved_id,
            "focus_mode": self._focus_mode_locked(resolved_id),
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
        self._require_ordinary_thread_result(result, selected)
        return {
            "thread_id": selected,
            "result": self._thread_messages_projection(result, selected),
        }

    def list_threads(self) -> dict[str, Any]:
        """Return every bounded native Thread for this project."""

        project_root = _normalized_thread_cwd(str(self._project_root))
        threads: list[dict[str, Any]] = []
        seen: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        raw_entry_count = 0

        for page_number in range(1, THREAD_LIST_MAX_PAGES + 1):
            params: dict[str, Any] = {
                "cwd": _thread_cwd_filters(str(self._project_root)),
                "archived": False,
                "limit": THREAD_LIST_PAGE_SIZE,
                "modelProviders": [],
                "useStateDbOnly": True,
                "sortKey": "recency_at",
                "sortDirection": "desc",
            }
            if cursor is not None:
                params["cursor"] = cursor
            result = self._client.request("thread/list", params)
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise self._invalid_thread_response("Response data must be an array")

            raw_entry_count += len(result["data"])
            if raw_entry_count > THREAD_LIST_MAX_ENTRIES:
                raise BridgeError(
                    "THREAD_LIST_LIMIT_EXCEEDED",
                    "Codex Thread history exceeded the Bridge entry limit",
                    http_status=502,
                    details={"max_entries": THREAD_LIST_MAX_ENTRIES},
                )
            for entry in result["data"]:
                if not isinstance(entry, dict):
                    raise self._invalid_thread_response("Thread entry must be an object")
                thread_id = self._validated_thread_response_string(
                    entry.get("id"), "id", MODEL_IDENTIFIER_MAX_LENGTH, allow_empty=False
                )
                cwd = self._validated_thread_response_string(
                    entry.get("cwd"), "cwd", THREAD_CWD_MAX_LENGTH, allow_empty=False
                )
                if thread_id in seen:
                    raise self._invalid_thread_response(
                        "Response contains a duplicate thread id", field="id"
                    )
                seen.add(thread_id)
                if _normalized_thread_cwd(cwd) != project_root:
                    continue
                with self._lock:
                    self._ordinary_thread_ids.add(thread_id)

                raw_name = entry.get("name")
                name = None
                if raw_name is not None:
                    name = self._validated_thread_response_string(
                        raw_name, "name", THREAD_NAME_MAX_LENGTH, allow_empty=True
                    )
                preview = self._sanitized_thread_preview(entry.get("preview"))
                updated_at = entry.get("updatedAt")
                if (
                    not isinstance(updated_at, int)
                    or isinstance(updated_at, bool)
                    or updated_at < 0
                ):
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

            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return {"threads": threads}
            next_cursor = self._validated_thread_response_string(
                next_cursor,
                "nextCursor",
                THREAD_CURSOR_MAX_LENGTH,
                allow_empty=False,
            )
            if next_cursor in seen_cursors:
                raise self._invalid_thread_response(
                    "Response contains a repeated pagination cursor",
                    field="nextCursor",
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            if page_number == THREAD_LIST_MAX_PAGES:
                raise BridgeError(
                    "THREAD_LIST_LIMIT_EXCEEDED",
                    "Codex Thread history exceeded the Bridge page limit",
                    http_status=502,
                    details={"max_pages": THREAD_LIST_MAX_PAGES},
                )

        raise AssertionError("unreachable Thread pagination state")

    def rename_thread(self, thread_id: str, name: str) -> dict[str, Any]:
        thread_id = self._validated_identifier(thread_id, "thread_id")
        self._require_ordinary_thread(thread_id)
        name = self._validated_optional_selection(
            name, "thread_name", THREAD_NAME_MAX_LENGTH
        )
        if name is None:
            raise BridgeError("INVALID_THREAD_NAME", "thread_name is required")
        result = self._client.request(
            "thread/name/set", {"threadId": thread_id, "name": name}
        )
        return {"thread_id": thread_id, "name": name, "result": result}

    def delete_thread(self, thread_id: str) -> dict[str, Any]:
        """Permanently delete one explicitly selected Codex Thread."""

        thread_id = self._validated_identifier(thread_id, "thread_id")
        with self._lock:
            self._require_no_active_turn_locked()
            self._require_ordinary_thread(thread_id)

        result = self._client.request(
            "thread/delete",
            {"threadId": thread_id},
        )
        removed_cache_paths, cache_cleanup_error = self._cleanup_thread_cache(
            thread_id
        )

        cleanup_warning: dict[str, Any] | None = None
        with self._lock:
            was_selected = self._thread_id == thread_id
            self._focus_enabled_threads.discard(thread_id)
            self._focus_goal_bindings.pop(thread_id, None)
            self._ordinary_thread_ids.discard(thread_id)
            self._ordinary_thread_profiles.pop(thread_id, None)
            if was_selected:
                self._thread_id = None
                self._reset_turn_locked()
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
            "cache_cleanup": {
                "complete": cache_cleanup_error is None,
                "removed_paths": removed_cache_paths,
            },
        }
        if cache_cleanup_error is not None:
            event_fields["cache_cleanup_error"] = cache_cleanup_error
            response["cache_cleanup"]["error"] = cache_cleanup_error
        if cleanup_warning is not None:
            event_fields["cleanup_warning"] = cleanup_warning
            response["cleanup_warning"] = cleanup_warning
        self._events.publish("thread_deleted", **event_fields)
        return response

    def _cleanup_thread_cache(self, thread_id: str) -> tuple[list[str], str | None]:
        """Remove only the deleted Thread's project-local attachment cache."""

        attachments_root = (
            self._project_root / ".runtime" / "attachments"
        ).resolve()
        candidate = attachments_root / thread_id
        if not candidate.exists():
            return [], None
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.parent != attachments_root or not resolved.is_dir():
                raise OSError("attachment cache target is not one safe Thread directory")
            shutil.rmtree(resolved)
        except (OSError, RuntimeError) as exc:
            return [], f"{type(exc).__name__}: {exc}"
        return [str(resolved)], None

    def get_goal(self, expected_thread_id: str) -> dict[str, Any]:
        thread_id = self._selected_thread_id(expected_thread_id)
        self._require_ordinary_thread(thread_id)
        result = self._client.request(
            "thread/goal/get",
            {"threadId": thread_id},
        )
        self._selected_thread_id(thread_id)
        goal = self._goal_projection(result, thread_id, allow_none=True)
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
        self._require_ordinary_thread(thread_id)
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
        goal = self._goal_projection(result, thread_id, allow_none=False)
        self._reconcile_focus_goal(thread_id, goal)
        return {
            "thread_id": thread_id,
            "goal": goal,
            "focus_mode": self._focus_mode_for_thread(thread_id),
        }

    def clear_goal(self, expected_thread_id: str) -> dict[str, Any]:
        thread_id = self._selected_thread_id(expected_thread_id)
        self._require_ordinary_thread(thread_id)
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
        self._require_ordinary_thread(thread_id)
        if enabled:
            result = self._client.request(
                "thread/goal/get",
                {"threadId": thread_id},
            )
            goal = self._goal_projection(result, thread_id, allow_none=True)
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

    def start_turn(
        self,
        text: str,
        model: str | None = None,
        effort: str | None = None,
        local_image_paths: list[str] | None = None,
        service_tier: str | None = None,
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
        service_tier = self._validated_optional_selection(
            service_tier,
            "service_tier",
            SERVICE_TIER_MAX_LENGTH,
        )
        with self._lock:
            thread_id = self._validated_identifier(self._thread_id, "thread_id")
            self._require_ordinary_thread(thread_id)
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
            reservation = self._scene_writer.reserve(thread_id)
            self._scene_writer_reservation = reservation
            self._scene_writer_owner = None
            self._active_hia_item_ids.clear()
            self._anonymous_hia_items = 0
            self._turn_generation += 1
            generation = self._turn_generation
            self._start_source_turn_id = self._turn_id or self._start_source_turn_id
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
                "approvalPolicy": "never",
                "sandboxPolicy": {
                    "type": "workspaceWrite",
                    "networkAccess": True,
                },
                "serviceTier": service_tier,
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
                    self._start_source_turn_id = None
                    confirmed_not_created = True
            if confirmed_not_created:
                self._scene_writer.abandon_uncreated(reservation)
                with self._lock:
                    if self._scene_writer_reservation == reservation:
                        self._scene_writer_reservation = None
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
        writer_terminal = False
        writer_items: tuple[str, ...] = ()
        writer_anonymous_items = 0
        owner: str | None = None
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
                owner = self._scene_writer.bind(reservation, thread_id, turn_id)
                self._scene_writer_owner = owner
                profile = self._ordinary_thread_profiles.get(thread_id)
                if profile is not None:
                    runtime_changes: dict[str, Any] = {}
                    if model is not None:
                        runtime_changes["model"] = model
                    if effort is not None:
                        runtime_changes["reasoning_effort"] = effort
                    if service_tier is not None:
                        runtime_changes["service_tier"] = service_tier
                    if runtime_changes:
                        self._ordinary_thread_profiles[thread_id] = replace(
                            profile,
                            **runtime_changes,
                        )
                writer_items = tuple(self._active_hia_item_ids)
                writer_anonymous_items = self._anonymous_hia_items
                writer_terminal = not self._turn_active
                self._start_source_turn_id = None
                if self._turn_active and self._turn_status == "starting":
                    self._turn_status = "inProgress"
                publish_selection = True
        if owner is not None:
            for item_id in writer_items:
                self._scene_writer.hia_started(owner, item_id)
            for _ in range(writer_anonymous_items):
                self._scene_writer.hia_anonymous_started(owner)
            if writer_terminal and self._scene_writer.turn_terminal(owner):
                with self._lock:
                    if self._scene_writer_owner == owner:
                        self._scene_writer_owner = None
                        self._scene_writer_reservation = None
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
            self._require_ordinary_thread(thread_id)
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
            thread_id = self._thread_id
            turn_id = self._turn_id
            if not self._turn_active or not all(
                self._identifier_is_valid(value) for value in (thread_id, turn_id)
            ):
                raise self._no_active_turn_error_locked()
            self._require_ordinary_thread(str(thread_id))
            if self._turn_status == "stopRequested":
                raise BridgeError(
                    "INTERRUPT_NOT_CONFIRMED",
                    "The prior interrupt request is still unconfirmed",
                    http_status=409,
                )
            generation = self._turn_generation
            prior_status = self._turn_status
            self._turn_status = "stopRequested"
            houdini_may_still_be_finishing = (
                self._tool_may_still_be_running_locked()
            )

        self._events.publish("session_state", session=self.snapshot())
        result: Any = None
        request_with_timeout = getattr(self._client, "request_with_timeout", None)
        if not callable(request_with_timeout):
            with self._lock:
                if (
                    generation == self._turn_generation
                    and self._thread_id == thread_id
                    and self._turn_id == turn_id
                    and self._turn_active
                    and self._turn_status == "stopRequested"
                ):
                    self._turn_status = prior_status
            raise BridgeError(
                "INTERRUPT_NOT_CONFIRMED",
                "Codex interrupt transport is unavailable, so interruption was not confirmed",
                http_status=503,
            )
        try:
            result = request_with_timeout(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
                timeout_seconds=STOP_INTERRUPT_GRACE_SECONDS,
            )
        except (BridgeError, CodexRPCError):
            # A lost or late interrupt acknowledgement is not terminal evidence.
            # The exact Turn remains authoritative; no recovery action follows.
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
                owner = self._scene_writer_owner
                if (
                    isinstance(owner, str)
                    and self._scene_writer.retained_after_terminal(owner)
                ):
                    raise BridgeError(
                        "SCENE_WRITER_STILL_ACTIVE",
                        "The Turn stopped, but an HIA scene write is still active",
                        http_status=409,
                        details={
                            "thread_id": thread_id,
                            "turn_id": turn_id,
                            "owner": owner,
                        },
                    )
                return {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "result": result,
                    "houdini_may_still_be_finishing": False,
                    "session": snapshot,
                }

        with self._turn_condition:
            if (
                generation == self._turn_generation
                and self._thread_id == thread_id
                and self._turn_id == turn_id
                and self._turn_active
                and self._turn_status == "stopRequested"
            ):
                self._turn_status = prior_status
            snapshot = self.snapshot()
        self._events.publish("session_state", session=snapshot)
        raise BridgeError(
            "INTERRUPT_NOT_CONFIRMED",
            "Codex did not confirm that the active Turn was interrupted",
            http_status=504,
            details={
                "thread_id": thread_id,
                "turn_id": turn_id,
                "houdini_may_still_be_finishing": houdini_may_still_be_finishing,
            },
        )

    def _tool_may_still_be_running_locked(self) -> bool:
        return (
            isinstance(self._last_tool_name, str)
            and bool(self._last_tool_name)
            and self._last_tool_status not in {"completed", "failed"}
        )

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
    def _goal_projection(
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
        if not self._turn_active and self._scene_writer_owner is None:
            return
        if not self._turn_active:
            raise BridgeError(
                "SCENE_WRITER_STILL_ACTIVE",
                "The Turn ended, but an HIA scene write is still active",
                http_status=409,
                details={
                    "turn_active": False,
                    "thread_id": self._thread_id,
                    "turn_id": self._turn_id,
                    "turn_status": self._turn_status,
                    "owner": self._scene_writer_owner,
                },
            )
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
    def _is_hia_item(item: Mapping[str, Any]) -> bool:
        server = item.get("server")
        tool = item.get("tool")
        return server in {"hia_mcp_v2", "houdini_intelligence"} or (
            isinstance(tool, str) and tool.startswith("hia_")
        )

    @staticmethod
    def _extract_thread_id(result: Any) -> str:
        if not isinstance(result, dict) or not isinstance(result.get("thread"), dict):
            raise BridgeError("INVALID_CODEX_RESPONSE", "Thread response has no thread object", 502)
        thread_id = result["thread"].get("id")
        return BridgeSession._validated_identifier(thread_id, "thread_id")

    def _rotation_profile_from_response(
        self, result: Any
    ) -> ThreadRotationProfile | None:
        if not isinstance(result, Mapping):
            return None
        thread = result.get("thread")
        model = result.get("model")
        sandbox = result.get("sandbox")
        if (
            not isinstance(thread, Mapping)
            or not isinstance(model, str)
            or not model
            or not isinstance(sandbox, Mapping)
        ):
            return None
        reasoning_effort = result.get("reasoningEffort")
        service_tier = result.get("serviceTier")
        if reasoning_effort is not None and not isinstance(reasoning_effort, str):
            return None
        if service_tier is not None and not isinstance(service_tier, str):
            return None
        thread_source = thread.get("threadSource")
        if not isinstance(thread_source, str) or not thread_source:
            return None
        cwd = result.get("cwd")
        return ThreadRotationProfile(
            cwd=cwd if isinstance(cwd, str) and cwd else str(self._project_root),
            developer_instructions=self._developer_instructions(),
            ephemeral=False,
            thread_source=thread_source,
            model=model,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
            fork_sandbox="workspace-write",
            response_sandbox=copy.deepcopy(dict(sandbox)),
            config={},
        )

    def owns_ordinary_thread(self, thread_id: str) -> bool:
        with self._lock:
            return thread_id in self._ordinary_thread_ids

    def rotation_profile(
        self, thread_id: str, last_turn_id: str
    ) -> ThreadRotationProfile | None:
        if not self._identifier_is_valid(last_turn_id):
            return None
        with self._lock:
            if thread_id not in self._ordinary_thread_ids:
                return None
            profile = self._ordinary_thread_profiles.get(thread_id)
            return copy.deepcopy(profile) if profile is not None else None

    def rotation_is_idle(self, thread_id: str) -> bool:
        with self._lock:
            if thread_id not in self._ordinary_thread_ids or self._closed:
                return False
            if thread_id == self._thread_id and (
                self._turn_active
                or self._active_hia_item_ids
                or self._scene_writer_reservation is not None
                or self._scene_writer_owner is not None
            ):
                return False
        writer = self._scene_writer.snapshot()
        return writer.get("thread_id") != thread_id

    def rotation_rebind(self, old_thread_id: str, new_thread_id: str) -> None:
        with self._lock:
            if (
                old_thread_id not in self._ordinary_thread_ids
                or new_thread_id in self._ordinary_thread_ids
            ):
                raise ValueError("ordinary Thread rotation identity is stale")
            profile = self._ordinary_thread_profiles.get(old_thread_id)
            if profile is None:
                raise ValueError("ordinary Thread rotation profile is missing")
            old_ids = set(self._ordinary_thread_ids)
            old_profiles = dict(self._ordinary_thread_profiles)
            old_selected = self._thread_id
            old_focus = set(self._focus_enabled_threads)
            old_bindings = dict(self._focus_goal_bindings)
            try:
                self._ordinary_thread_ids.remove(old_thread_id)
                self._ordinary_thread_ids.add(new_thread_id)
                self._ordinary_thread_profiles.pop(old_thread_id, None)
                self._ordinary_thread_profiles[new_thread_id] = profile
                if self._thread_id == old_thread_id:
                    self._thread_id = new_thread_id
                if old_thread_id in self._focus_enabled_threads:
                    self._focus_enabled_threads.remove(old_thread_id)
                    self._focus_enabled_threads.add(new_thread_id)
                binding = self._focus_goal_bindings.pop(old_thread_id, None)
                if binding is not None:
                    self._focus_goal_bindings[new_thread_id] = binding
                self._write_focus_state_locked()
            except Exception:
                self._ordinary_thread_ids = old_ids
                self._ordinary_thread_profiles = old_profiles
                self._thread_id = old_selected
                self._focus_enabled_threads = old_focus
                self._focus_goal_bindings = old_bindings
                raise

    def _require_ordinary_thread(self, thread_id: str) -> None:
        with self._lock:
            if thread_id in self._ordinary_thread_ids:
                return
        result = self._client.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": False},
        )
        self._require_ordinary_thread_result(result, thread_id)

    def _require_ordinary_thread_result(self, result: Any, thread_id: str) -> None:
        resolved_id = BridgeSession._extract_thread_id(result)
        if resolved_id != thread_id:
            raise BridgeError(
                "INVALID_CODEX_RESPONSE",
                "Thread response does not match the requested thread",
                502,
            )
        with self._lock:
            self._ordinary_thread_ids.add(thread_id)

    @staticmethod
    def _thread_messages_projection(
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
        anonymous_hia_protocol_error: tuple[str, str] | None = None
        event_type = event.get("type")
        if event_type == "codex_notification":
            method = event.get("method")
            params = event.get("params")
            params = params if isinstance(params, dict) else {}
            with self._lock:
                if method == "turn/started":
                    turn = params.get("turn")
                    thread_id = params.get("threadId")
                    turn_id = turn.get("id") if isinstance(turn, dict) else None
                    if (
                        self._turn_active
                        and thread_id == self._thread_id
                        and self._identifier_is_valid(turn_id)
                        and turn_id != self._start_source_turn_id
                        and self._turn_id in {None, turn_id}
                    ):
                        self._turn_id = turn_id
                        self._turn_status = "inProgress"
                        self._turn_created = True
                        self._last_tool_name = None
                        self._last_tool_status = None
                elif method == "turn/completed":
                    turn = params.get("turn")
                    thread_id = params.get("threadId")
                    turn_id = turn.get("id") if isinstance(turn, dict) else None
                    if (
                        isinstance(turn, dict)
                        and thread_id == self._thread_id
                        and self._identifier_is_valid(turn_id)
                        and turn_id != self._start_source_turn_id
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
                        owner = self._scene_writer_owner
                        if (
                            isinstance(owner, str)
                            and self._scene_writer.turn_terminal(owner)
                        ):
                            self._scene_writer_owner = None
                            self._scene_writer_reservation = None
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
                        owner = self._scene_writer_owner
                        item_id = item.get("id")
                        is_hia_item = self._is_hia_item(item)
                        exact_item_id = (
                            item_id
                            if isinstance(item_id, str) and bool(item_id)
                            else None
                        )
                        if is_hia_item and exact_item_id is None:
                            if method == "item/started":
                                self._anonymous_hia_items += 1
                                if isinstance(owner, str):
                                    self._scene_writer.hia_anonymous_started(owner)
                            else:
                                if self._anonymous_hia_items > 0:
                                    self._anonymous_hia_items -= 1
                                if (
                                    isinstance(owner, str)
                                    and self._scene_writer.hia_anonymous_finished(owner)
                                ):
                                    self._scene_writer_owner = None
                                    self._scene_writer_reservation = None
                            anonymous_hia_protocol_error = (thread_id, turn_id)
                            self._last_tool_status = "failed"
                        elif method == "item/started" and is_hia_item:
                            self._active_hia_item_ids.add(exact_item_id)
                            if isinstance(owner, str):
                                self._scene_writer.hia_started(owner, exact_item_id)
                        elif method == "item/completed" and is_hia_item:
                            self._active_hia_item_ids.discard(exact_item_id)
                            if (
                                isinstance(owner, str)
                                and self._scene_writer.hia_finished(
                                    owner, exact_item_id
                                )
                            ):
                                self._scene_writer_owner = None
                                self._scene_writer_reservation = None
                        tool_name = item.get("tool")
                        if isinstance(tool_name, str) and tool_name:
                            self._last_tool_name = " ".join(tool_name.split())[:128]
                            status = item.get("status")
                            self._last_tool_status = (
                                " ".join(status.split())[:64]
                                if isinstance(status, str) and status
                                else (
                                    "started"
                                    if method == "item/started"
                                    else "completed"
                                )
                            )
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
                        self._ordinary_thread_ids.discard(thread_id)
                        self._ordinary_thread_profiles.pop(thread_id, None)
                        self._focus_enabled_threads.discard(thread_id)
                        self._focus_goal_bindings.pop(thread_id, None)
                        if self._thread_id == thread_id:
                            self._thread_id = None
                            self._reset_turn_locked()
                        try:
                            self._write_focus_state_locked()
                        except BridgeError:
                            pass
        elif event_type == "process_exit":
            with self._turn_condition:
                self._connected = False
                self._turn_condition.notify_all()
        if anonymous_hia_protocol_error is not None:
            thread_id, turn_id = anonymous_hia_protocol_error
            self._events.publish(
                "protocol_warning",
                code="INVALID_HIA_ITEM_ID",
                message="Tracked an anonymous HIA item lifecycle after its exact id was omitted",
                thread_id=thread_id,
                turn_id=turn_id,
            )
        fields = {key: value for key, value in event.items() if key != "type"}
        self._events.publish(str(event_type), **fields)
