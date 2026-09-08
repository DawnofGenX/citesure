"""Reachability tier (tier 1 of D2).

Maps fetch outcomes to D3 statuses and drives end-to-end verification of a
list of citations.

Status mapping (documented choices):

* DNS failure / connection error / timeout / HTTP 4xx–5xx → ``unreachable``.
* Paywall detected → ``paywalled`` (checked before retraction so a paywalled
  retraction notice is reported as paywalled — we never saw the article).
* Retraction detected → **``unsupported``** with a note. Rationale: the page
  was fetched successfully and its content is *available* to us; that content
  says the cited work has been retracted, which is a red flag against the
  claim rather than an inability to verify it. We therefore report
  ``unsupported`` (the claim is not supported by a valid source) with an
  explicit retraction note, instead of ``ambiguous``.
* Otherwise → ``supported``. At tier 1 this only means "reachable with no
  red flags"; content-overlap and NLI tiers (Phases 2–3) refine this.
"""

from __future__ import annotations

import asyncio

from .citations import Citation
from .fetcher import FetchedPage, fetch
from .models import Report, Status, Verdict


def classify_reachability(page: FetchedPage) -> tuple[Status, list[str]]:
    """Map a :class:`FetchedPage` to ``(status, notes)`` per D3.

    See the module docstring for the full mapping and the documented choice
    of ``unsupported`` (not ``ambiguous``) for retracted pages.
    """
    notes: list[str] = []

    if not page.ok:
        if page.error:
            notes.append(page.error)
        notes.extend(page.notes)
        return Status.UNREACHABLE, notes

    if page.paywall_detected:
        notes.append("paywall/login wall detected on the cited page")
        return Status.PAYWALLED, notes

    if page.retraction_detected:
        notes.append(
            "retraction marker detected on the cited page — the source is "
            "flagged as retracted, so the claim cannot be treated as supported"
        )
        return Status.UNSUPPORTED, notes

    if page.text:
        notes.append("page fetched; reachable with no red flags (tier 1)")
    else:
        notes.append("page fetched but no extractable text (tier 1 only)")
    return Status.SUPPORTED, notes


async def verify_citations(citations: list[Citation]) -> Report:
    """Verify citations at tier 1 (reachability) and build a :class:`Report`.

    Each citation is fetched concurrently (bounded by the fetcher's
    semaphore), classified via :func:`classify_reachability`, and turned into
    a :class:`Verdict` with ``tier_reached=1`` and evidence clipped from the
    fetched text (≤300 chars, handled by :class:`Verdict`).
    """
    pages = await asyncio.gather(*(fetch(c.url) for c in citations))
    verdicts: list[Verdict] = []
    for citation, page in zip(citations, pages):
        status, notes = classify_reachability(page)
        verdicts.append(
            Verdict(
                citation_id=citation.citation_id,
                url=citation.url,
                status=status,
                tier_reached=1,
                score=None,
                evidence=page.text or "",
                notes=notes,
            )
        )
    return Report.from_verdicts(verdicts)
