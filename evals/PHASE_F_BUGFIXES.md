# Phase F — Bug-Fix Loop (all 8 defects fixed + regression-locked)

Date: 2026-09-09 · Method: each bug from Phase C (`/tmp/citesure-bugs.md`) and Phase E
(`evals/INSTALL_MATRIX.md`) was fixed at its root cause, its strict-xfail test flipped to a
normal regression test, and the full suite re-run. Proof bar met: repro → fix → regression
test → green suite → (for BUG-8) re-run of the exact failing install combo.

## Fixes applied

| Bug | Root cause | Fix | File | Regression test |
|-----|-----------|-----|------|-----------------|
| BUG-1 | `fetch()` let malformed IPv6 URLs escape as `ValueError` | Up-front URL validation; return failed `FetchedPage` | `fetcher.py` | `test_fetch_malformed_ipv6_url_returns_failed_page` |
| BUG-2 | NUL-byte URLs escaped as `httpx.InvalidURL` | Same guard (NUL check + `httpx.URL` validation) | `fetcher.py` | `test_fetch_null_byte_url_returns_failed_page` |
| BUG-3 | `render_human` crashed on non-numeric `score` (`f"{s:.3f}"`) | Coerce to `float`, skip on failure | `report.py` | `test_render_human_survives_non_numeric_score` |
| BUG-4 | HTTP 429 returned immediately, no retry | Retry 429/503 honoring `Retry-After` (capped 30 s) | `fetcher.py` | `test_ratelimit_429_is_retried_with_backoff_then_succeeds` |
| BUG-5 | Cache key not URL-normalized (scheme/host case) | `_normalize_url()` in key **and** lookup | `fetcher.py` | `test_cache_key_stable_across_scheme_case_variants` |
| BUG-6 | `_local_path_for` raised `RuntimeError` on `~unknown-user` | Wrap `expanduser()` in try/except → return None | `fetcher.py` | `test_local_path_for_tilde_unknown_user_does_not_raise` |
| BUG-7 | Global `asyncio.Semaphore` deadlocks across event loops | Per-loop semaphore via `WeakKeyDictionary` (leak-safe) | `fetcher.py` | `test_twenty_parallel_thread_pool_fetches_all_complete` |
| BUG-8 | Fresh py3.10 install crashes: missing `lxml_html_clean` | Declare `lxml_html_clean>=0.1` dependency | `pyproject.toml` | install matrix (py3.10 wheel+sdist now PASS) |

## Verification

- **Full offline suite:** `215 passed, 5 deselected` (was 146 baseline → 208 with Phase C's
  7 xfail → 215 with all xfails flipped to passing regression tests). No xfail remaining.
- **BUG-7 probe:** run standalone, all 20 parallel thread-pool fetches return `True`.
- **BUG-8 install matrix (post-fix):** all 6 combos PASS —
  `py3.10/3.11/3.12 × wheel/sdist`. The previously-failing `py3.10/wheel` now collects and
  installs `lxml_html_clean-0.4.5`, imports cleanly, and verifies. See
  `evals/results/install_matrix_postfix.tsv`.

## Latent harness bug found & fixed during Phase F

The BUG-7 probe ends with `os._exit(code)`, which skips stdio flushing. Under pytest's
pipe-buffered stdout the final `print("RESULTS", ...)` was discarded, so the test saw empty
stdout even though the probe succeeded. Fixed by adding `flush=True` to the probe's prints.
(This is a test-harness defect, not a product defect — but it would have masked a real pass.)

## Not changed

No behavior changes beyond the 8 fixes. Thresholds, verdict logic, and the overlap/NLI tiers
are untouched — those are the subject of the improvement plan (see TEST_REPORT.md), not this
bug-fix loop.
