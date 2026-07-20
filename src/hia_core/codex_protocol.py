"""Offline validation for the pinned Codex app-server protocol baseline.

The module reads generated JSON Schema and repository-owned contract files. It
does not launch Codex, open a network connection, write files, or execute a
protocol request.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence


SUPPORTED_CODEX_VERSION = "0.144.3"
SCHEMA_DRAFT = "http://json-schema.org/draft-07/schema#"
CONTRACT_RELATIVE_ROOT = Path("contracts/codex-app-server/0.144.3")

AGGREGATE_SCHEMAS = {
    "client_requests": "ClientRequest.json",
    "server_requests": "ServerRequest.json",
    "server_notifications": "ServerNotification.json",
    "client_notifications": "ClientNotification.json",
}

CORE_CLIENT_REQUESTS = frozenset(
    {
        "initialize",
        "account/read",
        "model/list",
        "thread/list",
        "thread/name/set",
        "thread/goal/get",
        "thread/goal/set",
        "thread/goal/clear",
        "thread/start",
        "thread/resume",
        "thread/read",
        "thread/fork",
        "turn/start",
        "turn/steer",
        "turn/interrupt",
    }
)
CORE_CLIENT_NOTIFICATIONS = frozenset({"initialized"})
CORE_APPROVAL_REQUESTS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
    }
)
P1_PASSIVE_SERVER_NOTIFICATIONS = frozenset(
    {
        "account/rateLimits/updated",
        "mcpServer/startupStatus/updated",
        "remoteControl/status/changed",
        "skills/changed",
    }
)
CORE_SERVER_NOTIFICATIONS = frozenset(
    {
        "error",
        "warning",
        "deprecationNotice",
        "guardianWarning",
        "configWarning",
        "thread/started",
        "thread/name/updated",
        "thread/goal/updated",
        "thread/goal/cleared",
        "thread/status/changed",
        "thread/tokenUsage/updated",
        "thread/compacted",
        "turn/started",
        "turn/completed",
        "turn/diff/updated",
        "turn/plan/updated",
        "item/started",
        "item/completed",
        "item/agentMessage/delta",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/textDelta",
        "item/commandExecution/outputDelta",
        "item/commandExecution/terminalInteraction",
        "item/fileChange/outputDelta",
        "item/fileChange/patchUpdated",
        "item/mcpToolCall/progress",
        "serverRequest/resolved",
        "model/rerouted",
        "model/verification",
        "model/safetyBuffering/updated",
    }
) | P1_PASSIVE_SERVER_NOTIFICATIONS

REQUIRED_EXCLUSIONS = {
    "experimental": frozenset(
        {
            "experimentalFeature/list",
            "experimentalFeature/enablement/set",
            "item/tool/requestUserInput",
            "item/plan/delta",
        }
    ),
    "dynamicTools": frozenset({"item/tool/call"}),
    "processApi": frozenset(
        {
            "command/exec",
            "command/exec/write",
            "command/exec/terminate",
            "command/exec/resize",
            "command/exec/outputDelta",
            "process/outputDelta",
            "process/exited",
        }
    ),
    "threadShellCommand": frozenset({"thread/shellCommand"}),
}


@dataclass(frozen=True)
class ProtocolContractError(ValueError):
    """Structured failure raised by offline protocol validation."""

    code: str
    message: str
    path: str | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "structured_error": {
                "code": self.code,
                "message": self.message,
                "path": self.path,
            },
        }


def _reject(code: str, message: str, path: Path | None = None) -> None:
    raise ProtocolContractError(code, message, str(path) if path else None)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _reject("MISSING_FILE", "Required protocol file does not exist", path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _reject("INVALID_JSON", f"Cannot read JSON: {exc}", path)
    if not isinstance(value, dict):
        _reject("INVALID_JSON_ROOT", "JSON root must be an object", path)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        _reject("HASH_FAILED", f"Cannot hash file: {exc}", path)
    return digest.hexdigest()


def _safe_child(root: Path, relative: str, *, context: str) -> Path:
    if not isinstance(relative, str) or not relative:
        _reject("INVALID_RELATIVE_PATH", f"{context} must be a non-empty string")
    if "\\" in relative:
        _reject("INVALID_RELATIVE_PATH", f"{context} must use forward slashes")
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or ".." in parsed.parts or ":" in relative:
        _reject("INVALID_RELATIVE_PATH", f"{context} escapes its approved root")
    return root.joinpath(*parsed.parts)


def _iter_refs(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref" and isinstance(child, str):
                yield child
            else:
                yield from _iter_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_refs(child)


def _resolve_local_ref(document: Any, ref: str, path: Path) -> None:
    if ref == "#":
        return
    if not ref.startswith("#/"):
        _reject("EXTERNAL_SCHEMA_REF", f"Only local JSON Schema refs are allowed: {ref}", path)
    current = document
    for raw_token in ref[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
            continue
        if isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            try:
                current = current[int(token)]
                continue
            except (ValueError, IndexError):
                pass
        _reject("BROKEN_SCHEMA_REF", f"Unresolved JSON Schema ref: {ref}", path)


def validate_local_schema_refs(document: Mapping[str, Any], path: Path) -> None:
    """Require every JSON Schema reference in a document to resolve locally."""

    for ref in _iter_refs(document):
        _resolve_local_ref(document, ref, path)


def extract_method_inventory(schema_path: Path) -> list[dict[str, Any]]:
    """Extract and validate method discriminators from an aggregate schema."""

    schema = _read_json(schema_path)
    if schema.get("$schema") != SCHEMA_DRAFT:
        _reject("SCHEMA_DRAFT_MISMATCH", "Aggregate schema is not draft-07", schema_path)
    validate_local_schema_refs(schema, schema_path)
    variants = schema.get("oneOf")
    if not isinstance(variants, list) or not variants:
        _reject("INVALID_AGGREGATE_SCHEMA", "Expected a non-empty oneOf array", schema_path)

    methods: list[dict[str, Any]] = []
    seen: set[str] = set()
    for variant in variants:
        if not isinstance(variant, dict):
            _reject("INVALID_METHOD_VARIANT", "Method variant must be an object", schema_path)
        properties = variant.get("properties")
        if not isinstance(properties, dict):
            _reject("INVALID_METHOD_VARIANT", "Method variant has no properties", schema_path)
        method_schema = properties.get("method")
        if not isinstance(method_schema, dict):
            _reject("INVALID_METHOD_VARIANT", "Method discriminator is missing", schema_path)
        enum = method_schema.get("enum")
        if not isinstance(enum, list) or len(enum) != 1 or not isinstance(enum[0], str):
            _reject("INVALID_METHOD_ENUM", "Method enum must contain one string", schema_path)
        method = enum[0]
        if method in seen:
            _reject("DUPLICATE_METHOD", f"Duplicate method discriminator: {method}", schema_path)
        seen.add(method)

        params_definition = None
        params_schema = properties.get("params")
        if isinstance(params_schema, dict) and "$ref" in params_schema:
            ref = params_schema["$ref"]
            prefix = "#/definitions/"
            if not isinstance(ref, str) or not ref.startswith(prefix):
                _reject("INVALID_PARAMS_REF", f"Unexpected params ref for {method}", schema_path)
            params_definition = ref[len(prefix) :]

        description = variant.get("description")
        methods.append(
            {
                "method": method,
                "params_definition": params_definition,
                "schema_title": variant.get("title"),
                "declared_experimental": isinstance(description, str)
                and "experimental" in description.casefold(),
            }
        )
    return sorted(methods, key=lambda item: item["method"])


def load_protocol_documents(repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the versioned inventory and deny-by-default allowlist."""

    contract_root = repo_root / CONTRACT_RELATIVE_ROOT
    return (
        _read_json(contract_root / "protocol-inventory.json"),
        _read_json(contract_root / "core-allowlist.json"),
    )


def _method_set(entries: Any, *, context: str) -> set[str]:
    if not isinstance(entries, list):
        _reject("INVALID_CONTRACT", f"{context} must be an array")
    methods: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("method"), str):
            _reject("INVALID_CONTRACT", f"{context} contains an invalid method entry")
        method = entry["method"]
        if method in methods:
            _reject("DUPLICATE_ALLOWLIST_METHOD", f"Duplicate allowlisted method: {method}")
        methods.add(method)
    return methods


def validate_protocol_contract(repo_root: Path) -> dict[str, Any]:
    """Validate the frozen 0.144.3 inventory and stable-core allowlist offline."""

    inventory, allowlist = load_protocol_documents(repo_root)
    if inventory.get("format_version") != 1 or allowlist.get("format_version") != 1:
        _reject("FORMAT_VERSION_MISMATCH", "Protocol contract format must be version 1")
    for document in (inventory, allowlist):
        if document.get("codex_cli_version") != SUPPORTED_CODEX_VERSION:
            _reject("CODEX_VERSION_MISMATCH", "Protocol contract Codex version changed")
        if document.get("schema_draft") != SCHEMA_DRAFT:
            _reject("SCHEMA_DRAFT_MISMATCH", "Protocol contract draft changed")

    schema_root_relative = inventory.get("generated_schema_root")
    schema_root = _safe_child(
        repo_root,
        schema_root_relative,
        context="generated_schema_root",
    )
    aggregates = inventory.get("aggregates")
    if not isinstance(aggregates, dict) or set(aggregates) != set(AGGREGATE_SCHEMAS):
        _reject("AGGREGATE_SET_MISMATCH", "Aggregate schema inventory changed")

    actual_by_category: dict[str, dict[str, dict[str, Any]]] = {}
    aggregate_counts: dict[str, int] = {}
    for category, expected_filename in AGGREGATE_SCHEMAS.items():
        record = aggregates.get(category)
        if not isinstance(record, dict) or record.get("path") != expected_filename:
            _reject("AGGREGATE_PATH_MISMATCH", f"Unexpected aggregate path for {category}")
        schema_path = _safe_child(schema_root, expected_filename, context=f"{category}.path")
        if record.get("sha256") != _sha256(schema_path):
            _reject("AGGREGATE_HASH_MISMATCH", f"Aggregate hash changed: {category}", schema_path)
        actual_methods = extract_method_inventory(schema_path)
        if record.get("methods") != actual_methods:
            _reject("METHOD_INVENTORY_MISMATCH", f"Method inventory changed: {category}", schema_path)
        actual_by_category[category] = {
            item["method"]: item for item in actual_methods
        }
        aggregate_counts[category] = len(actual_methods)

    response_records = inventory.get("response_schemas")
    if not isinstance(response_records, list):
        _reject("INVALID_RESPONSE_INVENTORY", "response_schemas must be an array")
    response_paths: set[str] = set()
    for record in response_records:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            _reject("INVALID_RESPONSE_INVENTORY", "Invalid response schema record")
        relative = record["path"]
        if relative in response_paths:
            _reject("DUPLICATE_RESPONSE_SCHEMA", f"Duplicate response schema: {relative}")
        response_paths.add(relative)
        response_path = _safe_child(schema_root, relative, context="response schema path")
        if record.get("sha256") != _sha256(response_path):
            _reject("RESPONSE_HASH_MISMATCH", f"Response schema hash changed: {relative}", response_path)
        response_schema = _read_json(response_path)
        if response_schema.get("$schema") != SCHEMA_DRAFT:
            _reject("SCHEMA_DRAFT_MISMATCH", "Response schema is not draft-07", response_path)
        validate_local_schema_refs(response_schema, response_path)

    actual_response_paths = {
        path.relative_to(schema_root).as_posix()
        for path in schema_root.rglob("*Response.json")
        if path.is_file()
    }
    if response_paths != actual_response_paths:
        _reject("RESPONSE_INVENTORY_MISMATCH", "Response schema file set changed", schema_root)

    if allowlist.get("policy") != "deny-by-default":
        _reject("ALLOWLIST_POLICY_MISMATCH", "Core protocol must remain deny-by-default")
    if allowlist.get("inventory") != "protocol-inventory.json":
        _reject("INVENTORY_LINK_MISMATCH", "Allowlist must reference its sibling inventory")
    generation = allowlist.get("schema_generation")
    if not isinstance(generation, dict) or generation.get("experimental") is not False:
        _reject("EXPERIMENTAL_SCHEMA_ENABLED", "Experimental schema must remain disabled")
    if generation.get("forbidden_flag") != "--experimental":
        _reject("EXPERIMENTAL_FLAG_MISMATCH", "The forbidden schema flag changed")

    transport = allowlist.get("transport")
    if not isinstance(transport, dict) or transport.get("allowed") != ["stdio-jsonl"]:
        _reject("TRANSPORT_POLICY_MISMATCH", "Only stdio JSONL may be allowed")
    forbidden_transports = set(transport.get("forbidden", []))
    if not {"websocket", "ws", "wss"}.issubset(forbidden_transports):
        _reject("WEBSOCKET_NOT_EXCLUDED", "WebSocket transports must be forbidden")

    allowed = allowlist.get("allowed")
    if not isinstance(allowed, dict) or set(allowed) != set(AGGREGATE_SCHEMAS):
        _reject("ALLOWLIST_CATEGORY_MISMATCH", "Allowlist categories changed")
    allowed_sets: dict[str, set[str]] = {}
    all_allowed: set[str] = set()
    for category in AGGREGATE_SCHEMAS:
        entries = allowed.get(category)
        methods = _method_set(entries, context=f"allowed.{category}")
        allowed_sets[category] = methods
        for entry in entries:
            method = entry["method"]
            actual = actual_by_category[category].get(method)
            if actual is None:
                _reject("MISSING_ALLOWLIST_METHOD", f"Allowlisted method is absent: {method}")
            if entry.get("params_definition") != actual["params_definition"]:
                _reject("PARAMS_DEFINITION_MISMATCH", f"Params changed for {method}")
            if actual["declared_experimental"]:
                _reject("EXPERIMENTAL_METHOD_ALLOWED", f"Experimental method is allowlisted: {method}")
            response_schema = entry.get("response_schema")
            if response_schema is not None and response_schema not in response_paths:
                _reject("MISSING_RESPONSE_SCHEMA", f"Response schema is absent: {response_schema}")
        all_allowed.update(methods)

    expected_allowed = {
        "client_requests": CORE_CLIENT_REQUESTS,
        "server_requests": CORE_APPROVAL_REQUESTS,
        "server_notifications": CORE_SERVER_NOTIFICATIONS,
        "client_notifications": CORE_CLIENT_NOTIFICATIONS,
    }
    for category, expected in expected_allowed.items():
        if allowed_sets[category] != expected:
            _reject("CORE_ALLOWLIST_MISMATCH", f"Frozen core changed: {category}")

    exclusions = allowlist.get("explicit_exclusions")
    if not isinstance(exclusions, dict):
        _reject("MISSING_EXCLUSIONS", "Explicit exclusion policy is missing")
    all_inventory_methods = {
        method
        for category in actual_by_category.values()
        for method in category
    }
    for feature, required_methods in REQUIRED_EXCLUSIONS.items():
        feature_policy = exclusions.get(feature)
        if not isinstance(feature_policy, dict):
            _reject("MISSING_EXCLUSION", f"Required exclusion is missing: {feature}")
        excluded_methods = set(feature_policy.get("methods", []))
        if not required_methods.issubset(excluded_methods):
            _reject("INCOMPLETE_EXCLUSION", f"Required methods are not excluded: {feature}")
        if not excluded_methods.issubset(all_inventory_methods):
            _reject("UNKNOWN_EXCLUDED_METHOD", f"Exclusion names an unknown method: {feature}")
        overlap = excluded_methods & all_allowed
        if overlap:
            _reject("EXCLUDED_METHOD_ALLOWED", f"Excluded methods are allowlisted: {sorted(overlap)}")
    websocket_policy = exclusions.get("webSocket")
    if not isinstance(websocket_policy, dict) or not {"websocket", "ws", "wss"}.issubset(
        set(websocket_policy.get("transports", []))
    ):
        _reject("WEBSOCKET_NOT_EXCLUDED", "Explicit WebSocket exclusion is missing")

    compatibility = allowlist.get("compatibility")
    required_compatibility = {
        "unknown_methods": "reject",
        "unknown_notifications": "log-and-ignore-with-metric",
        "missing_allowlisted_method": "block-upgrade",
        "changed_allowlisted_schema": "block-upgrade-pending-review",
        "additive_optional_fields": "compatible-after-contract-tests",
        "additive_non_allowlisted_methods": "ignored",
    }
    if compatibility != required_compatibility:
        _reject("COMPATIBILITY_POLICY_MISMATCH", "Upgrade compatibility policy changed")

    return {
        "ok": True,
        "codex_cli_version": SUPPORTED_CODEX_VERSION,
        "schema_draft": SCHEMA_DRAFT,
        "aggregate_method_counts": aggregate_counts,
        "response_schema_count": len(response_paths),
        "allowlist_method_counts": {
            category: len(methods) for category, methods in allowed_sets.items()
        },
    }
