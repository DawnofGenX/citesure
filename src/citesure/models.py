"""Verdict data model (D3).

Per-citation :class:`Verdict` and aggregate :class:`Report`. These are plain
dataclasses so they serialize trivially to JSON for the CLI / MCP tools and
stay free of any framework dependency.

Status semantics (D3):

* ``supported``   — page fetched and the claim is present in the cited region
                    (tier 2+) / reachable with no red flags (tier 1).
* ``unsupported`` — page fetched but the claim is absent from the cited region
                    (and NLI < 0.3 when the NLI tier is on).
* ``unreachable`` — DNS failure, HTTP 4xx/5xx, or timeout.
* ``paywalled``   — login/paywall detected.
* ``ambiguous``   — partial overlap, NLI in the 0.3–0.7 band, or the citation
                    marker could not be located (e.g. a JS-rendered page).

In Phase 1 only the reachability tier runs, so ``supported`` means "reachable
with no paywall/retraction flags" and ``unsupported`` is not yet produced;
the overlap/NLI tiers fill those in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Status(str, Enum):
    """Verification outcome for a single citation."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNREACHABLE = "unreachable"
    PAYWALLED = "paywalled"
    AMBIGUOUS = "ambiguous"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Maximum length of the ``evidence`` field (D3).
EVIDENCE_MAX_LEN = 300


def _clip_evidence(text: str | None) -> str:
    """Clip evidence to the D3 cap of 300 characters."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= EVIDENCE_MAX_LEN:
        return text
    return text[: EVIDENCE_MAX_LEN - 1].rstrip() + "…"


@dataclass
class Verdict:
    """The verification result for one citation (D3)."""

    citation_id: str
    url: str
    status: Status
    tier_reached: int
    score: float | None = None
    evidence: str = ""
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.status, str) and not isinstance(self.status, Status):
            self.status = Status(self.status)
        self.evidence = _clip_evidence(self.evidence)
        if self.notes is None:
            self.notes = []

    @property
    def passes(self) -> bool:
        """True when this citation counts toward the pass rate.

        Only ``supported`` passes. Everything else (unsupported, unreachable,
        paywalled, ambiguous) is a non-pass. In ``--strict`` CI mode the caller
        additionally treats ``ambiguous`` as an explicit failure, but it never
        contributes to the numerator either way.
        """
        return self.status is Status.SUPPORTED

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Verdict":
        return cls(
            citation_id=data["citation_id"],
            url=data["url"],
            status=Status(data["status"]),
            tier_reached=int(data.get("tier_reached", 1)),
            score=data.get("score"),
            evidence=data.get("evidence", ""),
            notes=list(data.get("notes", [])),
        )


@dataclass
class Report:
    """Aggregate verification report across all citations (D3)."""

    total: int
    supported: int
    unsupported: int
    unverifiable: int
    pass_rate: float
    verdicts: list[Verdict] = field(default_factory=list)

    @classmethod
    def from_verdicts(cls, verdicts: list[Verdict]) -> "Report":
        """Build a report from a list of per-citation verdicts.

        * ``supported``    → numerator of the pass rate.
        * ``unsupported``  → counted separately.
        * everything else  → ``unverifiable`` (unreachable / paywalled /
          ambiguous), i.e. we could not confirm the claim.
        """
        total = len(verdicts)
        supported = sum(1 for v in verdicts if v.status is Status.SUPPORTED)
        unsupported = sum(1 for v in verdicts if v.status is Status.UNSUPPORTED)
        unverifiable = total - supported - unsupported
        pass_rate = (supported / total) if total else 0.0
        return cls(
            total=total,
            supported=supported,
            unsupported=unsupported,
            unverifiable=unverifiable,
            pass_rate=round(pass_rate, 4),
            verdicts=list(verdicts),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "supported": self.supported,
            "unsupported": self.unsupported,
            "unverifiable": self.unverifiable,
            "pass_rate": self.pass_rate,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }
