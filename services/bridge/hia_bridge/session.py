"""Connection and Thread/Turn state without a duplicate chat store."""

from __future__ import annotations

import copy
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


class BridgeSession:
    """Own the Codex child and only the connection identifiers needed by UI."""

    def __init__(
        self,
        project_root: Path,
        client: CodexStdioClient,
        events: EventBuffer,
    ) -> None:
        self._project_root = project_root
        self._client = client
        self._events = events
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
                "codex_pid": self._client.process_id,
                "authentication": self._authentication_status(account, account_error),
                "account": account,
                "account_error": account_error,
                "thread_id": self._thread_id,
                "turn_id": self._turn_id,
                "turn_status": self._turn_status,
                "turn_active": self._turn_active,
            }

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
    ) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
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
            self._require_no_active_turn_locked()
            self._turn_generation += 1
            generation = self._turn_generation
            self._turn_id = None
            self._turn_status = "starting"
            self._turn_active = True
            self._turn_created = False

        try:
            params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [
                    {
                        "type": "text",
                        "text": text,
                        "text_elements": [],
                    }
                ],
                "cwd": str(self._project_root),
                "approvalPolicy": "on-request",
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
        if decision not in {"allow", "deny"}:
            raise BridgeError(
                "INVALID_APPROVAL_DECISION",
                "Approval decision must be 'allow' or 'deny'",
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
        if method in {
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

    def _on_client_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "codex_notification":
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
