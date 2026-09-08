"""Live-network smoke test (D9).

Skipped by default (``pyproject.toml`` addopts deselects ``-m live``); run
explicitly with ``pytest -q -m live``. Hits 2–3 very stable public URLs to
confirm the fetcher works end-to-end against the real internet — reachability,
HTTP status, and trafilatura text extraction. These are deliberately boring,
long-lived pages (no paywall, no retraction, no JS-only rendering) so the test
is stable over time.

This is the *only* test in the suite that touches the network; everything else
runs offline against the frozen fixture corpus (D9).
"""

from __future__ import annotations

import asyncio

import pytest

from citesure.fetcher import fetch

# Deliberately stable, long-lived, non-paywalled, non-JS pages.
LIVE_URLS = [
    "https://example.com/",
    "https://www.python.org/",
    "https://en.wikipedia.org/wiki/Python_(programming_language)",
]


@pytest.mark.live
def test_live_fetch_stable_urls(tmp_path, monkeypatch):
    """Each stable URL resolves, returns HTTP 200, and yields extracted text."""
    # Isolate the disk cache so the run neither reads nor writes the user's
    # real ~/.cache/citesure.
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))

    async def run():
        return await asyncio.gather(*(fetch(u) for u in LIVE_URLS))

    pages = asyncio.run(run())

    assert len(pages) == len(LIVE_URLS)
    for url, page in zip(LIVE_URLS, pages):
        assert page.ok, f"{url} did not fetch ok: {page.error}"
        assert page.status_code == 200, f"{url} status {page.status_code}"
        # trafilatura should extract some readable body text from each page.
        assert page.text.strip(), f"{url} yielded no extracted text"
        # None of these stable pages should trip the red-flag heuristics.
        assert not page.paywall_detected, f"{url} flagged as paywalled"
        assert not page.retraction_detected, f"{url} flagged as retracted"
