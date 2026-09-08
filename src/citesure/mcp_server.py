"""citesure-mcp — MCP server entry point (STUB).

Phase 4 builds the real FastMCP/MCPServer exposing the ``verify_citations`` and
``verify_markdown`` tools on top of the library API in :mod:`citesure`. This
module exists now so the ``citesure-mcp`` console script resolves (D5/D12) and
so the package installs cleanly; it deliberately does **not** pull in the MCP
SDK at import time, keeping the bulk of the codebase testable without any MCP
plumbing.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Placeholder entry point.

    The real server is implemented in Phase 4. For now this prints a short
    notice and exits non-zero so callers know the tool is not yet available.
    """
    print(
        "citesure-mcp: the MCP server is not implemented yet (Phase 4).\n"
        "Use the `citesure` CLI or the Python library API instead.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
