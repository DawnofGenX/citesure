"""Task 2 (IMP-3) tests: contradiction signal.

- status_for_nli forces unsupported when contradiction >= 0.5.
- The override cannot touch an ent >= 0.7 supported verdict.
- _contradiction_probs returns 0.0 for binary models.
- score_nli_batch_all returns (ent, con) tuples.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import torch

from citesure.models import Status
from citesure.nli import (
    CONTRADICTION_THRESHOLD,
    _contradiction_probs,
    apply_nli_tier,
    score_nli_batch_all,
    status_for_nli,
)


# ---------------------------------------------------------------------------
# status_for_nli: contradiction override
# ---------------------------------------------------------------------------

def test_contradiction_overrides_ambiguous():
    """con >= 0.5 forces unsupported even when ent is mid-band (ambiguous)."""
    result = status_for_nli(Status.SUPPORTED, nli_score=0.4, contradiction=0.8)
    assert result is Status.UNSUPPORTED


def test_contradiction_overrides_supported_band():
    """con >= 0.5 forces unsupported even when ent is in supported band."""
    result = status_for_nli(Status.SUPPORTED, nli_score=0.7, contradiction=0.8)
    assert result is Status.UNSUPPORTED


def test_no_contradiction_unchanged():
    """Without contradiction, banding is unchanged."""
    assert status_for_nli(Status.SUPPORTED, nli_score=0.9) is Status.SUPPORTED
    assert status_for_nli(Status.SUPPORTED, nli_score=0.5) is Status.AMBIGUOUS
    assert status_for_nli(Status.SUPPORTED, nli_score=0.1) is Status.UNSUPPORTED


def test_contradiction_below_threshold_no_effect():
    """con < 0.5 does not trigger the override."""
    result = status_for_nli(Status.SUPPORTED, nli_score=0.9, contradiction=0.3)
    assert result is Status.SUPPORTED


def test_contradiction_none_no_effect():
    """contriction=None uses normal banding."""
    assert status_for_nli(Status.SUPPORTED, nli_score=0.9, contradiction=None) is Status.SUPPORTED


def test_contradiction_cannot_touch_high_ent():
    """The override can never produce a supported verdict (safety invariant)."""
    result = status_for_nli(Status.SUPPORTED, nli_score=0.99, contradiction=0.5)
    assert result is Status.UNSUPPORTED


# ---------------------------------------------------------------------------
# apply_nli_tier: contradiction threading
# ---------------------------------------------------------------------------

def test_apply_nli_tier_logs_contradiction():
    """apply_nli_tier records contradiction in notes."""
    notes = []
    final, tier, score, notes = apply_nli_tier(
        Status.SUPPORTED, notes, nli_score=0.4, marker_locatable=True,
        evidence="test", contradiction=0.8,
    )
    assert final is Status.UNSUPPORTED
    assert any("contradiction" in n for n in notes)


def test_apply_nli_tier_no_contradiction():
    """apply_nli_tier without contradiction works as before."""
    notes = []
    final, tier, score, notes = apply_nli_tier(
        Status.SUPPORTED, notes, nli_score=0.9, marker_locatable=True,
        evidence="test",
    )
    assert final is Status.SUPPORTED


# ---------------------------------------------------------------------------
# _contradiction_probs: binary model fallback
# ---------------------------------------------------------------------------

def test_contradiction_probs_binary_model():
    """Binary (single-logit) models return 0.0 for contradiction."""
    model = MagicMock()
    model.config.num_labels = 1
    model.eval = MagicMock()
    model.device = "cpu"
    tokenizer = MagicMock()
    result = _contradiction_probs(model, tokenizer, {0: "entailment"}, [("claim", "passage")])
    assert result == [0.0]


def test_contradiction_probs_three_label_model():
    """3-label models return the contradiction column."""

    class FakeConfig:
        num_labels = 3

    class FakeModel:
        config = FakeConfig()
        device = "cpu"

        def eval(self):
            pass

        def __call__(self, **kwargs):
            class Out:
                logits = torch.tensor([[2.0, 0.5, 0.5]])
            return Out()

    class FakeTokenizer:
        def __call__(self, *args, **kwargs):
            return {"input_ids": torch.tensor([[1, 2]]),
                    "attention_mask": torch.tensor([[1, 1]])}

    model = FakeModel()
    tokenizer = FakeTokenizer()
    result = _contradiction_probs(model, tokenizer,
                                  {0: "contradiction", 1: "entailment", 2: "neutral"},
                                  [("claim", "passage")])
    assert len(result) == 1
    # softmax([2.0, 0.5, 0.5])[0] = exp(2.0) / (exp(2.0) + 2*exp(0.5))
    # ≈ 7.389 / (7.389 + 2*1.6487) ≈ 0.6914
    assert abs(result[0] - 0.6914) < 0.01


# ---------------------------------------------------------------------------
# score_nli_batch_all
# ---------------------------------------------------------------------------

def test_score_nli_batch_all_returns_tuples():
    """score_nli_batch_all returns (ent, con) per pair."""
    from citesure.nli import NLICrossEncoder
    model = MagicMock(spec=NLICrossEncoder)
    model.predict_all = MagicMock(return_value=[(0.9, 0.1), (0.2, 0.7)])
    result = score_nli_batch_all(model, [("c1", "p1"), ("c2", "p2")])
    assert result == [(0.9, 0.1), (0.2, 0.7)]


def test_score_nli_batch_all_empty():
    """Empty input returns empty list."""
    from citesure.nli import NLICrossEncoder
    model = MagicMock(spec=NLICrossEncoder)
    assert score_nli_batch_all(model, []) == []
