"""Offline compatibility metadata and package-policy tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import hermes_claude_agent_sdk.compatibility as compatibility
from hermes_claude_agent_sdk.configuration import SDKSessionConfiguration


class _Distribution:
    def __init__(self, version: object, cli_source: str | None) -> None:
        self.version = version
        self._cli_source = cli_source

    def read_text(self, name: str) -> str | None:
        assert name == "claude_agent_sdk/_cli_version.py"
        return self._cli_source


def test_sdk_metadata_reads_distribution_and_bundled_cli_without_import(
    monkeypatch,
):
    monkeypatch.setattr(
        compatibility.metadata,
        "distribution",
        lambda name: _Distribution(
            "0.2.151", '__cli_version__ = "2.1.258"\n'
        ),
    )

    report = compatibility._sdk_metadata()

    assert report["installed_version"] == "0.2.151"
    assert report["bundled_cli_version"] == "2.1.258"
    assert report["metadata_status"] == "compatible"


def test_malformed_bundled_cli_metadata_is_not_accepted(monkeypatch):
    monkeypatch.setattr(
        compatibility.metadata,
        "distribution",
        lambda name: _Distribution("0.2.151", "__cli_version__ = object()\n"),
    )

    report = compatibility._sdk_metadata()

    assert report["installed_version"] == "0.2.151"
    assert report["bundled_cli_version"] is None
    assert report["metadata_status"] == "malformed"


@pytest.mark.parametrize(
    "cli_source",
    (
        '__cli_version__ = "2.1.258"\n__cli_version__ += ".post1"\n',
        '__cli_version__ = "2.1.258"\ndel __cli_version__\n',
        (
            '__cli_version__ = "2.1.258"\n'
            'if True:\n    __cli_version__ = "2.1.239"\n'
        ),
        (
            '"""doc"""\n'
            '__cli_version__ = "2.1.258"\n'
            '"unexpected trailing statement"\n'
        ),
        '"first"\n"second"\n__cli_version__ = "2.1.258"\n',
    ),
)
def test_bundled_cli_mutation_or_reassignment_fails_closed(
    monkeypatch, cli_source
):
    monkeypatch.setattr(
        compatibility.metadata,
        "distribution",
        lambda name: _Distribution("0.2.151", cli_source),
    )

    report = compatibility._sdk_metadata()
    decision = compatibility.check_model_compatibility(
        compatibility.FABLE_51_MODEL_ID, sdk_metadata=report
    )

    assert report["bundled_cli_version"] is None
    assert report["metadata_status"] == "malformed"
    assert decision["compatible"] is False
    assert decision["reason"] == "metadata_unavailable"


def test_missing_distribution_metadata_is_not_accepted(monkeypatch):
    def missing(name: str):
        raise compatibility.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(compatibility.metadata, "distribution", missing)

    report = compatibility._sdk_metadata()

    assert report["installed_version"] is None
    assert report["bundled_cli_version"] is None
    assert report["metadata_status"] == "missing"


def test_malformed_sdk_distribution_version_is_not_accepted(monkeypatch):
    monkeypatch.setattr(
        compatibility.metadata,
        "distribution",
        lambda name: _Distribution("0.2.151+local", '__cli_version__ = "2.1.258"\n'),
    )

    report = compatibility._sdk_metadata()

    assert report["installed_version"] is None
    assert report["bundled_cli_version"] is None
    assert report["metadata_status"] == "malformed"


def test_sdk_above_first_rc_ceiling_is_not_accepted(monkeypatch):
    monkeypatch.setattr(
        compatibility.metadata,
        "distribution",
        lambda name: _Distribution("0.2.152", '__cli_version__ = "2.1.258"\n'),
    )

    report = compatibility._sdk_metadata()

    assert report["installed_version"] == "0.2.152"
    assert report["bundled_cli_version"] == "2.1.258"
    assert report["metadata_status"] == "unsupported"
    assert report["compatible"] is False


def test_incompatible_metadata_status_cannot_be_overridden_by_version_values():
    result = compatibility.check_model_compatibility(
        compatibility.FABLE_51_MODEL_ID,
        sdk_metadata={
            "installed_version": "0.2.151",
            "bundled_cli_version": "2.1.258",
            "metadata_status": "malformed",
        },
    )

    assert result["compatible"] is False
    assert result["reason"] == "metadata_unavailable"


def test_fable_51_rejects_a_non_exact_bundled_cli_identity():
    result = compatibility.check_model_compatibility(
        compatibility.FABLE_51_MODEL_ID,
        sdk_metadata={
            "installed_version": "0.2.151",
            "bundled_cli_version": "2.1.259",
            "metadata_status": "compatible",
        },
    )

    assert result["compatible"] is False
    assert result["reason"] == "bundled_cli_version_unsupported"


def test_package_dependency_admits_both_frozen_and_successor_cells():
    root = Path(__file__).resolve().parents[1]
    project = (root / "pyproject.toml").read_text()

    assert '"claude-agent-sdk==0.2.151"' in project


def test_option_fields_are_zero_native_and_use_the_exact_prompt_snapshot():
    configuration = SDKSessionConfiguration.create(
        cwd="/synthetic/workspace",
        model="claude-fable-5-1",
        prompt_snapshot="Hermes-owned prompt snapshot",
        mcp_servers={"hermes-tools": {"tools": []}},
        allowed_tools=("mcp__hermes-tools__pwd",),
    )

    fields = configuration.option_fields()

    assert fields["system_prompt"] == "Hermes-owned prompt snapshot"
    assert fields["tools"] == []
    assert fields["setting_sources"] == []
    assert fields["strict_mcp_config"] is True
    assert set(fields["mcp_servers"]) == {"hermes-tools"}
    assert fields["allowed_tools"] == ["mcp__hermes-tools__pwd"]
    assert not {
        "agents",
        "plugins",
        "extra_args",
        "settings",
        "skills",
        "hooks",
        "can_use_tool",
    } & set(fields)


def test_full_hermes_prompt_snapshot_is_preserved_with_a_bounded_utf8_limit():
    prompt_snapshot = "Hermes-owned context. " * 5_250

    configuration = SDKSessionConfiguration.create(
        cwd="/synthetic/workspace",
        model="claude-fable-5-1",
        prompt_snapshot=prompt_snapshot,
    )

    assert len(prompt_snapshot) > 100_000
    assert configuration.option_fields()["system_prompt"] == prompt_snapshot


def test_prompt_snapshot_over_one_megabyte_is_rejected():
    with pytest.raises(ValueError, match="prompt_snapshot is invalid"):
        SDKSessionConfiguration.create(
            cwd="/synthetic/workspace",
            prompt_snapshot="p" * (1_048_576 + 1),
        )


def test_nonempty_setting_sources_are_rejected_fail_closed():
    with pytest.raises(ValueError, match="setting_sources must be empty"):
        SDKSessionConfiguration.create(
            cwd="/synthetic/workspace",
            prompt_snapshot="Hermes-owned prompt snapshot",
            setting_sources=("user",),
        )


def test_non_hermes_mcp_server_and_raw_tool_names_are_rejected():
    with pytest.raises(ValueError, match="mcp_servers must contain only Hermes MCP"):
        SDKSessionConfiguration.create(
            cwd="/synthetic/workspace",
            mcp_servers={"other": {"tools": []}},
        )
    with pytest.raises(ValueError, match="Hermes MCP names"):
        SDKSessionConfiguration.create(
            cwd="/synthetic/workspace",
            allowed_tools=("pwd",),
        )
