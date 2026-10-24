"""Structural invariants both independent eval sets must satisfy."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO = Path(__file__).resolve().parent.parent
REQUIRED_KEYS = {
    "id", "category", "expected_status", "labeler_confidence", "url", "claim", "notes",
}
VALID_STATUS = {"supported", "unsupported", "unreachable", "paywalled", "ambiguous"}
SETS = [
    "evals/independent_set.json",
    "evals/independent_set_v2.json",
    "evals/independent_set_v3.json",
    "evals/independent_set_v4.json",
]


def _load(rel: str) -> list[dict]:
    path = REPO / rel
    if not path.exists():
        pytest.skip(f"{rel} not present yet")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("rel", SETS)
def test_set_shape_and_labels(rel: str) -> None:
    cases = _load(rel)
    assert cases, f"{rel} is empty"
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), f"{rel}: duplicate ids"
    for c in cases:
        assert REQUIRED_KEYS <= set(c), f"{c['id']}: missing {REQUIRED_KEYS - set(c)}"
        assert c["expected_status"] in VALID_STATUS, f"{c['id']}: bad status"
        assert c["labeler_confidence"] in {"high", "medium", "low"}, c["id"]
        assert c["url"].startswith(("http://", "https://")), c["id"]
        assert c["claim"].strip(), c["id"]


CONTRAST_CATEGORIES = {"verbatim-supported", "negation-flip", "entity-swap"}


def test_v2_url_cap_and_diversity() -> None:
    """v2: at most 3 CONTRAST cases per URL, drawn from many domains.

    The 3-per-URL cap protects the three-way contrast (A/B/C must share one
    source sentence to be diagnostic). It deliberately does NOT cap other
    categories: a partially-true-ambiguous case spans two clauses and is not
    part of any triple, so it may legitimately share a page with a triple.
    """
    cases = _load("evals/independent_set_v2.json")
    contrast = [c for c in cases if c["category"] in CONTRAST_CATEGORIES]
    top = Counter(c["url"] for c in contrast).most_common(1)[0]
    assert top[1] <= 3, f"v2 has {top[1]} contrast cases on {top[0]}"
    assert len({urlparse(c["url"]).netloc for c in cases}) >= 8, "too few domains"


def test_v2_has_three_way_contrast() -> None:
    """Each positive URL must carry 1 supported + >= 2 unsupported cases."""
    cases = _load("evals/independent_set_v2.json")
    by_url: dict[str, list[dict]] = {}
    for c in cases:
        by_url.setdefault(c["url"], []).append(c)
    contrast = 0
    for url, group in by_url.items():
        pos = [c for c in group if c["expected_status"] == "supported"]
        neg = [c for c in group if c["expected_status"] == "unsupported"]
        if pos:
            assert len(neg) >= 2, f"{url}: {len(pos)} supported but only {len(neg)} negatives"
            contrast += 1
    assert contrast >= 25, f"only {contrast} contrast pages; expected >= 25"


def test_v2_avoids_v1_overused_pages() -> None:
    """v2 must not re-concentrate on the pages that dominate v1."""
    cases = _load("evals/independent_set_v2.json")
    banned = {
        "https://docs.python.org/3/whatsnew/3.12.html",
        "https://arxiv.org/abs/1706.03762",
        "https://arxiv.org/abs/1810.04805",
        "https://en.wikipedia.org/wiki/Apollo_11",
        "https://www.nasa.gov/mission/apollo-11/",
    }
    assert not (banned & {c["url"] for c in cases}), "v2 reused a banned v1 page"


def test_sets_do_not_overlap_ids() -> None:
    old = _load("evals/independent_set.json")
    new = _load("evals/independent_set_v2.json")
    assert not ({c["id"] for c in old} & {c["id"] for c in new}), "id collision between sets"


# ---------------------------------------------------------------------------
# v4: the held-out, constructively-labelled set (see evals/AUTHORING_V4.md)
# ---------------------------------------------------------------------------

FAKE_HOST_SUFFIXES = ("example.com", "example.org", "example.invalid")


def _real_hosts(cases: list[dict]) -> set[str]:
    hosts = {urlparse(c["url"]).netloc for c in cases}
    return {h for h in hosts if not h.endswith(FAKE_HOST_SUFFIXES)}


def test_v4_uses_domains_no_earlier_set_touched() -> None:
    """The whole point of v4 is that nobody tuned against these pages."""
    v4 = _load("evals/independent_set_v4.json")
    earlier: list[dict] = []
    for rel in ("evals/independent_set.json", "evals/independent_set_v2.json",
                "evals/independent_set_v3.json"):
        earlier.extend(_load(rel))
    overlap = _real_hosts(v4) & _real_hosts(earlier)
    assert not overlap, f"v4 reuses domain(s) already in v1/v2/v3: {sorted(overlap)}"
    shared = {c["url"] for c in v4} & {c["url"] for c in earlier}
    assert not shared, f"v4 reuses URL(s): {sorted(shared)[:3]}"
    assert len(_real_hosts(v4)) >= 8, f"only {len(_real_hosts(v4))} fresh domains in v4"


def test_v4_claims_agree_with_the_saved_page_text() -> None:
    """A label that contradicts the saved text is a labelling defect, not a model error.

    Mapping-free on purpose: a supported claim must appear verbatim in at least one saved
    page (that is how it was constructed), and an unsupported claim must appear in none
    (the transformation removed the contiguous source span). If either fails, the case's
    label is wrong and every number computed on the set inherits the error.
    """
    cases = _load("evals/independent_set_v4.json")
    text_dir = REPO / "evals" / "page_text_v4"
    texts = {p.name: p.read_text(encoding="utf-8") for p in text_dir.glob("*.txt")}
    assert texts, "no saved page text to check v4 labels against"

    bad: list[str] = []
    for c in cases:
        claim = c["claim"]
        if c["category"] == "verbatim-supported":
            if not any(claim in t for t in texts.values()):
                bad.append(f"{c['id']}: labelled supported, but the claim is not verbatim in any saved page")
        elif c["category"] in CONTRAST_CATEGORIES:
            if any(claim in t for t in texts.values()):
                bad.append(f"{c['id']}: labelled unsupported, but the claim appears verbatim in a saved page")
    assert not bad, "; ".join(bad[:5])
