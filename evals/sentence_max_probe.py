"""Per-sentence max NLI scoring — A/B harness for the 108-case set v2.

Renders each premise's sentences as separate (claim, sentence) pairs and takes
the max entailment per original pair, so a trailing sentence that
meta-comments on the claim (e.g. "Changed in version 3.6.") can no longer
collapse an otherwise-identical premise.

Usage:
    .venv/bin/python evals/sentence_max_probe.py --set evals/independent_set_v2.json \
        --cache /tmp/citesure-v2 --out evals/results/sentence_max_ab.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

SENT_RE = re.compile(r"(?<=[.!?])\s+")
MIN_SENTENCE_CHARS = 12


def split_sentences(premise: str) -> list[str]:
    """Split a premise into sentences, dropping fragments too short to score."""
    sents = [s.strip() for s in SENT_RE.split(premise.strip()) if s.strip()]
    kept = [s for s in sents if len(s) >= MIN_SENTENCE_CHARS]
    return kept or sents


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="evals/independent_set_v2.json")
    ap.add_argument("--cache", default="/tmp/citesure-v2")
    ap.add_argument("--hf-root", default="/home/hermes/.cache/huggingface/hub")
    ap.add_argument("--model", default="cross-encoder/nli-deberta-v3-base")
    ap.add_argument("--out", default="evals/results/sentence_max_ab.json")
    args = ap.parse_args()

    os.environ["CITECHECK_CACHE_DIR"] = args.cache

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from bench_models import resolve_model

    from citesure.citations import Citation
    from citesure.nli import get_nli_model, score_nli_batch_all
    from citesure.reachability import verify_citations

    model_path = resolve_model(args.model, args.hf_root)
    items = json.loads(Path(args.set).read_text(encoding="utf-8"))

    rows = []
    t0 = time.perf_counter()
    for i, item in enumerate(items, 1):
        cit = Citation(citation_id=item["id"], url=item["url"], claim=item["claim"])
        report = await verify_citations(
            [cit], use_overlap=True, use_nli=True, nli_model=model_path
        )
        verdict = report.verdicts[0]
        rows.append({
            "id": item["id"],
            "category": item["category"],
            "expected": item["expected_status"],
            "baseline_pred": verdict.status.value,
            "baseline_score": verdict.score,
        })
        if i % 25 == 0:
            print(f"  [{i}/{len(items)}]", flush=True)

    # --- second pass: per-sentence max, re-scoring the captured premises ------
    print("\n[ab] scoring per-sentence max ...", flush=True)
    enc = get_nli_model(model_path)

    for row, item in zip(rows, items):
        cit = Citation(citation_id=item["id"], url=item["url"], claim=item["claim"])
        captured: list[tuple[str, str]] = []

        from citesure.nli import NLICrossEncoder

        real_all = NLICrossEncoder.predict_all

        # Patched on the CLASS, so `self` arrives as the first positional
        # argument. Binding it as _inst keeps the captured pairs correct.
        def spy(_inst, pairs, _real=real_all, _cap=captured):
            _cap.clear()
            _cap.extend(pairs)
            return _real(_inst, pairs)

        NLICrossEncoder.predict_all = spy
        try:
            await verify_citations([cit], use_overlap=True, use_nli=True,
                                   nli_model=model_path)
        finally:
            NLICrossEncoder.predict_all = real_all

        if not captured:
            row["sentmax_pred"] = row["baseline_pred"]
            row["sentmax_score"] = row["baseline_score"]
            continue

        # Expand: one pair per (claim, sentence), grouped by original premise.
        expanded: list[tuple[str, str]] = []
        group_of: list[int] = []
        for gi, (claim, premise) in enumerate(captured):
            for s in split_sentences(premise):
                expanded.append((claim, s))
                group_of.append(gi)
        scored = score_nli_batch_all(enc, expanded)

        best: dict[int, tuple[float, float]] = {}
        for gi, (e, k) in zip(group_of, scored):
            if gi not in best or e > best[gi][0]:
                best[gi] = (e, k)
        per_group = [best[g] for g in sorted(best)]
        row["sentmax_score"] = round(max((e for e, _ in per_group), default=0.0), 4)
        from citesure.nli import nli_band
        row["sentmax_pred"] = nli_band(row["sentmax_score"]).value

    elapsed = time.perf_counter() - t0

    def summarize(key_pred):
        matched = sum(1 for r in rows if r[key_pred] == r["expected"])
        fs = Counter()
        tot = Counter()
        for r in rows:
            if r["expected"] == "unsupported":
                tot[r["category"]] += 1
                if r[key_pred] == "supported":
                    fs[r["category"]] += 1
        return {
            "matched": matched,
            "total": len(rows),
            "agreement_pct": round(100.0 * matched / len(rows), 2),
            "negation_flip_false_supported": fs.get("negation-flip", 0),
            "entity_swap_false_supported": fs.get("entity-swap", 0),
        }

    base = summarize("baseline_pred")
    smax = summarize("sentmax_pred")
    flips = [
        (r["id"], r["category"], r["expected"], r["baseline_pred"], r["sentmax_pred"])
        for r in rows
        if r["baseline_pred"] != r["sentmax_pred"]
    ]

    payload = {
        "set": args.set,
        "model": args.model,
        "elapsed_s": round(elapsed, 1),
        "baseline": base,
        "sentence_max": smax,
        "flips": flips,
        "rows": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n=== A/B on 108 cases ===")
    print(f"baseline      : {base['matched']}/{base['total']} = {base['agreement_pct']}%")
    print(f"sentence-max  : {smax['matched']}/{smax['total']} = {smax['agreement_pct']}%")
    print(f"delta         : {smax['matched'] - base['matched']:+d} cases")
    print(f"safety base   : neg {base['negation_flip_false_supported']}/32  "
          f"ent {base['entity_swap_false_supported']}/32")
    print(f"safety smax   : neg {smax['negation_flip_false_supported']}/32  "
          f"ent {smax['entity_swap_false_supported']}/32")
    print(f"\nflips: {len(flips)}")
    for f in flips:
        print(f"  {f[0]:<9s} {f[1]:<28s} exp={f[2]:<11s} {f[3]:>11s} -> {f[4]}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
