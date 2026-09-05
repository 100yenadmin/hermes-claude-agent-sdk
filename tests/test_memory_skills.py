from __future__ import annotations

from hermes_claude_agent_sdk.memory_skills import (
    build_memory_skill_references,
    stable_tool_schema_hash,
)


def _schema(name: str, *, description: str = "") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_reference_order_and_aggregate_hash_are_input_order_independent() -> None:
    first = build_memory_skill_references(
        [_schema("skills_list"), _schema("memory"), _schema("session_search")]
    )
    second = build_memory_skill_references(
        [_schema("session_search"), _schema("memory"), _schema("skills_list")]
    )

    assert first.tool_names == ("memory", "session_search", "skills_list")
    assert first.tool_names == second.tool_names
    assert first.schema_hash == second.schema_hash
    assert first.memory_tool_names == ("memory", "session_search")
    assert first.skill_tool_names == ("skills_list",)


def test_only_read_side_memory_and_skill_tools_become_non_executable_refs() -> None:
    refs = build_memory_skill_references(
        [
            _schema("memory"),
            _schema("skill_view"),
            _schema("skill_manage"),
            _schema("shell"),
            {
                "name": "memory",
                "callback": "must never be copied",
                "config": {"token": "must never be copied"},
            },
        ]
    )

    assert refs.tool_names == ("memory", "skill_view")
    assert all(not hasattr(ref, "schema") for ref in refs.references)
    assert all(not hasattr(ref, "callback") for ref in refs.references)
    assert "must never be copied" not in repr(refs)
    assert "token" not in repr(refs)


def test_schema_hash_is_stable_for_mapping_order_but_changes_for_schema_content() -> None:
    left = {"name": "memory", "parameters": {"b": 2, "a": 1}}
    right = {"parameters": {"a": 1, "b": 2}, "name": "memory"}

    assert stable_tool_schema_hash(left) == stable_tool_schema_hash(right)
    assert stable_tool_schema_hash(left) != stable_tool_schema_hash(
        {**left, "description": "different"}
    )


def test_non_string_mapping_keys_hash_without_comparing_opaque_values() -> None:
    schema = {
        "name": "memory",
        "parameters": {
            1: {"first": "opaque"},
            2: {"second": "opaque"},
        },
    }

    assert len(stable_tool_schema_hash(schema)) == 64


def test_heterogeneous_nested_mapping_values_do_not_abort_reference_build() -> None:
    refs = build_memory_skill_references(
        [
            {
                "name": "memory",
                "parameters": {
                    1: {"first": "opaque"},
                    2: ["second", "opaque"],
                },
            }
        ]
    )

    assert refs.tool_names == ("memory",)


def test_unknown_and_malformed_schemas_are_ignored_without_raw_payload_capture() -> None:
    refs = build_memory_skill_references(
        [
            {"name": "unknown", "secret": "redacted"},
            {"function": {"description": "missing name"}},
            "not a schema",
            None,
        ]
    )

    assert refs.references == ()
    assert refs.tool_names == ()
    assert "redacted" not in repr(refs)
