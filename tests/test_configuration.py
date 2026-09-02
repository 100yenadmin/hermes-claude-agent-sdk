"""Offline compatibility metadata and package-policy tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import hermes_claude_agent_sdk.compatibility as compatibility


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


def test_package_dependency_admits_both_frozen_and_successor_cells():
    root = Path(__file__).resolve().parents[1]
    project = (root / "pyproject.toml").read_text()

    assert '"claude-agent-sdk>=0.2.144,<0.2.152"' in project
