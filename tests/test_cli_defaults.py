"""Task 1 (IMP-4) tests: safe-by-default NLI in CLI and MCP.

- Default CLI run reaches tier 3 (NLI on).
- `--no-nli` prints a warning naming the risk.
- MCP tools accept per-call `use_nli` (default True); server-wide
  CITECHECK_NLI=0 still opts out.
"""
from __future__ import annotations

import asyncio
import io
import sys
from contextlib import redirect_stderr
from unittest.mock import patch

import pytest

from citesure.cli import _build_parser, _cmd_verify
from citesure.mcp_server import build_server, _nli_default


# ---------------------------------------------------------------------------
# CLI defaults
# ---------------------------------------------------------------------------

def test_cli_default_is_nli_on():
    """Plain `citesure verify file.md` → use_nli is True."""
    parser = _build_parser()
    args = parser.parse_args(["verify", "tests/fixtures/sample.md"])
    assert args.nli is True, "default should be NLI on"


def test_cli_no_nli_flag():
    """`--no-nli` flips the default off (after `verify` subcommand)."""
    parser = _build_parser()
    args = parser.parse_args(["verify", "--no-nli", "tests/fixtures/sample.md"])
    assert args.nli is False


def test_cli_nli_model_implies_nli():
    """`--nli-model` still implies NLI (back-compat)."""
    parser = _build_parser()
    args = parser.parse_args(["verify", "--nli-model", "cross-encoder/nli-deberta-v3-base",
                              "tests/fixtures/sample.md"])
    assert args.nli is True


def test_cli_no_nli_warning(capsys):
    """`--no-nli` prints a stderr warning naming negation/entity-swap risk."""
    parser = _build_parser()
    args = parser.parse_args(["verify", "--no-nli", "tests/fixtures/sample.md"])
    stderr_buf = io.StringIO()
    with redirect_stderr(stderr_buf):
        try:
            _cmd_verify(args)
        except Exception:
            pass  # we only care about the warning
    err = stderr_buf.getvalue()
    assert "WARNING" in err
    assert "--no-nli" in err
    assert "negation" in err or "entity-swap" in err


# ---------------------------------------------------------------------------
# MCP defaults
# ---------------------------------------------------------------------------

def test_mcp_nli_default_true():
    """_nli_default() returns True unless CITECHECK_NLI is falsy."""
    with patch.dict("os.environ", {}, clear=True):
        assert _nli_default() is True


def test_mcp_nli_default_env_override():
    """CITECHECK_NLI=0 opts the server-wide default out."""
    with patch.dict("os.environ", {"CITECHECK_NLI": "0"}, clear=True):
        assert _nli_default() is False
    with patch.dict("os.environ", {"CITECHECK_NLI": "false"}, clear=True):
        assert _nli_default() is False


def test_mcp_tool_signatures_accept_use_nli():
    """Both MCP tools expose a `use_nli` parameter in their schema."""
    from mcp.client.client import Client

    server = build_server()

    async def run():
        async with Client(server) as client:
            result = await client.list_tools()
            return result.tools

    tools = asyncio.run(run())
    by_name = {t.name: t for t in tools}
    assert "verify_citations" in by_name, "verify_citations tool missing"
    assert "verify_markdown" in by_name, "verify_markdown tool missing"
    for name in ("verify_citations", "verify_markdown"):
        schema = by_name[name].input_schema
        assert "use_nli" in schema["properties"], f"{name} missing use_nli param"
