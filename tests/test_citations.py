"""Unit tests for citation extraction (D1) — all three input forms."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from citesure.citations import Citation, extract_citations, load_input

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Form A: numeric [n] markers + Sources section
# ---------------------------------------------------------------------------


def test_numeric_markers_with_numbered_sources_section():
    text = (
        "# Doc\n\n"
        "The sky is blue [1]. Water is wet [2].\n\n"
        "## Sources\n\n"
        "1. https://example.com/sky\n"
        "2) https://example.com/water\n"
    )
    cits = extract_citations(text)
    assert [(c.citation_id, c.url) for c in cits] == [
        ("1", "https://example.com/sky"),
        ("2", "https://example.com/water"),
    ]


def test_numeric_markers_with_reference_style_definitions():
    text = (
        "Claim one [1].\n\n"
        "## References\n\n"
        "[1]: https://example.com/one\n"
        "[2]: https://example.com/two\n"
    )
    cits = extract_citations(text)
    assert len(cits) == 1
    assert cits[0].citation_id == "1"
    assert cits[0].url == "https://example.com/one"


def test_explicit_url_map_argument():
    text = "Alpha [1] and beta [2]."
    cits = extract_citations(text, {"1": "https://a.example", "2": "https://b.example"})
    assert [(c.citation_id, c.url) for c in cits] == [
        ("1", "https://a.example"),
        ("2", "https://b.example"),
    ]


def test_explicit_url_map_overrides_section():
    text = (
        "Fact [1].\n\n"
        "## Sources\n\n"
        "1. https://section.example\n"
    )
    cits = extract_citations(text, {"1": "https://explicit.example"})
    assert cits[0].url == "https://explicit.example"


def test_missing_id_in_url_map_is_skipped():
    # Documented policy: markers whose id is absent from the map are skipped
    # (no Citation emitted), so callers can detect the gap by count.
    text = "Known [1]. Unknown [99]. Another known [2].\n\n## Sources\n\n1. https://a.example\n2. https://b.example\n"
    cits = extract_citations(text)
    assert [c.citation_id for c in cits] == ["1", "2"]


# ---------------------------------------------------------------------------
# Form B: inline [label](url) links
# ---------------------------------------------------------------------------


def test_inline_link_form():
    text = "See [the docs](https://docs.example/api) for details."
    cits = extract_citations(text)
    assert len(cits) == 1
    assert cits[0].citation_id == "the docs"
    assert cits[0].url == "https://docs.example/api"
    assert cits[0].claim == "See [the docs](https://docs.example/api) for details."


def test_inline_link_and_numeric_marker_coexist():
    text = (
        "First fact [1]. Second fact [link label](https://l.example).\n\n"
        "## Sources\n\n1. https://n.example\n"
    )
    cits = extract_citations(text)
    assert [(c.citation_id, c.url) for c in cits] == [
        ("1", "https://n.example"),
        ("link label", "https://l.example"),
    ]


def test_anchor_only_links_are_ignored():
    text = "Jump to [the top](#intro) now."
    assert extract_citations(text) == []


# ---------------------------------------------------------------------------
# Form C: structured JSON
# ---------------------------------------------------------------------------


def test_json_list_of_pairs(tmp_path: Path):
    p = tmp_path / "in.json"
    p.write_text(
        json.dumps(
            [
                {"claim": "C1", "citation": "https://x.example/1"},
                {"claim": "C2", "citation": "https://x.example/2"},
            ]
        ),
        encoding="utf-8",
    )
    cits, meta = load_input(str(p))
    assert meta["format"] == "json"
    assert [c.claim for c in cits] == ["C1", "C2"]
    assert [c.url for c in cits] == ["https://x.example/1", "https://x.example/2"]
    assert [c.citation_id for c in cits] == ["c1", "c2"]


def test_json_wrapper_with_sources_map(tmp_path: Path):
    p = tmp_path / "in.json"
    p.write_text(
        json.dumps(
            {
                "citations": [
                    {"claim": "C1", "citation": "src-a"},
                    {"claim": "C2", "citation": "https://direct.example"},
                ],
                "sources": {"src-a": "https://mapped.example/a"},
            }
        ),
        encoding="utf-8",
    )
    cits, meta = load_input(str(p))
    assert meta["format"] == "json"
    assert cits[0].url == "https://mapped.example/a"
    assert cits[1].url == "https://direct.example"


def test_json_bare_relative_path_is_anchored(tmp_path: Path):
    p = tmp_path / "in.json"
    p.write_text(
        json.dumps([{"claim": "C1", "citation": "pages/local.html"}]),
        encoding="utf-8",
    )
    cits, _ = load_input(str(p))
    assert cits[0].url == str((tmp_path / "pages" / "local.html").resolve())


def test_json_unknown_id_raises(tmp_path: Path):
    p = tmp_path / "in.json"
    p.write_text(
        json.dumps([{"claim": "C1", "citation": "nope"}]), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="sources"):
        load_input(str(p))


def test_json_malformed_item_raises(tmp_path: Path):
    p = tmp_path / "in.json"
    p.write_text(json.dumps([{"claim": "only claim"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="citation"):
        load_input(str(p))


# ---------------------------------------------------------------------------
# Claim-unit selection (D4)
# ---------------------------------------------------------------------------


def test_claim_is_sentence_containing_marker():
    text = "First sentence. Second sentence [1]. Third sentence.\n\n## Sources\n\n1. https://e.example\n"
    cits = extract_citations(text)
    assert cits[0].claim == "Second sentence [1]."


def test_claim_falls_back_to_paragraph_without_boundary():
    # No sentence-ending punctuation at all → whole paragraph is the claim.
    text = "No punctuation here at all [1]\n\n## Sources\n\n1. https://e.example\n"
    cits = extract_citations(text)
    assert cits[0].claim == "No punctuation here at all [1]"


def test_marker_at_end_of_paragraph():
    text = "The final assertion stands [7]\n\n## Sources\n\n7. https://e.example\n"
    cits = extract_citations(text)
    assert cits[0].claim == "The final assertion stands [7]"


def test_multiple_markers_in_one_sentence_share_claim():
    text = "Both facts hold [1] and [2] together.\n\n## Sources\n\n1. https://a.example\n2. https://b.example\n"
    cits = extract_citations(text)
    assert len(cits) == 2
    assert cits[0].claim == cits[1].claim == "Both facts hold [1] and [2] together."


def test_markers_in_different_sentences_get_distinct_claims():
    text = (
        "One thing is true [1]. Another is false [2].\n\n"
        "## Sources\n\n1. https://a.example\n2. https://b.example\n"
    )
    cits = extract_citations(text)
    assert cits[0].claim == "One thing is true [1]."
    assert cits[1].claim == "Another is false [2]."


# ---------------------------------------------------------------------------
# load_input auto-detection + bundled fixture
# ---------------------------------------------------------------------------


def test_load_input_detects_markdown():
    cits, meta = load_input(str(FIXTURES / "sample.md"))
    assert meta["format"] == "markdown"
    assert len(cits) == 5
    urls = [c.url for c in cits]
    # Bare relative paths are anchored to the fixture directory.
    assert all(u.startswith("/") for u in urls[:4])
    assert any(".invalid/" in u for u in urls)


def test_load_input_detects_json_fixture():
    cits, meta = load_input(str(FIXTURES / "sample.json"))
    assert meta["format"] == "json"
    assert len(cits) == 3
    assert cits[0].url.endswith("pages/page1.html")


def test_load_input_missing_file_raises():
    with pytest.raises(OSError):
        load_input("/nonexistent/nope.md")


def test_citation_dataclass_defaults():
    c = Citation(citation_id="1", url="https://e.example", claim="x")
    assert c.source_text is None
