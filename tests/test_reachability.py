"""Unit tests for the reachability tier (tier 1)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from citesure.citations import Citation, load_input
from citesure.fetcher import FetchedPage
from citesure.models import Report, Status
from citesure.reachability import classify_reachability, verify_citations

FIXTURES = Path(__file__).parent / "fixtures"


def _page(**kwargs) -> FetchedPage:
    base = dict(url="https://example.com/x", ok=True, status_code=200)
    base.update(kwargs)
    return FetchedPage(**base)


# ---------------------------------------------------------------------------
# classify_reachability on synthetic pages
# ---------------------------------------------------------------------------


def test_ok_page_is_supported():
    status, notes = classify_reachability(_page(text="some content"))
    assert status is Status.SUPPORTED
    assert any("no red flags" in n for n in notes)


def test_dns_failure_is_unreachable():
    status, notes = classify_reachability(
        _page(ok=False, error="ConnectError: [Errno -2] Name or service not known")
    )
    assert status is Status.UNREACHABLE
    assert any("Name or service not known" in n for n in notes)


def test_http_404_is_unreachable():
    status, _ = classify_reachability(_page(ok=False, status_code=404, error="HTTP 404"))
    assert status is Status.UNREACHABLE


def test_http_500_is_unreachable():
    status, _ = classify_reachability(_page(ok=False, status_code=500, error="HTTP 500"))
    assert status is Status.UNREACHABLE


def test_timeout_is_unreachable():
    status, notes = classify_reachability(_page(ok=False, error="ReadTimeout: timed out"))
    assert status is Status.UNREACHABLE
    assert any("ReadTimeout" in n for n in notes)


def test_paywall_is_paywalled():
    status, notes = classify_reachability(_page(paywall_detected=True))
    assert status is Status.PAYWALLED
    assert any("paywall" in n.lower() for n in notes)


def test_retraction_is_unsupported_with_note():
    # Documented choice: retraction → unsupported (content available but
    # flagged), not ambiguous. See reachability module docstring.
    status, notes = classify_reachability(_page(retraction_detected=True))
    assert status is Status.UNSUPPORTED
    assert any("retract" in n.lower() for n in notes)


def test_paywall_takes_precedence_over_retraction():
    status, _ = classify_reachability(
        _page(paywall_detected=True, retraction_detected=True)
    )
    assert status is Status.PAYWALLED


def test_robots_blocked_page_is_unreachable_with_note():
    status, notes = classify_reachability(
        _page(ok=False, error="blocked by robots.txt", notes=["disallowed by robots.txt"])
    )
    assert status is Status.UNREACHABLE
    assert any("robots.txt" in n for n in notes)


# ---------------------------------------------------------------------------
# verify_citations end-to-end on the bundled fixture (offline, file:// URLs)
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    citations, meta = load_input(str(FIXTURES / "sample.md"))
    assert meta["format"] == "markdown"
    return asyncio.run(verify_citations(citations))


def test_sample_md_statuses(sample_report: Report):
    by_id = {v.citation_id: v for v in sample_report.verdicts}
    # 3 numeric markers → local fixture pages → supported at tier 1.
    assert by_id["1"].status is Status.SUPPORTED
    assert by_id["2"].status is Status.SUPPORTED
    assert by_id["3"].status is Status.SUPPORTED
    # Inline [source](url) link → same local page → supported.
    assert by_id["the HTTP/2 overview"].status is Status.SUPPORTED
    # Deliberately dead .invalid URL → unreachable.
    assert by_id["4"].status is Status.UNREACHABLE
    assert any(".invalid" in v.url for v in sample_report.verdicts if v.status is Status.UNREACHABLE)


def test_sample_md_report_math(sample_report: Report):
    assert sample_report.total == 5
    assert sample_report.supported == 4
    assert sample_report.unsupported == 0
    assert sample_report.unverifiable == 1
    assert sample_report.pass_rate == pytest.approx(0.8)


def test_verdicts_carry_tier_and_evidence(sample_report: Report):
    for v in sample_report.verdicts:
        assert v.tier_reached == 1
        assert len(v.evidence) <= 300  # D3 cap enforced by Verdict
    ok = next(v for v in sample_report.verdicts if v.status is Status.SUPPORTED)
    assert ok.evidence  # evidence snippet from fetched text
    dead = next(v for v in sample_report.verdicts if v.status is Status.UNREACHABLE)
    assert dead.evidence == ""
    assert dead.notes  # error note recorded


def test_verify_empty_list():
    report = asyncio.run(verify_citations([]))
    assert isinstance(report, Report)
    assert report.total == 0
    assert report.pass_rate == 0.0


def test_verify_single_local_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = FIXTURES / "pages" / "page1.html"
    report = asyncio.run(
        verify_citations([Citation("1", page.as_uri(), "Python 3.12 released.")])
    )
    assert report.total == 1
    assert report.supported == 1
    assert report.pass_rate == 1.0
