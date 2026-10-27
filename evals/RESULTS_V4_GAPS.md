# v4 gaps closed (2026-10-27) — verbatim demotions and entity-swap false supports

Two defects recorded in `RESULTS_v4.md` and in the portfolio improvement backlog (§E1, §E2)
were worked in this session. Both are now fixed, with the limits of each fix measured and
stated rather than implied.

Run artefacts (committed):

| File | What it is |
|---|---|
| `evals/results/v4_nli_replay/20261027-071627.json` | replay gate — baseline reproduced |
| `evals/results/v4_fix2_2026-10-27/20261027-073155.json` | both fixes on the same set |
| `evals/results/v2_nli_replay/20261027-072228.json` | v2 (in-sample, 108 cases) baseline on current code |
| `evals/results/v2_fix_2026-10-27/` | v2 with both fixes — regression check |

## Replay gate (run before any change)

The v4 baseline reproduces **exactly**: 64/76 = 84.2%, entity-swap false-supported 5/22,
negation-flip false-supported 0/20 — **zero per-case differences** against the committed
`evals/results/v4_nli/` run, on pages re-fetched from the live sites. The v2 baseline
reproduces its documented 93/108 = 86.1% with safety (0, 5). So every comparison below is
against a verified baseline, not a drifted one.

## Result

| Configuration | v4 agreement | Wilson 95% CI | Cluster bootstrap (13 URLs) |
|---|---|---|---|
| baseline (before this session) | 64/76 = 84.2% | [74.4, 90.7] | [76.0, 92.4] |
| entity-mismatch guard only | 69/76 = 90.8% | [82.2, 95.5] | — |
| **both fixes** | **74/76 = 97.4%** | **[90.9, 99.3]** | **[91.8, 100.0]** |

Per family:

| Family | before | after |
|---|---|---|
| negation-flip | 20/20 | **20/20** |
| entity-swap | 17/22 (5 false-supported) | **22/22 (0 false-supported)** |
| verbatim-supported | 15/22 | **20/22** |
| mixed-false-conjunct | 10/10 | **10/10** |
| dead-link | 2/2 | **2/2** |

Gain/loss ledger on the same 76 cases: **gained 10, lost 0**. Paired exact McNemar on
(10, 0) discordant pairs: p = 0.002. Both fixes are one-directional by construction —
a veto can only remove `supported`, and the added premise can only raise max entailment —
so "lost 0" is structural, not luck; what the numbers still have to establish is whether
each mechanism is *right*, which is the section after next.

## Floor: what 97.4% is worth

The set's label distribution is skewed and the report must say so:

| Trivial baseline | Score | Wilson 95% CI |
|---|---|---|
| always `unsupported` (majority class) | 52/76 = **68.4%** | [57.3, 77.8] |
| always `supported` | 22/76 = 28.9% | — |
| always `unreachable` | 2/76 = 2.6% | — |

So the attributable gap is **29.0 points** (97.4 − 68.4), not 97.4. A balanced set would put
the floor near 50% and widen the gap; 35 of the 76 cases expect `unsupported`, so this floor
is a property of the set design, not of the verifier. `RESULTS_v4.md` published no floor at
all — that omission is corrected here.

## Fix 1 — verbatim demotions (presentation layer)

**Diagnosis first, by layer.** All 7 demoted `verbatim-supported` cases had the claim present
in the saved page text as one contiguous span covering 100% of the claim's tokens:

| Case | NLI score before | in page (span) |
|---|---|---|
| ind4-001 | 0.022 | yes, whole claim |
| ind4-008 | 0.004 | no — production text is 6,722 chars and omits it (extraction) |
| ind4-009 | 0.000 | no — same page, extraction |
| ind4-022 | 0.281 | yes |
| ind4-030 | 0.143 | yes |
| ind4-043 | 0.390 | yes |
| ind4-061 | 0.007 | yes |

So 5 are fixable and 2 are `doc.rust-lang.org` extraction artefacts — the split `RESULTS_v4.md`
claimed, now confirmed case by case.

**Root cause, measured:** the pipeline scored the best-term-coverage sentence (not the claim's
sentence), then expanded it by one sentence on each side "for pronoun resolution". For an exact
quote, that expansion destroys the cross-encoder's judgement:

| Case | premise = the claim's sentence alone | premise = that sentence + 2 neighbours |
|---|---|---|
| ind4-001 | **0.9911** | 0.0223 |
| ind4-022 | **0.9941** | 0.2812 |
| ind4-030 | **0.9919** | 0.0116 |
| ind4-043 | **0.9894** | 0.3901 |
| ind4-061 | **0.9827** | 0.0068 |

Every one of these claims was demoted on a premise that *contained the claim verbatim* —
a premise-shape defect, not a model limitation.

**Fix:** `find_verbatim_span()` (overlap.py) returns the page's own copy of the claim when one
contiguous token run covers the whole claim (≥10 tokens, coverage 1.0). That span is added as
one extra candidate premise, unexpanded. Max-entailment then does the rest; no new verdict rule.

**Why coverage must be 1.0, not 0.9:** a sweep over the rule as pure arithmetic on committed
scores and saved page text gave, on v4: coverage 1.0 → 22 fires / 7 gains / **0 wrong**;
coverage 0.8 → 66 fires / 7 gains / **39 wrong**. Half a sentence shares most of its vocabulary
with the page, so anything below full coverage turns the rule into a false-support generator.
A unit test asserts the 0.9-style case fails (`test_verbatim_span_none_for_entity_swap`).

## Fix 2 — entity-swap false supports (entity-mismatch guard)

**Root cause:** all 5 false-supports are one name swapped inside an otherwise verbatim sentence,
scored 0.89–0.996 by the cross-encoder:

| Case | claim says | page says |
|---|---|---|
| ind4-033 | "Overview **Marlowe** air pollution…" | "Overview **Outdoor** air pollution…" |
| ind4-046 | "…in the **Zenith** States…" | "…in the **United** States…" |
| ind4-058 | "The **Marlowe** of Cancer…" | "The **Definition** of Cancer…" |
| ind4-059 | "…between **Zenith** Cells and…" | "…between **Cancer** Cells and…" |
| ind4-066 | "…our **Zenith** of Use…" | "…our **Terms** of Use…" |

**Fix:** `ungrounded_proper_nouns()` — any proper noun the claim names that never occurs in the
fetched page vetoes a `supported` verdict (terminal, at any tier depth). This is the
entity-mismatch guard of Longpre et al. (Findings EMNLP 2021, arXiv:2109.05052), and it also
catches sentence-initial cases ("**This Marlowe** …") by stripping leading determiners instead
of dropping the phrase.

The whole-page comparison is what makes it usable. An earlier probe measured the same idea
against the *evidence window*: 5/5 fixes but 11/26 false rejects, because a clipped snippet
legitimately omits names the page mentions elsewhere. Against the full fetched text, as a veto
on `supported` only: v4 fixes **5/5** with **0** correctly-supported cases lost; on v2 it fixes
**0** of that set's 7 false-supports (`entity_swap_false_supported` 5 -> 5, unchanged) and
**0** correctly-supported cases lost.

## Regression on v2 (in-sample, 108 cases)

| Configuration | v2 agreement | Wilson 95% CI | Cluster bootstrap (40 URLs) | neg-flip FS | entity-swap FS |
|---|---|---|---|---|---|
| baseline on current code | 93/108 = 86.1% | — | — | 0/32 | 5/32 |
| both fixes | **96/108 = 88.9%** | [81.6, 93.5] | [83.3, 94.2] | 0/32 | 5/32 |

Gained 3 (`ind-101`, `ind-104`, `ind-179`), lost 0. The three gains are the verbatim-span fix
working on real pages, i.e. it is not confined to the family built to describe it. Note what did
NOT move: **v2's five real-name false-supports are untouched**, which is the central limitation
stated below.

## What these fixes do NOT establish — read this before quoting 97.4%

1. **The v4 entity-swap family is built from a fixed fake-name pool.** v4 cheats in the
   fix's favour: every swap substitutes `Marlowe / Zenith / Northwind / Calder / Halcyon /
   Tessellate`, none of which occurs in any source page, so "the claim names an entity the page
   never mentions" is **true by construction for all 22 swaps**. Measured against v2's hand-built
   swaps — real names that DO appear on their pages (ReLU/GELU, HTTP/2 vs HTTP/1.X,
   Sobolev/Besov, ForeignKey many-to-many, asyncio) — the guard catches **0 of 7** false-supports.
   The real shape, *the contradicted slot is present in the evidence but bound to a different
   subject*, is **still open**. This is the fourth mechanism class attempted on it; the previous
   four (RESULTS_ENTITY_SWAP_PROBE.md) also failed.
2. **The verbatim fix is close to definitional.** The v4 `verbatim-supported` family is
   *constructed* as "the claim is a contiguous span of the page", so a fix keyed on exactly that
   property makes the family pass by construction. That is legitimate — a verifier must not tell
   a user the page does not support its own sentence — but it is not evidence of better judgement
   on paraphrase, negation, or entity binding, which are untouched.
3. **A page can quote a claim it refutes.** "Critics claim that X" contains X verbatim. No case
   in v2 or v4 tests that frame, so the verbatim rule's false-positive rate on refutation
   contexts is unmeasured. This is the highest-value missing case class, and it belongs with the
   genuine-ambiguity class that v4 also lacks.
4. **No genuine-ambiguity class in v4** (unchanged; v3's hand-labelled 10-case class remains the
   only test, at 1/10).
5. **The 2 remaining v4 misses are extraction artefacts** on `doc.rust-lang.org` (production text
   is 6,722 chars and omits the chapter's opening line). They stay counted against the set.

## Side finding worth its own experiment

Context expansion (IMP-2, radius 1) is not just irrelevant to exact quotes — on this evidence it
is actively harmful for them, by up to 0.97 of entailment. It may also be costing accuracy on
non-verbatim claims whose supporting sentence gets a distractor attached. That was **not**
measured this session; the cheap test is a v4 + v2 run with the expansion disabled, gated the
same way (no family may lose more than 2 cases).

## Reproduce

```bash
cd ~/citesure
HF_HUB_OFFLINE=1 CITECHECK_CACHE_DIR=$HOME/.cache/citesure-eval \
  .venv/bin/python evals/run_eval.py --set evals/independent_set_v4.json --nli \
  --out evals/results/v4_fix2_2026-10-27 --compare evals/results/v4_nli_replay/20261027-071627.json
HF_HUB_OFFLINE=1 CITECHECK_CACHE_DIR=$HOME/.cache/citesure-eval \
  .venv/bin/python evals/run_eval.py --set evals/independent_set_v2.json --nli \
  --out evals/results/v2_fix_2026-10-27 --compare evals/results/v2_nli_replay/20261027-072228.json
.venv/bin/python -m pytest tests/test_verbatim_and_entity_guard.py -q
```

The unit tests stub the encoder, so they need no model. `tests/test_verbatim_and_entity_guard.py`
fails loudly if either mechanism is removed.
