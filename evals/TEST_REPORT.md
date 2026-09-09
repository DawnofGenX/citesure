# citesure — Accuracy & Robustness Test Report

Date: 2026-09-09 · Scope: full 7-phase test plan (A–G) · Verdict: **shippable with one
known accuracy limitation** (see §Headline). All 8 discovered defects fixed and
regression-locked; final gate green.

## Headline

citesure is **robust and bug-free** after Phase F (215 tests green, 6/6 install combos
pass, MCP + CLI + NLI all verified). Its **accuracy is tier-dependent**:

- **Tier 1 (reachability) + Tier 2 (overlap) alone are NOT a safe support gate.** On the
  independent 41-case set they score 53.7% agreement and return `supported` for **100%** of
  negation-flips and entity-swaps. The overlap tier is a *topical* matcher, not a
  *propositional* one — it cannot see that a claim contradicts or misattributes the source.
- **Tier 3 (NLI) fixes the safety hole** (negation-flip false-supported drops 100%→20%,
  entity-swap 100%→0%) but **introduces a passage-selection regression**: it scores true
  claims against the *wrong* sentence window, dropping supported-recall to 75%.

**Bottom line:** run citesure **with `--nli`** for any real support decision. The overlap
tier should be treated as a fast pre-filter / evidence locator, never as the final yes/no.
The single highest-value improvement is fixing overlap-tier passage selection so NLI scores
the right sentence (see improvement plan, IMP-1).

## Results by phase

| Phase | What | Result |
|-------|------|--------|
| A | Independent 41-case accuracy eval (NLI off) | 53.7% agreement; false-supported 100% on negation-flip & entity-swap |
| B | Threshold calibration (NLI on) | 61.0% agreement; best threshold gains only +1 item → passage-selection bound, not threshold-bound |
| C | Fuzzing + robustness + MCP stress | 7 real bugs (BUG-1..7); 6 new test files (1248 ln); suite 208 passed / 7 xfail |
| D | Performance baselines | reachability 0.06 s · overlap 0.09 s · NLI 0.43 s (+21 MB RSS) per 5-citation batch |
| E | Install matrix (py3.10/3.11/3.12 × wheel/sdist) | py3.10 FAILS (missing `lxml_html_clean`) = BUG-8; 3.11/3.12 pass |
| F | Bug-fix loop | All 8 fixed; suite **215 passed**; matrix **6/6 PASS** |
| G | Final gate | offline 215 ✓ · live smoke ✓ · NLI 4 ✓ · CLI demo ✓ · MCP 3 ✓ · `--strict` semantics ✓ |

## Accuracy detail (Phase A/B)

Independent set: 41 hand-labeled real-world claim/URL pairs across 9 categories
(verbatim, paraphrase, numeric, false-claim, negation-flip, entity-swap, partially-true,
dead-link, paywalled). Sources the code never saw: docs.python.org, python.org, Wikipedia,
MDN, arXiv, NASA, SCMP. Every label verified present/absent in trafilatura-extracted text.

| Metric | NLI off | NLI on |
|--------|---------|--------|
| Agreement | 22/41 = 53.7% | 25/41 = 61.0% |
| False-supported, negation-flip | 5/5 = 100% | 1/5 = 20% |
| False-supported, entity-swap | 5/5 = 100% | 0/5 = 0% |
| Supported recall | 16/16 = 100% | 12/16 = 75% |
| Unreachable / paywalled P=R | 1.000 | 1.000 |

Root causes of the NLI-on regressions (all passage-selection, none model-quality):
- **Pronoun windows:** claim "BERT obtains SOTA" scored against "**It** obtains SOTA…" —
  NLI can't resolve "It"→BERT in a single-sentence window (explicit "BERT" scores 0.988).
- **Wrong row:** numeric claims matched the wrong table row (e.g. mission-duration row
  instead of the EVA-duration row); NLI correctly reports non-entailment of the wrong text.

Threshold sweep over saved tier-3 scores: distribution is bimodal with a wide empty gap
(0.05 → 0.524 → 0.96); best threshold (0.50/0.45) recovers only +1 item because the
misclassified items sit on the *wrong side* of the gap. **No threshold closes the gap.**

## Robustness detail (Phase C)

7 defects, each with an offline repro (full log: `/tmp/citesure-bugs.md`):
BUG-1 malformed IPv6 URL raises; BUG-2 NUL-byte URL raises; BUG-3 renderer crashes on
string score; BUG-4 HTTP 429 not retried; BUG-5 cache key not URL-normalized; BUG-6
`~unknown-user` path raises; BUG-7 global semaphore deadlocks across event loops.
Plus 6 new test files: fuzz_extraction, fuzz_report, fuzz_urls, edge_cases, concurrency,
mcp_stress. Mock server extended with `/slow`, `/disconnect`, `/ratelimit`.

## Performance (Phase D)

Per 5-citation batch, warm cache: reachability-only 0.06 s, +overlap 0.09 s, +NLI 0.43 s
with +21.2 MB RSS (model load dominates; steady-state NLI inference is sub-second).
Baseline stored at `evals/results/perf_baseline.json` for future regression detection.

## Install matrix (Phase E → F)

Pre-fix: py3.10 wheel+sdist FAIL at import (`ImportError: lxml.html.clean … separate
project lxml_html_clean`); py3.11/3.12 pass (resolver happened to pull the dep). Post-fix
(`lxml_html_clean>=0.1` declared): **all 6 combos PASS**. See
`evals/results/install_matrix.tsv` (pre) and `install_matrix_postfix.tsv` (post).

## Final gate (Phase G)

- Offline suite: **215 passed**, 5 deselected (live/nli markers)
- Live network smoke: 1 passed
- NLI live (real DeBERTa-v3): 4 passed
- CLI demo (`verify sample.md`): PASS, pass_rate 0.8, exit 0
- MCP real-client integration: 3 passed
- `--strict` semantics: verified exit 1 on `unsupported`; `unreachable` correctly excluded
  from strict-failure per D3

## Artifacts

- `evals/independent_set.json` — 41-case ground-truth set
- `evals/run_eval.py` — confusion-matrix + safety-metric runner
- `evals/EVAL_REPORT.md` — Phase A full report (all 19 mismatches w/ root cause)
- `evals/THRESHOLD_CALIBRATION.md` — Phase B sweep
- `evals/perf_baseline.py` + `results/perf_baseline.json` — Phase D
- `evals/INSTALL_MATRIX.md` + `results/install_matrix*.tsv` — Phase E/F
- `evals/PHASE_F_BUGFIXES.md` — fix table + verification
- `tests/test_{fuzz_*,edge_cases,concurrency,mcp_stress}.py` — 1248 lines of regression tests
