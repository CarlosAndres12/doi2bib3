"""Entry point for the ``doi2bib3-mcp`` console script.

Starts the MCP stdio server. Accepts an optional ``--cache-db <path>`` flag to
override the reference cache location (default: XDG cache dir). If the optional
``[mcp]`` extra is not installed, prints an actionable error and exits non-zero.

Usage:
    doi2bib3-mcp [--cache-db /path/to/references.db]
    python -m doi2bib3.mcp_server --cache-db /custom/path.db
"""

from __future__ import annotations

import sys


def _parse_cache_db(args: list[str]) -> str | None:
    """Extract --cache-db <path> from argv; return None if not present."""
    for i, arg in enumerate(args):
        if arg == "--cache-db" and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--cache-db="):
            return arg.split("=", 1)[1]
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    # Probe for the optional mcp SDK up front so a missing [mcp] extra produces
    # an actionable message instead of an import traceback deep in the server.
    try:
        import mcp  # noqa: F401
    except ImportError as exc:
        sys.stderr.write(
            "doi2bib3-mcp requires the 'mcp' extra. Install it with:\n"
            "    pip install doi2bib3[mcp]\n"
            f"(missing dependency: {exc.name})\n"
        )
        return 1

    # Configure the reference cache (--cache-db flag or env var).
    cache_db = _parse_cache_db(argv)
    from . import cache
    cache.configure(db_path=cache_db)

    from .server import main as server_main
    return server_main()


if __name__ == "__main__":
    raise SystemExit(main())