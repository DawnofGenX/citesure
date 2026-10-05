"""Structural invariants for the v3 independent eval set.

v3 adds per-clause bookkeeping (``supported_clauses`` / ``unsupported_clauses``)
to fix the ambiguous class that scored 0/4 recall.  These tests enforce the
structural guarantees that make the set usable: schema, clause bookkeeping,
and verbatim-span evidence for supported clauses.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
REQUIRED_KEYS = {
    "id",
    "category",
    "expected_status",
    "labeler_confidence",
    "url",
    "claim",
    "notes",
    "supported_clauses",
    "unsupported_clauses",
}
# D3 statuses. The CATEGORY "partially-true-mixed" maps to expected_status
# "ambiguous"; an earlier revision listed the category name here, which would
# have rejected every correctly-labelled mixed-support case.
VALID_STATUS = {"supported", "unsupported", "unreachable", "paywalled", "ambiguous"}
VALID_CONFIDENCE = {"high", "medium", "low"}
MIN_PARTIALLY_TRUE_MIXED = 8
MIN_FULLY_SUPPORTED_COMPOUND = 8


def _load(rel: str) -> list[dict]:
    path = REPO / rel
    if not path.exists():
        pytest.skip(f"{rel} not present yet")
    return json.loads(path.read_text(encoding="utf-8"))


def _shingles(text: str, n: int = 6) -> set[str]:
    """Return the set of n-gram shingles for *text*."""
    words = text.lower().split()
    return {" ".join(words[i : i + n]) for i in range(max(0, len(words) - n + 1))}


def test_v3_set_shape_and_labels() -> None:
    """Set loads, ids are unique and prefixed ``ind3-``, all required keys present."""
    cases = _load("evals/independent_set_v3.json")
    assert cases, "v3 set is empty"
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for c in cases:
        assert c["id"].startswith("ind3-"), f"{c['id']}: bad prefix"
        assert REQUIRED_KEYS <= set(c), f"{c['id']}: missing {REQUIRED_KEYS - set(c)}"
        assert c["expected_status"] in VALID_STATUS, f"{c['id']}: bad status"
        assert c["labeler_confidence"] in VALID_CONFIDENCE, c["id"]
        assert c["url"].startswith(("http://", "https://")), c["id"]
        assert c["claim"].strip(), c["id"]


def test_v3_clause_keys_present() -> None:
    """Every case has BOTH clause keys (even if empty lists)."""
    cases = _load("evals/independent_set_v3.json")
    for c in cases:
        assert "supported_clauses" in c, f"{c['id']}: missing supported_clauses"
        assert "unsupported_clauses" in c, f"{c['id']}: missing unsupported_clauses"
        assert isinstance(c["supported_clauses"], list), f"{c['id']}: supported_clauses not a list"
        assert isinstance(c["unsupported_clauses"], list), f"{c['id']}: unsupported_clauses not a list"


def test_v3_partially_true_mixed_has_both_sides() -> None:
    """Every ``partially-true-mixed`` case has >= 1 supported AND >= 1 unsupported clause."""
    cases = _load("evals/independent_set_v3.json")
    for c in cases:
        if c["category"] == "partially-true-mixed":
            assert c["supported_clauses"], f"{c['id']}: partially-true-mixed but no supported_clauses"
            assert c["unsupported_clauses"], f"{c['id']}: partially-true-mixed but no unsupported_clauses"


def test_v3_has_enough_partially_true_mixed() -> None:
    """At least 8 ``partially-true-mixed`` cases exist (else the set cannot serve its purpose)."""
    cases = _load("evals/independent_set_v3.json")
    n = sum(1 for c in cases if c["category"] == "partially-true-mixed")
    assert n >= MIN_PARTIALLY_TRUE_MIXED, (
        f"only {n} partially-true-mixed cases; need >= {MIN_PARTIALLY_TRUE_MIXED}"
    )


def test_v3_has_enough_fully_supported_compound_controls() -> None:
    """At least 8 ``fully-supported-compound`` CONTROL cases exist.

    A control is a compound claim (>= 2 clauses) where every clause is supported.
    These guard against the regression that killed the v2 per-clause scoring fix.
    """
    cases = _load("evals/independent_set_v3.json")
    controls = [
        c
        for c in cases
        if c["expected_status"] == "supported"
        and len(c["supported_clauses"]) >= 2
        and not c["unsupported_clauses"]
    ]
    assert len(controls) >= MIN_FULLY_SUPPORTED_COMPOUND, (
        f"only {len(controls)} fully-supported-compound controls; "
        f"need >= {MIN_FULLY_SUPPORTED_COMPOUND}"
    )


def test_v3_no_id_collision_with_v1_v2() -> None:
    """No v3 id collides with any v1 or v2 id."""
    v3 = _load("evals/independent_set_v3.json")
    old_ids: set[str] = set()
    for rel in ("evals/independent_set.json", "evals/independent_set_v2.json"):
        p = REPO / rel
        if p.exists():
            old_ids |= {c["id"] for c in json.loads(p.read_text(encoding="utf-8"))}
    v3_ids = {c["id"] for c in v3}
    assert not (v3_ids & old_ids), f"id collision: {sorted(v3_ids & old_ids)}"


def test_v3_supported_clauses_share_6gram() -> None:
    """If page text is available, every supported clause shares a 6-gram with it."""
    cases = _load("evals/independent_set_v3.json")
    pool = json.loads((REPO / "evals" / "page_pool_v3.json").read_text(encoding="utf-8"))
    by_url: dict[str, dict] = {e["url"]: e for e in pool["entries"]}
    for c in cases:
        entry = by_url.get(c["url"])
        if entry is None:
            continue
        text_path = REPO / entry["text_path"]
        if not text_path.exists():
            continue
        text = text_path.read_text(encoding="utf-8")
        text_shingles = _shingles(text)
        for clause in c["supported_clauses"]:
            assert _shingles(clause) & text_shingles, (
                f"{c['id']}: supported clause has no 6-gram in text: {clause[:60]!r}"
            )
