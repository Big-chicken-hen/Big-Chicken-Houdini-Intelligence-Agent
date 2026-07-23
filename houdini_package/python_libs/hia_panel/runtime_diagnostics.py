"""Small project-local Markdown writer for one diagnostic report per Codex Turn."""

from __future__ import annotations

import json
import math
import os
import re
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_MAX_DEPTH = 6
_MAX_COLLECTION_ITEMS = 64
_MAX_ATTACHMENTS = 32
_MAX_TEXT = 4_000
_MAX_LONG_TEXT = 16_000
_MAX_SUMMARY = 1_000
_MAX_SLUG = 48

_SENSITIVE_KEY_PARTS = (
    "token",
    "authorization",
    "cookie",
    "apikey",
    "secret",
    "password",
    "credential",
)
_SENSITIVE_KEY_NAMES = frozenset({"access", "refresh", "login"})
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[^\s,;\"']+")
_AUTHORIZATION_SCHEME_PATTERN = re.compile(
    r"(?i)(\b(?:proxy[-_\s]?authorization|authorization)\s*[:=]\s*)"
    r"(?:basic|digest|bearer)\s+[^\s,;\"']+"
)
_BASIC_AUTH_URL_PATTERN = re.compile(
    r"(?i)(https?://)[^/\s:@]+:[^/@\s]+@"
)
_OPENAI_KEY_PATTERN = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}")
_CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(\b(?:access[-_\s]?token|refresh[-_\s]?token|login[-_\s]?credential|"
    r"token|authorization|cookie|api[-_\s]?key|secret|password|credential)"
    r"[\"']?\s*[:=]\s*[\"']?)[^\s,;&#\"']+"
)
_QUERY_CREDENTIAL_PATTERN = re.compile(
    r"(?i)([?&](?:access[_-]?token|refresh[_-]?token|login[_-]?credential|"
    r"token|authorization|cookie|api[_-]?key|secret|password|credential)=)"
    r"([^&#\s]*)"
)
_SAFE_SLUG_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")

_SNAPSHOT_FIELDS = (
    ("时间", ("time", "timestamp"), "不可用"),
    ("状态", ("status",), "不可用"),
    ("Houdini build", ("houdini_build",), "不可用"),
    ("Python", ("python_version", "python"), "不可用"),
    (
        "插件或 Git commit",
        (
            "plugin_or_git_commit",
            "plugin_version",
            "git_commit",
            "implementation_version",
        ),
        "不可用",
    ),
    ("Thread", ("thread_id", "thread"), "不可用"),
    ("Turn", ("turn_id", "turn"), "不可用"),
    ("Model", ("model",), "不可用"),
    ("Effort", ("effort",), "不可用"),
    (
        "用户目标摘要",
        ("user_goal_summary", "goal_summary", "user_goal", "goal"),
        "未提供",
    ),
    ("预期结果", ("expected", "expected_result"), "未提供"),
    ("实际结果", ("actual", "actual_result"), "未提供"),
    ("阶段", ("stage",), "未提供"),
    ("工具及顺序", ("tools", "tool_order"), "未提供"),
    ("错误代码", ("error_code",), "未提供"),
    ("错误文本", ("error_text", "error_message"), "未提供"),
    ("Traceback（已脱敏）", ("traceback",), "未提供"),
    ("警告", ("warnings",), "未提供"),
    ("重试", ("retries", "retry_count"), "未提供"),
    ("恢复情况", ("recovery",), "未提供"),
    ("节点", ("nodes", "node_paths"), "未提供"),
    ("当前选择", ("selection", "selected_nodes"), "未提供"),
    ("场景版本", ("revision", "scene_revision"), "不可用"),
    ("未保存", ("dirty",), "不可用"),
    ("场景已修改", ("scene_modified",), "不可用"),
    ("根路径", ("root_path", "result_root_path"), "未提供"),
    ("人工检查", ("manual_check",), "未提供"),
    ("Undo", ("undo", "undo_status"), "未提供"),
    ("附件", ("attachments",), "未提供"),
    ("复现步骤", ("reproduction", "repro"), "未提供"),
    ("变通方法", ("workaround",), "未提供"),
    ("影响", ("impact",), "未提供"),
    ("下一步", ("next_step",), "未提供"),
    (
        "待验证假设",
        ("unverified_hypotheses", "hypotheses"),
        "未提供",
    ),
)

_OCCURRENCE_FIELDS = (
    ("发生时间", ("time", "timestamp"), "不可用"),
    ("状态", ("status",), "不可用"),
    ("阶段", ("stage",), "未提供"),
    ("工具", ("tool", "tool_name"), "未提供"),
    ("错误代码", ("error_code", "code"), "未提供"),
    ("错误文本", ("error_text", "error_message", "message"), "未提供"),
    ("Traceback（已脱敏）", ("traceback",), "未提供"),
    ("重试", ("retries", "retry_count"), "未提供"),
    ("恢复情况", ("recovery",), "未提供"),
    ("复现步骤", ("reproduction", "repro"), "未提供"),
    ("变通方法", ("workaround",), "未提供"),
    ("影响", ("impact",), "未提供"),
    ("下一步", ("next_step",), "未提供"),
    ("手动记录", ("manual", "manual_feedback", "feedback"), "未提供"),
    ("备注", ("note",), "未提供"),
)

_SUMMARY_KEYS = frozenset(
    {
        "user_goal_summary",
        "goal_summary",
        "user_goal",
        "goal",
    }
)
_LONG_TEXT_KEYS = frozenset({"traceback", "error_text", "error_message", "message"})


class RuntimeDiagnosticWriter:
    """Write a bounded report and append later occurrences to the same Turn file."""

    def __init__(
        self,
        project_root: str | os.PathLike[str] | None = None,
        clock: Callable[[], object] | None = None,
    ) -> None:
        self._project_root = self._resolve_project_root(project_root)
        self._diagnostics_root = (
            self._project_root / ".runtime" / "diagnostics"
        ).resolve()
        self._require_descendant(self._diagnostics_root, self._project_root)
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._paths: dict[str, Path] = {}
        self._occurrence_counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def record(
        self,
        turn_key: object,
        *,
        snapshot: Mapping[str, Any],
        occurrence: Mapping[str, Any],
        slug: str = "issue",
    ) -> str:
        """Create or append one report and return its absolute project-local path."""

        key = self._turn_key(turn_key)
        if not isinstance(snapshot, Mapping) or not isinstance(occurrence, Mapping):
            raise TypeError("snapshot and occurrence must be mappings")
        moment = self._read_clock()

        with self._lock:
            existing = self._paths.get(key)
            if existing is None:
                self._diagnostics_root.mkdir(parents=True, exist_ok=True)
                existing = self._find_existing_report(snapshot)
            if existing is None:
                body = self._render_initial(snapshot, occurrence, moment)
                path = self._create_exclusive(body, moment, slug)
                self._paths[key] = path
                self._occurrence_counts[key] = 1
                return str(path)

            count = self._occurrence_counts.get(key)
            if count is None:
                count = self._existing_occurrence_count(existing)
                self._paths[key] = existing
            count += 1
            update = self._render_update(snapshot, occurrence, moment, count)
            with existing.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(update)
            self._occurrence_counts[key] = count
            return str(existing)

    def _find_existing_report(self, snapshot: Mapping[str, Any]) -> Path | None:
        thread_key, thread_id = self._first_value(snapshot, ("thread_id", "thread"))
        turn_key, turn_id = self._first_value(snapshot, ("turn_id", "turn"))
        if thread_key is None or turn_key is None:
            return None
        thread_text = self._display(self._sanitize(thread_id, thread_key), "")
        turn_text = self._display(self._sanitize(turn_id, turn_key), "")
        unavailable = {"", "不可用", "尚未确认", "未提供"}
        if thread_text in unavailable or turn_text in unavailable:
            return None
        thread_line = f"- Thread：{thread_text}"
        turn_line = f"- Turn：{turn_text}"
        for path in sorted(self._diagnostics_root.glob("*.md"), reverse=True):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            lines = set(content.splitlines())
            if thread_line in lines and turn_line in lines:
                return path.resolve()
        return None

    @staticmethod
    def _existing_occurrence_count(path: Path) -> int:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return 1
        return 1 + content.count("\n## 更新 ")

    def path_for(self, turn_key: object) -> str | None:
        """Return the report path already allocated for a Turn, if any."""

        key = self._turn_key(turn_key)
        with self._lock:
            path = self._paths.get(key)
            return str(path) if path is not None else None

    @staticmethod
    def _resolve_project_root(
        project_root: str | os.PathLike[str] | None,
    ) -> Path:
        if project_root is None:
            project_root = os.environ.get("HIA_PROJECT_ROOT")
        root = (
            Path(project_root)
            if project_root is not None
            else Path(__file__).resolve().parents[3]
        ).resolve()
        if not root.is_dir():
            raise ValueError("project_root must be an existing directory")
        return root

    @staticmethod
    def _require_descendant(candidate: Path, parent: Path) -> None:
        try:
            candidate.relative_to(parent)
        except ValueError as exc:
            raise ValueError("diagnostics path escapes the project root") from exc

    @staticmethod
    def _turn_key(value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("turn_key must be a string")
        normalized = value.strip()
        if not normalized or len(normalized) > 512 or "\x00" in normalized:
            raise ValueError("turn_key must be a bounded non-empty string")
        return normalized

    def _read_clock(self) -> datetime:
        value = self._clock()
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.astimezone()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                raise ValueError("clock returned a non-finite timestamp")
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        raise TypeError("clock must return datetime or a Unix timestamp")

    def _create_exclusive(self, body: str, moment: datetime, slug: str) -> Path:
        timestamp = moment.strftime("%Y%m%d-%H%M%S")
        safe_slug = _SAFE_SLUG_PATTERN.sub("-", str(slug)).strip("-_").lower()
        safe_slug = (safe_slug or "issue")[:_MAX_SLUG].rstrip("-_") or "issue"
        for index in range(1, 10_000):
            suffix = "" if index == 1 else f"-{index}"
            path = self._diagnostics_root / f"{timestamp}-{safe_slug}{suffix}.md"
            try:
                with path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(body)
            except FileExistsError:
                continue
            return path.resolve()
        raise FileExistsError("unable to allocate a unique diagnostic report name")

    def _render_initial(
        self,
        snapshot: Mapping[str, Any],
        occurrence: Mapping[str, Any],
        moment: datetime,
    ) -> str:
        values = dict(snapshot)
        if not self._has_value(values, ("time", "timestamp")):
            values["time"] = moment.isoformat(timespec="seconds")
        lines = ["# Big-Chicken Houdini Intelligence Agent 问题报告", "", "## 本轮快照", ""]
        lines.extend(self._render_fields(values, _SNAPSHOT_FIELDS))
        lines.append(self._render_occurrence(occurrence, moment, 1))
        return "\n".join(lines).rstrip() + "\n"

    def _render_update(
        self,
        snapshot: Mapping[str, Any],
        occurrence: Mapping[str, Any],
        moment: datetime,
        count: int,
    ) -> str:
        snapshot_lines = self._render_fields(
            snapshot,
            _SNAPSHOT_FIELDS,
            omit_missing=True,
        )
        if not snapshot_lines:
            snapshot_lines = ["- 未提供新增快照字段"]

        values = dict(occurrence)
        if not self._has_value(values, ("time", "timestamp")):
            values["time"] = moment.isoformat(timespec="seconds")
        lines = [
            "",
            f"## 更新 {count}",
            "",
            "### 当前快照更新",
            "",
            *snapshot_lines,
            "",
            "### 发生记录",
            "",
            *self._render_fields(values, _OCCURRENCE_FIELDS),
        ]
        return "\n".join(lines).rstrip() + "\n"

    def _render_occurrence(
        self,
        occurrence: Mapping[str, Any],
        moment: datetime,
        count: int,
    ) -> str:
        values = dict(occurrence)
        if not self._has_value(values, ("time", "timestamp")):
            values["time"] = moment.isoformat(timespec="seconds")
        lines = ["", f"## 发生记录 {count}", ""]
        lines.extend(self._render_fields(values, _OCCURRENCE_FIELDS))
        return "\n".join(lines).rstrip() + "\n"

    def _render_fields(
        self,
        values: Mapping[str, Any],
        fields: Sequence[tuple[str, Sequence[str], str]],
        *,
        omit_missing: bool = False,
    ) -> list[str]:
        rendered: list[str] = []
        for label, aliases, missing in fields:
            source_key, value = self._first_value(values, aliases)
            if source_key is None:
                if omit_missing:
                    continue
                text = missing
            elif source_key == "attachments":
                text = self._display(self._attachment_names(value), missing)
            else:
                limit = (
                    _MAX_SUMMARY
                    if source_key in _SUMMARY_KEYS
                    else _MAX_LONG_TEXT
                    if source_key in _LONG_TEXT_KEYS
                    else _MAX_TEXT
                )
                text = self._display(self._sanitize(value, source_key, limit=limit), missing)
            rendered.append(f"- {label}：{text}")
        return rendered

    @staticmethod
    def _has_value(values: Mapping[str, Any], aliases: Sequence[str]) -> bool:
        return RuntimeDiagnosticWriter._first_value(values, aliases)[0] is not None

    @staticmethod
    def _first_value(
        values: Mapping[str, Any], aliases: Sequence[str]
    ) -> tuple[str | None, Any]:
        for name in aliases:
            value = values.get(name)
            if RuntimeDiagnosticWriter._value_is_present(value):
                return name, value
        return None, None

    @staticmethod
    def _value_is_present(value: Any) -> bool:
        if value is None or value == "":
            return False
        if isinstance(value, (Mapping, Sequence, set, frozenset)) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            return len(value) > 0
        return True

    def _sanitize(
        self,
        value: Any,
        field_name: str = "",
        *,
        depth: int = 0,
        seen: set[int] | None = None,
        limit: int = _MAX_TEXT,
    ) -> Any:
        if self._sensitive_key(field_name):
            return "[REDACTED]"
        if depth > _MAX_DEPTH:
            return "[nested data omitted]"
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else "[non-finite number omitted]"
        if isinstance(value, (str, os.PathLike)):
            return self._sanitize_text(str(value), limit)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return "[binary data omitted]"

        seen = seen if seen is not None else set()
        identity = id(value)
        if identity in seen:
            return "[cyclic value omitted]"
        seen.add(identity)
        try:
            if isinstance(value, Mapping):
                output: dict[str, Any] = {}
                items = list(value.items())[:_MAX_COLLECTION_ITEMS]
                for key, item in items:
                    name = self._sanitize_text(str(key), 256)
                    output[name] = self._sanitize(
                        item,
                        name,
                        depth=depth + 1,
                        seen=seen,
                        limit=limit,
                    )
                if len(value) > len(items):
                    output["omitted"] = f"{len(value) - len(items)} more items"
                return output
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                items = list(value)[:_MAX_COLLECTION_ITEMS]
                output = [
                    self._sanitize(
                        item,
                        field_name,
                        depth=depth + 1,
                        seen=seen,
                        limit=limit,
                    )
                    for item in items
                ]
                if len(value) > len(items):
                    output.append(f"{len(value) - len(items)} more items omitted")
                return output
            if isinstance(value, (set, frozenset)):
                items = sorted(value, key=lambda item: str(item))[:_MAX_COLLECTION_ITEMS]
                return [
                    self._sanitize(
                        item,
                        field_name,
                        depth=depth + 1,
                        seen=seen,
                        limit=limit,
                    )
                    for item in items
                ]
            return self._sanitize_text(str(value), limit)
        finally:
            seen.discard(identity)

    def _attachment_names(self, value: Any) -> list[str]:
        if isinstance(value, (str, os.PathLike, Mapping)):
            items = [value]
        elif isinstance(value, Sequence):
            items = list(value)
        else:
            items = [value]
        names: list[str] = []
        for item in items[:_MAX_ATTACHMENTS]:
            if isinstance(item, Mapping):
                item = item.get("name") or item.get("path", "")
            name = str(item).replace("\\", "/").rsplit("/", 1)[-1].strip()
            if name:
                names.append(self._sanitize_text(name, 256))
        if len(items) > _MAX_ATTACHMENTS:
            names.append(f"{len(items) - _MAX_ATTACHMENTS} more attachments omitted")
        return names

    @staticmethod
    def _sensitive_key(name: object) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", str(name).lower())
        return normalized in _SENSITIVE_KEY_NAMES or any(
            marker in normalized for marker in _SENSITIVE_KEY_PARTS
        )

    @staticmethod
    def _sanitize_text(value: str, limit: int) -> str:
        text = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
        text = _BASIC_AUTH_URL_PATTERN.sub(r"\1[REDACTED]@", text)
        text = _AUTHORIZATION_SCHEME_PATTERN.sub(r"\1[REDACTED]", text)
        text = _BEARER_PATTERN.sub("[REDACTED]", text)
        text = _QUERY_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]", text)
        text = _CREDENTIAL_ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", text)
        text = _OPENAI_KEY_PATTERN.sub("[REDACTED]", text)
        if len(text) > limit:
            text = text[:limit] + "…[truncated]"
        return text

    @staticmethod
    def _display(value: Any, missing: str) -> str:
        if value is None or value == "" or value == [] or value == {}:
            return missing
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (Mapping, list, tuple)):
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            text = str(value)
        if len(text) > _MAX_LONG_TEXT:
            text = text[:_MAX_LONG_TEXT] + "…[truncated]"
        return text.replace("\n", "<br>")


__all__ = ["RuntimeDiagnosticWriter"]
