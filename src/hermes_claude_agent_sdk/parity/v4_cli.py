"""Transport-free CLI for binding v3 evidence into v4 packets and grading it.

The command consumes only already-recorded, sanitized JSON packets and
ownership receipts.  It deliberately has no import path to the v3 runner,
provider clients, credentials, or the Claude SDK.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .results import ResultViolation, read_result_packet
from .v4_contract import V4ContractViolation, load_v4_contract
from .v4_evidence import V4EvidenceViolation, bind_v4_evidence
from .v4_runner import V4ResultViolation, grade_result_packets


class V4CLIError(ValueError):
    """Input or output layout cannot be handled safely."""


_RAW_KEYS = frozenset(
    {
        "raw_prompt",
        "raw_content",
        "raw_transcript",
        "messages",
        "session_id",
        "credential",
        "credentials",
        "access_token",
        "refresh_token",
        "api_key",
    }
)
_MAX_STRUCTURED_BYTES = 8 * 1024 * 1024
_RECEIPT_SUFFIXES = ("", ".receipt", ".ownership")


def _unique_object(pairs: list[tuple[Any, Any]]) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key, value in pairs:
        if key in result:
            raise V4CLIError("structured input contains duplicate fields")
        result[key] = value
    return result


def _reject_raw(value: Any, location: str = "input") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _RAW_KEYS:
                raise V4CLIError(f"{location} contains forbidden raw-data fields")
            _reject_raw(child, f"{location}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_raw(child, f"{location}[{index}]")


def _reject_symlinked_components(path: Path, field: str) -> None:
    """Reject a direct symlink or any existing lexical symlink ancestor."""

    lexical = path if path.is_absolute() else Path.cwd() / path
    current = Path(lexical.anchor)
    try:
        for component in lexical.parts[1:]:
            current /= component
            if current.is_symlink():
                raise V4CLIError(f"{field} must not contain symlinked path components")
    except OSError as exc:
        raise V4CLIError(f"{field} path components cannot be inspected safely") from exc


def _regular_file(path: Path, field: str) -> Path:
    _reject_symlinked_components(path, field)
    try:
        bounded = path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_STRUCTURED_BYTES
    except OSError as exc:
        raise V4CLIError(f"{field} must be a bounded regular file") from exc
    if bounded:
        raise V4CLIError(f"{field} must be a bounded regular file")
    return path


def _json_files(directory: Path, field: str) -> list[Path]:
    _reject_symlinked_components(directory, field)
    if directory.is_symlink() or not directory.is_dir():
        raise V4CLIError(f"{field} must be an existing directory")
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise V4CLIError(f"{field} cannot be read safely") from exc
    if not entries:
        raise V4CLIError(f"{field} contains no inputs")
    if any(item.is_symlink() or not item.is_file() or item.suffix.casefold() != ".json" for item in entries):
        raise V4CLIError(f"{field} contains an extra or unsupported input")
    return [_regular_file(item, field) for item in entries]


def _read_structured(path: Path, field: str) -> Any:
    source = _regular_file(path, field)
    try:
        text = source.read_text(encoding="utf-8")
        value = json.loads(text, object_pairs_hook=_unique_object) if source.suffix.casefold() == ".json" else yaml.safe_load(text)
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise V4CLIError(f"{field} cannot be parsed safely") from exc
    _reject_raw(value, field)
    return value


def _safe_manifest_name(value: Any) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value or value in {".", ".."}:
        raise V4CLIError("ownership receipt manifest names must be plain packet filenames")
    return value


def _receipt_file(value: Any, manifest_dir: Path) -> dict[str, Any]:
    if isinstance(value, Mapping):
        _reject_raw(value, "ownership receipt")
        return dict(value)
    if not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts:
        raise V4CLIError("ownership receipt reference escapes its manifest directory")
    loaded = _read_structured(manifest_dir / value, "ownership receipt")
    if not isinstance(loaded, Mapping):
        raise V4CLIError("ownership receipt must be a mapping")
    return dict(loaded)


def _manifest_entries(document: Any, manifest_dir: Path) -> dict[str, dict[str, Any]]:
    if isinstance(document, Mapping) and "receipts" in document:
        allowed = {"schema_version", "receipts"}
        if set(document) - allowed or document.get("schema_version", 1) != 1:
            raise V4CLIError("ownership receipt manifest fields are not closed")
        document = document["receipts"]
    result: dict[str, dict[str, Any]] = {}
    if isinstance(document, Mapping):
        for packet_name, receipt in document.items():
            name = _safe_manifest_name(packet_name)
            if name in result:
                raise V4CLIError("ownership receipt manifest contains duplicate packet names")
            result[name] = _receipt_file(receipt, manifest_dir)
        if not result:
            raise V4CLIError("ownership receipt manifest contains no receipts")
        return result
    if isinstance(document, Sequence) and not isinstance(document, (str, bytes, bytearray)):
        for index, item in enumerate(document):
            if not isinstance(item, Mapping) or set(item) != {"packet", "receipt"}:
                raise V4CLIError(f"ownership receipt manifest entry {index} is malformed")
            name = _safe_manifest_name(item["packet"])
            if name in result:
                raise V4CLIError("ownership receipt manifest contains duplicate packet names")
            result[name] = _receipt_file(item["receipt"], manifest_dir)
        if not result:
            raise V4CLIError("ownership receipt manifest contains no receipts")
        return result
    raise V4CLIError("ownership receipt manifest must be a mapping or list")


def _load_receipts(path: Path, packet_paths: Sequence[Path]) -> dict[str, dict[str, Any]]:
    if path.is_dir():
        files = _json_files(path, "ownership receipt directory")
        by_name = {item.name: item for item in files}
        selected: dict[str, dict[str, Any]] = {}
        used: set[Path] = set()
        for packet in packet_paths:
            candidates = [path / f"{packet.stem}{suffix}.json" for suffix in _RECEIPT_SUFFIXES]
            matches = [by_name[candidate.name] for candidate in candidates if candidate.name in by_name]
            if len(matches) != 1:
                kind = "missing" if not matches else "duplicate"
                raise V4CLIError(f"{kind} ownership receipt for packet {packet.name}")
            receipt_path = matches[0]
            used.add(receipt_path)
            loaded = _read_structured(receipt_path, "ownership receipt")
            if not isinstance(loaded, Mapping):
                raise V4CLIError("ownership receipt must be a mapping")
            selected[packet.name] = dict(loaded)
        if used != set(files):
            raise V4CLIError("ownership receipt directory contains an extra receipt")
        return selected
    if not path.is_file():
        raise V4CLIError("ownership receipts must be a directory or manifest file")
    return _manifest_entries(_read_structured(path, "ownership receipt manifest"), path.parent)


def _load_packets(path: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    packet_paths = _json_files(path, "v3 packet directory")
    packets: list[dict[str, Any]] = []
    for packet_path in packet_paths:
        try:
            _read_structured(packet_path, "v3 packet")
            packets.append(read_result_packet(packet_path).to_dict())
        except ResultViolation as exc:
            raise V4CLIError(f"invalid v3 packet {packet_path.name}") from exc
    return packet_paths, packets


def _paths_overlap(output: Path, inputs: Sequence[Path]) -> bool:
    for item in inputs:
        try:
            output.relative_to(item)
            return True
        except ValueError:
            pass
        try:
            item.relative_to(output)
            return True
        except ValueError:
            pass
    return False


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _persist(output: Path, documents: Mapping[str, Mapping[str, Any]]) -> None:
    if output.exists() and output.is_symlink():
        raise V4CLIError("output must not be a symlink")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=f".{output.name}.", dir=str(output.parent)) as staging_name:
            staging = Path(staging_name)
            for name, document in sorted(documents.items()):
                _write_json(staging / name, document)
            output.mkdir(exist_ok=True)
            if not output.is_dir():
                raise V4CLIError("output must be a directory")
            for name in sorted(documents):
                target = output / name
                staged = staging / name
                if target.exists() and (
                    target.is_symlink()
                    or not target.is_file()
                    or target.read_bytes() != staged.read_bytes()
                ):
                    raise V4CLIError(f"refusing to replace pre-existing output {name}")
            for name in sorted(documents):
                target = output / name
                staged = staging / name
                if target.exists():
                    continue
                os.replace(staged, target)
    except OSError as exc:
        raise V4CLIError("output could not be persisted atomically") from exc


def bind_and_grade(*, contract_path: str | Path, v3_packets: str | Path, ownership_receipts: str | Path, output: str | Path, lane: str = "rc") -> tuple[dict[str, Any], int]:
    """Bind all supplied v3 packets, then grade the resulting v4 packets."""

    contract_input = Path(contract_path).expanduser()
    packets_input = Path(v3_packets).expanduser()
    receipts_input = Path(ownership_receipts).expanduser()
    output_input = Path(output).expanduser()
    for path, field in (
        (contract_input, "contract"),
        (packets_input, "v3 packet directory"),
        (receipts_input, "ownership receipts"),
        (output_input, "output"),
    ):
        _reject_symlinked_components(path, field)
    contract_source = contract_input.resolve()
    packets_source = packets_input.resolve()
    receipts_source = receipts_input.resolve()
    output_path = output_input.resolve()
    if lane not in {"rc", "runtime"}:
        raise V4CLIError("grade lane must be rc or runtime")
    if _paths_overlap(output_path, (packets_source, receipts_source)):
        raise V4CLIError("output must be separate from input artifacts")
    try:
        contract = load_v4_contract(contract_source)
    except V4ContractViolation:
        raise
    packet_paths, trials = _load_packets(packets_source)
    receipts = _load_receipts(receipts_source, packet_paths)
    if set(receipts) != {item.name for item in packet_paths}:
        raise V4CLIError("ownership receipts do not match the packet set exactly")
    bound = []
    for packet_path, trial in zip(packet_paths, trials):
        try:
            bound.append(bind_v4_evidence(contract, trial, receipts[packet_path.name]))
        except V4EvidenceViolation as exc:
            raise V4CLIError(f"cannot bind v3 packet {packet_path.name}") from exc
    try:
        report = grade_result_packets(bound, contract=contract, lane=lane)
    except V4ResultViolation as exc:
        raise V4CLIError("v4 packet grading failed closed") from exc
    report_name = f"grade-{lane}.json"
    if any(path.name == report_name for path in packet_paths):
        raise V4CLIError(f"v3 packet filename collides with {report_name}")
    documents = {path.name: packet for path, packet in zip(packet_paths, bound)}
    documents[report_name] = report
    _persist(output_path, documents)
    return report, int(report["exit_code"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-claude-agent-sdk-parity-v4",
        description="Bind recorded v3 evidence into v4 packets and grade it without execution.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("bind-grade", aliases=("bind_grade",))
    command.add_argument("--contract", "--v4-contract", dest="contract", required=True)
    command.add_argument("--v3-packets", "--v3-packet-dir", "--v3-packet-directory", dest="v3_packets", required=True)
    command.add_argument(
        "--ownership-receipts",
        "--ownership-receipt-dir",
        "--ownership-receipt-directory",
        "--receipts",
        dest="ownership_receipts",
        required=True,
    )
    command.add_argument("--output", "--output-dir", dest="output", required=True)
    command.add_argument("--lane", choices=("rc", "runtime"), default="rc")
    return parser


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if effective_argv and effective_argv[0].startswith("-"):
        effective_argv.insert(0, "bind-grade")
    args = _parser().parse_args(effective_argv)
    try:
        _, exit_code = bind_and_grade(
            contract_path=args.contract,
            v3_packets=args.v3_packets,
            ownership_receipts=args.ownership_receipts,
            output=args.output,
            lane=args.lane,
        )
        print(json.dumps(json.loads((Path(args.output).expanduser().resolve() / f"grade-{args.lane}.json").read_text(encoding="utf-8")), ensure_ascii=False, sort_keys=True))
        return exit_code
    except (V4CLIError, V4ContractViolation) as exc:
        print(f"contract violation: {exc}", file=sys.stderr)
        return 2


__all__ = ["V4CLIError", "bind_and_grade", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
