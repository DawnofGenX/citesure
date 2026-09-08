# citesure

An MCP server + CLI + library that verifies whether an LLM's claims are actually
supported by the sources it cites — resolves each citation, fetches the real
content, flags dead/retracted/paywalled links, and abstains when it can't confirm.

> **Phase 1 (v0.1.0):** package skeleton, citation extraction (markdown `[n]`,
> markdown `[source](url)`, structured JSON), HTTP fetcher with disk cache,
> reachability tier, verdict model, and the `citesure verify` CLI. Content-overlap
> and NLI tiers land in later phases.

## Install

```bash
pip install -e .          # or: pipx install citesure
```

## CLI

```bash
citesure verify tests/fixtures/sample.md --json
```

Exit code is `0` when `pass_rate >= --threshold` (default `0.8`), else `1`.

## Library

```python
from citesure import extract_citations, verify_citations

citations = extract_citations("The sky is blue [1].", {"1": "https://example.com"})
report = verify_citations(citations)
print(report.pass_rate)
```

## MCP server

`citesure-mcp` is provided as an entry point; the FastMCP tools
(`verify_citations`, `verify_markdown`) are built in Phase 4.
