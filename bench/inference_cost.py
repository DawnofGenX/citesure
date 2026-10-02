"""Inference-cost study for the citesure NLI tier.

Measures throughput and peak memory for the same cross-encoder at FP32 and at
dynamically-quantized INT8, and scores each precision through citesure's real
pipeline against the committed 108-case eval set so the accuracy delta is
directly comparable to the published 76.9% (83/108) baseline.

Two run modes, because the two halves need different environments (see
``bench/RESULTS.md`` for the measured reason):

* Throughput / memory (runs in any torch env; CUDA torch lives in
  ``/home/hermes/heartlib-venv``)::

      HF_HUB_OFFLINE=1 /home/hermes/heartlib-venv/bin/python -m bench.inference_cost \
          --mode throughput --device cuda

* Accuracy through the real citesure pipeline (needs citesure's fetch/extract
  deps, so it runs in citesure's own venv)::

      ./.venv/bin/python -m bench.inference_cost --mode accuracy

``quantize_dynamic`` emits CPU-only kernels (a CUDA forward raises
``RuntimeError: index is on cpu``), so INT8 is always measured on CPU; FP32 is
measured on both devices. No number here is estimated — every field is printed
from the run that produced it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL = "cross-encoder/nli-deberta-v3-base"
ROOT = Path(__file__).resolve().parent.parent
EVAL_SET = ROOT / "evals" / "independent_set_v2.json"
PAGE_POOL = ROOT / "evals" / "page_pool_verified.json"

#: Published full-pipeline agreement with the NLI tier ON (citesure README,
#: commit 34db250): 83/108 = 76.9%.
BASELINE_MATCHED = 83
BASELINE_N = 108
MAX_LENGTH = 256


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------
def load_model(precision: str, device: str):
    """Return ``(model, tokenizer, device_used)`` at the requested precision.

    ``int8`` uses ``torch.ao.quantization.quantize_dynamic`` on Linear layers
    only. That route produces CPU-only quantized kernels, so an ``int8``
    request is pinned to CPU regardless of the ``device`` argument — moving the
    result to CUDA raises at forward time, so pretending otherwise would be a
    fabricated result.
    """
    model = AutoModelForSequenceClassification.from_pretrained(MODEL).eval()
    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    if precision == "int8":
        if device != "cpu":
            print(f"[load_model] int8 quantize_dynamic is CPU-only; "
                  f"ignoring device={device!r} and running on cpu")
        model = torch.ao.quantization.quantize_dynamic(
            model, {torch.nn.Linear}, dtype=torch.qint8
        )
        return model, tokenizer, "cpu"
    if device == "cuda":
        if not torch.cuda.is_available():
            raise SystemExit("--device cuda requested but torch.cuda.is_available() is False")
        model = model.cuda()
        return model, tokenizer, "cuda"
    return model, tokenizer, "cpu"


# ---------------------------------------------------------------------------
# Eval-set loading (the REAL shape, not the guessed one)
# ---------------------------------------------------------------------------
def load_pairs(limit: int | None = None) -> list[tuple[str, str]]:
    """Read ``(claim, passage)`` pairs from the committed eval set.

    The real file is a **top-level JSON list** of 108 case dicts, each with
    ``id``/``category``/``expected_status``/``url``/``claim`` — there is no
    ``passage`` field. Passage text lives in ``evals/page_text/<slug>.txt`` and
    is mapped from the case ``url`` via ``evals/page_pool_verified.json``
    (``entries[*].text_path``). Cases whose page failed to fetch (the
    ``unreachable`` trio) have no saved text and are skipped, leaving 105
    resolvable pairs.
    """
    cases = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    pool = json.loads(PAGE_POOL.read_text(encoding="utf-8"))
    by_url = {e["url"]: e for e in pool.get("entries", [])}
    pairs: list[tuple[str, str]] = []
    for case in cases:
        entry = by_url.get(case.get("url"))
        if not entry or not entry.get("text_path"):
            continue
        text_path = Path(entry["text_path"])
        if not text_path.exists():
            continue
        claim = case.get("claim")
        if not claim:
            continue
        pairs.append((claim, text_path.read_text(encoding="utf-8")))
    if limit:
        pairs = pairs[:limit]
    return pairs


# ---------------------------------------------------------------------------
# Throughput / memory
# ---------------------------------------------------------------------------
def bench(precision: str, device: str, pairs, warmup: int = 2, iters: int = 5) -> dict:
    """Time batched scoring of ``pairs`` at one precision and record peak memory."""
    import resource

    model, tok, dev = load_model(precision, device)

    def run(batch):
        enc = tok(
            [p[1] for p in batch],  # premise = passage
            [p[0] for p in batch],  # hypothesis = claim
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=MAX_LENGTH,
        )
        enc = {k: v.to(dev) for k, v in enc.items()}
        with torch.no_grad():
            return model(**enc).logits

    for _ in range(warmup):
        run(pairs[:8])
    if dev == "cuda":
        torch.cuda.synchronize()

    latencies = []
    for _ in range(iters):
        t0 = time.perf_counter()
        run(pairs)
        if dev == "cuda":
            torch.cuda.synchronize()
        latencies.append(time.perf_counter() - t0)

    peak_vram = 0.0
    if dev == "cuda":
        torch.cuda.reset_peak_memory_stats()
        run(pairs)
        torch.cuda.synchronize()
        peak_vram = torch.cuda.max_memory_allocated() / (1024 ** 2)

    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # KiB -> MiB

    return {
        "precision": precision,
        "device": dev,
        "n_pairs": len(pairs),
        "mean_sec": round(statistics.mean(latencies), 4),
        "p50_sec": round(statistics.median(latencies), 4),
        "pairs_per_sec": round(len(pairs) / statistics.mean(latencies), 2),
        "peak_vram_mb": round(peak_vram, 1),
        "peak_rss_mb": round(peak_rss, 1),
        "max_length": MAX_LENGTH,
    }


# ---------------------------------------------------------------------------
# Accuracy through the REAL citesure pipeline (tiers 1+2+3)
# ---------------------------------------------------------------------------
def accuracy(precision: str, device: str = "cpu", limit: int | None = None) -> dict:
    """Score the eval set through citesure's pipeline with an injected encoder.

    Reproducing the published 76.9% requires the *whole* pipeline (overlap
    tiers + NLI banding + contradiction vetoes), not a bare entailment
    threshold on one pair per case — so this loads citesure, builds an
    :class:`NLICrossEncoder` at the requested precision and installs it into
    the module cache, then runs ``verify_citations(use_nli=True)`` over every
    case and compares the final status to ``expected_status``.

    Needs citesure's own dependencies (fetch/extract); run it from the citesure
    venv. Returns matched/total plus the delta against the 83/108 baseline.
    """
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    os.environ.setdefault("CITECHECK_CACHE_DIR", str(Path.home() / ".cache" / "citesure"))
    os.environ.setdefault("HF_HUB_CACHE", str(Path.home() / ".cache" / "citesure" / "hf"))

    from citesure.citations import Citation
    from citesure.reachability import verify_citations
    import citesure.nli as nli_mod

    model, tok, dev = load_model(precision, device)
    encoder = nli_mod.NLICrossEncoder(
        name=MODEL,
        model=model,
        tokenizer=tok,
        id2label=getattr(model.config, "id2label", None) or None,
    )
    with nli_mod._cache_lock:
        nli_mod._model_cache[MODEL] = encoder

    cases = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    if limit:
        cases = cases[:limit]

    async def _run():
        citations = [
            Citation(citation_id=c["id"], url=c["url"], claim=c["claim"]) for c in cases
        ]
        return await verify_citations(citations, use_overlap=True, use_nli=True)

    report = asyncio.run(_run())

    matched = 0
    per_category: dict[str, list[int]] = {}
    mismatches = []
    for case, verdict in zip(cases, report.verdicts):
        ok = verdict.status.value == case["expected_status"]
        matched += int(ok)
        bucket = per_category.setdefault(case["category"], [0, 0])
        bucket[0] += int(ok)
        bucket[1] += 1
        if not ok:
            mismatches.append(
                {"id": case["id"], "category": case["category"],
                 "expected": case["expected_status"], "predicted": verdict.status.value,
                 "score": verdict.score}
            )

    n = len(cases)
    return {
        "precision": precision,
        "device": dev,
        "n": n,
        "matched": matched,
        "agreement": round(matched / n, 4) if n else None,
        "baseline_matched": BASELINE_MATCHED,
        "baseline_n": BASELINE_N,
        "baseline_agreement": round(BASELINE_MATCHED / BASELINE_N, 4),
        "delta_cases": matched - BASELINE_MATCHED,
        "per_category": {
            k: {"correct": v[0], "total": v[1]} for k, v in sorted(per_category.items())
        },
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_throughput_table(rows: list[dict]) -> None:
    print("\n| precision | device | pairs | mean s | p50 s | pairs/s | peak VRAM MB | peak RSS MB |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        vram = r["peak_vram_mb"] if r["peak_vram_mb"] else "n/a (cpu)"
        print(f"| {r['precision']} | {r['device']} | {r['n_pairs']} | {r['mean_sec']} | "
              f"{r['p50_sec']} | {r['pairs_per_sec']} | {vram} | {r['peak_rss_mb']} |")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["throughput", "accuracy", "both"], default="throughput")
    ap.add_argument("--precisions", default="fp32,int8")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    precisions = [p.strip() for p in args.precisions.split(",") if p.strip()]

    if args.mode in ("throughput", "both"):
        pairs = load_pairs(args.limit)
        if not pairs:
            raise SystemExit(f"no pairs parsed from {EVAL_SET} — inspect the file's real shape")
        print(f"[throughput] {len(pairs)} (claim, passage) pairs from {EVAL_SET.name}")
        rows = [bench(p, args.device, pairs) for p in precisions]
        print(json.dumps(rows, indent=2))
        _print_throughput_table(rows)

    if args.mode in ("accuracy", "both"):
        print("\n[accuracy] running citesure's real pipeline (tiers 1+2+3) per precision")
        acc_rows = [accuracy(p, "cpu", args.limit) for p in precisions]
        print(json.dumps(acc_rows, indent=2))
        print("\n| precision | device | matched | total | agreement | vs baseline 83/108 (76.9%) |")
        print("|---|---|---|---|---|---|")
        for r in acc_rows:
            print(f"| {r['precision']} | {r['device']} | {r['matched']} | {r['n']} | "
                  f"{r['agreement']:.1%} | {r['delta_cases']:+d} cases |")


if __name__ == "__main__":
    main()
