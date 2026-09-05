"""Offline diagnostics for the Hermes Claude Agent SDK plugin.

The doctor is deliberately a thin wrapper around the public
``compatibility.doctor`` contract.  It may inspect the Hermes AgentRuntime
manifest and installed distribution metadata, but it never imports the Claude
SDK, reads credentials, starts a subprocess, or sends a query.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any


_JSON_KWARGS = {"sort_keys": True, "separators": (",", ":")}


def collect_report() -> dict[str, Any]:
    """Return a credential-free compatibility report.

    The compatibility module is imported inside this function so importing
    the CLI module itself remains inert.  The public compatibility contract
    already keeps SDK metadata separate from host compatibility and maps
    expected host failures to stable report fields.  The outer guard is a
    final safety boundary: even an unexpected implementation failure is
    represented by a fixed category rather than leaking exception text or a
    local path to command output.
    """

    try:
        from .compatibility import doctor

        report = doctor()
    except (ImportError, ModuleNotFoundError):
        return _failure_report("host_unavailable")
    except (TypeError, ValueError):
        return _failure_report("invalid_compatibility_report")
    except Exception:
        return _failure_report("diagnostic_failure")

    if not isinstance(report, dict):
        return _failure_report("invalid_compatibility_report")
    return report


def report_json(report: dict[str, Any] | None = None) -> str:
    """Serialize a report as deterministic compact JSON."""

    if report is None:
        report = collect_report()
    try:
        return json.dumps(report, **_JSON_KWARGS)
    except (TypeError, ValueError, OverflowError):
        # Keep the command's output safe and machine-readable even if a
        # caller supplies an unserializable custom report in-process.
        return json.dumps(_failure_report("invalid_compatibility_report"), **_JSON_KWARGS)


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``hermes-claude-agent-sdk doctor`` and return a shell status.

    JSON is the default output because the standalone command has one
    diagnostic surface.  ``--json`` is accepted explicitly for scripts and
    documents the intended machine-readable invocation.
    """

    parser = argparse.ArgumentParser(
        prog="hermes-claude-agent-sdk",
        description="Report offline Hermes AgentRuntime compatibility.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="doctor",
        choices=("doctor",),
        help="diagnostic command (default: doctor)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="emit deterministic compact JSON",
    )
    args = parser.parse_args(argv)

    # ``args.json_output`` is intentionally not used to select a separate
    # code path: the stable JSON form is the command's sole output format.
    del args
    report = collect_report()
    print(report_json(report))
    return 0 if report.get("status") == "compatible" and report.get("compatible") is True else 1


def _failure_report(category: str) -> dict[str, Any]:
    """Build a report containing only stable, non-sensitive fields."""

    # Keep this fallback independent from compatibility imports.  It is used
    # when the host package is absent or when report construction itself fails.
    return {
        "status": "host_unavailable" if category == "host_unavailable" else "error",
        "compatible": False,
        "runtime_id": "hermes-claude-agent-sdk",
        "plugin_version": "0.1.0rc1",
        "error": "offline compatibility report unavailable",
        "error_category": category,
    }


__all__ = ["collect_report", "main", "report_json"]
