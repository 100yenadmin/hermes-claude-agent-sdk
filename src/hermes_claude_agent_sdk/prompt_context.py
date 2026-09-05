"""Pure prompt/context adapter for the Claude Agent SDK plugin.

Hermes builds and owns the public ``RuntimeTurnRequest``.  The adapter only
reads its public attributes (prompt snapshot, selection, attachments, and tool
schemas), then produces bounded SDK-facing values.  It never imports Claude's
SDK or Hermes' private prompt, memory, configuration, or execution modules.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .memory_skills import MemorySkillReferences, build_memory_skill_references

DEFAULT_FRAGMENT_MAX_CHARS = 8_000
DEFAULT_TOTAL_MAX_CHARS = 22_000

_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|auth(?:orization)?|cookie|credential|"
    r"password|passwd|secret|token)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(?:\b(?:api[_ -]?key|access[_ -]?token|authorization|cookie|credential|"
    r"password|passwd|secret|token)\b\s*[:=]\s*)[^\s,;]+"
    r"|\bBearer\s+[^\s,;]+|\bsk-[A-Za-z0-9_-]{8,}",
    re.IGNORECASE,
)


def _public_get(value: Any, key: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _safe_text(value: Any, limit: int) -> str:
    """Coerce bounded text and redact common inline credential forms."""

    if (
        limit <= 0
        or value is None
        or isinstance(value, (Mapping, Sequence)) and not isinstance(value, str)
    ):
        return ""
    text = str(value).replace("\x00", "").strip()
    text = _SENSITIVE_VALUE_RE.sub("[redacted]", text)
    return text[:limit]


def _strip_uncallable_skill_guidance(text: str) -> str:
    """Drop sentences that advertise the host-owned mutating skill tool."""

    return re.sub(r"[^.!?\n]*\bskill_manage\b[^.!?\n]*[.!?]?\s*", "", text, flags=re.IGNORECASE)


def _safe_relative_path(value: Any) -> str:
    path = _safe_text(value, 240).replace("\\", "/")
    if not path or path.startswith(("/", "~")):
        return ""
    components = path.split("/")
    if any(component in {"", ".", ".."} for component in components):
        return ""
    if any(_SENSITIVE_KEY_RE.search(component) for component in components):
        return ""
    return path


@dataclass(frozen=True, repr=False)
class ProjectFile:
    """A bounded, relative project-context file snapshot."""

    path: str
    content: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _safe_relative_path(self.path))
        object.__setattr__(
            self,
            "content",
            _safe_text(self.content, DEFAULT_FRAGMENT_MAX_CHARS),
        )

    def __repr__(self) -> str:
        return (
            f"ProjectFile(path_chars={len(self.path)}, "
            f"content_chars={len(self.content)})"
        )


def _coerce_project_file(value: Any) -> ProjectFile | None:
    if isinstance(value, ProjectFile):
        return value if value.path else None
    if isinstance(value, Mapping):
        path = value.get("path", value.get("name", ""))
        content = value.get("content", value.get("text", ""))
        candidate = ProjectFile(path=path, content=content)
        return candidate if candidate.path else None
    return None


@dataclass(frozen=True, repr=False)
class ProjectMetadata:
    """Immutable project instructions accepted by the adapter.

    Only ``label``/``name``, ``instructions``, and relative ``files`` are
    accepted by :meth:`from_mapping`.  Config, environment, credential, and
    absolute-path fields are deliberately outside this input contract.
    """

    label: str = ""
    instructions: str = ""
    files: tuple[ProjectFile, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _safe_text(self.label, 240))
        object.__setattr__(
            self,
            "instructions",
            _safe_text(self.instructions, DEFAULT_FRAGMENT_MAX_CHARS),
        )

        raw_files: Any = self.files
        if isinstance(raw_files, Mapping):
            raw_items = [
                {"path": path, "content": content}
                for path, content in raw_files.items()
            ]
        elif isinstance(raw_files, Sequence) and not isinstance(
            raw_files, (str, bytes, bytearray)
        ):
            raw_items = raw_files
        else:
            raw_items = ()
        files = [
            item for item in (_coerce_project_file(value) for value in raw_items) if item
        ]
        # A host snapshot may be assembled from more than one source.  Keep a
        # single deterministic entry for each path and never trust input order.
        deduped: dict[str, ProjectFile] = {}
        for item in files:
            previous = deduped.get(item.path)
            if previous is None or item.content < previous.content:
                deduped[item.path] = item
        object.__setattr__(
            self,
            "files",
            tuple(sorted(deduped.values(), key=lambda item: (item.path, item.content))),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> ProjectMetadata:
        """Read the explicit project fields from a public metadata mapping."""

        if not isinstance(value, Mapping):
            return cls()
        return cls(
            label=value.get("label", value.get("name", "")),
            instructions=value.get("instructions", value.get("content", "")),
            files=value.get("files", ()),
        )

    def render(self, *, max_chars: int = DEFAULT_FRAGMENT_MAX_CHARS) -> str:
        """Render project context without reading ambient files or config."""

        if max_chars <= 0:
            return ""
        sections: list[str] = []
        if self.label:
            sections.append(f"## {self.label}")
        if self.instructions:
            sections.append(self.instructions)
        for item in self.files:
            sections.append(f"## {item.path}\n\n{item.content}")
        if not sections:
            return ""
        rendered = "# Project Context\n\n" + "\n\n".join(sections)
        return rendered[:max_chars]

    def __repr__(self) -> str:
        return (
            f"ProjectMetadata(label_chars={len(self.label)}, "
            f"instructions_chars={len(self.instructions)}, files_count={len(self.files)})"
        )


@dataclass(frozen=True)
class PromptContextLimits:
    """Caps applied before values enter SDK options or prompt text."""

    fragment_max_chars: int = DEFAULT_FRAGMENT_MAX_CHARS
    total_max_chars: int = DEFAULT_TOTAL_MAX_CHARS

    def __post_init__(self) -> None:
        if self.fragment_max_chars < 1 or self.total_max_chars < 1:
            raise ValueError("prompt context limits must be positive")


@dataclass(frozen=True, repr=False)
class PromptContextSnapshot:
    """Immutable prompt and project snapshot supplied to one SDK turn."""

    base_prompt: str = ""
    identity: str = ""
    session_metadata: str = ""
    platform_hint: str = ""
    provider: str = ""
    model: str = ""
    project: ProjectMetadata = ProjectMetadata()
    memory_fragments: tuple[str, ...] = ()
    skill_fragments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "base_prompt",
            "identity",
            "session_metadata",
            "platform_hint",
            "provider",
            "model",
        ):
            object.__setattr__(
                self,
                name,
                _safe_text(getattr(self, name), DEFAULT_FRAGMENT_MAX_CHARS),
            )
        project = self.project
        if not isinstance(project, ProjectMetadata):
            project = ProjectMetadata.from_mapping(
                project if isinstance(project, Mapping) else None
            )
        object.__setattr__(self, "project", project)
        for name in ("memory_fragments", "skill_fragments"):
            values = getattr(self, name)
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
                values = ()
            normalized = sorted(
                {
                    (
                        _strip_uncallable_skill_guidance(
                            _safe_text(value, DEFAULT_FRAGMENT_MAX_CHARS)
                        )
                        if name == "skill_fragments"
                        else _safe_text(value, DEFAULT_FRAGMENT_MAX_CHARS)
                    )
                    for value in values
                    if _safe_text(value, DEFAULT_FRAGMENT_MAX_CHARS)
                }
            )
            object.__setattr__(self, name, tuple(item for item in normalized if item))

    @classmethod
    def from_request(
        cls,
        request: Any,
        *,
        project: ProjectMetadata | Mapping[str, Any] | None = None,
        identity: str = "",
        session_metadata: str = "",
        platform_hint: str = "",
        memory_fragments: Sequence[str] = (),
        skill_fragments: Sequence[str] = (),
    ) -> PromptContextSnapshot:
        """Copy only public request fields into an immutable local snapshot."""

        selection = _public_get(request, "selection", None)
        resolved_project = project
        if resolved_project is None:
            resolved_project = _project_from_attachments(
                _public_get(request, "attachments", ())
            )
        return cls(
            base_prompt=_public_get(request, "prompt_snapshot", ""),
            identity=identity,
            session_metadata=session_metadata,
            platform_hint=platform_hint,
            provider=_public_get(selection, "provider", ""),
            model=_public_get(selection, "model", ""),
            project=resolved_project or ProjectMetadata(),
            memory_fragments=memory_fragments,
            skill_fragments=skill_fragments,
        )

    def __repr__(self) -> str:
        return (
            "PromptContextSnapshot("
            f"base_prompt_chars={len(self.base_prompt)}, identity_chars={len(self.identity)}, "
            f"project={self.project!r}, memory_count={len(self.memory_fragments)}, "
            f"skill_count={len(self.skill_fragments)})"
        )


def _project_from_attachments(attachments: Any) -> ProjectMetadata:
    if not isinstance(attachments, Sequence) or isinstance(
        attachments, (str, bytes, bytearray)
    ):
        return ProjectMetadata()
    for attachment in attachments:
        if not isinstance(attachment, Mapping):
            continue
        kind = str(attachment.get("kind", attachment.get("type", ""))).strip().lower()
        if kind not in {"project", "project_context"}:
            continue
        metadata = attachment.get("metadata")
        if isinstance(metadata, Mapping):
            return ProjectMetadata.from_mapping(metadata)
        return ProjectMetadata.from_mapping(attachment)
    return ProjectMetadata()


def _selection_metadata(
    snapshot: PromptContextSnapshot, limits: PromptContextLimits
) -> str:
    if snapshot.session_metadata:
        return snapshot.session_metadata[: limits.fragment_max_chars]
    parts = []
    if snapshot.model:
        parts.append(f"Model: {snapshot.model}")
    if snapshot.provider:
        parts.append(f"Provider: {snapshot.provider}")
    return "\n".join(parts)[:limits.fragment_max_chars]


def render_prompt_append(
    snapshot: PromptContextSnapshot,
    memory_skills: MemorySkillReferences | None = None,
    *,
    limits: PromptContextLimits | None = None,
) -> str | None:
    """Compose fixed-order, whole-block prompt fragments for the SDK.

    A block that cannot fit is omitted as a unit, allowing later small blocks
    to remain available.  This mirrors the candidate's append-budget behavior
    without loading any host files or mutable process state.
    """

    limits = limits or PromptContextLimits()
    refs = memory_skills or MemorySkillReferences()
    blocks: list[str] = []

    for value in (
        snapshot.identity,
        _selection_metadata(snapshot, limits),
        snapshot.platform_hint,
        snapshot.project.render(max_chars=limits.fragment_max_chars),
        *snapshot.memory_fragments,
    ):
        text = _safe_text(value, limits.fragment_max_chars)
        if text:
            blocks.append(text)

    guidance = refs.prompt_guidance()
    if guidance:
        blocks.append(_safe_text(guidance, limits.fragment_max_chars))
    for value in snapshot.skill_fragments:
        text = _safe_text(value, limits.fragment_max_chars)
        if text:
            blocks.append(text)

    output: list[str] = []
    used = 0
    for block in blocks:
        cost = len(block) + (2 if output else 0)
        if used + cost > limits.total_max_chars:
            continue
        output.append(block)
        used += cost
    return "\n\n".join(output) or None


@dataclass(frozen=True, repr=False)
class SdkPromptContext:
    """Bounded SDK inputs plus host-owned memory/skill references."""

    base_prompt: str
    system_prompt_append: str | None
    memory_skills: MemorySkillReferences
    options: Mapping[str, Any]
    tool_schema_hash: str = ""

    def __repr__(self) -> str:
        return (
            f"SdkPromptContext(base_prompt_chars={len(self.base_prompt)}, "
            f"append_chars={len(self.system_prompt_append or '')}, "
            f"tool_names={self.memory_skills.tool_names!r})"
        )

    @property
    def prompt(self) -> str:
        """Alias used by SDK adapters for the immutable base prompt."""

        return self.base_prompt

    @property
    def system_prompt(self) -> str | None:
        return self.system_prompt_append


def build_sdk_prompt_context(
    request: Any,
    *,
    project: ProjectMetadata | Mapping[str, Any] | None = None,
    identity: str = "",
    session_metadata: str = "",
    platform_hint: str = "",
    memory_fragments: Sequence[str] = (),
    skill_fragments: Sequence[str] = (),
    memory_skills: MemorySkillReferences | None = None,
    limits: PromptContextLimits | None = None,
) -> SdkPromptContext:
    """Build deterministic SDK prompt options from public request data."""

    limits = limits or PromptContextLimits()
    snapshot = PromptContextSnapshot.from_request(
        request,
        project=project,
        identity=identity,
        session_metadata=session_metadata,
        platform_hint=platform_hint,
        memory_fragments=memory_fragments,
        skill_fragments=skill_fragments,
    )
    refs = memory_skills or build_memory_skill_references(
        _public_get(request, "tool_schemas", ())
    )
    host_tool_schema_hash = _safe_text(
        _public_get(request, "tool_schema_hash", ""),
        64,
    )
    if not re.fullmatch(r"[0-9a-fA-F]{64}", host_tool_schema_hash):
        host_tool_schema_hash = refs.schema_hash
    append = render_prompt_append(snapshot, refs, limits=limits)
    options = MappingProxyType(
        {
            "model": _safe_text(snapshot.model, limits.fragment_max_chars),
            "provider": _safe_text(snapshot.provider, limits.fragment_max_chars),
            "api_mode": _safe_text(
                _public_get(_public_get(request, "selection", None), "api_mode", ""),
                limits.fragment_max_chars,
            ),
            "system_prompt": append,
            "tool_names": refs.tool_names,
            "tool_schema_hash": host_tool_schema_hash,
            "memory_skill_schema_hash": refs.schema_hash,
        }
    )
    return SdkPromptContext(
        base_prompt=snapshot.base_prompt,
        system_prompt_append=append,
        memory_skills=refs,
        options=options,
        tool_schema_hash=host_tool_schema_hash,
    )


# Friendly aliases for host adapters migrating from the candidate naming.
PromptSnapshot = PromptContextSnapshot
build_prompt_context = build_sdk_prompt_context
build_system_prompt_append = render_prompt_append


__all__ = [
    "DEFAULT_FRAGMENT_MAX_CHARS",
    "DEFAULT_TOTAL_MAX_CHARS",
    "ProjectFile",
    "ProjectMetadata",
    "PromptContextLimits",
    "PromptContextSnapshot",
    "PromptSnapshot",
    "SdkPromptContext",
    "build_prompt_context",
    "build_sdk_prompt_context",
    "build_system_prompt_append",
    "render_prompt_append",
]
