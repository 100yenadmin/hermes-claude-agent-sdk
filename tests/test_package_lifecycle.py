"""Offline checks for the standalone distribution lifecycle.

The lifecycle test is enabled by ``scripts/verify_package_lifecycle.sh``
after it has built an sdist and wheel.  The wheel is installed into a fresh
venv with dependencies and indexes disabled so importing the plugin cannot
silently exercise the Claude SDK or a Hermes installation.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        rendered = " ".join(command)
        raise AssertionError(
            f"command failed ({result.returncode}): {rendered}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _python_probe(*, python: Path, cwd: Path, env: dict[str, str], source: str) -> None:
    _run([str(python), "-I", "-c", source], cwd=cwd, env=env)


def test_built_package_lifecycle(tmp_path: Path) -> None:
    """Build outputs install, import, uninstall, and reinstall offline."""

    wheel_value = os.environ.get("HERMES_LIFECYCLE_WHEEL")
    sdist_value = os.environ.get("HERMES_LIFECYCLE_SDIST")
    if not wheel_value or not sdist_value:
        pytest.skip(
            "set HERMES_LIFECYCLE_WHEEL and HERMES_LIFECYCLE_SDIST to run "
            "the artifact lifecycle"
        )

    wheel = Path(wheel_value).resolve()
    sdist = Path(sdist_value).resolve()
    assert wheel.is_file() and wheel.name.endswith(".whl")
    assert sdist.is_file() and sdist.name.endswith(".tar.gz")
    artifact_hashes = {wheel: _sha256(wheel), sdist: _sha256(sdist)}
    assert all(len(digest) == 64 for digest in artifact_hashes.values())
    for artifact, digest in artifact_hashes.items():
        print(f"artifact sha256 {digest}  {artifact.name}")

    required_notices = {"LICENSE", "NOTICE", "AUTHORS"}
    with zipfile.ZipFile(wheel) as archive:
        wheel_notices = {Path(name).name for name in archive.namelist()}
    with tarfile.open(sdist, mode="r:gz") as archive:
        sdist_notices = {Path(name).name for name in archive.getnames()}
    assert required_notices <= wheel_notices
    assert required_notices <= sdist_notices
    with tarfile.open(sdist, mode="r:gz") as archive:
        sdist_paths = {"/".join(Path(name).parts[1:]) for name in archive.getnames()}
    assert {
        "qa/parity-contract-v3.yaml",
        "qa/agent-sdk-boundary-ledger-v3.yaml",
        "qa/dependency-restore-manifest-v3.txt",
        "qa/result-packet-v3.schema.json",
        "qa/v2-to-v3-replacement-receipt.md",
        "tests/parity/test_catalog.py",
    } <= sdist_paths

    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes-home"
    home.mkdir()
    hermes_home.mkdir()
    shared_hermes_home = Path.home() / ".hermes"
    assert hermes_home.resolve() != shared_hermes_home.resolve()

    # Construct a deliberately small environment.  In particular, do not
    # pass through auth, proxy, or SDK configuration from the invoking shell.
    env = {
        "HOME": str(home),
        "HERMES_HOME": str(hermes_home),
        "PATH": os.environ.get("PATH", os.defpath),
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONNOUSERSITE": "1",
        "LC_ALL": "C.UTF-8",
    }
    venv_dir = tmp_path / "venv"
    _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=tmp_path, env=env)
    python_name = "python.exe" if os.name == "nt" else "python"
    python = venv_dir / ("Scripts" if os.name == "nt" else "bin") / python_name
    assert python.is_file()

    no_sdk_import_probe = """
import importlib.metadata
import importlib.util
import sys

assert importlib.util.find_spec("claude_agent_sdk") is None

class RejectSDKImport:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "claude_agent_sdk":
            raise AssertionError("package import attempted to load Claude SDK")
        return None

sys.meta_path.insert(0, RejectSDKImport())
import hermes_claude_agent_sdk as plugin
assert plugin.__version__ == "0.1.0rc1"
assert "claude_agent_sdk" not in sys.modules
assert "agent" not in sys.modules
matches = importlib.metadata.entry_points(
    group="hermes_agent.plugins", name="claude-agent-sdk"
)
assert len(matches) == 1
entry_point = next(iter(matches))
assert entry_point.value == "hermes_claude_agent_sdk"
assert entry_point.load() is plugin
parity_scripts = importlib.metadata.entry_points(
    group="console_scripts", name="hermes-claude-agent-sdk-parity"
)
assert len(parity_scripts) == 1
assert next(iter(parity_scripts)).value == "hermes_claude_agent_sdk.parity.cli:main"
parity_executors = importlib.metadata.entry_points(
    group="hermes_claude_agent_sdk.parity_executors",
    name="v3",
)
assert len(parity_executors) == 1
assert next(iter(parity_executors)).value == (
    "hermes_claude_agent_sdk.parity.executors:EXECUTORS"
)
assert "claude_agent_sdk" not in sys.modules
print("installed import passed")
"""
    import_absence_probe = """
import importlib.metadata
import importlib.util

assert importlib.util.find_spec("hermes_claude_agent_sdk") is None
try:
    importlib.metadata.version("hermes-claude-agent-sdk")
except importlib.metadata.PackageNotFoundError:
    pass
else:
    raise AssertionError("distribution metadata survived uninstall")
print("uninstall absence passed")
"""

    # --no-index and --no-deps make this a local wheel operation even when
    # the package declares the real Claude SDK as a runtime dependency.
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--no-deps",
            "--no-cache-dir",
            str(wheel),
        ],
        cwd=tmp_path,
        env=env,
    )
    _python_probe(python=python, cwd=tmp_path, env=env, source=no_sdk_import_probe)

    _run(
        [
            str(python),
            "-m",
            "pip",
            "uninstall",
            "--disable-pip-version-check",
            "--yes",
            "hermes-claude-agent-sdk",
        ],
        cwd=tmp_path,
        env=env,
    )
    _python_probe(python=python, cwd=tmp_path, env=env, source=import_absence_probe)

    assert _sha256(wheel) == artifact_hashes[wheel]
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--no-deps",
            "--no-cache-dir",
            str(wheel),
        ],
        cwd=tmp_path,
        env=env,
    )
    _python_probe(python=python, cwd=tmp_path, env=env, source=no_sdk_import_probe)
    assert _sha256(wheel) == artifact_hashes[wheel]
    print("reinstall exact wheel passed")
