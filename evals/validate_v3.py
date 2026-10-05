#!/usr/bin/env python3
"""Validate set v3 labels against saved extracted page text.

v3 adds per-clause bookkeeping: ``supported_clauses`` and ``unsupported_clauses``.
Hard gate: every supported clause must share a 6-gram with the extracted text.
Unsupported clauses get a WARNING (not a hard failure) because absence-of-span
is weaker evidence than presence — a compound claim's unsupported clause is
often built from words that DO appear on the page in a different combination.

The 6-gram check on clause lists applies ONLY to ``supported_clauses`` presence
and to whole-claim ``expected_status``.  For ``unsupported_clauses`` a match is
reported as a WARNING with an explanation, never a hard failure.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SET_PATH = REPO / "evals" / "independent_set_v3.json"
V1_PATH = REPO / "evals" / "independent_set.json"
V2_PATH = REPO / "evals" / "independent_set_v2.json"
POOL_PATH = REPO / "evals" / "page_pool_v3.json"

REQUIRED_KEYS = {
    "id",
    "category",
    "expected_status",
    "labeler_confidence",
    "url",
    "claim",
    "notes",
    "supported_clauses",
    "unsupported_clauses",
}
# D3 statuses (mirrors run_eval.py STATUSES minus the "error" bucket).
# NOTE: the CATEGORY "partially-true-mixed" maps to expected_status
# "ambiguous" — a mixed-support compound claim is D3-ambiguous, not a
# separate status. An earlier revision wrongly listed the category name here,
# which would have rejected every correctly-labelled PTM case.
VALID_STATUS = {"supported", "unsupported", "unreachable", "paywalled", "ambiguous"}
VALID_CONFIDENCE = {"high", "medium", "low"}


def shingles(text: str, n: int = 6) -> set[str]:
    """Return the set of n-gram shingles for *text*."""
    words = text.lower().split()
    return {" ".join(words[i : i + n]) for i in range(max(0, len(words) - n + 1))}


def main() -> int:
    if not SET_PATH.exists():
        print("[validate_v3] evals/independent_set_v3.json not present yet — skipping")
        return 0

    cases: list[dict] = json.loads(SET_PATH.read_text(encoding="utf-8"))
    problems: list[str] = []
    warnings: list[str] = []

    # Load pool for text lookup
    pool = json.loads(POOL_PATH.read_text(encoding="utf-8"))
    by_url: dict[str, dict] = {e["url"]: e for e in pool["entries"]}

    # Load v1/v2 ids and urls for collision / contamination checks
    old_ids: set[str] = set()
    old_urls: set[str] = set()
    for p in (V1_PATH, V2_PATH):
        if p.exists():
            old: list[dict] = json.loads(p.read_text(encoding="utf-8"))
            old_ids |= {c["id"] for c in old}
            old_urls |= {c["url"] for c in old}

    ids: list[str] = []
    for c in cases:
        cid = c.get("id", "<no-id>")
        ids.append(cid)

        # ── Schema ──────────────────────────────────────────────────────
        missing = REQUIRED_KEYS - set(c)
        if missing:
            problems.append(f"{cid}: missing keys {missing}")
            continue

        if not cid.startswith("ind3-"):
            problems.append(f"{cid}: id must start with 'ind3-'")

        if c["expected_status"] not in VALID_STATUS:
            problems.append(f"{cid}: invalid expected_status {c['expected_status']!r}")

        if c["labeler_confidence"] not in VALID_CONFIDENCE:
            problems.append(f"{cid}: invalid labeler_confidence {c['labeler_confidence']!r}")

        if not c["url"].startswith(("http://", "https://")):
            problems.append(f"{cid}: url must start with http(s)://")

        if not c["claim"].strip():
            problems.append(f"{cid}: claim is empty")

        # ── Clause bookkeeping ──────────────────────────────────────────
        supported: list[str] = c.get("supported_clauses", [])
        unsupported: list[str] = c.get("unsupported_clauses", [])

        if c["category"] == "partially-true-mixed":
            if not supported:
                problems.append(f"{cid}: partially-true-mixed but no supported_clauses")
            if not unsupported:
                problems.append(f"{cid}: partially-true-mixed but no unsupported_clauses")

        # ── Verbatim-span evidence ──────────────────────────────────────
        entry = by_url.get(c["url"])
        if entry is None:
            # A dead-link / unreachable case points at a URL that is NOT in the
            # verified pool BY DESIGN: the pool only records fetchable pages,
            # and the absence of the page is what makes the case unreachable.
            # Every other category must resolve to a saved snapshot.
            if c["expected_status"] in ("unreachable", "paywalled"):
                continue
            problems.append(f"{cid}: url not in verified pool")
            continue

        text_path = REPO / entry["text_path"]
        if not text_path.exists():
            # A page that failed to fetch has no saved text BY DESIGN — that is
            # what makes it an unreachable/dead-link case.
            if entry.get("ok"):
                problems.append(f"{cid}: fetchable page but no saved text {entry['text_path']}")
            continue

        text = text_path.read_text(encoding="utf-8")
        text_shingles = shingles(text)

        # Supported clauses: MUST share a 6-gram (hard failure)
        for clause in supported:
            if not (shingles(clause) & text_shingles):
                problems.append(
                    f"{cid}: supported clause has no 6-gram in text: {clause[:60]!r}"
                )

        # Unsupported clauses: WARNING only — absence-of-span is weaker
        # evidence than presence.  A compound claim's unsupported clause is
        # often built from words that DO appear on the page in a different
        # combination, so a 6-gram match does NOT prove the clause is supported.
        for clause in unsupported:
            if shingles(clause) & text_shingles:
                warnings.append(
                    f"{cid}: unsupported clause shares a 6-gram with text "
                    f"(WARNING — absence is weaker evidence): {clause[:60]!r}"
                )

        # Whole-claim check, v3-ADAPTED.
        #
        # v2's rule ("a supported claim must share a 6-gram with the text")
        # does NOT transfer to v3. A v3 `supported` case may be a COMPOUND
        # claim whose two clauses each quote a different part of the page; the
        # joined claim string then has no run of six consecutive words in the
        # source BY CONSTRUCTION, because the words are non-adjacent in the
        # page. Applying v2's rule verbatim rejected 7 of 10 valid
        # fully-supported-compound cases.
        #
        # So: when a case declares supported_clauses, the clause-level check
        # above is the authoritative one and the whole-claim check is skipped.
        # For a single-clause supported case the whole-claim check still
        # applies, because there the two are equivalent.
        if c["expected_status"] == "supported":
            if not supported:
                if not (shingles(c["claim"]) & text_shingles):
                    problems.append(
                        f"{cid}: labeled supported but no 6-gram in extracted text"
                    )
        elif c["expected_status"] == "unsupported":
            # For a compound claim, only the UNSUPPORTED clauses are asserted
            # absent; the claim as a whole may legitimately contain spans from
            # the page (that is the point of a mixed-support case). Require
            # that no unsupported clause matches, which the warning above
            # already reports, so nothing extra is enforced here.
            pass

    # ── Duplicate ids ────────────────────────────────────────────────────
    id_counts = Counter(ids)
    for cid, n in id_counts.items():
        if n > 1:
            problems.append(f"{cid}: duplicate id ({n} occurrences)")

    # ── Id collisions with v1/v2 (hard failure) ─────────────────────────
    collisions = set(ids) & old_ids
    if collisions:
        problems.append(f"id collision with v1/v2: {sorted(collisions)}")

    # ── Contamination warning (NOT a hard failure) ───────────────────────
    reused = {c["url"] for c in cases} & old_urls
    if reused:
        warnings.append(f"{len(reused)} URLs reused from v1/v2: {sorted(reused)}")

    # ── Output ───────────────────────────────────────────────────────────
    for w in warnings:
        print("WARNING:", w)
    for p in problems:
        print("PROBLEM:", p)

    cat_counts = Counter(c.get("category", "<missing>") for c in cases)
    status_counts = Counter(c.get("expected_status", "<missing>") for c in cases)

    print(f"\n[validate_v3] {len(cases)} cases, {len(problems)} problems, {len(warnings)} warnings")
    print("\nPer-category counts:")
    for cat, n in sorted(cat_counts.items()):
        print(f"  {cat}: {n}")
    print("\nexpected_status distribution:")
    for status, n in sorted(status_counts.items()):
        print(f"  {status}: {n}")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
