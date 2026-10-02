"""The benchmark harness must be reproducible and must not fabricate numbers."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent / "bench" / "inference_cost.py"


def _load():
    spec = importlib.util.spec_from_file_location("inference_cost", HARNESS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_harness_exists_and_is_importable() -> None:
    mod = _load()
    assert hasattr(mod, "bench")
    assert hasattr(mod, "load_pairs")
    assert hasattr(mod, "accuracy")


def test_eval_set_parses_to_nonempty_pairs() -> None:
    mod = _load()
    pairs = mod.load_pairs()
    assert pairs, "eval set yielded no claim/passage pairs — parser must match the real file shape"
    for claim, passage in pairs[:5]:
        assert isinstance(claim, str) and isinstance(passage, str)
        assert claim.strip() and passage.strip()


def test_results_file_is_not_hand_written() -> None:
    """A committed results file must match the harness's own schema."""
    results = Path(__file__).resolve().parent.parent / "bench" / "results.json"
    if not results.exists():
        return  # nothing committed yet is acceptable; a WRONG file is not
    rows = json.loads(results.read_text())
    assert isinstance(rows, list) and rows
    for row in rows:
        assert {"precision", "device", "n_pairs", "mean_sec", "pairs_per_sec"} <= set(row)
