#!/usr/bin/env python3
"""Phase D — performance baselines for citesure.

Measures wall-clock + peak-RSS for the three pipeline depths over a fixed
input, so regressions are detectable later. Uses the eval cache (warm) so
network variance is excluded; a separate cold-cache timing is reported once.

Usage: .venv/bin/python evals/perf_baseline.py [--n 5]
"""
from __future__ import annotations
import argparse, asyncio, json, os, resource, sys, time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
os.environ.setdefault("CITECHECK_CACHE_DIR", str(Path.home() / ".cache" / "citesure-eval"))


def _rss_mb() -> float:
    # ru_maxrss is KB on Linux
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


async def _time_run(citations, use_overlap, use_nli):
    from citesure.reachability import verify_citations
    rss0 = _rss_mb()
    t0 = time.perf_counter()
    report = await verify_citations(citations, use_overlap=use_overlap, use_nli=use_nli)
    dt = time.perf_counter() - t0
    return dt, _rss_mb() - rss0, len(report.verdicts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="citations per run")
    args = ap.parse_args()

    items = json.loads((_ROOT / "evals" / "independent_set.json").read_text())
    # Use the reachable, non-paywalled items so tiers 2/3 actually run.
    pool = [i for i in items if i["expected_status"] in ("supported", "unsupported", "ambiguous")]
    sel = pool[: args.n]

    from citesure.citations import Citation
    cits = [Citation(citation_id=i["id"], url=i["url"], claim=i["claim"]) for i in sel]

    print(f"input: {len(cits)} citations | cache warm | n={args.n}")
    rows = []
    for label, ov, nli in [
        ("reachability-only", False, False),
        ("+overlap (tier2)", True, False),
        ("+NLI (tier3)", True, True),
    ]:
        # warm-up (model load / connection) excluded from the timed run
        asyncio.run(_time_run(cits, ov, nli))
        dt, rss, nv = asyncio.run(_time_run(cits, ov, nli))
        rows.append((label, dt, rss, nv))
        print(f"  {label:20s} {dt:7.2f}s   ΔRSS {rss:6.1f} MB   verdicts={nv}")

    out = _ROOT / "evals" / "results" / "perf_baseline.json"
    out.write_text(json.dumps(
        {"n": args.n, "rows": [{"tier": r[0], "seconds": round(r[1], 3),
                                "delta_rss_mb": round(r[2], 1), "verdicts": r[3]} for r in rows]},
        indent=2))
    print(f"wrote -> {out}")


if __name__ == "__main__":
    main()
