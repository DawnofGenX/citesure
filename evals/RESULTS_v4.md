# v4 — held-out measurement on fresh domains

Set: `evals/independent_set_v4.json` — **76 cases over 11 pages on 11 domains** that v1, v2 and v3
never touch, built reproducibly by `evals/build_v4.py`. Authoring rules and the dropped-source
list: `AUTHORING_V4.md`.

Families: 22 verbatim-supported, 20 negation-flip, 22 entity-swap, 10 mixed-false-conjunct,
2 dead-link.

## Headline

| Configuration | Overall | Cluster bootstrap (by URL) |
|---|---|---|
| tiers 1+2, no NLI | **22/76 = 28.9%** | — |
| all tiers, NLI on (default path) | **64/76 = 84.2%** | **[76.0, 92.4]** |

Against v2's in-sample 86.1% (CI [78.3, 91.4]), the held-out figure is **1.9 points lower** and the
intervals overlap. On the 74 cases whose labels are verifiable against the text the product
actually fetched, it is **64/74 = 86.5%**. The in-sample number was not inflated to any material
degree — which is not what the previous version of this document said, and the reason is below.

| Family | v2 (in-sample) | v4 (held-out) |
|---|---|---|
| negation-flip | 32/32 = 100% | **20/20 = 100%** |
| entity-swap | 27/32 = 84% | **17/22 = 77%** |
| verbatim-supported | 26/26 = 100% | **15/22 = 68%** |
| mixed-false-conjunct | — (new family) | **10/10 = 100%** |
| dead-link | 8/8 | **2/2** |

## The correction this version carries

The previous version of this document reported **71.1%** and called the mixed class "the clearest
product gap: 0/11". **That was a labelling error in this set, mine, and it inverted the conclusion.**

The family was built as a verbatim clause A joined to clause B, where B is a *negated copy of a
sentence that is in the page*. The label was `ambiguous`, on the assumption that "half true" means
"indeterminate". It does not:

- the claim asserts `A ∧ ¬B`;
- the page asserts `B`;
- so the conjunction is **contradicted by the page** — it is false, not undecidable;
- `unsupported` is therefore the correct verdict, and citesure returned exactly that for all 11.

Measured on the shipped set, both directions: the affirmative form of clause B is present in the
page in **9 of 11** cases and the negated form in **0 of 11**.

The family is now `mixed-false-conjunct` with `expected_status: unsupported`, and it passes 10/10.
The earlier "0/11 gap" was the test being wrong, not the product — and the clause-mixture rule that
was written and reverted to "fix" it (`RESULTS_CLAUSE_MIXTURE.md`) was, in effect, demoting correct
verdicts: consistent with it costing 20 correctly-`unsupported` cases while "gaining" 8.

**Genuine ambiguity is still untested here.** It is not mechanically constructible the way the
other families are, because it requires a claim that the page neither supports nor contradicts.
v3's hand-labelled class remains the only test of it (1/10).

## Two real gaps that survive

**1. Entity swapping still fails, sometimes confidently.** 5 of 22 swapped-entity claims are
reported `supported`. This is the class an attacker uses, and the class the 2026-10-06 probe tried
and failed to improve on v2 — now confirmed on material nobody tuned against.

**2. Verbatim claims are demoted — 7 of 22.** Split by cause:

| Cause | Count | Evidence |
|---|---|---|
| Claim **is** in the text the verifier fetched; NLI rejects it anyway | **5** | claim found in `~/.cache/citesure-eval/*.json` |
| Claim is **not** in that text (extraction mismatch on the page) | **2** | `doc.rust-lang.org`: pipeline text 6,722 chars, missing the chapter's opening line |

Both numbers stand: dropping the two silently would be the post-hoc tidying this repo's own docs
warn about. The 5 are a real NLI false-negative rate on long sentences from unfamiliar domains.

## Build bugs found along the way (recorded, not hidden)

1. **The extractor did not match the product's.** The first build used
   `trafilatura.extract(..., favor_precision=True)`; the product calls
   `trafilatura.extract(html, include_comments=False)` (`src/citesure/fetcher.py:431`). Some claims
   were verbatim in the builder's copy and absent from the verifier's. Rebuilt against the
   verifier's extraction.
2. **The mixed family was silently truncated.** `--per-page` defaulted to 5 while the authoring
   emits up to 7, so the last family was cut off in every earlier build. Two builds reported "no
   mixed cases" for that reason alone. A set can lose a whole family and still look complete.
3. **The mixed label was wrong** (above). This is the third instance in this lineage of a
   plausible-but-unverifiable label — v2's ambiguous class and v3's first drafting pass were the
   others. Constructive labelling reduces the risk but does not remove it: the label still rests on
   an argument about what a verdict *should* be, and that argument can be wrong even when the
   construction is mechanical.

## What v4 does not claim

- **No ambiguity class.** See above; v3 is the only test of it.
- **76 cases over 11 domains is small.** 13 clusters. Treat 84.2 vs 86.1 as "no material gap
  detected", not as a precise ranking.
- **Single author, constructive labels.** No inter-annotator agreement is measured.
- **Live pages.** Re-fetching later can legitimately change a verdict.

## Reproduce

```bash
.venv/bin/python evals/build_v4.py                                                    # rebuild the set
.venv/bin/python evals/run_eval.py --set evals/independent_set_v4.json --out evals/results/v4_tiers12
.venv/bin/python evals/run_eval.py --set evals/independent_set_v4.json --nli --out evals/results/v4_nli
```
