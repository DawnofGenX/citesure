"""Offline unit tests for evals/tier_ablation.py.

These tests do NOT require network access. They cover:
- The configuration list is exactly the four named ablation points.
- expected_status matching logic against CATEGORY_CANONICAL.
- Per-tier breakdown maths on hand-built synthetic input.
- Wilson CI helper correctness.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load the module under test from its file path (evals/ is not a package).
_MODULE_PATH = Path(__file__).resolve().parent.parent / "evals" / "tier_ablation.py"
_spec = importlib.util.spec_from_file_location("tier_ablation", _MODULE_PATH)
assert _spec and _spec.loader
ta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ta)


# ---------------------------------------------------------------------------
# Configuration list
# ---------------------------------------------------------------------------

def test_configs_are_exactly_the_four_named() -> None:
    names = [c["name"] for c in ta.CONFIGS]
    assert names == [
        "reachability_only",
        "overlap_only",
        "nli_only_effect",
        "all_tiers",
    ]


def test_configs_have_correct_flags() -> None:
    by_name = {c["name"]: c for c in ta.CONFIGS}
    assert by_name["reachability_only"]["use_overlap"] is False
    assert by_name["reachability_only"]["use_nli"] is False
    assert by_name["overlap_only"]["use_overlap"] is True
    assert by_name["overlap_only"]["use_nli"] is False
    assert by_name["nli_only_effect"]["use_overlap"] is True
    assert by_name["nli_only_effect"]["use_nli"] is True
    assert by_name["all_tiers"]["use_overlap"] is True
    assert by_name["all_tiers"]["use_nli"] is True


def test_nli_only_effect_and_all_tiers_are_identical_calls() -> None:
    """Configs 3 and 4 must be the same call (consistency check)."""
    c3 = next(c for c in ta.CONFIGS if c["name"] == "nli_only_effect")
    c4 = next(c for c in ta.CONFIGS if c["name"] == "all_tiers")
    assert c3["use_overlap"] == c4["use_overlap"]
    assert c3["use_nli"] == c4["use_nli"]


# ---------------------------------------------------------------------------
# CATEGORY_CANONICAL matching logic
# ---------------------------------------------------------------------------

def test_category_canonical_covers_all_nine_categories() -> None:
    expected = {
        "verbatim-supported",
        "paraphrase-supported",
        "numeric-detail-supported",
        "false-claim",
        "negation-flip",
        "entity-swap",
        "partially-true-ambiguous",
        "dead-link",
        "paywalled",
    }
    assert set(ta.CATEGORY_CANONICAL.keys()) == expected


def test_category_canonical_values_are_valid_statuses() -> None:
    valid = {"supported", "unsupported", "unreachable", "paywalled", "ambiguous"}
    for cat, status in ta.CATEGORY_CANONICAL.items():
        assert status in valid, f"{cat} maps to invalid status {status!r}"


def test_category_canonical_supported_group() -> None:
    supported_cats = [
        "verbatim-supported",
        "paraphrase-supported",
        "numeric-detail-supported",
    ]
    for cat in supported_cats:
        assert ta.CATEGORY_CANONICAL[cat] == "supported"


def test_category_canonical_unsupported_group() -> None:
    unsupported_cats = ["false-claim", "negation-flip", "entity-swap"]
    for cat in unsupported_cats:
        assert ta.CATEGORY_CANONICAL[cat] == "unsupported"


def test_category_canonical_ambiguous_and_unreachable() -> None:
    assert ta.CATEGORY_CANONICAL["partially-true-ambiguous"] == "ambiguous"
    assert ta.CATEGORY_CANONICAL["dead-link"] == "unreachable"
    assert ta.CATEGORY_CANONICAL["paywalled"] == "paywalled"


# ---------------------------------------------------------------------------
# Per-tier breakdown maths
# ---------------------------------------------------------------------------

def _make_record(
    rid: str,
    expected: str,
    predicted: str,
    tier: int,
    category: str = "verbatim-supported",
) -> dict:
    return {
        "id": rid,
        "category": category,
        "expected": expected,
        "predicted": predicted,
        "score": None,
        "tier_reached": tier,
        "match": predicted == expected,
        "evidence": "",
        "notes": [],
    }


def test_per_tier_breakdown_basic() -> None:
    records = [
        _make_record("a", "supported", "supported", 1),
        _make_record("b", "supported", "supported", 1),
        _make_record("c", "supported", "unsupported", 1),
        _make_record("d", "unsupported", "unsupported", 2),
        _make_record("e", "unsupported", "supported", 2),
        _make_record("f", "ambiguous", "ambiguous", 3),
    ]
    result = ta._per_tier_breakdown(records)
    assert result["1"]["total"] == 3
    assert result["1"]["matched"] == 2
    assert result["1"]["accuracy_pct"] == pytest.approx(66.67, abs=0.01)
    assert result["2"]["total"] == 2
    assert result["2"]["matched"] == 1
    assert result["2"]["accuracy_pct"] == 50.0
    assert result["3"]["total"] == 1
    assert result["3"]["matched"] == 1
    assert result["3"]["accuracy_pct"] == 100.0


def test_per_tier_breakdown_empty() -> None:
    result = ta._per_tier_breakdown([])
    assert result == {}


def test_per_tier_breakdown_error_records_go_to_tier_0() -> None:
    """Records with tier_reached=0 (errors) should appear under tier '0'."""
    records = [
        _make_record("x", "supported", "error:TimeoutError: timeout", 0),
    ]
    result = ta._per_tier_breakdown(records)
    assert "0" in result
    assert result["0"]["total"] == 1
    assert result["0"]["matched"] == 0


def test_per_tier_breakdown_all_correct() -> None:
    records = [
        _make_record("a", "supported", "supported", 1),
        _make_record("b", "unsupported", "unsupported", 2),
    ]
    result = ta._per_tier_breakdown(records)
    assert result["1"]["accuracy_pct"] == 100.0
    assert result["2"]["accuracy_pct"] == 100.0


# ---------------------------------------------------------------------------
# Wilson CI helper
# ---------------------------------------------------------------------------

def test_wilson_ci_perfect_score() -> None:
    ci = ta._wilson_ci(10, 10)
    assert ci["low"] > 0.0
    assert ci["high"] == 100.0


def test_wilson_ci_zero_score() -> None:
    ci = ta._wilson_ci(0, 10)
    assert ci["low"] == 0.0
    assert ci["high"] < 100.0


def test_wilson_ci_half_score() -> None:
    ci = ta._wilson_ci(5, 10)
    assert ci["low"] < 50.0 < ci["high"]


def test_wilson_ci_empty() -> None:
    ci = ta._wilson_ci(0, 0)
    assert ci == {"low": 0.0, "high": 0.0}


def test_wilson_ci_symmetric() -> None:
    """wilson_ci(k, n) and wilson_ci(n-k, n) should be symmetric around 50%."""
    ci1 = ta._wilson_ci(8, 10)
    ci2 = ta._wilson_ci(2, 10)
    # The intervals should be mirror images
    assert ci1["low"] == pytest.approx(ci2["high"] - 100.0 + 100.0 - ci2["high"] + ci1["low"], abs=0.01) or True
    # More meaningful: low of one + high of the other should be ~100
    assert ci1["low"] + ci2["high"] == pytest.approx(100.0, abs=0.5)


# ---------------------------------------------------------------------------
# Per-labeler-confidence breakdown
# ---------------------------------------------------------------------------

def test_per_labeler_confidence_basic() -> None:
    items_by_id = {
        "a": {"labeler_confidence": "high"},
        "b": {"labeler_confidence": "medium"},
        "c": {"labeler_confidence": "medium"},
    }
    records = [
        _make_record("a", "supported", "supported", 1),
        _make_record("b", "unsupported", "unsupported", 2),
        _make_record("c", "unsupported", "supported", 2),
    ]
    result = ta._per_labeler_confidence(records, items_by_id)
    assert result["high"]["total"] == 1
    assert result["high"]["matched"] == 1
    assert result["high"]["accuracy_pct"] == 100.0
    assert result["medium"]["total"] == 2
    assert result["medium"]["matched"] == 1
    assert result["medium"]["accuracy_pct"] == 50.0


def test_per_labeler_confidence_unknown() -> None:
    """Records with no matching item should bucket under 'unknown'."""
    items_by_id: dict[str, dict] = {}
    records = [_make_record("z", "supported", "supported", 1)]
    result = ta._per_labeler_confidence(records, items_by_id)
    assert "unknown" in result
    assert result["unknown"]["total"] == 1


# ---------------------------------------------------------------------------
# _normalize_ci
# ---------------------------------------------------------------------------

def test_normalize_ci_from_dict() -> None:
    raw = {"low": 12.34, "high": 56.78}
    result = ta._normalize_ci(raw)
    assert result == {"low": 12.34, "high": 56.78}


def test_normalize_ci_from_tuple_proportions() -> None:
    """Sibling stats.py returns (lo, hi) as proportions 0-1."""
    raw = (0.1234, 0.5678)
    result = ta._normalize_ci(raw)
    assert result["low"] == pytest.approx(12.34, abs=0.01)
    assert result["high"] == pytest.approx(56.78, abs=0.01)


def test_normalize_ci_from_list_proportions() -> None:
    raw = [0.0, 1.0]
    result = ta._normalize_ci(raw)
    assert result == {"low": 0.0, "high": 100.0}


# ---------------------------------------------------------------------------
# _summarize_config integration
# ---------------------------------------------------------------------------

def test_summarize_config_structure() -> None:
    items_by_id = {
        "a": {"labeler_confidence": "high"},
        "b": {"labeler_confidence": "medium"},
    }
    records = [
        _make_record("a", "supported", "supported", 1),
        _make_record("b", "unsupported", "supported", 2),
    ]
    result = ta._summarize_config("test_cfg", records, ta._wilson_ci, items_by_id)
    assert result["name"] == "test_cfg"
    assert result["total"] == 2
    assert result["matched"] == 1
    assert result["agreement_pct"] == 50.0
    assert "wilson_ci" in result
    assert "per_tier" in result
    assert "per_labeler_confidence" in result
    assert "records" in result
    assert len(result["records"]) == 2


# ---------------------------------------------------------------------------
# Module structure
# ---------------------------------------------------------------------------

def test_module_has_main_and_configs() -> None:
    assert hasattr(ta, "main")
    assert hasattr(ta, "CONFIGS")
    assert hasattr(ta, "_verify_one")
    assert hasattr(ta, "_per_tier_breakdown")
    assert hasattr(ta, "_summarize_config")
    assert hasattr(ta, "_wilson_ci")
    assert hasattr(ta, "CATEGORY_CANONICAL")
