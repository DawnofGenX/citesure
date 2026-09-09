"""Phase C3: concurrency & flaky-server tests (mock server only, offline).

Covers:
* 20 parallel fetches through a thread pool (all must complete);
* /ratelimit: 429 + Retry-After must be retried with backoff, then succeed;
* /disconnect: mid-body close → clean unreachable verdict, no hang, no leaked
  client sockets;
* 1 s timeout against /slow fails fast in < 3 s.

The 20-thread probe runs in a *subprocess* with a hard timeout: while BUG-7
is present the probe deadlocks, and we must not let a deadlocked
ThreadPoolExecutor (non-daemon threads) hang the whole pytest session.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import citesure.fetcher as F
from citesure.fetcher import clear_robots_cache, fetch

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_robots():
    clear_robots_cache()
    yield
    clear_robots_cache()


def _open_fds() -> int:
    """Count of file descriptors open in this process (proxy for sockets)."""
    return len(os.listdir("/proc/self/fd"))


# ---------------------------------------------------------------------------
# 20 parallel fetches (thread pool)
# ---------------------------------------------------------------------------

_PROBE_SCRIPT = """
import sys, os, asyncio, shutil
sys.path.insert(0, {repo!r})
os.environ["CITECHECK_CACHE_DIR"] = {cache!r}
shutil.rmtree({cache!r}, ignore_errors=True)
from tests.fixtures.mock_server import MockServer
from citesure.fetcher import fetch, clear_robots_cache
from concurrent.futures import ThreadPoolExecutor
s = MockServer().start()
ex = ThreadPoolExecutor(max_workers=20)
try:
    clear_robots_cache()
    def worker(i):
        return asyncio.run(fetch(s.url("/redirect-final"))).ok
    futs = [ex.submit(worker, i) for i in range(20)]
    results = [f.result(timeout=40) for f in futs]
    print("RESULTS", results, flush=True)
    code = 0
except Exception as e:
    print("PROBE-ERROR", type(e).__name__, e, flush=True)
    code = 1
finally:
    ex.shutdown(wait=False, cancel_futures=True)
    s.stop()
# os._exit skips atexit handlers: while BUG-7 is present the deadlocked
# executor threads would otherwise hang interpreter shutdown forever.
os._exit(code)
"""


def test_twenty_parallel_thread_pool_fetches_all_complete(tmp_path):
    """20 threads each running asyncio.run(fetch(...)) must ALL complete.

    Runs in a subprocess with a hard 90 s timeout so a deadlock (BUG-7)
    fails this test without hanging the suite.
    """
    cache = str(tmp_path / "cache")
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT.format(repo=str(REPO_ROOT), cache=cache)],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CITECHECK_CACHE_DIR": cache},
    )
    assert proc.returncode == 0, f"probe crashed:\n{proc.stdout}\n{proc.stderr}"
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULTS")), "")
    assert line.count("True") == 20, f"not all 20 fetches succeeded: {line}"
    assert line.count("False") == 0, f"some fetches failed: {line}"


def test_ten_parallel_thread_pool_fetches_complete(mock_server, tmp_path, monkeypatch):
    """Sanity baseline: ≤10 concurrent cross-loop fetches work today."""
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    url = mock_server.url("/redirect-final")

    def worker(i: int) -> bool:
        return asyncio.run(fetch(url)).ok

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(worker, range(10)))
    assert all(results)


# ---------------------------------------------------------------------------
# /ratelimit: 429 must be retried with backoff, then succeed
# ---------------------------------------------------------------------------


def test_ratelimit_429_is_retried_with_backoff_then_succeeds(mock_server, tmp_path, monkeypatch):
    """First hit → 429 (Retry-After: 1); the fetcher must wait and retry,
    landing on the subsequent 200."""
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    mock_server.reset_state()
    t0 = time.time()
    page = asyncio.run(fetch(mock_server.url("/ratelimit")))
    elapsed = time.time() - t0
    assert page.ok is True, f"expected success after retry, got {page.error}"
    assert "rate limit lifted" in page.text
    # A real retry honors Retry-After/backoff → at least ~1 s of waiting.
    assert elapsed >= 0.9, f"returned in {elapsed:.2f}s — no backoff observed"


# ---------------------------------------------------------------------------
# /disconnect: mid-body close → clean unreachable, no hang, no leaked sockets
# ---------------------------------------------------------------------------


def test_disconnect_mid_body_is_clean_unreachable_no_hang(mock_server, tmp_path, monkeypatch):
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    fds_before = _open_fds()
    t0 = time.time()
    page = asyncio.run(fetch(mock_server.url("/disconnect")))
    elapsed = time.time() - t0
    # Clean failure, not a crash or a hang.
    assert page.ok is False
    assert page.error is not None
    assert elapsed < 30, f"disconnect handling took {elapsed:.1f}s"
    # No leaked client sockets: fd count returns to baseline (±2 tolerance
    # for GC timing).
    time.sleep(0.2)  # let httpx release pooled connections
    fds_after = _open_fds()
    assert fds_after <= fds_before + 2, f"fd leak: {fds_before} -> {fds_after}"


# ---------------------------------------------------------------------------
# Timeout enforcement: 1 s timeout vs /slow (2 s delay) fails fast
# ---------------------------------------------------------------------------


def test_one_second_timeout_against_slow_route_fails_fast(mock_server, tmp_path, monkeypatch):
    """With REQUEST_TIMEOUT=1s and no retries, /slow (2 s delay) must fail
    in < 3 s total wall time."""
    monkeypatch.setenv("CITECHECK_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(F, "REQUEST_TIMEOUT", 1.0)
    monkeypatch.setattr(F, "MAX_RETRIES", 0)  # isolate the timeout itself
    t0 = time.time()
    page = asyncio.run(fetch(mock_server.url("/slow")))
    elapsed = time.time() - t0
    assert page.ok is False
    assert "Timeout" in (page.error or "")
    assert elapsed < 3.0, f"timeout enforcement took {elapsed:.2f}s"
