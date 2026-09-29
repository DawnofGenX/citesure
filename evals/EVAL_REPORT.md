# Citesure — Phase A Independent Accuracy Evaluation Report

**Date:** 2026-09-09 (UTC) · **Mode:** NLI **OFF** (tiers 1+2 only) · **Set:** `evals/independent_set.json` (41 items)
**Runner:** `evals/run_eval.py` · **Raw results:** `evals/results/20260909-061839.json`

---

## 1. Headline result

| Metric | Value |
|---|---|
| Total items | 41 |
| Agreement (predicted == expected) | **22/41 = 53.7%** |
| Negation-flip false-supported | **5/5 = 100%** |
| Entity-swap false-supported | **5/5 = 100%** |
| Overall false-supported (expected=unsupported → predicted=supported) | **13/16 = 81.3%** |

**Bottom line:** With NLI off, the overlap tier is a *topical* matcher, not a *propositional* one. It is essentially perfect on the "does this page talk about this topic" axis (all 16 supported items and all 5 unreachable/paywalled items classified correctly), but it is **completely blind to negation and entity swaps** — every single negation-flip and entity-swap item was returned as `supported`, several with score 1.0. This is exactly the R3 blind spot the test plan predicted, and it means the overlap tier alone is **unsafe as a citation-support gate**: it will confidently bless claims that the cited page actually contradicts.

The 53.7% agreement number is dominated by the 16 `unsupported`-labeled items (false-claim / negation-flip / entity-swap) that the overlap tier cannot distinguish from true claims. On the categories the overlap tier was designed for (verbatim/paraphrase/numeric support, dead links, paywalls) it scored **26/26 = 100%**.

---

## 2. Methodology

- **Pipeline under test:** the library entry point `citesure.reachability.verify_citations(citations, nli=False)` — i.e. tiers 1 (reachability/paywall) + 2 (fetch → trafilatura extract → bag-of-words overlap). No ML model downloaded; NLI tier disabled per the gate spec.
- **Independence:** the eval set was built by hand from live public pages fetched with an independent `curl` (UA `citesure/0.1.0 (+https://github.com/DawnofGenX/citesure)`), extracted with trafilatura to mirror the pipeline's own method, and hand-labeled against that extracted text. The set does **not** reuse `tests/fixtures/cases.json`.
- **Cache:** isolated at `~/.cache/citesure-eval` via `CITECHECK_CACHE_DIR` so the run is reproducible and doesn't pollute the app cache.
- **Labels:** each item carries `expected_status` (one of `supported`/`unsupported`/`ambiguous`/`unreachable`/`paywalled`) and a `labeler_confidence`. "Dead-link" labels are **intentional** (`example.invalid` TLD, a random unregistered domain, and a real 404 on a stable domain) — verified to fail DNS/return 404 before labeling, not accidental outages.
- **Agreement** = fraction of items where normalized predicted status == expected status.
- **Safety metrics** count, among items whose *expected* status is `unsupported`, how many were *predicted* `supported` (the dangerous direction for a citation verifier).

### Source domains used
docs.python.org, python.org, en.wikipedia.org, developer.mozilla.org, arxiv.org, nasa.gov, scmp.com (paywall cases). See §6 for why NYT/WSJ were excluded.

---

## 3. Confusion matrix (rows = expected, cols = predicted)

```
expected \ pred |    supported  unsupported  unreachable    paywalled    ambiguous        error
-----------------------------------------------------------------------------------------------
    supported|           16            0            0            0            0            0
  unsupported|           13            0            0            0            3            0
  unreachable|            0            0            3            0            0            0
    paywalled|            0            0            0            2            0            0
    ambiguous|            3            0            0            0            1            0
        error|            0            0            0            0            0            0
```

Reading: the only off-diagonal mass is in the `unsupported` row (13 → `supported`, 3 → `ambiguous`) and the `ambiguous` row (3 → `supported`). The pipeline **never** predicted `unsupported` or `error` — with NLI off it has no mechanism to emit a hard "the page contradicts this" verdict; its failure mode is over-claiming support.

## 4. Per-category recall & per-status precision/recall

Per-category **recall** (fraction of a category's items that received the category's canonical status). Per-category *precision* is not reported because three categories share the canonical status `supported` and three share `unsupported`, so cross-category precision is ill-defined; status-level P/R below is the well-defined multi-class view.

```
category                     status        recall  (correct/total)
dead-link                    unreachable    1.000  (3/3)
entity-swap                  unsupported    0.000  (0/5)
false-claim                  unsupported    0.000  (0/6)
negation-flip                unsupported    0.000  (0/5)
numeric-detail-supported     supported      1.000  (4/4)
paraphrase-supported         supported      1.000  (6/6)
partially-true-ambiguous     ambiguous      0.250  (1/4)
paywalled                    paywalled      1.000  (2/2)
verbatim-supported           supported      1.000  (6/6)
```

```
status             P       R  (tp/fp/fn)
supported      0.500   1.000  (16/16/0)
unsupported      n/a   0.000  (0/0/16)
unreachable    1.000   1.000  (3/0/0)
paywalled      1.000   1.000  (2/0/0)
ambiguous      0.250   0.250  (1/3/3)
error            n/a     n/a  (0/0/0)
```

- `supported` has **recall 1.0 but precision 0.5**: every true claim is caught, but half of everything labeled "supported" is actually a false/negated/swapped claim. That precision of 0.5 is the single most important number for a citation verifier — half of its "this is supported" answers are wrong.
- `unsupported` has **recall 0.0**: the pipeline never rejects a false claim as unsupported.
- `unreachable` / `paywalled`: perfect (tier 1 works).

## 5. Safety metrics (false-supported where expected = unsupported)

```
negation-flip : 5/5 = 100.0%   (every negated claim returned 'supported')
entity-swap   : 5/5 = 100.0%   (every entity-swapped claim returned 'supported')
```

These are the two categories that matter most for a *safety* gate, and both are at the worst possible value. A user relying on tier-2 output would be told, with high confidence, that a page supports "Apollo 11 did not launch on July 16, 1969" or "Python 3.11 was released on October 2, 2023" — both false.

---

## 6. Every mismatch (19) with root-cause hypothesis

Thresholds in `overlap.py`: score ≥ 0.6 → `supported`, ≥ 0.3 → `ambiguous`, else `unsupported`. All 19 mismatches are **over-claims** (predicted more supportive than expected); none under-claim.

### 6a. false-claim (6) — expected `unsupported`

| id | claim | pred | score | root-cause hypothesis |
|---|---|---|---|---|
| ind-017 | Python 3.12 was released on March 15, 2024. | ambiguous | 0.4545 | Wrong *date* only; "python/3.12/released/2023" all present → mid score. Closest to correct of the six. |
| ind-018 | Fetch API is a deprecated replacement for XMLHttpRequest being removed. | supported | 0.70 | Page discusses Fetch + XMLHttpRequest + deprecation context → high topical coverage; "deprecated/replacement/removing" are all words the page uses about *other* things. |
| ind-019 | Transformer shows recurrence is essential for SOTA MT quality. | ambiguous | 0.30 | "recurrence/machine translation/quality" appear in abstract → low-mid. Paper actually argues the opposite. |
| ind-020 | Python designed by James Gosling, first appeared 1995. | ambiguous | 0.4545 | "python/designed/first appeared" topical; "Gosling" absent → drags score down. |
| ind-021 | Apollo 11's primary objective was a permanent crewed lunar base. | supported | 0.75 | "Apollo 11 / primary objective / Moon / crewed" all on page → high coverage; the specific (false) objective isn't checked. |
| ind-022 | JSON can be used as a constructor with the new operator. | supported | 0.875 | **Page denies it using the exact same words** ("JSON is *not* a constructor… you cannot use it with the *new operator*") → near-perfect term match on a false proposition. |

**Unifying cause:** bag-of-words measures *topical* overlap, not *propositional* truth. A false claim built from the page's own vocabulary scores high. Only when the false detail introduces genuinely foreign tokens (Gosling, "March 15", "recurrence essential") does the score dip toward `ambiguous` — and even then it never reaches `unsupported`.

### 6b. negation-flip (5) — expected `unsupported`, ALL predicted `supported`

| id | claim | pred | score | root-cause hypothesis |
|---|---|---|---|---|
| ind-023 | Python 3.12 did not remove distutils from stdlib. | supported | 0.8889 | "did"/"not" are stopwords → stripped; remaining terms (python/3.12/remove/distutils/stdlib) all present. |
| ind-024 | Transformer does not dispense with recurrence & convolutions entirely. | supported | 0.8333 | Negation stripped; "transformer/recurrence/convolutions" all in abstract. |
| ind-025 | Apollo 11 did not launch on July 16, 1969. | supported | **1.0** | Claim terms after stopword removal = {apollo,11,launch,july,16,1969}; **all** appear verbatim on the page → perfect score on a directly-false statement. |
| ind-026 | Armstrong did not become first human to walk on the Moon during Apollo 11. | supported | 0.8333 | Negation stripped; every other term present. |
| ind-027 | BERT does not obtain new SOTA results on eleven NLP tasks. | supported | 0.7273 | Negation stripped; "BERT/state-of-the-art/eleven/natural language processing/tasks" all in abstract. |

**Unifying cause:** `clean_claim()` removes negation operators ("not", "no", "never", "did not") as stopwords *before* scoring, so the pipeline literally cannot see the flip. This is the single clearest safety defect: a verifier whose job is to catch "the page says the opposite" is blind to the word that encodes "opposite."

### 6c. entity-swap (5) — expected `unsupported`, ALL predicted `supported`

| id | claim | pred | score | root-cause hypothesis |
|---|---|---|---|---|
| ind-028 | Python 3.11 was released on October 2, 2023. | supported | **1.0** | Swapped token "3.11" **literally appears** in the top passage ("…in Python 3.12, compared to **3.11**"), so every claim term hits → 1.0. |
| ind-029 | Armstrong & Aldrin descended aboard LM *Columbia*, landing Sea of Tranquility Jul 20 20:17 UTC. | supported | **1.0** | LM name swapped (Eagle→Columbia) but "Columbia" also appears on the page (command module) → all terms hit. |
| ind-030 | Armstrong joined NASA's predecessor NACA in 1965. | supported | 0.625 | Year/entity detail swapped; "Armstrong/NASA/NACA/aeronautical/pilot" present. |
| ind-031 | Our model achieves 28.4 BLEU on WMT 2014 En→Fr. | supported | 0.8571 | Number↔language swap: **28.4 is the En→German** figure; En→French is **41.8**. Both numbers + "BLEU/WMT/2014/English/French/German" all appear on the page → high coverage on a false pairing. |
| ind-032 | Van Rossum first released Python in 1991 as version 1.0. | supported | 0.7692 | Version/year detail swapped; "Van Rossum/Python/1991/version" present. |

**Unifying cause:** bag-of-words cannot bind an attribute to the right entity. If the swapped token happens to appear anywhere on the page (very common on dense reference pages), the score stays high. ind-028/ind-029 hitting exactly 1.0 shows the failure is total, not marginal.

### 6d. partially-true-ambiguous (3 of 4) — expected `ambiguous`

| id | claim | pred | score | root-cause hypothesis |
|---|---|---|---|---|
| ind-033 | Py3.12 released Oct 2 2023 **and** introduced a brand-new Rust garbage collector. | supported | 0.6111 | True half dominates term coverage; fabricated "Rust garbage collector" adds few foreign tokens → just over 0.6. |
| ind-034 | Apollo 11 landed Sea of Tranquility Jul 20 1969 **and** crew walked three full weeks. | supported | 0.6087 | True half dominates; "three full weeks walking" barely dilutes coverage. |
| ind-036 | Fetch API fetches resources across network **and** requires a paid subscription. | supported | 0.7143 | True half dominates; "paid subscription" is off-topic noise that doesn't lower the score enough. |

**Contrast — the one correct item, ind-035** (BERT bidirectional encoder … trained exclusively on Shakespeare's works), predicted `ambiguous` at **0.3846**: its fabricated half ("Shakespeare's complete works") introduces tokens with *zero* overlap with the BERT abstract, dragging the score below 0.6. So the tier *can* land on `ambiguous` when the false half is lexically foreign — but when the false half reuses the page's vocabulary (as in 033/034/036), it crosses into `supported`.

### Summary of root causes
1. **Negation is stopword-stripped** before scoring → 100% false-supported on negation-flips (worst case).
2. **No entity–attribute binding** → swapped entities that appear elsewhere on the page still score ~1.0.
3. **Topical ≠ propositional** → false claims phrased in the page's own vocabulary score high; the tier has no path to `unsupported` at all (it never emitted that status once).
4. **Partial-truth handling is lexical, not logical** → a mixed claim is judged by whether the false half is lexically foreign, not by whether any conjunct is false.

---

## 7. Deviations from the brief (disclosed)

- **Paywall source = SCMP, not NYT/WSJ.** The brief named NYT/WSJ as paywall candidates, but both **bot-block our UA**: `nytimes.com` returns **403** to the eval UA and `wsj.com`/`barrons.com` robots.txt **Disallow** it. A 403/robots-block is an *unreachable* signal, not a *paywall* — labeling those "paywalled" would be dishonest (the response isn't a paywall, it's a bot block). I therefore used **SCMP article URLs**, which return **HTTP 200 + a genuine metered paywall** (`isAccessibleForFree: false`, `.piano-metering__paywall-container`) that citesure's own `detect_paywall` fires on. Both SCMP items were classified `paywalled` correctly. Confidence on these two labels is **medium** (metered, not hard-walled).
- **Per-category metric = recall, not P/R.** The brief asked for per-category precision/recall, but three categories share canonical status `supported` and three share `unsupported`, so cross-category *precision* is ill-defined (a correct `supported` on a paraphrase item is not a false positive for the verbatim category). I report per-category **recall** (well-defined) plus **status-level multi-class P/R** from the confusion matrix, which is the meaningful view. This is a methodological correction, not a shortcut.

## 8. Honest gaps & limitations

1. **N=41, single run.** No confidence intervals; a ±1 flip in any safety item moves the headline numbers. The 100% safety-failure rates are robust (5/5 each), but the 53.7% agreement and 0.5 supported-precision are point estimates.
2. **No NLI comparison.** This run is NLI **OFF** only, per the gate spec. We have not measured how much the NLI tier would recover on negation/entity-swap — that is Phase B's job. The overlap-tier failure here is expected and is precisely what NLI is meant to fix; this report does *not* claim the whole product fails, only that the overlap tier alone is unsafe as a gate.
3. **Label subjectivity on `ambiguous`.** The four partially-true items are labeled `ambiguous` at **medium** confidence; a stricter labeler might call some `unsupported` (any false conjunct ⇒ not fully supported). If relabeled `unsupported`, the false-supported rate rises further — the current numbers are, if anything, generous to the pipeline.
4. **Topical-overlap ceiling.** Because the tier can never emit `unsupported`, its best possible agreement on this set is bounded below 100% even with perfect topical matching. The 53.7% is therefore a floor-limited figure, not a pure accuracy estimate.
5. **Live-web drift.** Claims were verified against pages fetched 2026-09-09. Wikipedia/NASA/MDN content can change; a re-run could shift individual scores (though the structural failures — negation stripping, no entity binding — are code properties, not content properties, and will persist).
6. **Cache reuse.** The run used a warm cache (`~/.cache/citesure-eval`). Tier-1 reachability/paywall results are cached; a cold-cache re-run should reproduce them but was not separately timed here.

## 9. Recommendations (for Phase B / fixes)

1. **Do not gate on tier 2 alone.** Until negation and entity-binding are handled, a `supported` verdict from the overlap tier must be treated as "topically related," not "factually supported."
2. **Preserve negation.** Keep "not/no/never/did not/cannot" out of the stopword set, or add an explicit negation-awareness pass before scoring.
3. **Add entity–attribute binding** (or lean on the NLI tier) so a swapped token that appears elsewhere on the page doesn't satisfy the claim.
4. **Give the tier a path to `unsupported`** — currently the lowest bucket is `ambiguous`; a hard-reject path is needed for a safety gate.
5. **Re-run with NLI ON** (Phase B) to quantify recovery on the 16 `unsupported` items and confirm the NLI tier closes the negation/entity-swap gap.

## 10. Reproduction

```bash
cd /home/hermes/citesure
# (cache already warm at ~/.cache/citesure-eval; set CITECHECK_CACHE_DIR to reuse)
CITECHECK_CACHE_DIR=$HOME/.cache/citesure-eval .venv/bin/python evals/run_eval.py --set evals/independent_set.json
```
Outputs: stdout (confusion matrix, per-category recall, per-status P/R, safety metrics, mismatch list) and `evals/results/<timestamp>.json`.




---

## 2026-09-29 addendum

Post-Phase-B v2 results are in `evals/RESULTS_v2_BASELINE.md` (frozen baseline + appended
re-confirmation section) and `evals/RESULTS_v2_COMPOUND_CLAIM.md` (2026-09-29 re-confirmation
runs: three metrically-identical saves of the same 93/108 = 86.1% v2 verdict set; the frozen
41-case gate remains 32/41 = 78.0%). The sections above describe the original NLI-off v1
report and are left as written.
