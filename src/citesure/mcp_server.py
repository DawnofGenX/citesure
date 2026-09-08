"""citesure-mcp — MCP server exposing the citesure verification pipeline (D5).

Built on the official ``mcp`` Python SDK. **SDK version note:** the installed
SDK is ``mcp`` 2.x, where the v1 ``FastMCP`` class was renamed to
``MCPServer`` (imported from ``mcp.server.mcpserver``); the API shape used
here (``MCPServer(name=...)``, ``@server.tool()``, ``run(transport="stdio")``)
is the direct successor of the FastMCP one described in D5.

Tools
-----
* ``verify_citations(citations: list[dict]) -> dict`` — the D1 structured
  JSON form: a list of ``{"claim": str, "citation": url-or-id}`` objects
  (optional per-item ``"excerpt"``). Runs the full library pipeline
  (reachability + content overlap; NLI only when enabled below) and returns
  ``report.to_dict()`` (the D3 report shape).
* ``verify_markdown(markdown: str, url_map: dict[str, str] | None = None)
  -> dict`` — raw markdown with inline citations (``[n]`` markers resolved
  through ``url_map`` or a trailing Sources/References section, plus
  ``[label](url)`` links). Same pipeline, same report shape.

Both tools validate their inputs and return a clean JSON-serializable dict.
Bad *content* (e.g. a citation item missing its ``claim`` key) comes back as
``{"error": "<message>"}`` rather than raising to the client; inputs that
violate the declared argument schema are rejected by the SDK itself with a
descriptive tool error.

NLI configuration (D2/D6)
-------------------------
The NLI entailment tier (tier 3) is **OFF by default** — the fast tiers-1+2
path runs without any model download. Enable it at *server start* via
environment variables:

* ``CITECHECK_NLI=1`` — turn the NLI tier on (any of ``1/true/yes/on``).
* ``CITECHECK_NLI_MODEL=<hf-name-or-path>`` — select the cross-encoder
  (middle of the D6 priority chain; the built-in default
  ``cross-encoder/nli-deberta-v3-base`` applies when unset).

The heavy NLI/torch stack is imported lazily — only when the tier actually
runs — so importing this module (or starting the server with NLI off) never
pulls in torch.

Import safety
-------------
Importing :mod:`citesure.mcp_server` does **not** start the server, open any
network connection, or require the MCP SDK beyond what ``build_server()``
needs. Only :func:`main` (the ``citesure-mcp`` console script) runs the
server, over stdio — launchable by any MCP client::

    {
      "mcpServers": {
        "citesure": { "command": "citesure-mcp" }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

#: Env var enabling the NLI tier at server start (documented above).
ENV_NLI_ENABLED = "CITECHECK_NLI"
#: Env var selecting the NLI cross-encoder (D6 chain).
ENV_NLI_MODEL = "CITECHECK_NLI_MODEL"


def _nli_enabled() -> bool:
    """True when the server was started with ``CITECHECK_NLI`` set truthy."""
    return os.environ.get(ENV_NLI_ENABLED, "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _nli_model_name() -> str | None:
    """The ``CITECHECK_NLI_MODEL`` override, or None for the D6 default."""
    name = os.environ.get(ENV_NLI_MODEL, "").strip()
    return name or None


async def _run_pipeline(citations: list[Any]) -> dict[str, Any]:
    """Run the full library pipeline and return the D3 report as a dict.

    Tiers 1+2 always run; tier 3 (NLI) only when the server was started with
    ``CITECHECK_NLI`` enabled. The NLI stack is imported lazily inside the
    pipeline (see :mod:`citesure.nli`) — nothing heavy happens here unless
    the tier is on.
    """
    from .nli import NLIError
    from .reachability import verify_citations

    try:
        report = await verify_citations(
            citations,
            use_overlap=True,
            use_nli=_nli_enabled(),
            nli_model=_nli_model_name(),
        )
    except NLIError as exc:
        # Fail fast with a clear, actionable message (D6) instead of a crash.
        return {"error": f"NLI model failed to load: {exc}"}
    return report.to_dict()


def build_server():
    """Construct the MCPServer (v1: FastMCP) app named ``citesure``.

    Import-safe: building the app registers tools but starts nothing. The
    MCP SDK is imported here (not at module import time) so that importing
    :mod:`citesure.mcp_server` stays cheap and dependency-light.
    """
    from mcp.server.mcpserver import MCPServer

    from .citations import _citations_from_json, extract_citations

    server = MCPServer(
        name="citesure",
        description=(
            "Verify that an LLM's claims are supported by the sources it "
            "cites: reachability + content overlap (and optional NLI "
            "entailment when started with CITECHECK_NLI=1)."
        ),
    )

    @server.tool(
        description=(
            "Verify a list of claim/citation pairs (D1 structured JSON form). "
            "Each item: {'claim': str, 'citation': url-or-id} with an optional "
            "'excerpt' (user-supplied passage to match against). Returns the "
            "D3 report: {total, supported, unsupported, unverifiable, "
            "pass_rate, verdicts[]}. Bad input returns {'error': ...}."
        )
    )
    async def verify_citations(citations: list[dict]) -> dict:
        # Defensive: some clients send the list as a JSON string.
        data = citations
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                return {"error": f"'citations' is not valid JSON: {exc}"}
        try:
            cits = _citations_from_json(data)
        except ValueError as exc:
            return {"error": str(exc)}
        if not cits:
            return {"error": "no citations to verify"}
        try:
            return await _run_pipeline(cits)
        except Exception as exc:  # noqa: BLE001 - never crash the session
            return {"error": f"{type(exc).__name__}: {exc}"}

    @server.tool(
        description=(
            "Verify inline citations in raw markdown text: '[n]' numeric "
            "markers (resolved via url_map or a trailing ## Sources / "
            "## References section) and '[label](url)' links. Returns the "
            "same D3 report shape as verify_citations. Bad input returns "
            "{'error': ...}."
        )
    )
    async def verify_markdown(
        markdown: str, url_map: dict[str, str] | None = None
    ) -> dict:
        if not isinstance(markdown, str) or not markdown.strip():
            return {"error": "'markdown' must be a non-empty string"}
        cits = extract_citations(markdown, url_map)
        if not cits:
            return {
                "error": (
                    "no citations found in the markdown (expected '[n]' "
                    "markers with a matching url_map/Sources section, or "
                    "'[label](url)' links)"
                )
            }
        try:
            return await _run_pipeline(cits)
        except Exception as exc:  # noqa: BLE001 - never crash the session
            return {"error": f"{type(exc).__name__}: {exc}"}

    return server


def main() -> int:
    """Console-script entry point (``citesure-mcp``): run over stdio.

    Blocks reading stdin until the client disconnects; a clean SIGTERM/EOF
    exit is expected, not a traceback.
    """
    server = build_server()
    try:
        asyncio.run(server.run_stdio_async())
    except KeyboardInterrupt:  # pragma: no cover - interactive Ctrl-C
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
