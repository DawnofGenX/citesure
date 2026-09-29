"""Phase C4: MCP stress tests — real ``citesure-mcp`` subprocess, offline.

Covers (per brief):
* 500-citation payload through ``verify_citations`` (must complete, correct
  report shape, bounded runtime);
* malformed JSON-RPC on stdin (garbage line, non-RPC JSON, truncated JSON,
  unknown method) — the server must survive every one of them;
* zombie/exit hygiene: EOF on stdin → clean exit; SIGTERM → exit; no
  lingering ``citesure-mcp`` processes after a completed SDK session.

All fixtures are local ``file://`` pages — fully offline.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES = Path(__file__).resolve().parent / "fixtures" / "pages"


def _mcp_command() -> str:
    """Portable path to the citesure-mcp server entry point.

    See the identical helper in test_mcp_integration.py: the repo-local .venv
    path does not exist under `pip install -e ".[dev]"` in CI, where the console
    script is on the runner's PATH instead. Returns a single executable path,
    because the MCP stdio transport passes it as the command with no arguments.
    """
    found = shutil.which("citesure-mcp")
    if found:
        return found
    for local in (
        REPO_ROOT / ".venv" / "bin" / "citesure-mcp",
        REPO_ROOT / ".venv" / "Scripts" / "citesure-mcp.exe",
    ):
        if local.exists():
            return str(local)
    raise RuntimeError(
        "citesure-mcp not found on PATH and no repo-local .venv entry point; "
        "install the package (pip install -e '.[dev]') before running the MCP tests"
    )


MCP_SCRIPT = _mcp_command()


def _uri(name: str) -> str:
    return (PAGES / name).as_uri()


def _run(coro):
    """Run an async coroutine to completion (no pytest-asyncio dependency)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1) 500-citation payload
# ---------------------------------------------------------------------------


def test_mcp_500_citation_payload(tmp_path):
    """A 500-citation batch must complete with a well-formed report."""
    params = StdioServerParameters(
        command=str(MCP_SCRIPT),
        env={"CITECHECK_CACHE_DIR": str(tmp_path / "cache")},
    )
    pages = ["sup4.html", "unsup1.html", "page1.html", "page2.html"]
    citations = [
        {
            "claim": f"Claim number {i} about Rust release dates.",
            "citation": _uri(pages[i % len(pages)]),
        }
        for i in range(500)
    ]

    async def run():
        async with Client(params, read_timeout_seconds=300) as client:
            t0 = time.time()
            result = await client.call_tool(
                "verify_citations", {"use_nli": False, "citations": citations}
            )
            elapsed = time.time() - t0
        return result, elapsed

    result, elapsed = _run(run())
    assert not result.is_error, f"tool errored: {result.content}"
    report = json.loads(result.content[0].text)
    assert report["total"] == 500
    assert len(report["verdicts"]) == 500
    # Every verdict must carry the D3 fields.
    for v in report["verdicts"]:
        for key in ("citation_id", "url", "status", "tier_reached"):
            assert key in v, f"missing verdict key {key}: {v}"
        assert v["status"] in (
            "supported", "unsupported", "unreachable", "paywalled", "ambiguous"
        )
    # Counts must be internally consistent.
    assert report["supported"] + report["unsupported"] + report["unverifiable"] == 500
    # Bounded runtime: 500 local-file citations must not take minutes.
    assert elapsed < 120, f"500-citation run took {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# 2) Malformed JSON-RPC on stdin — server must survive
# ---------------------------------------------------------------------------


def _spawn_server(cache_dir: str) -> subprocess.Popen:
    return subprocess.Popen(
        [str(MCP_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "CITECHECK_CACHE_DIR": cache_dir},
    )


@pytest.mark.parametrize(
    "label,payload",
    [
        ("garbage-line", "this is not json at all\n"),
        ("non-rpc-json", '{"foo": "bar"}\n'),
        (
            "truncated-json",
            '{"jsonrpc":"2.0","id":1,"method":"tools/call",'
            '"params":{"name":"verify_citations","arguments":{"citations":[',
        ),
        (
            "unknown-method",
            json.dumps(
                {"jsonrpc": "2.0", "id": 9, "method": "nonexistent/method", "params": {}}
            )
            + "\n",
        ),
    ],
)
def test_mcp_survives_malformed_jsonrpc(tmp_path, label, payload):
    """Any malformed input on stdin must not crash or hang the server."""
    proc = _spawn_server(str(tmp_path / "cache"))
    try:
        proc.stdin.write(payload)
        proc.stdin.flush()
        time.sleep(2)
        assert proc.poll() is None, (
            f"server died after {label}: rc={proc.poll()}"
        )
    finally:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


# ---------------------------------------------------------------------------
# 3) Zombie / exit hygiene
# ---------------------------------------------------------------------------


def test_mcp_eof_on_stdin_exits_cleanly(tmp_path):
    """Closing stdin (client disconnect) must end the server promptly."""
    proc = _spawn_server(str(tmp_path / "cache"))
    try:
        time.sleep(1)  # let the server start
        proc.stdin.close()  # EOF
        t0 = time.time()
        rc = proc.wait(timeout=10)
        assert rc == 0, f"expected clean exit 0 on EOF, got {rc}"
        assert time.time() - t0 < 10
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_mcp_sigterm_exits(tmp_path):
    """SIGTERM must terminate the server (no hang)."""
    proc = _spawn_server(str(tmp_path / "cache"))
    try:
        time.sleep(1)
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=10)
        # Killed by SIGTERM → negative return code on POSIX; the point is
        # that it *exits* rather than hangs.
        assert rc != 0 or rc is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_mcp_no_lingering_processes_after_session(tmp_path):
    """After a completed SDK session, no citesure-mcp process may linger."""
    params = StdioServerParameters(
        command=str(MCP_SCRIPT),
        env={"CITECHECK_CACHE_DIR": str(tmp_path / "cache")},
    )

    async def run():
        async with Client(params, read_timeout_seconds=60) as client:
            return await client.call_tool(
                "verify_markdown", {"markdown": f"[x]({_uri('page1.html')})"}
            )

    result = _run(run())
    assert not result.is_error
    time.sleep(1)
    ps = subprocess.run(
        ["ps", "-eo", "pid,stat,comm"], capture_output=True, text=True
    ).stdout
    lingering = [l for l in ps.splitlines() if "citesure-mcp" in l]
    assert not lingering, f"lingering citesure-mcp processes: {lingering}"
