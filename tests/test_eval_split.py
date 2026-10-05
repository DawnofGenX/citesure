"""Tests for evals/split.py — URL-clustered split + trivial baselines.

Offline and fast. Covers:
  * Determinism (same seed -> same partition)
  * No URL appears on BOTH sides (leak guard)
  * Every input case appears in exactly one side
  * Baseline maths on hand-built inputs
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# Guard scipy/numpy import — if unavailable, skip the whole module.
pytest.importorskip("scipy", reason="scipy not installed")
pytest.importorskip("numpy", reason="numpy not installed")

# Load evals/split.py as a module (evals/ has no __init__.py)
_EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"
_spec = importlib.util.spec_from_file_location("split", _EVALS_DIR / "split.py")
split = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(split)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_case(case_id: str, url: str, status: str) -> dict:
    """Build a minimal case dict for testing."""
    return {"id": case_id, "url": url, "expected_status": status}


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    """Same input + same seed -> byte-identical partition."""

    def test_same_seed_same_partition(self):
        cases = [
            _make_case("a1", "http://x.com/1", "supported"),
            _make_case("a2", "http://x.com/1", "unsupported"),
            _make_case("a3", "http://x.com/2", "supported"),
            _make_case("a4", "http://x.com/3", "unsupported"),
            _make_case("a5", "http://x.com/3", "supported"),
        ]
        p1 = split.build_partition(cases, seed=42)
        p2 = split.build_partition(cases, seed=42)
        assert p1 == p2

    def test_same_seed_same_json(self, tmp_path):
        cases = [
            _make_case("a1", "http://x.com/1", "supported"),
            _make_case("a2", "http://x.com/1", "unsupported"),
            _make_case("a3", "http://x.com/2", "supported"),
        ]
        p1 = split.build_partition(cases, seed=42)
        p2 = split.build_partition(cases, seed=42)
        j1 = json.dumps(p1, sort_keys=True)
        j2 = json.dumps(p2, sort_keys=True)
        assert j1 == j2

    def test_different_seed_may_differ(self):
        """Different seeds should (usually) produce different partitions."""
        cases = [
            _make_case(f"c{i}", f"http://x.com/{i}", "supported" if i % 2 == 0 else "unsupported")
            for i in range(20)
        ]
        p1 = split.build_partition(cases, seed=1)
        p2 = split.build_partition(cases, seed=99)
        # Not guaranteed to differ, but with 20 URLs it's overwhelmingly likely
        assert p1 != p2


# ---------------------------------------------------------------------------
# Leak guard — no URL on both sides
# ---------------------------------------------------------------------------

class TestLeakGuard:
    """No URL may appear on BOTH sides of the split."""

    def test_no_url_overlap(self):
        cases = [
            _make_case("a1", "http://x.com/1", "supported"),
            _make_case("a2", "http://x.com/1", "unsupported"),
            _make_case("a3", "http://x.com/2", "supported"),
            _make_case("a4", "http://x.com/3", "unsupported"),
            _make_case("a5", "http://x.com/3", "supported"),
            _make_case("a6", "http://x.com/4", "unsupported"),
        ]
        p = split.build_partition(cases, seed=42)
        tune_urls = set(p["tune"]["urls"])
        heldout_urls = set(p["heldout"]["urls"])
        assert tune_urls.isdisjoint(heldout_urls)

    def test_no_url_overlap_many_cases(self):
        """Stress test with many URLs and cases."""
        cases = []
        for i in range(50):
            for j in range(3):
                cases.append(
                    _make_case(f"c{i}_{j}", f"http://x.com/{i}", "supported" if j == 0 else "unsupported")
                )
        p = split.build_partition(cases, seed=42)
        tune_urls = set(p["tune"]["urls"])
        heldout_urls = set(p["heldout"]["urls"])
        assert tune_urls.isdisjoint(heldout_urls)


# ---------------------------------------------------------------------------
# Completeness — every case in exactly one side
# ---------------------------------------------------------------------------

class TestCompleteness:
    """Every input case must appear in exactly one side."""

    def test_all_cases_accounted_for(self):
        cases = [
            _make_case("a1", "http://x.com/1", "supported"),
            _make_case("a2", "http://x.com/1", "unsupported"),
            _make_case("a3", "http://x.com/2", "supported"),
            _make_case("a4", "http://x.com/3", "unsupported"),
            _make_case("a5", "http://x.com/3", "supported"),
        ]
        p = split.build_partition(cases, seed=42)
        tune_ids = set(p["tune"]["case_ids"])
        heldout_ids = set(p["heldout"]["case_ids"])
        all_ids = set(c["id"] for c in cases)
        assert tune_ids | heldout_ids == all_ids
        assert tune_ids.isdisjoint(heldout_ids)

    def test_all_cases_accounted_for_large(self):
        cases = []
        for i in range(100):
            cases.append(_make_case(f"c{i}", f"http://x.com/{i}", "supported" if i % 2 == 0 else "unsupported"))
        p = split.build_partition(cases, seed=42)
        tune_ids = set(p["tune"]["case_ids"])
        heldout_ids = set(p["heldout"]["case_ids"])
        all_ids = set(c["id"] for c in cases)
        assert tune_ids | heldout_ids == all_ids
        assert tune_ids.isdisjoint(heldout_ids)


# ---------------------------------------------------------------------------
# Baseline maths
# ---------------------------------------------------------------------------

class TestBaselines:
    """Baseline accuracy on hand-built inputs."""

    def test_always_supported(self):
        cases = [
            _make_case("a", "http://x.com/1", "supported"),
            _make_case("b", "http://x.com/2", "supported"),
            _make_case("c", "http://x.com/3", "supported"),
            _make_case("d", "http://x.com/4", "unsupported"),
            _make_case("e", "http://x.com/5", "unsupported"),
        ]
        r = split.baseline_always_supported(cases)
        assert r["correct"] == 3
        assert r["n"] == 5
        assert r["accuracy"] == 0.6

    def test_always_unsupported(self):
        cases = [
            _make_case("a", "http://x.com/1", "supported"),
            _make_case("b", "http://x.com/2", "supported"),
            _make_case("c", "http://x.com/3", "supported"),
            _make_case("d", "http://x.com/4", "unsupported"),
            _make_case("e", "http://x.com/5", "unsupported"),
        ]
        r = split.baseline_always_unsupported(cases)
        assert r["correct"] == 2
        assert r["n"] == 5
        assert r["accuracy"] == 0.4

    def test_majority_class(self):
        cases = [
            _make_case("a", "http://x.com/1", "supported"),
            _make_case("b", "http://x.com/2", "supported"),
            _make_case("c", "http://x.com/3", "supported"),
            _make_case("d", "http://x.com/4", "unsupported"),
            _make_case("e", "http://x.com/5", "unsupported"),
        ]
        r = split.baseline_majority_class(cases)
        assert r["correct"] == 3
        assert r["n"] == 5
        assert r["accuracy"] == 0.6
        assert r["majority_status"] == "supported"

    def test_random_by_distribution_deterministic(self):
        cases = [
            _make_case("a", "http://x.com/1", "supported"),
            _make_case("b", "http://x.com/2", "supported"),
            _make_case("c", "http://x.com/3", "supported"),
            _make_case("d", "http://x.com/4", "unsupported"),
            _make_case("e", "http://x.com/5", "unsupported"),
        ]
        r1 = split.baseline_random_by_distribution(cases, seed=42)
        r2 = split.baseline_random_by_distribution(cases, seed=42)
        assert r1 == r2

    def test_status_prior_random_deterministic(self):
        cases = [
            _make_case("a", "http://x.com/1", "supported"),
            _make_case("b", "http://x.com/2", "supported"),
            _make_case("c", "http://x.com/3", "supported"),
            _make_case("d", "http://x.com/4", "unsupported"),
            _make_case("e", "http://x.com/5", "unsupported"),
        ]
        r1 = split.baseline_status_prior_random(cases, seed=42)
        r2 = split.baseline_status_prior_random(cases, seed=42)
        assert r1 == r2

    def test_tier1_always_supported(self):
        cases = [
            _make_case("a", "http://x.com/1", "supported"),
            _make_case("b", "http://x.com/2", "unsupported"),
            _make_case("c", "http://x.com/3", "supported"),
            _make_case("d", "http://x.com/4", "unsupported"),
        ]
        tier_data = [
            {"id": "a", "tier_reached": 1},
            {"id": "b", "tier_reached": 3},
            {"id": "c", "tier_reached": 1},
            {"id": "d", "tier_reached": 2},
        ]
        r = split.baseline_tier1_always_supported(cases, tier_data)
        # tier 1 -> "supported": a (correct), c (correct)
        # tier 3 -> "unsupported": b (correct)
        # tier 2 -> "unsupported": d (correct)
        assert r["correct"] == 4
        assert r["n"] == 4
        assert r["accuracy"] == 1.0


# ---------------------------------------------------------------------------
# Wilson CI integration
# ---------------------------------------------------------------------------

class TestWilsonCI:
    """Verify Wilson CI is attached to baseline results."""

    def test_wilson_ci_present(self):
        cases = [
            _make_case("a", "http://x.com/1", "supported"),
            _make_case("b", "http://x.com/2", "unsupported"),
        ]
        r = split.baseline_always_supported(cases)
        assert "wilson_ci" not in r  # CI added by run_baselines, not individual functions

    def test_run_baselines_adds_ci(self):
        cases = [
            _make_case("a", "http://x.com/1", "supported"),
            _make_case("b", "http://x.com/2", "unsupported"),
        ]
        results = split.run_baselines(cases)
        for r in results:
            assert "wilson_ci" in r
            lo, hi = r["wilson_ci"]
            assert 0.0 <= lo <= hi <= 1.0
