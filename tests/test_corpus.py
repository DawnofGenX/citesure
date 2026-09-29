"""Phase-2 acceptance test: the full frozen fixture corpus (D9).

Loads ``tests/fixtures/cases.json`` (27 cases covering all five D3 statuses),
runs the complete verification pipeline offline, and asserts EVERY case gets
its recorded expected status. This is the Phase-2 exit gate.

Unreachable coverage (D9): the corpus carries 5 ``unreachable`` cases using
``.invalid`` TLD domains (RFC 2606 reserved — they never resolve, so the
suite stays fully offline and the corpus file is directly runnable through
the CLI). In addition, :func:`test_mock_server_unreachable_routes` points 5
citations at the local mock server's 404/500/timeout routes, satisfying
D9's "5 unreachable (via local mock server)" requirement.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path

import pytest

from citesure.citations import Citation
from citesure.fetcher import clear_robots_cache
from citesure.models import Status
from citesure.overlap import verify_citations

FIXTURES = Path(__file__).parent / "fixtures"
CASES_JSON = FIXTURES / "cases.json"


@pytest.fixture(autouse=True)
def _clean_robots():
    clear_robots_cache()
    yield
    clear_robots_cache()


@pytest.fixture()
def _clean_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))


def _load_cases() -> list[dict]:
    raw = json.loads(CASES_JSON.read_text(encoding="utf-8"))
    items = raw["citations"] if isinstance(raw, dict) else raw
    out = []
    for item in items:
        citation = item["citation"]
        # {FIXTURES} is a portable stand-in for the absolute fixtures dir. The
        # three file:// cases used to hardcode a machine-specific absolute path
        # (/home/hermes/...), so they silently passed locally and failed in CI
        # with "No such file or directory". Keep the file:// scheme (the shape
        # test requires >= 3 of them) but resolve the path at load time.
        if "{FIXTURES}" in citation:
            citation = citation.replace("{FIXTURES}", str(FIXTURES))
        # Anchor bare relative paths against the fixtures directory.
        if not any(citation.startswith(s) for s in ("http://", "https://", "file://", "/")):
            citation = str((FIXTURES / citation).resolve())
        out.append(
            {
                "id": item["id"],
                "claim": item["claim"],
                "url": citation,
                "excerpt": item.get("excerpt"),
                "expected_status": Status(item["expected_status"]),
            }
        )
    return out


def _citations(cases: list[dict]) -> list[Citation]:
    return [
        Citation(
            citation_id=c["id"], url=c["url"], claim=c["claim"], excerpt=c.get("excerpt")
        )
        for c in cases
    ]


# ---------------------------------------------------------------------------
# Corpus shape sanity
# ---------------------------------------------------------------------------


def test_corpus_shape():
    cases = _load_cases()
    counts = Counter(c["expected_status"] for c in cases)
    assert len(cases) >= 25  # ~30-case frozen corpus (D9)
    assert counts[Status.SUPPORTED] >= 5
    assert counts[Status.UNSUPPORTED] >= 5
    assert counts[Status.UNREACHABLE] == 5
    assert counts[Status.PAYWALLED] == 3
    assert counts[Status.AMBIGUOUS] == 4  # 3 partial-overlap + 1 JS page
    # At least 3 citations explicitly use the file:// scheme (D9).
    file_scheme = [c for c in cases if c["url"].startswith("file://")]
    assert len(file_scheme) >= 3
    # Every case carries an expected status annotation.
    assert all(c["expected_status"] is not None for c in cases)
    # Corpus is offline-safe: no resolvable http(s) hosts.
    for c in cases:
        if c["url"].startswith(("http://", "https://")):
            assert ".invalid" in c["url"], f"non-offline URL in corpus: {c['url']}"


# ---------------------------------------------------------------------------
# THE acceptance test: every case gets its expected status
# ---------------------------------------------------------------------------


def test_full_corpus_expected_statuses(_clean_cache):
    cases = _load_cases()
    report = asyncio.run(verify_citations(_citations(cases)))

    by_id = {v.citation_id: v for v in report.verdicts}
    failures = []
    for c in cases:
        v = by_id[c["id"]]
        if v.status is not c["expected_status"]:
            failures.append(
                f"{c['id']}: expected {c['expected_status'].value}, "
                f"got {v.status.value} (score={v.score}, tier={v.tier_reached}, "
                f"url={c['url']}) notes={v.notes}"
            )
    assert not failures, "corpus mismatches:\n" + "\n".join(failures)

    # Report aggregates match the annotated expectations exactly.
    expected = Counter(c["expected_status"] for c in cases)
    assert report.total == len(cases)
    assert report.supported == expected[Status.SUPPORTED]
    assert report.unsupported == expected[Status.UNSUPPORTED]
    assert report.unverifiable == (
        expected[Status.UNREACHABLE]
        + expected[Status.PAYWALLED]
        + expected[Status.AMBIGUOUS]
    )
    assert report.pass_rate == pytest.approx(
        round(expected[Status.SUPPORTED] / len(cases), 4)
    )

    # Tier bookkeeping: overlap tier ran exactly on the clean-reachable pages.
    for v in report.verdicts:
        if v.status in (Status.SUPPORTED, Status.UNSUPPORTED, Status.AMBIGUOUS):
            # supported/unsupported/ambiguous all come from tier 2 — except
            # retraction-driven unsupported, which is decided at tier 1.
            if v.tier_reached == 1:
                assert any("retract" in n.lower() for n in v.notes), (
                    f"{v.citation_id}: tier-1 {v.status.value} without retraction note"
                )
            else:
                assert v.score is not None or "not locatable" in " ".join(v.notes)
        else:
            assert v.tier_reached == 1
            assert v.score is None


def test_corpus_per_status_counts_reported(_clean_cache):
    """Print the per-status counts (used as the Phase-2 exit-gate evidence)."""
    cases = _load_cases()
    report = asyncio.run(verify_citations(_citations(cases)))
    counts = Counter(v.status.value for v in report.verdicts)
    print(
        f"\ncorpus: total={report.total} supported={report.supported} "
        f"unsupported={report.unsupported} unverifiable={report.unverifiable} "
        f"pass_rate={report.pass_rate:.4f}\nper-status: {dict(counts)}"
    )
    assert sum(counts.values()) == report.total


# ---------------------------------------------------------------------------
# D9: 5 unreachable cases via the LOCAL MOCK SERVER (404/500/timeout)
# ---------------------------------------------------------------------------


def test_mock_server_unreachable_routes(mock_server, _clean_cache):
    """Point 5 citations at the local mock server; all must be unreachable."""
    cases = [
        ("m1", mock_server.url("/not-found"), "The quarterly earnings report exceeded analyst expectations."),
        ("m2", mock_server.url("/server-error"), "The new compiler backend reduces code size by 12 percent."),
        ("m3", mock_server.url("/timeout"), "The satellite relay station went offline during the storm."),
        ("m4", mock_server.url("/not-found"), "The museum reopened after its decade-long renovation."),
        ("m5", mock_server.url("/server-error"), "The bridge collapsed during the morning rush hour."),
    ]
    citations = [Citation(cid, url, claim) for cid, url, claim in cases]
    report = asyncio.run(verify_citations(citations))
    by_id = {v.citation_id: v for v in report.verdicts}
    for cid, url, _ in cases:
        v = by_id[cid]
        assert v.status is Status.UNREACHABLE, (
            f"{cid} ({url}): expected unreachable, got {v.status.value} notes={v.notes}"
        )
        assert v.tier_reached == 1
        assert v.score is None
    assert report.unverifiable == 5
    assert report.pass_rate == 0.0
