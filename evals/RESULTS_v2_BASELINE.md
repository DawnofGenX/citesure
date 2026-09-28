# Independent Eval Set v2 — Baseline Results

**Date:** 2026-09-28
**Set:** `evals/independent_set_v2.json` — 108 cases, 40 unique URLs, 13 domains
**Model:** `cross-encoder/nli-deberta-v3-base` (unchanged from v1 baseline)
**Code state:** `888ea87`

---

## Why v2 exists

The v1 set had 41 cases but only **16 unique URLs**. The top 4 pages carried 51% of
all cases, so v1 accuracy partly measured per-claim extraction on four pages rather
than general citation verification. v2 fixes this with a **three-way contrast
design**: each page contributes the *same underlying fact* three times —

| Case | Category | Construction | Isolates |
|---|---|---|---|
| A | `verbatim-supported` | the fact as stated | baseline |
| B | `negation-flip` | A with polarity reversed | A−B = negation handling |
| C | `entity-swap` | A's detail re-attributed | A−C = subject binding |

Because A, B and C share one source sentence, a drop in A−B is unambiguously a
polarity bug and A−C is unambiguously a binding bug. v1 confounded these — its
negatives came from different sentences, pages and subjects.

---

## Headline results

| Run | Agreement | v1 equivalent |
|---|---|---|
| v2, NLI **on** | **83/108 = 76.9%** | 32/41 = 78.0% |
| v2, NLI **off** | **41/108 = 38.0%** | 22/41 = 53.7% |

The two sets are **not directly comparable** — different domains, different
difficulty, different case mix. Only within-set before/after is meaningful.

### Safety metrics

| Metric | v1 | v2 NLI-on | v2 NLI-off |
|---|---|---|---|
| negation-flip false-supported | 1/5 = 20% | 4/32 = **12.5%** | 32/32 = 100% |
| entity-swap false-supported | 0/5 = 0% | 7/32 = **21.9%** | 29/32 = 90.6% |

### Per-category recall (NLI on)

| Category | v2 | NLI-off |
|---|---|---|
| dead-link | 4/4 = 1.00 | 1.00 |
| paywalled | 4/4 = 1.00 | 1.00 |
| negation-flip | 28/32 = 0.875 | **0.00** |
| entity-swap | 25/32 = 0.781 | **0.00** |
| verbatim-supported | 22/32 = 0.688 | 1.00 |
| partially-true-ambiguous | **0/4 = 0.00** | 0.25 |

---

## The NLI-off result validates the set design

Without NLI the pipeline scores **0/32 on negation-flip and 0/32 on entity-swap**
while scoring **32/32 on verbatim-supported**.

That is the signature of negatives built to require actual reasoning: each shares
its fact, entities and detail with the page, and differs only in polarity or
subject binding. A bag-of-words matcher cannot separate them by construction.

v1's NLI-off figure of 53.7% was not wrong, but it was suspiciously high for a
lexical matcher — with only 16 URLs there was less opportunity for genuine
contrast. v2's negatives are demonstrably harder.

---

## The three-way contrast: polarity vs binding

`evals/contrast_report.py evals/results/v2_baseline`

| Verdict | Pages | Meaning |
|---|---|---|
| clean | **14** | A, B and C all correct — polarity and binding both fine |
| miss | **10** | A itself wrong — selection/extraction failure |
| binding-bug | **4** | A correct, entity-swap false-supported |
| polarity-bug | **2** | A correct, negation-flip false-supported |
| polarity-bug + binding-bug | **2** | A correct, both negatives false-supported |

**This is the first measurement in the project's history that separates polarity
failures from binding failures.** v1 could not distinguish them.

**Binding failures (6 pages)** — the pipeline asserts a fact whose detail is real
but whose subject is wrong:
- MDN HTTP/Headers, Django models, asyncio-task, asyncio
- arXiv 1308.0853, Marie Curie (both also polarity)

**Polarity failures (4 pages)** — the pipeline asserts a negated fact:
- itertools, Mount Everest
- arXiv 1308.0853, Marie Curie (both also binding)

**Binding failures outnumber polarity failures roughly 2:1** (6 pages vs 4). This
was not measurable in v1. The implication is that entity binding deserves at
least as much attention as negation handling going forward.

---

## Weakest category: partially-true-ambiguous

**0/4 correct (NLI on), 0.25 (NLI off)** — identical weakness to v1, now with
double the cases. The four failures split both ways:

- **False-supported** (ind-250, ind-251): the claim's clauses are all verbatim
  true, so the pipeline matches confidently (scores 0.954, 0.996) and never
  surfaces the contested premise — a superlative ("first woman entombed on her
  own merits", "first railway journey") or a loose attribution ("discovered").
- **Missed as unsupported** (ind-249, ind-252): the model scores these 0.003 and
  0.0007, treating a compound claim as unsupported because it cannot align the
  whole proposition.

The category needs a distinct mechanism — the pipeline has no representation for
"a claim whose parts are separately true but whose conjunction asserts something
contested." 8 cases across v1+v2 make this the clearest single target for future
work.

---

## Second-weakest: verbatim-supported at 0.688

10 of 32 verbatim cases are missed, but **not for one reason** — the scores split
into three distinct failure modes:

| Score band | Cases | Likely cause |
|---|---|---|
| ~0.002–0.008 | 8 (ind-104, 131, 161, 164, 179, 185, 191, 194) | **selection or extraction failure** — near-zero means the model never saw the claim's sentence in the selected passage |
| ~0.115 | 1 (ind-128) | partial/ambiguous match |
| ~0.676 | 1 (ind-101) | borderline NLI judgement on the ECMA spec text |

The 8 near-zero cases are the actionable group: the claim is verbatim in the
extracted text, so a correct pipeline should score them `supported`, and the
model returning ~0.002 indicates the claim's sentence is not reaching the NLI
input at all. Contributing pages include ResNet, Steam engine, Mars, GELU, Adam,
os.path.normcase.

This is the same class of failure as v1's ind-012 (claim absent from extraction).
It is worth a targeted investigation into whether these sentences are being
segmented out of the passage — or ranked below a competing passage — before NLI
sees them. Note the counter-example ind-101 at 0.676: a low score is not
automatically an extraction bug, so any fix should be measured per case rather
than inferred from the score alone.

---

## What the set does and does not measure

v2 samples **stable, fetchable, English, mostly-technical pages** across 13
domains. It is **not** a random sample of the web, and its numbers will not
transfer to paywalled news, PDFs, or non-English sources.

**Thin coverage, stated plainly:**
- `gov-science` has 1 usable URL (noaa.gov and usgs.gov both unreachable from here)
- 4 paywalled cases is a small sample for a distinct sub-problem
- No non-English and no PDF cases at all
- Wikipedia is 8 of 40 URLs, so wiki-specific extraction quirks may be
  over-represented relative to real citation traffic

---

## Reproduce

```bash
cd /home/hermes/citesure
CITECHECK_NLI_MODEL=/home/hermes/.cache/huggingface/hub/models--cross-encoder--nli-deberta-v3-base/snapshots/6c749ce3425cd33b46d187e45b92bbf96ee12ec7 \
CITECHECK_CACHE_DIR=/tmp/citesure-v2 .venv/bin/python evals/run_eval.py \
  --set evals/independent_set_v2.json --nli --out evals/results/v2_baseline

.venv/bin/python evals/validate_v2.py                              # 0 problems
.venv/bin/python evals/contrast_report.py evals/results/v2_baseline
```

Page text is regenerated by `evals/build_page_pool.py` and is gitignored.
