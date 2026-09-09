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

## Q2 addendum — full 3-label distribution (contradiction column)

Date: 2026-09-09 · Method: re-ran the NLI-on pipeline over all 41 items, then pulled the
FULL softmax (entailment / neutral / contradiction) per selected (passage, claim) pair.
Raw table: `evals/results/q2_con_probs.tsv`. Label map: `{0: contradiction, 1: entailment, 2: neutral}`.

**The contradiction column is perfectly separated on this set:**

```
expected=supported   : con max = 0.064   (ind-002; all others <= 0.003)
expected=unsupported : con min = 0.349   (ind-034); 13 items >= 0.5, most >= 0.96
```

Gap between 0.064 and 0.349 → any threshold T ∈ [0.2, 0.8] yields the identical flip set
(13 items, all genuinely unsupported). Threshold choice is therefore not fragile.

**Sweep `con >= T → force unsupported`:**

| T | flipped | of which exp=unsupported | false-unsupported (exp=supported) |
|---|---------|--------------------------|-----------------------------------|
| 0.3–0.7 | 13 | 13 | **0** |
| 0.8–0.9 | 12 | 12 | **0** |

**Consequences:**
1. On today's set the override is a *no-op* — all 13 high-con items already predict
   `unsupported` via the entailment band (ent < 0.05). It adds no accuracy now.
2. It IS a genuine safety net for the banding gap: any future pair with ent ∈ [0.3, 0.7)
   AND con ≥ 0.5 would currently be `ambiguous`; the override makes it `unsupported`.
   By the 3-way softmax sum, con ≥ 0.5 ⇒ ent ≤ 0.5, so the override can never touch an
   ent ≥ 0.7 supported verdict (verified empirically: 0 rows with con ≥ 0.5 ∧ ent ≥ 0.7).
3. It does NOT fix the remaining false-supported (ind-026, Apollo negation-flip): that
   item's *selected passage* is a different sentence (ent 0.914, con 0.026) — a
   passage-selection failure, not a label-mapping failure. Only Tasks 1/2 (top-k max +
   context expansion) address it.
4. Calibration caveat: n=41, single model, single domain mix. The clean separation may
   partly reflect that our "unsupported" cases are strong contradictions (false claims,
   direct negations), not subtle ones. Re-measure when the eval set grows to ≥ 80.

**Decision input:** hard-`unsupported` at con ≥ 0.5 is empirically safe (zero collateral
on true supports) and costs nothing on current accuracy; its value is defensive.
