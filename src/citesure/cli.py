"""Command-line interface: ``citesure verify <file>`` (D5/D12).

Usage::

    citesure verify tests/fixtures/sample.md            # human-readable
    citesure verify tests/fixtures/sample.md --json     # machine-readable
    citesure verify sample.json --threshold 0.9 --strict
    citesure verify sample.md --md report.md            # also write a .md file
    citesure verify sample.md --nli                     # + NLI entailment tier
    citesure verify sample.md --nli --nli-model NAME    # custom cross-encoder

Exit codes (D3):

* ``0`` — default mode: iff ``pass_rate >= --threshold`` (default 0.8).
  With ``--strict`` (CI mode): additionally requires **no** ``ambiguous``
  AND **no** ``unsupported`` verdicts — ambiguous counts as an explicit
  failure.
* ``1`` — verification ran but the exit rule above is not met.
* ``2`` — input error (unreadable file, malformed JSON, no citations found
  in a JSON input that must have some) OR the NLI model failed to load
  (fail fast, D6 — distinct from a verification-failure exit 1).

The default pipeline runs both D2 tiers: reachability (tier 1) and content
overlap (tier 2); ``tier_reached`` in each verdict records how far it got.

``--nli`` enables the NLI entailment tier (tier 3, D2/D6): a local
cross-encoder scores each (claim, best-passage) pair and the D3 banding
produces the final status. ``--nli-model NAME`` selects the model via the
D6 priority chain (flag → ``CITECHECK_NLI_MODEL`` env → built-in default
``cross-encoder/nli-deberta-v3-base``). The model is lazy-downloaded on
first use (~425 MB) into ``~/.cache/citesure/`` (override:
``CITECHECK_CACHE_DIR``). When NLI is on, the report header shows which
model was used.
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
from .nli import NLIError, get_nli_model, nli_extra_available, resolve_nli_model
from .reachability import verify_citations
from .report import exit_code, render_human, render_markdown


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
        default=None,
        help=(
            "enable the NLI entailment tier (tier 3): a local cross-encoder "
            "scores each (claim, best-passage) pair. Lazy-downloads the "
            "default model (~425 MB) into ~/.cache/citesure/ on first use. "
            "(default: auto — on when the [nli] extra is installed, off when "
            "it is not; the report always states which tier ran)"
        ),
    )
    v.add_argument(
        "--no-nli",
        action="store_false",
        dest="nli",
        help=(
            "disable the NLI entailment tier; overlap-only verdicts cannot "
            "detect negation or entity-swap claims."
        ),
    )
    v.add_argument(
        "--nli-model",
        metavar="NAME",
        help=(
            "cross-encoder model name (Hugging Face id) or local path; "
            "highest priority in the D6 chain (flag > CITECHECK_NLI_MODEL "
            "env > built-in default). Implies --nli."
        ),
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
    # --nli-model implies --nli (selecting a model means using the tier).
    # ``args.nli`` is None when neither flag was passed: AUTO-DETECT. The NLI
    # stack is the optional ``[nli]`` extra, so a plain ``pip install
    # citesure`` cannot run tier 3. Defaulting it ON anyway made the
    # documented first command fail outright on a default install (exit 2).
    # Instead: on when the extra is importable, off when it is not, and the
    # report always names the tier that actually ran - never a silent
    # downgrade (D6 fail-fast spirit).
    nli_available, nli_missing = nli_extra_available()
    if args.nli is None:
        use_nli = nli_available
    else:
        use_nli = bool(args.nli)
    if args.nli_model:
        # An explicit model means the user wants the tier; if the extra is
        # missing, get_nli_model() below fails fast with the actionable
        # message rather than silently ignoring the request.
        use_nli = True
    if not use_nli:
        print(
            "citesure: WARNING --no-nli disables the entailment tier; "
            "overlap-only verdicts cannot detect negation or entity-swap claims "
            "(see evals/EVAL_REPORT.md).",
            file=sys.stderr,
        )
        if args.nli is None and not nli_available:
            print(
                "citesure: note the NLI extra is not installed (missing: "
                + ", ".join(nli_missing)
                + '), so tier 3 is off. Install it with pip install "citesure[nli]" '
                "to enable entailment scoring.",
                file=sys.stderr,
            )

    try:
        citations, meta = load_input(args.file)
    except (OSError, ValueError) as exc:
        print(f"citesure: cannot read input: {exc}", file=sys.stderr)
        return 2
    if not citations:
        print("citesure: warning: no citations found in input", file=sys.stderr)
    # D6: a marker that resolved to no URL is a claim that is NEVER verified.
    # Say so loudly rather than reporting clean coverage over a silent hole.
    dropped = meta.get("dropped_markers") or []
    for d in dropped:
        print(
            "citesure: WARNING unresolved citation marker "
            f"{d['marker']} — {d['reason']}. This claim was NOT verified.",
            file=sys.stderr,
        )
    if dropped:
        print(
            f"citesure: {len(dropped)} marker(s) could not be resolved to a URL; "
            f"{len(citations)} citation(s) were verified.",
            file=sys.stderr,
        )

    nli_name: str | None = None
    if use_nli:
        # Fail fast BEFORE any fetching/scoring (D6): a bad model name must
        # not burn a full verification run only to die at the end.
        try:
            get_nli_model(args.nli_model)
        except NLIError as exc:
            print(f"citesure: {exc}", file=sys.stderr)
            return 2
        nli_name = resolve_nli_model(args.nli_model)

    # Pipeline: tier 1 (reachability) + tier 2 (content overlap), plus tier 3
    # (NLI entailment) when enabled.
    try:
        report = asyncio.run(
            verify_citations(
                citations, use_overlap=True, use_nli=use_nli,
                nli_model=args.nli_model, allow_any_local=True,
            )
        )
    except NLIError as exc:  # defensive: load already validated above
        print(f"citesure: {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = report.to_dict()
        # Never let a JSON consumer mistake an overlap-only run for an
        # entailment-checked one: state the tiers that actually ran.
        payload["nli_active"] = use_nli
        payload["tiers_reached"] = 3 if use_nli else 2
        payload["dropped_markers"] = dropped
        print(json.dumps(payload, indent=2))
    else:
        print(render_human(
            report, meta, args.threshold, args.strict,
            nli_model=nli_name, nli_active=use_nli,
        ))
    if args.md:
        Path(args.md).write_text(
            render_markdown(
                report, meta, args.threshold, args.strict,
                nli_model=nli_name, nli_active=use_nli,
            ),
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
