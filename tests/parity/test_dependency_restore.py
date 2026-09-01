from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from hermes_claude_agent_sdk.parity.dependency_restore import (
    DependencyRestoreError,
    verify_dependency_restore_dry_run,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "qa" / "dependency-restore-manifest-v3.txt"


def test_dependency_restore_manifest_dry_run_retains_exact_sdk_pin() -> None:
    receipt = verify_dependency_restore_dry_run(MANIFEST)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert receipt.dry_run_succeeded is True
    assert receipt.project_version == "0.1.0rc1"
    assert receipt.sdk_version == "0.2.144"
    assert "claude-agent-sdk==0.2.144" in project["project"]["dependencies"]
    assert len(receipt.manifest_hash) == 64


def test_unpinned_restore_manifest_fails_before_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "restore.txt"
    manifest.write_text(
        "hermes-claude-agent-sdk==0.1.0rc1\n"
        "claude-agent-sdk>=0.2.144\n"
        "pyyaml==6.0.3\n",
        encoding="utf-8",
    )
    called = False

    def forbidden_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("pip must not run for an unsafe restore manifest")

    monkeypatch.setattr(subprocess, "run", forbidden_run)

    with pytest.raises(DependencyRestoreError, match="exact direct dependency set"):
        verify_dependency_restore_dry_run(manifest)
    assert called is False
