"""In-process Claude SDK adapters for Hermes host-owned tools.

This module deliberately stops at the public ``RuntimeHostServices`` seam.
The SDK object built here is presentation only: every handler validates its
request and delegates exactly once to ``host.execute_tool``.  It does not
know about Hermes registries, approvals, terminals, providers, or sessions.

The module is import-light.  ``claude_agent_sdk`` is imported only by
``HostToolBridge.build_sdk_mcp_server``; direct use of the bridge and all
schema validation remain usable without the optional SDK import.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


_MAX_NAME_UTF8_BYTES = 256
_MAX_REQUEST_ID_UTF8_BYTES = 256
_MAX_INPUT_UTF8_BYTES = 64 * 1024
_MAX_RESULT_UTF8_BYTES = 64 * 1024
_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 10_000

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


class RuntimeHostServices(Protocol):
    """The dependency-free subset of the public host facade used here."""

    async def execute_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        request_id: str | None = None,
    ) -> Any: ...

    def cancellation_requested(self) -> bool: ...


class ToolBridgeError(RuntimeError):
    """Base error for bridge configuration and request validation."""


class ToolBridgeConfigurationError(ToolBridgeError):
    """Tool schemas cannot be exposed safely by this adapter."""


_REQUEST_MESSAGES = {
    "request_id": "Tool request correlation is malformed",
    "name": "Tool name is malformed",
    "unknown": "Tool is not available",
    "arguments": "Tool arguments are malformed",
    "cancelled": "Tool call cancelled",
    "cancellation_unavailable": "Tool cancellation state unavailable",
}


class ToolBridgeRequestError(ToolBridgeError):
    """A tool request was rejected before host execution."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(_REQUEST_MESSAGES.get(code, "Tool request rejected"))


@dataclass(frozen=True)
class HostToolDefinition:
    """One immutable, normalized public tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCallResult:
    """Bounded result retaining the request correlation outside SDK text."""

    request_id: str
    correlation_id: str | None
    tool_name: str
    text: str
    is_error: bool = False

    def to_sdk_result(self) -> dict[str, Any]:
        """Return the public ``@tool`` result shape used by the admitted SDKs."""

        return {
            "content": [{"type": "text", "text": self.text}],
            "is_error": self.is_error,
        }


def _utf8_size(value: str) -> int | None:
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        return None
    return size


def _model_text(value: str, *, redact: bool = True) -> str | None:
    """Preserve model payload whitespace/Unicode; redact secrets, not formatting.

    Display/evidence limits must not silently rewrite host-approved tool output.
    Structural JSON safety remains separate from Hermes' output-size policy.
    """
    if type(value) is not str or _utf8_size(value) is None:
        return None
    return _redact_secret_like(value) if redact else value


_SECRET_LIKE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
)


def _redact_secret_like(value: str) -> str:
    """Remove common credential-shaped values without a host/private import."""

    for pattern in _SECRET_LIKE_PATTERNS:
        value = pattern.sub("[redacted]", value)
    return value


_INVALID = object()


def _copy_plain_json(
    value: Any,
    *,
    max_input_bytes: int | None = _MAX_INPUT_UTF8_BYTES,
) -> Any:
    """Copy a bounded exact-builtin JSON graph, rejecting hostile values."""

    nodes = 0
    input_bytes = 0
    active: set[int] = set()

    def copy(current: Any, depth: int) -> Any:
        nonlocal nodes, input_bytes
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            return _INVALID

        kind = type(current)
        if kind is str:
            size = _utf8_size(current)
            if size is None:
                return _INVALID
            input_bytes += size
            if max_input_bytes is not None and input_bytes > max_input_bytes:
                return _INVALID
            return current
        if current is None or kind is bool:
            return current
        if kind is int:
            # A cheap conservative check before any potentially huge repr.
            if current.bit_length() > _MAX_RESULT_UTF8_BYTES * 8:
                return _INVALID
            return current
        if kind is float:
            return current if math.isfinite(current) else _INVALID
        if not isinstance(current, Mapping) and kind not in (list, tuple):
            return _INVALID

        identity = id(current)
        if identity in active:
            return _INVALID
        active.add(identity)
        try:
            if kind in (list, tuple):
                copied: list[Any] = []
                for child in current:
                    child_copy = copy(child, depth + 1)
                    if child_copy is _INVALID:
                        return _INVALID
                    copied.append(child_copy)
                return copied

            copied_dict: dict[str, Any] = {}
            for key, child in current.items():
                if type(key) is not str or _utf8_size(key) is None:
                    return _INVALID
                copied_key = copy(key, depth + 1)
                child_copy = copy(child, depth + 1)
                if copied_key is _INVALID or child_copy is _INVALID:
                    return _INVALID
                copied_dict[copied_key] = child_copy
            return copied_dict
        except BaseException:
            return _INVALID
        finally:
            active.discard(identity)

    return copy(value, 0)


def _schema_validator(schema: dict[str, Any]):
    """Validate standard schemas without fetching external references.

    Use the SDK dependency stack's JSON Schema implementation rather than a
    second partial validator. Unknown keywords/dialects fail explicitly instead
    of silently weakening host constraints.
    """
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    from referencing.exceptions import NoSuchResource

    def no_remote(uri: str):
        raise NoSuchResource(ref=uri)

    annotations = {
        "$schema", "$id", "$ref", "$anchor", "$dynamicAnchor", "$dynamicRef",
        "$defs", "definitions", "$comment", "title", "description", "default",
        "examples", "deprecated", "readOnly", "writeOnly",
    }
    # These assertion modifiers are evaluated by ``contains`` rather than
    # having independent entries in jsonschema's validator dispatch map.
    assertion_modifiers = {"minContains", "maxContains"}
    schema_maps = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
    schema_lists = {"allOf", "anyOf", "oneOf", "prefixItems"}
    schema_values = {
        "additionalProperties", "unevaluatedProperties", "propertyNames",
        "items", "contains", "unevaluatedItems", "not", "if", "then", "else",
    }

    registry = Registry(retrieve=no_remote)
    root_resource = Resource.from_contents(schema, default_specification=DRAFT202012)
    resolver = registry.resolver_with_root(root_resource)

    def check(node, resolver):
        if type(node) is bool:
            return
        if type(node) is not dict:
            raise ValueError("invalid schema")
        if set(node) - (set(Draft202012Validator.VALIDATORS) | annotations | assertion_modifiers):
            raise ValueError("unsupported schema keyword")
        if "$schema" in node and node["$schema"] not in {
            "https://json-schema.org/draft/2020-12/schema",
            "https://json-schema.org/draft/2020-12/schema#",
        }:
            raise ValueError("unsupported schema dialect")
        for key in ("$ref", "$dynamicRef"):
            if key in node and (not isinstance(node[key], str) or not node[key].startswith("#")):
                raise ValueError("external schema references are unsupported")
            if key in node:
                # Resolve at configuration time, without fetching or following
                # recursively. A dangling local reference must not be exposed.
                target = resolver.lookup(node[key]).contents
                if type(target) not in (dict, bool):
                    raise ValueError("local reference target is not a schema")
                Draft202012Validator.check_schema(target)
        def check_child(child):
            resource = Resource.from_contents(child, default_specification=DRAFT202012)
            check(child, resolver.in_subresource(resource))
        for key in schema_maps & node.keys():
            for child in node[key].values():
                check_child(child)
        for key in schema_lists & node.keys():
            for child in node[key]:
                check_child(child)
        for key in schema_values & node.keys():
            check_child(node[key])

    Draft202012Validator.check_schema(schema)
    check(schema, resolver)
    return Draft202012Validator(schema, registry=registry)


def normalize_tool_schema(spec: Any) -> HostToolDefinition:
    """Normalize one public OpenAI- or Anthropic-shaped tool schema.

    The canonical host name is preserved exactly.  In particular, a native
    ``mcp__server__tool`` name is never stripped or provider-remapped.
    """

    if not isinstance(spec, Mapping):
        raise ToolBridgeConfigurationError("tool schema must be an object")

    if spec.get("type") == "function":
        function = spec.get("function")
        if not isinstance(function, Mapping):
            raise ToolBridgeConfigurationError("tool schema function is malformed")
        name = function.get("name")
        description = function.get("description")
        input_schema = function.get("parameters")
    else:
        name = spec.get("name")
        description = spec.get("description")
        input_schema = spec.get("input_schema", spec.get("parameters"))

    if (
        type(name) is not str
        or not name
        or _utf8_size(name) is None
        or _utf8_size(name) > _MAX_NAME_UTF8_BYTES
        or any(unicodedata.category(char).startswith("C") or char.isspace() for char in name)
    ):
        raise ToolBridgeConfigurationError("tool schema name is malformed")
    if description is None:
        description = name
    if type(description) is not str:
        raise ToolBridgeConfigurationError("tool schema description is malformed")
    description = _model_text(description, redact=False)
    if description is None:
        raise ToolBridgeConfigurationError("tool schema description is malformed")

    copied_schema = _copy_plain_json(input_schema, max_input_bytes=None)
    if (
        copied_schema is _INVALID
        or type(copied_schema) is not dict
        or copied_schema.get("type") != "object"
    ):
        raise ToolBridgeConfigurationError("tool schema input schema is unsupported")
    try:
        _schema_validator(copied_schema)
    except Exception:
        # Schema diagnostics may contain host-private values: expose only a code.
        raise ToolBridgeConfigurationError("tool schema input schema is unsupported") from None
    return HostToolDefinition(name, description, copied_schema)


def normalize_tool_schemas(specs: Sequence[Any]) -> tuple[HostToolDefinition, ...]:
    """Normalize, reject duplicates, and sort a tool schema collection."""

    if isinstance(specs, (str, bytes, bytearray)):
        raise ToolBridgeConfigurationError("tool schemas must be a sequence")
    try:
        iterator = iter(specs)
    except TypeError as exc:
        raise ToolBridgeConfigurationError("tool schemas must be a sequence") from exc

    by_name: dict[str, HostToolDefinition] = {}
    for spec in iterator:
        definition = normalize_tool_schema(spec)
        if definition.name in by_name:
            raise ToolBridgeConfigurationError("duplicate tool schema name")
        by_name[definition.name] = definition
    return tuple(by_name[name] for name in sorted(by_name))


def _matches_schema(value: Any, schema: dict[str, Any]) -> bool:
    try:
        return _schema_validator(schema).is_valid(value)
    except Exception:
        # Invalid/unresolvable schemas fail before execution, without retrieval.
        return False


def _result_text(value: Any) -> tuple[str, bool]:
    """Convert host-approved output without display truncation or flattening."""

    copied = _copy_plain_json(value, max_input_bytes=None)
    if copied is _INVALID:
        return "Host returned unsupported result", True
    try:
        if type(copied) is str:
            text = copied
        else:
            text = json.dumps(
                copied,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        return "Host returned unsupported result", True
    bounded = _model_text(text)
    if bounded is None:
        return "Host returned unsupported result", True
    is_error = False
    if type(copied) is dict and set(copied) == {"error"}:
        is_error = True
    elif type(copied) is str:
        try:
            parsed = json.loads(copied)
        except (TypeError, ValueError, RecursionError, UnicodeError):
            parsed = None
        is_error = type(parsed) is dict and set(parsed) == {"error"}
    return bounded, is_error


class HostToolBridge:
    """Expose normalized schemas through an injected host execution facade."""

    def __init__(
        self,
        host: RuntimeHostServices,
        tool_schemas: Sequence[Any],
        *,
        excluded_names: Iterable[str] = (),
        correlation_id: str | None = None,
    ) -> None:
        execute = getattr(host, "execute_tool", None)
        cancellation = getattr(host, "cancellation_requested", None)
        if not callable(execute) or not callable(cancellation):
            raise ToolBridgeConfigurationError("host services are incomplete")
        if correlation_id is not None:
            correlation_id = self._safe_identifier(correlation_id, _MAX_REQUEST_ID_UTF8_BYTES)
            if correlation_id is None:
                raise ToolBridgeConfigurationError("host correlation is malformed")
        try:
            excluded = frozenset(excluded_names)
        except TypeError as exc:
            raise ToolBridgeConfigurationError("excluded tool names are malformed") from exc
        if any(
            type(name) is not str
            or not name
            or self._safe_identifier(name, _MAX_NAME_UTF8_BYTES) is None
            for name in excluded
        ):
            raise ToolBridgeConfigurationError("excluded tool names are malformed")

        definitions = normalize_tool_schemas(tool_schemas)
        self._host = host
        self._correlation_id = correlation_id
        self._excluded_names = excluded
        self._definitions = tuple(
            definition for definition in definitions if definition.name not in excluded
        )
        self._definitions_by_name = {
            definition.name: definition for definition in self._definitions
        }
        self._sdk_call_count = 0
        self._host_execution_count = 0

    @staticmethod
    def _safe_identifier(value: Any, limit: int) -> str | None:
        if type(value) is not str or not value or _utf8_size(value) is None:
            return None
        if _utf8_size(value) > limit or _SAFE_IDENTIFIER_RE.fullmatch(value) is None:
            return None
        return value

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(definition.name for definition in self._definitions)

    @property
    def tool_definitions(self) -> tuple[HostToolDefinition, ...]:
        return self._definitions

    @property
    def host_execution_count(self) -> int:
        """Return only the number of calls that crossed into host execution."""

        return self._host_execution_count

    def begin_turn(self, correlation_id: str | None) -> None:
        """Bind subsequent SDK tool calls to the current Hermes turn."""

        if correlation_id is not None:
            correlation_id = self._safe_identifier(
                correlation_id, _MAX_REQUEST_ID_UTF8_BYTES
            )
            if correlation_id is None:
                raise ToolBridgeConfigurationError("host correlation is malformed")
        self._correlation_id = correlation_id

    def bind_transcript_admission(self, admission) -> None:
        """Bind the current turn's awaited host-persistence acknowledgment."""
        self._transcript_admission = admission

    async def handle_tool_call(
        self,
        request_id: str,
        name: str,
        arguments: Any,
    ) -> ToolCallResult:
        """Validate one request and route it through host execution exactly once."""

        safe_request_id = self._safe_identifier(request_id, _MAX_REQUEST_ID_UTF8_BYTES)
        if safe_request_id is None:
            raise ToolBridgeRequestError("request_id")
        if type(name) is not str:
            raise ToolBridgeRequestError("name")
        definition = self._definitions_by_name.get(name)
        if definition is None:
            raise ToolBridgeRequestError("unknown")

        copied_arguments = _copy_plain_json(arguments)
        if (
            copied_arguments is _INVALID
            or type(copied_arguments) is not dict
            or not _matches_schema(copied_arguments, definition.input_schema)
        ):
            raise ToolBridgeRequestError("arguments")

        if self._is_cancelled():
            raise ToolBridgeRequestError("cancelled")

        execute = self._host.execute_tool
        try:
            self._host_execution_count += 1
            result = execute(
                name,
                copied_arguments,
                request_id=safe_request_id,
            )
            if not inspect.isawaitable(result):
                return ToolCallResult(
                    safe_request_id,
                    self._correlation_id,
                    name,
                    "Host tool execution failed",
                    True,
                )
            host_result = await result
        except asyncio.CancelledError:
            raise
        except BaseException:
            # Deliberately omit exception type/message: both can carry secrets
            # or customer content, and the host remains the diagnostic owner.
            return ToolCallResult(
                safe_request_id,
                self._correlation_id,
                name,
                "Host tool execution failed",
                True,
            )

        if self._is_cancelled():
            return ToolCallResult(
                safe_request_id,
                self._correlation_id,
                name,
                "Tool call cancelled",
                True,
            )
        text, is_error = _result_text(host_result)
        return ToolCallResult(
            safe_request_id,
            self._correlation_id,
            name,
            text,
            is_error,
        )

    async def execute_tool(
        self,
        name: str,
        arguments: Any,
        *,
        request_id: str | None = None,
    ) -> ToolCallResult:
        """Convenience alias for runtime integrations using host-like naming."""

        if request_id is None:
            request_id = self._next_sdk_request_id(name)
        return await self.handle_tool_call(request_id, name, arguments)

    async def dispatch(
        self,
        request_id: str,
        name: str,
        arguments: Any,
    ) -> ToolCallResult:
        """Alias retaining the explicit runtime request correlation."""

        return await self.handle_tool_call(request_id, name, arguments)

    def _is_cancelled(self) -> bool:
        try:
            value = self._host.cancellation_requested()
        except BaseException:
            # Cancellation observation is a mandatory safety gate.  A broken
            # probe therefore fails closed rather than executing a tool.
            raise ToolBridgeRequestError("cancellation_unavailable")
        if type(value) is not bool:
            raise ToolBridgeRequestError("cancellation_unavailable")
        return value

    def _next_sdk_request_id(self, name: str) -> str:
        self._sdk_call_count += 1
        prefix = self._correlation_id or "sdk"
        candidate = f"{prefix}:sdk-call-{self._sdk_call_count:04d}"
        bounded = self._safe_identifier(candidate, _MAX_REQUEST_ID_UTF8_BYTES)
        return bounded or f"sdk-call-{self._sdk_call_count:04d}"

    def _make_sdk_handler(self, name: str):
        async def handler(arguments: Any) -> dict[str, Any]:
            request_id = self._next_sdk_request_id(name)
            try:
                admission = getattr(self, "_transcript_admission", None)
                if admission is not None:
                    try:
                        request_id = await admission(name, arguments)
                    except Exception:
                        raise ToolBridgeRequestError("cancelled") from None
                result = await self.handle_tool_call(request_id, name, arguments)
            except ToolBridgeRequestError as exc:
                return ToolCallResult(
                    request_id,
                    self._correlation_id,
                    name,
                    str(exc),
                    True,
                ).to_sdk_result()
            return result.to_sdk_result()

        return handler

    def build_sdk_mcp_server(
        self,
        server_name: str = "hermes-tools",
        *,
        version: str = "1.0.0",
        sdk_module: Any | None = None,
    ) -> Any:
        """Build the public SDK in-process MCP adapter lazily.

        The returned server contains no independent executor.  Its handlers
        close over this bridge and can only call ``handle_tool_call``.
        """

        safe_server_name = self._safe_identifier(server_name, _MAX_NAME_UTF8_BYTES)
        safe_version = self._safe_identifier(version, _MAX_NAME_UTF8_BYTES)
        if safe_server_name is None or safe_version is None:
            raise ToolBridgeConfigurationError("SDK MCP server identity is malformed")

        # Import only at adapter construction, never at plugin/module import.
        if sdk_module is None:
            import claude_agent_sdk as sdk
        else:
            sdk = sdk_module

        sdk_tools: list[Any] = []
        for definition in self._definitions:
            sdk_tools.append(
                sdk.tool(
                    definition.name,
                    definition.description,
                    definition.input_schema,
                )(self._make_sdk_handler(definition.name))
            )
        return sdk.create_sdk_mcp_server(
            name=safe_server_name,
            version=safe_version,
            tools=sdk_tools,
        )


__all__ = [
    "HostToolBridge",
    "HostToolDefinition",
    "RuntimeHostServices",
    "ToolBridgeConfigurationError",
    "ToolBridgeError",
    "ToolBridgeRequestError",
    "ToolCallResult",
    "normalize_tool_schema",
    "normalize_tool_schemas",
]
