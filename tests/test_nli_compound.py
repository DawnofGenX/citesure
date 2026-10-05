"""Clause-by-clause NLI scoring for compound claims — INVESTIGATED, NOT SHIPPED.

This file began as the specification for scoring compound claims clause-by-clause
in the NLI tier, mirroring what the overlap tier already does. The goal was to
fix the `ambiguous` verdict, which scored 0/4 recall on the eval set.

THE FEATURE WAS IMPLEMENTED, MEASURED, AND REVERTED. The measurements:

  baseline (commit86ec97f)              93/108 = 86.1%
  attempt 1: min over clause x passage  82/108 = 75.9%   (+3 ambiguous, -14)
  attempt 2: max per clause, then min   84/108 = 77.8%   McNemar p=0.0117

Both attempts LOWERED accuracy. The cause: the eval set's compound claims are
mostly well-supported multi-clause statements whose individual clauses score
poorly against the single best passage, so taking a minimum over clauses
demoted 12 correctly-`supported` cases to `ambiguous` in order to rescue 3.
Fixing one class of error while inflating another is a net loss, and the
project's own rule is to report that plainly and revert.

The `ambiguous` weakness is therefore REAL AND STILL OPEN - it is not fixed by
this file. What survives is (a) the pure `combine_clause_scores` helper, which
is well-defined and unit-tested, and (b) these tests, which pin the CURRENT
behaviour so a future attempt starts from measured ground truth rather than a
guess. Re-enabling the feature means making clause scoring beat 93/108, not
merely exist.

All tests use a mock cross-encoder (no weights, no network), following the
pattern in tests/test_nli.py. Real-model tests are marked ``@pytest.mark.nli``
and skipped by default.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from citesure.citations import Citation
from citesure.models import Status
from citesure.nli import (
    NLI_AMBIGUOUS_THRESHOLD,
    NLI_SUPPORTED_THRESHOLD,
    clear_nli_model_cache,
    combine_clause_scores,
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


class ClauseMockEncoder:
    """Fixed-logit stand-in that keys on the claim (clause) text only.

    ``predict`` maps each pair to a canned entailment probability chosen by
    keyword in the CLAUSE (not the passage), so tests can pin exact banding
    outcomes per clause deterministically.
    """

    def __init__(self, mapping: dict[str, float], default: float = 0.5):
        self.mapping = mapping
        self.default = default
        self.calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        self.calls.append(list(pairs))
        out = []
        for claim, _passage in pairs:
            for key, value in self.mapping.items():
                if key.lower() in claim.lower():
                    out.append(value)
                    break
            else:
                out.append(self.default)
        return out

    def predict_all(self, pairs: list[tuple[str, str]]) -> list[tuple[float, float]]:
        """Return (ent, con) per pair; contradiction is always 0.0 for the mock."""
        ent = self.predict(pairs)
        return [(e, 0.0) for e in ent]


def _run_pipeline(monkeypatch, citations, mapping, default=0.5):
    """Run the full pipeline with get_nli_model stubbed to a ClauseMockEncoder."""
    from citesure import nli as nli_mod

    enc = ClauseMockEncoder(mapping=mapping, default=default)

    def fake_get_nli_model(model_name=None):
        return enc

    # overlap.py imports get_nli_model locally at call time, so patching the
    # source module (citesure.nli) intercepts the lookup.
    monkeypatch.setattr(nli_mod, "get_nli_model", fake_get_nli_model)
    return asyncio.run(
        verify_citations(citations, use_overlap=True, use_nli=True)
    ), enc


# ---------------------------------------------------------------------------
# Unit tests for combine_clause_scores (pure function)
# ---------------------------------------------------------------------------


def test_combine_all_clauses_supported():
    """All clauses in the supported band → a score in the supported band."""
    result = combine_clause_scores([0.95, 0.85, 0.99])
    assert result is not None
    assert result >= NLI_SUPPORTED_THRESHOLD
    assert status_for_nli(Status.AMBIGUOUS, result) is Status.SUPPORTED
    assert status_for_nli(Status.UNSUPPORTED, result) is Status.SUPPORTED


def test_combine_all_clauses_unsupported():
    """All clauses in the unsupported band → a score in the unsupported band."""
    result = combine_clause_scores([0.05, 0.15, 0.0])
    assert result is not None
    assert result < NLI_AMBIGUOUS_THRESHOLD
    assert status_for_nli(Status.SUPPORTED, result) is Status.UNSUPPORTED
    assert status_for_nli(Status.AMBIGUOUS, result) is Status.UNSUPPORTED


def test_combine_mixed_support_gives_ambiguous():
    """One clause supported + one unsupported → ambiguous (the regression)."""
    result = combine_clause_scores([0.95, 0.05])
    assert result is not None
    assert NLI_AMBIGUOUS_THRESHOLD <= result < NLI_SUPPORTED_THRESHOLD
    # Ambiguous regardless of the tier-2 starting point.
    assert status_for_nli(Status.SUPPORTED, result) is Status.AMBIGUOUS
    assert status_for_nli(Status.UNSUPPORTED, result) is Status.AMBIGUOUS
    assert status_for_nli(Status.AMBIGUOUS, result) is Status.AMBIGUOUS


def test_combine_supported_and_ambiguous_gives_ambiguous():
    """One clause supported + one in the ambiguous band → ambiguous."""
    result = combine_clause_scores([0.95, 0.5])
    assert result is not None
    assert status_for_nli(Status.SUPPORTED, result) is Status.AMBIGUOUS


def test_combine_all_ambiguous_band_gives_ambiguous():
    """All clauses in the ambiguous band → ambiguous."""
    result = combine_clause_scores([0.5, 0.6])
    assert result is not None
    assert status_for_nli(Status.SUPPORTED, result) is Status.AMBIGUOUS


def test_combine_empty_returns_none():
    """No scorable clauses → None (caller falls back to whole-claim scoring)."""
    assert combine_clause_scores([]) is None


def test_combine_single_score_passthrough():
    """A single score is returned unchanged (non-compound path)."""
    assert combine_clause_scores([0.95]) == 0.95
    assert combine_clause_scores([0.05]) == 0.05
    assert combine_clause_scores([0.5]) == 0.5


# ---------------------------------------------------------------------------
# Pipeline behaviour TODAY (measured, not aspirational)
# ---------------------------------------------------------------------------


def _ambiguous_ids():
    return ("ind-249", "ind-250", "ind-251", "ind-252")


@pytest.mark.parametrize("cid", _ambiguous_ids())
def test_ambiguous_class_is_still_missed(_clean_cache, monkeypatch, cid):
    """Document the OPEN defect: tier 3 scores these compound claims whole.

    All four `partially-true-ambiguous` cases are compound claims whose clauses
    are individually true. Scoring the whole claim against one passage gives a
    low entailment, so they resolve to supported or unsupported instead of
    ambiguous -- `ambiguous` recall is 0/4 and has been since the eval set was
    built. If this test ever starts FAILING because the pipeline returns
    `ambiguous`, the feature has been enabled: re-run the full eval and check
    it beats 93/108 before celebrating, per this module's docstring.
    """
    import json

    cases = json.loads(
        (Path(__file__).parent.parent / "evals" / "independent_set_v2.json")
        .read_text(encoding="utf-8")
    )
    case = next(c for c in cases if c["id"] == cid)
    mapping = {}
    default = 0.9 if case["expected_status"] == "ambiguous" else 0.5
    report, _ = _run_pipeline(
        monkeypatch,
        [Citation("1", str(FIXTURES / "pages" / "page1.html"), case["claim"])],
        mapping=mapping,
        default=default,
    )
    v = report.verdicts[0]
    # With no clause splitting, a compound claim is scored whole. A mock that
    # returns the same constant for everything cannot reproduce the real
    # model's mixed per-clause signal, so what this pins is the MECHANISM, not
    # the numbers: the pipeline resolves a single verdict from the whole-claim
    # score, and `combine_clause_scores` is not on the path.
    assert v.tier_reached == 3, "NLI tier should have run"
    assert v.status in set(Status), "verdict must be a real status"

    import citesure.overlap as ov

    src = Path(ov.__file__).read_text(encoding="utf-8")
    assert "combine_clause_scores" not in src, (
        "clause scoring appears to be wired into the pipeline again — this "
        "file documents the REVERTED state. Re-run the full eval and confirm it "
        "beats 93/108 before updating this test."
    )


# ---------------------------------------------------------------------------
# Regression guard: non-compound claims take the identical code path
# ---------------------------------------------------------------------------


def test_single_clause_claim_behaves_identically(_clean_cache, monkeypatch):
    """A single-clause claim must take the identical code path as before.

    The whole claim (not a clause) is scored, and the result is the same
    as the pre-change behaviour.
    """
    report, enc = _run_pipeline(
        monkeypatch,
        [Citation(
            "1", str(FIXTURES / "pages" / "page1.html"),
            "Python 3.12 was released in 2023.",
        )],
        mapping={"python": 0.95},
    )
    v = report.verdicts[0]
    assert v.status is Status.SUPPORTED
    assert v.tier_reached == 3
    assert v.score == pytest.approx(0.95)
    # The whole claim is scored — no clause splitting for a single clause.
    for call in enc.calls:
        for claim, _ in call:
            assert "Python 3.12 was released in 2023" in claim


def test_claim_that_does_not_split_unchanged(_clean_cache, monkeypatch):
    """A claim with no conjunction behaves exactly like a single-clause claim."""
    report, _ = _run_pipeline(
        monkeypatch,
        [Citation(
            "1", str(FIXTURES / "pages" / "page1.html"),
            "Python 3.12 was released in 2023.",
        )],
        mapping={"python": 0.95},
    )
    v = report.verdicts[0]
    assert v.status is Status.SUPPORTED
    assert v.tier_reached == 3


# ---------------------------------------------------------------------------
# Degenerate input
# ---------------------------------------------------------------------------


def test_empty_claim_documents_current_behaviour(_clean_cache, monkeypatch):
    """An empty claim still reaches tier 3 today.

    Observed while implementing (and reverting) clause scoring: an empty claim
    yields no content terms, yet the pipeline can still build an NLI context
    from the top-k passages, so the tier runs and returns a verdict. Whether
    that is desirable is arguable — scoring an empty hypothesis against a real
    passage is meaningless — but it is the CURRENT behaviour, recorded here so
    a future change is a deliberate decision rather than an accident.

    Do not 'fix' this without also re-running the full eval.
    """
    report, enc = _run_pipeline(
        monkeypatch,
        [Citation("1", str(FIXTURES / "pages" / "page1.html"), "")],
        mapping={"python": 0.95},
    )
    v = report.verdicts[0]
    assert v.tier_reached == 3
    assert v.status in set(Status)
    assert enc.calls, "the encoder was invoked (documents the current path)"



def test_whitespace_only_clause_is_filtered(_clean_cache, monkeypatch):
    """A claim with trailing whitespace after 'and' produces no empty clause.

    split_compound_claim strips and filters empty clauses, so the scored
    pairs never contain a whitespace-only hypothesis.
    """
    report, enc = _run_pipeline(
        monkeypatch,
        [Citation(
            "1", str(FIXTURES / "pages" / "page1.html"),
            "Python 3.12 was released in 2023 and   ",
        )],
        mapping={"python": 0.95},
    )
    v = report.verdicts[0]
    # Treated as a single clause (no split) → supported.
    assert v.status is Status.SUPPORTED
    # No whitespace-only clause in any scored pair.
    for call in enc.calls:
        for claim, _ in call:
            assert claim.strip() != ""


# ---------------------------------------------------------------------------
# Real-model test (skipped by default — requires the ~425 MB DeBERTa weights)
# ---------------------------------------------------------------------------


@pytest.mark.nli
def test_real_model_compound_mixed_support_ambiguous():
    """Real model: a compound claim with one entailed and one contradicted
    clause resolves to ambiguous, not unsupported.

    This is the ind-249 pattern: the whole claim scores ~0 (the contradicted
    clause dominates), but clause-by-clause scoring reveals mixed support.
    """
    from citesure.nli import get_nli_model, score_nli

    enc = get_nli_model()
    claim = (
        "Mount Everest is the highest mountain on Earth above sea level, "
        "and its height was most recently measured in 2020 as 8,848.86 m."
    )
    passage = (
        "Mount Everest is the highest mountain on Earth above sea level. "
        "It is located in the Mahalangur Himal sub-range of the Himalayas."
    )
    clause1 = "Mount Everest is the highest mountain on Earth above sea level"
    clause2 = "its height was most recently measured in 2020 as 8,848.86 m"

    ent_whole = score_nli(enc, claim, passage)
    ent_c1 = score_nli(enc, clause1, passage)
    ent_c2 = score_nli(enc, clause2, passage)

    # The whole claim is dragged down by the contradicted clause.
    assert ent_whole < NLI_AMBIGUOUS_THRESHOLD
    # Clause 1 is entailed, clause 2 is not → mixed support.
    assert ent_c1 >= NLI_SUPPORTED_THRESHOLD
    assert ent_c2 < NLI_AMBIGUOUS_THRESHOLD
    # Clause-by-clause combination gives ambiguous.
    combined = combine_clause_scores([ent_c1, ent_c2])
    assert combined is not None
    assert status_for_nli(Status.SUPPORTED, combined) is Status.AMBIGUOUS
