#!/usr/bin/env python3
"""Tier-ablation accuracy runner for citesure.

Measures the library pipeline's accuracy across the four tier configurations
(reachability-only, overlap-only, +NLI, all-tiers) on a labeled eval set and
writes a JSON results file plus a printed summary table.

Usage::

    .venv/bin/python evals/tier_ablation.py \\
        --set evals/independent_set_v2.json \\
        --out evals/results/tier_ablation \\
        [--cases N] [--seed 42]

Configurations
--------------
1. ``reachability_only``  — ``verify_citations(cits, use_overlap=False, use_nli=False)``
2. ``overlap_only``       — ``verify_citations(cits, use_overlap=True,  use_nli=False)``
3. ``nli_only_effect``    — ``verify_citations(cits, use_overlap=True,  use_nli=True)``
4. ``all_tiers``          — same call as 3; included as a consistency check

For each configuration the script reports total / matched / agreement_pct and
a per-``tier_reached`` accuracy breakdown.  If the sibling module
``evals/stats.py`` provides ``wilson_ci`` it is used; otherwise a private
inline Wilson helper is used instead.

Isolation & robustness
----------------------
* Own cache dir (``~/.cache/citesure-eval-ablation`` or ``CITECHECK_CACHE_DIR``).
* Per-case timeout (default 30 s) so a hung fetch cannot stall the run.
* Per-case exception isolation: ``predicted="error:<msg>"`` and continue.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- project import path (library, not installed) ---------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

# --- own cache dir (set BEFORE any fetch happens) --------------------------
DEFAULT_EVAL_CACHE = Path.home() / ".cache" / "citesure-eval-ablation"


def _configure_cache_dir() -> str:
    """Point citesure's disk cache at the ablation-specific dir."""
    cache_dir = os.environ.get("CITECHECK_CACHE_DIR") or str(DEFAULT_EVAL_CACHE)
    os.environ["CITECHECK_CACHE_DIR"] = cache_dir
    return cache_dir


# --- canonical category mapping (copied from run_eval.py) -------------------
CATEGORY_CANONICAL: dict[str, str] = {
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

STATUSES = ["supported", "unsupported", "unreachable", "paywalled", "ambiguous", "error"]

# --- the four ablation configurations ---------------------------------------
CONFIGS: list[dict[str, Any]] = [
    {"name": "reachability_only", "use_overlap": False, "use_nli": False},
    {"name": "overlap_only", "use_overlap": True, "use_nli": False},
    {"name": "nli_only_effect", "use_overlap": True, "use_nli": True},
    {"name": "all_tiers", "use_overlap": True, "use_nli": True},
]


# ---------------------------------------------------------------------------
# Wilson score interval (inline fallback; sibling stats.py may override)
# ---------------------------------------------------------------------------

def _wilson_ci(matched: int, total: int, z: float = 1.96) -> dict[str, float]:
    """Wilson score interval for a binomial proportion.

    Returns ``{"low": ..., "high": ...}`` as percentages (0-100).
    """
    if total == 0:
        return {"low": 0.0, "high": 0.0}
    p = matched / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return {
        "low": round(max(0.0, centre - margin) * 100, 2),
        "high": round(min(1.0, centre + margin) * 100, 2),
    }


def _load_wilson_ci() -> Any:
    """Try to import wilson_ci from the sibling evals/stats.py; fall back to inline."""
    stats_path = Path(__file__).resolve().parent / "stats.py"
    if stats_path.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("evals_stats", stats_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            fn = getattr(mod, "wilson_ci", None)
            if callable(fn):
                return fn
    return _wilson_ci


# ---------------------------------------------------------------------------
# Per-case verification (never raises)
# ---------------------------------------------------------------------------

async def _verify_one(
    item: dict,
    use_overlap: bool,
    use_nli: bool,
    timeout: float = 30.0,
) -> dict:
    """Run the library pipeline for a single item; never raise.

    A per-case timeout prevents a hung fetch from stalling the whole run.
    """
    from citesure.citations import Citation
    from citesure.reachability import verify_citations

    citation = Citation(citation_id=item["id"], url=item["url"], claim=item["claim"])
    try:
        report = await asyncio.wait_for(
            verify_citations([citation], use_overlap=use_overlap, use_nli=use_nli),
            timeout=timeout,
        )
        v = report.verdicts[0]
        predicted = v.status.value if v.status else "error:None"
        return {
            "id": item["id"],
            "category": item["category"],
            "expected": item["expected_status"],
            "predicted": predicted,
            "score": v.score,
            "tier_reached": v.tier_reached,
            "match": predicted == item["expected_status"],
            "evidence": v.evidence or "",
            "notes": list(v.notes),
        }
    except asyncio.TimeoutError:
        return {
            "id": item["id"],
            "category": item["category"],
            "expected": item["expected_status"],
            "predicted": "error:TimeoutError: per-case timeout",
            "score": None,
            "tier_reached": 0,
            "match": False,
            "evidence": "",
            "notes": ["per-case timeout exceeded"],
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


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _per_tier_breakdown(records: list[dict]) -> dict[str, dict[str, Any]]:
    """Accuracy conditioned on ``tier_reached``.

    Returns ``{"1": {"total": n, "matched": m, "accuracy_pct": p}, ...}``.
    """
    buckets: dict[int, list[dict]] = defaultdict(list)
    for rec in records:
        tier = rec.get("tier_reached", 0)
        buckets[tier].append(rec)
    out: dict[str, dict[str, Any]] = {}
    for tier in sorted(buckets):
        items = buckets[tier]
        total = len(items)
        matched = sum(1 for r in items if r["match"])
        out[str(tier)] = {
            "total": total,
            "matched": matched,
            "accuracy_pct": round(matched / total * 100, 2) if total else 0.0,
        }
    return out


def _per_labeler_confidence(records: list[dict], items_by_id: dict[str, dict]) -> dict[str, dict[str, Any]]:
    """Accuracy conditioned on ``labeler_confidence`` (high/medium/low)."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        conf = items_by_id.get(rec["id"], {}).get("labeler_confidence", "unknown")
        buckets[conf].append(rec)
    out: dict[str, dict[str, Any]] = {}
    for conf in sorted(buckets):
        items = buckets[conf]
        total = len(items)
        matched = sum(1 for r in items if r["match"])
        out[conf] = {
            "total": total,
            "matched": matched,
            "accuracy_pct": round(matched / total * 100, 2) if total else 0.0,
        }
    return out


def _normalize_ci(raw: Any) -> dict[str, float]:
    """Normalize a wilson_ci result to ``{"low": float, "high": float}`` (percentages).

    The sibling ``evals/stats.py`` returns ``(lo, hi)`` as proportions (0-1);
    the inline fallback returns ``{"low": ..., "high": ...}`` as percentages.
    This function accepts either shape and always returns percentages.
    """
    if isinstance(raw, dict):
        return {"low": float(raw["low"]), "high": float(raw["high"])}
    # Assume tuple/list of two proportions
    lo, hi = raw[0], raw[1]
    return {"low": round(float(lo) * 100, 2), "high": round(float(hi) * 100, 2)}


def _summarize_config(
    name: str,
    records: list[dict],
    wilson_fn: Any,
    items_by_id: dict[str, dict],
) -> dict[str, Any]:
    """Build the per-config result block."""
    total = len(records)
    matched = sum(1 for r in records if r["match"])
    agreement = round(matched / total * 100, 2) if total else 0.0
    ci_raw = wilson_fn(matched, total)
    ci = _normalize_ci(ci_raw)
    return {
        "name": name,
        "total": total,
        "matched": matched,
        "agreement_pct": agreement,
        "wilson_ci": ci,
        "per_tier": _per_tier_breakdown(records),
        "per_labeler_confidence": _per_labeler_confidence(records, items_by_id),
        "records": records,
    }


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

def _print_table(configs: list[dict[str, Any]]) -> None:
    """Print a readable summary table."""
    print("\n" + "=" * 78)
    print("TIER ABLATION RESULTS")
    print("=" * 78)
    header = f"{'config':22s} {'matched':>8s} {'total':>6s} {'agree%':>8s} {'wilson 95% CI':>18s}"
    print(header)
    print("-" * len(header))
    for cfg in configs:
        ci = cfg["wilson_ci"]
        ci_str = f"[{ci['low']:.1f}, {ci['high']:.1f}]"
        print(
            f"{cfg['name']:22s} {cfg['matched']:>8d} {cfg['total']:>6d} "
            f"{cfg['agreement_pct']:>7.2f}% {ci_str:>18s}"
        )

    # Per-tier breakdown
    print("\n--- Per-tier accuracy breakdown ---")
    tier_header = f"{'config':22s} {'tier':>6s} {'matched':>8s} {'total':>6s} {'acc%':>8s}"
    print(tier_header)
    print("-" * len(tier_header))
    for cfg in configs:
        for tier, d in sorted(cfg["per_tier"].items(), key=lambda x: int(x[0])):
            print(
                f"{cfg['name']:22s} {tier:>6s} {d['matched']:>8d} "
                f"{d['total']:>6d} {d['accuracy_pct']:>7.2f}%"
            )

    # Per-labeler-confidence breakdown
    print("\n--- Per-labeler-confidence accuracy ---")
    conf_header = f"{'config':22s} {'confidence':>12s} {'matched':>8s} {'total':>6s} {'acc%':>8s}"
    print(conf_header)
    print("-" * len(conf_header))
    for cfg in configs:
        for conf, d in sorted(cfg["per_labeler_confidence"].items()):
            print(
                f"{cfg['name']:22s} {conf:>12s} {d['matched']:>8d} "
                f"{d['total']:>6d} {d['accuracy_pct']:>7.2f}%"
            )

    # Consistency check: config 3 vs config 4
    if len(configs) >= 4:
        c3, c4 = configs[2], configs[3]
        if c3["matched"] != c4["matched"] or c3["total"] != c4["total"]:
            print("\n*** WARNING: nli_only_effect and all_tiers disagree! ***")
            print(f"  nli_only_effect: {c3['matched']}/{c3['total']}")
            print(f"  all_tiers:       {c4['matched']}/{c4['total']}")
        else:
            print("\nConsistency check: nli_only_effect == all_tiers ✓")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="citesure tier-ablation accuracy eval",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--set",
        default=str(Path(__file__).resolve().parent / "independent_set_v2.json"),
        help="path to eval set JSON (default: evals/independent_set_v2.json)",
    )
    ap.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent / "results" / "tier_ablation"),
        help="output directory for results JSON",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=42,
        help="random seed for bootstrap (reserved for future use)",
    )
    ap.add_argument(
        "--cases",
        type=int,
        default=None,
        help="limit to first N cases (for smoke runs)",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="per-case timeout in seconds (default: 30)",
    )
    args = ap.parse_args(argv)

    cache_dir = _configure_cache_dir()
    wilson_fn = _load_wilson_ci()

    set_path = Path(args.set)
    items: list[dict] = json.loads(set_path.read_text(encoding="utf-8"))
    if args.cases is not None:
        items = items[: args.cases]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"eval set       : {args.set} ({len(items)} items)")
    print(f"cache dir      : {cache_dir}")
    print(f"seed           : {args.seed}")
    print(f"per-case timeout: {args.timeout}s")
    print(f"wilson_ci      : {'evals/stats.py' if wilson_fn is not _wilson_ci else 'inline fallback'}")

    items_by_id = {item["id"]: item for item in items}
    all_configs: list[dict[str, Any]] = []

    for cfg in CONFIGS:
        name = cfg["name"]
        use_overlap = cfg["use_overlap"]
        use_nli = cfg["use_nli"]
        print(f"\n--- Running config: {name} (overlap={use_overlap}, nli={use_nli}) ---")

        records: list[dict] = []
        for i, item in enumerate(items, 1):
            rec = asyncio.run(
                _verify_one(item, use_overlap, use_nli, timeout=args.timeout)
            )
            records.append(rec)
            mark = "OK " if rec["match"] else "MISS"
            print(
                f"[{i:2d}/{len(items)}] {mark} {rec['id']:8s} "
                f"exp={rec['expected']:11s} pred={rec['predicted']:11s} "
                f"tier={rec['tier_reached']} score={rec['score']}"
            )

        summary = _summarize_config(name, records, wilson_fn, items_by_id)
        all_configs.append(summary)

    _print_table(all_configs)

    # ---- persist ------------------------------------------------------------
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    result_path = out_dir / f"{ts}.json"
    payload = {
        "generated_utc": ts,
        "set": str(args.set),
        "seed": args.seed,
        "cache_dir": cache_dir,
        "wilson_ci_source": "evals/stats.py" if wilson_fn is not _wilson_ci else "inline",
        "configs": all_configs,
    }
    result_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nwrote results -> {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
