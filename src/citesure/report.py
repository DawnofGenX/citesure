"""Report rendering (Phase 2).

Human-readable (Markdown-style) rendering of a :class:`citesure.models.Report`
plus the exit-code rule for ``citesure verify``.

Exit-code semantics (documented in ``--help``):

* default mode — exit ``0`` iff ``pass_rate >= --threshold`` (D3), else ``1``.
* ``--strict`` (CI mode) — ambiguous verdicts count as explicit failures:
  exit ``0`` iff ``pass_rate >= --threshold`` AND there are no ``ambiguous``
  AND no ``unsupported`` verdicts; else ``1``. (``pass_rate`` itself never
  counts ambiguous in the numerator, D3.)

Input errors (unreadable file, malformed JSON) always exit ``2``.
"""

from __future__ import annotations

from .models import Report, Status, Verdict

_STATUS_SYMBOLS = {
    Status.SUPPORTED: "✅",
    Status.UNSUPPORTED: "❌",
    Status.UNREACHABLE: "⛔",
    Status.PAYWALLED: "🔒",
    Status.AMBIGUOUS: "❓",
}


def _verdict_label(verdict: Verdict, strict: bool) -> str:
    """Label a verdict for the human-readable report.

    ``--strict`` relabels ``ambiguous`` from UNVERIFIABLE to an explicit
    failure (see module docstring for the exit-code semantics).
    """
    if verdict.status is Status.SUPPORTED:
        return "PASS"
    if verdict.status is Status.UNSUPPORTED:
        return "FAIL"
    if verdict.status is Status.AMBIGUOUS and strict:
        return "FAIL (strict)"
    return "UNVERIFIABLE"


def render_human(
    report: Report, meta: dict, threshold: float, strict: bool,
    nli_model: str | None = None,
) -> str:
    """Render the human-readable Markdown-style report to a string.

    Per-citation lines carry a status symbol, label, url, overlap score
    (tier 2) and an evidence snippet; a summary block closes with totals,
    per-status counts and the pass/decision line. When ``nli_model`` is
    given (the NLI tier is on), the header shows which cross-encoder was
    used (D6).
    """
    lines: list[str] = [
        f"# citesure verification report — {report.total} citation(s) "
        f"(input format: {meta.get('format', 'unknown')})"
    ]
    if nli_model:
        lines.append(f"NLI: {nli_model}")
    lines.append("")
    for v in report.verdicts:
        symbol = _STATUS_SYMBOLS.get(v.status, "•")
        # A foreign/hand-built payload may carry a non-numeric score; never
        # let that crash rendering (BUG-3). Coerce to float; skip on failure.
        score_part = ""
        if v.score is not None:
            try:
                score_part = f" score={float(v.score):.3f}"
            except (TypeError, ValueError):
                score_part = ""
        tier_part = f" tier={v.tier_reached}"
        lines.append(
            f"- {symbol} **[{v.citation_id}]** {_verdict_label(v, strict)}"
            f"{score_part}{tier_part} — {v.url}"
        )
        if v.evidence:
            lines.append(f"  - evidence: {v.evidence}")
        for note in v.notes:
            lines.append(f"  - note: {note}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    counts = {s: 0 for s in Status}
    for v in report.verdicts:
        counts[v.status] += 1
    lines.append(
        f"- total: {report.total}\n"
        f"- supported: {counts[Status.SUPPORTED]}\n"
        f"- unsupported: {counts[Status.UNSUPPORTED]}\n"
        f"- unreachable: {counts[Status.UNREACHABLE]}\n"
        f"- paywalled: {counts[Status.PAYWALLED]}\n"
        f"- ambiguous: {counts[Status.AMBIGUOUS]}\n"
        f"- unverifiable (unreachable + paywalled + ambiguous): {report.unverifiable}\n"
        f"- pass_rate: {report.pass_rate:.4f}"
    )
    decision = "PASS" if report.pass_rate >= threshold else "FAIL"
    lines.append(
        f"\n**Decision: {decision}** (pass_rate {report.pass_rate:.4f} "
        f"{'>=' if report.pass_rate >= threshold else '<'} threshold {threshold:g})"
    )
    return "\n".join(lines)


def render_markdown(
    report: Report, meta: dict, threshold: float, strict: bool,
    nli_model: str | None = None,
) -> str:
    """Markdown report file variant (same content as :func:`render_human`)."""
    return render_human(report, meta, threshold, strict, nli_model=nli_model)


def exit_code(report: Report, threshold: float, strict: bool) -> int:
    """Exit-code rule for ``citesure verify``.

    * default: ``0`` iff ``pass_rate >= threshold`` (D3), else ``1``.
    * ``--strict``: additionally requires zero ``ambiguous`` and zero
      ``unsupported`` verdicts (ambiguous counts as failure in CI mode).
    """
    if report.pass_rate < threshold:
        return 1
    if strict:
        for v in report.verdicts:
            if v.status in (Status.AMBIGUOUS, Status.UNSUPPORTED):
                return 1
    return 0
