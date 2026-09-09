# Phase B — Threshold Calibration Findings

Date: 2026-09-09 · Method: ran `evals/run_eval.py --nli` over the 41-item independent set,
then swept support/ambiguous thresholds over the saved tier-3 scores.

## Headline: thresholds are NOT the accuracy lever

Current thresholds (support ≥ 0.7, ambiguous ≥ 0.3) give 24/36 = 66.7% agreement among
tier-3 items. An exhaustive sweep of support ∈ [0.30,0.90] × ambiguous ∈ [0.05,support)
finds a best of **25/36 = 69.4%** (at support ≥ 0.50) — a gain of exactly ONE item.

## Why: the score distribution is bimodal with the errors inside the clusters

```
expected=supported   : 0.002 0.003 0.003 0.015 0.025 0.050 | 0.524 | 0.960 0.983 0.990 0.994 0.995 0.996 0.996 0.996 0.998
expected=unsupported : 0.000×9 0.001×3 0.004 0.005 0.017 | 0.914
```

There is a wide empty band between 0.05 and 0.524, and another between 0.524 and 0.96.
The 7 wrongly-scored *supported* items cluster at 0.002–0.05 (bottom of the supported
range); the 1 wrongly-scored *unsupported* item sits at 0.914 (top of the unsupported
range). They are on the WRONG side of the gaps, so moving the cut points cannot rescue
them. Threshold tuning is a dead end for accuracy.

## Root cause: overlap-tier passage selection feeds NLI the wrong / too-narrow window

Two distinct failure modes, both in `src/citesure/overlap.py` top-k selection:

1. **Wrong passage picked** — ind-016 claim "lunar EVA lasted 2h31m40s"; the highest
   term-coverage window is the *Mission duration* table row ("8 days, 3 hours..."), not
   the EVA row. NLI correctly scores 0.001. The true EVA sentence exists elsewhere on
   the page but was never selected.
2. **Right passage, too narrow / pronoun-only** — ind-006 claim "BERT obtains SOTA on
   eleven tasks"; selected sentence reads "**It** obtains SOTA on eleven tasks". NLI
   cannot resolve "It"→BERT with no antecedent in the single-sentence window → 0.007.
   Scoring the same sentence with "BERT" explicit gives 0.988. Model is healthy
   (trivial entailment 0.997, contradiction 0.0002).

## Recommendation (feeds the improvement plan)

Do NOT retune thresholds. Fix passage selection instead:
- Expand each candidate window to include surrounding sentences (antecedent context) so
  pronouns resolve.
- Rank by more than raw term coverage — prefer windows containing the claim's entity
  terms AND numeric tokens together; consider scoring ALL top-k passages through NLI and
  taking the max entailment rather than trusting the single top-1 window.

## NLI-ON vs NLI-OFF (same 41-item set)

| metric | NLI OFF | NLI ON |
|---|---|---|
| overall agreement | 53.7% (22/41) | 70.7% (29/41) |
| negation-flip false-supported | 5/5 = 100% | 1/5 = 20% |
| entity-swap false-supported | 5/5 = 100% | 0/5 = 0% |
| supported recall | 16/16 = 100% | 9/16 = 56% |
| unsupported recall | 0/16 = 0% | 15/16 = 94% |

NLI is a net win on SAFETY (the asymmetric cost that matters most) but regresses
supported-recall via the passage-selection bug above. Both tiers share the same
passage-selection weakness; fixing it should lift NLI-ON supported recall without
sacrificing the safety gains.
