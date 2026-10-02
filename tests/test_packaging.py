"""pyproject declares the heavy NLI stack as optional, not required.

The default path must install without torch: a plain ``pip install citesure``
that pulls ~2GB of ML deps contradicts the README's offline/lazy claim, and the
imports in nli.py are function-local precisely so this is possible.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ``tomllib`` is stdlib only from Python 3.11, but pyproject declares
# ``requires-python = ">=3.10"`` and CI runs a 3.10 leg. Skip rather than break
# a supported version.
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 has no stdlib tomllib
    tomllib = None

pytestmark = pytest.mark.skipif(
    tomllib is None, reason="tomllib requires Python 3.11+"
)

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _deps() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]


def test_nli_stack_not_in_required_dependencies() -> None:
    required = " ".join(_deps()["dependencies"])
    for heavy in ("torch", "transformers", "sentence-transformers"):
        assert heavy not in required, f"{heavy} must not be a hard dependency"


def test_nli_extra_declares_the_stack() -> None:
    extra = " ".join(_deps()["optional-dependencies"]["nli"])
    for heavy in ("torch", "transformers", "sentence-transformers"):
        assert heavy in extra, f"{heavy} must be in the [nli] extra"
