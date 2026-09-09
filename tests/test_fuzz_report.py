"""Phase C1: hypothesis fuzzing of the report renderers (src/citesure/report.py).

Invariants (per brief):
* renderers NEVER raise on arbitrary verdict lists/dicts;
* JSON output is always serializable;
* summary counts are consistent with the verdicts.

Fully offline. max_examples=300 per test as specified.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from citesure.models import Report, Status, Verdict
from citesure.report import exit_code, render_human, render_markdown

_SUPPRESS = list(HealthCheck)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

STATUS = st.sampled_from(list(Status))
SCORE = st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False))
TEXT_FIELD = st.text(max_size=200)


def verdict_strategy() -> st.SearchStrategy[Verdict]:
    return st.builds(
        Verdict,
        citation_id=TEXT_FIELD,
        url=TEXT_FIELD,
        status=STATUS,
        tier_reached=st.integers(min_value=1, max_value=3),
        score=SCORE,
        evidence=TEXT_FIELD,
        notes=st.lists(TEXT_FIELD, max_size=5),
    )


VERDICTS = st.lists(verdict_strategy(), min_size=0, max_size=30)
META = st.dictionaries(st.text(max_size=20), st.text(max_size=40), max_size=5)
THRESHOLD = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
STRICT = st.booleans()


def _assert_summary_consistent(report: Report, text: str) -> None:
    """The rendered summary block must agree with the verdict list."""
    counts = {s: 0 for s in Status}
    for v in report.verdicts:
        counts[v.status] += 1
    assert f"- total: {report.total}" in text
    assert f"- supported: {counts[Status.SUPPORTED]}" in text
    assert f"- unsupported: {counts[Status.UNSUPPORTED]}" in text
    assert f"- unreachable: {counts[Status.UNREACHABLE]}" in text
    assert f"- paywalled: {counts[Status.PAYWALLED]}" in text
    assert f"- ambiguous: {counts[Status.AMBIGUOUS]}" in text
    assert f"- unverifiable (unreachable + paywalled + ambiguous): {report.unverifiable}" in text
    # pass_rate line present and consistent with supported/total
    expected_rate = round(counts[Status.SUPPORTED] / report.total, 4) if report.total else 0.0
    assert f"pass_rate: {expected_rate:.4f}" in text


# ---------------------------------------------------------------------------
# render_human / render_markdown
# ---------------------------------------------------------------------------


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(VERDICTS, META, THRESHOLD, STRICT)
def test_render_human_never_raises(verdicts, meta, threshold, strict):
    report = Report.from_verdicts(verdicts)
    out = render_human(report, meta, threshold, strict)
    assert isinstance(out, str) and out
    _assert_summary_consistent(report, out)


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(VERDICTS, META, THRESHOLD, STRICT)
def test_render_markdown_matches_human(verdicts, meta, threshold, strict):
    report = Report.from_verdicts(verdicts)
    a = render_markdown(report, meta, threshold, strict)
    b = render_human(report, meta, threshold, strict)
    assert a == b  # documented: same content


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(VERDICTS, META, THRESHOLD, STRICT)
def test_render_with_nli_model_header(verdicts, meta, threshold, strict):
    report = Report.from_verdicts(verdicts)
    out = render_human(report, meta, threshold, strict, nli_model="cross-encoder/x")
    assert "NLI: cross-encoder/x" in out


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(VERDICTS, THRESHOLD, STRICT)
def test_exit_code_never_raises_and_is_0_or_1(verdicts, threshold, strict):
    report = Report.from_verdicts(verdicts)
    rc = exit_code(report, threshold, strict)
    assert rc in (0, 1)
    # strict can only be stricter than default
    if exit_code(report, threshold, False) == 1:
        assert rc == 1


# ---------------------------------------------------------------------------
# JSON serializability (Report.to_dict — what --json / MCP emit)
# ---------------------------------------------------------------------------


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(VERDICTS)
def test_report_to_dict_always_json_serializable(verdicts):
    report = Report.from_verdicts(verdicts)
    blob = json.dumps(report.to_dict())  # must not raise
    back = json.loads(blob)
    assert back["total"] == len(verdicts)
    assert len(back["verdicts"]) == len(verdicts)
    for v in back["verdicts"]:
        assert v["status"] in {s.value for s in Status}


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(VERDICTS)
def test_report_counts_consistent_with_verdicts(verdicts):
    report = Report.from_verdicts(verdicts)
    n_sup = sum(1 for v in verdicts if v.status is Status.SUPPORTED)
    n_unsup = sum(1 for v in verdicts if v.status is Status.UNSUPPORTED)
    assert report.total == len(verdicts)
    assert report.supported == n_sup
    assert report.unsupported == n_unsup
    assert report.unverifiable == len(verdicts) - n_sup - n_unsup
    expected = round(n_sup / len(verdicts), 4) if verdicts else 0.0
    assert report.pass_rate == expected


# ---------------------------------------------------------------------------
# BUG-3: non-numeric score must not crash the renderer (regression)
# ---------------------------------------------------------------------------


def test_render_human_survives_non_numeric_score():
    """A string score (e.g. from a hand-built or foreign JSON payload) must not
    crash the renderer; it should be coerced or skipped."""
    v = Verdict(
        citation_id="a", url="u", status=Status.SUPPORTED, tier_reached=1, score=None
    )
    v.score = "abc"  # type: ignore[assignment]  # BUG-3 repro: foreign/str score
    report = Report.from_verdicts([v])
    out = render_human(report, {"format": "markdown"}, 0.8, False)
    assert isinstance(out, str)
