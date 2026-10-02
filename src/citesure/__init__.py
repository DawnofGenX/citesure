"""citesure — verify that an LLM's claims are supported by the sources it cites.

Public API (library form, D5). The CLI and MCP server are thin wrappers over
these functions so ~80% of the logic is testable without any MCP plumbing.

Typical library usage::

    from citesure import extract_citations, verify_citations, Report

    citations = extract_citations("The sky is blue [1].", {"1": "https://..."})
    report = verify_citations(citations)   # tiers 1+2 by default (Phase 2)
"""

from __future__ import annotations

from .citations import Citation, extract_citations, load_input
from .models import Report, Status, Verdict
from .nli import (
    DEFAULT_NLI_MODEL,
    NLIError,
    NLICrossEncoder,
    apply_nli_tier,
    get_nli_model,
    resolve_nli_model,
    score_nli,
    score_nli_batch,
    status_for_nli,
)
from .overlap import (
    DEFAULT_TOP_K,
    HIGH_OVERLAP_THRESHOLD,
    LOW_OVERLAP_THRESHOLD,
    rank_passages,
    score_overlap,
    segment_passages,
    status_for_score,
)
from .reachability import classify_reachability, verify_citations

__version__ = "0.2.0"

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
    # overlap tier (Phase 2)
    "score_overlap",
    "rank_passages",
    "segment_passages",
    "status_for_score",
    "DEFAULT_TOP_K",
    "HIGH_OVERLAP_THRESHOLD",
    "LOW_OVERLAP_THRESHOLD",
    # NLI tier (Phase 3)
    "NLIError",
    "NLICrossEncoder",
    "get_nli_model",
    "resolve_nli_model",
    "score_nli",
    "score_nli_batch",
    "status_for_nli",
    "apply_nli_tier",
    "DEFAULT_NLI_MODEL",
]
