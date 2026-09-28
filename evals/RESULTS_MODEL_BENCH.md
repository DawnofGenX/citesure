# NLI Checkpoint Benchmark — Independent Set v2

**Date:** 2026-09-28
**Set:** `evals/independent_set_v2.json` — 108 cases, 40 unique URLs
**Harness:** `evals/bench_models.py`, shared page cache `/tmp/citesure-v2`, CPU
**Data:** `evals/results/bench_baseline_v2.json`, `evals/results/bench_candidates_v2.json`

---

## Result: DO NOT SWAP

The model swap proposed in `.hermes/plans/2026-09-27_190000-nli-model-swap.md`
is **not supported by measurement on this eval set.** The current default stays.

## Measured comparison

| Model | Agreement | s/case | neg-flip false-supp | entity-swap false-supp |
|---|---|---|---|---|
| **`nli-deberta-v3-base`** (current) | 83/108 = **76.85%** | 1.10 | **4/32** | 7/32 |
| `DeBERTa-v3-base-mnli-fever-anli` | 79/108 = 73.15% | 0.93 | **11/32** | 6/32 |
| `DeBERTa-v3-large-mnli-fever-anli-ling-wanli` | 82/108 = 75.93% | 2.97 | 7/32 | **2/32** |
| `nli-deberta-v3-large` | 86/108 = **79.63%** | 3.15 | 4/32 | 7/32 |

### Per-category recall

| Model | verbatim | negation-flip | entity-swap | ambiguous | dead | paywalled |
|---|---|---|---|---|---|---|
| **current (base)** | 22/32 | **28/32** | **25/32** | 0/4 | 4/4 | 4/4 |
| MoritzLaurer base | **30/32** | **16/32** | 25/32 | 0/4 | 4/4 | 4/4 |
| MoritzLaurer large | 29/32 | 20/32 | 25/32 | 0/4 | 4/4 | 4/4 |
| nli-deberta-v3-large | 27/32 | 28/32 | 23/32 | 0/4 | 4/4 | 4/4 |

---

## Paired significance (McNemar, exact binomial)

Aggregate agreement differences on 108 cases are not meaningful on their own.
The paired test asks: on cases where the two models disagree, is the win/loss
split balanced?

| Candidate | Gains | Losses | Discordant | Exact two-sided p | Verdict |
|---|---|---|---|---|---|
| MoritzLaurer base | 12 | 16 | 28 | 0.5716 | not significant |
| MoritzLaurer large | 14 | 15 | 29 | 1.0000 | not significant |
| **nli-deberta-v3-large** | 13 | 10 | 23 | **0.6776** | **not significant** |

**No candidate is statistically distinguishable from the current model.**

The large model's +2.8pt headline comes from a 13-10 split — well within noise
for n=23 discordant pairs. At 3.15s/case it costs **2.9x the CPU time** for a
difference that could plausibly be zero.

---

## Why the published ranking inverts here

The swap was motivated by aggregate benchmark tables, where
`MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` beat `nli-deberta-v3-base` by
+1.8pt MNLI and +27.8pt ANLI-R1 at identical size. **On this eval set it is the
worst of the four.**

The per-category split shows exactly why:

| | verbatim | negation-flip |
|---|---|---|
| current base | 22/32 | 28/32 |
| MoritzLaurer base | **30/32** | **16/32** |

It is markedly better at *asserting* support (+8 verbatim) and markedly worse at
*refusing* false claims (−12 negation-flip), driving false-supported negations
from 4/32 to **11/32 — nearly triple the current rate.**

Aggregate NLI benchmarks measure general entailment. This eval measures a
specific combination: verbatim recall, negation refusal, and entity binding
together. The published metrics do not capture that trade-off, so they are a
proxy for the target rather than the target itself.

**Decision rule #1 (reject any model that worsens entity-swap) was not the
binding constraint** — MoritzLaurer base is in fact marginally better on
entity-swap (6/32). It is rejected on overall accuracy and on a severe
negation-safety regression instead.

---

## The one candidate worth revisiting

`nli-deberta-v3-large` is the only model to beat the current one (79.6% vs
76.9%) while holding negation false-supported flat at 4/32. It also shows the
best entity-swap safety of any large model (7/32, vs 2/32 for MoritzLaurer
large — the best of all four).

It is **not** adopted now, because the paired test cannot distinguish it. It
becomes worth revisiting if either holds:
- the eval set grows materially (see open questions), or
- CPU budget allows 3x inference cost for a possibly-real +2.8pt

---

## Harness note: a bug that produced a false negative

The first candidate run scored **0/108 for all three models** with 0/32 on both
safety metrics. Taken literally that reads as "all three are catastrophically
bad." They had never loaded.

Cause: citesure passes `CITECHECK_CACHE_DIR` to `from_pretrained(cache_dir=...)`
(`src/citesure/nli.py:163`), so setting the page cache to `/tmp/citesure-v2`
also redirected where transformers looks for model **weights**, shadowing the
real HF cache. Fixed by resolving each repo id to its absolute snapshot path
(`resolve_model()`), and the harness now persists per-case records and prints an
error count so a total-load failure can never again read as a quality result.

---

## Label-order safety

The two model families ship **opposite** `id2label` orders:

| Family | id2label |
|---|---|
| `cross-encoder/*` | `{0: contradiction, 1: entailment, 2: neutral}` |
| `MoritzLaurer/*` | `{0: entailment, 1: neutral, 2: contradiction}` |

`_label_index()` resolves indices from label **names**, so no code change is
needed to swap. `tests/test_nli.py::test_label_index_resolves_by_name_not_position`
now pins both orders. A positional implementation would read the neutral column
as entailment on any MoritzLaurer model — which would *raise* the agreement
score rather than lower it, making the failure invisible to a naive check.

---

## Reproduce

```bash
cd /home/hermes/citesure
.venv/bin/python evals/bench_models.py \
  --models cross-encoder/nli-deberta-v3-base \
           MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli \
           MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli \
           cross-encoder/nli-deberta-v3-large \
  --set evals/independent_set_v2.json --cache /tmp/citesure-v2 \
  --out evals/results/bench_candidates_v2.json
```

---

## Open questions

1. **108 cases cannot resolve a 3-case difference.** Detecting a +2.8pt effect at
   this magnitude needs roughly 500+ cases, or paired evaluation on a much larger
   set. Until then, model selection on this project should be driven by the
   safety metrics (which are large, stable effects) rather than aggregate
   agreement.
2. **The ambiguous category is 0/4 for every model** — no checkpoint helps. It is
   a pipeline design gap, not a model problem.
3. **verbatim-supported is the weakest positive category** for the current model
   (22/32). MoritzLaurer fixes 8 of those 10 misses but pays for it in negation
   safety. An ensemble or a category-aware routing rule is a possible direction,
   though it adds complexity that must be earned.
