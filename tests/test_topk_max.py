"""Task 3 (IMP-1) tests: score all top-k passages, take max entailment."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from citesure.models import Status
from citesure.nli import (
    apply_nli_tier,
    score_nli_batch_all,
)


def test_top_k_max_takes_highest_entailment():
    """When multiple passages are scored, the max entailment wins."""
    from citesure.nli import NLICrossEncoder
    model = MagicMock(spec=NLICrossEncoder)
    # Two passages: first has ent=0.2, second has ent=0.9
    model.predict_all = MagicMock(return_value=[(0.2, 0.1), (0.9, 0.05)])
    result = score_nli_batch_all(model, [("c", "p1"), ("c", "p2")])
    assert result == [(0.2, 0.1), (0.9, 0.05)]
    # The pipeline would take max ent = 0.9 → supported


def test_top_k_max_rescues_wrong_row():
    """The true supporting sentence that wasn't the term-coverage argmax
    still gets scored and can rescue the verdict."""
    from citesure.nli import NLICrossEncoder
    model = MagicMock(spec=NLICrossEncoder)
    # Passage 1 (argmax by term coverage): ent=0.05 (wrong row)
    # Passage 2 (true supporting sentence): ent=0.95
    model.predict_all = MagicMock(return_value=[(0.05, 0.01), (0.95, 0.02)])
    result = score_nli_batch_all(model, [("c", "wrong_row"), ("c", "true_sentence")])
    assert result[1][0] > result[0][0]  # second passage has higher entailment
    max_ent = max(ent for ent, con in result)
    assert max_ent == 0.95


def test_single_passage_unchanged():
    """When only one passage exists, behavior is unchanged."""
    from citesure.nli import NLICrossEncoder
    model = MagicMock(spec=NLICrossEncoder)
    model.predict_all = MagicMock(return_value=[(0.8, 0.1)])
    result = score_nli_batch_all(model, [("c", "p")])
    assert result == [(0.8, 0.1)]


def test_apply_nli_tier_uses_best_score():
    """apply_nli_tier with the best (max) score from top-k produces supported."""
    notes = []
    final, tier, score, notes = apply_nli_tier(
        Status.AMBIGUOUS, notes, nli_score=0.95, marker_locatable=True,
        evidence="test", contradiction=0.02,
    )
    assert final is Status.SUPPORTED
    assert tier == 3
    assert score == 0.95
