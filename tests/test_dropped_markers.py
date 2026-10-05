"""D6: unresolved citation markers must never be silent (fixed 2026-10-04).

Before this, a numeric marker with no matching URL was dropped inside
``extract_citations`` with a bare ``continue``. The report then showed only the
markers that DID resolve, so a document with three claims and one broken marker
produced a clean-looking report covering two claims — the worst possible
failure mode for a verifier, which is a false sense of coverage.

These tests assert the drop is RECORDED and SURFACED on every channel:
library, human report, and JSON.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from citesure.citations import (
    extract_citations,
    extract_citations_with_drops,
    load_input,
)
from citesure.cli import _build_parser, _cmd_verify
from citesure.models import Report, Status, Verdict
from citesure.report import render_human


MD = """# Notes

The sky is blue [1]. The grass is green [99]. Water is wet [2].

## Sources

1. https://example.invalid/one
2. https://example.invalid/two
"""


class TestExtraction:
    def test_unresolved_marker_is_recorded(self):
        cits, dropped = extract_citations_with_drops(MD)
        assert len(cits) == 2, "the two resolvable markers still extract"
        assert len(dropped) == 1
        assert dropped[0].marker == "[99]"
        assert "no URL" in dropped[0].reason
        assert "grass" in dropped[0].context.lower()

    def test_backwards_compatible_wrapper_returns_only_citations(self):
        """extract_citations() keeps its old signature and behaviour."""
        cits = extract_citations(MD)
        assert len(cits) == 2
        assert all(isinstance(c.url, str) for c in cits)

    def test_all_markers_resolvable_gives_no_drops(self):
        md = "Claim one [1]. Claim two [2].\n\n## Sources\n\n1. https://a.invalid\n2. https://b.invalid\n"
        cits, dropped = extract_citations_with_drops(md)
        assert len(cits) == 2
        assert dropped == []

    def test_drop_is_ordered_and_keeps_the_resolvable_one(self):
        """Only markers absent from the map are dropped, in document order."""
        md = "A [5]. B [7]. C [9].\n\n## Sources\n\n5. https://a.invalid\n"
        cits, dropped = extract_citations_with_drops(md)
        assert [c.citation_id for c in cits] == ["5"]
        assert [d.marker for d in dropped] == ["[7]", "[9]"]

    def test_to_dict_is_json_serialisable(self):
        _c, dropped = extract_citations_with_drops(MD)
        json.dumps([d.to_dict() for d in dropped])


class TestLoadInput:
    def test_markdown_load_input_reports_drops_in_meta(self, tmp_path):
        f = tmp_path / "notes.md"
        f.write_text(MD, encoding="utf-8")
        cits, meta = load_input(str(f))
        assert len(cits) == 2
        assert meta["dropped_markers"], "meta must carry the dropped markers"
        assert meta["dropped_markers"][0]["marker"] == "[99]"

    def test_json_load_input_has_empty_dropped_markers(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text(json.dumps([{"claim": "a", "citation": "https://a.invalid"}]),
                     encoding="utf-8")
        cits, meta = load_input(str(f))
        assert meta["dropped_markers"] == []


class TestSurfacing:
    def _report(self):
        return Report.from_verdicts([
            Verdict(citation_id="1", url="https://example.invalid/one",
                    status=Status.SUPPORTED, tier_reached=2, score=0.9),
        ])

    def test_human_report_has_unresolved_section(self):
        meta = {"format": "markdown",
                "dropped_markers": [{"marker": "[99]",
                                     "reason": "no URL for this marker id",
                                     "context": "grass is green"}]}
        out = render_human(self._report(), meta, 0.8, False, nli_active=False)
        assert "Unresolved citation markers (1)" in out
        assert "[99]" in out
        assert "NOT verified" in out

    def test_human_report_clean_when_nothing_dropped(self):
        out = render_human(self._report(), {"format": "markdown", "dropped_markers": []},
                           0.8, False, nli_active=False)
        assert "Unresolved citation markers" not in out

    def test_cli_warns_on_stderr(self, tmp_path, capsys, monkeypatch):
        f = tmp_path / "notes.md"
        f.write_text(MD, encoding="utf-8")
        monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
        parser = _build_parser()
        args = parser.parse_args(["verify", str(f), "--no-nli"])
        try:
            _cmd_verify(args)
        except SystemExit:
            pass
        err = capsys.readouterr().err
        assert "WARNING unresolved citation marker [99]" in err
        assert "NOT verified" in err
        assert "1 marker(s) could not be resolved" in err

    def test_cli_json_includes_dropped_markers(self, tmp_path, capsys, monkeypatch):
        f = tmp_path / "notes.md"
        f.write_text(MD, encoding="utf-8")
        monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
        parser = _build_parser()
        args = parser.parse_args(["verify", str(f), "--no-nli", "--json"])
        try:
            _cmd_verify(args)
        except SystemExit:
            pass
        payload = json.loads(capsys.readouterr().out)
        assert payload["dropped_markers"], "json consumer must see the gap"
        assert payload["dropped_markers"][0]["marker"] == "[99]"
        assert payload["total"] == 2, "only the resolvable two were verified"