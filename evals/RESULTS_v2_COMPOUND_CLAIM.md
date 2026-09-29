# Independent Eval Set v2 — 2026-09-29 Re-confirmation Runs

**Date:** 2026-09-29
**Set:** `evals/independent_set_v2.json` — 108 cases (verbatim-supported 32,
negation-flip 32, entity-swap 32, dead-link 4, paywalled 4, partially-true-ambiguous 4)
**Model:** `cross-encoder/nli-deberta-v3-base`, NLI tier **on** (`"nli": true`)
**Code state at run time:** see "What these runs are" below — the three runs were
**not** tied to distinct commits; this is documented honestly, not glossed over.

Result files (all three committed under `evals/results/`):

| Directory | Timestamp | Size |
|---|---|---|
| `evals/results/v2_entity_swap_fix/20260929-073340.json` | 07:33:40 UTC | 100,386 bytes |
| `evals/results/v2_passage_fallback/20260929-075200.json` | 07:52:00 UTC | 100,386 bytes |
| `evals/results/v2_compound_claim/20260929-081159.json` | 08:11:59 UTC | 100,386 bytes |

---

## What these runs are: one result, saved three times

The three runs are **metrically identical**. Verified directly from the JSONs on
disk (2026-09-29, this doc's commit):

- `confusion_matrix`, `per_category_recall`, `per_status_pr`, `safety`, `total`,
  `matched`, `agreement_pct`: **identical across all three files**.
- Per-record diff, earliest vs latest run: **0 of 108 records differ** (records
  compared field-by-field: id, category, expected, predicted, match, score,
  tier_reached, notes, evidence).
- The only difference between any two files is the `generated_utc` timestamp
  field. The `cache_dir` (`/tmp/citesure-v2`) is the same in all three.

Furthermore, all three are **record-for-record identical to the tracked,
already-committed** `evals/results/v2_slot_conflict/20260929-054505.json`
(0 of 108 records differ), which is the run behind the `ff05177`
slot-gated pooled-contradiction numbers (v2 93/108 = 86.1%).

**Therefore these runs are a re-confirmation of the `ff05177` result, not a new
result.** The frozen 41-case gate (`evals/independent_set.json`) was not re-run
by any of these three files — their record ids have **zero overlap** with the
41-case set, so they carry no v1 evidence. The governing v1 figure remains the
tracked `evals/results/v1_slot_conflict/20260929-054244.json`
(32/41 = 78.05%), which in the committed history ran at 05:42:44, **before**
every one of the v2 runs below.

## Known unknown: what the three directory labels meant

The directory names (`entity_swap_fix`, `passage_fallback`, `compound_claim`)
suggest runs intended to measure an entity-swap fix, the passage fallback, and
compound-claim scoring respectively. Git history does **not** support that
reading as three distinct code states:

- `b723a48` ("passage fallback and compound claim scoring") was committed at
  **09:58 UTC — after all three runs** (latest: 08:11 UTC).
- `ff05177`/`5901fcb` (slot-gated contradiction, the entity-swap improvement
  6→5) were committed **before** the first run.
- No commit message or result-file field records which working-tree state each
  run used.

We cannot establish from git history what the labels distinguished. Two readings
are consistent with the evidence: (a) the runs re-measured the same effective
code state, or (b) the intended fixes were present in the working tree but had
**zero effect on the 108-case verdicts**. Either way, the honest statement is:
**no measurable effect was captured.** Do not cite these three files as evidence
that passage fallback or compound-claim scoring changed anything.

---

## Numbers (re-verified from `20260929-081159.json` on disk)

**Agreement: 93/108 = 86.1%** — matches `ff05177`'s recorded v2 figure exactly.

### Confusion matrix (rows = expected, cols = predicted)

| expected \ predicted | supported | unsupported | ambiguous | unreachable | paywalled | error |
|---|---|---|---|---|---|---|
| supported (31) | **26** | 5 | 1 | 0 | 0 | 0 |
| unsupported (64) | 5 | **59** | 0 | 0 | 0 | 0 |
| ambiguous (4) | 2 | 2 | **0** | 0 | 0 | 0 |
| unreachable (4) | 0 | 0 | 0 | **4** | 0 | 0 |
| paywalled (4) | 0 | 0 | 0 | 0 | **4** | 0 |
| error (0) | 0 | 0 | 0 | 0 | 0 | **0** |

### Safety metrics

| Metric | Value |
|---|---|
| negation-flip false-supported | **0 / 32** (rate 0.0) |
| entity-swap false-supported | **5 / 32** (rate 0.1562) |

### Per-category recall

| Category | Recall | Rate |
|---|---|---|
| dead-link | 4/4 | 1.000 |
| negation-flip | 32/32 | 1.000 |
| paywalled | 4/4 | 1.000 |
| entity-swap | 27/32 | 0.844 |
| verbatim-supported | 26/32 | 0.813 |
| partially-true-ambiguous | 0/4 | 0.000 |
| false-claim | — | total 0, recall NaN |
| numeric-detail-supported | — | total 0, recall NaN |
| paraphrase-supported | — | total 0, recall NaN |

`false-claim`, `numeric-detail-supported` and `paraphrase-supported` report
`total: 0` and `NaN` recall because **the v2 set contains no cases in those
categories** — that is a set-composition gap, not a result. The categories are
not measured at all.

### Frozen-gate context (unchanged by these runs)

- v1 41-case frozen gate: **32/41 = 78.0%** (`v1_slot_conflict`, ran first at
  05:42:44; delta 0 vs the v1 baseline — no regression).
- v2 108-case set: **93/108 = 86.1%** (up from the 76.9% v2 NLI-on baseline;
  re-confirmed three times, identical records, by the runs in this doc).
- The two sets are **not directly comparable** (see `RESULTS_v2_BASELINE.md`);
  neither number contradicts the other.

---

## Reproduce

Same harness as the baseline doc:

```bash
cd /home/hermes/citesure
CITECHECK_NLI_MODEL=/home/hermes/.cache/huggingface/hub/models--cross-encoder--nli-deberta-v3-base/snapshots/6c749ce3425cd33b46d187e45b92bbf96ee12ec7 \
CITECHECK_CACHE_DIR=/tmp/citesure-v2 .venv/bin/python evals/run_eval.py \
  --set evals/independent_set_v2.json --nli --out evals/results/<name>
```

The four scratch scripts committed alongside these results
(`bench_finetune.py`, `evals/sentence_max_probe.py`, `probe_definitive.py`,
`sweep_params.py`) are offline probes/tuning harnesses against the same cache
and set; none of them produced the numbers above, and none changed `src/`.
