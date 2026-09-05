from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from hermes_claude_agent_sdk.memory_skills import build_memory_skill_references
from hermes_claude_agent_sdk.prompt_context import (
    DEFAULT_TOTAL_MAX_CHARS,
    ProjectMetadata,
    PromptContextLimits,
    PromptContextSnapshot,
    build_sdk_prompt_context,
    render_prompt_append,
)


def _schema(name: str) -> dict:
    return {"name": name, "parameters": {"type": "object"}}


def _request(**overrides):
    values = {
        "prompt_snapshot": "base prompt",
        "selection": SimpleNamespace(
            provider="claude-agent-sdk",
            model="claude-fable-5",
            api_mode="agent_runtime",
        ),
        "tool_schemas": (_schema("skills_list"), _schema("memory")),
        "attachments": (),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_project_metadata_is_immutable_and_deterministically_ordered() -> None:
    project = ProjectMetadata.from_mapping(
        {
            "name": "demo",
            "instructions": "run the focused check",
            "files": {
                "z.md": "z content",
                "a.md": "a content",
            },
            "config": {"token": "must not enter the snapshot"},
            "environment": "must not enter the snapshot",
        }
    )

    assert project.label == "demo"
    assert [item.path for item in project.files] == ["a.md", "z.md"]
    assert "token" not in repr(project)
    with pytest.raises(FrozenInstanceError):
        project.label = "changed"  # type: ignore[misc]


def test_append_order_and_sdk_options_are_bounded_and_public_only() -> None:
    project = ProjectMetadata.from_mapping(
        {
            "label": "demo",
            "instructions": "project instructions",
            "files": {"AGENTS.md": "follow this file"},
        }
    )
    request = _request()
    request.tool_schema_hash = "a" * 64
    context = build_sdk_prompt_context(
        request,
        project=project,
        identity="identity",
        platform_hint="platform",
        memory_fragments=("memory b", "memory a"),
        skill_fragments=("skill b", "skill a"),
    )

    assert context.base_prompt == "base prompt"
    rendered = context.system_prompt_append or ""
    assert rendered.index("identity") < rendered.index("platform")
    assert rendered.index("platform") < rendered.index("# Project Context")
    assert rendered.index("# Project Context") < rendered.index("memory a")
    assert rendered.index("memory a") < rendered.index("skill a")
    assert len(rendered) <= DEFAULT_TOTAL_MAX_CHARS
    assert context.options["model"] == "claude-fable-5"
    assert context.options["tool_names"] == ("memory", "skills_list")
    assert context.tool_schema_hash == "a" * 64
    assert context.options["tool_schema_hash"] == "a" * 64
    assert "tool_schemas" not in context.options
    assert "execute_tool" not in context.options


def test_large_project_block_is_skipped_without_evicting_later_guidance() -> None:
    limits = PromptContextLimits(fragment_max_chars=64, total_max_chars=170)
    snapshot = PromptContextSnapshot(
        identity="identity",
        project=ProjectMetadata(label="large", instructions="p" * 500),
        memory_fragments=("memory-guidance",),
        skill_fragments=("skill-guidance",),
    )
    refs = build_memory_skill_references([_schema("memory")])

    output = render_prompt_append(snapshot, refs, limits=limits)

    assert len(output or "") <= limits.total_max_chars
    assert "memory-guidance" in (output or "")
    assert "skill-guidance" in (output or "")


def test_request_attachments_only_accept_explicit_project_fields() -> None:
    request = _request(
        attachments=(
            {
                "kind": "project",
                "name": "attached project",
                "instructions": "attached instructions",
                "config": "do not capture",
                "env": {"TOKEN": "do not capture"},
            },
        )
    )

    context = build_sdk_prompt_context(request)
    rendered = context.system_prompt_append or ""

    assert "attached project" in rendered
    assert "attached instructions" in rendered
    assert "do not capture" not in rendered
    assert "TOKEN" not in rendered


def test_prompt_snapshot_does_not_capture_private_request_objects_or_sdk() -> None:
    request = _request(
        private_host_object=SimpleNamespace(secret="must not be read"),
        config={"secret": "must not be read"},
    )

    context = build_sdk_prompt_context(request)
    assert "must not be read" not in repr(context)
    assert context.memory_skills.tool_names == ("memory", "skills_list")
