#!/usr/bin/env python3
"""Benchmark NLI checkpoints on a citesure independent eval set.

Runs the real library pipeline once per checkpoint against the same eval set
and cache, and scores agreement plus the two safety metrics.

Usage:
    .venv/bin/python evals/bench_models.py --models MODEL_A MODEL_B \
        --set evals/independent_set_v2.json --out evals/results/model_bench.json
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def load_set(eval_set: Path) -> list[dict]:
    """Load an independent eval set (a JSON list of case dicts)."""
    return json.loads(eval_set.read_text(encoding="utf-8"))


def resolve_model(name: str, hf_root: str) -> str:
    """Map an HF repo id to its local absolute snapshot path.

    citesure passes ``CITECHECK_CACHE_DIR`` to ``from_pretrained(cache_dir=...)``,
    so pointing that at an eval page-cache dir makes transformers look for the
    weights *there* and miss the real HF cache entirely — every case then fails
    to load. Resolving to the snapshot path sidesteps the shadowing.
    """
    if os.path.isabs(name) or os.path.isdir(name):
        return name
    slug = "models--" + name.replace("/", "--")
    snap_root = Path(hf_root) / slug / "snapshots"
    if snap_root.is_dir():
        snaps = sorted(snap_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        if snaps:
            return str(snaps[0])
    return name  # not cached locally; let transformers resolve it (or fail clearly)


def score(records: list[dict], items: list[dict]) -> dict:
    """Score one run's per-case records against the set's expected statuses."""
    expected = {i["id"]: i["expected_status"] for i in items}
    matched = 0
    per_cat: Counter[str] = Counter()
    per_cat_total: Counter[str] = Counter()
    false_supported: Counter[str] = Counter()
    unsupported_total: Counter[str] = Counter()

    for r in records:
        exp = expected.get(r["id"])
        pred = r["predicted"]
        if pred == exp:
            matched += 1
        cat = r["category"]
        per_cat_total[cat] += 1
        if pred == exp:
            per_cat[cat] += 1
        if exp == "unsupported":
            unsupported_total[cat] += 1
            if pred == "supported":
                false_supported[cat] += 1

    total = len(records)
    return {
        "matched": matched,
        "total": total,
        "agreement_pct": round(100.0 * matched / total, 2) if total else 0.0,
        "per_category": {
            c: {"correct": per_cat[c], "total": per_cat_total[c],
                "recall": round(per_cat[c] / per_cat_total[c], 4) if per_cat_total[c] else None}
            for c in sorted(per_cat_total)
        },
        "false_supported": {
            c: {"count": false_supported[c], "total": unsupported_total[c],
                "rate": round(false_supported[c] / unsupported_total[c], 4) if unsupported_total[c] else None}
            for c in ("negation-flip", "entity-swap")
            if unsupported_total.get(c)
        },
    }


async def verify_one(item: dict, nli_model: str) -> dict:
    """Run the library pipeline for one case; never raise."""
    from citesure.citations import Citation
    from citesure.reachability import verify_citations

    citation = Citation(citation_id=item["id"], url=item["url"], claim=item["claim"])
    try:
        report = await verify_citations(
            [citation], use_overlap=True, use_nli=True, nli_model=nli_model
        )
        v = report.verdicts[0]
        status = v.status.value
        predicted = "error" if str(status).startswith("error") else status
    except Exception as exc:  # noqa: BLE001 - benchmark must not abort
        predicted = "error"
        v = None
    return {
        "id": item["id"],
        "category": item["category"],
        "expected": item["expected_status"],
        "predicted": predicted,
        "score": getattr(v, "score", None),
        "tier_reached": getattr(v, "tier_reached", 0),
        "evidence": (getattr(v, "evidence", "") or "")[:200],
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--set", default="evals/independent_set_v2.json")
    ap.add_argument("--out", default="evals/results/model_bench.json")
    ap.add_argument("--cache", default=os.environ.get("CITECHECK_CACHE_DIR", "/tmp/citesure-bench"))
    ap.add_argument("--hf-snapshot-root", default="/home/hermes/.cache/huggingface/hub",
                    help="Root containing models--<org>--<name>/snapshots/<hash>/ dirs. "
                         "citesure passes CITECHECK_CACHE_DIR to from_pretrained as the "
                         "transformers cache_dir, so an eval cache dir SHADOWS the real HF "
                         "cache. Models must therefore be addressed by absolute snapshot path.")
    args = ap.parse_args()

    os.environ["CITECHECK_CACHE_DIR"] = args.cache
    eval_set = Path(args.set)
    items = load_set(eval_set)

    models = [resolve_model(m, args.hf_snapshot_root) for m in args.models]
    print(f"[bench] set={args.set} cases={len(items)} page_cache={args.cache}")
    for m, resolved in zip(args.models, models):
        print(f"[bench]   {m} -> {resolved}")

    results = []
    for m, resolved in zip(args.models, models):
        print(f"\n[bench] === {m} ===", flush=True)
        t0 = time.perf_counter()
        records = []
        for i, item in enumerate(items, 1):
            records.append(await verify_one(item, resolved))
            if i % 25 == 0:
                print(f"  [{i}/{len(items)}]", flush=True)
        elapsed = time.perf_counter() - t0
        s = score(records, items)
        s["model"] = m
        s["elapsed_s"] = round(elapsed, 1)
        s["s_per_case"] = round(elapsed / max(len(records), 1), 3)
        # Persist per-case records: without them a systemic failure (e.g. every
        # case erroring on model load) is indistinguishable from a bad model.
        s["records"] = records
        s["errors"] = sum(1 for r in records if r["predicted"] == "error")
        results.append(s)
        print(f"  -> {s['matched']}/{s['total']} = {s['agreement_pct']}%  "
              f"({elapsed:.1f}s, {s['s_per_case']}s/case)  errors={s['errors']}")
        if s["errors"] == s["total"]:
            print("     WARNING: every case errored — this is a LOAD failure, not a "
                  "model-quality result. Check --model-cache / HF_HOME.")
        for cat, v in s["false_supported"].items():
            print(f"     safety {cat:14s} {v['count']}/{v['total']} = {v['rate']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"set": args.set, "cache": args.cache, "results": results}, indent=2),
        encoding="utf-8",
    )
    print(f"\n[bench] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
