#!/usr/bin/env python3
"""Per-page three-way contrast: does the pipeline assert A but miss B and C?

For each page, classifies the pipeline's behaviour on the (A supported,
B negation-flip, C entity-swap) trio into one of:
  clean        - A correct, B correct, C correct   (polarity + binding both fine)
  polarity-bug - A correct, a negation-flip false-supported
  binding-bug  - A correct, an entity-swap false-supported
  miss         - A itself wrong                   (selection/extraction failure)

Usage:
    .venv/bin/python evals/contrast_report.py evals/results/v2_baseline
"""
from __future__ import annotations

import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load(run_dir: str) -> dict[str, dict]:
    files = sorted(glob.glob(f"{run_dir}/*.json"))
    if not files:
        sys.exit(f"no results in {run_dir}")
    data = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    return {r["id"]: r for r in data["records"]}


def main() -> int:
    recs = load(sys.argv[1] if len(sys.argv) > 1 else "evals/results/v2_baseline")
    cases = json.loads(Path("evals/independent_set_v2.json").read_text(encoding="utf-8"))

    by_url: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        by_url[c["url"]].append(c)

    verdicts: Counter[str] = Counter()
    detail: list[tuple[str, str, str, str]] = []

    for url, group in by_url.items():
        pos = [c for c in group if c["expected_status"] == "supported"]
        neg = [c for c in group if c["expected_status"] == "unsupported"]
        if not pos or len(neg) < 2:
            continue
        a = recs.get(pos[0]["id"])
        if not a:
            continue
        a_ok = a["predicted"] == "supported"

        bad_polarity, bad_binding = [], []
        for c in neg:
            r = recs.get(c["id"])
            if r and r["predicted"] == "supported":
                (bad_polarity if c["category"] == "negation-flip" else bad_binding).append(r["id"])

        if not a_ok:
            verdict = "miss"
        elif bad_polarity and bad_binding:
            verdict = "polarity-bug+binding-bug"
        elif bad_polarity:
            verdict = "polarity-bug"
        elif bad_binding:
            verdict = "binding-bug"
        else:
            verdict = "clean"
        verdicts[verdict] += 1
        detail.append((verdict, url, pos[0]["id"], ",".join(bad_polarity + bad_binding)))

    print(f"{'verdict':26s} pages")
    for k, v in verdicts.most_common():
        print(f"  {k:24s} {v}")
    print(f"  {'TOTAL':24s} {sum(verdicts.values())}")

    print("\n=== pages with false-supported negatives ===")
    for verdict, url, aid, ids in sorted(detail):
        if verdict not in ("clean", "miss"):
            print(f"  [{verdict}] {url[:58]}")
            print(f"      A={aid} false-supported={ids or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
