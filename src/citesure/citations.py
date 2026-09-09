"""Citation extraction (D1).

Supported input forms:

* **Markdown, numeric markers** — ``The sky is blue [1].`` where ``1``
  resolves through a URL map. The map comes either from an explicit
  ``url_map`` argument or from a trailing ``## Sources`` / ``## References``
  section in the document (numbered list lines such as ``1. https://...`` or
  reference-style definitions such as ``[1]: https://...``).
* **Markdown, inline links** — ``See [the docs](https://docs.example/api)``
  where the URL lives in the link itself. The link label becomes the
  ``citation_id``.
* **Structured JSON** — a list of ``{"claim": ..., "citation": url-or-id}``
  objects, optionally wrapped in ``{"citations": [...]}``. When
  ``citation`` is an id rather than a URL, it is resolved through an
  optional top-level ``"sources"`` (or ``"url_map"``) dict. An optional
  per-item ``"excerpt"`` field (D4) carries a user-supplied passage that
  the overlap tier matches against instead of the fetched page.

Claim unit (D4): the *sentence* containing the marker is the claim text. If
the surrounding paragraph has no sentence boundary (no ``.``/``!``/``?``
followed by whitespace), the whole paragraph is the claim.

Missing-id policy: a numeric marker whose id is absent from the URL map is
**skipped** — no :class:`Citation` is emitted for it. ``extract_citations``
is a pure function with no side channel for warnings, so callers can detect
the gap by comparing the number of markers in the text to the number of
returned citations.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

#: ``[1]`` style numeric marker, but NOT ``[1](url)`` (an inline link).
_NUMERIC_MARKER_RE = re.compile(r"\[(\d+)\](?!\()")

#: ``[label](target)`` inline link. Target: no spaces, no nested parens.
_INLINE_LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\(\s*([^()\s]+)\s*\)")

#: A ``## Sources`` / ``## References`` heading (any level, up to 3 leading spaces).
_SOURCES_HEADING_RE = re.compile(
    r"^\s{0,3}(#{1,6})\s*(sources|references)\b.*$", re.IGNORECASE | re.MULTILINE
)

#: Reference-style definition: ``[1]: https://example.com`` (optional title tail).
_REF_DEF_RE = re.compile(r"^\s*\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s+.*)?$", re.MULTILINE)

#: Numbered list item: ``1. https://...`` or ``2) [Title](https://...)``.
_NUMBERED_ITEM_RE = re.compile(r"^\s*(\d+)[.)]\s+(\S.*)$", re.MULTILINE)

#: An explicit URL token inside a sources line.
_URL_TOKEN_RE = re.compile(r"(?:https?://|file://)[^\s\)\]>\"']+")

#: Paragraph separator: blank line(s).
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")

#: Sentence boundary: sentence-ending punctuation followed by whitespace.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Citation:
    """One claim-to-source pairing extracted from an input document."""

    citation_id: str
    url: str
    claim: str
    #: Optional raw context around the citation in the source document.
    source_text: str | None = None
    #: Optional user-supplied excerpt (D4): when set, the overlap tier
    #: matches the claim against this text instead of the fetched page.
    #: Only the structured JSON input form carries it (``"excerpt"`` field).
    excerpt: str | None = None


# ---------------------------------------------------------------------------
# Sources-section parsing
# ---------------------------------------------------------------------------


def _split_sources_section(text: str) -> tuple[str, dict[str, str]]:
    """Split a markdown document into (body, url_map).

    Everything from a ``## Sources``/``## References`` heading to the next
    heading of the same or higher level (or EOF) is treated as the sources
    block and removed from the body.
    """
    m = _SOURCES_HEADING_RE.search(text)
    if not m:
        return text, {}
    level = len(m.group(1))
    body = text[: m.start()].rstrip()
    rest = text[m.end() :]
    stop = re.search(rf"^\s{{0,3}}#{{{1},{level}}}\s+\S", rest, re.MULTILINE)
    block = rest[: stop.start()] if stop else rest
    return body, _parse_sources_block(block)


def _parse_sources_block(block: str) -> dict[str, str]:
    """Parse a sources block into ``{id: url}``.

    Handles both reference-style definitions (``[1]: url``) and numbered
    list lines (``1. url`` / ``2) [Title](url)`` / ``3. Some words url``).
    """
    url_map: dict[str, str] = {}
    for m in _REF_DEF_RE.finditer(block):
        url_map[m.group(1).strip()] = m.group(2).strip()
    for m in _NUMBERED_ITEM_RE.finditer(block):
        num, rest = m.group(1), m.group(2).strip()
        url = _extract_url_from_line(rest)
        if url:
            url_map.setdefault(num, url)
    return url_map


def _extract_url_from_line(line: str) -> str | None:
    """Pull a URL out of a sources-list line (link, URL token, or bare path)."""
    link = re.search(r"\[[^\]]*\]\(\s*([^()\s]+)\s*\)", line)
    if link:
        return link.group(1)
    tok = _URL_TOKEN_RE.search(line)
    if tok:
        return tok.group(0)
    candidate = line.strip().rstrip(".")
    if candidate and " " not in candidate and not candidate.startswith("#"):
        return candidate
    return None


# ---------------------------------------------------------------------------
# Claim-unit selection (D4)
# ---------------------------------------------------------------------------


def _claim_unit(paragraph: str, pos: int) -> str:
    """Return the sentence containing character offset ``pos``.

    If the paragraph has no sentence boundary, the whole paragraph is the
    claim (D4: "the sentence, or paragraph, if no sentence boundary").
    """
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_BOUNDARY_RE.finditer(paragraph):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(paragraph)))
    for s, e in spans:
        if s <= pos < e:
            seg = paragraph[s:e].strip()
            return seg or paragraph.strip()
    return paragraph.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_citations(
    text: str, url_map: dict[str, str] | None = None
) -> list[Citation]:
    """Extract citations from markdown text.

    Handles numeric ``[n]`` markers (resolved via ``url_map`` or a trailing
    Sources/References section) and inline ``[label](url)`` links. The claim
    for each marker is the sentence (or paragraph) containing it (D4).

    Numeric markers whose id is missing from the URL map are skipped (see
    module docstring for the rationale).
    """
    body, section_map = _split_sources_section(text)
    merged: dict[str, str] = dict(section_map)
    if url_map:
        merged.update({str(k): v for k, v in url_map.items()})

    citations: list[Citation] = []
    for para in _PARAGRAPH_SPLIT_RE.split(body):
        para = para.strip()
        if not para:
            continue
        events: list[tuple[int, str, str, str]] = []  # (pos, kind, payload, label)
        for m in _NUMERIC_MARKER_RE.finditer(para):
            events.append((m.start(), "num", m.group(1), m.group(1)))
        for m in _INLINE_LINK_RE.finditer(para):
            target = m.group(2)
            if target.startswith("#") or target.lower().startswith("mailto:"):
                continue
            events.append((m.start(), "link", target, m.group(1).strip()))
        events.sort(key=lambda e: e[0])
        for pos, kind, payload, label in events:
            if kind == "num":
                url = merged.get(payload)
                if url is None:
                    continue  # missing id in url_map → skip (documented)
                citations.append(
                    Citation(citation_id=payload, url=url, claim=_claim_unit(para, pos))
                )
            else:
                citations.append(
                    Citation(citation_id=label, url=payload, claim=_claim_unit(para, pos))
                )
    return citations


# ---------------------------------------------------------------------------
# JSON input
# ---------------------------------------------------------------------------


def _looks_like_path(value: str) -> bool:
    """True for URLs (any scheme) or filesystem paths (absolute, relative
    with a slash, or ``~``)."""
    v = value.strip()
    if "://" in v:
        return True
    return v.startswith(("/", "./", "../", "~")) or "/" in v


def _citations_from_json(data: Any) -> list[Citation]:
    """Parse structured JSON input into citations (D1)."""
    if isinstance(data, dict):
        if "citations" not in data:
            raise ValueError(
                "JSON input must be a list of {claim, citation} objects or an "
                "object with a 'citations' key"
            )
        items = data["citations"]
        sources = data.get("sources") or data.get("url_map") or {}
        if not isinstance(sources, dict):
            raise ValueError("'sources' must be a mapping of id -> url")
    elif isinstance(data, list):
        items, sources = data, {}
    else:
        raise ValueError("JSON input must be a list or an object")

    out: list[Citation] = []
    if items is None:
        return out
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict) or "claim" not in item or "citation" not in item:
            raise ValueError(
                f"citation #{i}: expected an object with 'claim' and 'citation' keys"
            )
        cid = str(item.get("id") or f"c{i}")
        ref = str(item["citation"]).strip()
        if "{{" in ref or "}}" in ref:
            raise ValueError(
                f"citation #{i} ({cid}): citation '{ref}' contains an "
                "unresolved template placeholder (e.g. {{MOCK}}) — substitute "
                "a concrete URL before verifying"
            )
        if _looks_like_path(ref):
            url = ref
        elif ref in sources:
            url = sources[ref]
        else:
            raise ValueError(
                f"citation #{i} ({cid}): citation '{ref}' is not a URL, not a "
                "path, and there is no matching entry in the 'sources' map"
            )
        out.append(
            Citation(
                citation_id=cid,
                url=url,
                claim=str(item["claim"]),
                source_text=item.get("source_text"),
                excerpt=(str(item["excerpt"]).strip() or None)
                if item.get("excerpt") is not None
                else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


def _anchor(citation: Citation, base: Path) -> Citation:
    """Resolve a bare relative path in ``citation.url`` against ``base``.

    Scheme URLs (http/https/file) and absolute paths are left untouched.
    Anchoring happens in :func:`load_input` so that bundled samples work
    regardless of the current working directory.
    """
    u = citation.url
    if "://" in u or u.startswith(("/", "~")):
        return citation
    return replace(citation, url=str((base / u).resolve()))


def load_input(path: str) -> tuple[list[Citation], dict[str, Any]]:
    """Read a file and auto-detect its format (D1).

    * Structured JSON (list of ``{claim, citation}`` pairs, optionally
      wrapped in ``{"citations": [...]}``) → ``meta["format"] == "json"``.
    * Anything else is treated as markdown with inline citations →
      ``meta["format"] == "markdown"``.

    Returns ``(citations, meta)``. Bare relative paths in citation URLs are
    resolved against the input file's directory.
    """
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    stripped = raw.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if data is not None:
            citations = _citations_from_json(data)
            return [_anchor(c, p.parent) for c in citations], {"format": "json"}
    citations = extract_citations(raw)
    return [_anchor(c, p.parent) for c in citations], {"format": "markdown"}
