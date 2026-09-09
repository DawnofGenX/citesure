"""Phase 4 exit gate: real MCP client integration test (D5/D9).

Exercises the ``citesure-mcp`` server through a *real* MCP client session —
not by calling the tool functions directly.

Transport choice (documented per brief): the primary tests use the **stdio
transport**, launching ``.venv/bin/citesure-mcp`` as a subprocess via the
official SDK's ``StdioServerParameters`` + ``mcp.Client``. This is the most
robust option because it exercises exactly what an external MCP client
(Claude Desktop, etc.) does: process spawn, JSON-RPC over stdin/stdout,
tool listing, and tool calls — and it simultaneously proves the console
script entry point works end-to-end (D12). A secondary in-process test
(``mcp.Client`` pointed at the ``MCPServer`` instance directly) covers the
bad-input error paths cheaply without another subprocess.

All fixtures are local ``file://`` pages — fully offline, no NLI (the server
runs with the default fast tiers-1+2 path). Runtime is a few seconds
(subprocess spawn + SDK import); the tests are unmarked so they run in the
default suite.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES = Path(__file__).resolve().parent / "fixtures" / "pages"
MCP_SCRIPT = REPO_ROOT / ".venv" / "bin" / "citesure-mcp"


def _uri(name: str) -> str:
    """Absolute file:// URI for a fixture page."""
    return (PAGES / name).as_uri()


def _run(coro):
    """Run an async coroutine to completion (no pytest-asyncio dependency)."""
    return asyncio.run(coro)


def _report(result) -> dict:
    """Unwrap a successful CallToolResult into its report dict."""
    assert not result.is_error, f"tool returned an error result: {result.content}"
    return json.loads(result.content[0].text)


# ---------------------------------------------------------------------------
# Primary: stdio transport against the real console script
# ---------------------------------------------------------------------------


def test_stdio_verify_citations(tmp_path):
    """List tools and call verify_citations over a real stdio client session."""
    params = StdioServerParameters(
        command=str(MCP_SCRIPT),
        env={"CITECHECK_CACHE_DIR": str(tmp_path / "cache")},
    )

    async def run():
        async with Client(params, read_timeout_seconds=60) as client:
            tools = await client.list_tools()
            names = sorted(t.name for t in tools.tools)
            assert names == ["verify_citations", "verify_markdown"], names

            result = await client.call_tool(
                "verify_citations",
                {
                    "use_nli": False,
                    "citations": [
                        {
                            "claim": (
                                "Rust 1.75 was released on July 25, 2024, "
                                "introducing a new inline assembly syntax."
                            ),
                            "citation": _uri("sup4.html"),
                        },
                        {
                            "claim": (
                                "Python 3.12 was released on October 2, 2023, "
                                "bringing a new interactive debugger and faster "
                                "startup times."
                            ),
                            "citation": _uri("unsup1.html"),
                        },
                        {
                            "claim": "This source does not exist.",
                            "citation": "file:///tmp/citesure-no-such-page-xyz.html",
                        },
                    ],
                },
            )
            return names, result

    names, result = _run(run())
    report = _report(result)

    # D3 report shape.
    for key in ("total", "supported", "unsupported", "unverifiable", "pass_rate"):
        assert key in report, f"missing report key: {key}"
    assert report["total"] == 3
    assert report["supported"] == 1
    assert report["unsupported"] == 1
    assert report["unverifiable"] == 1
    assert abs(report["pass_rate"] - 1 / 3) < 1e-3

    by_id = {v["citation_id"]: v for v in report["verdicts"]}
    assert by_id["c1"]["status"] == "supported"
    assert by_id["c1"]["tier_reached"] == 2  # use_nli=False
    assert by_id["c2"]["status"] == "unsupported"
    assert by_id["c2"]["tier_reached"] == 2  # use_nli=False
    assert by_id["c3"]["status"] == "unreachable"
    assert by_id["c3"]["tier_reached"] == 1


def test_stdio_verify_markdown(tmp_path):
    """Call verify_markdown over a real stdio client session."""
    params = StdioServerParameters(
        command=str(MCP_SCRIPT),
        env={"CITECHECK_CACHE_DIR": str(tmp_path / "cache")},
    )
    markdown = (
        "The HTTP/2 protocol multiplexes multiple requests over a single TCP "
        f"connection, eliminating head-of-line blocking "
        f"[the HTTP/2 overview]({_uri('page2.html')})."
    )

    async def run():
        async with Client(params, read_timeout_seconds=60) as client:
            return await client.call_tool("verify_markdown", {"use_nli": False, "markdown": markdown})

    report = _report(_run(run()))
    assert report["total"] == 1
    assert report["supported"] == 1
    assert report["pass_rate"] == 1.0
    verdict = report["verdicts"][0]
    assert verdict["status"] == "supported"
    assert verdict["tier_reached"] == 2  # use_nli=False
    assert verdict["citation_id"] == "the HTTP/2 overview"


# ---------------------------------------------------------------------------
# Secondary: in-process client for bad-input error paths (fast, no subprocess)
# ---------------------------------------------------------------------------


def test_inprocess_bad_input_returns_error_dict():
    """Bad *content* comes back as {'error': ...}, not a crash (brief req.)."""
    from citesure.mcp_server import build_server

    server = build_server()

    async def run():
        async with Client(server) as client:
            # Missing 'citation' key → validation error surfaced as a dict.
            r1 = await client.call_tool(
                "verify_citations", {"citations": [{"claim": "a claim"}]}
            )
            # Empty markdown → no citations found.
            r2 = await client.call_tool("verify_markdown", {"markdown": "no links here"})
            # Empty list.
            r3 = await client.call_tool("verify_citations", {"citations": []})
            return r1, r2, r3

    r1, r2, r3 = _run(run())
    for r in (r1, r2, r3):
        assert not r.is_error, "expected a non-fatal error dict, got an error result"
        payload = json.loads(r.content[0].text)
        assert "error" in payload and payload["error"]
    assert "no citations" in r2.content[0].text
    assert "no citations" in r3.content[0].text
