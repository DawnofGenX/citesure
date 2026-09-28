#!/usr/bin/env python3
"""Validate set v2 labels against saved extracted page text.

Hard gate: a `supported` claim must share a 6-gram with the extracted text, and
an `unsupported` claim must not. Also enforces the three-way contrast
invariant: for every url carrying a supported case there must be >= 2
unsupported siblings.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

POOL = json.loads(Path("evals/page_pool_verified.json").read_text(encoding="utf-8"))
BY_URL = {e["url"]: e for e in POOL["entries"]}


def shingles(text: str, n: int = 6) -> set[str]:
    words = text.lower().split()
    return {" ".join(words[i : i + n]) for i in range(max(0, len(words) - n + 1))}


def main() -> int:
    cases = json.loads(Path("evals/independent_set_v2.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    by_url: dict[str, list[dict]] = defaultdict(list)

    for c in cases:
        entry = BY_URL.get(c["url"])
        if entry is None:
            problems.append(f"{c['id']}: url not in verified pool")
            continue
        by_url[c["url"]].append(c)

        if not Path(entry["text_path"]).exists():
            problems.append(f"{c['id']}: missing saved text {entry['text_path']}")
            continue
        text = Path(entry["text_path"]).read_text(encoding="utf-8")
        present = bool(shingles(c["claim"]) & shingles(text))

        if c["expected_status"] == "supported" and not present:
            problems.append(f"{c['id']}: labeled supported but no 6-gram in extracted text")
        if c["expected_status"] == "unsupported" and present:
            problems.append(f"{c['id']}: labeled unsupported but claim text IS present")

    # Three-way contrast invariant.
    for url, group in by_url.items():
        pos = [c for c in group if c["expected_status"] == "supported"]
        neg = [c for c in group if c["expected_status"] == "unsupported"]
        if pos and len(neg) < 2:
            problems.append(f"{url}: has a supported case but only {len(neg)} negatives (need 2)")

    for p in problems:
        print("PROBLEM:", p)
    print(f"\n[validate] {len(cases)} cases, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
