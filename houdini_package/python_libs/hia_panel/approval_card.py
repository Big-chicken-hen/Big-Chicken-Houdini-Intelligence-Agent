"""Human-readable presentation for the existing app-server approval request."""

from __future__ import annotations

import json
import ntpath
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .runtime_diagnostics import RuntimeDiagnosticWriter


_WINDOWS_PATH_PATTERN = re.compile(
    r'''(?ix)
    "(?P<double>[a-z]:[\\/][^"]*)"
    |'(?P<single>[a-z]:[\\/][^']*)'
    |(?<![a-z])(?P<bare>[a-z]:[\\/][^\s|;&><,"']*)
    '''
)
_URL_PATTERN = re.compile(r"(?i)https?://[^\s<>\"']+")
_TARGET_FLAG_PATTERN = re.compile(
    r'''(?ix)
    -(?:literalpath|path|filepath|outfile|destination|dest|output|o)(?:\s+|=)
    (?:(?:"(?P<double>[^"]+)")|(?:'(?P<single>[^']+)')|(?P<bare>[^\s|;&]+))
    '''
)
_WRITE_TARGET_FLAG_PATTERN = re.compile(
    r'''(?ix)
    -(?:outfile|destination|dest|output|o)(?:\s+|=)
    (?:(?:"(?P<double>[^"]+)")|(?:'(?P<single>[^']+)')|(?P<bare>[^\s|;&]+))
    '''
)
_SYSTEM_LOCATION_REFERENCE_PATTERN = re.compile(
    r"(?i)(?:\$(?:\{(?:env:)?(?:systemdrive|userprofile|home|appdata|"
    r"localappdata|systemroot|windir)\}|(?:env:)?(?:systemdrive|userprofile|"
    r"home|appdata|localappdata|systemroot|windir)\b)|%(?:systemdrive|userprofile|"
    r"home|appdata|localappdata|systemroot|windir)%|~[\\/])"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r'''(?ix)
    (?P<prefix>
        ["']?\b(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|
        api[-_\s]?key|access[-_\s]?token|refresh[-_\s]?token|secret|password)
        \b["']?\s*[:=]\s*
    )
    (?:"[^"]*"|'[^']*'|[^\s,;}\]]+)
    '''
)
_COOKIE_ARGUMENT_PATTERN = re.compile(
    r'''(?ix)
    (?P<prefix>(?:--cookie|-b)\s+)
    (?:(?:"[^"]*")|(?:'[^']*')|(?:[^\s]+))
    '''
)
_DELETE_PATTERN = re.compile(
    r"(?i)\b(?:remove-item|clear-content|del|erase|rmdir)\b|"
    r"\b(?:unlink|rmtree|remove)\s*\("
)
_MOVE_PATTERN = re.compile(
    r"(?i)\b(?:move-item|rename-item|move)\b|\b(?:rename|replace)\s*\("
)
_CREATE_PATTERN = re.compile(
    r"(?i)\b(?:new-item|mkdir|md)\b|\b(?:makedirs|mkdir)\s*\("
)
_WRITE_PATTERN = re.compile(
    r"(?i)(?:\b(?:set|add|clear)-content\b|\bout-file\b|"
    r"\b(?:new|remove|copy|move|rename)-item\b|"
    r"\binvoke-webrequest\b[^\r\n]*\s-outfile\b|"
    r"\bwriteall(?:text|bytes)\b|\bwrite_(?:text|bytes)\b|"
    r"(?<![<>=])>>?(?![=]))"
)
_WEB_DOWNLOAD_PATTERN = re.compile(
    r"(?i)\binvoke-webrequest\b[^\r\n]*\s-outfile\b"
)


@dataclass(frozen=True)
class ApprovalCard:
    summary: str
    advanced_details: str
    offers_persistent_rule: bool


def _redact_text(value: str) -> str:
    text = RuntimeDiagnosticWriter._sanitize_text(value, max(1, len(value) + 1))
    text = _COOKIE_ARGUMENT_PATTERN.sub(r"\g<prefix>[REDACTED]", text)
    text = _SECRET_ASSIGNMENT_PATTERN.sub(r"\g<prefix>[REDACTED]", text)
    return text


def _redact_value(value: Any, field_name: str = "") -> Any:
    if RuntimeDiagnosticWriter._sensitive_key(field_name):
        return "[REDACTED]"
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            name = _redact_text(str(key))
            output[name] = _redact_value(item, name)
        return output
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_redact_value(item, field_name) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


def _match_value(match: re.Match[str]) -> str:
    return next(
        (value for value in match.groupdict().values() if value is not None),
        "",
    ).strip().rstrip(",)")


def _command_text(params: Mapping[str, Any]) -> str:
    commands: list[str] = []
    actions = params.get("commandActions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            command = action.get("command")
            if isinstance(command, str) and command.strip():
                commands.append(command)
    if commands:
        return "\n".join(commands)
    command = params.get("command")
    return command if isinstance(command, str) else ""


def _permission_targets(params: Mapping[str, Any]) -> list[str]:
    permissions = params.get("permissions")
    file_system = (
        permissions.get("fileSystem") if isinstance(permissions, Mapping) else None
    )
    if not isinstance(file_system, Mapping):
        return []
    targets: list[str] = []
    write = file_system.get("write")
    if isinstance(write, list):
        targets.extend(value for value in write if isinstance(value, str))
    entries = file_system.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, Mapping) or entry.get("access") != "write":
                continue
            path = entry.get("path")
            if not isinstance(path, Mapping):
                continue
            for key in ("path", "pattern"):
                value = path.get(key)
                if isinstance(value, str):
                    targets.append(value)
            value = path.get("value")
            if isinstance(value, Mapping):
                if value.get("kind") == "root":
                    targets.append("系统盘根目录")
                elif isinstance(value.get("path"), str):
                    targets.append(value["path"])
    return targets


def _target_paths(method: str, params: Mapping[str, Any], command: str) -> list[str]:
    targets: list[str] = []
    if method == "item/fileChange/requestApproval":
        grant_root = params.get("grantRoot")
        if isinstance(grant_root, str) and grant_root.strip():
            targets.append(grant_root)
    elif method == "item/permissions/requestApproval":
        targets.extend(_permission_targets(params))
    else:
        actions = params.get("commandActions")
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, Mapping):
                    continue
                path = action.get("path")
                if isinstance(path, str) and path.strip():
                    targets.append(path)
        targets.extend(
            _match_value(match) for match in _TARGET_FLAG_PATTERN.finditer(command)
        )
        targets.extend(
            _match_value(match) for match in _WINDOWS_PATH_PATTERN.finditer(command)
        )
    unique: list[str] = []
    for target in targets:
        safe_target = _redact_text(target)
        if safe_target and safe_target not in unique:
            unique.append(safe_target)
    return unique


def _preferred_target(command: str, targets: list[str]) -> str:
    if not targets:
        return "系统盘目标"
    explicit_write_targets = [
        _redact_text(_match_value(match))
        for match in _WRITE_TARGET_FLAG_PATTERN.finditer(command)
    ]
    if explicit_write_targets:
        return explicit_write_targets[0]
    system_drive = (
        ntpath.splitdrive(os.environ.get("SystemRoot", ""))[0]
        or os.environ.get("SystemDrive")
        or "C:"
    ).casefold()
    for target in targets:
        if _SYSTEM_LOCATION_REFERENCE_PATTERN.search(target):
            return target
        if ntpath.splitdrive(target.strip("\"'"))[0].casefold() == system_drive:
            return target
    return targets[0]


def _url_label(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return _redact_text(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path or "/"
    if host == "www.sidefx.com":
        host = "sidefx.com"
    if host == "www.shadertoy.com":
        host = "shadertoy.com"
    if host == "sidefx.com" and path.endswith("/docs/houdini/nodes/cop/wrangle.html"):
        return "SideFX COP Wrangle 文档"
    if host == "shadertoy.com":
        return "ShaderToy" + (f" {path}" if path != "/" else "")
    return f"{host}{path}" if host else _redact_text(url)


def _purpose(method: str, command: str, targets: list[str], urls: list[str]) -> str:
    target = _preferred_target(command, targets)
    if method == "item/fileChange/requestApproval":
        return f"允许修改系统盘目录：{target}"
    if method == "item/permissions/requestApproval":
        return f"授予系统盘写入权限：{target}"
    if _DELETE_PATTERN.search(command):
        return f"删除系统盘文件或目录：{target}"
    if _MOVE_PATTERN.search(command):
        return f"移动或重命名系统盘文件：{target}"
    if _CREATE_PATTERN.search(command):
        return f"创建系统盘文件或目录：{target}"
    if urls and _WEB_DOWNLOAD_PATTERN.search(command):
        return f"将网页资料写入系统盘：{_url_label(urls[0])} → {target}"
    if _WRITE_PATTERN.search(command):
        return f"修改系统盘文件：{target}"
    if urls:
        return f"读取网页资料：{_url_label(urls[0])}"
    return "执行本地命令，目的未提供"


def format_approval_card(event: Mapping[str, Any]) -> ApprovalCard:
    method = event.get("method")
    method_text = method if isinstance(method, str) else ""
    params_value = event.get("params")
    params = params_value if isinstance(params_value, Mapping) else {}
    command = _command_text(params)
    safe_command = _redact_text(command) if command else "未提供"
    targets = _target_paths(method_text, params, command)
    urls = [
        _redact_text(match.group(0).rstrip(".,);]"))
        for match in _URL_PATTERN.finditer(command)
    ]
    file_change = method_text in {
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
    } or _WRITE_PATTERN.search(command) is not None

    purpose = _purpose(method_text, command, targets, urls)
    operation_type = "文件写入" if file_change else "未知"
    scope_lines = ["目标路径：" + ("；".join(targets) if targets else "协议未提供明确路径")]
    cwd = params.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        scope_lines.append("工作目录：" + _redact_text(cwd))
    if urls:
        scope_lines.append("网页来源：" + "；".join(_url_label(url) for url in urls))
    impact = (
        "可能在系统盘创建、修改、移动或删除文件；请确认目标路径后再允许。"
        if file_change
        else "无法可靠确认写入范围；请先查看高级详情。"
    )
    summary = "\n".join(
        (
            "目的：" + purpose,
            "操作类型：" + operation_type,
            *scope_lines,
            "影响：" + impact,
        )
    )

    available = params.get("availableDecisions")
    decisions = _redact_value(available) if available is not None else "未提供"
    details_parts = [
        "原始 command：\n" + safe_command,
        "availableDecisions：\n"
        + (
            json.dumps(decisions, ensure_ascii=False, indent=2)
            if not isinstance(decisions, str)
            else decisions
        ),
    ]
    amendment = params.get("proposedExecpolicyAmendment")
    offers_persistent_rule = (
        isinstance(amendment, list)
        and bool(amendment)
        and all(isinstance(part, str) and part.strip() for part in amendment)
    )
    available = params.get("availableDecisions")
    if available is not None:
        offers_persistent_rule = (
            offers_persistent_rule
            and isinstance(available, list)
            and "acceptWithExecpolicyAmendment" in available
        )
    if offers_persistent_rule:
        details_parts.append(
            "以后允许相同命令规则：协议提供了持续授权建议；"
            "当前 Panel 不会自动选择。"
        )
    details_parts.append(
        "完整 JSON：\n"
        + json.dumps(_redact_value(event), ensure_ascii=False, indent=2)
    )
    return ApprovalCard(
        summary=summary,
        advanced_details="\n\n".join(details_parts),
        offers_persistent_rule=offers_persistent_rule,
    )


__all__ = ["ApprovalCard", "format_approval_card"]
