<p align="center"><img src="https://raw.githubusercontent.com/DawnofGenX/citesure/main/docs/og-banner.png" alt="citesure" width="100%"/></p>

# citesure

An MCP server + CLI + library that verifies whether an LLM's claims are actually
supported by the sources it cites — resolves each citation, fetches the real
content, flags dead/retracted/paywalled links, and abstains when it can't
confirm.

citesure verifies *claims* (not just whether a link is alive). It needs no API keys and no
generative LLM: the fast path (tiers 1+2) is pure `httpx` + `trafilatura`, and passing
`--no-nli` keeps it fully offline. The NLI tier (tier 3) runs a local cross-encoder and is
**auto-detected**: on when the optional `[nli]` extra is installed, off when it is not. Every
report states which tiers actually ran, so an overlap-only result can never be mistaken for an
entailment-checked one.

## Accuracy

Measured on an independent 108 case set (40 unique URLs, 16 domains), built as a three-way
contrast: each source fact appears three times — verbatim-supported, negation-flipped, and
entity-swapped — all sharing one source sentence. The A-vs-B gap isolates polarity handling and
A-vs-C isolates subject binding, so a drop points at a specific bug class rather than "hard cases".

All four numbers below come from ONE committed run,
`evals/results/v2_post_score_fix/20260929-200951.json`:

| Metric | Result |
|---|---|
| NLI tier on (default claim path) | **93/108 = 86.1%** |
| NLI tier off (deterministic tiers only) | **41/108 = 38.0%** |
| Negation-flip false-supported | **0/32 = 0.0%** |
| Entity-swap false-supported | **5/32 = 15.6%** |

The 48.1-point gap is the cross-encoder tier's entire contribution, measured rather than
asserted. The 86.1% figure is the current one after the pooled-contradiction and score fixes (it
supersedes the original 76.9% v2 baseline).

> **Read these numbers with their sample size.** The set is 108 cases but only **40 unique URLs**:
> 32 URLs appear three times each (one source sentence, three transformations), so the cases are
> correlated, not independent. A 95% Wilson interval on 86.1% is **[78.3%, 91.4%]**, and the safety
> subsets (n=32) are far wider still — 15.6% carries roughly [7%, 30%]. Treat these as accurate
> descriptions of *this* 108-case set, not as general accuracy on the open web. See
> `evals/RESULTS_v2_BASELINE.md` for the design and its limits.

Both safety rates rise to ~90-100% with the NLI tier off, which is the honest cost of the fast path.

An exhaustive threshold sweep (support 0.30-0.90 x ambiguous 0.05-support) moved agreement from
66.7% to 69.4% — a gain of exactly one item. Threshold tuning is a dead end here; see
`evals/THRESHOLD_CALIBRATION.md`.

## Install

```bash
pip install citesure            # tiers 1+2: httpx + trafilatura only
pip install "citesure[nli]"     # adds the NLI cross-encoder tier (auto-detected on)
```

`requires-python >= 3.10`. The default fast path (tiers 1+2) needs only
`httpx` + `trafilatura` — no torch, no model download, no API key, so tier 3 is
auto-detected OFF and the report says so. Installing the `[nli]` extra pulls in the
cross-encoder stack (`torch`, `transformers`, `sentence-transformers`) and tier 3
auto-detects ON, lazy-downloading a ~425 MB model on first use — see
[NLI tier](#nli-tier-auto-detected).

From a checkout: `pip install .` (default) or `pip install ".[nli]"` / `pip install -e ".[dev]"`.

## Quickstart

Create two small files in any directory — a claim document and the page it
cites:

`source.html`
```html
<!DOCTYPE html>
<html>
<head><title>Python 3.12 release notes</title></head>
<body>
<h1>What's New In Python 3.12</h1>
<p>Python 3.12.0 was released on October 2, 2023. It introduces a new
interactive debugger, improved error messages, and faster startup times.
The new interactive debugger lets you step through code directly from the
REPL, and the interpreter now starts up roughly 5% faster than 3.11.</p>
</body>
</html>
```

`notes.md`
```markdown
# My notes

Python 3.12 was released on October 2, 2023, bringing a new interactive
debugger and faster startup times [1].

The moon is made of green cheese [2].

## Sources

1. source.html
2. https://nonexistent-citesure-demo.invalid/green-cheese
```

Then verify:

```bash
citesure verify notes.md
```

On the first run, if the `[nli]` extra is installed, the NLI tier lazy-downloads a ~425 MB
cross-encoder, so that command needs network once. On a plain `pip install citesure` the tier
auto-detects OFF, no download happens, and the report prints `NLI: OFF`. Force tiers 1+2 only
(link status + content overlap) with `--no-nli`:

```bash
citesure verify notes.md --no-nli
```

Real output (run from a clean venv, zero API keys, ~2 s):

```text
# citesure verification report — 2 citation(s) (input format: markdown)

- ✅ **[1]** PASS score=0.944 tier=2 — /tmp/demo/source.html
  - evidence: Python 3.12.0 was released on October 2, 2023. It introduces a new interactive debugger, improved error messages, and faster startup times. The new interactive debugger lets you step through code directly from the REPL, and the interpreter now starts up roughly 5% faster than 3.11.
  - note: page fetched; reachable with no red flags (tier 1)
  - note: overlap tier: score 0.944 against fetched page; thresholds: >= 0.6 supported, >= 0.3 ambiguous, below unsupported
- ⛔ **[2]** UNVERIFIABLE tier=1 — https://nonexistent-citesure-demo.invalid/green-cheese
  - note: ConnectError: [Errno -2] Name or service not known

## Summary

- total: 2
- supported: 1
- unsupported: 0
- unreachable: 1
- paywalled: 0
- ambiguous: 0
- unverifiable (unreachable + paywalled + ambiguous): 1
- pass_rate: 0.5000

**Decision: FAIL** (pass_rate 0.5000 < threshold 0.8)
```

Exit code is `1` because `pass_rate` (0.5) is below the default threshold
(0.8). Citation `[1]` is **supported** (its terms strongly overlap the cited
page); citation `[2]` is **unreachable** (the host doesn't resolve).

The repo also ships a larger bundled sample you can point the CLI at directly:

```bash
citesure verify tests/fixtures/sample.md            # 5 citations, all local
citesure verify tests/fixtures/sample.md --json     # machine-readable JSON
citesure verify tests/fixtures/cases.json --json --strict
citesure verify tests/fixtures/sample.md --md report.md   # also write a .md file
```

## How verification works — the 3-tier pipeline

Each citation is pushed through up to three tiers. A verdict records
`tier_reached` so you can see how far it got.

1. **Reachability (tier 1)** — does the URL resolve and fetch? Is it dead,
   retracted, or paywalled? Uses `httpx` + `trafilatura` (HTML → clean text),
   5 s timeout, 2 retries with backoff, max 10 concurrent fetches, a
   citesure-identifying User-Agent, and robots.txt respect. `file://` and bare
   local paths are supported for offline demos/tests. No headless browser.
2. **Content overlap (tier 2)** — does the claim actually appear in the cited
   region? The sentence/paragraph containing the `[n]` marker is the claim
   unit (D4); it's matched against the top-k most relevant passages of the
   fetched page using deterministic weighted term-coverage scoring in
   `[0, 1]`. No LLM, no embeddings. Compound claims (joined by "and", "while",
   "but") are split into clauses and scored independently — a claim with mixed
   support (some clauses true, some false) returns `ambiguous`, not `supported`.
   When all top-k passages score below 0.3, the fallback scores all passages.
3. **NLI entailment (tier 3, auto-detected)** — a local cross-encoder scores each
   `(claim, best-passage)` pair for entailment. Runs automatically when the `[nli]` extra
   is installed; force it either way with `--nli` / `--no-nli`. See
   [NLI tier](#nli-tier-auto-detected).

Tiers 1+2 need no model and run entirely offline. Tier 3 is fully built and tested — not a
stub — but it requires the optional `[nli]` extra, which is why its default is detected rather
than hard-coded.

### Verdict statuses (D3)

| Status | Meaning |
|--------|---------|
| `supported` | Page fetched and claim terms strongly overlap the cited region (score ≥ 0.6). |
| `unsupported` | Page fetched but the claim is absent from the cited region (score < 0.3), or the page carries a retraction notice. |
| `unreachable` | DNS failure, HTTP 4xx/5xx, timeout, or robots.txt block. |
| `paywalled` | Login/paywall detected on the cited page. |
| `ambiguous` | Partial overlap (0.3 ≤ score < 0.6), or the citation marker could not be located (e.g. a JavaScript-rendered page with no extractable body). |

The overall report is `{total, supported, unsupported, unverifiable,
pass_rate}` where `unverifiable = unreachable + paywalled + ambiguous` and
`pass_rate = supported / total`.

## CLI flags

| Flag | Meaning |
|------|---------|
| `--json` | Print the full report as JSON (`report.to_dict()`). |
| `--md FILE` | Also write the Markdown report to `FILE` (in addition to stdout). |
| `--threshold F` | Minimum `pass_rate` for exit code 0 (default `0.8`). |
| `--strict` | CI mode: `ambiguous` counts as an explicit failure (see exit codes). |
| `--nli` | Enable the NLI entailment tier (tier 3). Lazy-downloads the default model (~425 MB) on first use. |
| `--nli-model NAME` | Cross-encoder model name (HF id) or local path; highest priority in the model-swap chain. Implies `--nli`. |
| `--cache-dir DIR` | Override the fetch cache directory (sets `CITECHECK_CACHE_DIR`). |

### Exit codes

* `0` — default mode: iff `pass_rate >= --threshold`. With `--strict`:
  additionally requires **no** `ambiguous` AND **no** `unsupported` verdicts.
* `1` — verification ran but the rule above is not met.
* `2` — input error (unreadable file, malformed JSON) OR the NLI model failed
  to load (fail fast, D6 — distinct from a verification-failure exit 1).

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

* **`verify_citations(citations)`** — the structured JSON form: a list of
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
script instead, e.g. `"command": "/path/to/venv/bin/citesure-mcp"`.

To enable the NLI tier at server start, set the env vars:

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

`CITECHECK_NLI_MODEL` is optional (the built-in default applies); the model is
lazy-downloaded on first use (~425 MB) into `~/.cache/citesure/`.

## NLI tier (auto-detected)

NLI entailment (tier 3) runs **when the `[nli]` extra is installed** and stays off when it is
not. You do not need a flag in the common case:

```bash
pip install citesure             # tiers 1+2 only  -> NLI OFF, prints which tier ran
pip install "citesure[nli]"      # + cross-encoder -> NLI ON, lazily downloads ~425 MB
```

Force it either way with `--nli` (fail fast if the extra is missing) or `--no-nli`. For the MCP
server, `CITECHECK_NLI=1`/`0` forces the tier on or off; when the variable is unset the server
auto-detects exactly like the CLI.

Whatever the outcome, the report says so — the header names the cross-encoder when tier 3 ran and
prints `NLI: OFF (tiers 1+2 only ...)` when it did not, and `--json` carries `nli_active` and
`tiers_reached`. An overlap-only report is never silently presented as entailment-checked.

Model selection follows a priority chain (D6): `--nli-model NAME` flag /
`nli_model=` library param → `CITECHECK_NLI_MODEL` env var → built-in default
`cross-encoder/nli-deberta-v3-base`. Any Hugging Face cross-encoder name or
local path is accepted; citesure fails fast with a clear error (exit 2) if it
won't load — it never silently falls back to another model.

When NLI is on, the report header shows which model was used, and each scorable
verdict records its entailment score with `tier_reached=3`.

## Local-file citations and the read sandbox

citesure can verify a saved HTML file instead of a URL, which is how you use it fully offline.
That ability is a read primitive, so it is sandboxed:

| Citation form | CLI | MCP server |
|---|---|---|
| relative path (`source.html`) | allowed | allowed |
| path under the current directory | allowed | allowed |
| absolute path elsewhere, `file://` URL | allowed (you asked for it) | **blocked** |
| inside `~/.cache/citesure/` | allowed | allowed |

The MCP server is the primary integration channel and is driven by a model, so it does **not**
get the unrestricted behaviour the CLI has: a citation of `/etc/passwd` is refused, and the
refusal names the fix rather than leaking the file. To widen it deliberately:

```bash
export CITECHECK_LOCAL_ROOTS=/srv/docs:/data/pages   # allow specific directories
export CITECHECK_ALLOW_LOCAL_FILES=1                # allow any local path (CLI-equivalent)
```

A refused path yields the same result whether or not it exists, so the tool cannot be used to
probe the filesystem. Fetch-cache policy is applied *before* these checks, so a snapshot cached
by an earlier run is never replayed to a sandboxed caller.

## Silent-coverage guard

A citation marker that resolves to no URL is a claim that is **never verified**. Those markers
are recorded rather than dropped: the CLI warns on stderr per marker, `--json` includes a
`dropped_markers` array, and the human report gets an *Unresolved citation markers* section. A
report that looks complete is complete.

## Cache & offline-first design

* **Fetch cache** — fetched pages are cached to disk for 24 h, keyed by
  URL+etag, under `~/.cache/citesure/`. Override the location with
  `CITECHECK_CACHE_DIR` (or the CLI `--cache-dir`). Re-verifying the same URLs
  within 24 h makes no network calls.
* **Offline-first** — the tiers-1+2 path needs no network beyond the
  cited URLs themselves, no API keys, and no generative LLM. `file://` and bare local
  paths let you verify entirely offline. The only thing that ever downloads is
  the NLI model, and only when tier 3 is enabled and not yet cached.
* **No headless browser** — JavaScript-rendered pages yield no extractable
  body, so their citations come back `ambiguous` rather than being silently
  guessed at.

## Development

```bash
pip install -e ".[dev]"     # installs pytest
pytest -q                   # offline suite (live + NLI-model tests deselected by default)
pytest -q -m live           # run live-network smoke tests (hit the real internet)
pytest -q -m nli            # run real DeBERTa-v3 model tests (needs the ~425 MB download)
```

Live-network tests (`@pytest.mark.live`) and real-model NLI tests
(`@pytest.mark.nli`) are skipped by default so the suite is green offline.
