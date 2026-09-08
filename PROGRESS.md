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

### Phase 3 — NLI tier — CODE COMPLETE, MODEL DOWNLOAD PENDING (2026-09-08)
Target: ai-ml-engineer (dispatched ~17:58, finished 20:01, ~2h wall; one
context-compaction + a transient HF-cache-lock hang during its own debugging,
both resolved).

**Built (verified by orchestrator on disk):**
- `src/citesure/nli.py` (438 ln) — D6: `resolve_nli_model()` (arg > env >
  default chain), lazy `get_nli_model()` with module-level cache, CPU-only
  CrossEncoder load, batched scoring, `nli_band()` D3 banding (>=0.7 supported,
  0.3–0.7 ambiguous, <0.3 unsupported) + documented overlap×NLI combination
  rules, custom `NLIError` (fail fast, no silent fallback). Cache under
  ~/.cache/citesure (CITECHECK_CACHE_DIR override).
- `tests/test_nli.py` (591 ln) — D6 chain, fail-fast, banding, mock-model
  plumbing (all offline); 4 @pytest.mark.nli model-dependent tests (skipped by
  default).
- CLI wired: `--nli` / `--nli-model` (implies --nli); pipeline threads
  use_nli through reachability→overlap→tier-3 (use_nli implies overlap).

**Orchestrator's own verification (not subagent claims):**
- `.venv/bin/python -m pytest -x -q` → **143 passed, 4 deselected in 65.45s**
  (exit 0). Clean exit re-confirmed (PYTEST_EXIT=0, no teardown hang in my run).
- D6 chain verified directly: default=cross-encoder/nli-deberta-v3-base;
  env-only→env value; arg-over-env→arg value; empty-arg→env value. ✓
- Fail-fast verified directly: bad name raises NLIError with clear message
  ("not a local folder and not a valid model identifier"). ✓
- Banding thresholds present (NLi_SUPPORTED_THRESHOLD=0.7, AMBIGUOUS=0.3). ✓

**GAP (recorded per protocol):** the ~425MB DeBERTa-v3 model did NOT finish
downloading — HF is reachable (fail-fast got a real HF response) but throughput
was very slow; cache stalled at ~132K of ~425MB, no weight files present. The
4 @pytest.mark.nli tests are therefore deselected/skipped. Code is complete and
the non-model suite is green. A background download was started by the
orchestrator to attempt completion; if it lands, run
`.venv/bin/python -m pytest -q -m nli` to exercise the model-dependent tests.
Commit: 2d2cef2.

### PAUSE (2026-09-08 ~20:55 UTC) — user requested
Orchestrator + Phase 4 child killed at user request. State: Phases 1-3 committed
(fffdeae, 92e2980, 92845bd, 2d2cef2). Phase 4 (MCP server) was IN PROGRESS when
paused: mcp_server.py rewritten from stub, tests/test_mcp_integration.py added,
PROGRESS.md/README.md touched — all UNCOMMITTED (working tree dirty on purpose;
resume by re-dispatching orchestrator with ORCHESTRATOR-BRIEF.md, which tells it
to verify existing state first). Open item: DeBERTa-v3 model download stalled at
132K/425MB (network); retry before final NLI acceptance test.

### Phase 4 — MCP server — COMPLETE (2026-09-08, resumed after user pause)
Built by software-engineer child before pause; verified by main agent on resume.

**Built (verified on disk):**
- `src/citesure/mcp_server.py` (200 ln) — `citesure-mcp` entry point on official
  mcp SDK 2.x (MCPServer, successor of FastMCP). Tools: `verify_citations`
  (D1 structured JSON list of {claim, citation, excerpt?}) and
  `verify_markdown` (raw markdown, [n] markers via url_map or trailing
  Sources/References section, plus [label](url) links). Both return the D3
  report dict; bad content → {"error": ...} not an exception. NLI tier OFF by
  default (CITECHECK_NLI=1 / CITECHECK_NLI_MODEL env at server start); torch
  imported lazily so NLI-off startup never loads the heavy stack. Importing
  the module starts nothing.
- `tests/test_mcp_integration.py` (174 ln) — REAL MCP client session over stdio:
  spawns .venv/bin/citesure-mcp as subprocess via StdioServerParameters +
  mcp.Client, lists tools, calls both tools against local file:// fixtures;
  plus in-process bad-input error-path test.

**Verification (main agent's own runs):**
- `.venv/bin/python -m pytest tests/test_mcp_integration.py -v` → 3 passed in 2.06s.
- Full suite: `.venv/bin/python -m pytest -q` → **146 passed, 4 deselected**
  (deselected = live DeBERTa tests, model download still pending).
- `build_server()` returns MCPServer instance cleanly.

**Gaps:** none for Phase 4 scope. Open item carried from Phase 3: DeBERTa-v3
model download stalled at 132K/425MB — retry before final acceptance.
Commit: (this commit).

---

## 2026-09-08 — Phase 5 — Polish & launch

### NLI correctness fix (unblocked by completed model download)

The background DeBERTa-v3 download **finished** during Phase 5
(`du -sh ~/.cache/citesure` = 715M; no `.incomplete` blobs; weights present).
Running the previously-deselected `@pytest.mark.nli` tests for the first time
exposed a real bug: `test_real_default_model_entailment_ordering` FAILED —
clearly-entailed pairs scored ~0.0002 instead of ~0.99.

**Root cause:** the cross-encoder is trained on `(premise, hypothesis)` where
the *premise* is the source text and the *hypothesis* is the statement being
checked. `_entailment_probs` fed the tokenizer `(claim, passage)` — i.e. it
asked "does the claim entail the passage?" instead of "does the passage
support the claim?". A passage that merely *adds* information to the claim
reads as "neutral" (~0), silently collapsing every verdict toward unsupported.
Verified empirically against `cross-encoder/nli-deberta-v3-base`: swapped order
gives ent=0.993 (entailed) vs ent=0.0007 (unrelated); original order gave
ent=0.0002 / ent=0.0003.

**Fix:** feed the tokenizer `(passage, claim)` in `_entailment_probs`
(`src/citesure/nli.py`), keeping the public `score_nli(model, claim, passage)`
API and all mock-based tests intact. Committed as `579dc7c`. After the fix,
`pytest -q -m nli` → **4 passed in 133.40s**. This bug was masked through
Phases 1–4 because the model never finished downloading, so all NLI tests were
deselected.

### Deliverable 1 — README.md polish

Rewrote `README.md` (technical-writer scope): accurate quickstart with REAL
captured output, MCP server setup snippet (mcpServers JSON + CITECHECK_NLI env
vars), the 3-tier pipeline, the 5 verdict statuses, exit codes, all flags
(`--strict`/`--json`/`--md`/`--threshold`/`--nli`/`--nli-model`/`--cache-dir`),
cache behavior (`CITECHECK_CACHE_DIR`), and offline-first design.

**D9 reproducibility claim verified by actually running it in a fresh venv:**
- `python3 -m venv /tmp/citesure-clean` (created 22:11:28)
- `/tmp/citesure-clean/bin/pip install dist/citesure-0.1.0.tar.gz`
  (finished 22:13:19) → **total install ≈ 1 min 51 s**, zero API keys.
- `/tmp/citesure-clean/bin/citesure verify notes.md` → **real 2.37 s**, output
  byte-identical to the repo venv (1 supported, 1 unreachable, pass_rate 0.5,
  exit 1). Well under the <2 min bar.

### Deliverable 2 — GitHub Actions CI

Created `.github/workflows/ci.yml`: trigger on push + PR; matrix python
3.10/3.11/3.12 (matches `requires-python >= 3.10`); steps checkout →
setup-python (pip cache) → `pip install -e ".[dev]"` → `pytest -q`. The `[dev]`
extra (pytest) already existed in pyproject. Live + NLI-model tests are
deselected by default via pyproject addopts, so CI is green offline (D9).
Validated: `python -c "import yaml; yaml.safe_load(...)"` → YAML OK, jobs/matrix/
steps parsed correctly. Local validation only (cannot push to GitHub — user
reviews first).

### Deliverable 3 — PyPI publish PREP (NOT published)

- pyproject.toml metadata confirmed complete: name=citesure, version=0.1.0,
  description, readme (long_description), license MIT, authors, keywords,
  classifiers, project_urls (Homepage + Repository).
- `.venv/bin/pip install build twine`; `.venv/bin/python -m build` →
  **Successfully built citesure-0.1.0.tar.gz and citesure-0.1.0-py3-none-any.whl**
  (both in `dist/`).
- `.venv/bin/twine check dist/*` →
  ```
  Checking dist/citesure-0.1.0-py3-none-any.whl: PASSED
  Checking dist/citesure-0.1.0.tar.gz: PASSED
  ```
- Note: the sdist includes `tests/*.py` but not the HTML/JSON fixtures (standard
  library packaging); the README quickstart uses self-contained inline files so
  it does not depend on shipped fixtures.

### PUBLISH (user runs this)

Do NOT run this automatically — publishing requires PyPI credentials and user
approval. From the repo root, after confirming `dist/` holds the intended
artifacts:

```bash
twine upload dist/*
```

(Optionally `twine upload --repository pypi dist/*` if multiple repositories
are configured. Requires `TWINE_USERNAME`/`TWINE_PASSWORD` or an API token.)

### Deliverable 4 — Phase 5 QA gate

Full offline suite run personally: `.venv/bin/python -m pytest -q` →
**146 passed, 5 deselected in 55.04s** (deselected = 4 NLI-model + 1 live).

| Check | Result | Evidence |
|-------|--------|----------|
| Full offline suite | PASS | 146 passed, 5 deselected in 55.04s |
| `citesure verify tests/fixtures/sample.md` | PASS | 4 supported, 1 unverifiable, pass_rate 0.8, exit 0 |
| `citesure verify tests/fixtures/cases.json --json --strict` | PASS | exit 1; pass_rate 0.2222 (< 0.8) |
| MCP integration test alone | PASS | `pytest tests/test_mcp_integration.py -v` → 3 passed in 1.77s |
| NLI live tests (model now present) | PASS | `pytest -q -m nli` → 4 passed in 133.40s (post-fix) |

**D9 acceptance bar, item by item:**

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 1 | Corpus composition (~30 saved pages + pairs) | PASS | 27 cases: supported=6(≥5), unsupported=9(≥5), unreachable=5(≥5, incl. mock server), paywalled=3(≥3), ambiguous=4(≥3), retraction/dead-link=3, local/file://=22(≥3); 20 fixture HTML pages. Meets/exceeds D9. |
| 2 | Extraction forms (all 3 input forms) | PASS | test_citations.py: 12 markdown-form tests (`[n]`, `[source](url)`, url_map, Sources/References sections) + 6 JSON-form tests (list-of-pairs, wrapper+sources map, relative path, malformed). |
| 3 | Overlap scoring | PASS | test_overlap.py present; tier_reached=2 exercised across corpus with real numeric scores (e.g. sup1 score=0.944). |
| 4 | Verdict logic (all 5 statuses) | PASS | All 5 statuses represented in the frozen corpus and asserted per-case by test_corpus.py. |
| 5 | Live smoke test `@pytest.mark.live`, skipped by default | PASS | Added `tests/test_live_smoke.py` (was a gap — marker declared in pyproject but unused). Deselected by default (now 5 deselected); `pytest -q -m live` → 1 passed in 1.27s against example.com / python.org / wikipedia. |
| 6 | CI file present | PASS | `.github/workflows/ci.yml` created, valid YAML, matrix 3.10/3.11/3.12, offline `pytest -q`. |
| 7 | One-command install works | PASS | Clean venv install from sdist ≈ 1 min 51 s; `citesure` console script resolves and runs. |
| 8 | MCP real-client test green | PASS | Real stdio MCP client session (subprocess spawn + JSON-RPC): 3 passed in 1.77s. |
| 9 | README demo reproducible <2 min, zero API keys | PASS | Fresh venv: install ≈ 1 min 51 s + demo run 2.37 s, zero API keys, output matches README exactly. |

**Gaps / notes:**
- The `@pytest.mark.live` smoke test did not exist before Phase 5 (marker was
  declared but no test used it) — added this phase to satisfy D9 item 5.
- The NLI premise/hypothesis direction bug was found and fixed this phase
  (commit `579dc7c`); it was latent through Phases 1–4 because the model never
  downloaded. All 150 tests (146 offline + 4 NLI) now pass.
- Nothing committed by this phase except the NLI fix (`579dc7c`); git is
  handled by the main agent.
