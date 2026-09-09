"""Phase C1: hypothesis fuzzing of every extractor in src/citesure/citations.py.

Invariants (per brief):
* extractors NEVER raise on arbitrary input (ValueError is the documented
  controlled error for malformed structured JSON — anything else is a bug);
* results always have valid shapes (list of Citation with str fields);
* well-formed inputs round-trip: extracted count == input count.

Fully offline. max_examples=300 per test as specified.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_SUPPRESS = list(HealthCheck)

from citesure.citations import Citation, _citations_from_json, extract_citations

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Arbitrary text: unicode, newlines, markdown-ish punctuation, control chars.
TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),  # no surrogates
    min_size=0,
    max_size=400,
)

# Markdown-flavored text: brackets, parens, hashes, digits — the shapes the
# regexes actually care about.
MARKDOWN = st.from_regex(
    r"(?:[A-Za-z0-9 .!?#*\[\]()\-_/]{0,40}\n?){0,20}", fullmatch=True
)

# JSON values of arbitrary depth (no NaN/Inf so json.dumps stays valid).
JSON_VALUE = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-10**6, max_value=10**6),
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(max_size=50),
        st.lists(st.none(), max_size=5),
        st.dictionaries(st.text(max_size=10), st.none(), max_size=5),
    ),
    lambda x: st.one_of(
        st.lists(x, max_size=6),
        st.dictionaries(st.text(max_size=10), x, max_size=6),
    ),
    max_leaves=12,
)


def _assert_valid_shape(out) -> None:
    assert isinstance(out, list), f"expected list, got {type(out)}"
    for c in out:
        assert isinstance(c, Citation), f"expected Citation, got {type(c)}"
        assert isinstance(c.citation_id, str)
        assert isinstance(c.url, str)
        assert isinstance(c.claim, str)
        assert c.source_text is None or isinstance(c.source_text, str)
        assert c.excerpt is None or isinstance(c.excerpt, str)


# ---------------------------------------------------------------------------
# extract_citations (markdown path)
# ---------------------------------------------------------------------------


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(TEXT)
def test_extract_citations_never_raises_on_arbitrary_text(text: str):
    out = extract_citations(text)
    _assert_valid_shape(out)


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(MARKDOWN)
def test_extract_citations_never_raises_on_markdown(text: str):
    out = extract_citations(text)
    _assert_valid_shape(out)


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(TEXT, st.dictionaries(st.integers(min_value=0, max_value=999), TEXT, max_size=10))
def test_extract_citations_with_arbitrary_url_map(text: str, url_map):
    # url_map keys are coerced to str() inside extract_citations; arbitrary
    # int/str keys and values must not crash it.
    out = extract_citations(text, url_map)
    _assert_valid_shape(out)


@settings(
    max_examples=300, deadline=None, suppress_health_check=_SUPPRESS
)
@given(JSON_VALUE)
def test_extract_citations_on_json_serialized_garbage(value):
    """Feeding a JSON-serialized arbitrary value as *markdown* must not raise."""
    try:
        blob = json.dumps(value)
    except (TypeError, ValueError):
        return  # unserializable → skip; json.dumps itself is not our code
    out = extract_citations(blob)
    _assert_valid_shape(out)


@settings(
    max_examples=300, deadline=None, suppress_health_check=_SUPPRESS
)
@given(st.lists(st.integers(min_value=0, max_value=999), min_size=1, max_size=20))
def test_numeric_markers_round_trip(nums: list[int]):
    """Well-formed numeric markers with a complete url_map: one Citation per marker."""
    urls = {str(n): f"https://ex.example/{n}" for n in nums}
    parts = [f"Claim {i} is true [{n}]." for i, n in enumerate(nums)]
    text = "\n\n".join(parts)
    out = extract_citations(text, urls)
    _assert_valid_shape(out)
    assert len(out) == len(nums), (len(out), len(nums))
    assert [c.citation_id for c in out] == [str(n) for n in nums]
    assert all(c.url == urls[c.citation_id] for c in out)


@settings(
    max_examples=300, deadline=None, suppress_health_check=_SUPPRESS
)
@given(
    st.lists(
        st.from_regex(r"[A-Za-z0-9 -]{1,20}", fullmatch=True).filter(lambda s: s.strip() != ""),
        min_size=1,
        max_size=10,
    )
)
def test_inline_links_round_trip(labels: list[str]):
    """Well-formed inline links with plain labels: one Citation per link."""
    parts = []
    for i, label in enumerate(labels):
        url = f"https://ex.example/{i}"
        parts.append(f"See [{label}]({url}) for details.")
    text = "\n\n".join(parts)
    out = extract_citations(text)
    _assert_valid_shape(out)
    assert len(out) == len(labels), (len(out), len(labels), labels)
    assert [c.citation_id for c in out] == [l.strip() for l in labels]


# ---------------------------------------------------------------------------
# _citations_from_json (structured JSON path)
# ---------------------------------------------------------------------------


@settings(max_examples=300, deadline=None, suppress_health_check=_SUPPRESS)
@given(JSON_VALUE)
def test_json_extractor_never_unexpectedly_raises(value):
    """Arbitrary JSON values: either a valid list of Citations or ValueError."""
    try:
        out = _citations_from_json(value)
    except ValueError:
        return  # documented controlled error
    _assert_valid_shape(out)


@settings(
    max_examples=300, deadline=None, suppress_health_check=_SUPPRESS
)
@given(
    st.lists(
        st.fixed_dictionaries(
            {
                "claim": st.text(min_size=1, max_size=100),
                "citation": st.from_regex(r"https://ex\.example/[a-z0-9]{1,8}", fullmatch=True),
            }
        ),
        min_size=1,
        max_size=20,
    )
)
def test_json_well_formed_round_trip(items: list[dict]):
    """Well-formed {claim, citation} lists: extracted count == input count."""
    out = _citations_from_json(items)
    _assert_valid_shape(out)
    assert len(out) == len(items)
    for c, item in zip(out, items):
        assert c.claim == item["claim"]
        assert c.url == item["citation"]


@settings(
    max_examples=300, deadline=None, suppress_health_check=_SUPPRESS
)
@given(
    st.lists(
        st.fixed_dictionaries(
            {
                "claim": st.text(min_size=1, max_size=50),
                "citation": st.text(min_size=1, max_size=8),
            }
        ),
        min_size=1,
        max_size=10,
    ),
    st.dictionaries(st.text(min_size=1, max_size=8), st.text(min_size=1, max_size=40), max_size=10),
)
def test_json_with_sources_map(items: list[dict], sources: dict[str, str]):
    """Arbitrary id-like citations + sources map: valid output or ValueError only."""
    data = {"citations": items, "sources": sources}
    try:
        out = _citations_from_json(data)
    except ValueError:
        return
    _assert_valid_shape(out)
    assert len(out) == len(items)


@pytest.mark.parametrize(
    "bad",
    [
        {"no_citations_key": True},
        {"citations": "not a list"},
        {"citations": [{"claim": "only claim"}]},
        {"citations": [{"claim": "x", "citation": "{{MOCK}}"}]},
        {"citations": [{"claim": "x", "citation": "not-a-url-or-id"}]},
        {"citations": [{"claim": "x", "citation": "id1"}], "sources": "not-a-dict"},
        42,
        "a string",
        None,
    ],
)
def test_json_malformed_shapes_raise_value_error(bad):
    with pytest.raises(ValueError):
        _citations_from_json(bad)
