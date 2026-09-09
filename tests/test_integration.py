"""Integration tests for the full citesure pipeline (tiers 1+2+3)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from citesure.citations import Citation
from citesure.models import Report, Status
from citesure.overlap import verify_citations

FIXTURES = Path(__file__).parent / "fixtures"


# Check if the HF model is available locally
def _has_nli_model():
    """Check if the DeBERTa NLI model is cached locally."""
    paths = [
        Path.home() / ".cache" / "citesure" / "hf" / "models--cross-encoder--nli-deberta-v3-base",
        Path.home() / ".cache" / "citesure" / "models--cross-encoder--nli-deberta-v3-base",
    ]
    return any(p.exists() for p in paths)


requires_nli_model = pytest.mark.skipif(
    not _has_nli_model(),
    reason="NLI model not cached locally (run with CITECHECK_CACHE_DIR=~/.cache/citesure/hf first)"
)


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path, monkeypatch):
    # If the NLI model is cached, point to the cache dir containing it
    model_cache = Path.home() / ".cache" / "citesure" / "hf"
    if (model_cache / "models--cross-encoder--nli-deberta-v3-base").exists():
        monkeypatch.setenv("CITECHECK_CACHE_DIR", str(model_cache))
    else:
        monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    yield


# ---------------------------------------------------------------------------
# Full pipeline: tiers 1+2 with NLI off
# ---------------------------------------------------------------------------


def test_pipeline_all_tiers_without_nli_reaches_tier_2():
    """Full pipeline with NLI off reaches tier 2 for reachable citations."""
    page = FIXTURES / "pages" / "page1.html"
    cits = [
        Citation("1", page.as_uri(), "Python 3.12 was released on October 2, 2023."),
    ]
    report = asyncio.run(verify_citations(cits, use_nli=False))
    v = report.verdicts[0]
    assert v.tier_reached == 2


def test_pipeline_mixed_reachability():
    """Mix of reachable, unreachable, paywalled citations."""
    page = FIXTURES / "pages" / "page1.html"
    cits = [
        Citation("1", page.as_uri(), "Python 3.12 was released."),
        Citation("2", "https://nonexistent-citesure-test.invalid/foo", "Unreachable claim."),
    ]
    report = asyncio.run(verify_citations(cits, use_nli=False))
    by_id = {v.citation_id: v for v in report.verdicts}
    assert by_id["1"].tier_reached == 2
    assert by_id["2"].tier_reached == 1
    assert report.total == 2


def test_pipeline_deterministic():
    """Same input twice produces same output."""
    page = FIXTURES / "pages" / "page1.html"
    cits = [Citation("1", page.as_uri(), "Python 3.12 was released on October 2, 2023.")]
    r1 = asyncio.run(verify_citations(cits, use_nli=False))
    r2 = asyncio.run(verify_citations(cits, use_nli=False))
    assert r1.verdicts[0].status == r2.verdicts[0].status
    assert r1.verdicts[0].score == r2.verdicts[0].score
    assert r1.verdicts[0].tier_reached == r2.verdicts[0].tier_reached


def test_pipeline_unicode_claims():
    """Unicode claims don't crash the pipeline."""
    page = FIXTURES / "pages" / "page1.html"
    cits = [Citation("1", page.as_uri(), "これはテストです")]
    report = asyncio.run(verify_citations(cits, use_nli=False))
    assert report.total == 1


def test_pipeline_100_citations_completes():
    """100 citations complete without error."""
    page = FIXTURES / "pages" / "page1.html"
    cits = [
        Citation(f"c{i}", page.as_uri(), f"Claim number {i} about Python 3.12.")
        for i in range(100)
    ]
    report = asyncio.run(verify_citations(cits, use_nli=False))
    assert report.total == 100
    assert len(report.verdicts) == 100


def test_pipeline_report_math_consistent():
    """Report counts are consistent with verdicts."""
    page = FIXTURES / "pages" / "page1.html"
    cits = [Citation("1", page.as_uri(), "Python 3.12 was released.")]
    report = asyncio.run(verify_citations(cits, use_nli=False))
    assert report.total == len(report.verdicts)
    n_sup = sum(1 for v in report.verdicts if v.status is Status.SUPPORTED)
    assert report.supported == n_sup


# ---------------------------------------------------------------------------
# Full pipeline: tiers 1+2+3 with NLI (requires model)
# ---------------------------------------------------------------------------


@requires_nli_model
def test_pipeline_all_tiers_with_nli_reaches_tier_3():
    """Full pipeline with NLI on reaches tier 3 for reachable citations."""
    page = FIXTURES / "pages" / "page1.html"
    cits = [
        Citation("1", page.as_uri(), "Python 3.12 was released on October 2, 2023."),
    ]
    report = asyncio.run(verify_citations(cits, use_nli=True))
    v = report.verdicts[0]
    assert v.tier_reached == 3
    assert v.status in (Status.SUPPORTED, Status.AMBIGUOUS, Status.UNSUPPORTED)
