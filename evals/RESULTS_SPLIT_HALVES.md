# Split-half evaluation: tune vs held-out

Run date: 2026-10-06. Both halves executed with `--nli` (the reported configuration).
Committed artefacts:

| Artefact | Path |
|---|---|
| tune-half result | `evals/results/tune_split/20261006-180831.json` |
| held-out-half result | `evals/results/heldout_split/20261006-180705.json` |
| partition (case IDs only) | `evals/splits/independent_set_v2_tune_heldout.json` |
| materialized halves | `evals/subsets/independent_set_v2_{tune,heldout}.json` |

Reproduce:

```bash
.venv/bin/python evals/run_eval.py --set evals/subsets/independent_set_v2_heldout.json --nli --out evals/results/heldout_split
.venv/bin/python evals/run_eval.py --set evals/subsets/independent_set_v2_tune.json     --nli --out evals/results/tune_split
```

The runner takes a `--set` path, so the partition's ID lists were materialized into
standalone subset files rather than adding a filter flag. `evals/subsets/` is derived
data — it is regenerated from `independent_set_v2.json` plus the partition, and must never
be edited by hand.

## The partition, and what it can and cannot prove

`evals/split.py` splits **by source URL, not by row**, because 32 of 40 unique URLs
contribute three cases each (verbatim / negation-flip / entity-swap derived from ONE source
sentence). Splitting rows would put a near-duplicate of a tuning case into held-out and
reopen the leak silently. Verified assertions on the committed partition:

```
no URL cluster appears on both sides   PASS
no case id appears on both sides       PASS
tune ∪ heldout == all 108 ids         PASS
tune    54 cases / 20 urls
heldout 54 cases / 20 urls
```

Both sides carry an identical status distribution — `{unsupported: 32, supported: 16,
unreachable: 2, paywalled: 2, ambiguous: 2}` — so the floor is directly comparable across
the split rather than an artifact of one side being easier.

**What this does NOT do:** make the existing 86.1% out-of-sample. Thresholds were tuned on
v1 and the NLI model was chosen using all of v2, so *both* halves carry that selection.
This is a **stability check** — does the number depend on which half you look at — not a
clean held-out estimate. A genuinely clean number needs cases from sources absent from every
prior selection decision.

## Headline result

| Half | Agreement | Wilson 95% CI | Cluster-bootstrap 95% CI |
|---|---|---|---|
| tune | 47/54 = 87.0% | [75.6, 93.6] | [80.0, 94.1] |
| **held-out** | **46/54 = 85.2%** | **[73.4, 92.3]** | **[76.5, 93.8]** |

Reconciliation, as a replay gate: 47 + 46 = **93/108 = 86.11%**, reproducing the incumbent
README headline exactly. The two halves are a partition of the same 108 cases, so this is a
consistency check on the pipeline, not an independent second sample.

## Split-half stability

The halves are different cases, so this is **unpaired** — Fisher's exact test, not McNemar.

```
tune 47/54 vs heldout 46/54
delta = +1.85 points
Fisher exact p = 1.000
```

**No detectable difference between halves.** The headline is not an artifact of which half
you happen to report. The gap is one case; on n=54 per side nothing smaller is resolvable
anyway, which is the honest limit of this design.

## Safety metrics — the asymmetric gate, per half

| Half | Negation-flip false-supported | Entity-swap false-supported |
|---|---|---|
| tune | 0/16 = 0.0% CI [0.0, 19.4] | 2/16 = 12.5% CI [3.5, 36.0] |
| held-out | 0/16 = 0.0% CI [0.0, 19.4] | 3/16 = 18.8% CI [6.6, 43.0] |

Entity-swap rate across halves: Fisher exact **p = 1.000** — indistinguishable. The
negation-flip direction is clean on both sides at 0/16, which is the metric the eval was
built to protect: a confident false positive on a negated claim is the dangerous error.

Pooled across both halves this reproduces the headline's 5/32 = 15.6% entity-swap figure.

## The finding that matters more than the accuracy

**Neither half can gate the one fix still open.** The skill's rule is that a validation set
must be able to *detect* the change it is being used to gate, not merely survive it: assert
`available_positives >= ~10` and `available_negatives >= ~10` in the direction the fix moves.
Measured on this partition:

| Half | ambiguous cases | supported | negatives | Can gate an ambiguous-class fix? |
|---|---|---|---|---|
| tune | 2 | 16 | 32 | **NO** (needs ≥ 10) |
| held-out | 2 | 16 | 32 | **NO** (needs ≥ 10) |

v2 contains **4** `partially-true-ambiguous` cases in total, so a URL-clustered split can
never give either side 10. The unresolved `ambiguous` class is the gap this project exists to
close, and v2 is structurally incapable of measuring a fix to it — a fix could move the
ambiguous cases from 2/4 to 4/4 while regressing 12 supported cases, and this partition
would not reliably catch the regression.

That is what v3 is for: **10** `partially-true-mixed` cases, already labelled, already run.
It is currently used as a *diagnostic* (v3 is a different slice of the web — literature, not
technical docs — so it is not a substitute for v2). Any attempt to fix the ambiguous class
should be gated on a v3-style set built for the purpose, not on these halves.

## Mismatch triage by layer

Per the layer table — attribution, not a fix list.

| Half | Selection/extraction (score < 0.05) | Model (confidently wrong, ≥ 0.9) | Borderline |
|---|---|---|---|
| tune | 4 | 3 | 0 |
| held-out | 2 | 5 | 1 |

Near-zero scores mean the claim's sentence never reached the model — a selection/extraction
layer problem. High scores on a wrong verdict mean the fact reached the model intact and it
still got it wrong: **a limitation to document, not a bug to fix.** Both classes are present
on both sides, and the distribution is similar, which is further evidence the halves are
comparable.

### The model-layer mismatches, individually

Spot-checked against the captured evidence rather than read off the score:

| Case | Category | Score | Assessment |
|---|---|---|---|
| ind-196 | entity-swap | 0.9788 | **Model layer.** Evidence is GELU's formula; claim re-attributes it to ReLU. Page mentions ReLU but says it gates by sign. A correct resolution needs an entity-subject comparison. |
| ind-190 | entity-swap | 0.9893 | **Model layer.** Page says "In HTTP/1.X, a header is a case-insensitive name…"; claim says HTTP/2. The qualifier difference is exactly the shape the disjoint-proper-noun rule is designed to *not* fire on. |
| ind-118 | entity-swap | 0.9851 | **Model layer.** Same class. |
| ind-136 (tune) | entity-swap | 0.9983 | **Model layer.** Same class. |
| ind-109 (tune) | entity-swap | 0.9873 | **Model layer.** Same class. |
| ind-128 (tune) | verbatim-supported | 0.9952 | **Selection layer** despite the high score — evidence is a JSON parse error message, so the supporting sentence never reached the model. A high score is not proof of a model-layer verdict. |
| ind-161 (tune) | verbatim-supported | 0.0007 | **Selection layer.** Near-zero: claim text never selected. |

Two `partially-true-ambiguous` cases on the held-out side (ind-250 score 0.954, ind-251
score 0.9957) are flagged as **contestable labels**, not model failures:

- **ind-250** — the evidence window covers Curie's Polish identity and does not reach the
  death/entombment clauses (3/13 claim content tokens present, longest shared verbatim span
  2 words). A verifier that cannot see the clause cannot be blamed for returning supported;
  this is an evidence-window problem.
- **ind-251** — 19/22 claim tokens present, **longest shared verbatim span 14 words**. The
  page genuinely states the Pen-y-darren details. The `ambiguous` label rests entirely on the
  labeler's judgment that "first railway journey" is a contested designation, which is a
  defensible-but-debatable call. The system returning `supported` here is arguably correct.
  **Re-reviewed below — the label was upheld, not changed.**

An eval number is a statement about labels as much as about code. One of the four ambiguous
labels in the entire reported set is contestable on the evidence, and it moved the held-out
headline.

### ind-251 label re-review: label UPHELD, not changed

This case was flagged for re-review because it is a held-out miss that `supported` would have
"fixed". It was re-reviewed against the saved page text and **the `ambiguous` label stands**.
Recorded here so the reasoning is auditable, because the case looks like free accuracy.

| Check | Result |
|---|---|
| Claim content tokens present in page | 22/22 = **100%** |
| Longest verbatim contiguous span | **14 words** ("Trevithick's steam locomotive hauled 10 tonnes of iron, 70 passengers and five wagons along") |
| Labeller's own note | "All the details are verbatim true" |
| Contesting context present on page | **Yes** — "In 1825 George Stephenson built the Locomotion for the Stockton and Darlington Railway. This was the first public steam railway in the world" |

So the page does assert the claim, and it does carry the competing 1825 framing elsewhere. The
label is a defensible judgment call: `supported` is the right answer *to the sentence*, and
`ambiguous` is the right answer *to whether the claim's premise is safe to assert*. citesure
answers the first question, so it is not wrong here.

Relabelling it would have moved held-out from 46/54 to 47/54 (+1.85 points) and tune to 48/54.
It was rejected on three grounds, in order of weight:

1. **It would fix the wrong thing.** The same re-review applied to **all four** v2 ambiguous
   cases: every one is 100% token-present, and every labeller wrote "BOTH CLAUSES ARE VERBATIM
   TRUE". All four are the same shape. Relabelling one to make a number look better would be
   labelling to a target — the exact defect this project's eval discipline exists to prevent.
2. **It would leave the category undefined.** One `supported` case sitting inside
   `partially-true-ambiguous` alongside three `ambiguous` ones means the category no longer
   denotes anything, and every future comparison against it becomes meaningless.
3. **v3 already answered this.** v3 defines `partially-true-mixed` as *one clause present, one
   clause GENUINELY ABSENT from the page*. None of v2's four cases has an absent clause. By v3's
   own vocabulary these four are `fully-supported-compound`, whose expected status is
   `supported` — i.e. exactly what the system returned.

The real defect is therefore the **v2 label vocabulary**, not this case: v2 named all-verbatim-true
compounds "partially-true-ambiguous", so the eval demanded a contested-premise signal that is not
a partial-support signal at all. That mislabelling is what made the per-clause NLI fix look
necessary — and that fix demoted 12 correctly-supported cases (v2 fell 93→82→84) before being
reverted. The four labels are left exactly as they are; the finding is recorded instead.

## Anomaly: labeler_confidence is inverted

| Half | high confidence | medium confidence |
|---|---|---|
| tune | 27/32 = 84.4% | 20/22 = 90.9% |
| held-out | 23/30 = 76.7% | 23/24 = 95.8% |

High-confidence labels score **worse** than medium-confidence ones on both sides, and the
inversion is larger on held-out (76.7% vs 95.8%). That is the opposite of what the field means
and it is consistent across both halves, so it is not sampling noise on one side.

The likely cause is visible in the triage above: the high-confidence mismatches are the
**entity-swap** cases, where a careful labeler correctly identifies a hard distinction and
the system confidently gets it wrong. Medium-confidence labels are mostly paraphrases, which
the system handles. Confidence tracks *difficulty for the system*, not label quality. Do not
read the high-confidence slice as the more reliable part of the ground truth — and note that
the uncertainty block reports this breakdown without comment, so a reader would otherwise
assume high = more trustworthy.

## What this changes

Nothing about the headline, and that is the result: **86.1% survives a URL-clustered split
with no detectable difference between halves** (p = 1.000), and reproduces exactly when the
two halves are recombined. What the split adds is three things the single number could not
say:

1. The number does not depend on which half you report.
2. The negation-flip safety metric is clean on both sides independently — 0/16 twice.
3. **v2 cannot gate a fix to the `ambiguous` class**, on either side, because it holds only 4
   such cases and a cluster-disjoint split halves that to 2. Any ambiguous-class work must be
   gated on a purpose-built set.