# Baseline Results — Trivial Classifiers on `independent_set_v2`

**Date:** 2026-10-05  
**Set:** `evals/independent_set_v2.json` (108 cases, 40 unique URLs)  
**Seed:** 42  
**Wilson CI:** 95% confidence, computed via `evals/stats.py::wilson_ci`

## Baseline Table

| Baseline | Accuracy | Wilson CI | What it proves |
|---|---|---|---|
| `always_supported` | 32/108 = 29.6% | [21.8%, 38.8%] | A verifier that blesses every claim scores 29.6% — the class distribution is NOT trivially easy by predicting "supported" for everything. |
| `always_unsupported` | 64/108 = 59.3% | [49.8%, 68.1%] | A verifier that rejects every claim scores 59.3% — the majority class is "unsupported" (64/108), so a trivial reject-all classifier already beats random. |
| `majority_class` (ORACLE) | 64/108 = 59.3% | [49.8%, 68.1%] | Upper bound for a label-aware trivial classifier: always predict "unsupported". Not a blind baseline — uses the labels to pick the class. |
| `random_by_distribution` | 47/108 = 43.5% | [34.6%, 52.9%] | Sampling predictions from the observed class distribution scores 43.5% — below the majority-class floor, confirming that the distribution is skewed enough that blind sampling underperforms. |
| `status_prior_random` | 41/108 = 38.0% | [29.4%, 47.4%] | Cross-check via a separate implementation path (CDF-based sampling). The two random baselines disagree by 5.5 pp, which is within sampling noise for n=108 and confirms both are estimating the same quantity. |
| `tier1_always_supported` | 64/108 = 59.3% | [49.8%, 68.1%] | Citesure-specific: predict "supported" when `tier_reached == 1` (reachability check only), "unsupported" otherwise. Scores 59.3% — identical to `always_unsupported` because only 8 cases reach tier 1, and all 8 are "unsupported" (dead links). |

## Interpretation

**How much of citesure's 86.1% is attributable to the verifier rather than the class distribution being easy?**

The trivial floor is **59.3%** (always predict "unsupported"). Citesure's 86.1% exceeds this by **26.8 percentage points**. This gap — not the raw 86.1% — is the portion attributable to the verifier's actual claim-checking ability.

However, two caveats apply:

1. **The 59.3% floor is itself optimistic for a "trivial" baseline** because the class distribution is skewed (59% unsupported). A balanced set would make the trivial floor 50%, widening the verifier's apparent contribution. The skew is a property of the eval set, not of citesure.

2. **The 86.1% headline is in-sample** (see contamination note below), so the 26.8 pp gap is also optimistically biased. The true held-out gap is likely smaller.

## Contamination Note — What a Retrospective Split Cannot Fix

**This split does NOT retroactively make the 86.1% headline held-out.**

The historical record is:

1. **Thresholds were tuned on v1** (41 cases, `evals/THRESHOLD_CALIBRATION.md`).
2. **NLI models were compared on v2** (108 cases, `evals/RESULTS_MODEL_BENCH.md`).
3. **v2 is the set the README reports** (86.1% headline).

Because v2 was used to select the NLI model, the 86.1% is an **in-sample estimate** for that model choice. A retrospective split of v2 into tune/held-out halves does not undo this: the model was chosen because it performed well on ALL of v2, so both halves are contaminated by that selection.

**What this partition IS:** a frozen split available for **FUTURE** tuning decisions. If you later adjust thresholds or swap models, you may tune on the TUNE half and evaluate on the HELD-OUT half without leakage.

**What a genuinely clean v3 set would require:**

- A new eval set, **not used for any prior model selection or threshold tuning**.
- Cases drawn from **sources not present in v1 or v2** (or at minimum, URLs not used in any prior tuning decision).
- Labels assigned **before** any model comparison is run on the set.
- The set is then split (by URL cluster) and frozen, and the split is committed before any tuning begins.

Until such a set exists, the 86.1% should be read as an **optimistic in-sample estimate**, and the 59.3% trivial floor should be read as a **lower bound on the verifier's contribution** (the true contribution is likely between 26.8 pp and 36.1 pp, but the uncertainty is not quantifiable without a clean held-out set).

## Reproducibility

```bash
# Regenerate the split
.venv/bin/python evals/split.py --set evals/independent_set_v2.json --seed 42 \
  --tier-data evals/results/p1_uncertainty/20261005-171702.json

# Run tests
.venv/bin/python -m pytest tests/test_eval_split.py -q
```

The split is deterministic: same input + same seed → byte-identical partition JSON.
