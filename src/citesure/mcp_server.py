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


def _nli_default() -> bool:
    """Default NLI-tier state for the MCP server (auto-detect).

    Order of decision:
      1. ``CITECHECK_NLI`` explicitly set to a falsy value (``0/false/no/off``)
         -> NLI off (explicit opt-out always wins).
      2. ``CITECHECK_NLI`` explicitly truthy -> NLI on.
      3. Otherwise AUTO-DETECT: on when the optional ``[nli]`` extra is
         importable, off when it is not.

    Rationale (bug fixed 2026-10-04): this used to return True unless the
    env var was falsy. Combined with torch/transformers moving to the
    ``[nli]`` extra in v0.2.0, every default install answered every tool call
    with ``{"error": "NLI model failed to load..."}`` - the MCP server was
    non-functional out of the box. The tier that actually ran is reported in
    the tool result (``nli_active``), so this is never a silent downgrade.
    """
    val = os.environ.get(ENV_NLI_ENABLED, "").strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    if val in ("1", "true", "yes", "on"):
        return True
    from .nli import nli_extra_available

    available, _missing = nli_extra_available()
    return available


def _nli_model_name() -> str | None:
    """The ``CITECHECK_NLI_MODEL`` override, or None for the D6 default."""
    name = os.environ.get(ENV_NLI_MODEL, "").strip()
    return name or None


async def _run_pipeline(
    citations: list[Any], use_nli: bool | None = None
) -> dict[str, Any]:
    """Run the full library pipeline and return the D3 report as a dict.

    Tiers 1+2 always run; tier 3 (NLI) only when ``use_nli`` is True.
    ``use_nli=None`` means "decide automatically" via :func:`_nli_default`,
    which honours CITECHECK_NLI and otherwise auto-detects the ``[nli]``
    extra.

    The default was previously ``True``, which meant any caller that did not
    pass the flag explicitly got NLI-on regardless of what was installed - the
    bug that made the server answer every call with an error on a default
    install. Resolving inside this function means the tool wrappers, the
    library entry point and the documented default cannot drift apart again.

    The NLI stack is imported lazily inside the pipeline — nothing heavy
    happens here unless the tier is on.
    """
    if use_nli is None:
        use_nli = _nli_default()
    from .nli import NLIError
    from .reachability import verify_citations

    try:
        report = await verify_citations(
            citations,
            use_overlap=True,
            use_nli=use_nli,
            nli_model=_nli_model_name(),
        )
    except NLIError as exc:
        # Fail fast with a clear, actionable message (D6) instead of a crash.
        return {"error": f"NLI model failed to load: {exc}"}
    payload = report.to_dict()
    # Declare which tiers actually ran. An overlap-only result must never be
    # read as an entailment-checked one - the safety numbers in evals/ depend
    # on tier 3 having executed.
    payload["nli_active"] = use_nli
    payload["tiers_reached"] = 3 if use_nli else 2
    return payload


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
    async def verify_citations(
        citations: list[dict], use_nli: bool | None = None
    ) -> dict:
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
            return await _run_pipeline(cits, use_nli=use_nli if use_nli is not None else _nli_default())
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
        markdown: str,
        url_map: dict[str, str] | None = None,
        use_nli: bool | None = None,
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
            return await _run_pipeline(cits, use_nli=use_nli if use_nli is not None else _nli_default())
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
