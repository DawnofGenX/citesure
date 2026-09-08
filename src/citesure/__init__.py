"""citesure — verify that an LLM's claims are supported by the sources it cites.

Public API (library form, D5). The CLI and MCP server are thin wrappers over
these functions so ~80% of the logic is testable without any MCP plumbing.

Typical library usage::

    from citesure import extract_citations, verify_citations, Report

    citations = extract_citations("The sky is blue [1].", {"1": "https://..."})
    report = verify_citations(citations)   # reachability tier by default
"""

from __future__ import annotations

from .citations import Citation, extract_citations, load_input
from .models import Report, Status, Verdict
from .reachability import classify_reachability, verify_citations

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Citation",
    "Status",
    "Verdict",
    "Report",
    "extract_citations",
    "load_input",
    "classify_reachability",
    "verify_citations",
]
