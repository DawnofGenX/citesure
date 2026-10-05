"""NLI default behaviour in the CLI and MCP server.

Behaviour CHANGED on 2026-10-04 (see the D1/D3 audit):

- Neither `--nli` nor `--no-nli` -> `args.nli is None`, meaning AUTO-DETECT:
  the NLI tier runs when the optional `[nli]` extra is importable and stays off
  when it is not. It used to default to `True`, which made the documented
  first command (`pip install citesure` + `citesure verify notes.md`) exit 2 on
  a default install, because torch/transformers live in the extra.
- `--nli` forces on, `--no-nli` forces off, `--nli-model` still implies on.
- MCP `_nli_default()` auto-detects the same way, and CITECHECK_NLI=0 still
  opts out explicitly.
- MCP tools accept per-call `use_nli`; the result declares `nli_active` so an
  overlap-only run can never be mistaken for an entailment-checked one.
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

def test_cli_default_is_auto_detect():
    """Neither flag -> None, i.e. AUTO-DETECT (not a hard-coded on/off)."""
    parser = _build_parser()
    args = parser.parse_args(["verify", "tests/fixtures/sample.md"])
    assert args.nli is None, "default should be auto-detect, not on or off"


def test_cli_explicit_nli_flag_forces_on():
    """`--nli` explicitly requests the tier even if the extra is missing.

    get_nli_model() then fails fast with the actionable install message, which
    is the documented D6 behaviour - never a silent downgrade.
    """
    parser = _build_parser()
    args = parser.parse_args(["verify", "--nli", "tests/fixtures/sample.md"])
    assert args.nli is True


def test_cli_no_nli_flag():
    """`--no-nli` flips the default off (after `verify` subcommand)."""
    parser = _build_parser()
    args = parser.parse_args(["verify", "--no-nli", "tests/fixtures/sample.md"])
    assert args.nli is False


def test_cli_nli_model_leaves_flag_unset_for_resolution():
    """`--nli-model` keeps args.nli as auto-detect.

    The resolution happens in _cmd_verify (use_nli = True when a model is
    named), so the parser itself must NOT hard-code True here.
    """
    parser = _build_parser()
    args = parser.parse_args(["verify", "--nli-model", "cross-encoder/nli-deberta-v3-base",
                              "tests/fixtures/sample.md"])
    assert args.nli is None
    assert args.nli_model == "cross-encoder/nli-deberta-v3-base"


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

def test_mcp_nli_default_autodetects_extra():
    """Unset CITECHECK_NLI -> decided by whether the [nli] extra is importable."""
    from citesure.nli import nli_extra_available

    with patch.dict("os.environ", {}, clear=True):
        expected, _missing = nli_extra_available()
        assert _nli_default() is expected


def test_mcp_nli_default_true_when_extra_present(monkeypatch):
    """With torch/transformers installed, auto-detect resolves to on."""
    monkeypatch.setattr("citesure.nli.nli_extra_available", lambda: (True, []))
    with patch.dict("os.environ", {}, clear=True):
        assert _nli_default() is True


def test_mcp_nli_default_false_when_extra_missing(monkeypatch):
    """Without the extra, auto-detect resolves to off instead of erroring.

    This is the bug: it used to return True, so every tool call on a default
    install answered {"error": "NLI model failed to load..."}.
    """
    monkeypatch.setattr("citesure.nli.nli_extra_available", lambda: (False, ["torch"]))
    with patch.dict("os.environ", {}, clear=True):
        assert _nli_default() is False


def test_mcp_nli_env_truthy_forces_on(monkeypatch):
    """CITECHECK_NLI=1 wins over a missing extra (user asked for it)."""
    monkeypatch.setattr("citesure.nli.nli_extra_available", lambda: (False, ["torch"]))
    with patch.dict("os.environ", {"CITECHECK_NLI": "1"}, clear=True):
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
