"""Command-line interface: ``citesure verify <file>`` (D5/D12).

Usage::

    citesure verify tests/fixtures/sample.md            # human-readable
    citesure verify tests/fixtures/sample.md --json     # machine-readable
    citesure verify sample.json --threshold 0.9 --strict

Exit code is ``0`` iff ``pass_rate >= --threshold`` (default 0.8), else ``1``
(D3). Input errors (unreadable file, malformed JSON) exit ``2``.

``--strict`` (CI mode): ambiguous verdicts are reported as explicit
failures. Per D3 and :mod:`citesure.models`, ambiguous never contributes to
the pass-rate numerator in either mode, so the exit-code rule
(``pass_rate >= threshold``) is unchanged; strict mode changes how ambiguous
rows are labelled in the human-readable report.

``--nli`` / ``--nli-model`` are accepted now for forward compatibility but
print a notice and are ignored — the NLI tier lands in Phase 3 (D2/D6).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .citations import load_input
from .models import Report, Status, Verdict
from .reachability import verify_citations

NLI_NOTICE = (
    "citesure: --nli/--nli-model accepted but ignored for now — "
    "the NLI entailment tier lands in Phase 3."
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="citesure",
        description=(
            "Verify that an LLM's claims are supported by the sources it cites."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser(
        "verify", help="verify citations in a markdown or JSON file"
    )
    v.add_argument(
        "file",
        help="input file: markdown with inline citations, or structured JSON",
    )
    v.add_argument(
        "--json",
        action="store_true",
        help="print the report as JSON instead of human-readable text",
    )
    v.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="minimum pass_rate for exit code 0 (default: 0.8)",
    )
    v.add_argument(
        "--strict",
        action="store_true",
        help="CI mode: ambiguous verdicts are reported as explicit failures",
    )
    v.add_argument(
        "--nli",
        action="store_true",
        help="(Phase 3) enable the NLI entailment tier",
    )
    v.add_argument(
        "--nli-model",
        metavar="NAME",
        help="(Phase 3) cross-encoder model name or local path",
    )
    v.add_argument(
        "--cache-dir",
        metavar="DIR",
        help="override the fetch cache directory (sets CITECHECK_CACHE_DIR)",
    )
    return parser


# ---------------------------------------------------------------------------
# Rendering / exit decision (pure, unit-testable)
# ---------------------------------------------------------------------------


def _verdict_label(verdict: Verdict, strict: bool) -> str:
    """Label a verdict for the human-readable report.

    ``--strict`` relabels ``ambiguous`` from UNVERIFIABLE to an explicit
    failure (see module docstring for why the exit rule itself is unchanged).
    """
    if verdict.status is Status.SUPPORTED:
        return "PASS"
    if verdict.status is Status.UNSUPPORTED:
        return "FAIL"
    if verdict.status is Status.AMBIGUOUS and strict:
        return "FAIL (strict)"
    return "UNVERIFIABLE"


def render_human(
    report: Report, meta: dict, threshold: float, strict: bool
) -> str:
    """Render the human-readable report to a string."""
    lines: list[str] = [
        f"citesure verification report — {report.total} citation(s) "
        f"(input format: {meta.get('format', 'unknown')})",
        "",
    ]
    for v in report.verdicts:
        lines.append(f"  [{v.citation_id}] {_verdict_label(v, strict):<14} {v.url}")
        if v.evidence:
            lines.append(f"      evidence: {v.evidence}")
        for note in v.notes:
            lines.append(f"      note: {note}")
    lines.append("")
    lines.append(
        f"Summary: total={report.total} supported={report.supported} "
        f"unsupported={report.unsupported} unverifiable={report.unverifiable} "
        f"pass_rate={report.pass_rate:.4f}"
    )
    decision = "PASS" if report.pass_rate >= threshold else "FAIL"
    lines.append(
        f"Decision: {decision} (pass_rate {report.pass_rate:.4f} "
        f"{'>=' if report.pass_rate >= threshold else '<'} threshold {threshold:g})"
    )
    return "\n".join(lines)


def exit_code(report: Report, threshold: float, strict: bool) -> int:
    """D3 exit rule: 0 iff ``pass_rate >= threshold``, else 1.

    ``strict`` does not alter the arithmetic (ambiguous never enters the
    numerator, D3); it only changes report labelling.
    """
    return 0 if report.pass_rate >= threshold else 1


# ---------------------------------------------------------------------------
# Command implementation
# ---------------------------------------------------------------------------


def _cmd_verify(args: argparse.Namespace) -> int:
    if args.cache_dir:
        os.environ["CITECHECK_CACHE_DIR"] = str(
            Path(args.cache_dir).expanduser().resolve()
        )
    if args.nli or args.nli_model:
        print(NLI_NOTICE, file=sys.stderr)

    try:
        citations, meta = load_input(args.file)
    except (OSError, ValueError) as exc:
        print(f"citesure: cannot read input: {exc}", file=sys.stderr)
        return 2
    if not citations:
        print("citesure: warning: no citations found in input", file=sys.stderr)

    report = asyncio.run(verify_citations(citations))

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render_human(report, meta, args.threshold, args.strict))
    return exit_code(report, args.threshold, args.strict)


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point (``citesure``)."""
    args = _build_parser().parse_args(argv)
    if args.command == "verify":
        return _cmd_verify(args)
    return 2  # pragma: no cover - argparse enforces the subcommand


if __name__ == "__main__":
    raise SystemExit(main())
