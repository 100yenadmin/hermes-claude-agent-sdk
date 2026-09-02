from __future__ import annotations

from pathlib import Path

import pytest

import hermes_claude_agent_sdk.compatibility as compatibility


_RESOURCE = "claude_agent_sdk/_bundled/claude"


class FakeDistribution:
    def __init__(self, files: list[str], located: object) -> None:
        self.files = files
        self.located = located

    def locate_file(self, candidate: object) -> object:
        return self.located


def _executable(path: Path) -> Path:
    path.write_bytes(b"#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_resolve_bundled_cli_returns_unique_absolute_executable_resource(
    tmp_path: Path,
) -> None:
    executable = _executable(tmp_path / "claude")
    distribution = FakeDistribution([_RESOURCE], executable)

    assert compatibility.resolve_bundled_cli(distribution=distribution) == str(
        executable
    )


@pytest.mark.parametrize(
    ("files", "located"),
    [
        ([], "/tmp/claude"),
        ([_RESOURCE, _RESOURCE], "/tmp/claude"),
        ([_RESOURCE], None),
        ([_RESOURCE], "relative/claude"),
        ([_RESOURCE], "bad\x00path"),
    ],
)
def test_resolve_bundled_cli_fails_closed_for_missing_duplicate_or_malformed_path(
    files: list[str],
    located: object,
) -> None:
    distribution = FakeDistribution(files, located)

    assert compatibility.resolve_bundled_cli(distribution=distribution) is None


def test_resolve_bundled_cli_rejects_nonfile_resource(tmp_path: Path) -> None:
    resource_dir = tmp_path / "claude"
    resource_dir.mkdir()

    assert (
        compatibility.resolve_bundled_cli(
            distribution=FakeDistribution([_RESOURCE], resource_dir)
        )
        is None
    )


def test_resolve_bundled_cli_rejects_nonexecutable_resource(tmp_path: Path) -> None:
    resource = tmp_path / "claude"
    resource.write_bytes(b"not executable")
    resource.chmod(0o644)

    assert (
        compatibility.resolve_bundled_cli(
            distribution=FakeDistribution([_RESOURCE], resource)
        )
        is None
    )


def test_resolve_bundled_cli_reads_distribution_metadata_without_sdk_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = _executable(tmp_path / "claude")
    distribution = FakeDistribution([_RESOURCE], executable)
    imported: list[str] = []

    def reject_sdk_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "claude_agent_sdk" or name.startswith("claude_agent_sdk."):
            imported.append(name)
            raise AssertionError("resolver must not import claude_agent_sdk")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", reject_sdk_import)

    assert compatibility.resolve_bundled_cli(distribution=distribution) == str(
        executable
    )
    assert imported == []


def test_resolve_bundled_cli_uses_exact_platform_resource_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = _executable(tmp_path / "claude")
    distribution = FakeDistribution([_RESOURCE], executable)
    monkeypatch.setattr(compatibility.platform, "system", lambda: "Darwin")

    assert compatibility.resolve_bundled_cli(distribution=distribution) == str(
        executable
    )
