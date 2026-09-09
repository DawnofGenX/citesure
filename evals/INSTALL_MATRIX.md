# Phase E — Install Matrix Findings

Date: 2026-09-09 · Method: fresh Docker container per combo, `python:{ver}` base image,
`pip install --no-cache-dir <artifact>`, then a live verify demo (reachable + dead citation).
Artifacts rebuilt from source immediately before the run (`python -m build`).

## Result matrix

| python | artifact | install | verify | result |
|--------|----------|---------|--------|--------|
| 3.10   | wheel    | OK      | FAIL   | **FAIL** |
| 3.10   | sdist    | OK      | FAIL   | **FAIL** |
| 3.11   | wheel    | OK      | PASS   | PASS |
| 3.11   | sdist    | OK      | PASS   | PASS |
| 3.12   | wheel    | OK      | PASS   | PASS |
| 3.12   | sdist    | OK      | PASS   | PASS |

(First run had transient Docker registry TLS failures on 3.11/3.12 image pulls; re-run
after network recovered. All six combos completed.)

## BUG-8 (packaging): fresh install on Python 3.10 fails at import time

- **Symptom:** `pip install citesure` succeeds, but any use crashes with
  `ImportError: lxml.html.clean module is now a separate project lxml_html_clean.
  Install lxml[html-clean] or lxml_html_clean directly.`
- **Root cause:** `trafilatura 2.2.0` imports `lxml.html.clean`. In lxml ≥ 6 that module
  was split into the standalone `lxml_html_clean` package. citesure's `pyproject.toml`
  does not declare `lxml_html_clean` (nor pin `lxml<5`). Whether it gets installed is
  left to pip's transitive resolution, which is **interpreter-dependent**:
  - local venv (py3.12): has `lxml 6.1.3` + `lxml_html_clean 0.4.5` → works
  - docker py3.11 / py3.12: resolver pulled `lxml_html_clean-0.4.5` → works
  - docker py3.10: resolver did NOT pull it → ImportError
- **Why it matters:** this is the primary "just `pip install`" path, broken on a
  supported interpreter (3.10 is in the CI matrix). Non-deterministic across envs =
  exactly the class of bug users hit and we don't.
- **Fix options (for the improvement plan):**
  1. Add `lxml_html_clean` to `[project] dependencies` (explicit, deterministic). ← recommended
  2. Or pin `lxml<5` (avoids the split entirely, but blocks a security-relevant major).
  3. Or make the trafilatura import lazy/optional so reachability tier works without it.
- **Repro:** `docker run python:3.10 pip install <wheel> && python -c "import citesure"`

## Secondary observation (not a citesure bug, noted for completeness)

The install pulls the full `torch` + CUDA stack (~2GB: nvidia-cublas, cudnn, etc.) even
though citesure only needs CPU inference. This bloats every install. A `cpu` extra /
`torch==*+cpu` index hint would cut install size dramatically — candidate improvement.
