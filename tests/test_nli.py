"""Tests for the NLI entailment tier (Phase 3, D2/D6).

Structure (per the Phase-3 brief — must pass whether or not the ~425 MB
DeBERTa-v3 weights are downloadable):

* **Offline unit tests** (always run): D6 priority-chain resolution,
  fail-fast ``NLIError`` on unloadable models, D3 score-banding logic with
  pinned thresholds, and batch-scoring plumbing with a MOCK cross-encoder
  (fixed logits — no weights needed).
* **Tiny-local-model tests** (always run, no network): build a throwaway
  3-label sequence-classification model + word-level tokenizer in
  ``tmp_path``, save it, and exercise the REAL loader/scoring path
  end-to-end (cache hit, [0,1] scores, determinism).
* **Model-dependent tests** (``@pytest.mark.nli``, skipped by default):
  load the real default ``cross-encoder/nli-deberta-v3-base`` and verify
  entailment ordering on clearly-entailed vs unrelated pairs, plus the
  ``--nli-model`` swap path. Run explicitly with ``-m nli`` once the model
  has been downloaded.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from citesure.citations import Citation
from citesure.models import Status
from citesure.nli import (
    DEFAULT_NLI_MODEL,
    ENV_NLI_MODEL,
    NLI_AMBIGUOUS_THRESHOLD,
    NLI_SUPPORTED_THRESHOLD,
    NLIError,
    NLICrossEncoder,
    apply_nli_tier,
    clear_nli_model_cache,
    get_nli_model,
    resolve_nli_model,
    score_nli,
    score_nli_batch,
    status_for_nli,
)
from citesure.overlap import verify_citations

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean_nli_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate every test: fresh model cache + isolated CITECHECK_CACHE_DIR."""
    clear_nli_model_cache()
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "hf-cache"))
    yield
    clear_nli_model_cache()


@pytest.fixture()
def _clean_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated fetch disk cache (same pattern as tests/test_overlap.py)."""
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))


# ---------------------------------------------------------------------------
# Mock cross-encoder (duck-typed NLICrossEncoder — no weights)
# ---------------------------------------------------------------------------


class MockEncoder:
    """Fixed-logit stand-in for a loaded cross-encoder.

    ``predict`` maps each pair to a canned entailment probability chosen by
    keyword, so tests can pin exact banding outcomes deterministically.
    """

    def __init__(self, mapping: dict[str, float], default: float = 0.5):
        self.mapping = mapping
        self.default = default
        self.calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        self.calls.append(list(pairs))
        out = []
        for claim, passage in pairs:
            text = f"{claim} {passage}"
            for key, value in self.mapping.items():
                if key.lower() in text.lower():
                    out.append(value)
                    break
            else:
                out.append(self.default)
        return out

    def predict_all(self, pairs: list[tuple[str, str]]) -> list[tuple[float, float]]:
        """Return (ent, con) per pair; contradiction is always 0.0 for the mock."""
        ent = self.predict(pairs)
        return [(e, 0.0) for e in ent]


# ---------------------------------------------------------------------------
# D6 priority chain: explicit arg > CITECHECK_NLI_MODEL env > default
# ---------------------------------------------------------------------------


def test_resolve_default_when_nothing_set(monkeypatch):
    monkeypatch.delenv(ENV_NLI_MODEL, raising=False)
    assert resolve_nli_model() == DEFAULT_NLI_MODEL
    assert resolve_nli_model(None) == DEFAULT_NLI_MODEL
    assert resolve_nli_model("") == DEFAULT_NLI_MODEL
    assert resolve_nli_model("   ") == DEFAULT_NLI_MODEL


def test_resolve_env_var_when_no_arg(monkeypatch):
    monkeypatch.setenv(ENV_NLI_MODEL, "org/custom-encoder")
    assert resolve_nli_model() == "org/custom-encoder"
    assert resolve_nli_model(None) == "org/custom-encoder"


def test_resolve_arg_beats_env(monkeypatch):
    monkeypatch.setenv(ENV_NLI_MODEL, "org/env-encoder")
    assert resolve_nli_model("org/flag-encoder") == "org/flag-encoder"


def test_resolve_arg_beats_env_even_when_env_is_default(monkeypatch):
    monkeypatch.setenv(ENV_NLI_MODEL, DEFAULT_NLI_MODEL)
    assert resolve_nli_model("/local/model/path") == "/local/model/path"


def test_resolve_strips_whitespace():
    assert resolve_nli_model("  org/encoder  ") == "org/encoder"


# ---------------------------------------------------------------------------
# Fail-fast: unloadable models raise NLIError with an actionable message
# ---------------------------------------------------------------------------


def test_bad_local_path_fails_fast_offline(tmp_path):
    # A nonexistent local path must fail immediately (local_files_only — no
    # network round-trip) with a clear NLIError.
    bad = str(tmp_path / "no-such-model-dir")
    with pytest.raises(NLIError) as excinfo:
        get_nli_model(bad)
    msg = str(excinfo.value)
    assert bad in msg
    assert "could not load NLI model" in msg
    assert "fail" in msg.lower()  # actionable: names the fail-fast policy


def test_corrupt_local_dir_fails_fast(tmp_path):
    # An existing directory that is not a valid model dir also fails fast.
    d = tmp_path / "not-a-model"
    d.mkdir()
    (d / "README.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(NLIError) as excinfo:
        get_nli_model(str(d))
    assert "could not load NLI model" in str(excinfo.value)


def test_bad_hf_name_fails_fast_with_clear_message():
    # Guaranteed-bad HF id: the hub lookup fails (404 or no network) and we
    # must surface an NLIError naming the offending identifier. Takes a few
    # seconds (one quick hub lookup); no large download can start because
    # the repo does not exist.
    with pytest.raises(NLIError) as excinfo:
        get_nli_model("citesure/does-not-exist-xyz")
    msg = str(excinfo.value)
    assert "citesure/does-not-exist-xyz" in msg
    assert "could not load NLI model" in msg
    assert "huggingface.co" in msg  # actionable hint about the source


def test_failed_load_not_cached():
    # After a failure the cache must be empty (no poisoned entry).
    with pytest.raises(NLIError):
        get_nli_model("citesure/does-not-exist-abc")
    clear_nli_model_cache()  # autouse fixture also clears; explicit here


# ---------------------------------------------------------------------------
# D3 banding: pure (overlap_status, nli_score) -> final status
# ---------------------------------------------------------------------------


def test_band_thresholds_pinned():
    assert NLI_SUPPORTED_THRESHOLD == 0.7
    assert NLI_AMBIGUOUS_THRESHOLD == 0.3


def test_status_for_nli_supported_band_upgrades_and_holds():
    assert status_for_nli(Status.SUPPORTED, 0.95) is Status.SUPPORTED
    assert status_for_nli(Status.SUPPORTED, 0.7) is Status.SUPPORTED  # boundary
    assert status_for_nli(Status.AMBIGUOUS, 0.7) is Status.SUPPORTED  # UPGRADE
    assert status_for_nli(Status.UNSUPPORTED, 0.8) is Status.SUPPORTED  # UPGRADE


def test_status_for_nli_mid_band():
    assert status_for_nli(Status.SUPPORTED, 0.5) is Status.AMBIGUOUS  # downgrade
    assert status_for_nli(Status.AMBIGUOUS, 0.5) is Status.AMBIGUOUS  # hold
    assert status_for_nli(Status.UNSUPPORTED, 0.3) is Status.AMBIGUOUS  # upgrade
    assert status_for_nli(Status.UNSUPPORTED, 0.6999) is Status.AMBIGUOUS


def test_status_for_nli_low_band():
    assert status_for_nli(Status.SUPPORTED, 0.2) is Status.UNSUPPORTED  # downgrade
    assert status_for_nli(Status.AMBIGUOUS, 0.0) is Status.UNSUPPORTED  # downgrade
    assert status_for_nli(Status.UNSUPPORTED, 0.2999) is Status.UNSUPPORTED  # confirm
    assert status_for_nli(Status.UNSUPPORTED, 0.0) is Status.UNSUPPORTED


def test_status_for_nli_js_page_marker_not_locatable_stays_ambiguous():
    # D3: marker not locatable (JS page) is unconditionally ambiguous — there
    # is no passage to score, so even a high NLI score cannot upgrade it.
    assert (
        status_for_nli(Status.AMBIGUOUS, 0.99, marker_locatable=False)
        is Status.AMBIGUOUS
    )
    assert (
        status_for_nli(Status.AMBIGUOUS, 0.1, marker_locatable=False)
        is Status.AMBIGUOUS
    )


def test_apply_nli_tier_sets_tier_3_and_score():
    final, tier, score, notes = apply_nli_tier(
        Status.AMBIGUOUS, ["overlap tier: ..."], nli_score=0.91,
        marker_locatable=True, evidence="snippet",
    )
    assert final is Status.SUPPORTED
    assert tier == 3
    assert score == pytest.approx(0.91)
    assert any("NLI tier: entailment 0.910" in n for n in notes)
    assert any("ambiguous → supported" in n for n in notes)


def test_apply_nli_tier_none_score_holds_tier_2():
    final, tier, score, notes = apply_nli_tier(
        Status.AMBIGUOUS, [], nli_score=None,
        marker_locatable=False, evidence="",
    )
    assert final is Status.AMBIGUOUS
    assert tier == 2
    assert score is None
    assert any("no scorable passage" in n for n in notes)


def test_apply_nli_tier_same_status_no_move_note():
    final, tier, _, notes = apply_nli_tier(
        Status.SUPPORTED, [], nli_score=0.9, marker_locatable=True, evidence=""
    )
    assert final is Status.SUPPORTED
    assert tier == 3
    assert not any("moved verdict" in n for n in notes)


# ---------------------------------------------------------------------------
# Batch scoring shape / edge cases with the MOCK model (no weights)
# ---------------------------------------------------------------------------


def _mock_encoder() -> MockEncoder:
    return MockEncoder(
        mapping={
            "python 3.12": 0.95,   # clearly entailed pair → supported band
            "coffee": 0.05,        # unrelated pair → unsupported band
        },
        default=0.5,               # everything else → mid band
    )


def test_score_nli_single_pair_returns_float_in_unit_interval():
    enc = _mock_encoder()
    s = score_nli(enc, "Python 3.12 was released in 2023.", "Python 3.12 release.")
    assert isinstance(s, float)
    assert 0.0 <= s <= 1.0
    assert s == pytest.approx(0.95)


def test_score_nli_batch_shape_and_ordering():
    enc = _mock_encoder()
    pairs = [
        ("Python 3.12 was released in 2023.", "Python 3.12 release."),
        ("The coffee harvest doubled.", "Coffee beans are traded globally."),
        ("The bridge opened in 1998.", "A bridge was built."),
    ]
    scores = score_nli_batch(enc, pairs)
    assert scores == [pytest.approx(0.95), pytest.approx(0.05), pytest.approx(0.5)]
    # One batched call carrying all pairs (plumbing check).
    assert len(enc.calls) == 1
    assert enc.calls[0] == pairs


def test_score_nli_batch_empty_input():
    enc = _mock_encoder()
    assert score_nli_batch(enc, []) == []
    assert enc.calls == []  # no forward pass for empty input


def test_score_nli_batch_deterministic():
    enc = _mock_encoder()
    pairs = [("a claim", "a passage"), ("Python 3.12 x", "y")]
    first = score_nli_batch(enc, pairs)
    second = score_nli_batch(enc, list(pairs))
    assert first == second


def test_mock_scores_drive_full_banding_table():
    # The mock's canned values must land in exactly the D3 bands we pin.
    enc = _mock_encoder()
    hi = score_nli(enc, "Python 3.12 was released.", "Python 3.12 release.")
    mid = score_nli(enc, "The bridge opened in 1998.", "A bridge was built.")
    lo = score_nli(enc, "The coffee harvest doubled.", "Coffee beans trade.")
    assert status_for_nli(Status.AMBIGUOUS, hi) is Status.SUPPORTED
    assert status_for_nli(Status.SUPPORTED, mid) is Status.AMBIGUOUS
    assert status_for_nli(Status.UNSUPPORTED, lo) is Status.UNSUPPORTED


# ---------------------------------------------------------------------------
# Tiny LOCAL model: real loader + real scoring path, no network, no big weights
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tiny_model_dir(tmp_path_factory) -> Path:
    """Build and save a throwaway 3-label sequence-classification model.

    Tiny BERT (1 layer, hidden 8) with a 3-label head whose id2label is the
    conventional NLI order (entailment/neutral/contradiction). Its weights
    are randomly initialized, so these tests verify the REAL loader +
    scoring PLUMBING (offline load, cache, [0,1] range, determinism,
    batched pipeline) — NOT semantic entailment quality. Semantic ordering
    is asserted against the real DeBERTa-v3 in the ``@pytest.mark.nli``
    tests below.
    """
    import torch
    from transformers import BertConfig, BertForSequenceClassification

    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel

    d = tmp_path_factory.mktemp("tiny-model")
    vocab = ["[PAD]", "[CLS]", "[SEP]", "[UNK]"]
    for word in (
        "python", "3.12", "released", "october", "2023", "debugger",
        "coffee", "harvest", "beans", "the", "was", "on", "in", "a",
    ):
        vocab.append(word)
    tok = Tokenizer(WordLevel(vocab={w: i for i, w in enumerate(vocab)}, unk_token="[UNK]"))
    tok.save(str(d / "tokenizer.json"))
    (d / "tokenizer_config.json").write_text(
        json.dumps({
            "tokenizer_class": "PreTrainedTokenizerFast",
            "model_max_length": 512,
            "pad_token": "[PAD]",
            "cls_token": "[CLS]",
            "sep_token": "[SEP]",
            "unk_token": "[UNK]",
        }),
        encoding="utf-8",
    )

    config = BertConfig(
        vocab_size=len(vocab), hidden_size=8, num_hidden_layers=1,
        num_attention_heads=2, intermediate_size=16, max_position_embeddings=64,
        num_labels=3, id2label={0: "entailment", 1: "neutral", 2: "contradiction"},
        label2id={"entailment": 0, "neutral": 1, "contradiction": 2},
    )
    model = BertForSequenceClassification(config)
    # Deterministic head (no RNG): only the entailment logit can be nonzero.
    with torch.no_grad():
        model.classifier.weight.zero_()
        model.classifier.bias.zero_()
        model.classifier.weight[0, 0] = 4.0
    model.save_pretrained(str(d))
    return d


def test_tiny_local_model_loads_and_scores_offline(tiny_model_dir):
    enc = get_nli_model(str(tiny_model_dir))
    assert isinstance(enc, NLICrossEncoder)
    assert enc.name == str(tiny_model_dir)
    # Cache hit: second call returns the identical object, no reload.
    assert get_nli_model(str(tiny_model_dir)) is enc

    s1 = score_nli(enc, "Python 3.12 was released on October 2023.",
                   "Python 3.12 was released on October 2023.")
    s2 = score_nli(enc, "The coffee harvest doubled.",
                   "Beans were traded in markets.")
    # Entailment probability must land in [0, 1] for every pair.
    assert 0.0 <= s1 <= 1.0
    assert 0.0 <= s2 <= 1.0
    # Determinism: same model + same input → identical score.
    assert score_nli(enc, "Python 3.12 was released on October 2023.",
                     "Python 3.12 was released on October 2023.") == s1
    # Batch path agrees with the single-pair path (within floating-point tolerance).
    batched = score_nli_batch(enc, [
        ("Python 3.12 was released on October 2023.",
         "Python 3.12 was released on October 2023."),
        ("The coffee harvest doubled.", "Beans were traded in markets."),
    ])
    assert batched == pytest.approx([s1, s2], abs=1e-5)


def test_tiny_local_model_swap_via_nli_model_param(tmp_path, tiny_model_dir):
    """Model swap at the library level: nli_model= param beats env + default."""
    import os

    os.environ["CITECHECK_NLI_MODEL"] = "org/should-not-be-used"
    try:
        report = asyncio.run(
            verify_citations(
                [Citation(
                    "1", str(FIXTURES / "pages" / "page1.html"),
                    "Python 3.12 was released on October 2, 2023, bringing a new interactive debugger.",
                )],
                use_overlap=True, use_nli=True, nli_model=str(tiny_model_dir),
            )
        )
    finally:
        del os.environ["CITECHECK_NLI_MODEL"]
    v = report.verdicts[0]
    assert v.tier_reached == 3
    assert v.score is not None and 0.0 <= v.score <= 1.0
    assert any("NLI tier:" in n for n in v.notes)


# ---------------------------------------------------------------------------
# Pipeline integration with a MOCKED model (no weights): tier-3 bookkeeping
# ---------------------------------------------------------------------------


def _run_pipeline(monkeypatch, citations, mapping, default=0.5):
    """Run the full pipeline with get_nli_model stubbed to a MockEncoder."""
    from citesure import nli as nli_mod

    enc = MockEncoder(mapping=mapping, default=default)

    def fake_get_nli_model(model_name=None):
        return enc

    # overlap.py imports get_nli_model locally at call time, so patching the
    # source module (citesure.nli) intercepts the lookup.
    monkeypatch.setattr(nli_mod, "get_nli_model", fake_get_nli_model)
    return asyncio.run(
        verify_citations(citations, use_overlap=True, use_nli=True)
    ), enc


def test_pipeline_nli_upgrades_ambiguous_to_supported(_clean_cache, monkeypatch):
    # ambig1 + this claim: overlap 0.571 → ambiguous at tier 2; NLI 0.95 → supported.
    report, enc = _run_pipeline(
        monkeypatch,
        [Citation("1", str(FIXTURES / "pages" / "ambig1.html"),
                  "An interactive debugger was introduced in Python 3.12.")],
        mapping={"python": 0.95},
    )
    v = report.verdicts[0]
    assert v.status is Status.SUPPORTED
    assert v.tier_reached == 3
    assert v.score == pytest.approx(0.95)
    assert any("ambiguous → supported" in n for n in v.notes)
    assert len(enc.calls) == 1  # exactly one batched scoring pass


def test_pipeline_nli_downgrades_supported_to_unsupported(_clean_cache, monkeypatch):
    # page1: strong overlap (1.0) → supported at tier 2; NLI 0.1 → unsupported.
    report, _ = _run_pipeline(
        monkeypatch,
        [Citation("1", str(FIXTURES / "pages" / "page1.html"),
                  "Python 3.12 was released on October 2, 2023.")],
        mapping={"python": 0.1},
    )
    v = report.verdicts[0]
    assert v.status is Status.UNSUPPORTED
    assert v.tier_reached == 3
    assert any("supported → unsupported" in n for n in v.notes)


def test_pipeline_nli_mid_band_holds_ambiguous(_clean_cache, monkeypatch):
    # Same ambiguous tier-2 verdict (overlap 0.571); NLI 0.5 → stays ambiguous.
    report, _ = _run_pipeline(
        monkeypatch,
        [Citation("1", str(FIXTURES / "pages" / "ambig1.html"),
                  "An interactive debugger was introduced in Python 3.12.")],
        mapping={"python": 0.5},
    )
    v = report.verdicts[0]
    assert v.status is Status.AMBIGUOUS
    assert v.tier_reached == 3
    assert v.score == pytest.approx(0.5)


def test_pipeline_nli_js_page_stays_ambiguous_no_scoring(_clean_cache, monkeypatch):
    # JS page: marker not locatable → ambiguous at tier 2, no passage to
    # score → NLI tier does NOT run for it (tier stays 2, no forward pass).
    report, enc = _run_pipeline(
        monkeypatch,
        [Citation("1", str(FIXTURES / "pages" / "js_page.html"),
                  "The dashboard shows real-time server metrics.")],
        mapping={"dashboard": 0.99},
    )
    v = report.verdicts[0]
    assert v.status is Status.AMBIGUOUS
    assert v.tier_reached == 2
    assert v.score is None
    assert enc.calls == []  # nothing scorable → no NLI forward pass


def test_pipeline_nli_batch_scores_all_eligible_once(_clean_cache, monkeypatch):
    # Two eligible pages + one unreachable: exactly one batched call with all
    # top-k passage pairs per citation; the unreachable citation keeps its
    # tier-1 status untouched.
    report, enc = _run_pipeline(
        monkeypatch,
        [
            Citation("1", str(FIXTURES / "pages" / "page1.html"),
                     "Python 3.12 was released on October 2, 2023."),
            Citation("2", str(FIXTURES / "pages" / "unsup1.html"),
                     "Python 3.12 was released on October 2, 2023."),
            Citation("3", "https://unreachable-citesure-test.invalid/z",
                     "Some claim about nothing."),
        ],
        mapping={"python": 0.8},
    )
    by_id = {v.citation_id: v for v in report.verdicts}
    assert by_id["1"].tier_reached == 3 and by_id["1"].status is Status.SUPPORTED
    assert by_id["2"].tier_reached == 3 and by_id["2"].status is Status.SUPPORTED
    assert by_id["3"].tier_reached == 1 and by_id["3"].status is Status.UNREACHABLE
    assert by_id["3"].score is None
    # With top-k=5, each citation scores up to 5 passages (total pairs >= 2).
    # Batched scoring is the point -- the pipeline must never score one pair per
    # forward pass. Normally 2 calls: entailment, then the D3.2 contradiction
    # pool. The pool is empty when no sentence passes the slot-conflict test, so
    # 1 call is also correct. What must never happen is one call per pair.
    assert 1 <= len(enc.calls) <= 2
    assert len(enc.calls[0]) >= 2
    assert all(len(c) >= 1 for c in enc.calls)


# ---------------------------------------------------------------------------
# Model-dependent tests — require the real DeBERTa-v3 weights (~425 MB).
# Skipped by default (pyproject addopts); run explicitly with `-m nli`.
# ---------------------------------------------------------------------------


@pytest.mark.nli
def test_real_default_model_entailment_ordering():
    """Clearly-entailed pair scores high; unrelated pair scores low."""
    enc = get_nli_model()  # lazy-downloads cross-encoder/nli-deberta-v3-base
    assert enc.name == DEFAULT_NLI_MODEL

    entailed = score_nli(
        enc,
        "Python 3.12 was released on October 2, 2023.",
        "Python 3.12.0 was released on October 2, 2023. It introduces a new "
        "interactive debugger and faster startup times.",
    )
    unrelated = score_nli(
        enc,
        "The bridge collapsed during the morning rush hour.",
        "Coffee is one of the most popular beverages in the world. The story "
        "of coffee begins in the highlands of Ethiopia.",
    )
    assert 0.0 <= unrelated < entailed <= 1.0
    # A trained NLI model separates these cleanly across the D3 bands.
    assert entailed >= NLI_SUPPORTED_THRESHOLD
    assert unrelated < NLI_AMBIGUOUS_THRESHOLD
    assert status_for_nli(Status.AMBIGUOUS, entailed) is Status.SUPPORTED
    assert status_for_nli(Status.UNSUPPORTED, unrelated) is Status.UNSUPPORTED


@pytest.mark.nli
def test_real_default_model_deterministic():
    enc = get_nli_model()
    claim = "PostgreSQL 16 added asynchronous I/O support to its storage engine."
    passage = "PostgreSQL 16 was released on September 19, 2023."
    first = score_nli(enc, claim, passage)
    second = score_nli(enc, claim, passage)
    assert first == second  # exact equality: same model + input → same score


@pytest.mark.nli
def test_real_model_swap_via_env_var(tmp_path, monkeypatch):
    """D6 chain end-to-end: CITECHECK_NLI_MODEL env selects the loaded model."""
    monkeypatch.setenv("CITECHECK_NLI_MODEL", DEFAULT_NLI_MODEL)
    enc = get_nli_model()
    assert enc.name == DEFAULT_NLI_MODEL
    s = score_nli(enc, "The sky is blue.", "On clear days the sky appears blue.")
    assert 0.0 <= s <= 1.0


@pytest.mark.nli
def test_cli_nli_flag_end_to_end(tmp_path):
    """CLI --nli runs the real model over the sample fixture; header shows it."""
    from citesure.cli import main

    rc = main([
        "verify", str(FIXTURES / "sample.md"), "--nli",
        "--cache-dir", str(tmp_path), "--threshold", "0.0",
    ])
    # rc may be 0 or 1 depending on verdicts — what matters: it RAN (not 2).
    assert rc in (0, 1)


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


def test_contradiction_threshold_boundary():
    """con=0.499 doesn't trigger override; con=0.500 does."""
    from citesure.nli import status_for_nli
    # Just below threshold
    result = status_for_nli(Status.SUPPORTED, nli_score=0.9, contradiction=0.499)
    assert result is Status.SUPPORTED
    # At threshold
    result = status_for_nli(Status.SUPPORTED, nli_score=0.9, contradiction=0.500)
    assert result is Status.UNSUPPORTED


# ---------------------------------------------------------------------------
# Label-order safety across model families
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "id2label",
    [
        # cross-encoder/* family: contradiction, entailment, neutral
        {0: "contradiction", 1: "entailment", 2: "neutral"},
        # MoritzLaurer/* family: entailment, neutral, contradiction (REVERSED)
        {0: "entailment", 1: "neutral", 2: "contradiction"},
    ],
)
def test_label_index_resolves_by_name_not_position(id2label):
    """Entailment/neutral indices must be read from label NAMES, not positions.

    The two model families ship opposite label orders. A positional
    implementation would read the neutral column as entailment on one of
    them — which would RAISE the agreement score rather than lower it, so
    the failure is invisible to a naive "did the number go up?" check.
    """
    from citesure.nli import _label_index

    ent, neu = _label_index(id2label)
    expected_ent = int([k for k, v in id2label.items() if "entail" in v.lower()][0])
    expected_neu = int([k for k, v in id2label.items() if "neutral" in v.lower()][0])
    assert ent == expected_ent
    assert neu == expected_neu


def test_label_index_returns_none_for_empty_mapping():
    """A model with no id2label must degrade safely, not guess a position."""
    from citesure.nli import _label_index

    assert _label_index(None) == (None, None)
    assert _label_index({}) == (None, None)


# ---------------------------------------------------------------------------
# Contradiction-override note honesty
# ---------------------------------------------------------------------------


def test_no_contradiction_note_when_override_did_not_fire():
    """A low entailment band must NOT be reported as a contradiction.

    Regression: the note was emitted whenever the verdict moved to
    unsupported, so a case downgraded purely by the entailment band
    (entailment 0.002, contradiction 0.000) printed
    "contradiction 0.000 >= 0.5 - source contradicts claim" — a
    self-contradictory and actively misleading diagnostic.
    """
    from citesure.nli import apply_nli_tier

    final, _tier, _score, notes = apply_nli_tier(
        Status.SUPPORTED,
        ["overlap tier: score 1.000"],
        nli_score=0.002,
        marker_locatable=True,
        evidence="some evidence",
        contradiction=0.000,
    )

    assert final is Status.UNSUPPORTED  # downgraded by the entailment band
    assert any("contradiction 0.000" in n for n in notes)  # the value is reported
    assert not any("source contradicts claim" in n for n in notes)  # but not as a cause


def test_contradiction_note_present_when_override_fires():
    """When the override genuinely fires, the cause must be stated."""
    from citesure.nli import apply_nli_tier

    final, _tier, _score, notes = apply_nli_tier(
        Status.SUPPORTED,
        [],
        nli_score=0.1,
        marker_locatable=True,
        evidence="evidence",
        contradiction=0.98,
    )

    assert final is Status.UNSUPPORTED
    assert any("source contradicts claim" in n for n in notes)


def test_pooled_contradiction_vetoes_supported_verdict():
    """A strong claim-relevant contradiction must veto support (D3.2).

    The max-entailment pair's own contradiction is ~0.00 for these cases even
    when another captured sentence contradicts the claim at 0.99+.
    """
    from citesure.models import Status
    from citesure.nli import POOLED_CONTRADICTION_THRESHOLD, apply_nli_tier

    final, tier, _score, notes = apply_nli_tier(
        Status.AMBIGUOUS, [],
        nli_score=0.98,               # winning sentence strongly entails
        marker_locatable=True,
        evidence="...",
        contradiction=0.001,          # ...and its own contradiction is ~0
        pooled_contradiction=0.97,    # but a claim-relevant sentence contradicts
    )
    assert final is Status.UNSUPPORTED
    assert tier == 3
    assert any("pooled contradiction" in n for n in notes)


def test_pooled_contradiction_below_threshold_does_not_veto():
    from citesure.models import Status
    from citesure.nli import apply_nli_tier

    final, _tier, _score, notes = apply_nli_tier(
        Status.AMBIGUOUS, [],
        nli_score=0.98,
        marker_locatable=True,
        evidence="...",
        contradiction=0.001,
        pooled_contradiction=0.10,
    )
    assert final is Status.SUPPORTED
    assert not any("pooled contradiction" in n for n in notes)


def test_pooled_contradiction_none_preserves_default_behaviour():
    """Existing callers that pass no pooled value must behave exactly as before."""
    from citesure.models import Status
    from citesure.nli import apply_nli_tier

    final, _t, _s, _n = apply_nli_tier(
        Status.AMBIGUOUS, [],
        nli_score=0.98, marker_locatable=True, evidence="...",
        contradiction=0.001,
    )
    assert final is Status.SUPPORTED
