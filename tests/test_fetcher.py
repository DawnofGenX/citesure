"""Unit tests for the fetcher (D7) — fully offline."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib import robotparser

import pytest

from citesure.fetcher import (
    FetchedPage,
    _cache_root,
    _robots_allowed,
    clear_robots_cache,
    detect_paywall,
    detect_retraction,
    fetch,
)

FIXTURES = Path(__file__).parent / "fixtures"
PAGE1 = FIXTURES / "pages" / "page1.html"


@pytest.fixture(autouse=True)
def _clean_robots():
    clear_robots_cache()
    yield
    clear_robots_cache()


# ---------------------------------------------------------------------------
# Local files (file:// and bare paths)
# ---------------------------------------------------------------------------


def test_fetch_file_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = asyncio.run(fetch(PAGE1.as_uri()))
    assert page.ok is True
    assert page.status_code == 200
    assert "Python 3.12.0 was released on October 2, 2023" in page.text
    assert page.html.startswith("<!DOCTYPE html>")
    assert page.error is None
    assert page.paywall_detected is False
    assert page.retraction_detected is False


def test_fetch_bare_local_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = asyncio.run(fetch(str(PAGE1)))
    assert page.ok is True
    assert "multiplexes" not in page.text  # page1 content, not page2
    assert "interactive debugger" in page.text


def test_fetch_missing_local_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    missing = tmp_path / "nope.html"
    page = asyncio.run(fetch(missing.as_uri()))
    assert page.ok is False
    assert page.error is not None
    assert "not readable" in page.error


def test_extract_text_fallback_strips_tags():
    # A page trafilatura cannot parse still yields raw text via fallback.
    page = asyncio.run(fetch(str(FIXTURES / "pages" / "page2.html")))
    assert "HTTP/2" in page.text


# ---------------------------------------------------------------------------
# Paywall / retraction heuristics (pure functions)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "html",
    [
        "<div class='paywall'>Content hidden</div>",
        "Subscribe to continue reading the full article.",
        "Sign in to continue",
        "You've reached your monthly limit of free articles.",
        '<form action="/login" method="post"><input name="password"></form>',
        "This article requires a subscription.",
        "metered access applies after 5 articles",
    ],
)
def test_detect_paywall_positive(html: str):
    assert detect_paywall(html) is True


@pytest.mark.parametrize(
    "html",
    [
        "The quick brown fox jumps over the lazy dog.",
        "<p>Python 3.12 was released on October 2, 2023.</p>",
        "",
        None,
    ],
)
def test_detect_paywall_negative(html):
    assert detect_paywall(html) is False


@pytest.mark.parametrize(
    "html",
    [
        "<h2>Retraction Notice</h2><p>This article has been retracted.</p>",
        "RETRACTED: The findings below are no longer valid.",
        "Retraction: The authors express concerns about the validity.",
    ],
)
def test_detect_retraction_positive(html: str):
    assert detect_retraction(html) is True


@pytest.mark.parametrize(
    "html",
    [
        "A regular news article about databases.",
        "<p>The storage engine was updated.</p>",
        "",
    ],
)
def test_detect_retraction_negative(html: str):
    assert detect_retraction(html) is False


def test_heuristics_run_on_fetched_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paywalled = tmp_path / "paywalled.html"
    paywalled.write_text(
        "<html><body><h1>Article</h1>"
        "<div class='paywall'>Subscribe to continue</div></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = asyncio.run(fetch(paywalled.as_uri()))
    assert page.ok is True
    assert page.paywall_detected is True
    assert page.retraction_detected is False


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------


def test_second_fetch_hits_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(cache_dir))
    url = PAGE1.as_uri()

    first = asyncio.run(fetch(url))
    assert first.fetched_from_cache is False
    assert cache_dir.is_dir()
    entries = list(cache_dir.glob("*.json"))
    assert len(entries) == 1
    stored = json.loads(entries[0].read_text(encoding="utf-8"))
    assert stored["url"] == url
    assert stored["ok"] is True

    second = asyncio.run(fetch(url))
    assert second.fetched_from_cache is True
    assert second.ok is True
    assert second.text == first.text


def test_failed_fetch_is_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(cache_dir))
    missing = tmp_path / "missing.html"

    first = asyncio.run(fetch(missing.as_uri()))
    assert first.ok is False
    entries = list(cache_dir.glob("*.json")) if cache_dir.exists() else []
    assert entries == []

    second = asyncio.run(fetch(missing.as_uri()))
    assert second.fetched_from_cache is False
    assert second.ok is False


def test_expired_cache_entry_is_refetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import time as _time

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    url = PAGE1.as_uri()
    # Seed a stale entry (ts far in the past).
    stale = {
        "url": url,
        "ok": True,
        "status_code": 200,
        "text": "stale text",
        "html": "<html></html>",
        "etag": None,
        "error": None,
        "paywall_detected": False,
        "retraction_detected": False,
        "notes": [],
        "ts": _time.time() - 48 * 3600,  # older than the 24 h TTL
    }
    (cache_dir / "stale.json").write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(cache_dir))

    page = asyncio.run(fetch(url))
    assert page.fetched_from_cache is False
    assert "stale text" not in page.text


def test_cache_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "custom"))
    assert _cache_root() == tmp_path / "custom"


# ---------------------------------------------------------------------------
# robots.txt logic (fake robots string — no network)
# ---------------------------------------------------------------------------


def test_robotparser_disallow_logic_with_fake_robots_string():
    rp = robotparser.RobotFileParser()
    rp.parse(
        [
            "User-agent: *",
            "Disallow: /private/",
        ]
    )
    base = "https://example.com"
    ua = "citesure/0.1.0 (+https://github.com/DawnofGenX/citesure)"
    assert rp.can_fetch(ua, f"{base}/public") is True
    assert rp.can_fetch(ua, f"{base}/private/secret") is False
    # Prefix match: "/private" (no trailing slash) is NOT under "/private/".
    assert rp.can_fetch(ua, f"{base}/private") is True


class _FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    """Minimal stand-in for httpx.AsyncClient serving canned robots.txt."""

    def __init__(self, robots_text: str | None, robots_status: int = 200):
        self._robots_text = robots_text
        self._robots_status = robots_status
        self.requests: list[str] = []

    async def get(self, url: str, timeout=None):
        self.requests.append(url)
        return _FakeResponse(self._robots_status, self._robots_text or "")


async def _run_robots_check(client, url: str):
    return await _robots_allowed(url, client)


def test_robots_disallowed_returns_note_without_fetching_target():
    client = _FakeAsyncClient("User-agent: *\nDisallow: /blocked/\n")
    allowed, note = asyncio.run(_run_robots_check(client, "https://example.com/blocked/page"))
    assert allowed is False
    assert note is not None and "robots.txt" in note
    # Only robots.txt was requested; the target itself was never fetched.
    assert client.requests == ["https://example.com/robots.txt"]


def test_robots_allowed_when_no_policy():
    client = _FakeAsyncClient(None, robots_status=404)
    allowed, note = asyncio.run(_run_robots_check(client, "https://example.com/anything"))
    assert allowed is True
    assert note is None


def test_robots_fetched_once_per_domain():
    client = _FakeAsyncClient("User-agent: *\nDisallow: /\n")
    asyncio.run(_run_robots_check(client, "https://example.com/a"))
    asyncio.run(_run_robots_check(client, "https://example.com/b"))
    assert client.requests.count("https://example.com/robots.txt") == 1


def test_robots_unreadable_allows_access():
    class _BrokenClient(_FakeAsyncClient):
        async def get(self, url: str, timeout=None):
            import httpx as _httpx

            raise _httpx.ConnectError("boom")

    allowed, note = asyncio.run(_run_robots_check(_BrokenClient(""), "https://example.com/x"))
    assert allowed is True
    assert note is None


# ---------------------------------------------------------------------------
# FetchedPage shape
# ---------------------------------------------------------------------------


def test_fetched_page_defaults():
    p = FetchedPage(url="https://e.example")
    assert p.ok is False
    assert p.status_code is None
    assert p.text == ""
    assert p.html == ""
    assert p.etag is None
    assert p.error is None
    assert p.paywall_detected is False
    assert p.retraction_detected is False
    assert p.fetched_from_cache is False
    assert p.notes == []
