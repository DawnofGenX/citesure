"""Shared pytest fixtures for the citesure test suite."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Make ``tests.fixtures.mock_server`` importable regardless of how pytest is
# invoked (the repo root is not a package).
_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR.parent))

from tests.fixtures.mock_server import mock_server  # noqa: E402,F401  (re-exported fixture)


# ---------------------------------------------------------------------------
# Local-file sandbox opt-in for the test suite
#
# Since 2026-10-04 the fetcher sandboxes local-file reads: relative paths and
# paths under the CWD are allowed, but absolute paths and ``file://`` URLs
# outside the CWD are refused unless the caller opts in. This is the fix for
# the "MCP server reads /etc/passwd" finding (D2). pytest's ``tmp_path`` is
# outside the repo CWD, so every test that reads a fixture/tmp file would
# otherwise be testing the refusal path instead of its real subject.
#
# The suite therefore opts IN via an env var - deliberately, not implicitly:
# it keeps testing fetch/score behaviour, while the refusal behaviour itself
# is asserted explicitly in tests/test_local_sandbox.py. An autouse fixture is
# the right place because it applies uniformly and is visible in one diff.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _allow_local_files_in_tests(monkeypatch: pytest.MonkeyPatch):
    """Let the suite read local files outside the repo CWD."""
    monkeypatch.setenv("CITECHECK_ALLOW_LOCAL_FILES", "1")


# --------------------------------------------------------------------------
# Optional-ML-stack handling
#
# torch/transformers live in the ``nli`` extra, NOT in the required deps, so a
# default ``pip install citesure`` (and CI, which installs ``.[dev]`` only) has
# neither. Modules listed below exercise the real cross-encoder code path and
# genuinely cannot run without them; skip them rather than fail collection.
#
# This mirrors how the suite already treats other optional capabilities via
# markers (``live`` / ``nli`` in pyproject addopts).
# --------------------------------------------------------------------------
_TORCH_REQUIRED_MODULES = {
    "test_cli.py",
    "test_contradiction.py",
    "test_nli.py",
    "test_integration.py",
    "test_inference_cost.py",
}


def _torch_available() -> bool:
    try:
        return importlib.util.find_spec("torch") is not None
    except (ImportError, ValueError):
        return False


# -----------------------------------------------------------------------------
# Eval-statistics stack handling (added 2026-10-04)
#
# scipy/numpy back evals/stats.py (Wilson intervals, cluster bootstrap, exact
# McNemar). They are declared in the [dev] extra, but CI and contributors may
# still run in an environment that predates that, and these tests fail at
# IMPORT time without scipy - so they must skip, not error on collection. Same
# treatment as the torch modules above.
# -----------------------------------------------------------------------------
_SCIPY_REQUIRED_MODULES = {
    "test_eval_stats.py",
    "test_eval_split.py",
}


def _scipy_available() -> bool:
    try:
        return importlib.util.find_spec("scipy") is not None
    except (ImportError, ValueError):
        return False


def pytest_collection_modifyitems(config, items):
    """Skip modules whose optional dependency is absent instead of erroring."""
    skip_ml = None
    if not _torch_available():
        skip_ml = pytest.mark.skip(
            reason="torch is an optional dependency; "
                   "install citesure[nli] to run these"
        )
    skip_stats = None
    if not _scipy_available():
        skip_stats = pytest.mark.skip(
            reason="scipy/numpy back the eval statistics module; "
                   "install citesure[dev] to run these"
        )

    for item in items:
        module = Path(str(item.fspath)).name
        if module in _TORCH_REQUIRED_MODULES and skip_ml is not None:
            item.add_marker(skip_ml)
        elif module in _SCIPY_REQUIRED_MODULES and skip_stats is not None:
            item.add_marker(skip_stats)
