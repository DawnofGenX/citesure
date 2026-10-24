# v4 — first held-out measurement on fresh domains

Set: `evals/independent_set_v4.json` — 61 cases over 12 fresh pages on 12 domains that v1, v2
and v3 never touch, built reproducibly by `evals/build_v4.py` (see `AUTHORING_V4.md` for the
label-construction rules and the dropped source list).

This is the number the 86.1% headline has never had: material that was **not** used to pick the
NLI model, not used to tune the score logic, and not read by anyone before the run.

## Headline

| Configuration | Overall | Cluster bootstrap (by URL) |
|---|---|---|
| tiers 1+2, no NLI | **24/61 = 39.3%** | — |
| all tiers, NLI on (default path) | **48/61 = 78.7%** | **[67.8, 89.2]** |

Against v2's in-sample 86.1% (CI [78.3, 91.4]), the held-out figure is **7.4 points lower** and
its interval overlaps v2's. Read plainly: the headline was optimistic, as the README already
conceded, but it does not collapse on fresh material. Per-family, though, it is not the same
product:

| Family | v2 (in-sample) | v4 (held-out) | Delta |
|---|---|---|---|
| negation-flip | 32/32 = 100% | **21/22 = 95.5%** | −4.5 |
| entity-swap | 27/32 = 84% | **8/13 = 61.5%** | −22.5 |
| verbatim-supported | 26/26 = 100% | **17/24 = 70.8%** | −29.2 (see below) |
| dead-link | 8/8 | **2/2 = 100%** | — |

## The two real findings

**1. Entity-swap is the weak class, and it fails confidently.** 5 of 13 swapped-entity claims are
reported `supported`, at scores of 0.86, 0.97, 0.97, 0.99 and 0.996. These are not borderline
cases that need a better threshold; the model is confidently wrong. This is the same class the
2026-10-06 probe tried and failed to improve on v2, now measured where nobody tuned against it —
which makes it a real product defect rather than a v2 artefact.

**2. Verbatim claims are being demoted — 7 of 24.** A claim that is literally a sentence of the
page is reported `unsupported` or `ambiguous` in 7 cases, at scores as low as 0.0002. I split them
by cause rather than reporting one number:

| Cause | Count | Evidence |
|---|---|---|
| The claim **is** in the text the verifier fetched, and the NLI tier still rejects it | **5** | claim found in `~/.cache/citesure-eval/*.json` |
| The claim is **not** in the text the verifier fetched (extraction mismatch on that page) | **2** | `doc.rust-lang.org` — pipeline text is 6,722 chars and lacks the chapter's opening line |

So the honest split is: **59 cases where the label is verifiable against the text the product
actually saw → 48/59 = 81.4%**, plus 2 cases that are set artefacts, not model errors. Both numbers
are reported because the second one is only knowable after the mismatch was found; dropping those
two cases silently would be exactly the kind of post-hoc tidying this repo's own docs warn about.

## Why the first build was wrong (recorded, not hidden)

The build originally extracted pages with `trafilatura.extract(..., favor_precision=True)`. The
product uses `trafilatura.extract(html, include_comments=False)` (`src/citesure/fetcher.py:431`).
On `doc.rust-lang.org` those two calls return materially different text, so a claim could be
verbatim in the set-builder's copy and absent from the verifier's copy. The set was rebuilt against
the verifier's extraction. Two cases still fail that test, which means extraction is not fully
deterministic across fetch conditions — worth knowing before anyone treats a "supported" label as
free.

## What v4 does not claim

- **No ambiguous/mixed class.** v4 has no case whose label needs human adjudication, so it cannot
  speak to v2's 0/4 or v3's 1/10 on that class.
- **61 cases over 12 domains is small.** The cluster interval is 14 clusters wide; a 7-point
  difference is inside the noise. Do not treat 78.7 vs 86.1 as a ranking.
- **Constructive labels, one author.** No inter-annotator agreement is measured; the 6-gram
  assertions and the extraction cross-check above are what stand in for it.
- **Live pages.** Re-fetching later can legitimately change a verdict.

## Reproduce

```bash
.venv/bin/python evals/build_v4.py                         # rebuild the set (network)
.venv/bin/python evals/run_eval.py --set evals/independent_set_v4.json --out evals/results/v4_tiers12
.venv/bin/python evals/run_eval.py --set evals/independent_set_v4.json --nli --out evals/results/v4_nli
```
