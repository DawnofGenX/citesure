"""Regression tests for the compound-claim ambiguous floor.

See the module comment on test_score_compound_claim_mixed_support_floors_at_ambiguous
for the failure these guard against.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Regression guard: a compound claim must never be MORE confident than its
# weakest clause. Before the fix, score_compound_claim returned the raw MIN
# clause score, so a short trailing clause matching nothing (e.g. "faster
# startup times.") collapsed the whole claim to 0.000 -> unsupported, when the
# recorded expectation for such a partially-true claim is ambiguous.
# ---------------------------------------------------------------------------


def test_score_compound_claim_mixed_support_floors_at_ambiguous():
    """One supported clause + one unmatched clause -> ambiguous, not unsupported.

    Reproduces the ambig1 fixture: the claim's second clause shares almost no
    content terms with the page, so it scores 0.000. The correct verdict for a
    partially-true claim is the ambiguous band, not unsupported.
    """
    from citesure.overlap import (
        LOW_OVERLAP_THRESHOLD,
        HIGH_OVERLAP_THRESHOLD,
        score_compound_claim,
    )

    passages = [
        "Python 3.12 release notes Python 3.12.0 was released in autumn 2023. "
        "The interpreter now starts up roughly 5% quicker than Python 3.11, and "
        "the standard library gained several new modules for data processing.",
    ]
    claim = (
        "Python 3.12 was released on October 2, 2023, bringing a new interactive "
        "debugger and faster startup times."
    )
    score, _evidence = score_compound_claim(claim, passages)

    assert score >= LOW_OVERLAP_THRESHOLD, (
        f"mixed-support compound claim scored {score:.3f}, below the ambiguous "
        f"floor {LOW_OVERLAP_THRESHOLD}; a partially-true claim must not be "
        f"reported as unsupported"
    )
    assert score < HIGH_OVERLAP_THRESHOLD, (
        f"mixed-support compound claim scored {score:.3f}, at/above the "
        f"supported threshold {HIGH_OVERLAP_THRESHOLD}"
    )


def test_score_compound_claim_all_clauses_unmatched_stays_unsupported():
    """No clause matches at all -> genuinely unsupported; the floor must not
    rescue a claim with zero support anywhere."""
    from citesure.overlap import LOW_OVERLAP_THRESHOLD, score_compound_claim

    passages = ["Completely unrelated text about marine biology and coral reefs."]
    claim = "Python 3.12 was released in 2023 and introduced a Rust collector"
    score, _evidence = score_compound_claim(claim, passages)
    assert score < LOW_OVERLAP_THRESHOLD


def test_score_compound_claim_short_trailing_clause_does_not_zero_out():
    """The specific failure mode: a short trailing clause that matches nothing
    must not drag an otherwise-supported claim to 0.000."""
    from citesure.overlap import (
        LOW_OVERLAP_THRESHOLD,
        score_compound_claim,
        split_compound_claim,
        score_overlap,
    )

    passages = [
        "Python 3.12 was released on October 2, 2023 with a new interactive "
        "debugger included in the standard library distribution.",
    ]
    claim = (
        "Python 3.12 was released on October 2, 2023 with a new interactive "
        "debugger, and it also fixed a segfault."
    )
    clauses = split_compound_claim(claim)
    assert len(clauses) > 1, "fixture must actually split into multiple clauses"
    trailing_scores = [score_overlap(c, passages)[0] for c in clauses[1:]]
    assert min(trailing_scores) < LOW_OVERLAP_THRESHOLD, (
        "fixture must include a trailing clause below the ambiguous floor"
    )

    score, _evidence = score_compound_claim(claim, passages)
    assert score >= LOW_OVERLAP_THRESHOLD
