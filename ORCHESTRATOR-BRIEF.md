You are running the citesure build as Orchestrator. The full spec is at /home/hermes/citesure/PLAN.md — READ IT FIRST and treat every "Locked Decision" (D1-D12) as binding. Do not deviate from locked decisions; if you hit a genuine blocker, record it in PROGRESS.md and continue with what is unblocked.

## RESUME NOTE (this is a restart after a WSL reboot killed the previous run)
Previous run state on disk:
- Venv at /home/hermes/citesure/.venv exists with ALL deps installed (httpx, trafilatura, mcp, torch CPU, transformers, sentence-transformers, pytest). Do NOT recreate it.
- Phase 1 was PARTIALLY done: pyproject.toml, src/citesure/__init__.py, src/citesure/models.py (verdict model, D3), src/citesure/mcp_server.py (deliberate STUB for Phase 4), README.md exist. No fetcher, no extractor, no CLI, no tests/ dir, no fixtures yet.
- Git repo initialized but ZERO commits. PROGRESS.md does not exist.
- First action: VERIFY what exists (run .venv/bin/python -m pytest -q, inspect files), then continue Phase 1 from where it stopped. Commit the verified state as the first commit ("phase 1: partial — skeleton + models").

## Your job
Execute ALL FIVE PHASES in order (Phase 1 → Phase 5). For each phase:
1. Dispatch the target specialist profile(s) as subagent(s) using the terminal tool:
   `hermes -p <profile> chat --yolo -q "<task brief>"` (use --query-file with a temp file if the brief is long)
   Give each subagent: the relevant PLAN.md decisions verbatim, exact file paths under /home/hermes/citesure/, the phase exit criterion, and an instruction to commit nothing (you handle git).
2. VERIFY on disk yourself before accepting: run the test suite (`cd /home/hermes/citesure && .venv/bin/python -m pytest -x -q`), run the CLI on the sample file, inspect key files. A subagent claiming "done" without your own verification does NOT count.
3. If verification fails, send a follow-up dispatch with the specific failures. Max 2 retries per phase, then record the gap in PROGRESS.md and move on.
4. Append a phase-completion entry to /home/hermes/citesure/PROGRESS.md (what was built, test results you personally ran, files created, gaps).

## Hard constraints
- HARD CAP: 2 concurrent API calls total. Run child profiles STRICTLY SEQUENTIALLY — one `hermes -p ...` at a time, wait for it to finish before starting the next. Never parallelize two hermes invocations.
- Phase 3 (NLI) will download a ~425MB model on first use — that is expected; allow up to 10 minutes for that step. If the download fails due to network, mark Phase 3 as "code complete, model download pending" in PROGRESS.md and continue.
- Phase 5 PyPI publish: PREPARE everything (twine-ready sdist/wheel, verified metadata) but do NOT actually publish to PyPI — record the publish command in PROGRESS.md for the user to run.
- Git: make one clean commit per completed phase with a descriptive message. Do NOT push to GitHub — the user will review first.
- Work ONLY inside /home/hermes/citesure/ (plus temp files in /tmp). Never touch other directories.
- Python: use /home/hermes/citesure/.venv/bin/python and .venv/bin/pip. Do NOT create a new venv.

## Final report
When all five phases are done (or you are out of turns), end with a summary: per-phase status (complete/gap), test counts from YOUR OWN runs, git log --oneline output, and the exact commands the user needs to (a) install, (b) run the demo, (c) publish to PyPI when ready.
