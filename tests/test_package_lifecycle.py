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


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE_ROOT = ROOT / "src" / "hermes_claude_agent_sdk"
PARITY_SOURCE_ROOT = PACKAGE_SOURCE_ROOT / "parity"


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


IMMUTABLE_SDIST_HASHES = {
    "qa/parity-contract-v3.yaml": "e601f41313deb68b77a01402fe3b79c5da90afc7c46e40f87a6bac1850b69d8a",
    "qa/agent-sdk-boundary-ledger-v3.yaml": "22e738bebca804514cfd8311d0ff1bf1bc9da6e6a8d21cce5fb9f6aa31f1463b",
    "qa/result-packet-v3.schema.json": "dde70d2fbaa5e1cc669ff6167f89f043cc6854cf740ddff8e40c3dcb68ee1295",
    "qa/v2-to-v3-replacement-receipt.md": "d414c56daa00c83218e7f8c4cde8378390821b6a993cd64988553915b620ced7",
    "qa/parity-contract-v4.yaml": "53864834496403388f3475291475fea70acfa3105609ad49f5edf75ad1c67d94",
    "qa/agent-sdk-boundary-ledger-v4.yaml": "fa993d510876f4620e4bd0f71bd6f156dddce26466a5d833607a9e1c1d3b8cad",
    "qa/parity-v4-predecessor-map.yaml": "a82ce96126f835ca01b903de24493706573986739f6ac7a920fdbe7909b6883d",
    "qa/result-packet-v4.schema.json": "02f6141b977180256ea761ec322406137026b40c1caad63ac1c4e3c57123f6ee",
    "qa/parity-v4-manifest.json": "2e1ec826a7674bfae9adcd451074a4df4329daf17d12733ac8b1f4c9aa6eb71a",
}
REQUIRED_PARITY_TESTS = {
    "tests/parity/test_catalog.py",
    "tests/parity/test_v4_contract.py",
    "tests/parity/test_v4_runner.py",
}
RUNTIME_PACKAGE_FILES = {
    str(path.relative_to(ROOT / "src")).replace(os.sep, "/")
    for path in PACKAGE_SOURCE_ROOT.glob("*.py")
}
PARITY_SOURCE_FILES = {
    str(path.relative_to(ROOT)).replace(os.sep, "/")
    for path in PARITY_SOURCE_ROOT.glob("*.py")
}
PARITY_TEST_FILES = {
    str(path.relative_to(ROOT)).replace(os.sep, "/")
    for path in (ROOT / "tests" / "parity").glob("**/*.py")
}


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
        wheel_paths = set(archive.namelist())
        wheel_notices = {Path(name).name for name in wheel_paths}
        wheel_package_paths = {
            name
            for name in wheel_paths
            if name.startswith("hermes_claude_agent_sdk/")
        }
        entry_points_members = [
            name for name in wheel_paths if name.endswith("/entry_points.txt")
        ]
        assert len(entry_points_members) == 1
        wheel_entry_points = archive.read(entry_points_members[0]).decode(
            "utf-8"
        )
        metadata_members = [name for name in wheel_paths if name.endswith("/METADATA")]
        assert len(metadata_members) == 1
        wheel_metadata = archive.read(metadata_members[0]).decode("utf-8")
    with tarfile.open(sdist, mode="r:gz") as archive:
        sdist_archive_paths = set(archive.getnames())
        sdist_notices = {Path(name).name for name in sdist_archive_paths}
    assert required_notices <= wheel_notices
    assert required_notices <= sdist_notices
    assert wheel_package_paths == RUNTIME_PACKAGE_FILES
    assert wheel_entry_points == (
        "[console_scripts]\n"
        "hermes-claude-agent-sdk = hermes_claude_agent_sdk.__main__:main\n"
        "\n"
        "[hermes_agent.plugins]\n"
        "claude-agent-sdk = hermes_claude_agent_sdk\n"
    )
    metadata_lines = set(wheel_metadata.splitlines())
    assert "Requires-Dist: pyyaml==6.0.3" not in metadata_lines
    assert 'Requires-Dist: pyyaml==6.0.3; extra == "test"' in metadata_lines
    assert not any(
        Path(name).parts and Path(name).parts[0] == "claude_agent_sdk"
        for name in wheel_paths
    )
    assert not any(
        "claude_agent_sdk" in Path(name).parts[1:]
        for name in sdist_archive_paths
    )
    with tarfile.open(sdist, mode="r:gz") as archive:
        sdist_paths = {"/".join(Path(name).parts[1:]) for name in archive.getnames()}
        sdist_hashes = {}
        for name in archive.getnames():
            relative_name = "/".join(Path(name).parts[1:])
            if relative_name in IMMUTABLE_SDIST_HASHES:
                stream = archive.extractfile(name)
                assert stream is not None
                sdist_hashes[relative_name] = hashlib.sha256(stream.read()).hexdigest()
    assert {
        path for path in sdist_paths if path.startswith("src/hermes_claude_agent_sdk/parity/")
    } == PARITY_SOURCE_FILES
    assert {
        path
        for path in sdist_paths
        if path.startswith("tests/parity/") and path.endswith(".py")
    } == PARITY_TEST_FILES
    assert set(IMMUTABLE_SDIST_HASHES) | REQUIRED_PARITY_TESTS <= sdist_paths
    assert sdist_hashes == IMMUTABLE_SDIST_HASHES

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
assert importlib.util.find_spec("hermes_claude_agent_sdk.parity") is None
for module_name in (
    "hermes_claude_agent_sdk.parity.active_suite",
    "hermes_claude_agent_sdk.parity.native_suite",
    "hermes_claude_agent_sdk.parity.runtime_suite",
    "hermes_claude_agent_sdk.parity.native_sandbox",
):
    try:
        module_spec = importlib.util.find_spec(module_name)
    except ModuleNotFoundError:
        module_spec = None
    assert module_spec is None, module_name

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
assert "yaml" not in sys.modules
for script_name in (
    "hermes-claude-agent-sdk-parity",
    "hermes-claude-agent-sdk-parity-v4",
):
    assert not importlib.metadata.entry_points(
        group="console_scripts", name=script_name
    )
parity_executors = importlib.metadata.entry_points(
    group="hermes_claude_agent_sdk.parity_executors",
)
assert not parity_executors
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
