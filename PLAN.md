# citesure — MCP Citation Verifier (Project Plan)

**Repo:** `DawnofGenX/citesure` · **Package/CLI:** `citesure` / `citesure` + `citesure-mcp`
**One-liner:** An MCP server + CLI + library that verifies whether an LLM's claims are actually supported by the sources it cites — resolves each citation, fetches the real content, flags dead/retracted/paywalled links, and abstains when it can't confirm.

**Positioning:** vishwas verifies *content*; citesure verifies *claims*. Rides the momentum of PR #4776 to `modelcontextprotocol/servers` (same author, canonical SDK). General-web citations (existing tools cover academic DOIs only).

## Locked Decisions (grill-me session, 2026-09-08)

| # | Decision |
|---|----------|
| D1 | **Input formats:** both — markdown with inline citations (`[1]`, `[source](url)`) AND structured JSON `{claim, citation}` pairs |
| D2 | **Verification tiers (all in v1):** (1) reachability — URL resolves, fetches, not dead/retracted/paywalled; (2) content overlap — claim terms/entities vs cited passage, no LLM; (3) NLI entailment — local cross-encoder scores entailment. Tiers 1+2 are the default path; tier 3 is a first-class opt-in (`--nli`), fully built and tested, NOT a stub |
| D3 | **Verdict model:** per-citation `{citation_id, url, status, tier_reached, score, evidence (≤300 chars), notes[]}`. Statuses: `supported \| unsupported \| unreachable \| paywalled \| ambiguous`. Rules: unsupported = page fetched but claim absent from cited region AND (NLI < 0.3 when on); ambiguous = partial overlap, NLI 0.3–0.7 band, or marker not locatable (JS page); unreachable = DNS/404/timeout; paywalled = login/paywall detected. Overall report: `{total, supported, unsupported, unverifiable, pass_rate}`. Exit code 0 if pass_rate ≥ threshold else 1. `--strict` flag: ambiguous counts as failure (CI mode) |
| D4 | **Matching engine:** citation-anchored windows — sentence/paragraph containing the `[n]` marker is the claim unit; match against top-k most relevant passages in fetched page. User-supplied excerpts accepted as input option. NO whole-page matching |
| D5 | **Form factor:** ONE Python package, THREE entry points — `citesure` CLI (batch/CI), importable library API, `citesure-mcp` server exposing `verify_citations` + `verify_markdown` tools. Built on the OFFICIAL `mcp` Python SDK (FastMCP). ~80% of code testable without MCP plumbing |
| D6 | **NLI model:** `cross-encoder/nli-deberta-v3-base` default (~425 MB), lazy download on first `--nli` use, cached to `~/.cache/citesure/`. Model swap at 3 levels (priority order): `--nli-model <hf-name-or-path>` flag / `nli_model=` lib param → `CITECHECK_NLI_MODEL` env var → built-in default. Any HF cross-encoder name or local path; fail fast with clear error if it won't load |
| D7 | **Fetcher:** httpx + trafilatura (HTML→clean text). 5s timeout, 2 retries w/ backoff, max 10 concurrent, custom User-Agent identifying citesure, respect robots.txt (deliberate callback to PR #4776). 24h disk cache keyed by URL+etag. `file://` and bare paths supported for offline tests/demos. NO headless browser |
| D8 | **Non-goals (v1):** no headless-browser rendering (JS pages → ambiguous); no DOI/Crossref/OpenAlex resolution; no claim extraction from scratch (verifies given claims+citations only); no cross-document batch scoring; no paid API integrations (zero keys required); Linux/macOS only (no Windows guarantee) |
| D9 | **Acceptance bar:** frozen fixture corpus in `tests/fixtures/` (~30 saved HTML pages + claim/citation pairs): 5 supported, 5 unsupported, 5 unreachable (via local mock server), 3 paywalled, 3 ambiguous, 3 retraction/dead-link, 3 file://. Unit tests for citation extraction (all 3 input forms), overlap scoring, verdict logic. One live smoke test (`@pytest.mark.live`, skipped by default) hitting 2–3 stable URLs. GitHub Actions CI on every push. **Done =** `pip install -e .` → `citesure verify sample.md` correct on bundled sample; full suite green offline; MCP server passes real-client integration test; README demo reproducible from clean venv in <2 min, zero API keys |
| D10 | **Name:** `citesure` everywhere (repo, package, CLI). `citecheck` is taken on PyPI (nathanjmcdougall) |
| D11 | **Upstream strategy:** standalone repo first; after v1 stable+tested, propose inclusion in `modelcontextprotocol/servers` |
| D12 | **One-command install:** proper `pyproject.toml`, `pip install citesure` / `pipx install citesure`, entry points for both CLIs |

## Phases (each ends with a verifiable exit criterion)

### Phase 1 — Core engine (target: software-engineer)
Package skeleton (`pyproject.toml`, src layout, one-command install), citation extraction (markdown `[n]` + `[source](url)` + JSON), fetcher (D7), reachability tier, verdict data model (D3), CLI `citesure verify <file>`.
**Exit:** offline test suite green on fixture corpus subset (reachability cases); `citesure verify` runs end-to-end on a sample file.

### Phase 2 — Overlap tier + reporting (target: software-engineer)
trafilatura extraction, citation-anchored window logic (D4), top-k passage matching, overlap scoring, all 5 statuses, `--strict`, JSON + Markdown report output, exit codes.
**Exit:** all ~30 fixtures produce correct statuses offline; report renders correctly.

### Phase 3 — NLI tier (target: ai-ml-engineer)
DeBERTa-v3 integration (D6), lazy download + cache, `--nli` / `--nli-model` / env override, mid-band ambiguity logic, CPU perf sanity (<5 min for fixture suite).
**Exit:** NLI tests pass on CPU; model-swap paths verified (flag + env); fail-fast error on bad model name.

### Phase 4 — MCP server (target: software-engineer)
`citesure-mcp` entry point on official SDK, `verify_citations` + `verify_markdown` tools, real MCP client integration test.
**Exit:** works in an actual MCP client session; integration test green in CI.

### Phase 5 — Polish & launch (targets: technical-writer + devops-engineer + qa-engineer)
README with demo, docs, PyPI publish prep, GitHub Actions CI hardening, final QA gate against D9 acceptance bar.
**Exit:** clean-venv install → verify → correct report, end to end; QA sign-off checklist complete.

## Execution protocol
- Orchestrator profile drives each phase; specialist profiles execute as subagents via `hermes -p <profile> chat --yolo -q ...`
- **Hard cap: 2 concurrent API calls** — orchestrator runs child profiles SEQUENTIALLY (one at a time)
- Every phase: orchestrator verifies claims on disk (runs the tests itself) before reporting done
- Progress tracked in `PROGRESS.md` (append per phase)
