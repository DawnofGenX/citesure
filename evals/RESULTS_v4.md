# v4 — held-out measurement on fresh domains

Set: `evals/independent_set_v4.json` — **83 cases over 12 pages on 12 domains** that v1, v2 and v3
never touch, built reproducibly by `evals/build_v4.py`. Authoring rules and the dropped-source
list: `AUTHORING_V4.md`.

Families: 24 verbatim-supported, 22 negation-flip, 24 entity-swap, 11 partially-true-mixed,
2 dead-link.

## Headline

| Configuration | Overall | Cluster bootstrap (by URL) |
|---|---|---|
| tiers 1+2, no NLI | **27/83 = 32.5%** | — |
| all tiers, NLI on (default path) | **59/83 = 71.1%** | **[64.0, 77.9]** |

Against v2's in-sample 86.1% (CI [78.3, 91.4]) the held-out figure is **15 points lower**, and the
intervals do not overlap. That is the honest cost of measuring on material nobody tuned against.

| Family | v2 (in-sample) | v4 (held-out) |
|---|---|---|
| negation-flip | 32/32 = 100% | **22/22 = 100%** |
| entity-swap | 27/32 = 84% | **18/24 = 75%** |
| verbatim-supported | 26/26 = 100% | **17/24 = 70.8%** |
| partially-true-mixed | 0/4 = 0% (v2) · 1/10 (v3) | **0/11 = 0%** |
| dead-link | 8/8 | **2/2** |

## Three findings

**1. Mixed claims are unsolved, and now measured on fresh material.** All 11 half-true claims were
reported `unsupported`. That is the safe direction — nothing false was endorsed — but the wrong
answer, because the claim is neither supported nor refuted. v3 said 1/10 on hand-labelled mixed
cases; v4 says 0/11 on constructively-labelled ones. Two different constructions, same verdict:
this is a product gap, not a labelling artefact.

**2. Entity swapping still fails, sometimes confidently.** 6 of 24 swapped-entity claims are
reported `supported`. It is the strongest of the unsupported families on fresh domains (75%), but
it is the class an attacker uses, and it is the class the 2026-10-06 probe tried and failed to
improve on v2.

**3. Verbatim claims are still demoted — 7 of 24.** Split by cause rather than reported as one
number:

| Cause | Count | Evidence |
|---|---|---|
| Claim **is** in the text the verifier fetched; NLI rejects it anyway | **5** | claim found in `~/.cache/citesure-eval/*.json` |
| Claim is **not** in that text (extraction mismatch on the page) | **2** | `doc.rust-lang.org`: pipeline text 6,722 chars, missing the chapter's opening line |

On the 81 cases whose labels are verifiable against what the product actually saw:
**59/81 = 72.8%**. Both numbers stand: dropping the two silently would be exactly the post-hoc
tidying this repo's own docs warn about.

## Two build bugs found while doing this (recorded, not hidden)

1. **The extractor did not match the product's.** The first build used
   `trafilatura.extract(..., favor_precision=True)`; the product calls
   `trafilatura.extract(html, include_comments=False)` (`src/citesure/fetcher.py:431`). Some claims
   were verbatim in the builder's copy and absent from the verifier's — so "unsupported" was the
   correct product answer and a wrong label. Rebuilt against the verifier's own extraction.
2. **The mixed family was silently truncated.** `--per-page` defaulted to 5 while the authoring
   adds up to 7 (2 verbatim + 2 negation + 2 swap + 1 mixed), so the last family was always cut
   off. Two builds reported "no mixed cases" for that reason alone. The default is now 7 and the
   truncation is noted in the code — a set can lose a whole family and still look complete.

## What v4 does not claim

- **The mixed class is constructed, not adjudicated.** A verbatim clause joined to a negated
  absent clause is "half true" mechanically. Real mixed claims often hinge on framing (a
  superlative, an attribution), which this cannot represent — v3's hand-labelled class remains the
  better test of that, and it scored 1/10.
- **83 cases over 12 domains is small.** 14 clusters; a few points either way is noise. Do not
  treat 71.1 vs 86.1 as a precise ranking, only as evidence that the in-sample figure was
  optimistic.
- **Single author, constructive labels.** No inter-annotator agreement is measured; the 6-gram
  assertions plus the extraction cross-check stand in for it.
- **Live pages.** Re-fetching later can legitimately change a verdict.

## Reproduce

```bash
.venv/bin/python evals/build_v4.py                                                    # rebuild the set
.venv/bin/python evals/run_eval.py --set evals/independent_set_v4.json --out evals/results/v4_tiers12
.venv/bin/python evals/run_eval.py --set evals/independent_set_v4.json --nli --out evals/results/v4_nli
```
