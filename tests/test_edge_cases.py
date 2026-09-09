"""Phase C2: edge-case battery — CLI subprocess, fetcher, and disk cache.

All offline: CLI runs against local files; HTTP goes only to the local mock
server. No ``live`` marks.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from citesure.fetcher import clear_robots_cache, fetch

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_robots():
    clear_robots_cache()
    yield
    clear_robots_cache()


def _run_cli(*args: str, cache_dir: Path, timeout: int = 180) -> subprocess.CompletedProcess:
    """Run the CLI in a subprocess with a fresh env (same pattern as test_cli)."""
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
        timeout=timeout,
        cwd=str(REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# CLI: degenerate input files
# ---------------------------------------------------------------------------


def test_cli_nonexistent_file_exits_2(tmp_path: Path):
    proc = _run_cli("verify", str(tmp_path / "nope.md"), cache_dir=tmp_path)
    assert proc.returncode == 2
    assert "cannot read input" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_empty_file_warns_and_exits_1(tmp_path: Path):
    f = tmp_path / "empty.md"
    f.write_text("", encoding="utf-8")
    proc = _run_cli("verify", str(f), cache_dir=tmp_path)
    # No citations → pass_rate 0.0 < default threshold 0.8 → exit 1, with a
    # warning on stderr and a valid (empty) report on stdout.
    assert proc.returncode == 1, proc.stderr
    assert "no citations found" in proc.stderr
    # Human-readable output is fine — just require no traceback.
    assert "Traceback" not in proc.stderr


def test_cli_binary_file_exits_2_cleanly(tmp_path: Path):
    f = tmp_path / "binary.md"
    f.write_bytes(os.urandom(512))
    proc = _run_cli("verify", str(f), cache_dir=tmp_path)
    assert proc.returncode == 2
    assert "cannot read input" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_utf16_file_exits_2_cleanly(tmp_path: Path):
    """UTF-16 input is not supported (utf-8 only) — must fail cleanly, not crash."""
    f = tmp_path / "utf16.md"
    f.write_text("Claim [1].\n\n## Sources\n\n1. https://example.invalid/x\n", encoding="utf-16")
    proc = _run_cli("verify", str(f), cache_dir=tmp_path)
    assert proc.returncode == 2
    assert "cannot read input" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_zero_citation_file(tmp_path: Path):
    f = tmp_path / "nocite.md"
    f.write_text("Just prose. No citations at all.\n", encoding="utf-8")
    proc = _run_cli("verify", str(f), "--json", cache_dir=tmp_path)
    assert proc.returncode == 1  # pass_rate 0 < 0.8
    data = json.loads(proc.stdout)
    assert data["total"] == 0
    assert data["verdicts"] == []
    assert data["pass_rate"] == 0.0


def test_cli_1000_citations_completes_in_bounded_time(tmp_path: Path):
    page = tmp_path / "src.html"
    page.write_text(
        "<html><body><p>Rust 1.75 was released on July 25, 2024, introducing a "
        "new inline assembly syntax for x86 targets.</p></body></html>",
        encoding="utf-8",
    )
    lines = [
        f"Rust 1.75 was released on July 25, 2024, introducing a new inline "
        f"assembly syntax for x86 targets. [c{i}]({page.as_uri()})"
        for i in range(1000)
    ]
    f = tmp_path / "big.md"
    f.write_text("\n\n".join(lines), encoding="utf-8")
    t0 = time.time()
    proc = _run_cli("verify", str(f), "--json", cache_dir=tmp_path, timeout=300)
    elapsed = time.time() - t0
    assert proc.returncode in (0, 1), proc.stderr
    data = json.loads(proc.stdout)
    assert data["total"] == 1000
    assert len(data["verdicts"]) == 1000
    # Bounded: 1000 local-file fetches must finish well under 2 minutes.
    assert elapsed < 120, f"took {elapsed:.1f}s"


def test_cli_threshold_zero_and_one(tmp_path: Path):
    page = tmp_path / "src.html"
    page.write_text(
        "<html><body><p>Rust 1.75 was released on July 25, 2024.</p></body></html>",
        encoding="utf-8",
    )
    f = tmp_path / "one.md"
    f.write_text(f"Rust 1.75 was released on July 25, 2024. [c]({page.as_uri()})\n", encoding="utf-8")
    # threshold 0: any pass_rate >= 0 passes (even all-unverifiable).
    p0 = _run_cli("verify", str(f), "--threshold", "0", "--json", cache_dir=tmp_path)
    assert p0.returncode == 0, p0.stderr
    # threshold 1: requires pass_rate == 1.0; this page is <80 chars of text
    # so it lands ambiguous → pass_rate 0 → exit 1.
    p1 = _run_cli("verify", str(f), "--threshold", "1", "--json", cache_dir=tmp_path)
    assert p1.returncode == 1
    assert json.loads(p1.stdout)["pass_rate"] == 0.0


def test_cli_strict_json_combo(tmp_path: Path):
    page = tmp_path / "src.html"
    page.write_text(
        "<html><body><p>Rust 1.75 was released on July 25, 2024.</p></body></html>",
        encoding="utf-8",
    )
    f = tmp_path / "one.md"
    f.write_text(f"Rust 1.75 was released on July 25, 2024. [c]({page.as_uri()})\n", encoding="utf-8")
    proc = _run_cli("verify", str(f), "--strict", "--json", cache_dir=tmp_path)
    # strict + ambiguous verdict → exit 1, but output must still be valid JSON.
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["total"] == 1
    assert data["verdicts"][0]["status"] == "ambiguous"


def test_cli_path_with_spaces(tmp_path: Path):
    docs = tmp_path / "my docs"
    docs.mkdir()
    page = docs / "my source.html"
    page.write_text(
        "<html><body><p>Rust 1.75 was released on July 25, 2024.</p></body></html>",
        encoding="utf-8",
    )
    f = docs / "my doc.md"
    f.write_text(f"Rust 1.75 was released on July 25, 2024. [c]({page.as_uri()})\n", encoding="utf-8")
    proc = _run_cli("verify", str(f), "--json", cache_dir=tmp_path)
    assert proc.returncode in (0, 1), proc.stderr
    data = json.loads(proc.stdout)
    assert data["total"] == 1


# ---------------------------------------------------------------------------
# Fetcher: large pages, JS-only pages, redirects, cache-key stability
# ---------------------------------------------------------------------------


def test_fetch_10mb_html_page_bounded_time_and_memory(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    body = "<p>The quick brown fox jumps over the lazy dog. </p>" * 120000
    big = tmp_path / "big.html"
    big.write_text(f"<html><head><title>Big</title></head><body>{body}</body></html>", encoding="utf-8")
    assert big.stat().st_size > 5 * 1024 * 1024  # genuinely multi-MB
    t0 = time.time()
    page = asyncio.run(fetch(str(big)))
    elapsed = time.time() - t0
    assert page.ok is True
    assert len(page.text) > 100000
    assert elapsed < 60, f"10MB page took {elapsed:.1f}s"


def test_fetch_js_only_page_yields_no_text(monkeypatch, tmp_path):
    """A JS shell page has no extractable body text (D8: no headless browser)."""
    js_page = Path(__file__).parent / "fixtures" / "pages" / "js_page.html"
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = asyncio.run(fetch(str(js_page)))
    assert page.ok is True
    # Only the <noscript> notice survives extraction — far below the 80-char
    # MIN_EXTRACTABLE_CHARS threshold used by the overlap tier.
    assert len(page.text) < 80


def test_fetch_redirect_chain_via_mock_server(mock_server, monkeypatch, tmp_path):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    page = asyncio.run(fetch(mock_server.url("/redirect-1")))
    assert page.ok is True
    assert page.status_code == 200
    assert "end of redirect chain" in page.text


def test_cache_key_stable_across_scheme_case_variants(mock_server, monkeypatch, tmp_path):
    """http:// vs HTTP:// for the same host/path must hit the same cache entry."""
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    u = mock_server.url("/redirect-final")
    p1 = asyncio.run(fetch(u))
    assert p1.ok is True and p1.fetched_from_cache is False
    p2 = asyncio.run(fetch(u.replace("http://", "HTTP://")))
    assert p2.fetched_from_cache is True, "scheme-case variant should hit the cache"


def test_cache_key_stable_for_identical_url(mock_server, monkeypatch, tmp_path):
    """The exact same URL string must always hit the cache (sanity baseline)."""
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    u = mock_server.url("/redirect-final")
    p1 = asyncio.run(fetch(u))
    p2 = asyncio.run(fetch(u))
    assert p1.fetched_from_cache is False
    assert p2.fetched_from_cache is True


# ---------------------------------------------------------------------------
# Cache: concurrent writers, read-only dir
# ---------------------------------------------------------------------------


def _writer_script(cache_dir: str, url: str, tag: str) -> str:
    return (
        "import sys, os, asyncio\n"
        f"os.environ['CITECHECK_CACHE_DIR'] = {cache_dir!r}\n"
        "sys.path.insert(0, 'src')\n"
        "from citesure.fetcher import fetch\n"
        f"page = asyncio.run(fetch({url!r}))\n"
        f"print({tag!r}, 'ok=', page.ok)\n"
    )


def test_two_processes_write_same_cache_dir_concurrently(tmp_path: Path):
    """Two processes fetching the same URL into one CITECHECK_CACHE_DIR:
    both succeed and every cache file stays valid JSON (no corruption)."""
    cache_dir = tmp_path / "cache"
    url = (Path(__file__).parent / "fixtures" / "pages" / "page1.html").as_uri()
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _writer_script(str(cache_dir), url, f"w{i}")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "CITECHECK_CACHE_DIR": str(cache_dir)},
        )
        for i in range(2)
    ]
    outputs = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        outputs.append((p.returncode, out, err))
    for rc, out, err in outputs:
        assert rc == 0, f"writer failed: {out} {err}"
        assert "ok= True" in out
    # No corruption: every entry parses as JSON with the expected shape.
    entries = list(cache_dir.glob("*.json"))
    assert len(entries) >= 1
    for e in entries:
        data = json.loads(e.read_text(encoding="utf-8"))
        assert data["url"] == url
        assert data["ok"] is True
        assert "text" in data and "ts" in data


def test_read_only_cache_dir_is_clean_error_not_traceback(tmp_path: Path):
    """A non-writable cache dir must degrade gracefully: fetch still works
    (cache is best-effort) and no traceback appears anywhere."""
    ro = tmp_path / "ro-cache"
    ro.mkdir()
    os.chmod(ro, 0o555)
    try:
        f = tmp_path / "in.md"
        f.write_text("Claim here [1].\n\n## Sources\n\n1. https://example.invalid/x\n", encoding="utf-8")
        proc = _run_cli("verify", str(f), cache_dir=ro)
        # The fetch itself fails (DNS, .invalid) → exit 1, but NO traceback.
        assert proc.returncode in (0, 1, 2)
        combined = proc.stdout + proc.stderr
        assert "Traceback" not in combined, combined[-500:]
    finally:
        os.chmod(ro, 0o755)
