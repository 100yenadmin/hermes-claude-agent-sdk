from __future__ import annotations

import argparse
from importlib import metadata
from types import SimpleNamespace

import pytest

from hermes_claude_agent_sdk.parity import cli


def _run_args(tmp_path) -> argparse.Namespace:
    return cli._parser().parse_args(
        [
            "run",
            "--catalog",
            str(tmp_path / "catalog.yaml"),
            "--profile",
            "fable-v3-isolated",
            "--profile-manifest",
            str(tmp_path / "profile.yaml"),
            "--plugin-sha",
            "1" * 40,
            "--host-sha",
            "2" * 40,
            "--output",
            str(tmp_path / "results"),
            "--tool-inventory",
            str(tmp_path / "inventory.yaml"),
        ]
    )


def test_run_uses_exact_installed_sdk_when_cli_argument_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    args = _run_args(tmp_path)
    assert args.sdk_version is None
    monkeypatch.setattr(metadata, "version", lambda _: "0.2.151")

    catalog = SimpleNamespace(
        catalog_hash="3" * 64,
        contract={"profile_policy": {"allowed_ids": ["fable-v3-isolated"]}},
    )
    profile = SimpleNamespace(
        manifest_hash="4" * 64,
        isolation_kind="temporary_home",
        persistent=False,
    )
    inventory = SimpleNamespace(
        profile_hash="4" * 64,
        inventory_hash="5" * 64,
        observed_tools=(),
    )
    report = SimpleNamespace(to_dict=lambda: {"status": "PENDING"}, exit_code=75)
    observed: dict[str, str] = {}

    monkeypatch.setattr(cli, "load_catalog", lambda _: catalog)
    monkeypatch.setattr(cli, "validate_source_authority", lambda _: None)
    monkeypatch.setattr(cli, "load_profile_manifest", lambda *_, **__: profile)
    monkeypatch.setattr(cli, "load_tool_inventory", lambda *_, **__: inventory)
    monkeypatch.setattr(cli, "load_entrypoint_executors", lambda: object())
    monkeypatch.setattr(cli, "_write_json", lambda *_: None)
    monkeypatch.setattr(cli, "validate_run_manifest", lambda *_, **__: {})

    def run_catalog(*_, sdk_version: str, **__) -> tuple[tuple[()], object]:
        observed["sdk_version"] = sdk_version
        return (), report

    monkeypatch.setattr(cli, "run_catalog", run_catalog)

    assert cli._run(args) == 75
    assert observed == {"sdk_version": "0.2.151"}


@pytest.mark.parametrize("command", ["run", "grade"])
def test_cli_rejects_explicit_sdk_mismatch_before_creating_results(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(metadata, "version", lambda _: "0.2.151")
    output = tmp_path / "results"
    argv = [
        command,
        "--catalog",
        str(tmp_path / "missing-catalog.yaml"),
        "--profile",
        "fable-v3-isolated",
        "--profile-manifest",
        str(tmp_path / "missing-profile.yaml"),
        "--plugin-sha",
        "1" * 40,
        "--host-sha",
        "2" * 40,
        "--output",
        str(output),
        "--tool-inventory",
        str(tmp_path / "missing-inventory.yaml"),
        "--sdk-version",
        "0.2.144",
    ]

    assert cli.main(argv) == 2
    assert not output.exists()
    assert "sdk_version_mismatch" in capsys.readouterr().err
