# Defect: `ind-010` false pooled-contradiction veto (open, 2026-09-30)

**Status:** OPEN — unfixed. Every fix attempted was reverted after measurement.
**Impact:** 1 case on the 41-case frozen gate (`ind-010` only). 0 cases on the
108-case set (ind-010 is not in v2). No safety metric is affected.
**Cost of leaving it:** the frozen gate reads 32/41 = 78.05% instead of a possible
33/41 = 80.5%. v2 is unaffected at 93/108 = 86.11%.

---

## The defect

v1 case `ind-010`:

| field | value |
|---|---|
| category | `paraphrase-supported` |
| `labeler_confidence` | **high** |
| expected status | `supported` |
| claim | `os.chdir changes the current working directory to the given path.` |
| url | `https://docs.python.org/3/library/os.html` |

The page **does** support the claim. Verified against the live page 2026-09-30: it
contains

    os. chdir ( path ) ¶ Change the current working directory to path .

The labeler note records the same: *"Paraphrase of 'os.chdir(path) - Change the
current working directory to path.' curl: HTTP 200."*

Pipeline output for the case:

```
overlap tier: score 0.875              (>= 0.6 -> supported)
NLI tier: entailment 0.995             (>= 0.7 -> supported)
NLI tier: contradiction 0.000
pooled contradiction 0.996 >= 0.5      <-- veto
NLI moved verdict supported -> unsupported
```

`apply_nli_tier` flips a `supported` verdict when
`pooled_contradiction >= POOLED_CONTRADICTION_THRESHOLD` (0.5), so a pooled score
of 0.996 overwrites an entailment of 0.995.

## Root cause

`pool_candidate_sentences` (`src/citesure/overlap.py` lines 460-482) splits premises
with `_SENTENCE_BOUNDARY_RE`, which is only `re.compile(r"(?<=[.!?])\s+")`
(line 119). It does not break on newlines, headings, or code blocks, so a
documentation run with no sentence-final punctuation stays a single unit. For
ind-010 that unit is a **1,290-character** run of the `os.walk()` /
`shutil.rmtree()` documentation, code sample included.

That unit is admitted to the pool because it shares incidental vocabulary with the
claim — `changes, current, directory, os, path, working` — giving overlap
**0.750** against `CONTRADICTION_POOL_MIN_OVERLAP = 0.5`. It then passes
`has_slot_conflict`, whose negation branch fires because the unit contains
`not` ("does **not** keep track") and `never` ("walk() **never** changes"),
describing `os.walk` rather than `os.chdir`. The model scores the resulting pair
as contradiction 0.996, and the claim is vetoed.

`has_slot_conflict` is a **gate to scoring**, not a filter on results:

```
for sent in pool_candidate_sentences(claim, premises):
    if not has_slot_conflict(claim, sent):
        continue                       # only TRUE ones are scored
    pool_pairs.append(...)
pooled_contradiction = max(contradiction over admitted pairs)
```

So any change to the guard can only **add** candidates, i.e. cause **more** vetoes.
The guard is not the bug; the blob is scored because it is *admitted*.

## Why the obvious signals cannot arbitrate

Scored with the real encoder (`score_nli_batch_all`, `nli-deberta-v3-base`):

| pair | entailment | contradiction |
|---|---|---|
| ind-010 vs blob clause (**false** veto) | 0.000 | 0.985 |
| ind-010 vs sub-split clause (**false** veto) | 0.000 | 0.996 |
| ind-026 vs Apollo sentence (**true** veto) | 0.002 | 0.990 |
| ind-026 vs short form (**true** veto) | 0.000 | 0.962 |

- **Contradiction:** 0.985 (false) vs 0.990 (true). No threshold separates them.
- **Entailment:** 0.000 (false) vs 0.002 (true). No threshold separates them.

The two cases are also the *same surface form* — one side negates, the other
affirms — differing only in which side. ind-026 puts the negation on the claim
("Armstrong **did not** become..."), so no sentence clause contains a negation
word at all.

## Fixes attempted and rejected (do not retry without new evidence)

| # | approach | measured result | verdict |
|---|---|---|---|
| 1 | 600-char candidate ceiling | implemented and gate-run. Fixed ind-010, **broke ind-026**: v1 32/41 -> 32/41, `negation_flip_false_supported` 0/5 -> 1/5. Reverted. | rejected |
| 2 | clause-scoped negation (negation must sit in a clause sharing claim vocabulary) | prototype returned `False` for ind-026, the case it must preserve | rejected |
| 3 | entailment-gated veto (only veto when the pair does not entail) | inert: 0.000 vs 0.002 both pass any threshold | rejected |
| 4 | distinctive-content filter (any distinctive term) | the blob clause "never changes the current working directory" scores overlap **exactly 0.500**, on the 0.5 bar, sharing 4 distinctive terms — admitted | rejected |
| 5 | dotted-API-subject gate (candidate must name the claim's last two identifier segments) | implemented and gate-run. Excluded the test blob (0/8) but passed **10 of 10** candidates on the real page: the parent segment `os` is a 2-character substring matching `os.path.join`, `OSError`, `Those`, `close`. Gate run: 32/41, ind-010 still unsupported. Reverted. | rejected |

Approach 5's failure was a **method** error worth recording: it was validated
against the regression-test fixture, not against the real fetched page. A token
variant (`os` as a whole token rather than a substring) would **also** fail — the
blob's own term list contains `os` as a standalone token, from `import os`,
`os.walk` and `os.path`.

**Validate any candidate filter against the production input, never against a
fixture written for the test.**

Do **not** raise `CONTRADICTION_POOL_MIN_OVERLAP` to 0.6 to exclude the 0.500
clause: that drops the genuine ind-026 case (overlap 0.556) and is fitting a
frozen gate.

## Untested direction

The only mechanism not yet tried, and the one the evidence points at: resolve the
claim's subject against the **page's own structure** rather than against
arbitrary prose. The live page carries a clean definition entry —

    - os.chdir(path)¶
    - Change the current working directory to path.

— which genuinely *is* the claim's subject, as distinct from `os.walk`, `OSError`
and `os.listdir` documentation that merely share vocabulary. Every rejected
approach filtered on free-text token overlap, which is precisely what a
documentation page defeats. A structural check is a different class of filter.

This has **not** been prototyped. It should be validated against the real fetched
page before any plan is written.

## Related, still-open gap

`partially-true-ambiguous` scores **0/4** on both eval sets because the NLI tier
maps low entailment straight to `unsupported` with no ambiguous band. This is a
**separate** defect from ind-010 and is not addressed by anything above.

## Reproduction

```bash
cd /home/hermes/citesure
export CITECHECK_NLI_MODEL=/home/hermes/.cache/huggingface/hub/models--cross-encoder--nli-deberta-v3-base/snapshots/6c749ce3425cd33b46d187e45b92bbf96ee12ec7
export CITECHECK_CACHE_DIR=/tmp/citesure-v2
.venv/bin/python evals/run_eval.py --set evals/independent_set.json --nli --out /tmp/ind010
```

Then inspect the `ind-010` record: `predicted` should be `unsupported` while
`expected` is `supported`, with a note reading `pooled contradiction 0.996`.

Baseline evidence is committed at `evals/results/v1_pre_score_audit/` (and
`v2_pre_score_audit/`), which record the same 32/41 and 93/108 figures with the
`ind-010` notes.
