"""Reference-value tests for evals/stats.py.

These pin the statistics to known-good numbers so a future refactor cannot
silently change what the eval report quotes. The headline case is the real
93/108 = 86.1% result: its Wilson 95% CI is (0.783, 0.914), i.e. the honest
statement is "86% +/- 5 points", not "86.1%".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from scipy.stats import binomtest

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "evals") not in sys.path:
    sys.path.insert(0, str(REPO / "evals"))

from stats import (
    agreement_by_group,
    cluster_bootstrap_ci,
    labeler_confidence_breakdown,
    mcnemar_exact,
    wilson_ci,
)

REPO = Path(__file__).resolve().parent.parent


# --- wilson_ci -------------------------------------------------------------


def test_wilson_ci_headline_93_of_108() -> None:
    """The project's real headline: 93/108 = 86.1% -> (0.783, 0.914)."""
    lo, hi = wilson_ci(93, 108)
    assert (round(lo, 3), round(hi, 3)) == (0.783, 0.914)


def test_wilson_ci_small_samples_are_wide() -> None:
    """Quoting a small-n point estimate unqualified overstates precision.

    12/32 = 37.5% has a Wilson 95% CI of roughly (0.23, 0.55): the
    half-width is ~15 points, a third of the estimate itself. 5/32 = 15.6%
    is worse -- the interval upper bound (~0.32) is DOUBLE the point
    estimate, so a bare "15.6%" (or the 12.5% = 4/32 variant) hides that
    the true rate could plausibly be anywhere from ~7% to ~32%.
    """
    lo12, hi12 = wilson_ci(12, 32)
    assert hi12 - lo12 > 0.30, f"12/32 interval suspiciously narrow: {(lo12, hi12)}"

    lo5, hi5 = wilson_ci(5, 32)
    point = 5 / 32
    assert hi5 > 2 * point, f"5/32 upper bound {hi5} does not double the point estimate"
    assert lo5 < point < hi5


def test_wilson_ci_zero_trials_returns_degenerate() -> None:
    """n=0 must not raise and must return (0.0, 0.0)."""
    assert wilson_ci(0, 0) == (0.0, 0.0)


@pytest.mark.parametrize("n", [1, 2, 108])
def test_wilson_ci_stays_in_unit_interval(n: int) -> None:
    """k in {0, n//2, n} must never produce lo<0 or hi>1."""
    for k in {0, n // 2, n}:
        lo, hi = wilson_ci(k, n)
        assert 0.0 <= lo <= hi <= 1.0, f"k={k}, n={n} -> {(lo, hi)}"


def test_wilson_ci_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        wilson_ci(5, 4)  # k > n
    with pytest.raises(ValueError):
        wilson_ci(1, 10, confidence=1.5)


# --- cluster_bootstrap_ci ---------------------------------------------------


def test_cluster_bootstrap_wider_than_iid_under_perfect_correlation() -> None:
    """Perfectly correlated clusters must yield a wider interval than iid.

    6 clusters of size 3: 3 all-correct, 3 all-wrong (overall 9/18 = 50%).
    Resampling whole clusters can only produce accuracies in multiples of
    1/6, so the bootstrap distribution is far wider than the Wilson
    interval computed as if the 18 cases were independent. This is the
    property that justifies using the cluster bootstrap for citesure's
    URL-clustered eval set.
    """
    correct = [True] * 9 + [False] * 9
    clusters = [f"c{i}" for i in range(6) for _ in range(3)]

    lo_cb, hi_cb = cluster_bootstrap_ci(correct, clusters, n_resamples=10000, seed=0)
    lo_w, hi_w = wilson_ci(9, 18)

    assert lo_cb < lo_w, f"cluster lo {lo_cb} not below iid lo {lo_w}"
    assert hi_cb > hi_w, f"cluster hi {hi_cb} not above iid hi {hi_w}"
    # ~1.6x wider here; require a clear margin above 1x so a degenerate
    # implementation that merely matches the iid width cannot pass.
    assert hi_cb - lo_cb > 1.25 * (hi_w - lo_w)


def test_cluster_bootstrap_on_v2_clustering_shape() -> None:
    """Same property on the real v2 shape: 40 clusters, sizes {3, 1, 4}."""
    # 28 clusters of size 3, 8 of size 1, 4 of size 4 = 108 cases, 40 URLs.
    sizes = [3] * 28 + [1] * 8 + [4] * 4
    rng = __import__("random").Random(42)
    # Perfect within-cluster correlation: each cluster is all-correct or
    # all-wrong, chosen so the overall accuracy is near 50%.
    cluster_correct = [i % 2 == 0 for i in range(40)]
    rng.shuffle(cluster_correct)

    correct: list[bool] = []
    clusters: list[str] = []
    for i, (size, is_correct) in enumerate(zip(sizes, cluster_correct)):
        correct.extend([is_correct] * size)
        clusters.extend([f"url-{i}"] * size)

    k = sum(correct)
    lo_cb, hi_cb = cluster_bootstrap_ci(correct, clusters, n_resamples=10000, seed=0)
    lo_w, hi_w = wilson_ci(k, len(correct))

    assert lo_cb < lo_w
    assert hi_cb > hi_w


def test_cluster_bootstrap_deterministic_for_seed() -> None:
    """Same seed + same input -> identical output (the actual requirement).

    A cross-seed inequality assertion is deliberately NOT made: with few
    clusters the bootstrap distribution is discrete, so two seeds can
    legitimately land on the same percentile values.
    """
    correct = [True, False, True, True, False, False, True, False]
    clusters = ["a", "a", "b", "b", "c", "c", "d", "d"]
    a = cluster_bootstrap_ci(correct, clusters, n_resamples=2000, seed=7)
    b = cluster_bootstrap_ci(correct, clusters, n_resamples=2000, seed=7)
    assert a == b, "same seed must give identical output"


def test_cluster_bootstrap_all_singletons_matches_iid_scale() -> None:
    """Degenerate case: every cluster size 1 -> ordinary iid bootstrap."""
    correct = [True, False] * 25  # 50 cases, 50 clusters, 50% accuracy
    clusters = list(range(50))
    lo, hi = cluster_bootstrap_ci(correct, clusters, n_resamples=10000, seed=0)
    lo_w, hi_w = wilson_ci(25, 50)
    # Bootstrap percentile interval approximates the iid interval; allow
    # slack for bootstrap noise but require the same order of width.
    assert abs(lo - lo_w) < 0.05
    assert abs(hi - hi_w) < 0.05


def test_cluster_bootstrap_empty_input() -> None:
    assert cluster_bootstrap_ci([], []) == (0.0, 0.0)


def test_cluster_bootstrap_on_real_v2_set_shape() -> None:
    """The real v2 set: 108 cases, 40 URL clusters, sizes {3, 1, 4}.

    Synthetic correctness flags (perfect within-cluster correlation, ~50%
    overall) must produce a cluster-bootstrap interval wider than the iid
    Wilson interval on the same data -- the property that justifies the
    method for the actual eval set.
    """
    import json

    cases = json.loads((REPO / "evals" / "independent_set_v2.json").read_text(encoding="utf-8"))
    assert len(cases) == 108
    urls = [c["url"] for c in cases]
    assert len(set(urls)) == 40

    # Deterministic pseudo-labels: cluster i is all-correct iff i is even.
    cluster_ids = list(dict.fromkeys(urls))
    correct_by_url = {url: (i % 2 == 0) for i, url in enumerate(cluster_ids)}
    correct = [correct_by_url[url] for url in urls]

    k = sum(correct)
    lo_cb, hi_cb = cluster_bootstrap_ci(correct, urls, n_resamples=10000, seed=0)
    lo_w, hi_w = wilson_ci(k, len(correct))
    assert lo_cb < lo_w
    assert hi_cb > hi_w


def test_cluster_bootstrap_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        cluster_bootstrap_ci([True, False], ["a"])


# --- mcnemar_exact ----------------------------------------------------------


def test_mcnemar_exact_no_discordant_pairs() -> None:
    """b + c == 0: configs agree everywhere -> p == 1.0 exactly."""
    stat, p = mcnemar_exact(0, 0)
    assert (stat, p) == (0.0, 1.0)


def test_mcnemar_exact_matches_scipy_binomtest() -> None:
    """Reference value computed directly with scipy.stats.binomtest."""
    stat, p = mcnemar_exact(1, 9)
    assert stat == 1.0
    assert p == pytest.approx(binomtest(1, 10, 0.5, alternative="two-sided").pvalue)
    assert p == pytest.approx(0.021484375)
    # Symmetric in b and c for the two-sided test.
    assert mcnemar_exact(9, 1)[1] == pytest.approx(p)


def test_mcnemar_exact_clearly_significant() -> None:
    """10 of 10 discordant pairs won by the second config -> p < 0.01."""
    stat, p = mcnemar_exact(10, 0)
    assert stat == 10.0
    assert p == pytest.approx(0.001953125)
    assert p < 0.01


def test_mcnemar_exact_rejects_negative_counts() -> None:
    with pytest.raises(ValueError):
        mcnemar_exact(-1, 5)


# --- agreement_by_group -----------------------------------------------------


def test_agreement_by_group_basic() -> None:
    matches = [True, False, True, True]
    groups = ["tier-a", "tier-a", "tier-b", "tier-b"]
    result = agreement_by_group(matches, groups, key_fn=lambda g: g)
    assert result == {"tier-a": (1, 2), "tier-b": (2, 2)}


def test_agreement_by_group_with_key_fn_bucketing() -> None:
    matches = [True, False, True, False, True]
    groups = [1, 2, 3, 4, 5]
    result = agreement_by_group(matches, groups, key_fn=lambda g: "low" if g <= 2 else "high")
    assert result == {"low": (1, 2), "high": (2, 3)}


def test_agreement_by_group_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        agreement_by_group([True], [], key_fn=lambda g: g)


# --- labeler_confidence_breakdown -------------------------------------------


def test_labeler_confidence_breakdown_joins_on_id() -> None:
    records = [
        {"id": "ind-101", "match": True},
        {"id": "ind-102", "match": False},
        {"id": "ind-103", "match": True},
    ]
    set_cases = [
        {"id": "ind-101", "labeler_confidence": "high"},
        {"id": "ind-102", "labeler_confidence": "high"},
        {"id": "ind-103", "labeler_confidence": "medium"},
    ]
    result = labeler_confidence_breakdown(records, set_cases)
    assert result == {
        "high": {"correct": 1, "total": 2, "agreement": 0.5},
        "medium": {"correct": 1, "total": 1, "agreement": 1.0},
    }


def test_labeler_confidence_breakdown_skips_unknown_ids() -> None:
    """Records with no eval-set entry carry no confidence label; skip them."""
    records = [
        {"id": "ind-101", "match": True},
        {"id": "ghost-999", "match": False},
    ]
    set_cases = [{"id": "ind-101", "labeler_confidence": "low"}]
    result = labeler_confidence_breakdown(records, set_cases)
    assert result == {"low": {"correct": 1, "total": 1, "agreement": 1.0}}
