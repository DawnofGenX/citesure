"""Shared pytest fixtures for the citesure test suite."""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``tests.fixtures.mock_server`` importable regardless of how pytest is
# invoked (the repo root is not a package).
_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR.parent))

from tests.fixtures.mock_server import mock_server  # noqa: E402,F401  (re-exported fixture)
