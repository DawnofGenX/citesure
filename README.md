# citesure

An MCP server + CLI + library that verifies whether an LLM's claims are actually
supported by the sources it cites — resolves each citation, fetches the real
content, flags dead/retracted/paywalled links, and abstains when it can't confirm.

> **Phase 2 (v0.1.0):** citation extraction (markdown `[n]`, markdown
> `[source](url)`, structured JSON incl. user-supplied `"excerpt"`), HTTP
> fetcher with disk cache, reachability tier (1) **and** content-overlap tier
> (2) — all five verdict statuses (`supported`, `unsupported`, `unreachable`,
> `paywalled`, `ambiguous`), `--strict` CI mode, JSON + Markdown reporting,
> and documented exit codes. The NLI entailment tier lands in Phase 3.

## Install

```bash
pip install -e .          # or: pipx install citesure
```

## CLI

```bash
citesure verify tests/fixtures/sample.md            # human-readable report
citesure verify tests/fixtures/sample.md --json     # machine-readable JSON
citesure verify tests/fixtures/cases.json --json --strict
citesure verify tests/fixtures/sample.md --md report.md   # also write a .md file
```

The default pipeline runs both verification tiers: **reachability** (URL
resolves, fetches, not dead/retracted/paywalled) and **content overlap**
(claim terms vs. top-k most relevant passages of the cited page — no LLM, no
embeddings; deterministic weighted term-coverage scoring in `[0, 1]`). Each
verdict records `tier_reached` (1 or 2), the overlap `score`, and an evidence
snippet (≤300 chars).

### Flags

| Flag | Meaning |
|------|---------|
| `--json` | print the full report as JSON (`report.to_dict()`) |
| `--md FILE` | also write the Markdown report to `FILE` |
| `--threshold F` | minimum `pass_rate` for exit code 0 (default `0.8`) |
| `--strict` | CI mode: ambiguous counts as failure (see exit codes) |
| `--nli`, `--nli-model NAME` | accepted for forward compatibility; ignored until Phase 3 |
| `--cache-dir DIR` | override the fetch cache directory (`CITECHECK_CACHE_DIR`) |

### Exit codes

* `0` — default mode: iff `pass_rate >= --threshold`. With `--strict`:
  additionally requires **no** `ambiguous` AND **no** `unsupported` verdicts.
* `1` — verification ran but the rule above is not met.
* `2` — input error (unreadable file, malformed JSON).

### Verdict statuses (D3)

* `supported` — page fetched and claim terms strongly overlap the cited
  region (score ≥ 0.6).
* `unsupported` — page fetched but the claim is absent from the cited region
  (score < 0.3), or the page carries a retraction notice.
* `unreachable` — DNS failure, HTTP 4xx/5xx, timeout, or robots.txt block.
* `paywalled` — login/paywall detected on the cited page.
* `ambiguous` — partial overlap (0.3 ≤ score < 0.6), or the citation marker
  could not be located (e.g. a JavaScript-rendered page with no extractable
  body).

## Library

```python
import asyncio
from citesure import extract_citations, verify_citations, score_overlap

citations = extract_citations("The sky is blue [1].", {"1": "https://example.com"})
report = asyncio.run(verify_citations(citations))   # tiers 1+2 by default
print(report.pass_rate)

# Overlap tier in isolation (pure, deterministic):
score, snippet = score_overlap("Python 3.12 was released in 2023.", ["...page text..."])
```

## MCP server

`citesure-mcp` is a stdio MCP server (official `mcp` Python SDK) exposing two
tools:

* **`verify_citations(citations)`** — the D1 structured JSON form: a list of
  `{"claim": str, "citation": url-or-id}` objects (optional per-item
  `"excerpt"`). Returns the D3 report: `{total, supported, unsupported,
  unverifiable, pass_rate, verdicts[]}`.
* **`verify_markdown(markdown, url_map?)`** — raw markdown with inline
  citations (`[n]` markers resolved via `url_map` or a trailing
  `## Sources`/`## References` section, plus `[label](url)` links). Same
  report shape.

Both tools run the full pipeline (reachability + content overlap) and return
clean JSON-serializable dicts; bad input comes back as `{"error": "..."}`
rather than a protocol error.

### Adding it to an MCP client

Generic / Claude Desktop style config (any client that launches stdio MCP
servers):

```json
{
  "mcpServers": {
    "citesure": {
      "command": "citesure-mcp",
      "args": []
    }
  }
}
```

If the package isn't on your `PATH`, use the absolute path to the console
script instead, e.g. `"command": "/path/to/venv/bin/citesure-mcp"` (in this
repo: `.venv/bin/citesure-mcp`).

### NLI tier (opt-in)

NLI entailment (tier 3) is **off by default** — the fast tiers-1+2 path runs
with no model download. Enable it at *server start* via environment
variables:

```json
{
  "mcpServers": {
    "citesure": {
      "command": "citesure-mcp",
      "env": {
        "CITECHECK_NLI": "1",
        "CITECHECK_NLI_MODEL": "cross-encoder/nli-deberta-v3-base"
      }
    }
  }
}
```

`CITECHECK_NLI_MODEL` is optional (the built-in default applies); the model
is lazy-downloaded on first use (~425 MB) into `~/.cache/citesure/`.
