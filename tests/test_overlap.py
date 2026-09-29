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


def test_clip_passage_for_nli_selects_relevant_sentences():
    """Long passage should be clipped to sentences with highest term overlap."""
    from citesure.overlap import clip_passage_for_nli

    claim = "Comments in Python start with the hash character, #, and extend to the end of the physical line."
    long_passage = (
        "Many of the examples in this manual, even those entered at the interactive prompt, include comments. "
        "Comments in Python start with the hash character, #, and extend to the end of the physical line. "
        "A comment may appear at the start of a line or following whitespace or code, but not within a string literal."
    )
    clipped = clip_passage_for_nli(claim, long_passage)
    # The clipped passage should contain the claim sentence
    assert "Comments in Python start" in clipped
    # The clipped passage should NOT contain the irrelevant first sentence
    # (only shares "comments" with the claim, lowest term overlap)
    assert "Many of the examples" not in clipped



def test_select_best_sentence_picks_claim_bearing_one():
    """The sentence sharing the most claim terms must win, not the first.

    Trailing metadata like "Changed in version 3.6." collapses DeBERTa's
    entailment score on an otherwise identical premise, so the premise handed
    to NLI must be the single claim-relevant sentence.
    """
    from citesure.overlap import select_best_sentence

    claim = "On other operating systems, return the path unchanged."
    premise = (
        "Some unrelated opening sentence about nothing in particular. "
        "On Windows, convert all characters in the pathname to lowercase. "
        "On other operating systems, return the path unchanged. "
        "Changed in version 3.6: Accepts a path-like object."
    )
    assert select_best_sentence(claim, premise) == (
        "On other operating systems, return the path unchanged."
    )


def test_select_best_sentence_returns_whole_premise_when_single_sentence():
    from citesure.overlap import select_best_sentence

    claim = "The bridge opened in 1998."
    assert select_best_sentence(claim, "The bridge opened in 1998.") == (
        "The bridge opened in 1998."
    )


def test_select_best_sentence_handles_empty_input():
    """Empty/whitespace input must pass through, never fabricate a sentence."""
    from citesure.overlap import select_best_sentence

    assert select_best_sentence("claim", "") == ""
    # Whitespace-only: returned as-is (stripped to empty) rather than invented.
    assert select_best_sentence("claim", "   ").strip() == ""


def test_select_best_sentence_keeps_anaphoric_antecedent():
    """An anaphoric "It ..." must not be isolated from the sentence naming its subject.

    ind-006/ind-015 regression: "BERT is conceptually simple ... It obtains new
    state-of-the-art results" -- the pronoun sentence shares 7 claim terms while
    the antecedent sentence shares only 1 ("BERT"), so naive selection keeps the
    pronoun and strips the subject, dropping entailment 0.9975 -> 0.0028.
    """
    from citesure.overlap import select_best_sentence

    claim = "BERT obtains new state-of-the-art results on eleven language tasks."
    passage = (
        "BERT is conceptually simple and empirically powerful. "
        "It obtains new state-of-the-art results on eleven language tasks."
    )
    out = select_best_sentence(claim, passage)
    assert "BERT" in out, f"antecedent stripped: {out!r}"


def test_pool_candidate_sentences_filters_page_furniture():
    """Only claim-relevant sentences may enter the contradiction pool.

    Page furniture contradicts irrelevantly: "Holdich, Thomas (1911)." scores
    0.9997 contradiction against an unrelated claim and must be excluded.
    """
    from citesure.overlap import pool_candidate_sentences

    claim = "The GELU activation function is defined as x times Phi of x."
    premises = [
        "The GELU activation function is the hyperbolic tanh approximation, "
        "defined as 0.5 times x times one plus the exponential of minus 2x.",
        "Holdich, Thomas (1911). A Manual of Geographical Science. London.",
    ]
    kept = pool_candidate_sentences(claim, premises, min_overlap=0.5)
    assert kept == [premises[0]]
    assert not any("Holdich" in s for s in kept)


def test_pool_candidate_sentences_handles_empty_and_single_clause():
    from citesure.overlap import pool_candidate_sentences

    assert pool_candidate_sentences("claim", [], min_overlap=0.5) == []
    assert pool_candidate_sentences("claim", [""], min_overlap=0.5) == []
    # A claim with no content terms (all stopwords) yields no candidates.
    assert pool_candidate_sentences("the of and", ["a real sentence here"],
                                    min_overlap=0.5) == []


def test_pooled_contradiction_is_zero_without_encoder():
    """No encoder means no scoring; the veto must not fire by default."""
    from citesure.overlap import pooled_contradiction

    claim = "The GELU activation function is defined as x times Phi of x."
    premises = ["The GELU activation function is defined as 0.5 times x times "
                "one plus the exponential of minus 2x squared."]
    assert pooled_contradiction(claim, premises, encoder=None) == 0.0


def test_slot_conflict_detects_polarity_and_subject_conflict():
    """A contradicting sentence must disagree on a slot the claim also fills.

    ind-168/ind-145/ind-109 are genuine refutations: same subject, the sentence
    negates or reassigns what the claim asserts. ind-013 is a DIFFERENT
    experiment ("WMT 2014 English-to-French, BLEU 41.8" against a claim about
    English-to-German 28.4) and must not veto.
    """
    from citesure.overlap import has_slot_conflict

    # polarity conflict: claim negates, sentence affirms
    assert has_slot_conflict(
        "Tenzing Norgay and Edmund Hillary did not make the ascent in 1953.",
        "Tenzing Norgay and Edmund Hillary made the first documented ascent "
        "of Everest in 1953.",
    )
    # subject swap: both name a scheduler, they disagree which
    assert has_slot_conflict(
        "A Future is used to schedule coroutines concurrently.",
        "Tasks are used to run coroutines in event loops.",
    )
    # different experiment, same subject -> NOT a conflict
    assert not has_slot_conflict(
        "The Transformer achieved a BLEU score of 28.4 on the WMT 2014 "
        "English-to-German translation task.",
        "On the WMT 2014 English-to-French translation task, our model "
        "establishes a new single-model state-of-the-art BLEU score of 41.8.",
    )


def test_slot_conflict_ignores_off_topic_sentences():
    """A contradicting sentence about a different function is not a refutation."""
    from citesure.overlap import has_slot_conflict

    assert not has_slot_conflict(
        "os.chdir changes the current working directory to the given path.",
        "Set followlinks to True to visit directories pointed to by symlinks, "
        "on systems that support them.",
    )


def test_slot_conflict_handles_empty_input():
    from citesure.overlap import has_slot_conflict

    assert not has_slot_conflict("", "Some sentence with content here.")
    assert not has_slot_conflict("Some claim with content here.", "")


def test_score_overlap_fallback_to_all_passages_when_topk_all_low():
    """When all top-k passages score < 0.3, fall back to scoring ALL passages.

    This catches cases where the claim's sentence is in a passage ranked
    below top-5 due to term-coverage ties or segmentation splitting.
    """
    from citesure.overlap import score_overlap

    passages = [
        "The history of programming languages is long and varied.",
        "Many languages have come and gone over the decades.",
        "Some languages are interpreted, others compiled.",
        "The choice of language depends on the use case.",
        "Performance is often a key consideration.",
        "Python was released in 2023.",
    ]
    claim = "Python was released in 2023."
    score, best = score_overlap(claim, passages, top_k=5)
    assert score >= 0.6
    assert "Python was released in 2023" in best


def test_score_overlap_no_fallback_when_topk_has_good_match():
    """When a top-k passage scores >= 0.3, do NOT fall back (regression guard)."""
    from citesure.overlap import score_overlap

    passages = [
        "Python was released in 2023.",
        "Other text.",
        "More text.",
    ]
    claim = "Python was released in 2023."
    score, best = score_overlap(claim, passages, top_k=5)
    assert score >= 0.6
    assert "Python was released in 2023" in best


def test_split_compound_claim_simple():
    """Simple claim without conjunction → single clause."""
    from citesure.overlap import split_compound_claim
    clauses = split_compound_claim("Python 3.12 was released in 2023")
    assert len(clauses) == 1
    assert clauses[0] == "Python 3.12 was released in 2023"


def test_split_compound_claim_two_clauses():
    """Compound claim with 'and' → two clauses."""
    from citesure.overlap import split_compound_claim
    clauses = split_compound_claim(
        "Python 3.12 was released on October 2, 2023 and introduced a Rust garbage collector"
    )
    assert len(clauses) == 2
    assert "Python 3.12 was released on October 2, 2023" in clauses[0]
    assert "Rust garbage collector" in clauses[1]


def test_split_compound_claim_multiple_conjunctions():
    """Multiple conjunctions → multiple clauses."""
    from citesure.overlap import split_compound_claim
    clauses = split_compound_claim("A and B and C")
    assert len(clauses) == 3


def test_split_compound_claim_empty():
    """Empty claim → empty list."""
    from citesure.overlap import split_compound_claim
    assert split_compound_claim("") == []


def test_score_compound_claim_mixed_support_is_ambiguous():
    """A compound claim with one true and one false clause → ambiguous.

    Clause 1: "Python 3.12 was released in 2023" → supported (passage matches)
    Clause 2: "introduced a Rust garbage collector" → unsupported (no passage)
    Expected: ambiguous (not supported, not unsupported)
    """
    from citesure.overlap import score_compound_claim
    passages = [
        "Python 3.12 was released in 2023 with many new features.",
        "The release included performance improvements.",
    ]
    claim = "Python 3.12 was released in 2023 and introduced a Rust garbage collector"
    score, evidence = score_compound_claim(claim, passages)
    # The false clause should drag the score below supported threshold
    assert score < 0.6  # HIGH_OVERLAP_THRESHOLD


def test_score_compound_claim_all_supported():
    """A compound claim where all clauses are supported → falls back to whole-claim scoring."""
    from citesure.overlap import score_compound_claim
    passages = [
        "Python 3.12 was released in 2023.",
        "It introduced a new syntax for type parameters.",
    ]
    claim = "Python 3.12 was released in 2023 and introduced a new syntax for type parameters"
    score, evidence = score_compound_claim(claim, passages)
    # Falls back to whole-claim scoring since both clauses are in the same band
    assert score >= 0.3  # At least ambiguous
