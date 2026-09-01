"""Command line interface for parity-v3 inventory, execution, and grading."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from .catalog import CatalogViolation, load_catalog
from .grader import grade_packets
from .inventory import InventoryViolation, capture_tool_inventory, load_tool_inventory
from .profile import ProfileViolation, load_profile_manifest
from .results import ResultViolation, candidate_hash, read_result_packet
from .runner import load_entrypoint_executors, run_catalog, validate_run_manifest


RUNNER_VERSION = "3.0.0"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-claude-agent-sdk-parity",
        description="Trace-graded Hermes Agent SDK parity-v3 tooling",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("inventory", "run", "grade"):
        command = subparsers.add_parser(name)
        command.add_argument("--catalog", required=True)
        command.add_argument("--lane", choices=("rc", "runtime"), default="rc")
        command.add_argument("--profile")
        command.add_argument("--plugin-sha")
        command.add_argument("--host-sha")
        command.add_argument("--output")
        command.add_argument("--resume", action="store_true")
        command.add_argument("--tool-inventory")
        command.add_argument("--profile-manifest")
        command.add_argument("--sdk-version", default="0.2.144")
        command.add_argument("--runner-version", default=RUNNER_VERSION)
        command.add_argument("--capability-id", action="append", default=[])
        if name == "inventory":
            command.add_argument(
                "--capture",
                action="store_true",
                help="capture the repo-owned isolated tool surface through HostToolBridge",
            )
    return parser


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _require_run_fields(args: argparse.Namespace) -> None:
    missing = [
        name
        for name in (
            "profile",
            "profile_manifest",
            "plugin_sha",
            "host_sha",
            "output",
            "tool_inventory",
        )
        if not getattr(args, name)
    ]
    if missing:
        raise ResultViolation(f"{args.command} requires options: {', '.join('--' + name.replace('_', '-') for name in missing)}")


def _validate_profile(catalog, profile_id: str) -> None:
    allowed = set(catalog.contract["profile_policy"]["allowed_ids"])
    if profile_id not in allowed:
        raise ResultViolation("requested profile is outside the isolated v3 profile policy")


def _inventory(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.catalog)
    lane_capabilities = catalog.for_lane(args.lane)
    report: dict[str, Any] = {
        "schema_version": 1,
        "contract_name": catalog.contract["name"],
        "contract_version": catalog.version,
        "contract_hash": catalog.contract_hash,
        "catalog_hash": catalog.catalog_hash,
        "catalog_file_hash": catalog.file_hash,
        "lane": args.lane,
        "lane_capability_count": len(lane_capabilities),
        "source_counts": dict(catalog.source_counts),
        "registered_execution_ids": list(load_entrypoint_executors().execution_ids),
        "tool_inventory_status": "PENDING",
        "tool_inventory_hash": None,
        "tool_count": None,
        "proof_boundary": "Source coverage and inventory only; no scenario is executed or passed.",
    }
    exit_code = 75
    if args.capture:
        if not args.profile or not args.profile_manifest or not args.output:
            raise ResultViolation(
                "inventory --capture requires --profile, --profile-manifest, and --output"
            )
        _validate_profile(catalog, args.profile)
        document = capture_tool_inventory(
            args.profile_manifest,
            expected_profile=args.profile,
        )
        output = Path(args.output).expanduser().resolve()
        target = output if output.suffix in {".yaml", ".yml"} else output / "tool-inventory.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(
            yaml.safe_dump(document, sort_keys=False),
            encoding="utf-8",
        )
        temporary.replace(target)
        args.tool_inventory = str(target)
    if args.tool_inventory:
        if not args.profile or not args.profile_manifest:
            raise ResultViolation(
                "inventory with --tool-inventory also requires --profile and --profile-manifest"
            )
        _validate_profile(catalog, args.profile)
        profile = load_profile_manifest(args.profile_manifest, expected_profile=args.profile)
        inventory = load_tool_inventory(
            args.tool_inventory,
            expected_profile=args.profile,
            expected_profile_hash=profile.manifest_hash,
        )
        report["tool_inventory_status"] = "COMPLETE"
        report["tool_inventory_hash"] = inventory.inventory_hash
        report["profile_hash"] = inventory.profile_hash
        report["tool_count"] = inventory.tool_count
        exit_code = 0
    if args.output:
        output = Path(args.output).expanduser().resolve()
        if output.suffix == ".json":
            target = output
        elif args.capture and output.suffix in {".yaml", ".yml"}:
            target = output.with_name(f"inventory-{args.lane}.json")
        else:
            target = output / f"inventory-{args.lane}.json"
        _write_json(target, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code


def _run(args: argparse.Namespace) -> int:
    _require_run_fields(args)
    catalog = load_catalog(args.catalog)
    _validate_profile(catalog, args.profile)
    profile = load_profile_manifest(args.profile_manifest, expected_profile=args.profile)
    inventory = load_tool_inventory(
        args.tool_inventory,
        expected_profile=args.profile,
        expected_profile_hash=profile.manifest_hash,
    )
    _, report = run_catalog(
        catalog,
        lane=args.lane,
        profile_id=args.profile,
        profile_hash=inventory.profile_hash,
        plugin_sha=args.plugin_sha,
        host_sha=args.host_sha,
        sdk_version=args.sdk_version,
        runner_version=args.runner_version,
        inventory_hash=inventory.inventory_hash,
        output=args.output,
        resume=args.resume,
        registry=load_entrypoint_executors(),
        inventory_tools=inventory.observed_tools,
        capability_ids=tuple(args.capability_id),
        profile_isolation_kind=profile.isolation_kind,
        profile_persistent=profile.persistent,
    )
    exact_candidate = candidate_hash(
        catalog_hash=catalog.catalog_hash,
        plugin_sha=args.plugin_sha,
        host_sha=args.host_sha,
        sdk_version=args.sdk_version,
        profile_hash=inventory.profile_hash,
        runner_version=args.runner_version,
        inventory_hash=inventory.inventory_hash,
    )
    manifest_summary = validate_run_manifest(
        args.output,
        lane=args.lane,
        catalog=catalog,
        exact_candidate=exact_candidate,
    )
    payload = report.to_dict()
    payload["run_manifest"] = manifest_summary
    _write_json(Path(args.output).expanduser().resolve() / f"grade-{args.lane}.json", payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return report.exit_code


def _grade(args: argparse.Namespace) -> int:
    _require_run_fields(args)
    catalog = load_catalog(args.catalog)
    _validate_profile(catalog, args.profile)
    profile = load_profile_manifest(args.profile_manifest, expected_profile=args.profile)
    inventory = load_tool_inventory(
        args.tool_inventory,
        expected_profile=args.profile,
        expected_profile_hash=profile.manifest_hash,
    )
    output = Path(args.output).expanduser().resolve()
    if not output.is_dir():
        raise ResultViolation("grade output must be an existing result directory")
    packets = [read_result_packet(path) for path in sorted(output.glob("*__*__trial-*.json"))]
    exact_candidate = candidate_hash(
        catalog_hash=catalog.catalog_hash,
        plugin_sha=args.plugin_sha,
        host_sha=args.host_sha,
        sdk_version=args.sdk_version,
        profile_hash=inventory.profile_hash,
        runner_version=args.runner_version,
        inventory_hash=inventory.inventory_hash,
    )
    manifest_summary = validate_run_manifest(
        output,
        lane=args.lane,
        catalog=catalog,
        exact_candidate=exact_candidate,
    )
    report = grade_packets(
        catalog,
        packets,
        lane=args.lane,
        expected_candidate_hash=exact_candidate,
    )
    payload = report.to_dict()
    payload["run_manifest"] = manifest_summary
    _write_json(output / f"grade-{args.lane}.json", payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inventory":
            return _inventory(args)
        if args.command == "run":
            return _run(args)
        return _grade(args)
    except (CatalogViolation, InventoryViolation, ProfileViolation, ResultViolation) as exc:
        print(f"contract violation: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
