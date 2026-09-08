"""CLI tests for ``citesure verify`` — run offline against bundled fixtures."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from citesure.cli import main
from citesure.models import Report, Status, Verdict
from citesure.report import _verdict_label, exit_code, render_human

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_MD = FIXTURES / "sample.md"
SAMPLE_JSON = FIXTURES / "sample.json"


def _run_cli(*args: str, cache_dir: Path) -> subprocess.CompletedProcess:
    """Run the installed console script in a subprocess (fresh env)."""
    env = {
        "PATH": "/usr/bin:/bin",
        "CITECHECK_CACHE_DIR": str(cache_dir),
        "HOME": str(cache_dir / "home"),
    }
    return subprocess.run(
        [sys.executable, "-m", "citesure.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# Exit-code semantics (D3): 0 iff pass_rate >= threshold
# ---------------------------------------------------------------------------


def test_verify_sample_md_passes_default_threshold(tmp_path: Path):
    proc = _run_cli("verify", str(SAMPLE_MD), cache_dir=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "pass_rate: 0.8000" in proc.stdout
    assert "Decision: PASS" in proc.stdout


def test_verify_sample_md_fails_high_threshold(tmp_path: Path):
    proc = _run_cli("verify", str(SAMPLE_MD), "--threshold", "0.9", cache_dir=tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Decision: FAIL" in proc.stdout


def test_verify_json_input(tmp_path: Path):
    proc = _run_cli(
        "verify", str(SAMPLE_JSON), "--json", "--threshold", "0.5", cache_dir=tmp_path
    )
    assert proc.returncode == 0, proc.stderr
    # 2 of 3 supported → pass_rate 0.6667 >= 0.5
    data = json.loads(proc.stdout)
    assert data["pass_rate"] == pytest.approx(0.6667)


# ---------------------------------------------------------------------------
# --json output
# ---------------------------------------------------------------------------


def test_json_output_shape(tmp_path: Path):
    proc = _run_cli("verify", str(SAMPLE_MD), "--json", cache_dir=tmp_path)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    for key in ("total", "supported", "unsupported", "unverifiable", "pass_rate", "verdicts"):
        assert key in data
    assert data["total"] == 5
    assert data["supported"] == 4
    assert data["unverifiable"] == 1
    assert data["pass_rate"] == pytest.approx(0.8)
    statuses = {v["status"] for v in data["verdicts"]}
    assert statuses == {"supported", "unreachable"}
    for v in data["verdicts"]:
        # Tier-2 overlap ran on the reachable pages; the dead URL stopped at
        # tier 1.
        if v["status"] == "supported":
            assert v["tier_reached"] == 2
            assert v["score"] is not None
        else:
            assert v["tier_reached"] == 1
            assert v["score"] is None
        assert len(v["evidence"]) <= 300


# ---------------------------------------------------------------------------
# --strict and --nli flags
# ---------------------------------------------------------------------------


def test_strict_flag_accepted_and_relabels_ambiguous(tmp_path: Path):
    # No ambiguous verdicts in the fixture, but the flag must be accepted.
    proc = _run_cli("verify", str(SAMPLE_MD), "--strict", cache_dir=tmp_path)
    assert proc.returncode == 0, proc.stderr

    report = Report.from_verdicts(
        [
            Verdict("a", "https://e.example/a", Status.SUPPORTED, 1),
            Verdict("b", "https://e.example/b", Status.AMBIGUOUS, 1),
        ]
    )
    assert _verdict_label(report.verdicts[1], strict=False) == "UNVERIFIABLE"
    assert _verdict_label(report.verdicts[1], strict=True) == "FAIL (strict)"
    # Phase-2 strict semantics: ambiguous counts as FAILURE — exit 0 only if
    # pass_rate >= threshold AND no ambiguous AND no unsupported.
    assert exit_code(report, threshold=0.5, strict=False) == 0  # default: math only
    assert exit_code(report, threshold=0.5, strict=True) == 1  # ambiguous fails
    assert exit_code(report, threshold=0.6, strict=True) == 1


def test_strict_passes_when_no_ambiguous_or_unsupported():
    report = Report.from_verdicts(
        [
            Verdict("a", "https://e.example/a", Status.SUPPORTED, 2),
            Verdict("b", "https://e.example/b", Status.UNREACHABLE, 1),
        ]
    )
    # pass_rate 0.5 >= 0.5, no ambiguous/unsupported → strict passes.
    assert exit_code(report, threshold=0.5, strict=True) == 0
    # ...but a single unsupported flips it.
    report2 = Report.from_verdicts(
        [
            Verdict("a", "https://e.example/a", Status.SUPPORTED, 2),
            Verdict("b", "https://e.example/b", Status.UNSUPPORTED, 2),
        ]
    )
    assert exit_code(report2, threshold=0.5, strict=True) == 1


def test_md_flag_writes_markdown_file(tmp_path: Path):
    out_file = tmp_path / "report.md"
    rc = main(
        ["verify", str(SAMPLE_MD), "--md", str(out_file), "--cache-dir", str(tmp_path)]
    )
    assert rc == 0
    text = out_file.read_text(encoding="utf-8")
    assert "# citesure verification report" in text
    assert "## Summary" in text


def test_nli_flags_accepted_with_notice(tmp_path: Path, capsys):
    rc = main(
        ["verify", str(SAMPLE_MD), "--nli", "--nli-model", "some/model",
         "--cache-dir", str(tmp_path)]
    )
    err = capsys.readouterr().err
    assert "Phase 3" in err
    assert rc == 0  # same report as without --nli


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_missing_file_exits_2(tmp_path: Path):
    proc = _run_cli("verify", str(tmp_path / "nope.md"), cache_dir=tmp_path)
    assert proc.returncode == 2
    assert "cannot read input" in proc.stderr


def test_malformed_json_exits_2(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"claim": "no citation key"}', encoding="utf-8")
    proc = _run_cli("verify", str(bad), cache_dir=tmp_path)
    assert proc.returncode == 2
    assert "cannot read input" in proc.stderr


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_render_human_contains_summary():
    report = Report.from_verdicts(
        [Verdict("1", "https://e.example/1", Status.SUPPORTED, 2, score=0.9, evidence="ok")]
    )
    out = render_human(report, {"format": "markdown"}, threshold=0.8, strict=False)
    assert "PASS" in out
    assert "- total: 1" in out
    assert "- supported: 1" in out
    assert "score=0.900" in out
    assert "Decision: PASS" in out


def test_exit_code_boundary():
    report = Report.from_verdicts(
        [Verdict("1", "u", Status.SUPPORTED, 1), Verdict("2", "u", Status.UNREACHABLE, 1)]
    )
    assert report.pass_rate == 0.5
    assert exit_code(report, 0.5, strict=False) == 0  # >= threshold passes
    assert exit_code(report, 0.51, strict=False) == 1
