#!/usr/bin/env python3
"""URL-clustered deterministic tune/held-out split + trivial baselines.

This module provides:
  * A deterministic splitter that partitions an eval set into a TUNING half
    and a HELD-OUT half, splitting by SOURCE URL (not by case) to avoid
    near-duplicate leakage.
  * Trivial baselines so the headline accuracy has a reference floor.

Usage::

    .venv/bin/python evals/split.py --set evals/independent_set_v2.json --seed 42

Outputs
-------
* ``evals/splits/<setname>_tune_heldout.json`` — frozen partition
* Printed summary + baseline table

Pure stdlib + scipy/numpy. Imports evals/stats.py for Wilson CI.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# evals/ is a script directory (no __init__.py); add it to sys.path for stats.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stats import wilson_ci  # noqa: E402

DEFAULT_SEED = 42


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_cases(path: str | Path) -> list[dict[str, Any]]:
    """Load eval set cases from a JSON file.

    Accepts either a bare list of cases or a dict with a ``"cases"`` key.
    """
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        cases = data.get("cases", [])
    else:
        cases = data
    if not isinstance(cases, list):
        raise ValueError(f"Expected a list of cases in {path}, got {type(cases).__name__}")
    return cases


# ---------------------------------------------------------------------------
# URL-clustered split
# ---------------------------------------------------------------------------

def group_by_url(cases: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group cases by their ``url`` field."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        groups[case["url"]].append(case)
    return dict(groups)


def _imbalance(
    tune_status: Counter,
    heldout_status: Counter,
    tune_n: int,
    heldout_n: int,
    all_statuses: list[str],
) -> float:
    """Compute imbalance between two sides (lower is better).

    Combines case-count imbalance (normalized) with status-distribution
    imbalance (L1 distance between proportions).
    """
    total = tune_n + heldout_n
    if total == 0:
        return 0.0
    count_imb = abs(tune_n - heldout_n) / total
    status_imb = 0.0
    for status in all_statuses:
        tune_prop = tune_status[status] / tune_n if tune_n > 0 else 0.0
        heldout_prop = heldout_status[status] / heldout_n if heldout_n > 0 else 0.0
        status_imb += abs(tune_prop - heldout_prop)
    return count_imb + status_imb


def split_by_url(
    cases: list[dict[str, Any]],
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Split cases into tune/held-out by URL cluster.

    Deterministic: same input + same seed → same partition.

    Groups cases by URL, then greedily assigns each URL group to the side
    that minimizes a cost function combining case-count imbalance and
    status-distribution imbalance. Ties are broken with a seeded RNG.
    """
    groups = group_by_url(cases)
    all_statuses = sorted(set(c["expected_status"] for c in cases))

    # Sort URLs for deterministic ordering
    urls = sorted(groups.keys())

    # Seeded shuffle for tie-breaking
    rng = random.Random(seed)
    rng.shuffle(urls)

    # Greedy assignment
    tune_cases: list[dict[str, Any]] = []
    heldout_cases: list[dict[str, Any]] = []
    tune_status: Counter = Counter()
    heldout_status: Counter = Counter()

    for url in urls:
        group_cases = groups[url]
        group_status: Counter = Counter(c["expected_status"] for c in group_cases)
        group_n = len(group_cases)

        # Cost of assigning to each side
        tune_cost = _imbalance(
            tune_status + group_status,
            heldout_status,
            len(tune_cases) + group_n,
            len(heldout_cases),
            all_statuses,
        )
        heldout_cost = _imbalance(
            tune_status,
            heldout_status + group_status,
            len(tune_cases),
            len(heldout_cases) + group_n,
            all_statuses,
        )

        if tune_cost < heldout_cost:
            tune_cases.extend(group_cases)
            tune_status.update(group_status)
        elif heldout_cost < tune_cost:
            heldout_cases.extend(group_cases)
            heldout_status.update(group_status)
        else:
            # Tie-break with seeded RNG
            if rng.random() < 0.5:
                tune_cases.extend(group_cases)
                tune_status.update(group_status)
            else:
                heldout_cases.extend(group_cases)
                heldout_status.update(group_status)

    return {
        "tune": tune_cases,
        "heldout": heldout_cases,
        "tune_status": dict(tune_status),
        "heldout_status": dict(heldout_status),
    }


def build_partition(
    cases: list[dict[str, Any]],
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Build a partition dict with metadata for JSON serialisation."""
    result = split_by_url(cases, seed)

    tune_urls = sorted(set(c["url"] for c in result["tune"]))
    heldout_urls = sorted(set(c["url"] for c in result["heldout"]))

    return {
        "seed": seed,
        "tune": {
            "case_ids": [c["id"] for c in result["tune"]],
            "urls": tune_urls,
            "n_cases": len(result["tune"]),
            "status_distribution": result["tune_status"],
        },
        "heldout": {
            "case_ids": [c["id"] for c in result["heldout"]],
            "urls": heldout_urls,
            "n_cases": len(result["heldout"]),
            "status_distribution": result["heldout_status"],
        },
    }


def write_partition(
    partition: dict[str, Any],
    set_path: str | Path,
    seed: int,
) -> Path:
    """Write partition to ``evals/splits/<setname>_tune_heldout.json``."""
    setname = Path(set_path).stem
    out_dir = Path(__file__).resolve().parent / "splits"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{setname}_tune_heldout.json"

    with open(out_path, "w") as f:
        json.dump(partition, f, indent=2, sort_keys=True)
        f.write("\n")

    return out_path


def print_summary(partition: dict[str, Any]) -> None:
    """Print a readable summary of the partition."""
    print(f"Seed: {partition['seed']}")
    print()
    print("TUNE side:")
    print(f"  Cases: {partition['tune']['n_cases']}")
    print(f"  URLs: {len(partition['tune']['urls'])}")
    print(f"  Status distribution: {partition['tune']['status_distribution']}")
    print()
    print("HELD-OUT side:")
    print(f"  Cases: {partition['heldout']['n_cases']}")
    print(f"  URLs: {len(partition['heldout']['urls'])}")
    print(f"  Status distribution: {partition['heldout']['status_distribution']}")


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def baseline_always_supported(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Predict ``"supported"`` for every case."""
    n = len(cases)
    correct = sum(1 for c in cases if c["expected_status"] == "supported")
    return {
        "name": "always_supported",
        "correct": correct,
        "n": n,
        "accuracy": correct / n if n > 0 else 0.0,
    }


def baseline_always_unsupported(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Predict ``"unsupported"`` for every case."""
    n = len(cases)
    correct = sum(1 for c in cases if c["expected_status"] == "unsupported")
    return {
        "name": "always_unsupported",
        "correct": correct,
        "n": n,
        "accuracy": correct / n if n > 0 else 0.0,
    }


def baseline_majority_class(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Predict the most frequent expected status (ORACLE-fitted).

    Uses the labels to pick the majority class, so this is NOT a blind
    baseline — it is an upper bound on what a label-aware trivial classifier
    can achieve.
    """
    n = len(cases)
    status_counts: Counter = Counter(c["expected_status"] for c in cases)
    majority = status_counts.most_common(1)[0][0]
    correct = status_counts[majority]
    return {
        "name": "majority_class",
        "correct": correct,
        "n": n,
        "accuracy": correct / n if n > 0 else 0.0,
        "majority_status": majority,
    }


def baseline_random_by_distribution(
    cases: list[dict[str, Any]],
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Sample predictions from the observed expected-status distribution.

    Uses ``random.choices`` with weights proportional to class counts.
    """
    n = len(cases)
    status_counts: Counter = Counter(c["expected_status"] for c in cases)
    statuses = list(status_counts.keys())
    weights = [status_counts[s] for s in statuses]

    rng = random.Random(seed)
    correct = 0
    for c in cases:
        predicted = rng.choices(statuses, weights=weights)[0]
        if predicted == c["expected_status"]:
            correct += 1

    return {
        "name": "random_by_distribution",
        "correct": correct,
        "n": n,
        "accuracy": correct / n if n > 0 else 0.0,
    }


def baseline_status_prior_random(
    cases: list[dict[str, Any]],
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Same as ``random_by_distribution`` but via a separate implementation path.

    Uses ``random.random()`` with a cumulative distribution function instead
    of ``random.choices``. Serves as a sanity cross-check: both baselines
    estimate the same quantity (expected accuracy of a distribution-sampling
    classifier), so large disagreements indicate a bug.
    """
    n = len(cases)
    status_counts: Counter = Counter(c["expected_status"] for c in cases)
    total = sum(status_counts.values())

    # Build cumulative distribution
    cumdist: list[tuple[float, str]] = []
    cum = 0
    for status, count in sorted(status_counts.items()):
        cum += count
        cumdist.append((cum / total, status))

    rng = random.Random(seed)
    correct = 0
    for c in cases:
        r = rng.random()
        predicted = cumdist[-1][1]  # fallback
        for threshold, status in cumdist:
            if r < threshold:
                predicted = status
                break
        if predicted == c["expected_status"]:
            correct += 1

    return {
        "name": "status_prior_random",
        "correct": correct,
        "n": n,
        "accuracy": correct / n if n > 0 else 0.0,
    }


def baseline_tier1_always_supported(
    cases: list[dict[str, Any]],
    tier_data: list[dict[str, Any]],
) -> dict[str, Any]:
    """Citesure-specific: predict ``"supported"`` when ``tier_reached == 1``.

    Reads per-record tier data from a results JSON file (``records`` array
    with ``id``, ``tier_reached``). Predicts ``"supported"`` for cases that
    stopped at tier 1 (reachability check only), ``"unsupported"`` otherwise.
    """
    tier_by_id = {r["id"]: r["tier_reached"] for r in tier_data}
    n = len(cases)
    correct = 0
    for c in cases:
        tier = tier_by_id.get(c["id"])
        if tier is None:
            continue
        predicted = "supported" if tier == 1 else "unsupported"
        if predicted == c["expected_status"]:
            correct += 1

    return {
        "name": "tier1_always_supported",
        "correct": correct,
        "n": n,
        "accuracy": correct / n if n > 0 else 0.0,
    }


def run_baselines(
    cases: list[dict[str, Any]],
    tier_data: list[dict[str, Any]] | None = None,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, Any]]:
    """Run all baselines and return results with Wilson CIs."""
    results = [
        baseline_always_supported(cases),
        baseline_always_unsupported(cases),
        baseline_majority_class(cases),
        baseline_random_by_distribution(cases, seed),
        baseline_status_prior_random(cases, seed),
    ]
    if tier_data is not None:
        results.append(baseline_tier1_always_supported(cases, tier_data))

    # Add Wilson CI
    for r in results:
        lo, hi = wilson_ci(r["correct"], r["n"])
        r["wilson_ci"] = (lo, hi)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="URL-clustered tune/held-out split + trivial baselines",
    )
    parser.add_argument("--set", required=True, help="Path to eval set JSON")
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Random seed (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--tier-data",
        help="Path to tier data JSON (for tier1_always_supported baseline)",
    )
    args = parser.parse_args()

    cases = load_cases(args.set)

    # Split
    partition = build_partition(cases, args.seed)
    out_path = write_partition(partition, args.set, args.seed)
    print_summary(partition)
    print(f"\nPartition written to: {out_path}")

    # Baselines
    tier_data = None
    if args.tier_data:
        with open(args.tier_data) as f:
            tier_data = json.load(f).get("records", [])

    results = run_baselines(cases, tier_data, args.seed)
    print("\nBaselines:")
    for r in results:
        lo, hi = r["wilson_ci"]
        print(
            f"  {r['name']}: {r['correct']}/{r['n']} = "
            f"{r['accuracy']:.3f} [{lo:.3f}, {hi:.3f}]"
        )


if __name__ == "__main__":
    main()
