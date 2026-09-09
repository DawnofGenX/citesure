"""Content-overlap tier (tier 2 of D2) — citation-anchored windows (D4).

Design (deterministic, NO LLM, NO embeddings — NLI is Phase 3):

* **Claim unit** — ``Citation.claim`` (the sentence/paragraph containing the
  citation marker, extracted in Phase 1). Markdown link/marker syntax is
  stripped before tokenization (:func:`clean_claim`) so ``[1]`` and
  ``[label](url)`` do not pollute the term set.
* **Passage segmentation** — fetched page text is split into *short
  paragraphs*: consecutive sentences grouped up to ``PASSAGE_MAX_CHARS``
  characters; a trailing fragment shorter than ``PASSAGE_MIN_CHARS`` is
  merged into the previous passage. Sentences are delimited by ``.!?``
  followed by whitespace. (Documented choice: sentence-grouping rather than
  raw paragraphs, because trafilatura output paragraphs vary wildly in
  length and a 2000-char paragraph would drown a 200-char one in Jaccard
  math; short paragraphs keep the coverage ratio meaningful.)
* **Top-k selection** — passages are ranked by claim-term coverage and the
  top ``top_k`` (default ``DEFAULT_TOP_K = 5``, configurable) form the
  candidate set.
* **Scoring** — weighted term-coverage ratio in ``[0, 1]``: the fraction of
  the claim's total term weight that appears in a passage. Entity-ish
  tokens (capitalized words, numbers, exact quoted phrases) weigh
  ``ENTITY_WEIGHT`` (2.0); ordinary content terms weigh ``CONTENT_WEIGHT``
  (1.0). Common English stopwords are ignored. The final score is the best
  (max) over the top-k passages. Same input → same output (pure function).
* **Verdicts (D3)** — ``score >= HIGH_OVERLAP_THRESHOLD`` (0.6) →
  ``supported``; ``LOW_OVERLAP_THRESHOLD`` (0.3) ≤ score < 0.6 →
  ``ambiguous``; score < 0.3 → ``unsupported``. Pages that fail
  reachability keep their tier-1 status (``unreachable`` / ``paywalled`` /
  retracted → ``unsupported``). A reachable page with fewer than
  ``MIN_EXTRACTABLE_CHARS`` characters of extractable text is treated as a
  JavaScript-rendered page whose citation marker cannot be located →
  ``ambiguous`` (D3/D8: no headless browser).
* **User-supplied excerpts (D4)** — when ``Citation.excerpt`` is set, the
  overlap comparison runs against the excerpt instead of the fetched page
  text. Reachability is still checked first: an unreachable or paywalled
  source keeps its tier-1 status even when an excerpt is supplied.

Pipeline: fetch → classify reachability (tier 1) → if reachable with no red
flags, run the overlap tier (tier 2) → final status. ``tier_reached`` is 2
whenever the overlap tier ran.

Phase 3 (D2/D6): with ``use_nli=True`` a third tier runs on top of tiers 1+2
— a local cross-encoder scores entailment for each (claim, best-passage) pair
in ONE batched forward pass, and the D3 banding (see :mod:`citesure.nli`)
produces the final status. ``tier_reached`` is 3 whenever the NLI tier ran.
"""

from __future__ import annotations

import asyncio
import re

from .citations import Citation
from .fetcher import fetch
from .models import Report, Status, Verdict
from .reachability import classify_reachability

# ---------------------------------------------------------------------------
# Tunables — module-level constants so tests can pin them (D3 thresholds).
# ---------------------------------------------------------------------------

#: score >= HIGH → supported
HIGH_OVERLAP_THRESHOLD = 0.6
#: LOW <= score < HIGH → ambiguous; score < LOW → unsupported
LOW_OVERLAP_THRESHOLD = 0.3
#: Number of top-ranked passages considered for the final score.
DEFAULT_TOP_K = 5
#: Weight of entity-ish tokens (capitalized words, numbers, quoted phrases).
ENTITY_WEIGHT = 2.0
#: Weight of ordinary content terms.
CONTENT_WEIGHT = 1.0
#: Fewer extractable characters than this ⇒ "marker not locatable" (JS page).
#: Calibrated against the fixture corpus: a JS shell page whose only
#: extractable text is a <noscript> notice (~100 chars) must fall below this,
#: while every real content fixture page stays well above it.
MIN_EXTRACTABLE_CHARS = 80
#: Passage grouping caps (see module docstring).
PASSAGE_MAX_CHARS = 400
PASSAGE_MIN_CHARS = 20
#: Evidence snippets returned by :func:`score_overlap` are clipped to this.
SNIPPET_MAX_CHARS = 300

# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    """
    a an and are as at be been being but by can could did do does doing down
    during each few for from further had has have having he her here hers
    him his how i if in into is it its itself just me more most my myself
    of off on once one only or other our ours out over own same she
    should so some such than that the their theirs them then there these
    they this those through to too under until up very was we were what when
    where which while who whom why will with would you your yours
    """.split()
)

#: Negation words that must NOT be stripped — they carry the proposition.
#: (IMP-5: "not", "no", "never", "nor" were stopwords, making negation-flips
#: invisible to the overlap tier.)
_NEGATION_WORDS = frozenset({"not", "no", "never", "nor"})

#: Word token: letter-led (may contain digits/apostrophes/hyphens) or a
#: number (optionally decimal, e.g. "3.12").
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*|\d+(?:\.\d+)?")

#: Double- or single-quoted phrase (2+ chars, no newline).
_QUOTED_RE = re.compile(r'"([^"\n]{2,})"|\'([^\'\n]{2,})\'')

#: ``[1]`` numeric marker (but not ``[1](url)``).
_NUMERIC_MARKER_RE = re.compile(r"\[(\d+)\](?!\()")

#: ``[label](target)`` inline link.
_INLINE_LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\(\s*[^()\s]+\s*\)")

#: Sentence boundary: sentence-ending punctuation followed by whitespace.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def clean_claim(claim: str) -> str:
    """Strip citation-marker syntax from a claim before tokenizing.

    ``[label](url)`` collapses to ``label``; bare ``[n]`` markers are
    removed; whitespace runs are collapsed. The remaining text is the
    actual claim content.
    """
    cleaned = _INLINE_LINK_RE.sub(lambda m: m.group(1), claim or "")
    cleaned = _NUMERIC_MARKER_RE.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def content_terms(text: str) -> set[str]:
    """Lowercased content terms: non-stopword tokens, length >= 2 (digits
    of any length kept)."""
    out: set[str] = set()
    for m in _WORD_RE.finditer(text or ""):
        tok = m.group(0)
        low = tok.lower()
        if low in _STOPWORDS:
            continue
        if not tok[0].isdigit() and len(tok) < 2:
            continue
        out.add(low)
    return out


def entity_terms(text: str) -> set[str]:
    """Entity-ish tokens: numbers, capitalized words (non-stopwords), and
    exact quoted phrases (kept as whole multi-word strings)."""
    ents: set[str] = set()
    for m in _WORD_RE.finditer(text or ""):
        tok = m.group(0)
        low = tok.lower()
        if low in _STOPWORDS:
            continue
        if tok[0].isdigit():
            ents.add(low)
        elif tok[0].isupper() and len(tok) >= 2:
            ents.add(low)
    for m in _QUOTED_RE.finditer(text or ""):
        phrase = (m.group(1) or m.group(2) or "").strip().lower()
        if len(phrase) >= 2:
            ents.add(phrase)
    return ents


def term_weights(text: str) -> dict[str, float]:
    """Map each content term of ``text`` to its weight (entities upgraded)."""
    weights = {t: CONTENT_WEIGHT for t in content_terms(text)}
    for t in entity_terms(text):
        weights[t] = ENTITY_WEIGHT
    return weights


# ---------------------------------------------------------------------------
# Passage segmentation
# ---------------------------------------------------------------------------


def segment_passages(text: str) -> list[str]:
    """Split extracted page text into short-paragraph passages.

    Consecutive sentences are grouped until the group would exceed
    ``PASSAGE_MAX_CHARS``; a leftover fragment shorter than
    ``PASSAGE_MIN_CHARS`` is merged into the previous passage (or kept as a
    lone passage if there is no previous one). Deterministic.
    """
    flat = re.sub(r"\s+", " ", text or "").strip()
    if not flat:
        return []
    sentences = [s.strip() for s in _SENTENCE_BOUNDARY_RE.split(flat) if s.strip()]
    passages: list[str] = []
    buf = ""
    for sent in sentences:
        if buf and len(buf) + 1 + len(sent) > PASSAGE_MAX_CHARS:
            passages.append(buf)
            buf = sent
        else:
            buf = f"{buf} {sent}".strip()
    if buf:
        if passages and len(buf) < PASSAGE_MIN_CHARS:
            passages[-1] = f"{passages[-1]} {buf}"
        else:
            passages.append(buf)
    return passages


def expand_context(
    passage: str, sentences: list[str], passage_idx: int, radius: int = 1
) -> str:
    """Expand a passage to include adjacent sentences for pronoun resolution.

    Takes the passage at ``passage_idx`` in ``sentences`` and prepends/appends
    the ``radius`` surrounding sentences. Deterministic. Caps the result at
    ~512 chars pre-tokenizer.
    """
    if radius <= 0:
        return passage
    start = max(0, passage_idx - radius)
    end = min(len(sentences), passage_idx + radius + 1)
    expanded = " ".join(sentences[start:end])
    if len(expanded) > 512:
        # Truncate from the ends, keeping the passage itself intact.
        return passage
    return expanded


def expand_passage_in_text(passage: str, text: str, radius: int = 1) -> str:
    """Expand a passage to include adjacent sentences from the original text.

    Finds ``passage`` in ``text``, determines which sentence it belongs to,
    and includes ``radius`` surrounding sentences. Falls back to the original
    passage if expansion is not possible.
    """
    if radius <= 0 or not passage or not text:
        return passage
    # Split text into sentences
    sentences = [s.strip() for s in _SENTENCE_BOUNDARY_RE.split(text) if s.strip()]
    if not sentences:
        return passage
    # Find which sentence best matches the passage
    best_idx = 0
    best_overlap = 0
    passage_lower = passage.lower()
    for i, sent in enumerate(sentences):
        # Check if this sentence is contained in the passage or vice versa
        if sent.lower() in passage_lower or passage_lower in sent.lower():
            overlap = len(sent)
            if overlap > best_overlap:
                best_overlap = overlap
                best_idx = i
    if best_overlap == 0:
        return passage
    return expand_context(passage, sentences, best_idx, radius)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _coverage(claim_weights: dict[str, float], passage_terms: set[str]) -> float:
    """Weighted fraction of the claim's term weight present in a passage."""
    total = sum(claim_weights.values())
    if total <= 0:
        return 0.0
    hit = sum(w for t, w in claim_weights.items() if t in passage_terms)
    return hit / total


def _clip_snippet(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= SNIPPET_MAX_CHARS:
        return text
    return text[: SNIPPET_MAX_CHARS - 1].rstrip() + "…"


def rank_passages(
    claim: str, passages: list[str], top_k: int = DEFAULT_TOP_K
) -> list[tuple[float, str]]:
    """Rank passages by claim-term coverage; return the top-k as
    ``(score, passage)`` pairs, best first. Ties break on original order
    (deterministic)."""
    weights = term_weights(clean_claim(claim))
    scored: list[tuple[float, int, str]] = []
    for i, passage in enumerate(passages or []):
        score = round(_coverage(weights, content_terms(passage)), 4)
        scored.append((score, i, passage))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [(s, p) for s, _, p in scored[: max(0, top_k)]]


def score_overlap(
    claim: str, passages: list[str], top_k: int = DEFAULT_TOP_K
) -> tuple[float, str]:
    """Best overlap score in ``[0, 1]`` plus the best-matching passage.

    The score is the weighted claim-term coverage of the best passage among
    the top-k ranked candidates (see :func:`rank_passages`). Returns
    ``(0.0, "")`` when the claim has no content terms or no passages are
    given. Pure and deterministic.
    """
    if not term_weights(clean_claim(claim)):
        return 0.0, ""
    ranked = rank_passages(claim, passages, top_k)
    if not ranked:
        return 0.0, ""
    best_score, best_passage = ranked[0]
    return best_score, _clip_snippet(best_passage)


def status_for_score(score: float) -> Status:
    """Map an overlap score to a D3 status using the pinned thresholds."""
    if score >= HIGH_OVERLAP_THRESHOLD:
        return Status.SUPPORTED
    if score >= LOW_OVERLAP_THRESHOLD:
        return Status.AMBIGUOUS
    return Status.UNSUPPORTED


# ---------------------------------------------------------------------------
# Pipeline: reachability tier → overlap tier
# ---------------------------------------------------------------------------


async def _verify_one(
    citation: Citation, use_overlap: bool
) -> tuple[Verdict, dict | None]:
    """Verify a single citation: fetch → tier 1 → (if clean) tier 2.

    Returns ``(verdict, nli_context)`` where ``nli_context`` is
    ``{"claim": str, "passage": str, "marker_locatable": bool}`` when the
    NLI tier has something to score for this citation (a fetched page or
    excerpt with a usable best passage), else ``None`` (tier-1 outcomes,
    JS pages with no extractable text, empty claims).
    """
    try:
        page = await fetch(citation.url)
    except Exception as exc:  # malformed URL etc. → treat as unreachable
        from .fetcher import FetchedPage

        page = FetchedPage(
            url=citation.url, ok=False, error=f"{type(exc).__name__}: {exc}"
        )
    status, notes = classify_reachability(page)
    tier = 1
    score: float | None = None
    evidence = page.text or ""
    nli_context: dict | None = None

    if use_overlap and status is Status.SUPPORTED:
        tier = 2
        excerpt = (citation.excerpt or "").strip()
        if excerpt:
            target, source_label = excerpt, "user-supplied excerpt"
        else:
            target, source_label = page.text, "fetched page"
        # The "marker not locatable" (JS page) rule applies only to fetched
        # pages: a user-supplied excerpt means the caller already located the
        # cited region, so even a short excerpt is matched as-is (D4).
        if not excerpt and len(target) < MIN_EXTRACTABLE_CHARS:
            # Marker not locatable: JS-rendered page / no extractable body.
            status = Status.AMBIGUOUS
            notes.append(
                "citation marker not locatable: fewer than "
                f"{MIN_EXTRACTABLE_CHARS} chars of extractable text "
                "(possible JavaScript-rendered page)"
            )
            evidence = target
        else:
            passages = segment_passages(target)
            ranked = rank_passages(citation.claim, passages)
            if ranked:
                score, best_passage = ranked[0]
            else:
                score, best_passage = 0.0, ""
            status = status_for_score(score)
            notes.append(
                f"overlap tier: score {score:.3f} against {source_label}; "
                f"thresholds: >= {HIGH_OVERLAP_THRESHOLD} supported, "
                f">= {LOW_OVERLAP_THRESHOLD} ambiguous, below unsupported"
            )
            evidence = _clip_snippet(best_passage)
            if ranked:
                # Pass ALL top-k passages (full text, not clipped) to the NLI
                # tier so it can score each and take the max entailment.
                # Also pass the target text so the NLI tier can expand passages
                # with surrounding sentences for pronoun resolution (IMP-2).
                nli_context = {
                    "claim": clean_claim(citation.claim),
                    "passage": best_passage,  # best by term coverage (for evidence)
                    "passages": [p for _, p in ranked],  # all top-k full text
                    "target_text": target,  # original text for context expansion
                    "marker_locatable": True,
                }

    return (
        Verdict(
            citation_id=citation.citation_id,
            url=citation.url,
            status=status,
            tier_reached=tier,
            score=score,
            evidence=evidence,
            notes=notes,
        ),
        nli_context,
    )


async def verify_citations(
    citations: list[Citation], *, use_overlap: bool = True, use_nli: bool = False,
    nli_model: str | None = None,
) -> Report:
    """Verify citations through the full pipeline (D2 tiers 1+2, +3 with NLI).

    Each citation is fetched concurrently, classified at tier 1
    (reachability), and — when the page is reachable with no red flags and
    ``use_overlap`` is true — refined at tier 2 (content overlap).
    ``tier_reached`` is 2 whenever the overlap tier ran. Pass
    ``use_overlap=False`` to reproduce the Phase-1 tier-1-only behaviour.

    With ``use_nli=True`` (Phase 3, D2/D6) the NLI cross-encoder
    (``nli_model`` resolved via the D6 chain) scores every scorable
    (claim, best-passage) pair in one batched forward pass and the D3
    banding produces the final status; ``tier_reached`` is 3 whenever the
    NLI tier ran. Model-load failures raise :class:`citesure.nli.NLIError`
    (fail fast — no silent fallback).
    """
    results = list(
        await asyncio.gather(*(_verify_one(c, use_overlap) for c in citations))
    )
    verdicts: list[Verdict] = [v for v, _ in results]

    if use_nli:
        from .nli import apply_nli_tier, get_nli_model, score_nli_batch_all

        # Load once (lazy download on first use), then score all pairs in a
        # single batched pass — deterministic given the same model+input.
        encoder = get_nli_model(nli_model)
        eligible = [(i, ctx) for i, (_, ctx) in enumerate(results) if ctx]
        if eligible:
            # Build a flat list of (claim, passage) pairs for ALL top-k passages
            # across all citations. Track which citation each pair belongs to.
            flat_pairs = []
            pair_owner = []  # citation index for each pair
            for ci, ctx in eligible:
                target_text = ctx.get("target_text", "")
                for passage in ctx.get("passages", [ctx["passage"]]):
                    # IMP-2: expand passage with adjacent sentences for
                    # pronoun resolution ("It obtains SOTA" → "BERT ... It ...")
                    expanded = expand_passage_in_text(passage, target_text, radius=1)
                    flat_pairs.append((ctx["claim"], expanded))
                    pair_owner.append(ci)

            # Score all pairs in one batched pass
            all_scores = score_nli_batch_all(encoder, flat_pairs)

            # Group by citation and take the max entailment (and its contradiction)
            best_for_citation = {}
            for ci, (ent, con) in zip(pair_owner, all_scores):
                if ci not in best_for_citation or ent > best_for_citation[ci][0]:
                    best_for_citation[ci] = (ent, con)

            # Apply the NLI tier with the best score per citation
            for ci, ctx in eligible:
                if ci in best_for_citation:
                    best_ent, best_con = best_for_citation[ci]
                    verdict = verdicts[ci]
                    final, tier, new_score, notes = apply_nli_tier(
                        verdict.status,
                        list(verdict.notes),
                        nli_score=best_ent,
                        marker_locatable=ctx["marker_locatable"],
                        evidence=verdict.evidence,
                        contradiction=best_con,
                    )
                    verdicts[ci] = Verdict(
                        citation_id=verdict.citation_id,
                        url=verdict.url,
                        status=final,
                        tier_reached=tier,
                        score=new_score,
                        evidence=verdict.evidence,
                        notes=notes,
                    )

    return Report.from_verdicts(verdicts)
