# Inference-cost study of the citesure NLI tier (FP32 vs INT8)

**Date:** 2026-10-02
**Model:** `cross-encoder/nli-deberta-v3-base` (label order: 0=contradiction, 1=entailment, 2=neutral)
**Hardware:** NVIDIA GeForce RTX 5090 Laptop, 24463 MiB (WSL2); CPU path on the same host.
**Harness:** `bench/inference_cost.py`

This is a measurement, not a library. Every number below is copied from a run
whose command is given in the Reproduce section. No figure is estimated or
extrapolated.

## Headline

**Dynamic INT8 quantization of this cross-encoder is unusable.** It is only
~11% faster than FP32 on the same CPU (8.09 vs 7.31 pairs/s) — still 10× slower
than FP32 on the GPU — and it collapses accuracy from **93/108 = 86.1%** to
**72/108 = 66.7%**, because the quantized model cannot tell an entailed pair from
a contradicted one. On a trivially identical pair ("The sky is blue." vs itself)
FP32 gives entailment 0.9973 while INT8 gives 0.0274. **The default path must
stay FP32.**

## Preflight (Task 1.1)

The exact preflight block from the plan, run in `/home/hermes/heartlib-venv`
(the only CUDA torch env):

```
WARM_OK device=cuda secs=14.017
```

No transformers-version error. The CUDA path was taken; no CPU fallback was
needed. (The 14 s is first-call kernel autotuning, not steady-state.)

## Real shape of the eval set (Task 1.2)

The plan's `load_pairs()` guessed a dict with `cases`/`rows` keys and
`claim`+`passage` fields. **The real file is a top-level JSON list of 108 case
dicts**, keys:

```
id, category, expected_status, labeler_confidence, url, claim, notes
```

There is **no `passage` field**. Passage text is stored separately in
`evals/page_text/<slug>.txt`, mapped from each case's `url` via
`evals/page_pool_verified.json` (`entries[*].text_path`). `load_pairs()` was
adapted to that: it resolves `url → text_path`, reads the saved page text, and
returns `(claim, passage)` pairs. 105 of 108 cases resolve (the three
`unreachable` cases — `ind-241/242/243` — have no saved text by design, since
there is nothing to fetch). The accuracy run uses all 108 cases through
citesure's real pipeline, which handles the unreachable ones at tier 1.

No substitute dataset was used; the passages are citesure's own committed page
text.

## Throughput and memory (Task 1.3)

105 `(claim, passage)` pairs, `max_length=256`, 2 warmup + 5 timed iterations,
batch = all 105 pairs in one forward pass. Measured in `/home/hermes/heartlib-venv`.

| precision | device | pairs | mean s | p50 s | pairs/s | peak VRAM MB | peak RSS MB |
|---|---|---|---|---|---|---|---|
| fp32 | cuda | 105 | 1.299 | 1.2501 | 80.83 | 4159.7 | 3056.6 |
| fp32 | cpu | 105 | 14.3697 | 14.3152 | 7.31 | n/a (cpu) | 5993.8 |
| int8 | cpu | 105 | 12.9777 | 12.6928 | 8.09 | n/a (cpu) | 7487.0 |

**Device constraint, stated plainly:** `torch.ao.quantization.quantize_dynamic`
emits CPU-only quantized kernels. Moving the quantized model to CUDA succeeds
silently but the first forward pass raises
`RuntimeError: Expected all tensors to be on the same device, but got index is
on cpu, different from other tensors on cuda:0`. So **there is no INT8 GPU
number** — INT8 is a CPU-only measurement here, and the harness pins it to CPU
rather than fabricate a GPU figure. The only fair INT8-vs-FP32 throughput
comparison is therefore the **CPU** row pair: FP32 CPU 7.31 pairs/s vs INT8 CPU
8.09 pairs/s.

## Accuracy through citesure's real pipeline (Task 1.4)

The published metric is **full-pipeline agreement** (`evals/run_eval.py`):
overlap tiers + NLI banding + contradiction vetoes, compared to
`expected_status`. A bare entailment threshold on one pair per case cannot
reproduce it, so `accuracy()` loads citesure, injects an `NLICrossEncoder` at
the requested precision into the module cache, and runs
`verify_citations(use_nli=True)` over all 108 cases. Run in citesure's own venv
(it needs citesure's fetch/extract dependencies).

| precision | device | matched | total | agreement | vs README 83/108 (76.9%) |
|---|---|---|---|---|---|
| fp32 | cpu | 93 | 108 | 86.1% | +10 cases |
| int8 | cpu | 72 | 108 | 66.7% | −11 cases |

Per-category recall (correct/total):

| category | fp32 | int8 |
|---|---|---|
| dead-link | 4/4 | 4/4 |
| paywalled | 4/4 | 4/4 |
| negation-flip | 32/32 | 32/32 |
| entity-swap | 27/32 | 32/32 |
| verbatim-supported | **26/32** | **0/32** |
| partially-true-ambiguous | 0/4 | 0/4 |

The INT8 failure is concentrated and diagnostic: **every one of the 32
verbatim-supported cases is missed** (0/32), each scored in the 0.01–0.08
entailment band — the quantized model reads a verbatim-supported claim as
*unsupported*. It "passes" the negation-flip and entity-swap categories only by
collapsing everything toward unsupported, which is the wrong reason.

### Why FP32 reads 86.1% and not the README's 76.9%

The README (commit `34db250`) publishes **83/108 = 76.9%**. My FP32 run reads
**93/108 = 86.1%**, and this was investigated rather than published blind:

- My FP32 run is **record-for-record identical (108/108 predicted statuses)** to
  citesure's latest committed v2 result
  `evals/results/v2_post_score_fix/20260929-200951.json`, which is itself
  93/108 = 86.1%.
- It is **94/108** identical to the older `evals/results/v2_baseline/20260928-133916.json`
  (83/108), the run behind the README figure.
- citesure's own committed result files show the 76.9% was superseded: after
  `v2_baseline` (76.85%), the tracked runs `v2_slot_conflict`,
  `v2_entity_swap_fix`, `v2_passage_fallback`, `v2_compound_claim`,
  `v2_pre_score_audit` and `v2_post_score_fix` all record **86.11% (93/108)**.
  citesure's `evals/RESULTS_v2_COMPOUND_CLAIM.md` states 93/108 = 86.1% is the
  current v2 figure ("up from the 76.9% v2 NLI-on baseline").

So 76.9% is the *historical baseline the README still quotes*; the *current
committed* figure is 86.1%, and my harness reproduces it exactly. I report the
delta against **both**:

- vs README baseline 83/108: FP32 **+10 cases**, INT8 **−11 cases**.
- vs current committed baseline 93/108: FP32 **0 cases** (exact reproduction),
  INT8 **−21 cases**.

The accuracy story is the same either way: FP32 reproduces citesure's committed
behaviour, and INT8 loses 21 cases against it.

## Interpretation

1. **Dynamic INT8 is a loss on the axis that matters.** It buys ~11% on CPU
   throughput (8.09 vs 7.31 pairs/s — within run-to-run noise) while destroying
   accuracy (86.1% → 66.7%). Both are far below FP32 on the GPU (80.83 pairs/s),
   so there is no configuration in this study in which INT8 is the right choice
   for this model.
2. **The mechanism is a threshold collapse, not random noise.** Quantized
   entailment probabilities land in a narrow ~0.01–0.08 band, below the 0.3
   ambiguous floor, so the pipeline downgrades everything to unsupported. This
   is why verbatim-supported goes 26/32 → 0/32 while the negative categories
   stay superficially "correct".
3. **The GPU is where the wins are, not quantization.** FP32 on the RTX 5090
   runs at 80.83 pairs/s — **11× the FP32 CPU rate** — with a 4159.7 MB VRAM
   footprint. For this model, spending VRAM is strictly better than spending
   precision.
4. **Scope limit:** this measures dynamic (post-training, weight-only) INT8 for
   one model. It does not evaluate ONNX Runtime INT8 or static/QAT quantization,
   which may behave differently. The cached `onnx/` directory is empty and no
   env has `onnxruntime`, so those routes were out of scope per the plan.

## Reproduce

Throughput and memory (CUDA torch env; the model is read from the offline cache):

```bash
cd /home/hermes/citesure
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
HF_HUB_CACHE=/home/hermes/.cache/citesure/hf \
/home/hermes/heartlib-venv/bin/python -m bench.inference_cost \
    --mode throughput --device cuda --precisions fp32,int8

HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
HF_HUB_CACHE=/home/hermes/.cache/citesure/hf \
/home/hermes/heartlib-venv/bin/python -m bench.inference_cost \
    --mode throughput --device cpu --precisions fp32
```

Accuracy through the real pipeline (citesure's own venv, needs fetch/extract deps):

```bash
cd /home/hermes/citesure
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
HF_HUB_CACHE=/home/hermes/.cache/citesure/hf \
./.venv/bin/python -m bench.inference_cost --mode accuracy --precisions fp32,int8
```

The two venvs differ by design: citesure's venv has CPU-only torch but the
fetch/extract stack; `heartlib-venv` has CUDA torch but not citesure's deps.
Both use the same model snapshot from `/home/hermes/.cache/citesure/hf`.

## Environment

| Item | Value |
|---|---|
| CUDA torch env | `/home/hermes/heartlib-venv` — torch 2.10.0+cu128, transformers 4.57.0 |
| Pipeline env | `/home/hermes/citesure/.venv` — torch 2.14.0+cpu, transformers 5.16.1 |
| GPU | NVIDIA GeForce RTX 5090 Laptop, 24463 MiB |
| Model cache | `/home/hermes/.cache/citesure/hf/models--cross-encoder--nli-deberta-v3-base` (offline) |
| Quantization | `torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)` |

Version skew between the two venvs is expected and did not affect results: the
INT8 entailment collapse reproduces identically in both (identical-pair
entailment 0.0274 in each).
