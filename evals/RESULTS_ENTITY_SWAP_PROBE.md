# entity-swap: four mechanisms probed, none usable

Run date: 2026-10-06. Question: can any rule or signal separate the five confident
false-supports from the correctly-supported cases, so a fix to the open defect can be
measured rather than guessed at?

**Answer: no.** Four mechanisms were probed against the full population — the 5
false-supports and the 26 correctly-supported cases, from the two committed split-half
runs (`evals/results/{tune,heldout}_split/`). All four fail. This is a negative result
and is recorded as such, because a fix for the open defect is not available from these
mechanisms and future work should not re-attempt them blind.

## The population

All five are `entity-swap` mutations where the page states text nearly identical to the
claim with **one slot swapped**. Every one is correctly labelled: the page genuinely does
not support the claim.

| Case | Claim says | Page says | Score |
|---|---|---|---|
| ind-196 | ReLU is xΦ(x) | that is **GELU**; ReLU gates by sign | 0.979 |
| ind-190 | In **HTTP/2**, headers are case-insensitive names… | "In **HTTP/1.X**, a header is…" | 0.989 |
| ind-118 | **Sobolev** type norm → refined **Besov** | **Besov** type norm → refined **Sobolev** | 0.985 |
| ind-109 | `ForeignKey` defines many-to-many | ForeignKey is many-**one** | 0.987 |
| ind-136 | async/await syntax **is a library provided by** asyncio | asyncio **is the library that uses** async/await | 0.998 |

All five score 0.9788-0.9983. Break-risk population: 32 cases expect `supported`, 26 currently
correct.

## Mechanism 1 — disjoint proper nouns (strict)

The structural test already documented in this project's eval notes: fire when the claim
and the evidence each name a proper noun the other lacks, **and** share none.

```
fires on false-supports : 0/5
fires on supported      : 0/26
verdict: NOT USABLE — zero recall
```

Zero breaks *and* zero fixes. This confirms the documented limitation directly: the swap is
always a near-variant of the same entity (`ReLU`/`ReLUs`, `HTTP`/`HTTP/1.X`,
`Sobolev`/`Besov` both present), so the intersection is never empty and the rule never
fires. The rule is not too aggressive here — it is blind to this shape.

## Mechanism 2 — claim entity absent from evidence (weak)

Same test with the disjointness requirement dropped.

```
fires on false-supports : 5/5
fires on supported      : 11/26
verdict: NOT USABLE — 11 false rejections
```

Full recall, unusable precision: it would reject 11 correctly-supported claims to catch 5.
This is the trap the eval discipline exists to catch — a rule that looks like it works
because its target metric improves, while the aggregate collapses.

This result is also **stopword-set sensitive**, which matters before anyone builds on it:
widening the stopword list with ordinary verbs (`use`, `define`, `provide`, `write`) drops
recall from 5/5 to 4/5, because ind-136's only unmatched entities are the verbs `provided`
and `writing`. The 5/5 depends on treating those as content words. Either way the mechanism
is unusable so the sensitivity changes no decision — but a rule whose recall moves on an
arbitrary word list is not a rule to ship.

## Mechanism 3 — missing slot tokens (version/number/capitalised)

Restricting the absence test to slot-shaped tokens (numbers, version strings, capitalised
terms, `X/Y` forms).

```
false-supports with >=1 missing slot : 1/5   (only ind-190 -> 'HTTP/2')
supported with >=1 missing slot       : 7/26
break:fix ratio = 7.00
verdict: NOT USABLE — 7 breaks per 1 fix
```

Narrowing the token class improved precision and destroyed recall. The other four false-
supports have **no** missing slot token: `ReLU`, `Sobolev` and `Besov` all appear in their
own evidence, so the swap is invisible to lexical absence. This is the structural fact
behind the whole failure — **the contradicted slot is present in the evidence, attached to
a different subject.**

## Mechanism 4 — pooled NLI signals over the whole evidence window

Instead of a lexical rule, re-score every sentence in the captured evidence and pool:
`s_min = min entailment`, `s_max_con = max contradiction`. The motivation is that a
contradicting sentence exists somewhere in the window even when the pipeline only keeps the
best-entailing pair.

```
min-entailment   false-supports mean 0.199   supported mean 0.089
max-contradiction false-supports mean 0.194  supported mean 0.138

separation on min-entailment    : 0.992 vs 0.000 -> OVERLAPS
separation on max-contradiction : 0.000 vs 0.996 -> OVERLAPS
```

Both signals **overlap completely**, and the reason is worth recording because it
contradicts the intuition that motivated the probe:

```
ind-109  cur 0.987  minPool 0.000  maxContra 0.001     <- false-support
ind-158  cur 0.995  minPool 0.000  maxContra 0.979     <- correctly supported
```

A correctly-supported claim has min-entailment 0.000 too. Every page contains many
sentences unrelated to the claim, and those score ~0 entailment regardless of whether the
claim is supported. So `min` over a sentence pool is **not** a support signal for either
class — it measures pool breadth, not support. The false-supports do sit *higher* on
average (0.199 vs 0.089), but with overlap at both ends, no threshold separates them.

`max-contradiction` fails for the documented reason: it does separate ind-196 (0.957)
cleanly, but the highest contradiction among correctly-supported cases is **0.979**
(ind-158), so it cannot be used as a veto.

## What this rules out, and what it does not

Ruled out as measured: lexical entity-disjointness (both strict and weak), lexical slot
absence, and pooled min-entailment / max-contradiction over the evidence window.

**Not ruled out:** asking the model a different question. Every mechanism above compares
the claim to evidence as *strings or as a sentence-level entailment*. All five failures
share one shape — the contradicted slot is present but bound to a different subject — and
resolving that requires comparing **who is the subject of which predicate**, which is a
slot-filling or structured-comparison operation rather than a string or single-pair
entailment operation. That is the remaining direction, and it needs a different kind of
mechanism than the four measured here.

The consequence for planning is concrete: **do not schedule a fix from this evidence.** A
candidate now needs its own gate, and v2 supplies one for this class — each half holds 16
entity-swap negatives and 16 supported cases, both ≥10 — so gain and regression are both
measurable on `evals/subsets/`. The ambiguous class remains ungateable (see
`RESULTS_SPLIT_HALVES.md`), so any attempt must stay confined to entity-swap.

## Reproducing

The mechanisms are arithmetic over committed artefacts. Mechanisms 1–3 need no model
beyond the saved page text and the two split-half result files. Mechanism 4 needs the NLI
model already cached under `CITECHECK_CACHE_DIR`:

```bash
HF_HUB_OFFLINE=1 .venv/bin/python evals/run_eval.py \
  --set evals/subsets/independent_set_v2_heldout.json --nli --out evals/results/heldout_split
HF_HUB_OFFLINE=1 .venv/bin/python evals/run_eval.py \
  --set evals/subsets/independent_set_v2_tune.json --nli --out evals/results/tune_split
```

No probe script is committed: the four mechanisms were measured as throwaway arithmetic,
and this document plus the committed run files is the durable record. That matches the
project rule that a probe left in the repo root becomes permanent clutter.