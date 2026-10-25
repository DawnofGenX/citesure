# Clause-mixture rule — negative result (reverted)

> **Correction, same day: this experiment's premise was wrong.** The v4 family it set out to fix
> (`partially-true-mixed`, expected `ambiguous`) was **mislabelled in the set**: its second clause
> negates a sentence the page *does* state, so the conjunction is contradicted by the page and
> `unsupported` is the correct verdict — which is what citesure was already returning. The family is
> now `mixed-false-conjunct` with `expected_status: unsupported` and passes 10/10
> (`RESULTS_v4.md`). That is why this rule "gained" 8 mixed cases at the cost of 20 correctly
> `unsupported` ones: it was demoting correct verdicts to rescue a label that was wrong.
>
> The measurements below are real and were made against the superseded 83-case set with the old
> labels; the method and the gate stand, only the motivation does not. The one durable output is
> the diagnosis: the trigger "one verbatim clause plus one non-verbatim clause" is structurally
> identical across mixed, negation-flip and entity-swap claims, so no rule built on that shape can
> separate them.


Date: 2026-10-25 · Status: **attempted, measured, REVERTED** · Product code unchanged.

## What was attempted

The v4 held-out set (`evals/independent_set_v4.json`, 83 cases) shows
**partially-true-mixed 0/11** with NLI on: every half-true claim is reported
`unsupported` with NLI scores of 0.0003–0.04, so the tier is confidently wrong
rather than unsure. This is the largest measured product gap
(`evals/RESULTS_v4.md`).

The narrow rule under test (one function, `apply_clause_mixture_rule`, wired
into the NLI tier of `verify_citations` in `src/citesure/overlap.py`):

> When a claim splits into **>= 2 clauses** (`split_compound_claim`) **AND** the
> whole-claim verdict is **`unsupported`** **AND** at least one clause is
> **verbatim-present** in the original passage while at least one other clause
> is **not**, report **`ambiguous`** instead of `unsupported`.

Design intent (and the property that made it *look* safe): a one-way ratchet.
It only ever moves `unsupported` → `ambiguous`, and only for mixtures, so it
cannot touch a `supported` or `ambiguous` verdict — it cannot demote a
correctly-supported compound claim, which is exactly the failure mode of the
reverted per-clause NLI attempt (v2 93/108 → 82/108, McNemar p=0.0117; see
`AUTHORING_V3.md`). Single-clause claims are never touched.

"Verbatim-present" = the clause (cleaned, lowercased, whitespace-collapsed,
trailing punctuation stripped, >= 8 chars) appears as a substring of the
page text (lowercased, whitespace-collapsed).

## Pre-committed acceptance gate

Keep the change only if **all** hold:

1. v4 `partially-true-mixed` recall improves by at least 5 of 11;
2. v4 overall does not fall by more than 2 cases;
3. v2 overall does not fall by more than 2 cases;
4. no family in either set loses more than 2 correct cases.

## Measured result (NLI on, both sets)

Baselines are fresh runs of the current code, not the documented figures
(v2's live Wikipedia pages drift ~1 case from the 93/108 published in
`RESULTS_v4.md`; this run measured 92/108).

### v4 (held-out, 83 cases)

| family | baseline | rule | delta |
|---|---|---|---|
| partially-true-mixed | 0/11 | 8/11 | **+8** |
| entity-swap | 18/24 | 8/24 | **−10** |
| negation-flip | 22/22 | 12/22 | **−10** |
| verbatim-supported | 17/24 | 17/24 | 0 |
| dead-link | 2/2 | 2/2 | 0 |
| **overall** | **59/83 = 71.08%** | **47/83 = 56.63%** | **−12** |

Gained (8, all mixed): ind4-007, 028, 035, 042, 060, 067, 074, 081.
Lost (20): 10 entity-swap (ind4-005, 019, 020, 034, 041, 045, 051, 052, 065,
080) and 10 negation-flip (ind4-017, 018, 031, 032, 038, 049, 056, 063, 077,
078) — every one a correct `unsupported` demoted to `ambiguous`.

### v2 (in-sample, 108 cases)

| family | baseline | rule | delta |
|---|---|---|---|
| entity-swap | 27/32 | 27/32 | 0 |
| negation-flip | 30/32 | 30/32 | 0 |
| partially-true-ambiguous | 1/4 | 1/4 | 0 |
| verbatim-supported | 26/32 | 26/32 | 0 |
| paywalled | 4/4 | 4/4 | 0 |
| dead-link | 4/4 | 4/4 | 0 |
| **overall** | **92/108 = 85.19%** | **92/108 = 85.19%** | **0** |

v2 is untouched: its compound `unsupported` claims do not present a
verbatim-clause-plus-absent-clause shape against the cached page text, and its
four `partially-true-ambiguous` cases are the known mislabelled both-clauses-true
class (`AUTHORING_V3.md`), so no clause is absent and the rule never fires.

## Gate evaluation

| clause | result | detail |
|---|---|---|
| 1. v4 mixed recall +≥5 of 11 | **PASS** | gain = 8 |
| 2. v4 overall falls ≤2 | **FAIL** | delta = −12 |
| 3. v2 overall falls ≤2 | **PASS** | delta = 0 |
| 4. no family loses >2 | **FAIL** | v4 entity-swap −10, negation-flip −10 |

**Gate: FAIL.** Clauses 2 and 4 fail. The change is reverted.

## Why it failed (root cause)

The rule's trigger — *one clause verbatim in the page, another not* — is
**structurally identical** across three families, so it cannot separate them:

* **partially-true-mixed** (want `ambiguous`): a verbatim clause joined to a
  negated clause whose affirmative form is *absent* from the page.
* **negation-flip** (want `unsupported`): a verbatim clause joined to a negated
  clause whose affirmative form *is* in the page (the negation is the error).
* **entity-swap** (want `unsupported`): a verbatim clause joined to a clause
  whose entity has been swapped out of the page.

In all three, exactly one clause is verbatim-present and another is not, so the
rule fires on all three. It rescued 8/11 mixed claims at the cost of 20
correctly-`unsupported` negation-flip and entity-swap claims — a net −12 on v4.
The one-way-ratchet property (never touch `supported`) is real but
insufficient: the damage is done to the `unsupported` families, which the
ratchet is allowed to move.

The discriminator the rule *needs* — "the absent clause's affirmative form is
itself absent from the page" (mixed) vs. "present" (negation-flip) — requires
de-negating the absent clause and re-checking the page, which is a materially
different (and much less narrow) rule than the one under test. That is a
separate experiment, not a threshold tweak of this one.

## What was NOT done

* No NLI banding constant was touched (0.7 / 0.3 / 0.5 all unchanged).
* No per-clause NLI scoring was changed for non-mixture claims.
* Single-clause claims were never touched.
* The product code is reverted to the pre-experiment state
  (`git checkout -- src/citesure/overlap.py`); `apply_clause_mixture_rule` is
  gone from the tree.

## Artifacts

* `evals/results/clause_baseline_v4/`, `clause_rule_v4/` — v4 before/after.
* `evals/results/clause_baseline_v2/`, `clause_rule_v2/` — v2 before/after.
* `evals/compare_clause_rule.py` — reproducible before/after + gate evaluator:
  `.venv/bin/python evals/compare_clause_rule.py`.

## Reproduce

```bash
# baseline (current code)
.venv/bin/python evals/run_eval.py --set evals/independent_set_v4.json --nli --out evals/results/clause_baseline_v4
.venv/bin/python evals/run_eval.py --set evals/independent_set_v2.json --nli --out evals/results/clause_baseline_v2
# with the rule (re-apply the two edits to src/citesure/overlap.py, then)
.venv/bin/python evals/run_eval.py --set evals/independent_set_v4.json --nli --out evals/results/clause_rule_v4
.venv/bin/python evals/run_eval.py --set evals/independent_set_v2.json --nli --out evals/results/clause_rule_v2
# before/after + gate
.venv/bin/python evals/compare_clause_rule.py
```
