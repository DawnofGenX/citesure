"""Command-line interface: ``citesure verify <file>`` (D5/D12).

Usage::

    citesure verify tests/fixtures/sample.md            # human-readable
    citesure verify tests/fixtures/sample.md --json     # machine-readable
    citesure verify sample.json --threshold 0.9 --strict
    citesure verify sample.md --md report.md            # also write a .md file

Exit codes (D3):

* ``0`` — default mode: iff ``pass_rate >= --threshold`` (default 0.8).
  With ``--strict`` (CI mode): additionally requires **no** ``ambiguous``
  AND **no** ``unsupported`` verdicts — ambiguous counts as an explicit
  failure.
* ``1`` — verification ran but the exit rule above is not met.
* ``2`` — input error (unreadable file, malformed JSON, no citations found
  in a JSON input that must have some).

The default pipeline runs both D2 tiers: reachability (tier 1) and content
overlap (tier 2); ``tier_reached`` in each verdict records how far it got.

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
from .models import Report
from .reachability import verify_citations
from .report import exit_code, render_human, render_markdown

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
        help="print the full report as JSON instead of human-readable text",
    )
    v.add_argument(
        "--md",
        metavar="FILE",
        help="also write the Markdown report to FILE (in addition to stdout)",
    )
    v.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help=(
            "minimum pass_rate for exit code 0 (default: 0.8). Exit code is "
            "0 iff pass_rate >= threshold; with --strict, ambiguous and "
            "unsupported verdicts are additionally treated as failures."
        ),
    )
    v.add_argument(
        "--strict",
        action="store_true",
        help=(
            "CI mode: ambiguous verdicts count as explicit failures. Exit "
            "code is 0 only if pass_rate >= --threshold AND there are no "
            "ambiguous AND no unsupported verdicts; otherwise 1."
        ),
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

    # Default pipeline: tier 1 (reachability) + tier 2 (content overlap).
    report = asyncio.run(verify_citations(citations, use_overlap=True))

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render_human(report, meta, args.threshold, args.strict))
    if args.md:
        Path(args.md).write_text(
            render_markdown(report, meta, args.threshold, args.strict),
            encoding="utf-8",
        )
    return exit_code(report, args.threshold, args.strict)


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point (``citesure``)."""
    args = _build_parser().parse_args(argv)
    if args.command == "verify":
        return _cmd_verify(args)
    return 2  # pragma: no cover - argparse enforces the subcommand


if __name__ == "__main__":
    raise SystemExit(main())
