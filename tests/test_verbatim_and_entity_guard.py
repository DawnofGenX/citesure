"""Regression guards for the two v4 held-out gap fixes (2026-10-27).

Two independent defects, both measured on `evals/independent_set_v4.json`:

1. **Verbatim demotions (presentation layer).** 7 of 22 `verbatim-supported` cases
   were demoted while the page contained the claim word-for-word: the pipeline
   scored the best-term-coverage sentence (not the claim's sentence) and the
   cross-encoder returned 0.000-0.390. Fix: when the page reproduces the claim as
   one contiguous token run, that span is scored as an additional candidate
   premise. Max-entailment means the fix can only raise a verdict, and a
   negation-flip / entity-swap claim shares no 6-gram with its page by
   construction, so no span exists for those families.

2. **Entity-swap false supports (confident lie).** 5 of 22 swapped-entity claims
   were reported `supported`. Fix: a claim that names a proper noun the fetched
   page never mentions anywhere cannot be supported by it — terminal veto.

Both helpers are pure and unit-tested here; the pipeline tests use a stubbed
encoder so no model download is needed.
"""

from __future__ import annotations

import asyncio

import pytest

from citesure.citations import Citation
from citesure.models import Status
from citesure.overlap import find_verbatim_span, ungrounded_proper_nouns
from citesure.overlap import verify_citations

CLAIM = (
    "This annex also provides examples, additional specifications regarding "
    "normalization of Unicode text, and information about conformance testing for "
    "Unicode normalization forms."
)

PAGE_TEXT = (
    "Revision was 58. This annex describes normalization forms for Unicode text. "
    + CLAIM
    + " When implementations keep strings in a normalized form, they can be assured "
    "that equivalent strings have a unique binary representation."
)

HTML = "<html><body><p>" + PAGE_TEXT + "</p></body></html>"


# ---------------------------------------------------------------------------
# find_verbatim_span
# ---------------------------------------------------------------------------


def test_verbatim_span_found_when_page_states_the_claim():
    span = find_verbatim_span(CLAIM, PAGE_TEXT)
    assert span is not None
    assert span.lower().startswith("this annex also provides")


def test_verbatim_span_ignores_case_and_punctuation_differences():
    claim = CLAIM.lower().replace(",", "")
    assert find_verbatim_span(claim, PAGE_TEXT) is not None


def test_verbatim_span_none_for_paraphrase():
    claim = (
        "The annex supplies extra examples and further detail about Unicode "
        "normalisation, plus conformance-testing information."
    )
    assert find_verbatim_span(claim, PAGE_TEXT) is None


def test_verbatim_span_none_for_entity_swap():
    """A swapped name must NOT produce a span — that is what keeps the negative
    families untouched by this fix."""
    claim = CLAIM.replace("This annex", "This Marlowe")
    assert find_verbatim_span(claim, PAGE_TEXT) is None


def test_verbatim_span_requires_minimum_length():
    assert find_verbatim_span("Revision was 58.", PAGE_TEXT) is None


def test_verbatim_span_requires_near_full_coverage():
    """Half a sentence verbatim is ordinary shared vocabulary, not a quote."""
    half = "This annex also provides examples, additional specifications regarding normalization of text that does not appear anywhere in this page at all."
    assert find_verbatim_span(half, PAGE_TEXT) is None


# ---------------------------------------------------------------------------
# ungrounded_proper_nouns
# ---------------------------------------------------------------------------


def test_ungrounded_entity_detected():
    claim = "Marlowe provides examples and conformance testing information."
    assert "Marlowe" in ungrounded_proper_nouns(claim, PAGE_TEXT)


def test_ungrounded_entity_empty_when_page_names_it():
    claim = "Unicode normalization forms are described in this annex."
    assert ungrounded_proper_nouns(claim, PAGE_TEXT) == []


def test_ungrounded_entity_detected_inside_a_shared_phrase():
    """The swap is often a NAME inside an otherwise shared phrase."""
    claim = CLAIM.replace("Unicode text", "Zenith text")
    assert ungrounded_proper_nouns(claim, PAGE_TEXT) != []


def test_ungrounded_entity_empty_for_empty_page():
    assert ungrounded_proper_nouns("Marlowe said so.", "") == []


# ---------------------------------------------------------------------------
# Pipeline: verbatim span premise rescues a demoted supported claim
# ---------------------------------------------------------------------------


class _PremiseEncoder:
    """Scores every premise low except one that contains ``needle``."""

    def __init__(self, needle: str):
        self.needle = needle
        self.pairs: list[tuple[str, str]] = []

    def predict_all(self, pairs):
        self.pairs = list(pairs)
        return [
            (0.95, 0.01) if self.needle in premise else (0.05, 0.02)
            for _claim, premise in pairs
        ]


def _run(tmp_path, monkeypatch, claim, encoder, use_nli=True):
    page = tmp_path / "page.html"
    page.write_text(HTML, encoding="utf-8")
    cite = Citation("1", str(page), claim)
    if use_nli:
        from citesure import nli as nli_mod

        monkeypatch.setattr(nli_mod, "get_nli_model", lambda model_name=None: encoder)
    return asyncio.run(
        verify_citations([cite], use_overlap=True, use_nli=use_nli, allow_any_local=True)
    )


def test_pipeline_rescues_verbatim_claim_via_span_premise(tmp_path, monkeypatch):
    enc = _PremiseEncoder(needle="This annex also provides examples")
    report = _run(tmp_path, monkeypatch, CLAIM, enc)
    v = report.verdicts[0]
    assert v.status is Status.SUPPORTED
    assert any("verbatim span" in n for n in v.notes)
    # the claim's own sentence really was scored as a premise
    assert any("This annex also provides examples" in premise for _c, premise in enc.pairs)


def test_pipeline_without_span_would_demote(tmp_path, monkeypatch):
    """Control: a paraphrased claim gets no span pair (and no verbatim note)."""
    claim = (
        "The annex supplies extra examples and further detail about Unicode "
        "normalisation, plus conformance-testing information."
    )
    enc = _PremiseEncoder(needle="This annex also provides examples")
    report = _run(tmp_path, monkeypatch, claim, enc)
    assert not any("verbatim span" in n for n in report.verdicts[0].notes)


# ---------------------------------------------------------------------------
# Pipeline: ungrounded-entity veto
# ---------------------------------------------------------------------------


def test_pipeline_vetoes_supported_claim_naming_absent_entity(tmp_path, monkeypatch):
    claim = CLAIM.replace("This annex", "This Marlowe")
    enc = _PremiseEncoder(needle="This annex also provides examples")
    report = _run(tmp_path, monkeypatch, claim, enc)
    v = report.verdicts[0]
    assert v.status is Status.UNSUPPORTED
    assert any("ungrounded entity" in n for n in v.notes)


def test_pipeline_veto_applies_with_nli_off(tmp_path, monkeypatch):
    """The guard is terminal, not part of the NLI tier: tiers 1+2 alone obey it."""
    claim = CLAIM.replace("This annex", "This Marlowe")
    report = _run(tmp_path, monkeypatch, claim, None, use_nli=False)
    v = report.verdicts[0]
    assert v.status is Status.UNSUPPORTED
    assert any("ungrounded entity" in n for n in v.notes)


def test_pipeline_keeps_supported_claim_with_grounded_entities(tmp_path, monkeypatch):
    enc = _PremiseEncoder(needle="This annex also provides examples")
    report = _run(tmp_path, monkeypatch, CLAIM, enc)
    assert report.verdicts[0].status is Status.SUPPORTED
    assert not any("ungrounded entity" in n for n in report.verdicts[0].notes)
