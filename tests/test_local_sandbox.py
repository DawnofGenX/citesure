"""Local-file sandbox regression tests (D2, fixed 2026-10-04).

Before this, ``citesure`` fetched ANY absolute path or ``file://`` URL. Running
as an MCP server — its primary distribution channel — an LLM holding the tool
could request ``/etc/passwd`` and receive the contents back in its own context,
reproduced through the real pipeline on 2026-10-04.

Policy under test:
  * relative paths and paths under the CWD: ALLOWED
  * absolute paths and ``file://`` outside CWD: DENIED (sandboxed)
  * ``CITECHECK_ALLOW_LOCAL_FILES=1``: lifts the sandbox
  * ``CITECHECK_LOCAL_ROOTS``: allows specific directories
  * the MCP server never opts in; the CLI does.

The autouse fixture in ``tests/conftest.py`` sets ``CITECHECK_ALLOW_LOCAL_FILES``
so the rest of the suite keeps reading tmp files. These tests therefore clear it
explicitly to exercise the DENIED path — which is the whole point.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from citesure.fetcher import (
    ENV_ALLOW_LOCAL_FILES,
    ENV_LOCAL_ROOTS,
    _local_path_allowed,
    _local_path_for,
    fetch,
)


@pytest.fixture(autouse=True)
def _sandbox_active(monkeypatch: pytest.MonkeyPatch):
    """Clear the suite-wide opt-in so the default DENY path is under test."""
    monkeypatch.delenv(ENV_ALLOW_LOCAL_FILES, raising=False)
    monkeypatch.delenv(ENV_LOCAL_ROOTS, raising=False)


class TestPathDecision:
    def test_absolute_path_outside_cwd_is_denied(self):
        allowed, reason = _local_path_allowed(Path("/etc/passwd"))
        assert allowed is False
        assert ENV_ALLOW_LOCAL_FILES in reason  # error names the escape hatch

    def test_file_url_outside_cwd_denied(self):
        assert _local_path_for("file:///etc/passwd") is None

    def test_secret_file_denied(self):
        """The concrete exploit from the audit must stay closed."""
        assert _local_path_for(str(Path.home() / ".ssh" / "id_rsa")) is None

    def test_relative_path_allowed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "page.html").write_text("<p>hi</p>", encoding="utf-8")
        assert _local_path_for("page.html") is not None

    def test_path_under_cwd_allowed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        nested = tmp_path / "sub"
        nested.mkdir()
        target = nested / "page.html"
        target.write_text("<p>hi</p>", encoding="utf-8")
        assert _local_path_for(str(target)) is not None

    def test_allow_env_lifts_sandbox(self, monkeypatch):
        monkeypatch.setenv(ENV_ALLOW_LOCAL_FILES, "1")
        assert _local_path_for("/etc/passwd") is not None

    def test_allow_env_accepts_truthy_spellings(self, monkeypatch):
        for val in ("1", "true", "yes", "on", "all"):
            monkeypatch.setenv(ENV_ALLOW_LOCAL_FILES, val)
            assert _local_path_for("/etc/passwd") is not None, val

    def test_local_roots_env_allows_listed_dir(self, tmp_path, monkeypatch):
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        target = allowed_dir / "page.html"
        target.write_text("<p>hi</p>", encoding="utf-8")
        monkeypatch.setenv(ENV_LOCAL_ROOTS, str(allowed_dir))
        assert _local_path_for(str(target)) is not None

    def test_denial_gives_no_existence_oracle(self):
        """A denied path must look identical whether or not it exists.

        Otherwise a model could probe the filesystem by comparing the error
        text for a path it invented against one that exists.
        """
        existing_denied = _local_path_for("/etc/passwd")
        invented_denied = _local_path_for("/etc/definitely-not-here-zzz9")
        assert existing_denied is None
        assert invented_denied is None


class TestFetchUnderSandbox:
    def test_fetch_of_denied_path_fails_with_explanatory_error(self):
        page = asyncio.run(fetch("/etc/passwd"))
        assert page.ok is False
        assert ENV_ALLOW_LOCAL_FILES in (page.error or "")

    def test_fetch_of_denied_file_url_fails_cleanly(self):
        page = asyncio.run(fetch("file:///etc/passwd"))
        assert page.ok is False
        # Must not be reinterpreted as an HTTP(S) fetch either.
        assert page.error is not None

    def test_fetch_of_relative_file_still_works(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "page.html").write_text(
            "<html><body><p>grounded content</p></body></html>", encoding="utf-8"
        )
        page = asyncio.run(fetch("page.html"))
        assert page.ok is True
        assert "grounded content" in page.text

    def test_allow_any_local_kwarg_lifts_sandbox_for_cli(self, tmp_path):
        """The CLI passes this so a user can verify their own saved HTML."""
        f = tmp_path / "page.html"
        f.write_text("<html><body><p>cli content</p></body></html>", encoding="utf-8")
        page = asyncio.run(fetch(str(f), allow_any_local=True))
        assert page.ok is True
        assert "cli content" in page.text


class TestPipelineSurface:
    def test_mcp_pipeline_cannot_read_etc_passwd(self):
        """End-to-end: the MCP path (no allow_any_local) must be closed."""
        from citesure.citations import _citations_from_json
        from citesure.reachability import verify_citations

        cits = _citations_from_json(
            [{"claim": "root password hash", "citation": "/etc/passwd"}]
        )
        report = asyncio.run(verify_citations(cits, use_overlap=True, use_nli=False))
        blob = report.to_dict()
        rendered = str(blob)
        assert "root:x:0:0" not in rendered, "leaked /etc/passwd contents"
        assert ENV_ALLOW_LOCAL_FILES in rendered, "refusal reason not surfaced"

    def test_cli_pipeline_can_still_read_local_file(self, tmp_path):
        """The CLI keeps its legacy behaviour via allow_any_local=True."""
        from citesure.citations import _citations_from_json
        from citesure.reachability import verify_citations

        f = tmp_path / "page.html"
        f.write_text(
            "<html><body><p>Python 3.12.0 was released on October 2, 2023. "
            "It brings a new interactive debugger.</p></body></html>",
            encoding="utf-8",
        )
        cits = _citations_from_json(
            [{"claim": "Python 3.12 was released on October 2, 2023.", "citation": str(f)}]
        )
        report = asyncio.run(
            verify_citations(
                cits, use_overlap=True, use_nli=False, allow_any_local=True
            )
        )
        assert report.total == 1
        assert "released on October 2, 2023" in str(report.to_dict())