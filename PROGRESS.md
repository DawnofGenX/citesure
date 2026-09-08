# citesure — Build Progress Log

Orchestrator-driven build per PLAN.md. All "Locked Decisions" D1–D12 are binding.
Each phase entry records what was built, test results the orchestrator ran
personally, files created, and gaps.

---

## 2026-09-08 — Resume after WSL reboot (pre-Phase-1 state verified)

**Verified on disk by orchestrator:**
- Venv at `.venv/` intact: Python 3.12.14; httpx 0.28.1, trafilatura 2.2.0,
  mcp 2.2.0, torch 2.14.0+cpu, transformers 5.16.1, sentence-transformers 6.0.1,
  pytest 9.1.1 all present. NOT recreated.
- Existing files: `pyproject.toml`, `src/citesure/__init__.py`,
  `src/citesure/models.py` (D3 verdict model), `src/citesure/mcp_server.py`
  (deliberate Phase-4 stub), `README.md`, `.gitignore`, `PLAN.md`.
- **Known breakage (expected mid-phase state):** `__init__.py` imports
  `citesure.citations` and `citesure.reachability`, which do not exist yet →
  `import citesure` fails. Package NOT pip-installed into the venv yet.
- No `tests/` dir, no fixtures, no fetcher, no CLI. Git repo initialized, zero commits.
- `pytest -q`: no tests found (warning only) — consistent with no tests/ dir.

**Action:** committed this verified partial state as the first commit
("phase 1: partial — skeleton + models"), then resumed Phase 1.

### Phase 1 — Core engine — COMPLETE (2026-09-08)
Target: software-engineer (dispatched via `hermes -p software-engineer`, ~58 min
wall time — the custom model provider is slow, several single-turn waits of
10–20 min; process stayed alive on an established API connection throughout).

**Built (verified by orchestrator on disk):**
- `src/citesure/citations.py` (304 ln) — D1 extraction: `[n]` markers + url_map,
  `[source](url)` inline links, JSON `{claim, citation}` pairs; claim-unit =
  sentence/paragraph containing marker (D4); `load_input()` auto-detects
  json vs markdown incl. "## Sources" reference sections.
- `src/citesure/fetcher.py` (353 ln) — D7: httpx async, 5s timeout, 2 retries w/
  backoff, 10-concurrent semaphore, citesure User-Agent, robots.txt via
  robotparser (cached per domain), 24h disk cache keyed URL+etag under
  ~/.cache/citesure (CITECHECK_CACHE_DIR override), file:// + bare-path support,
  trafilatura HTML→text, pure paywall/retraction heuristic functions. No browser.
- `src/citesure/reachability.py` (85 ln) — tier 1: classify_reachability →
  D3 statuses; verify_citations → Report (tier_reached=1).
- `src/citesure/cli.py` (188 ln) — `citesure verify <file>`; --json, --threshold
  (default 0.8), --strict, --nli/--nli-model accepted (Phase-3 notice),
  --cache-dir; exit 0 iff pass_rate>=threshold else 1.
- `tests/` — test_citations.py (245 ln), test_fetcher.py (313 ln),
  test_reachability.py (148 ln), test_cli.py (157 ln); fixtures: pages/page1-3.html,
  sample.md (5 citations: 4 local file:// + 1 .invalid dead URL), sample.json.
- Editable install done (`pip install -e .`); console scripts resolve.

**Orchestrator's own verification (not subagent claims):**
- `.venv/bin/python -m pytest -x -q` → **78 passed in 21.12s** (exit 0).
- `.venv/bin/citesure verify tests/fixtures/sample.md --json` → valid report:
  total=5 supported=4 unverifiable=1 pass_rate=0.8, exit 0.
- Human-readable mode renders correctly (PASS/UNVERIFIABLE lines + summary).
- `--threshold 0.9 --strict` → exit 1 (exit-code semantics correct).
- Stub scan clean (only benign best-effort-cache `pass` in fetcher).
- `import citesure` resolves all __all__ exports.

**Gaps:** none for Phase 1 scope. (Overlap/NLI tiers are later phases by design.)
Commit: 92e2980.

### Phase 2 — Overlap tier + reporting — COMPLETE (2026-09-08)
Target: software-engineer (dispatched ~15:47, finished 17:52, ~2h wall — same
slow-provider pattern; one context-compaction mid-run, no impact).

**Built (verified by orchestrator on disk):**
- `src/citesure/overlap.py` (339 ln) — D4 citation-anchored windows: passage
  segmentation, top-k relevance ranking, deterministic overlap scoring
  (module-level thresholds), excerpt override path. NO LLM/embeddings.
- `src/citesure/report.py` (113 ln) — report rendering (human + markdown).
- `tests/fixtures/mock_server.py` (118 ln) — local http.server on 127.0.0.1
  serving 404/500/timeout routes (D9 "5 unreachable via local mock server").
- `tests/fixtures/cases.json` — 27-case frozen corpus with expected_status per
  case; 20 fixture HTML pages under tests/fixtures/pages/.
- `tests/test_overlap.py`, `tests/test_corpus.py` (189 ln) — corpus test iterates
  EVERY case and asserts its expected status (real acceptance gate).
- CLI extended: --strict now real, --md output, exit-code semantics documented.

**Orchestrator's own verification (not subagent claims):**
- `.venv/bin/python -m pytest -x -q` → **113 passed in 50.14s** (exit 0).
- `pytest tests/test_corpus.py -q` → 4 passed in 22.18s (full-corpus gate).
- `citesure verify tests/fixtures/sample.md` → PASS, pass_rate 0.8, exit 0.
- `citesure verify tests/fixtures/cases.json --json [--strict]` → exit 1
  (pass_rate 0.2222 < 0.8); strict semantics correct.
- Corpus composition vs D9: supported=6(≥5), unsupported=9(≥5), unreachable=5
  (incl. 3 mock-server), paywalled=3, ambiguous=4 (incl. JS-page), retraction/
  dead-link=3, local/file:// paths=22(≥3). Meets/exceeds D9.
- Overlap tier genuinely exercised: tier_reached=2 on 17/27 verdicts, real
  numeric scores (e.g. sup1 score=0.9444).

**Gaps:** none for Phase 2 scope. Commit: 92845bd.
