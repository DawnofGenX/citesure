"""Uncertainty quantification for the citesure evaluation harness.

The eval set is CORRELATED: 108 cases drawn from only 40 unique source URLs
(verbatim / negation-flip / entity-swap variants of one sentence per URL), so
cases inside a cluster are not independent and a plain iid interval is too
narrow. This module provides the statistics the report layer needs to quote
the headline NLI-on-vs-off gap with honest uncertainty:

* ``wilson_ci``            -- single-proportion interval (iid assumption).
* ``cluster_bootstrap_ci`` -- interval that respects URL-level clustering.
* ``mcnemar_exact``        -- paired two-arm comparison on discordant counts.
* ``agreement_by_group``   -- generic per-group correct/total breakdown.
* ``labeler_confidence_breakdown`` -- per-confidence-label agreement.

Pure stdlib + scipy/numpy. No citesure imports, no I/O, no side effects.
Every function is deterministic given its seed.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Hashable, Iterable, Mapping, Sequence

import numpy as np
from scipy.stats import binomtest, norm

__all__ = [
    "wilson_ci",
    "cluster_bootstrap_ci",
    "mcnemar_exact",
    "agreement_by_group",
    "labeler_confidence_breakdown",
]


def wilson_ci(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Computes the Wilson score interval for ``k`` successes in ``n`` trials at
    the given confidence level. Unlike the normal (Wald) interval, the Wilson
    interval stays inside [0, 1] and behaves well for small ``n`` and for
    proportions near 0 or 1.

    Assumes the ``n`` trials are INDEPENDENT and identically distributed.
    That assumption is wrong for citesure's eval set (cases are clustered by
    source URL); use :func:`cluster_bootstrap_ci` there. This function is the
    right tool only when the independence assumption genuinely holds.

    Edge cases: ``n == 0`` returns ``(0.0, 0.0)`` (no data, no interval);
    ``k == 0`` and ``k == n`` are handled without raising and never produce
    bounds outside [0, 1].
    """
    if n <= 0:
        return (0.0, 0.0)
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    k = int(k)
    if not 0 <= k <= n:
        raise ValueError(f"require 0 <= k <= n, got k={k}, n={n}")

    z = float(norm.ppf(1.0 - (1.0 - confidence) / 2.0))
    p_hat = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2.0 * n)) / denom
    half = (z / denom) * np.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n))
    lo = float(max(0.0, center - half))
    hi = float(min(1.0, center + half))
    return (lo, hi)


def cluster_bootstrap_ci(
    correct: Sequence[bool],
    clusters: Sequence[Hashable],
    confidence: float = 0.95,
    n_resamples: int = 10000,
    seed: int = 0,
) -> tuple[float, float]:
    """Cluster-bootstrap percentile interval for overall accuracy.

    Resamples WHOLE CLUSTERS with replacement ``n_resamples`` times, then
    recomputes the pooled accuracy (sum of correct / sum of cases across the
    resampled clusters) on each replicate, and returns the percentile
    interval of that bootstrap distribution.

    Assumes cases within a cluster are exchangeable and that clusters are
    independent of each other -- the standard cluster-bootstrap assumption,
    and the correct one for citesure where each URL contributes up to three
    near-duplicate cases. Resampling whole clusters preserves the
    within-cluster correlation instead of pretending the 108 cases are 108
    independent draws, so the interval is honestly wider than an iid one.

    Deterministic for a given ``seed`` (uses ``numpy.random.default_rng``).
    The degenerate case where every cluster has size 1 reduces to the
    ordinary iid bootstrap and is handled naturally. Empty input returns
    ``(0.0, 0.0)``.
    """
    if len(correct) != len(clusters):
        raise ValueError(
            f"correct and clusters must have equal length, "
            f"got {len(correct)} and {len(clusters)}"
        )
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if n_resamples < 1:
        raise ValueError(f"n_resamples must be >= 1, got {n_resamples}")
    n_cases = len(correct)
    if n_cases == 0:
        return (0.0, 0.0)

    # Aggregate to per-cluster (correct_count, size) so each resample is a
    # draw over clusters, not over cases.
    cluster_ids: list[Hashable] = []
    correct_counts: list[int] = []
    sizes: list[int] = []
    index: dict[Hashable, int] = {}
    for is_correct, cluster in zip(correct, clusters):
        idx = index.get(cluster)
        if idx is None:
            idx = len(cluster_ids)
            index[cluster] = idx
            cluster_ids.append(cluster)
            correct_counts.append(0)
            sizes.append(0)
        correct_counts[idx] += 1 if is_correct else 0
        sizes[idx] += 1

    cc = np.asarray(correct_counts, dtype=np.int64)
    sz = np.asarray(sizes, dtype=np.int64)
    n_clusters = len(cluster_ids)

    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_clusters, size=(n_resamples, n_clusters))
    accs = cc[draws].sum(axis=1) / sz[draws].sum(axis=1)

    tail = (1.0 - confidence) / 2.0 * 100.0
    lo = float(np.percentile(accs, tail))
    hi = float(np.percentile(accs, 100.0 - tail))
    return (lo, hi)


def mcnemar_exact(b: int, c: int) -> tuple[float, float]:
    """Exact two-sided McNemar test on paired binary outcomes.

    ``b`` = number of cases the SECOND config got right and the FIRST got
    wrong; ``c`` = the reverse (first right, second wrong). Only the
    discordant pairs carry information about a difference between the two
    arms; concordant pairs cancel out. Getting ``b`` and ``c`` backwards does
    not change the two-sided p-value, but it flips the sign of the reported
    direction, so label them carefully when writing up which config won.

    Computes the exact binomial test of the discordant split against p = 0.5
    via ``scipy.stats.binomtest`` (no chi-square approximation, so it is
    valid for small discordant counts). Returns ``(statistic, p_value)`` where
    the statistic is ``b`` itself (discordant pairs won by the second
    config). When ``b + c == 0`` the two configs agree on every case, there
    is no evidence of a difference, and the function returns ``(0.0, 1.0)``.

    Assumes the paired outcomes are independent ACROSS pairs (pairs may be
    correlated within themselves -- that is the point of the paired design).
    """
    b = int(b)
    c = int(c)
    if b < 0 or c < 0:
        raise ValueError(f"b and c must be non-negative, got b={b}, c={c}")
    if b + c == 0:
        return (0.0, 1.0)
    res = binomtest(b, b + c, 0.5, alternative="two-sided")
    return (float(b), float(res.pvalue))


def agreement_by_group(
    matches: Sequence[bool],
    groups: Sequence[Any],
    key_fn: Callable[[Any], Hashable],
) -> dict[Hashable, tuple[int, int]]:
    """Per-group correct/total breakdown.

    ``matches[i]`` is whether case ``i`` was judged correctly and
    ``groups[i]`` is that case's group identifier (e.g. a tier name or a
    labeler-confidence label). ``key_fn`` maps a group identifier to the key
    used in the returned dict, so callers can bucket or rename groups without
    touching this function.

    Assumes each case belongs to exactly one group and that ``matches`` and
    ``groups`` are aligned and of equal length. Returns a dict mapping each
    group key to ``(correct, total)``.
    """
    if len(matches) != len(groups):
        raise ValueError(
            f"matches and groups must have equal length, "
            f"got {len(matches)} and {len(groups)}"
        )
    tally: dict[Hashable, list[int]] = defaultdict(lambda: [0, 0])
    for is_correct, group in zip(matches, groups):
        key = key_fn(group)
        tally[key][0] += 1 if is_correct else 0
        tally[key][1] += 1
    return {key: (counts[0], counts[1]) for key, counts in tally.items()}


def labeler_confidence_breakdown(
    records: Iterable[Mapping[str, Any]],
    set_cases: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, float]]:
    """Agreement broken down by the eval set's ``labeler_confidence`` field.

    ``records`` are the harness's per-item records (each an ``id`` plus a
    ``match`` flag); ``set_cases`` is the loaded eval set (each an ``id`` plus
    a ``labeler_confidence`` label). The two are joined on ``id`` so the
    currently-collected-but-ignored confidence field can finally be
    analysed -- 46 of the 108 v2 cases are ``'medium'`` and nothing looks at
    them.

    Assumes every record's ``id`` exists in ``set_cases``; records whose id
    has no eval-set entry are skipped (they carry no confidence label to
    break down by). Returns a dict mapping each confidence label to
    ``{"correct": int, "total": int, "agreement": float}`` where agreement is
    ``correct / total``.
    """
    confidence_by_id = {case["id"]: case["labeler_confidence"] for case in set_cases}
    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for record in records:
        label = confidence_by_id.get(record["id"])
        if label is None:
            continue
        tally[label][0] += 1 if record["match"] else 0
        tally[label][1] += 1
    return {
        label: {
            "correct": counts[0],
            "total": counts[1],
            "agreement": counts[0] / counts[1] if counts[1] else 0.0,
        }
        for label, counts in tally.items()
    }
