"""Fail-closed validation for the package RC dependency restore manifest."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


_EXPECTED_REQUIREMENTS = (
    "hermes-claude-agent-sdk==0.1.0rc1",
    "claude-agent-sdk==0.2.144",
    "pyyaml==6.0.3",
)
_MAX_MANIFEST_BYTES = 16 * 1024
_SECRET_ENV_NAMES = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_API_KEY",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    }
)


class DependencyRestoreError(RuntimeError):
    """The restore manifest cannot be used safely."""


@dataclass(frozen=True, slots=True)
class DependencyRestoreReceipt:
    project_version: str
    sdk_version: str
    manifest_hash: str
    dry_run_succeeded: bool


def _read_manifest(path: Path) -> tuple[tuple[str, ...], bytes]:
    supplied = path.expanduser()
    if supplied.is_symlink():
        raise DependencyRestoreError("dependency restore manifest is unavailable")
    resolved = supplied.resolve()
    if (
        not resolved.is_file()
        or resolved.stat().st_size > _MAX_MANIFEST_BYTES
    ):
        raise DependencyRestoreError("dependency restore manifest is unavailable")
    try:
        raw = resolved.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise DependencyRestoreError("dependency restore manifest is unreadable") from exc
    requirements = tuple(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if requirements != _EXPECTED_REQUIREMENTS:
        raise DependencyRestoreError(
            "dependency restore manifest must contain the exact direct dependency set"
        )
    return requirements, raw


def verify_dependency_restore_dry_run(
    path: Path,
    *,
    python_executable: str = sys.executable,
) -> DependencyRestoreReceipt:
    """Validate exact pins, then ask pip for an offline no-change dry run."""

    _, raw = _read_manifest(path)
    try:
        project_version = importlib.metadata.version("hermes-claude-agent-sdk")
        sdk_version = importlib.metadata.version("claude-agent-sdk")
        yaml_version = importlib.metadata.version("pyyaml")
    except importlib.metadata.PackageNotFoundError as exc:
        raise DependencyRestoreError("a restore dependency is not installed") from exc
    if (project_version, sdk_version, yaml_version) != (
        "0.1.0rc1",
        "0.2.144",
        "6.0.3",
    ):
        raise DependencyRestoreError("installed dependency versions do not match the restore set")

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _SECRET_ENV_NAMES
    }
    env.update(
        {
            "LC_ALL": "C",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    if importlib.util.find_spec("pip") is not None:
        command = (
            python_executable,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--no-index",
            "--no-deps",
            "--no-cache-dir",
            "--requirement",
            str(path.expanduser().resolve()),
        )
    else:
        uv = shutil.which("uv")
        if uv is None:
            raise DependencyRestoreError("no dry-run dependency resolver is available")
        env["UV_OFFLINE"] = "1"
        command = (
            uv,
            "pip",
            "sync",
            "--dry-run",
            "--offline",
            "--python",
            python_executable,
            str(path.expanduser().resolve()),
        )
    try:
        completed = subprocess.run(
            command,
            env=env,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=60.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DependencyRestoreError("dependency restore dry run was unavailable") from exc
    if completed.returncode != 0:
        raise DependencyRestoreError("dependency restore dry run did not resolve")
    return DependencyRestoreReceipt(
        project_version=project_version,
        sdk_version=sdk_version,
        manifest_hash=hashlib.sha256(raw).hexdigest(),
        dry_run_succeeded=True,
    )


__all__ = [
    "DependencyRestoreError",
    "DependencyRestoreReceipt",
    "verify_dependency_restore_dry_run",
]
