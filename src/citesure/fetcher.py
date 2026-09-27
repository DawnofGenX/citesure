"""Async fetcher (D7).

httpx-based fetching with trafilatura HTML→text extraction:

* 5 s timeout per request; 2 retries with exponential backoff (0.5 s, 1.5 s).
* Max 10 concurrent in-flight fetches (``asyncio.Semaphore``).
* Custom User-Agent identifying citesure.
* robots.txt respected via :mod:`urllib.robotparser` — fetched once per
  domain and cached; disallowed URLs are **not** fetched and come back as
  unreachable with a note.
* 24 h disk cache keyed by URL+etag under ``~/.cache/citesure/``
  (override with ``CITECHECK_CACHE_DIR`` for tests).
* ``file://`` URLs and bare local paths read straight from disk — no network.
* No headless browser: JS-rendered pages are out of scope (D8).

Paywall / retraction detection are pure functions on HTML/text strings so
they are trivially unit-testable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse, urlunparse

import httpx
import trafilatura

#: User-Agent identifying citesure (D7).
USER_AGENT = "citesure/0.1.0 (+https://github.com/DawnofGenX/citesure)"

REQUEST_TIMEOUT = 5.0  # seconds per request
MAX_RETRIES = 2  # retries after the first attempt (backoff 0.5s, 1.5s)
RETRY_BACKOFFS = (0.5, 1.5)
MAX_CONCURRENT = 10
CACHE_TTL_SECONDS = 24 * 3600  # 24 h


def _cache_root() -> Path:
    override = os.environ.get("CITECHECK_CACHE_DIR")
    root = Path(override) if override else Path.home() / ".cache" / "citesure"
    return root


# ---------------------------------------------------------------------------
# Heuristics (pure, unit-testable)
# ---------------------------------------------------------------------------

_PAYWALL_PATTERNS = [
    re.compile(r"paywall", re.IGNORECASE),
    re.compile(r"subscribe\s+to\s+(?:continue|read|unlock|access)", re.IGNORECASE),
    re.compile(r"sign\s+in\s+to\s+continue", re.IGNORECASE),
    re.compile(r"log\s?in\s+to\s+(?:continue|read|access|view)", re.IGNORECASE),
    re.compile(r"metered\s+access", re.IGNORECASE),
    re.compile(r"this\s+article\s+requires\s+a\s+subscription", re.IGNORECASE),
    re.compile(r"you've\s+reached\s+your\s+(?:monthly\s+)?limit", re.IGNORECASE),
    re.compile(r"<form[^>]*\baction=[^>]*(?:login|signin|sign-in|auth)[^>]*>", re.IGNORECASE),
    re.compile(r"\bclass=[\"'][^\"']*(?:paywall|login-form|subscription-wall)[^\"']*[\"']", re.IGNORECASE),
]

_RETRACTION_PATTERNS = [
    re.compile(r"retraction\s+notice", re.IGNORECASE),
    re.compile(r"\bretracted\b", re.IGNORECASE),
    re.compile(r"\bretraction:\s", re.IGNORECASE),
    re.compile(r"expressed\s+concerns\s+about\s+the\s+validity", re.IGNORECASE),
]


def detect_paywall(html_or_text: str) -> bool:
    """Heuristic paywall detection on raw HTML or extracted text."""
    if not html_or_text:
        return False
    return any(p.search(html_or_text) for p in _PAYWALL_PATTERNS)


def detect_retraction(html_or_text: str) -> bool:
    """Heuristic retraction detection on raw HTML or extracted text."""
    if not html_or_text:
        return False
    return any(p.search(html_or_text) for p in _RETRACTION_PATTERNS)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class FetchedPage:
    """Outcome of fetching a single URL."""

    url: str
    ok: bool = False
    status_code: int | None = None
    #: Clean text via trafilatura (fallback: raw text).
    text: str = ""
    #: Raw response body (HTML or file contents).
    html: str = ""
    etag: str | None = None
    error: str | None = None
    paywall_detected: bool = False
    retraction_detected: bool = False
    fetched_from_cache: bool = False
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------


def _normalize_url(url: str) -> str:
    """Canonicalize a URL for cache-keying (BUG-5).

    Scheme and host are case-insensitive per RFC 3986; path/query/fragment
    are preserved verbatim. Without this, ``http://X`` and ``HTTP://x`` hash
    to different keys and miss each other's cache entries.
    """
    try:
        p = urlparse(url)
    except ValueError:
        return url
    return urlunparse(
        (p.scheme.lower(), p.netloc.lower(), p.path, p.params, p.query, p.fragment)
    )


def _cache_key(url: str, etag: str | None) -> str:
    digest = hashlib.sha256(
        f"{_normalize_url(url)}|{etag or ''}".encode("utf-8")
    ).hexdigest()[:40]
    return f"{digest}.json"


def _cache_put(page: FetchedPage) -> None:
    """Persist a successfully fetched page (only ok pages are cached)."""
    if not page.ok:
        return
    root = _cache_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "url": page.url,
            "ok": page.ok,
            "status_code": page.status_code,
            "text": page.text,
            "html": page.html,
            "etag": page.etag,
            "error": page.error,
            "paywall_detected": page.paywall_detected,
            "retraction_detected": page.retraction_detected,
            "notes": page.notes,
            "ts": time.time(),
        }
        (root / _cache_key(page.url, page.etag)).write_text(
            json.dumps(payload), encoding="utf-8"
        )
    except OSError:
        pass  # cache is best-effort; never fail a fetch because of it


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

_robots_cache: dict[str, robotparser.RobotFileParser] = {}


async def _robots_allowed(url: str, client: httpx.AsyncClient) -> tuple[bool, str | None]:
    """Check robots.txt for ``url``. Returns (allowed, note).

    The robots.txt for each domain is fetched once and cached in-process.
    If robots.txt itself cannot be fetched, access is allowed (standard
    robot behavior when the policy is unreadable).
    """
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    rp = _robots_cache.get(origin)
    if rp is None:
        rp = robotparser.RobotFileParser()
        robots_url = f"{origin}/robots.txt"
        try:
            resp = await client.get(robots_url, timeout=REQUEST_TIMEOUT)
            if resp.status_code < 400:
                rp.parse(resp.text.splitlines())
            else:
                rp.parse([])  # no policy → allow everything
        except httpx.HTTPError:
            rp.parse([])
        _robots_cache[origin] = rp
    allowed = rp.can_fetch(USER_AGENT, url)
    note = None
    if not allowed:
        note = f"disallowed by robots.txt ({origin}/robots.txt); not fetched"
    return allowed, note


def clear_robots_cache() -> None:
    """Drop the in-process robots.txt cache (used by tests)."""
    _robots_cache.clear()


# ---------------------------------------------------------------------------
# Local files
# ---------------------------------------------------------------------------


def _local_path_for(url: str) -> Path | None:
    """Map a ``file://`` URL or bare local path to a Path, else None."""
    if url.startswith("file://"):
        return Path(urlparse(url).path)
    if "://" in url:
        return None
    # ``expanduser()`` raises ``RuntimeError`` for ``~unknown-user`` paths
    # (no such user). Treat those as "not a local path" rather than crashing.
    # See BUG-6.
    try:
        p = Path(url).expanduser()
    except (RuntimeError, ValueError):
        return None
    return p if p.is_absolute() or p.exists() else None


def _fetch_local(url: str, path: Path) -> FetchedPage:
    """Read a local file as a 'fetched page' (no network)."""
    try:
        raw = path.read_bytes().decode("utf-8", errors="replace")
    except OSError as exc:
        return FetchedPage(
            url=url,
            ok=False,
            error=f"local file not readable: {exc}",
        )
    text = _extract_text(raw)
    return FetchedPage(
        url=url,
        ok=True,
        status_code=200,
        text=text,
        html=raw,
        etag=None,
        paywall_detected=detect_paywall(raw),
        retraction_detected=detect_retraction(raw),
    )


#: One pipe-table row: ``| key | value |`` (trafilatura renders Wikipedia
#: infobox/spec tables as pipe-delimited rows rather than prose).
_TABLE_ROW_RE = re.compile(r"\|\s*([^|\n]+?)\s*\|\s*([^|\n]+?)\s*\|")


def _normalize_table_markup(text: str) -> str:
    """Rewrite pipe-table rows into prose so NLI can read them.

    trafilatura emits Wikipedia infobox rows as ``| Key | Value |`` with no
    sentence structure. A cross-encoder asked whether a natural-language claim
    is entailed by ``| EVA duration | 2 hours, 31 minutes, 40 seconds |``
    scores ~0.001: there is no proposition to match against. Rewriting each
    populated row as ``"Key was Value."`` restores a sentence, but the row
    still carries **no subject**, so the model has nothing to bind the claim's
    entity to. Measured on the same row:

    =======================================  ========
    premise                                  entail
    =======================================  ========
    ``EVA duration was 2 hours, ...``            0.0006
    ``Apollo 11 EVA duration was 2 hours, ...``  0.0437
    ``Apollo 11. EVA duration was 2 hours, ...`` 0.9415
    =======================================  ========

    So the table's subject (its single-cell title row, e.g. ``| Apollo 11 |``)
    is emitted as a standalone sentence *before* the rows that follow it, and
    stays in scope until a new subject row appears. Rows whose value cell is
    empty (spacers/headers) are dropped so no dangling ``"Key was ."`` is
    produced. Prose without pipes is returned unchanged.
    """
    if not text or "|" not in text:
        return text

    out: list[str] = []
    pos = 0
    subject = ""
    # Match a whole pipe-table row (any number of cells), so empty-value
    # spacer rows can be consumed and discarded rather than left as raw pipes.
    row_re = re.compile(r"(?:\|[^\n|]*)+\s*")
    for m in row_re.finditer(text):
        row = m.group()
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        cells = [c for c in cells if c]
        # Skip pure separator rows (---, :::).
        if not cells or all(set(c) <= {"-", ":"} for c in cells):
            pos = m.end()
            continue
        out.append(text[pos : m.start()])
        if len(cells) == 1:
            # Single-cell row = table title / section subject. Emitted as its
            # own sentence so the model can bind the claim's entity to it, and
            # so downstream sentence-level selection can see a boundary.
            subject = cells[0]
            out.append(f"{subject}. ")
        else:
            out.append(f"{cells[0]} was {cells[1]}. ")
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def _extract_text(raw_html: str) -> str:
    """trafilatura HTML→clean text with a raw-text fallback."""
    try:
        extracted = trafilatura.extract(raw_html, include_comments=False)
    except Exception:
        extracted = None
    if extracted:
        return _normalize_table_markup(extracted).strip()
    # Fallback: strip tags crudely so we still have *some* text.
    stripped = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw_html)
    stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------

_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _get_semaphore() -> asyncio.Semaphore:
    """Return the concurrency-limiting semaphore *for the running loop*.

    A single module-global ``asyncio.Semaphore`` is bound to whichever event
    loop first created it; reusing it from a second loop deadlocks (its
    internal futures are tied to the first, now-closed loop). Keying the
    semaphore by the running loop keeps one limiter per loop, so nested or
    parallel ``asyncio.run`` calls (thread pools, repeated CLI invocations,
    MCP tool calls) each get a working limiter. The mapping is a
    ``WeakKeyDictionary`` so closed loops (e.g. one per MCP tool call) do not
    accumulate. See BUG-7.
    """
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(MAX_CONCURRENT)
        _semaphores[loop] = sem
    return sem


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds form) into a delay.

    Only integer-second values are honored; HTTP-date forms and garbage
    fall back to ``None`` (caller uses exponential backoff). Capped at 30 s
    so a hostile header cannot stall a fetch indefinitely. See BUG-4.
    """
    if not value:
        return None
    try:
        secs = float(value.strip())
    except ValueError:
        return None
    if secs < 0:
        return None
    return min(secs, 30.0)


_RETRYABLE_STATUS = {429, 503}


async def _http_get(client: httpx.AsyncClient, url: str) -> FetchedPage:
    """GET ``url`` with retries and exponential backoff (0.5 s, 1.5 s).

    Transient transport errors (timeout, connection reset) and rate-limit /
    service-unavailable responses (429/503) are retried; 429 honors the
    server's ``Retry-After`` header when present (BUG-4). Other HTTP errors
    (404, 403, ...) are returned immediately as failed pages.
    """
    last_error: str | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = await client.get(url, timeout=REQUEST_TIMEOUT)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFFS[attempt])
            continue
        raw = resp.content.decode("utf-8", errors="replace")
        etag = resp.headers.get("etag")
        text = _extract_text(raw)
        page = FetchedPage(
            url=url,
            ok=resp.status_code < 400,
            status_code=resp.status_code,
            text=text,
            html=raw,
            etag=etag,
            error=None if resp.status_code < 400 else f"HTTP {resp.status_code}",
            paywall_detected=detect_paywall(raw),
            retraction_detected=detect_retraction(raw),
        )
        if resp.status_code in _RETRYABLE_STATUS and attempt < MAX_RETRIES:
            delay = _parse_retry_after(resp.headers.get("retry-after"))
            if delay is None:
                delay = RETRY_BACKOFFS[attempt]
            last_error = f"HTTP {resp.status_code} (retried)"
            await asyncio.sleep(delay)
            continue
        return page
    return FetchedPage(url=url, ok=False, error=last_error)


async def fetch(url: str) -> FetchedPage:
    """Fetch a URL and return a :class:`FetchedPage`.

    * ``file://`` URLs and bare local paths are read from disk (no network).
    * HTTP(S) URLs go through the shared async client with robots.txt
      checking, retries, concurrency limiting, and the 24 h disk cache.

    The disk cache is consulted for *all* URL kinds (including local files)
    so repeated runs stay fast and offline-friendly.

    Malformed URLs (NUL bytes, broken IPv6 literals, ...) are rejected up
    front as a failed :class:`FetchedPage` instead of escaping as
    ``ValueError`` / ``httpx.InvalidURL``. See BUG-1 / BUG-2.
    """
    if not isinstance(url, str) or "\x00" in url:
        return FetchedPage(
            url=url if isinstance(url, str) else repr(url),
            ok=False,
            error="invalid URL: NUL byte or non-string input",
        )
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return FetchedPage(url=url, ok=False, error=f"invalid URL: {exc}")
    # urlparse accepts most garbage, but httpx rejects e.g. IPv6 literals
    # missing brackets or ports that are not numeric. Validate early so the
    # failure is a clean FetchedPage, not an exception out of fetch().
    try:
        httpx.URL(url)
    except httpx.InvalidURL as exc:
        return FetchedPage(url=url, ok=False, error=f"invalid URL: {exc}")
    if parsed.scheme not in ("", "file", "http", "https"):
        return FetchedPage(
            url=url,
            ok=False,
            error=f"unsupported URL scheme: {parsed.scheme!r}",
        )

    cached = _cache_lookup_by_url(url)
    if cached is not None:
        return cached

    local = _local_path_for(url)
    if local is not None:
        page = await asyncio.to_thread(_fetch_local, url, local)
        if page.ok:
            _cache_put(page)
        return page

    async with _get_semaphore():
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            allowed, note = await _robots_allowed(url, client)
            if not allowed:
                return FetchedPage(
                    url=url,
                    ok=False,
                    error="blocked by robots.txt",
                    notes=[note or "disallowed by robots.txt"],
                )
            page = await _http_get(client, url)
    if page.ok:
        _cache_put(page)
    return page


def _cache_lookup_by_url(url: str) -> FetchedPage | None:
    """Find a fresh cache entry for ``url`` regardless of its etag."""
    root = _cache_root()
    if not root.is_dir():
        return None
    now = time.time()
    norm_url = _normalize_url(url)
    for entry in root.glob("*.json"):
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if _normalize_url(data.get("url", "")) != norm_url:
            continue
        if now - float(data.get("ts", 0)) > CACHE_TTL_SECONDS:
            continue
        return FetchedPage(
            url=data["url"],
            ok=data["ok"],
            status_code=data.get("status_code"),
            text=data.get("text", ""),
            html=data.get("html", ""),
            etag=data.get("etag"),
            error=data.get("error"),
            paywall_detected=data.get("paywall_detected", False),
            retraction_detected=data.get("retraction_detected", False),
            fetched_from_cache=True,
            notes=list(data.get("notes", [])),
        )
    return None
