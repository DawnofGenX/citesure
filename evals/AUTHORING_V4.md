# v4 Authoring Notes

Generated 2026-10-24T23:24:00.913243+00:00 by `evals/build_v4.py` (committed, reproducible).

## Why v4 exists

v2 is the set the 86.1% headline comes from and was used for model selection and score
tuning; v3 is a single-domain diagnostic for the mixed-support class. Neither answers
whether the number survives on material nobody tuned against, from domains v1/v2/v3
never touched. v4 is that test.

## How the labels were derived

Constructively, then asserted against the saved page text — no human reading, no model
judgement:

| family | construction | assertion | expected |
|---|---|---|---|
| verbatim-supported | claim copied as a contiguous span | shares ≥1 6-gram with the page | supported |
| negation-flip | a negation inserted after an auxiliary | shares **no** 6-gram with the page | unsupported |
| entity-swap | a mid-sentence name replaced from a fixed pool | shares **no** 6-gram with the page | unsupported |
| dead-link | host does not resolve | n/a | unreachable |

Page text is extracted with **the same call the verifier uses**
(`trafilatura.extract(html, include_comments=False)`, `src/citesure/fetcher.py:431`).
That detail decides whether a label means anything: a first build extracted with
`favor_precision=True`, which kept some sentences the verifier never sees, so a
handful of cases were labelled `supported` while citesure's own text did not contain
them. The set was rebuilt against the verifier's extraction instead of relaxing the
assertions.

A case that fails its own assertion is dropped and counted below, never hand-repaired.

## What was built

- **61 cases** over **12 fresh pages** on **14 domains**.
- Categories: {'dead-link': 2, 'entity-swap': 13, 'negation-flip': 22, 'verbatim-supported': 24}.

| page | URL | chars | cases |
|---|---|---|---|
| unicode | https://www.unicode.org/reports/tr15/ | 72,624 | 5 |
| rustdoc | https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html | 21,391 | 5 |
| postgres-about | https://www.postgresql.org/about/ | 6,533 | 5 |
| stanford-encyclopedia | https://plato.stanford.edu/entries/artificial-intelligence/ | 183,841 | 5 |
| who | https://www.who.int/news-room/fact-sheets/detail/ambient-(outdoor)-air-quality-and-health | 7,981 | 5 |
| kotlin | https://kotlinlang.org/docs/classes.html | 8,697 | 5 |
| eff | https://www.eff.org/issues/privacy | 2,097 | 4 |
| un-climate | https://www.un.org/en/global-issues/climate-change | 8,812 | 5 |
| cancer-gov | https://www.cancer.gov/about-cancer/understanding/what-is-cancer | 14,134 | 5 |
| osm-about | https://www.openstreetmap.org/about | 2,076 | 5 |
| icann | https://www.icann.org/resources/pages/welcome-2012-02-25-en | 4,352 | 5 |
| rbi | https://www.rbi.org.in/Scripts/AboutUsDisplay.aspx | 6,445 | 5 |

## Size, and why it is below the target

The target was ≥100 cases over ≥30 fresh domains. This build got 61 cases over 14 domains, and the shortfall is
source availability, not authoring: of the candidate URLs, several returned 403 to a
Python client (w3.org, cisa.gov, hhs.gov, oecd.org, rbi.org.in), returned 404/202
(gov.uk, ftc.gov, federalreserve.gov, legislation.gov.uk), or extracted under 2 KB of
readable prose (nhs.uk, iea.org, unesco.org, worldbank.org, redcross.org.uk,
weather.gov, owasp.org). Every drop is listed verbatim below, so nothing is hidden.
Growing the set is a matter of adding source classes that serve plain HTML — .edu
documentation, standards bodies without bot walls — not of relaxing the assertions.

## What v4 does NOT claim

- **No ambiguous/mixed class.** That class needs human adjudication; adding it here
  without a second annotator would repeat v2's labelling defect. v4 measures polarity
  and subject binding only, so it is not directly comparable to v2's 86.1%.
- **Single annotator, constructive labels.** Inter-annotator agreement is not measured;
  the 6-gram assertions are what replace it for these families.
- **Live pages.** Unlike v3's frozen Gutenberg texts these can change; the saved text in
  `evals/page_text_v4/` is what each label was asserted against, and fetching again later
  may legitimately change a verdict.

## Dropped during the build

- w3c (www.w3.org): HTTP 403
- cisa (www.cisa.gov): HTTP 403
- gov-uk (www.gov.uk): HTTP 404
- owasp (owasp.org): extracted too little text (758 chars)
- legislation-uk (www.legislation.gov.uk): HTTP 202
- iea (www.iea.org): extracted too little text (1292 chars)
- unesco (www.unesco.org): extracted too little text (1628 chars)
- nhs (www.nhs.uk): extracted too little text (233 chars)
- ftc (www.ftc.gov): HTTP 404
- hhs-hipaa (www.hhs.gov): HTTP 403
- federalreserve (www.federalreserve.gov): HTTP 404
- worldbank (www.worldbank.org): extracted too little text (970 chars)
- oecd-ai (www.oecd.org): HTTP 403
- redcross-uk (www.redcross.org.uk): extracted too little text (1473 chars)
- weather-gov (www.weather.gov): extracted too little text (1544 chars)
