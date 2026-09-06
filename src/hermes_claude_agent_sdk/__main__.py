"""Command-line entry point for the standalone Hermes plugin doctor."""

from __future__ import annotations

from collections.abc import Sequence

from .diagnostics import main as _main


def main(argv: Sequence[str] | None = None) -> int:
    """Delegate to the offline diagnostics command."""

    return _main(argv)


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests
    raise SystemExit(main())


__all__ = ["main"]
