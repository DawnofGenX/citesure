"""Phase C1: hypothesis fuzzing of the fetcher's URL handling (D7).

Invariants (per brief):
* URL normalization/validation helpers NEVER raise on arbitrary input;
* ``javascript:`` / ``data:`` / ``file://`` are handled sanely (no crash;
  non-http schemes come back as clean failures, not exceptions);
* unicode / IDN domains do not crash the pure helpers.

``fetch()`` itself is only exercised on a *bounded* set of offline-safe
URLs (file:// + .invalid TLDs that fail fast at DNS) — never on external
network hosts. max_examples=300 per fuzz test as specified.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from citesure.fetcher import _cache_key, _local_path_for, fetch

_SUPPRESS = list(HealthCheck)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

SCHEMES = ["http://", "https://", "ftp://", "file://", "javascript:", "data:", ""]
HOSTS = [
    "example.com",
    "ex\u00e4mple.com",          # punycode-able IDN
    "\u5317\u4eac.example",      # CJK IDN
    "[::1]",                     # IPv6 literal
    "127.0.0.1",
    "",                          # empty host
]
PATH_CHARS = st.sampled_from(list("abcXYZ019/._~:?#[]@!$&'()*+,;=%\u00e9\u4e2d"))


def urlish() -> st.SearchStrategy[str]:
    """Arbitrary URL-ish strings: scheme + host + junk path."""
    return st.builds(
        lambda scheme, host, path: f"{scheme}{host}{path}",
        st.sampled_from(SCHEMES),
        st.sampled_from(HOSTS),
        st.lists(PATH_CHARS, min_size=0, max_size=30).map("".join),
    )


ARBITRARY = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)), max_size=120
)


# ---------------------------------------------------------------------------
# Pure helpers: must never raise
# ---------------------------------------------------------------------------


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(ARBITRARY.filter(lambda s: not s.startswith("~")))
def test_local_path_for_never_raises(text: str):
    out = _local_path_for(text)
    assert out is None or isinstance(out, type(_local_path_for("/tmp")))


def test_local_path_for_tilde_unknown_user_does_not_raise():
    """'~user' paths for nonexistent users must return None, not raise."""
    assert _local_path_for("~0") is None


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(urlish(), st.one_of(st.none(), st.text(max_size=40)))
def test_cache_key_never_raises_and_is_stable(url: str, etag):
    k1 = _cache_key(url, etag)
    k2 = _cache_key(url, etag)
    assert k1 == k2  # deterministic
    assert re.fullmatch(r"[0-9a-f]{40}\.json", k1), k1


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=80))
def test_cache_key_handles_unicode_and_idn(text: str):
    """Unicode/IDN-ish strings must not crash key derivation."""
    k = _cache_key(text, None)
    assert re.fullmatch(r"[0-9a-f]{40}\.json", k)


# ---------------------------------------------------------------------------
# Scheme handling via fetch() — bounded, offline-safe inputs only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "javascript:void(0)",
        "data:text/html,<b>x</b>",
        "data:text/plain,hello",
        "notaurl",
        "",
    ],
)
def test_non_http_schemes_fail_cleanly(monkeypatch, tmp_path, url: str):
    """Non-http(s) schemes must return a failed FetchedPage, never raise."""
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = asyncio.run(fetch(url))
    assert page.ok is False
    assert page.error is not None


def test_file_scheme_missing_path_fails_cleanly(monkeypatch, tmp_path):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = asyncio.run(fetch(f"file://{tmp_path}/no-such-file-xyz.html"))
    assert page.ok is False
    assert "not readable" in (page.error or "")


def test_unicode_idn_domain_does_not_crash(monkeypatch, tmp_path):
    """IDN domains fail fast at DNS (.invalid TLD) — no crash, no hang."""
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = asyncio.run(fetch("https://ex\u00e4mple.invalid/\u00fcnicode"))
    assert page.ok is False
    assert page.error is not None


def test_url_with_fragment_and_query_still_fetches_local(monkeypatch, tmp_path):
    """Fragments/queries on file:// URLs are ignored sanely (path part used)."""
    f = tmp_path / "p.html"
    f.write_text("<html><body><p>fragment target</p></body></html>", encoding="utf-8")
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = asyncio.run(fetch(f"{f.as_uri()}#section-2"))
    assert page.ok is True
    assert "fragment target" in page.text


# ---------------------------------------------------------------------------
# BUG-1 / BUG-2: malformed URLs must return a failed FetchedPage, not raise (regression)
# ---------------------------------------------------------------------------


def test_fetch_malformed_ipv6_url_returns_failed_page(monkeypatch, tmp_path):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = asyncio.run(fetch("http://[::1"))  # unclosed IPv6 literal
    assert page.ok is False
    assert page.error is not None


def test_fetch_null_byte_url_returns_failed_page(monkeypatch, tmp_path):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = asyncio.run(fetch("a\x00b"))
    assert page.ok is False
    assert page.error is not None
