#!/usr/bin/env python3
"""Independent accuracy evaluation runner for citesure (Phase A).

Runs the LIBRARY pipeline (``citesure.reachability.verify_citations``) over a
hand-labeled real-world claim/URL set and compares predicted vs expected
status. Tiers 1+2 run by default (NLI OFF); pass ``--nli`` to also run tier 3.

Usage::

    .venv/bin/python evals/run_eval.py --set evals/independent_set.json \
        [--nli] [--out DIR]

Outputs
-------
* ``<out>/<timestamp>.json`` — per-item records
  ``{id, category, expected, predicted, score, tier_reached, match, evidence}``.
* Printed: 6x6 confusion matrix (statuses incl. an ``error`` column),
  per-category precision/recall, overall agreement %, and TWO safety metrics
  (false-supported rate on negation-flip and on entity-swap items).

Isolation & robustness
----------------------
* Uses its own cache dir (default ``~/.cache/citesure-eval``, overridable via
  ``CITECHECK_CACHE_DIR``) so the app cache is untouched. A second run is fully
  cache-backed (offline-capable).
* Never crashes on a single item failure: the item is recorded with
  ``predicted="error:<msg>"`` and the run continues.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# --- project import path (library, not installed) ---------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
# evals/ is a script directory, not a package (no __init__.py), so the sibling
# stats module is loaded by path rather than imported as evals.stats.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# --- own cache dir (set BEFORE any fetch happens) --------------------------
DEFAULT_EVAL_CACHE = Path.home() / ".cache" / "citesure-eval"


def _configure_cache_dir() -> str:
    """Point citesure's disk cache at the eval-specific dir."""
    cache_dir = os.environ.get("CITECHECK_CACHE_DIR") or str(DEFAULT_EVAL_CACHE)
    os.environ["CITECHECK_CACHE_DIR"] = cache_dir
    return cache_dir


# Statuses we track in the confusion matrix (5 real + 1 error bucket).
STATUSES = ["supported", "unsupported", "unreachable", "paywalled", "ambiguous", "error"]

# Canonical expected status per category (for per-category P/R).
CATEGORY_CANONICAL = {
    "verbatim-supported": "supported",
    "paraphrase-supported": "supported",
    "numeric-detail-supported": "supported",
    "false-claim": "unsupported",
    "negation-flip": "unsupported",
    "entity-swap": "unsupported",
    "partially-true-ambiguous": "ambiguous",
    "dead-link": "unreachable",
    "paywalled": "paywalled",
}


def _norm_status(value) -> str:
    """Normalize a verdict status to one of STATUSES (error -> 'error')."""
    if value is None:
        return "error"
    s = str(value)
    if s.startswith("error"):
        return "error"
    return s if s in STATUSES else "error"


# ---------------------------------------------------------------------------
# Uncertainty quantification (added 2026-10-04)
# ---------------------------------------------------------------------------


def _load_stats():
    """Load the sibling stats module, or return None if it is unavailable."""
    try:
        import stats as _stats  # evals/ is on sys.path
    except Exception:
        return None
    return _stats


def _fmt_pct_ci(ci) -> str:
    """Format a (lo, hi) proportion pair as a percentage interval."""
    return f"[{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def _uncertainty_block(records: list[dict], items: list[dict]) -> dict:
    """Compute and print every interval the report should carry.

    Returns a JSON-serialisable dict so the numbers persist in the results
    file rather than only appearing on stdout -- a CI nobody can find later
    is not evidence.

    Prints:
      * Wilson 95% CI on the headline agreement (iid assumption).
      * Cluster-bootstrap 95% CI resampling whole URLs (correct assumption),
        which is the honest interval for this set.
      * McNemar on the NLI-on-vs-off claim when a baseline is supplied.
      * Agreement split by labeler_confidence (46/108 v2 cases are 'medium'
        and the field was previously collected but never analysed).
    """
    st = _load_stats()
    total = len(records)
    matched = sum(1 for r in records if r.get("match"))
    out: dict[str, Any] = {}

    print("\n=== UNCERTAINTY ===")
    if st is None:
        print("  (evals/stats.py unavailable -- intervals skipped)")
        return out

    matches = [bool(r.get("match")) for r in records]
    wilson = st.wilson_ci(matched, total)
    out["wilson_ci"] = {"low": round(wilson[0], 4), "high": round(wilson[1], 4),
                        "assumption": "iid cases"}
    print(f"  headline {matched}/{total}  Wilson 95% CI {_fmt_pct_ci(wilson)}"
          "   (assumes independent cases)")

    # Cluster on the source URL: cases from one URL share a source sentence.
    url_by_id = {it["id"]: it["url"] for it in items}
    clusters = [url_by_id.get(r["id"], r["id"]) for r in records]
    n_clusters = len(set(clusters))
    if n_clusters < total:
        boot = st.cluster_bootstrap_ci(matches, clusters)
        out["cluster_bootstrap_ci"] = {
            "low": round(boot[0], 4), "high": round(boot[1], 4),
            "n_clusters": n_clusters, "assumption": "cases clustered by source URL",
        }
        print(f"  {n_clusters} unique URLs across {total} cases -> cases are "
              "CORRELATED")
        print(f"  cluster-bootstrap 95% CI {_fmt_pct_ci(boot)}   "
              "(resamples whole URLs -- the honest interval)")
    else:
        print("  every case has a distinct URL; iid interval is adequate")

    # Safety subsets are small; a bare percentage overstates precision.
    for label, key in (("negation-flip", "negation-flip"),
                       ("entity-swap", "entity-swap")):
        sub = [(bool(r.get("match")), url_by_id.get(r["id"], r["id"]))
               for r in records if r.get("category") == key]
        if not sub:
            continue
        k = sum(1 for m, _ in sub if m)
        n = len(sub)
        ci = st.wilson_ci(k, n)
        out[f"{label}_wilson_ci"] = {"low": round(ci[0], 4), "high": round(ci[1], 4),
                                     "correct": k, "total": n}
        print(f"  {label:14s} {k}/{n} = {100 * k / n:.1f}%  "
              f"95% CI {_fmt_pct_ci(ci)}")

    # labeler_confidence was collected but never analysed until now.
    conf = st.labeler_confidence_breakdown(records, items)
    if conf:
        out["by_labeler_confidence"] = conf
        print("  by labeler_confidence:")
        for label, blk in sorted(conf.items()):
            print(f"    {label:7s} {blk['correct']}/{blk['total']} = "
                  f"{100 * blk['agreement']:.1f}%")
    return out


async def _verify_one(item: dict, use_nli: bool) -> dict:
    """Run the library pipeline for a single item; never raise."""
    from citesure.citations import Citation
    from citesure.reachability import verify_citations

    citation = Citation(citation_id=item["id"], url=item["url"], claim=item["claim"])
    try:
        report = await verify_citations(
            [citation], use_overlap=True, use_nli=use_nli
        )
        v = report.verdicts[0]
        return {
            "id": item["id"],
            "category": item["category"],
            "expected": item["expected_status"],
            "predicted": _norm_status(v.status.value),
            "score": v.score,
            "tier_reached": v.tier_reached,
            "match": _norm_status(v.status.value) == item["expected_status"],
            "evidence": v.evidence or "",
            "notes": list(v.notes),
        }
    except Exception as exc:  # noqa: BLE001 - isolate per-item failures
        return {
            "id": item["id"],
            "category": item["category"],
            "expected": item["expected_status"],
            "predicted": f"error:{type(exc).__name__}: {exc}",
            "score": None,
            "tier_reached": 0,
            "match": False,
            "evidence": "",
            "notes": [f"runner exception: {type(exc).__name__}: {exc}"],
        }


def _confusion_matrix(records: list[dict]) -> dict:
    """expected(row) x predicted(col) counts over STATUSES."""
    mat = {r: {c: 0 for c in STATUSES} for r in STATUSES}
    for rec in records:
        r = _norm_status(rec["expected"])
        c = _norm_status(rec["predicted"])
        if r in mat and c in mat[r]:
            mat[r][c] += 1
    return mat


def _print_confusion(mat: dict) -> None:
    col_w = max(len(s) for s in STATUSES) + 2
    header = "expected \\ pred |" + "".join(f"{s:>{col_w}}" for s in STATUSES)
    print("\n=== Confusion matrix (rows=expected, cols=predicted) ===")
    print(header)
    print("-" * len(header))
    for r in STATUSES:
        row = f"{r:>{col_w}}|" + "".join(f"{mat[r][c]:>{col_w}}" for c in STATUSES)
        print(row)


def _per_category_recall(records: list[dict]) -> dict:
    """Per-category recall: fraction of items in category c that received the
    category's canonical status.

    Recall is the well-defined per-category metric here because several
    categories share one canonical status (three -> 'supported', three ->
    'unsupported'), so per-category *precision* is not meaningful (a correct
    'supported' on a paraphrase item is not a false positive for the
    verbatim category). Status-level precision/recall is reported separately
    from the confusion matrix.
    """
    out = {}
    for cat in sorted(CATEGORY_CANONICAL):
        canon = CATEGORY_CANONICAL[cat]
        items = [r for r in records if r["category"] == cat]
        hit = sum(1 for r in items if _norm_status(r["predicted"]) == canon)
        total = len(items)
        out[cat] = {
            "canonical_status": canon,
            "total": total,
            "correct": hit,
            "recall": (hit / total) if total else float("nan"),
        }
    return out


def _status_pr(mat: dict) -> dict:
    """Multi-class precision/recall per STATUS from the confusion matrix."""
    out = {}
    for s in STATUSES:
        tp = mat[s][s]
        fp = sum(mat[r][s] for r in STATUSES if r != s)   # predicted s, actually other
        fn = sum(mat[s][c] for c in STATUSES if c != s)   # actually s, predicted other
        prec = tp / (tp + fp) if (tp + fp) else float("nan")
        rec_ = tp / (tp + fn) if (tp + fn) else float("nan")
        out[s] = {"tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec_}
    return out


def _print_per_category(pr: dict) -> None:
    print("\n=== Per-category recall (items in category getting its canonical status) ===")
    print(f"{'category':28s} {'status':12s} {'recall':>7s}  (correct/total)")
    for cat, d in pr.items():
        r = f"{d['recall']:.3f}" if d["recall"] == d["recall"] else "  n/a"
        print(f"{cat:28s} {d['canonical_status']:12s} {r:>7s}  ({d['correct']}/{d['total']})")


def _print_status_pr(sp: dict) -> None:
    print("\n=== Per-status precision / recall (multi-class, from confusion matrix) ===")
    print(f"{'status':12s} {'P':>7s} {'R':>7s}  (tp/fp/fn)")
    for s in STATUSES:
        d = sp[s]
        p = f"{d['precision']:.3f}" if d["precision"] == d["precision"] else "  n/a"
        r = f"{d['recall']:.3f}" if d["recall"] == d["recall"] else "  n/a"
        print(f"{s:12s} {p:>7s} {r:>7s}  ({d['tp']}/{d['fp']}/{d['fn']})")


def _safety_metric(records: list[dict], category: str) -> tuple[int, int, float]:
    """False-supported rate: predicted=supported where expected=unsupported."""
    items = [r for r in records if r["category"] == category]
    total = len(items)
    false_supported = sum(
        1 for r in items
        if _norm_status(r["predicted"]) == "supported"
        and r["expected"] == "unsupported"
    )
    rate = false_supported / total if total else float("nan")
    return false_supported, total, rate


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="citesure independent accuracy eval")
    ap.add_argument("--set", required=True, help="path to independent_set.json")
    ap.add_argument("--nli", action="store_true", help="enable NLI tier (Phase B)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"),
                    help="output directory for results JSON")
    ap.add_argument("--compare", default=None,
                    help="baseline results file or dir to diff against "
                         "(e.g. evals/results/v1_replay)")
    args = ap.parse_args(argv)

    cache_dir = _configure_cache_dir()
    items = json.loads(Path(args.set).read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"eval set       : {args.set} ({len(items)} items)")
    print(f"cache dir      : {cache_dir}")
    print(f"NLI tier       : {'ON' if args.nli else 'OFF'} (tiers 1+2{' +3' if args.nli else ''})")

    # Run each item through the library pipeline, isolated from the others.
    records = []
    for i, item in enumerate(items, 1):
        rec = asyncio.run(_verify_one(item, use_nli=args.nli))
        records.append(rec)
        mark = "OK " if rec["match"] else "MISS"
        print(f"[{i:2d}/{len(items)}] {mark} {rec['id']:8s} "
              f"exp={rec['expected']:11s} pred={rec['predicted']:11s} "
              f"tier={rec['tier_reached']} score={rec['score']}")

    # ---- metrics -----------------------------------------------------------
    total = len(records)
    matched = sum(1 for r in records if r["match"])
    agreement = matched / total * 100 if total else 0.0

    mat = _confusion_matrix(records)
    pr = _per_category_recall(records)
    sp = _status_pr(mat)
    nf_fs, nf_tot, nf_rate = _safety_metric(records, "negation-flip")
    es_fs, es_tot, es_rate = _safety_metric(records, "entity-swap")

    _print_confusion(mat)
    _print_per_category(pr)
    _print_status_pr(sp)

    print("\n=== Overall ===")
    print(f"agreement: {matched}/{total} = {agreement:.1f}%")

    # ---- uncertainty (added 2026-10-04) -----------------------------------
    # A bare point estimate on n=108 is reported as if it were exact. It is
    # not, and the eval set is CORRELATED: 32 of 40 unique URLs contribute
    # three cases each (verbatim / negation-flip / entity-swap of ONE source
    # sentence), so an iid interval is too narrow. Quote both.
    unc = _uncertainty_block(records, items)

    print("\n=== SAFETY METRICS (false-supported where expected=unsupported) ===")
    print(f"negation-flip : {nf_fs}/{nf_tot} = {nf_rate:.1%}")
    print(f"entity-swap   : {es_fs}/{es_tot} = {es_rate:.1%}")

    mismatches = [r for r in records if not r["match"]]
    print(f"\nmismatches: {len(mismatches)}")
    for r in mismatches:
        print(f"  {r['id']} [{r['category']}] exp={r['expected']} pred={r['predicted']} "
              f"score={r['score']} :: {r['evidence'][:90]}")

    # ---- persist ------------------------------------------------------------
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    result_path = out_dir / f"{ts}.json"
    payload = {
        "generated_utc": ts,
        "set": str(args.set),
        "nli": bool(args.nli),
        "cache_dir": cache_dir,
        "total": total,
        "matched": matched,
        "agreement_pct": round(agreement, 2),
        "confusion_matrix": mat,
        "per_category_recall": pr,
        "per_status_pr": sp,
        "uncertainty": unc,
        "safety": {
            "negation_flip_false_supported": nf_fs,
            "negation_flip_total": nf_tot,
            "negation_flip_rate": round(nf_rate, 4),
            "entity_swap_false_supported": es_fs,
            "entity_swap_total": es_tot,
            "entity_swap_rate": round(es_rate, 4),
        },
        "records": records,
    }
    def _write_results() -> None:
        """Persist the payload. Called after --compare so any McNemar result
        computed there is included (added 2026-10-04: it was previously
        written first, so the significance test never reached the file)."""
        result_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nwrote results -> {result_path}")

    _write_results()

    # ---- optional no-regression diff --------------------------------------
    if args.compare:
        base_path = Path(args.compare)
        if base_path.is_dir():
            files = sorted(base_path.glob("*.json"))
            baseline = json.loads(files[-1].read_text(encoding="utf-8")) if files else None
        elif base_path.exists():
            baseline = json.loads(base_path.read_text(encoding="utf-8"))
        else:
            baseline = None
        if baseline is None:
            print(f"\n[compare] no baseline found at {args.compare}")
        else:
            b_tot, n_tot = baseline.get("total", 0), total
            b_matched, n_matched = baseline.get("matched", 0), matched
            b_pct = baseline.get("agreement_pct", 0.0)
            print("\n=== COMPARISON vs baseline ===")
            print(f"baseline : {b_matched}/{b_tot} = {b_pct:.1f}%  ({baseline.get('set', '?')})")
            print(f"current  : {n_matched}/{n_tot} = {agreement:.1f}%  ({args.set})")
            if b_tot == n_tot:
                print(f"delta    : {n_matched - b_matched:+d} cases")
            else:
                print(f"delta    : {n_matched - b_matched:+d} cases "
                      f"(NOTE: set size changed {b_tot} -> {n_tot}; "
                      f"only within-set runs are comparable)")
            # Paired significance test for the headline NLI-on-vs-off gap.
            # The project already runs McNemar for model-vs-model in
            # RESULTS_MODEL_BENCH.md but never for this toggle, which is the
            # claim the README calls "measured rather than asserted".
            base_by_id = {}
            for br in baseline.get("records", []) or []:
                if "id" in br and "match" in br:
                    base_by_id[br["id"]] = bool(br["match"])
            cur_by_id = {r["id"]: bool(r["match"]) for r in records}
            shared = [i for i in cur_by_id if i in base_by_id]
            if shared and b_tot == n_tot:
                st = _load_stats()
                if st is not None:
                    nli_on_wins = sum(1 for i in shared
                                      if cur_by_id[i] and not base_by_id[i])
                    nli_off_wins = sum(1 for i in shared
                                       if base_by_id[i] and not cur_by_id[i])
                    stat, pval = st.mcnemar_exact(nli_on_wins, nli_off_wins)
                    verdict = ("SIGNIFICANT" if pval < 0.05
                               else "NOT significant")
                    print(f"McNemar (exact, paired, n={len(shared)} shared): "
                          f"current-only-correct={nli_on_wins} "
                          f"baseline-only-correct={nli_off_wins} "
                          f"p={pval:.5f}  -> {verdict}")
                    payload["mcnemar_vs_baseline"] = {
                        "shared_cases": len(shared),
                        "current_only_correct": nli_on_wins,
                        "baseline_only_correct": nli_off_wins,
                        "p_value": round(pval, 6),
                        "significant_at_0.05": pval < 0.05,
                        "note": "current = this run; baseline = --compare target",
                    }

            for key in ("negation_flip_false_supported", "entity_swap_false_supported"):
                b = baseline.get("safety", {}).get(key)
                n = payload["safety"].get(key)
                if b is not None and n is not None:
                    flag = "" if n <= b else "   <-- SAFETY REGRESSION"
                    print(f"{key:34s} {b} -> {n}{flag}")

    # Re-write so the McNemar block (added above, after the first write) is
    # persisted too. Cheap and idempotent.
    _write_results()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
