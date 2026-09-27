"""Unit tests for the content-overlap tier (Phase 2, D4).

Covers: tokenization/weights, passage segmentation, top-k selection,
``score_overlap`` with pinned thresholds, verdict mapping, the JS-page
(marker-not-locatable) rule, and the user-supplied excerpt override path.
All offline.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from citesure.citations import Citation
from citesure.overlap import (
    CONTENT_WEIGHT,
    DEFAULT_TOP_K,
    ENTITY_WEIGHT,
    HIGH_OVERLAP_THRESHOLD,
    LOW_OVERLAP_THRESHOLD,
    MIN_EXTRACTABLE_CHARS,
    clean_claim,
    content_terms,
    entity_terms,
    expand_context,
    rank_passages,
    score_overlap,
    segment_passages,
    status_for_score,
    term_weights,
    verify_citations,
)
from citesure.models import Status

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Tokenization / weights
# ---------------------------------------------------------------------------


def test_clean_claim_strips_numeric_markers():
    assert clean_claim("The sky is blue [1].") == "The sky is blue ."


def test_clean_claim_collapses_inline_links_to_label():
    assert clean_claim("See [the docs](https://d.example/api) now.") == "See the docs now."


def test_content_terms_drop_stopwords_and_single_letters():
    terms = content_terms("The quick brown fox jumps over the lazy dog.")
    assert "the" not in terms
    assert "quick" in terms
    assert "fox" in terms


def test_entity_terms_include_numbers_capitalized_and_quoted():
    text = 'Python 3.12 was released by the "Example Foundation" team.'
    ents = entity_terms(text)
    assert "3.12" in ents
    assert "python" in ents
    assert "example foundation" in ents
    assert "was" not in ents


def test_term_weights_upgrade_entities():
    weights = term_weights("Python 3.12 release")
    assert weights["python"] == ENTITY_WEIGHT
    assert weights["3.12"] == ENTITY_WEIGHT
    assert weights["release"] == CONTENT_WEIGHT


# ---------------------------------------------------------------------------
# Passage segmentation
# ---------------------------------------------------------------------------


def test_segment_passages_empty_text():
    assert segment_passages("") == []
    assert segment_passages("   \n\n ") == []


def test_segment_passages_groups_sentences_up_to_cap():
    text = " ".join(f"Sentence number {i} talks about topic {i}." for i in range(1, 40))
    passages = segment_passages(text)
    assert len(passages) > 1
    # No passage exceeds the cap by more than one sentence.
    for p in passages:
        assert len(p) <= 600  # PASSAGE_MAX_CHARS + one sentence slack
    # All sentences preserved (no content lost).
    joined = " ".join(passages)
    assert "Sentence number 39" in joined


def test_segment_passages_merges_tiny_trailing_fragment():
    text = "A long enough sentence to stand on its own here. ok"
    passages = segment_passages(text)
    assert passages == ["A long enough sentence to stand on its own here. ok"]


def test_segment_passages_deterministic():
    text = "First fact here. Second fact here. Third fact here. Fourth fact here."
    assert segment_passages(text) == segment_passages(text)


# ---------------------------------------------------------------------------
# Top-k selection
# ---------------------------------------------------------------------------


def test_rank_passages_orders_by_coverage_best_first():
    passages = [
        "Completely unrelated text about gardening and tomatoes.",
        "Python 3.12 was released on October 2, 2023.",
        "Another unrelated paragraph about ocean currents.",
    ]
    ranked = rank_passages("Python 3.12 was released on October 2, 2023.", passages, top_k=2)
    assert len(ranked) == 2
    assert "Python 3.12" in ranked[0][1]
    assert ranked[0][0] >= ranked[1][0]


def test_rank_passages_top_k_limits_results():
    passages = [f"Unrelated filler sentence number {i}." for i in range(10)]
    ranked = rank_passages("Some claim about nothing here.", passages, top_k=DEFAULT_TOP_K)
    assert len(ranked) == DEFAULT_TOP_K


def test_rank_passages_ties_break_on_original_order():
    passages = ["alpha beta gamma", "alpha beta gamma", "delta epsilon zeta"]
    ranked = rank_passages("alpha beta", passages, top_k=3)
    assert [p for _, p in ranked[:2]] == ["alpha beta gamma", "alpha beta gamma"]


# ---------------------------------------------------------------------------
# score_overlap — pinned thresholds (D3)
# ---------------------------------------------------------------------------


def test_score_overlap_strong_overlap_is_high():
    claim = "Python 3.12 was released on October 2, 2023, bringing a new interactive debugger."
    passages = [
        "Python 3.12.0 was released on October 2, 2023. It introduces a new interactive debugger.",
        "Unrelated text about coffee brewing methods.",
    ]
    score, snippet = score_overlap(claim, passages)
    assert score >= HIGH_OVERLAP_THRESHOLD
    assert "Python 3.12" in snippet
    assert status_for_score(score) is Status.SUPPORTED


def test_score_overlap_partial_overlap_is_mid_band():
    claim = "Python 3.12 was released on October 2, 2023, bringing a new interactive debugger."
    passages = ["Python 3.12.0 was released in autumn 2023."]
    score, _ = score_overlap(claim, passages)
    assert LOW_OVERLAP_THRESHOLD <= score < HIGH_OVERLAP_THRESHOLD
    assert status_for_score(score) is Status.AMBIGUOUS


def test_score_overlap_absent_claim_is_low():
    claim = "Rust 1.75 introduced portable SIMD types."
    passages = ["The history of coffee begins in the highlands of Ethiopia."]
    score, _ = score_overlap(claim, passages)
    assert score < LOW_OVERLAP_THRESHOLD
    assert status_for_score(score) is Status.UNSUPPORTED


def test_score_overlap_empty_inputs():
    assert score_overlap("", ["some passage"]) == (0.0, "")
    assert score_overlap("Python 3.12 release", []) == (0.0, "")
    # Claim with only stopwords → no content terms → 0.0.
    assert score_overlap("the of and a", ["whatever"]) == (0.0, "")


def test_score_overlap_snippet_respects_300_char_cap():
    long_passage = "word " * 200
    score, snippet = score_overlap("word", [long_passage.strip()])
    assert len(snippet) <= 300


def test_status_for_score_threshold_boundaries():
    assert status_for_score(HIGH_OVERLAP_THRESHOLD) is Status.SUPPORTED
    assert status_for_score(HIGH_OVERLAP_THRESHOLD - 0.001) is Status.AMBIGUOUS
    assert status_for_score(LOW_OVERLAP_THRESHOLD) is Status.AMBIGUOUS
    assert status_for_score(LOW_OVERLAP_THRESHOLD - 0.001) is Status.UNSUPPORTED
    assert status_for_score(0.0) is Status.UNSUPPORTED
    assert status_for_score(1.0) is Status.SUPPORTED


def test_score_overlap_deterministic():
    claim = "PostgreSQL 16 added asynchronous I/O support to its storage engine."
    passages = [
        "PostgreSQL 16 was released on September 19, 2023.",
        "It brings incremental backup improvements.",
    ]
    first = score_overlap(claim, passages)
    second = score_overlap(claim, list(passages))
    assert first == second


# ---------------------------------------------------------------------------
# Pipeline: reachability → overlap (tier_reached), JS page, excerpt override
# ---------------------------------------------------------------------------


@pytest.fixture()
def _clean_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))


def _run(citations, **kwargs):
    return asyncio.run(verify_citations(citations, **kwargs))


def test_pipeline_supported_reaches_tier_2(_clean_cache):
    report = _run(
        [Citation("1", str(FIXTURES / "pages" / "page1.html"),
                  "Python 3.12 was released on October 2, 2023, bringing a new interactive debugger and faster startup times.")]
    )
    v = report.verdicts[0]
    assert v.status is Status.SUPPORTED
    assert v.tier_reached == 2
    assert v.score is not None and v.score >= HIGH_OVERLAP_THRESHOLD
    assert v.evidence  # best-passage snippet


def test_pipeline_unsupported_reaches_tier_2(_clean_cache):
    report = _run(
        [Citation("1", str(FIXTURES / "pages" / "unsup1.html"),
                  "Python 3.12 was released on October 2, 2023, bringing a new interactive debugger.")]
    )
    v = report.verdicts[0]
    assert v.status is Status.UNSUPPORTED
    assert v.tier_reached == 2
    assert v.score is not None and v.score < LOW_OVERLAP_THRESHOLD


def test_pipeline_js_page_is_ambiguous_marker_not_locatable(_clean_cache):
    report = _run(
        [Citation("1", str(FIXTURES / "pages" / "js_page.html"),
                  "The dashboard shows real-time server metrics and alert counts.")]
    )
    v = report.verdicts[0]
    assert v.status is Status.AMBIGUOUS
    assert v.tier_reached == 2
    assert v.score is None
    assert any("not locatable" in n for n in v.notes)


def test_pipeline_unreachable_keeps_tier_1_status(_clean_cache):
    report = _run(
        [Citation("1", "https://unreachable-citesure-test.invalid/x", "Any claim at all.")]
    )
    v = report.verdicts[0]
    assert v.status is Status.UNREACHABLE
    assert v.tier_reached == 1
    assert v.score is None


def test_pipeline_paywalled_keeps_tier_1_status(_clean_cache):
    report = _run(
        [Citation("1", str(FIXTURES / "pages" / "paywalled1.html"),
                  "The research team announced a milestone in quantum error correction.")]
    )
    v = report.verdicts[0]
    assert v.status is Status.PAYWALLED
    assert v.tier_reached == 1


def test_use_overlap_false_reproduces_tier_1_only(_clean_cache):
    report = _run(
        [Citation("1", str(FIXTURES / "pages" / "unsup1.html"),
                  "Python 3.12 was released on October 2, 2023.")],
        use_overlap=False,
    )
    v = report.verdicts[0]
    # Tier 1 alone cannot see absence → supported (Phase-1 behaviour).
    assert v.status is Status.SUPPORTED
    assert v.tier_reached == 1
    assert v.score is None


def test_excerpt_override_matches_excerpt_not_page(_clean_cache):
    # Page content is unrelated (would be unsupported), but the user-supplied
    # excerpt contains the claim → supported via the excerpt path (D4).
    report = _run(
        [Citation(
            "1",
            str(FIXTURES / "pages" / "unsup4.html"),
            "Rust 1.75 was released on July 25, 2024, introducing a new inline assembly syntax.",
            excerpt=(
                "Rust 1.75 was released on July 25, 2024. It introduces a new "
                "inline assembly syntax, portable SIMD types, and faster compile times."
            ),
        )]
    )
    v = report.verdicts[0]
    assert v.status is Status.SUPPORTED
    assert v.tier_reached == 2
    assert any("excerpt" in n for n in v.notes)


def test_excerpt_override_absent_in_excerpt_is_unsupported(_clean_cache):
    report = _run(
        [Citation(
            "1",
            str(FIXTURES / "pages" / "sup5.html"),
            "Kubernetes 1.30 promoted in-place pod vertical scaling to beta.",
            excerpt="The quick brown fox jumps over the lazy dog while the farmer naps.",
        )]
    )
    v = report.verdicts[0]
    assert v.status is Status.UNSUPPORTED
    assert v.tier_reached == 2


def test_unreachable_page_with_excerpt_stays_unreachable(_clean_cache):
    # Reachability is checked before the excerpt override (documented).
    report = _run(
        [Citation(
            "1",
            "https://unreachable-citesure-test.invalid/y",
            "Any claim at all.",
            excerpt="This excerpt contains the claim verbatim word for word.",
        )]
    )
    v = report.verdicts[0]
    assert v.status is Status.UNREACHABLE
    assert v.tier_reached == 1


def test_min_extractable_chars_constant_sane():
    # The JS fixture page must fall below the floor; real pages far above it.
    js_text = "Please enable JavaScript."
    assert len(js_text) < MIN_EXTRACTABLE_CHARS
    real = (FIXTURES / "pages" / "page1.html").read_text(encoding="utf-8")
    assert len(real) > MIN_EXTRACTABLE_CHARS


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


def test_score_overlap_empty_passages():
    score, passage = score_overlap("test claim", [])
    assert score == 0.0
    assert passage == ""


def test_score_overlap_single_passage():
    score, passage = score_overlap("Python 3.12", ["Python 3.12 was released."])
    assert score > 0.0
    assert passage == "Python 3.12 was released."


def test_score_overlap_unicode():
    score, passage = score_overlap("テスト", ["これはテストです。"])
    assert score >= 0.0  # shouldn't crash


def test_negation_word_not_stripped():
    assert "not" in content_terms("Apollo 11 did not launch")


def test_context_expansion_includes_adjacent():
    sentences = ["BERT was trained on a corpus.", "It obtains SOTA on 11 tasks."]
    result = expand_context(sentences[1], sentences, passage_idx=1, radius=1)
    assert "BERT was trained" in result


def test_context_expansion_caps_at_512():
    # When expansion would exceed 512 chars, the original passage is returned.
    # Use a passage that's < 512 chars itself, but with adjacent sentences
    # that would push the total over 512.
    long_prefix = "A" * 300
    passage = "B" * 200  # < 512 chars itself
    sentences = [long_prefix, passage, long_prefix]
    result = expand_context(passage, sentences, passage_idx=1, radius=1)
    # Expansion would be 300+200+300 = 800 > 512, so returns original passage
    assert result == passage


def test_anaphoric_sentence_includes_antecedent():
    """Passage starting with anaphoric reference should include antecedent sentence."""
    text = (
        "BERT is a bidirectional encoder pre-trained on unlabeled text. "
        "It obtains new state-of-the-art results on eleven natural language processing tasks. "
        "The model was trained on large corpora."
    )
    passages = segment_passages(text)
    # The passage containing "It obtains" should also contain "BERT"
    it_passage = [p for p in passages if "It obtains" in p]
    assert len(it_passage) == 1
    assert "BERT" in it_passage[0]


def test_anaphoric_sentence_includes_antecedent():
    """Passage starting with anaphoric reference should include antecedent sentence.

    When a sentence starts with a pronoun (it, he, she, they, this, that, etc.)
    and the previous sentence is in a different passage, the passage should
    be expanded to include the antecedent for NLI pronoun resolution.
    """
    # Long antecedent forces the anaphoric sentence into a separate passage
    antecedent = (
        "BERT is a bidirectional encoder pre-trained on unlabeled text "
        "and uses a transformer architecture with attention mechanisms "
        "that was introduced in 2018 by researchers at Google AI Language "
        "and has since become one of the most widely used natural language "
        "processing models in the world with many applications including "
        "question answering named entity recognition text classification "
        "sentiment analysis and machine translation tasks across many "
        "domains and languages"
    )
    anaphoric = "It obtains new state-of-the-art results on eleven natural language processing tasks."
    text = antecedent + " " + anaphoric

    passages = segment_passages(text)

    # Find the passage containing the anaphoric sentence
    anaphoric_passages = [p for p in passages if "It obtains" in p]
    assert len(anaphoric_passages) == 1, f"Expected 1 passage with anaphoric, got {len(anaphoric_passages)}"

    # The anaphoric passage must include the antecedent "BERT"
    assert "BERT" in anaphoric_passages[0], (
        f"Anaphoric passage missing antecedent. Got: {anaphoric_passages[0][:100]}"
    )


def test_anaphoric_sentence_includes_antecedent():
    """Passage starting with anaphoric reference should include antecedent sentence.

    When a sentence starts with a pronoun (it, he, she, they, this, that, etc.)
    and the previous sentence is in a different passage, the passage should
    be expanded to include the antecedent for NLI pronoun resolution.
    """
    # Long antecedent (466 chars > PASSAGE_MAX_CHARS=400) forces the
    # anaphoric sentence into a separate passage
    antecedent = (
        "BERT is a bidirectional encoder pre-trained on unlabeled text "
        "and uses a transformer architecture with attention mechanisms "
        "that was introduced in 2018 by researchers at Google AI Language "
        "and has since become one of the most widely used natural language "
        "processing models in the world with many applications including "
        "question answering named entity recognition text classification "
        "sentiment analysis and machine translation tasks across many "
        "domains and languages."
    )
    anaphoric = "It obtains new state-of-the-art results on eleven natural language processing tasks."
    text = antecedent + " " + anaphoric

    passages = segment_passages(text)

    # Find the passage containing the anaphoric sentence
    anaphoric_passages = [p for p in passages if "It obtains" in p]
    assert len(anaphoric_passages) == 1, f"Expected 1 passage with anaphoric, got {len(anaphoric_passages)}"

    # The anaphoric passage must include the antecedent "BERT"
    assert "BERT" in anaphoric_passages[0], (
        f"Anaphoric passage missing antecedent. Got: {anaphoric_passages[0][:100]}"
    )
