"""Task 4 (IMP-2) and Task 5 (IMP-5) tests."""

from __future__ import annotations

from citesure.overlap import (
    _NEGATION_WORDS,
    _STOPWORDS,
    content_terms,
    expand_context,
    expand_passage_in_text,
    segment_passages,
)


# ---------------------------------------------------------------------------
# IMP-5: negation words preserved
# ---------------------------------------------------------------------------

def test_negation_words_not_stopwords():
    """Negation words are NOT in the stopword set."""
    for word in ("not", "no", "never", "nor"):
        assert word not in _STOPWORDS, f"{word!r} should not be a stopword"
        assert word in _NEGATION_WORDS


def test_negation_words_in_content_terms():
    """Negation words appear in content_terms() output."""
    assert "not" in content_terms("Apollo 11 did not launch")
    assert "no" in content_terms("There is no evidence")
    assert "never" in content_terms("This never happened")


def test_negation_flip_visible():
    """A negated claim has 'not' as a content term (visible to overlap tier)."""
    terms = content_terms("Apollo 11 did not launch")
    assert "not" in terms


def test_non_negation_stopwords_still_stopped():
    """Common stopwords (not negation) are still stripped."""
    terms = content_terms("This is a test of the system")
    assert "this" not in terms
    assert "is" not in terms
    assert "of" not in terms
    assert "the" not in terms


# ---------------------------------------------------------------------------
# IMP-2: context expansion
# ---------------------------------------------------------------------------

def test_expand_context_adjacent_sentence():
    """expand_context prepends the adjacent sentence."""
    sentences = [
        "BERT was trained on a large corpus.",
        "It obtains SOTA on eleven tasks.",
        "GPT is another model.",
    ]
    result = expand_context(sentences[1], sentences, passage_idx=1, radius=1)
    assert "BERT was trained" in result
    assert "It obtains" in result


def test_expand_context_no_radius():
    """radius=0 returns the passage unchanged."""
    sentences = ["A sentence.", "Another one."]
    result = expand_context(sentences[0], sentences, passage_idx=0, radius=0)
    assert result == sentences[0]


def test_expand_passage_in_text():
    """expand_passage_in_text finds the passage in the text and expands it."""
    text = "Python 3.12 was released in 2023. It introduces many new features."
    passage = "It introduces many new features"
    result = expand_passage_in_text(passage, text, radius=1)
    assert "Python 3.12 was released" in result
    assert "It introduces" in result


def test_expand_passage_not_in_text():
    """If the passage isn't found in the text, it's returned unchanged."""
    passage = "Something unrelated"
    text = "Python 3.12 was released in 2023."
    result = expand_passage_in_text(passage, text, radius=1)
    assert result == passage
