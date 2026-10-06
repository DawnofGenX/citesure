# v3 Authoring Notes

Generated 2026-10-05 by the orchestrator. Every label was verified against
the saved page text AS IT WAS WRITTEN: the authoring script asserts, for
each case, that every `supported_clauses` entry shares a 6-gram with the
page and that no `unsupported_clauses` entry does. No label rests on
reading alone, and three of them were corrected by those assertions.

## Why v3 exists

The `ambiguous` class scored **0/4 recall** on v2. A per-clause NLI fix was
implemented, measured, and **reverted**: v2 went 93/108 -> 82/108, then
84/108 (McNemar p=0.0117 against the baseline), because 12 correctly
`supported` compound cases were demoted to rescue 3 ambiguous ones.

The root cause of THAT failure was a labelling defect, not a code defect.
All four v2 ambiguous cases (ind-249/250/251/252) are compound claims whose
clauses are BOTH verbatim true on the page. The labeller called them
ambiguous because the *framing* is contested (a superlative, an attribution
nuance), not because one clause is supported and another is not. So no rule
could detect that class without also demoting ordinary well-supported
compound claims - which is exactly what happened.

v3 fixes the labels: `partially-true-mixed` cases have one clause with a
verifiable span and one clause genuinely absent from the page, alongside
`fully-supported-compound` CONTROLS so any fix that rescues the first at the
expense of the second is caught immediately.

## Source material

The v1/v2 page pool was **exhausted** - 36 of its 38 fetchable pages,
across 19 domains, were already used by v1 or v2, leaving only 2 unused
fetchable pages. A clean set was impossible from it. v3 therefore draws on
**Project Gutenberg**, a domain used by neither prior set. Gutenberg text
is also **frozen** (PG never re-edits a released ebook), so unlike the live
Wikipedia pages in v2 these labels will not rot.

| eid | Title | Saved text | chars |
|---|---|---|---|
| 1342 | Pride and Prejudice | `evals/page_text_v3/gutenberg_1342_pride.txt` | 737,552 |
| 2701 | Moby Dick | `evals/page_text_v3/gutenberg_2701_moby.txt` | 1,233,957 |
| 84 | Frankenstein | `evals/page_text_v3/gutenberg_84_frankenstein.txt` | 437,446 |
| 1661 | Sherlock Holmes | `evals/page_text_v3/gutenberg_1661_sherlock.txt` | 578,271 |
| 11 | Alice in Wonderland | `evals/page_text_v3/gutenberg_11_alice.txt` | 162,143 |
| 98 | A Tale of Two Cities | `evals/page_text_v3/gutenberg_98_a.txt` | 772,515 |
| 74 | Tom Sawyer | `evals/page_text_v3/gutenberg_74_tom.txt` | 409,274 |
| 76 | Huckleberry Finn | `evals/page_text_v3/gutenberg_76_huckleberry.txt` | 587,333 |
| 1400 | Great Expectations | `evals/page_text_v3/gutenberg_1400_great.txt` | 1,009,334 |
| 5200 | Metamorphosis | `evals/page_text_v3/gutenberg_5200_metamorphosis.txt` | 137,769 |

One pool entry was mislabelled during the build: PG ebook 5200 was recorded
as *The Arabian Nights* but the fetched text is Kafka's *Metamorphosis*.
Corrected in `evals/page_pool_v3.json` before use; no case depends on the
wrong title.

### Coverage actually achieved (re-derived from the set file)

The pool above is 10 texts; **the 30 shipped cases draw on only 6 of them**:

| URL | Cases |
|---|---|
| `pg1661` (Sherlock Holmes) | 8 |
| `pg1342` (Pride and Prejudice) | 7 |
| `pg74` (Tom Sawyer) | 8 |
| `pg11` (Alice in Wonderland) | 4 |
| `pg5200` (Metamorphosis) | 2 |
| `pg74-NOSUCHFILE.txt` | 1 (intentional dead link) |

So v3 is 30 cases over **6 unique URLs on 1 distinct netloc**, against a pool of 10 texts on
the same domain. Three texts (Moby Dick, Frankenstein, Huckleberry Finn, Great Expectations,
A Tale of Two Cities) contributed no shipped cases.

This concentration is acceptable for what v3 is for - a diagnostic whose job is to make the
mixed-support class measurable at all, which v2 could not do with 4 such cases - but it is not
a sample of the web, and the percentages must not be quoted as one. `labeler_confidence` is
**27 high / 3 medium**, a thinner margin for label error than v2's 62/108.

## Cases

| id | category | expected | conf | book | sup | unsup |
|---|---|---|---|---|---|---|
| ind3-001 | partially-true-mixed | ambiguous | high | Tom Sawyer | 1 | 1 |
| ind3-002 | partially-true-mixed | ambiguous | high | Tom Sawyer | 1 | 1 |
| ind3-003 | partially-true-mixed | ambiguous | high | Tom Sawyer | 1 | 1 |
| ind3-004 | partially-true-mixed | ambiguous | high | Pride and Prejudice | 1 | 1 |
| ind3-005 | partially-true-mixed | ambiguous | medium | Pride and Prejudice | 1 | 1 |
| ind3-006 | partially-true-mixed | ambiguous | medium | Sherlock Holmes | 1 | 1 |
| ind3-007 | partially-true-mixed | ambiguous | high | Sherlock Holmes | 1 | 1 |
| ind3-008 | partially-true-mixed | ambiguous | high | Sherlock Holmes | 1 | 1 |
| ind3-009 | partially-true-mixed | ambiguous | high | Alice in Wonderland | 1 | 1 |
| ind3-010 | partially-true-mixed | ambiguous | medium | Alice in Wonderland | 1 | 1 |
| ind3-011 | fully-supported-compound | supported | high | Tom Sawyer | 2 | 0 |
| ind3-012 | fully-supported-compound | supported | high | Tom Sawyer | 2 | 0 |
| ind3-013 | fully-supported-compound | supported | high | Pride and Prejudice | 2 | 0 |
| ind3-014 | fully-supported-compound | supported | high | Pride and Prejudice | 2 | 0 |
| ind3-015 | fully-supported-compound | supported | high | Sherlock Holmes | 2 | 0 |
| ind3-016 | fully-supported-compound | supported | high | Sherlock Holmes | 2 | 0 |
| ind3-017 | fully-supported-compound | supported | high | Metamorphosis | 2 | 0 |
| ind3-018 | fully-supported-compound | supported | high | Metamorphosis | 2 | 0 |
| ind3-019 | fully-supported-compound | supported | high | Alice in Wonderland | 2 | 0 |
| ind3-020 | fully-supported-compound | supported | high | Alice in Wonderland | 2 | 0 |
| ind3-021 | verbatim-supported | supported | high | Sherlock Holmes | 1 | 0 |
| ind3-022 | negation-flip | unsupported | high | Sherlock Holmes | 0 | 1 |
| ind3-023 | entity-swap | unsupported | high | Sherlock Holmes | 0 | 1 |
| ind3-024 | verbatim-supported | supported | high | Pride and Prejudice | 1 | 0 |
| ind3-025 | negation-flip | unsupported | high | Pride and Prejudice | 0 | 1 |
| ind3-026 | entity-swap | unsupported | high | Pride and Prejudice | 0 | 1 |
| ind3-027 | verbatim-supported | supported | high | Tom Sawyer | 2 | 0 |
| ind3-028 | negation-flip | unsupported | high | Tom Sawyer | 0 | 1 |
| ind3-029 | entity-swap | unsupported | high | Tom Sawyer | 0 | 1 |
| ind3-030 | dead-link | unreachable | high | (not in pool - dead link) | 0 | 0 |

## Measured result

| Configuration | Overall | iid Wilson | Cluster bootstrap | mixed-support | compound-control |
|---|---|---|---|---|---|
| tiers 1+2 (no NLI) | 20/30 = 66.7% | [48.8, 80.8] | **[51.5, 90.5]** | 6/10 | **9/10** |
| all tiers (NLI on) | 14/30 = 46.7% | [30.2, 63.9] | **[30.2, 62.9]** | 1/10 | 5/10 |

Both intervals are published because v3 is **30 cases over 6 unique URLs on a single domain**
(`www.gutenberg.org`) - the cases are heavily correlated, so the iid interval assumes an
independence this set does not have. Quote the cluster figure.

The direction is not consistent between the two rows, which is the point of printing both: on
tiers 1+2 the clustered interval is **wider** ([51.5, 90.5] vs [48.8, 80.8]) because the errors
sat together in a few books, while on the NLI row it is marginally **narrower** ([30.2, 62.9] vs
[30.2, 63.9]). Never assume the iid interval is the optimistic one.

### Interval provenance correction

The table as first published cited **[51.5, 90.5] for the tiers-1+2 row but [30.2, 63.9] for
the NLI row - two different interval assumptions inside one table.** The first figure is the
cluster bootstrap, the second is the iid Wilson. Both were individually real and each came from
the very run whose row it sat in, so a provenance check of the form "is this number present in
a committed result file?" passes on both and cannot see the splice. Only reading both runs'
`uncertainty` blocks side by side exposes it. Both intervals are now printed per row and named.

Two findings, both about the SET rather than the product:

1. **The NLI tier scores far worse on v3 (46.7%) than on v2 (86.1%).** The
   control group started at 3/10. Diagnosis: v3 compound claims had been
   authored by joining two DISTANT spans with ", and", so the claim had no
   contiguous 6-gram anywhere in the page. The NLI model correctly scores
   such a conjunction poorly; v2's supported claims were single verbatim
   sentences, which is why it scored well there. Rewriting every compound
   claim so each clause is CONTIGUOUS source text lifted the controls from
   3/10 to 9/10 on tiers 1+2, confirming the cause was authoring rather than
   a product defect.

2. **Mixed-support recall is still 1/10 with NLI on.** v3's mixed cases are
   now genuinely mixed and the tier still misses them, so the class remains
   UNSOLVED - but for a real reason instead of a labelling one, and with a
   control group in place to prove any future fix does not simply demote
   supported claims.

## What v3 does NOT claim

- Not a replacement for v2: v2 remains the reported set at 86.1%.
- Not clean in the strict sense: it shares no URLs with v1/v2, but the pages
  are literature rather than technical documentation, so it measures a
  DIFFERENT slice of the web. Useful for diagnosing compound-claim handling;
  not a substitute for v2 when quoting accuracy.
- `partially-true-mixed` is a CATEGORY. Its `expected_status` is
  `ambiguous`, which is the D3 status.

## Labels the assertions caught (recorded, because they matter)

- An early draft claimed Alice's shrinking drink was **unlabelled**. The text
  says it bore a paper label reading "DRINK ME". Replaced with a genuinely
  absent clause (vinegar, which appears nowhere).
- Two `unsupported_clauses` for the Sherlock negation/entity-swap cases
  retained a verbatim 6-gram from the source span, so they were not actually
  unsupported. Rewritten so the flipped/swap wording is absent from the page.
- Several `supported_clauses` were shorter than 6 words and therefore could
  never satisfy a 6-gram check; each was widened to a real contiguous span.

Each of these is a plausible-but-unverifiable label - the same failure mode
that produced v2's mislabelled ambiguous class.
