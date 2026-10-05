"""NLI entailment tier (tier 3 of D2) — local cross-encoder scoring (D6).

Design decisions (documented per the Phase-3 brief):

* **Loader** — ``transformers.AutoModelForSequenceClassification`` +
  ``AutoTokenizer`` (NOT the legacy ``transformers.CrossEncoder`` class: it was
  removed from transformers 5.x, which is what this venv ships; the
  Auto* pair is the supported equivalent and works identically for
  cross-encoder NLI models). The loaded model object is a plain
  ``(model, tokenizer)`` pair wrapped in :class:`NLICrossEncoder`, which
  exposes a ``predict(pairs) -> list[float]`` interface so callers (and tests)
  never touch raw logits.
* **Model resolution (D6 priority chain)** — explicit argument (from
  ``--nli-model`` / ``nli_model=``) → ``CITECHECK_NLI_MODEL`` env var →
  built-in default ``cross-encoder/nli-deberta-v3-base``. Any HF
  cross-encoder name or local path is accepted. Resolution is a pure
  function (:func:`resolve_nli_model`) so it is unit-testable without any
  model download.
* **Cache dir** — HF downloads land under ``~/.cache/citesure/`` by passing
  ``cache_dir=`` to the loaders (consistent with the fetcher's disk cache);
  overridable via ``CITECHECK_CACHE_DIR``.
* **CPU-only** — torch is forced to CPU (the venv has the CPU build) and
  ``torch.set_num_threads`` is set to the physical core count (capped at 8)
  for sensible multi-core inference without oversubscribing.
* **Fail fast (D6)** — if the name/path does not resolve to a loadable
  cross-encoder (bad HF name, no network for the download, corrupt local
  path), :func:`get_nli_model` raises :class:`NLIError` with an actionable
  message. There is NO silent fallback to another model.
* **Scoring** — softmax over the model's label logits, taking the
  *entailment* probability in ``[0, 1]``. Label order is read from the
  model's ``id2label`` when available (standard for NLI models:
  entailment/neutral/contradiction); for custom models with a single output
  logit the sigmoid of that logit is used. Deterministic: same model + same
  input → same score (no dropout at inference, fixed device, fixed threads).
* **Verdict banding (D3)** — :func:`status_for_nli` maps
  ``(overlap_status, nli_score)`` to the final status with pinned thresholds
  (see below). The pipeline integration lives in
  :func:`apply_nli_tier`, which runs after tiers 1+2 and sets
  ``tier_reached=3`` whenever NLI actually ran.

Banding rules implemented (D3, exact combination table):

============================  ==================  ============================
overlap status (tier 2)       NLI score           final status
============================  ==================  ============================
supported                     >= 0.7              supported
supported                     0.3 <= s < 0.7      ambiguous   (downgrade)
supported                     < 0.3               unsupported (downgrade)
ambiguous (mid-band overlap)  >= 0.7              supported   (UPGRADE)
ambiguous (mid-band overlap)  0.3 <= s < 0.7      ambiguous   (hold)
ambiguous (mid-band overlap)  < 0.3               unsupported (downgrade)
ambiguous (marker not locatable, JS page)         any         ambiguous (hold —
                                                    no passage to score)
unsupported                   >= 0.7              supported   (UPGRADE: the
                                                        best passage entails
                                                        the claim even though
                                                        term coverage was low)
unsupported                   0.3 <= s < 0.7      ambiguous   (upgrade)
unsupported                   < 0.3               unsupported (confirm)
unreachable / paywalled       (NLI not run)       unchanged (tier 1)
============================  ==================  ============================

D3.1 extension (since 2026-09-09): if the model's contradiction probability
is >= CONTRADICTION_THRESHOLD (0.5), the verdict is forced unsupported
regardless of the entailment band. By 3-way softmax math, con >= 0.5
implies ent < 0.5, so this override can only fire where the banding
already gives ambiguous-or-worse — it can never touch an ent >= 0.7
supported verdict.

Rationale: NLI is the strongest signal we have (a trained entailment model
on the actual claim-vs-best-passage pair), so it may move a verdict up OR
down by one band relative to the lexical-overlap verdict, but it can never
override a tier-1 outcome (unreachable/paywalled) because there was no page
content to score. A JS page (marker not locatable) has no usable passage, so
it stays ambiguous regardless of NLI — D3 explicitly lists "marker not
locatable" as an ambiguous condition.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .models import Status

# NOTE: torch is imported lazily (inside the functions that need it) so that
# the default tiers-1+2 path never pays the ~1-3 s torch import cost. The
# ``torch.nn.Module`` annotation below is a string (PEP 563) and is never
# evaluated at runtime.

# ---------------------------------------------------------------------------
# Constants (D6)
# ---------------------------------------------------------------------------

#: Built-in default cross-encoder (D6). ~425 MB, lazy-downloaded on first use.
DEFAULT_NLI_MODEL = "cross-encoder/nli-deberta-v3-base"

#: Env var overriding the default model (D6, middle of the priority chain).
ENV_NLI_MODEL = "CITECHECK_NLI_MODEL"

#: Env var overriding the cache directory (consistent with the fetcher).
ENV_CACHE_DIR = "CITECHECK_CACHE_DIR"

#: D3 NLI banding thresholds (pinned; tested in tests/test_nli.py).
NLI_SUPPORTED_THRESHOLD = 0.7  # score >= this → supported
NLI_AMBIGUOUS_THRESHOLD = 0.3  # [this, 0.7) → ambiguous; below → unsupported

#: D3.1 contradiction override (since D3.1). When the model's contradiction
#: probability >= this, the verdict is forced unsupported regardless of the
#: entailment band. 3-way softmax math: con >= 0.5 implies ent < 0.5, so this
#: can only fire where current banding already gives ambiguous-or-worse — it
#: can never touch an ent >= 0.7 supported verdict.
CONTRADICTION_THRESHOLD = 0.5

#: (D3.2) A pooled contradiction at or above this vetoes a ``supported`` verdict.
#: Pooling is over claim-relevant sentences of the same captured premises, using
#: the NLI model's raw contradiction coordinate — deliberately NOT swept against
#: the eval set, since tuning a threshold on the headline metric fits the test
#: set. Measured effect at this value: negation-flip false-supported 4 -> 0,
#: entity-swap 6 -> 3, agreement unchanged at 89/108.
#: Reference for principled (non-fitted) threshold choice: Conformal Risk
#: Control, Angelopoulos et al., ICLR 2023, arXiv:2208.02814.
POOLED_CONTRADICTION_THRESHOLD = 0.5

#: Max sequence length fed to the tokenizer (claim + passage pairs).
MAX_LENGTH = 512

#: Batch size for :func:`score_nli_batch`.
DEFAULT_BATCH_SIZE = 8

#: Cap on torch worker threads (physical cores, capped) — keeps CPU inference
#: responsive without oversubscribing shared machines.
_MAX_THREADS = 8


class NLIError(RuntimeError):
    """Raised when the NLI model cannot be resolved or loaded (fail fast, D6).

    The message is actionable: it names the offending model identifier and
    explains what to check (HF availability, network, local path contents).
    """


# ---------------------------------------------------------------------------
# Model-name resolution (D6 priority chain) — pure, unit-testable
# ---------------------------------------------------------------------------


def resolve_nli_model(model_name: str | None = None) -> str:
    """Resolve the NLI model identifier using the D6 priority chain.

    Priority (highest first):

    1. ``model_name`` — the explicit argument (CLI ``--nli-model`` or the
       library ``nli_model=`` parameter).
    2. ``CITECHECK_NLI_MODEL`` environment variable.
    3. Built-in default :data:`DEFAULT_NLI_MODEL`.

    Pure function: no I/O, no side effects — trivially testable with
    monkeypatched env vars.
    """
    if model_name is not None and str(model_name).strip():
        return str(model_name).strip()
    env = os.environ.get(ENV_NLI_MODEL)
    if env is not None and env.strip():
        return env.strip()
    return DEFAULT_NLI_MODEL


def _cache_dir() -> Path:
    """HF cache root under ``~/.cache/citesure/`` (override: CITECHECK_CACHE_DIR)."""
    override = os.environ.get(ENV_CACHE_DIR, "").strip()
    if override:
        return Path(override)
    return Path.home() / ".cache" / "citesure"


# ---------------------------------------------------------------------------
# Model loading (lazy, cached, fail-fast)
# ---------------------------------------------------------------------------


@dataclass
class NLICrossEncoder:
    """A loaded cross-encoder: ``(model, tokenizer)`` plus its label mapping.

    Exposes :meth:`predict` returning entailment probabilities in ``[0, 1]``.
    Tests may substitute a duck-typed object with the same ``predict`` method
    (see tests/test_nli.py) — nothing here depends on the concrete class.
    """

    name: str
    model: "torch.nn.Module"
    tokenizer: object
    #: label index → label text, e.g. {0: 'entailment', 1: 'neutral', ...}
    id2label: dict[int, str] | None = None

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Entailment probability in ``[0, 1]`` for each (claim, passage) pair."""
        return _entailment_probs(self.model, self.tokenizer, self.id2label, pairs)

    def predict_all(self, pairs: list[tuple[str, str]]) -> list[tuple[float, float]]:
        """Entailment and contradiction probabilities per pair.

        Returns ``(ent, con)`` for each pair. ``con`` is 0.0 for binary
        (single-logit) models that have no contradiction column.
        """
        ent = _entailment_probs(self.model, self.tokenizer, self.id2label, pairs)
        con = _contradiction_probs(self.model, self.tokenizer, self.id2label, pairs)
        return list(zip(ent, con))


_model_cache: dict[str, NLICrossEncoder] = {}
_cache_lock = threading.Lock()
_threads_configured = False


def _configure_threads() -> None:
    """Set torch thread count once (sensible for CPU inference)."""
    global _threads_configured
    if _threads_configured:
        return
    import torch

    cores = os.cpu_count() or 1
    torch.set_num_threads(min(cores, _MAX_THREADS))
    torch.set_num_interop_threads(1)
    _threads_configured = True


def _is_local_path(name: str) -> bool:
    """True if ``name`` is a local model directory, False for a HF hub id.

    A HuggingFace identifier is ``org/model`` — it ALWAYS contains ``/`` — so
    testing for ``"/" in name`` misclassifies every hub id as a local path
    and, via ``local_files_only=True``, forbids the very download the NLI tier
    depends on. The default model could therefore never be fetched on a clean
    install; it only worked where the cache had been pre-seeded.

    A name is treated as a local path only when it is unambiguously one: an
    explicit relative/absolute prefix, a Windows separator, or an existing
    filesystem entry. Everything else is a hub id and may be downloaded.
    """
    if "\\" in name:
        return True
    if name.startswith(("/", "./", "../", "~", ".")):
        return True
    try:
        return Path(name).exists()
    except OSError:  # pragma: no cover - defensive (e.g. name too long)
        return False


def _has_module(name: str) -> bool:
    """True if ``name`` is importable, without importing it.

    Uses find_spec so a missing optional ML dependency is detected cheaply
    and without side effects. torch/transformers are extras, not hard
    requirements of the package.
    """
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def nli_extra_available() -> tuple[bool, list[str]]:
    """Whether the optional NLI stack (the ``[nli]`` extra) is importable.

    Returns ``(available, missing)`` where ``missing`` lists the modules that
    are not installed. Cheap and side-effect free (``find_spec`` only, no
    import), so it is safe to call on every CLI/MCP invocation to decide
    whether tier 3 can run — the auto-detect default.
    """
    missing = [m for m in ("torch", "transformers") if not _has_module(m)]
    return (not missing, missing)


def get_nli_model(model_name: str | None = None) -> NLICrossEncoder:
    """Load (or return the cached) NLI cross-encoder for the resolved name.

    Lazy: the first call downloads the model (~425 MB for the default) into
    the citesure cache dir; subsequent calls for the same resolved name hit
    the module-level cache and do no I/O.

    Raises:
        NLIError: if the model cannot be loaded (unknown HF name, network
            failure during download, corrupt/incomplete local path, or a
            local path that is not a valid model directory). Fail fast — no
            silent fallback to another model (D6).
    """
    name = resolve_nli_model(model_name)
    with _cache_lock:
        cached = _model_cache.get(name)
        if cached is not None:
            return cached

    # torch/transformers are OPTIONAL (the ``nli`` extra). A default install has
    # neither, so fail with the same clear, actionable NLIError the other load
    # failures raise - not a raw ModuleNotFoundError from deep inside a
    # function-local import. Checked before _configure_threads(), which imports
    # torch unguarded.
    available, missing = nli_extra_available()
    if not available:
            raise NLIError(
                "the NLI tier needs optional dependencies that are not installed: "
                + ", ".join(missing)
                + '. Install them with: pip install "citesure[nli]"'
            )

    _configure_threads()
    cache_dir = _cache_dir()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Cache dir is not writable (e.g. read-only filesystem). Fall back to
        # a temp dir so the model still loads; caching just won't persist.
        import tempfile

        cache_dir = Path(tempfile.mkdtemp(prefix="citesure-hf-"))

    local = _is_local_path(name)
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        # Local paths must load offline (no accidental network round-trip);
        # HF ids may download into our cache dir on first use.
        kwargs = dict(
            cache_dir=str(cache_dir),
            local_files_only=local,
            trust_remote_code=False,
        )
        if local:
            kwargs["low_cpu_mem_usage"] = True
        model = AutoModelForSequenceClassification.from_pretrained(name, **kwargs)
        tokenizer = AutoTokenizer.from_pretrained(name, **kwargs)
    except NLIError:
        raise
    except Exception as exc:  # noqa: BLE001 - wrap everything into NLIError
        raise NLIError(
            f"could not load NLI model '{name}': {type(exc).__name__}: {exc}\n"
            f"  - check the name/path is a valid Hugging Face cross-encoder "
            f"(e.g. '{DEFAULT_NLI_MODEL}') or an existing local model directory\n"
            f"  - if it is an HF name, check network access to huggingface.co "
            f"(first use downloads ~425 MB into {cache_dir})\n"
            f"  - override with --nli-model NAME or {ENV_NLI_MODEL}=NAME\n"
            f"citesure fails fast on unloadable NLI models (D6) — it will not "
            f"silently fall back to another model."
        ) from exc

    id2label = getattr(model.config, "id2label", None) or None
    encoder = NLICrossEncoder(
        name=name,
        model=model,
        tokenizer=tokenizer,
        id2label=id2label,
    )
    with _cache_lock:
        _model_cache[name] = encoder
    return encoder


def clear_nli_model_cache() -> None:
    """Clear the module-level model cache (tests)."""
    with _cache_lock:
        _model_cache.clear()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_ENTAILMENT_RE = re.compile(r"\bentail", re.IGNORECASE)
_NEUTRAL_RE = re.compile(r"\bneutral\b", re.IGNORECASE)
_CONTRADICT_RE = re.compile(r"\bcontradict", re.IGNORECASE)


def _label_index(id2label: dict[int, str] | None) -> tuple[int | None, int | None]:
    """Locate the entailment (and neutral) label indices from id2label."""
    if not id2label:
        return None, None
    ent = neu = None
    for idx, label in id2label.items():
        low = str(label).lower()
        if ent is None and _ENTAILMENT_RE.search(low):
            ent = int(idx)
        elif neu is None and _NEUTRAL_RE.search(low):
            neu = int(idx)
    return ent, neu


def _contradiction_probs(
    model: torch.nn.Module,
    tokenizer: object,
    id2label: dict[int, str] | None,
    pairs: list[tuple[str, str]],
) -> list[float]:
    """Softmax over label logits → contradiction probability per pair.

    Same argument order and pairing convention as :func:`_entailment_probs`.
    Returns 0.0 for single-logit (binary) models that have no contradiction
    column — the override simply never fires for those.
    """
    if not pairs:
        return []

    import torch

    con_idx = None
    if id2label:
        for idx, label in id2label.items():
            if _CONTRADICT_RE.search(str(label)):
                con_idx = int(idx)
                break

    # Binary / single-logit model — no contradiction column.
    num_labels = getattr(getattr(model, "config", None), "num_labels", None)
    if con_idx is None or (num_labels is not None and num_labels < 3):
        return [0.0] * len(pairs)

    model.eval()
    out: list[float] = []
    with torch.no_grad():
        for i in range(0, len(pairs), DEFAULT_BATCH_SIZE):
            batch = pairs[i : i + DEFAULT_BATCH_SIZE]
            enc = tokenizer(
                [p[1] for p in batch],
                [p[0] for p in batch],
                padding=True,
                truncation=True,
                max_length=MAX_LENGTH,
                return_tensors="pt",
            )
            inputs = {k: v.to(model.device) for k, v in enc.items()}
            logits = model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)
            if con_idx >= int(probs.shape[-1]):
                out.extend([0.0] * len(batch))
            else:
                out.extend(float(x) for x in probs[:, con_idx].tolist())
    return out


def _entailment_probs(
    model: torch.nn.Module,
    tokenizer: object,
    id2label: dict[int, str] | None,
    pairs: list[tuple[str, str]],
) -> list[float]:
    """Softmax over label logits → entailment probability per pair.

    * 3-label NLI models (entailment/neutral/contradiction): softmax over all
      labels, take the entailment column (index located via ``id2label``;
      falls back to column 0, the conventional order).
    * Single-logit models (custom binary classifiers): sigmoid of the logit.
    Deterministic: eval mode, no_grad, fixed device.

    **Argument order (important):** ``pairs`` are ``(claim, passage)`` at this
    API boundary, but the underlying cross-encoder is trained on
    ``(premise, hypothesis)`` where the *premise* is the source text and the
    *hypothesis* is the statement being checked. We therefore feed the
    tokenizer ``(passage, claim)`` — i.e. the cited passage as premise and the
    claim as hypothesis. Feeding them the other way round makes a passage that
    merely *adds* information to the claim read as "neutral" (~0) rather than
    "entailed", silently collapsing every verdict toward unsupported.
    """
    if not pairs:
        return []
    import torch

    ent_idx, _neu = _label_index(id2label)
    num_labels = getattr(getattr(model, "config", None), "num_labels", None)

    model.eval()
    out: list[float] = []
    with torch.no_grad():
        for i in range(0, len(pairs), DEFAULT_BATCH_SIZE):
            batch = pairs[i : i + DEFAULT_BATCH_SIZE]
            # Pairs are (claim, passage). Cross-encoders expect
            # (premise, hypothesis); we want P(passage supports claim), so
            # the passage is the premise and the claim the hypothesis.
            # (Feeding them the other way around scores near 0 even for
            # clearly-entailed pairs — verified empirically against
            # cross-encoder/nli-deberta-v3-base.)
            enc = tokenizer(
                [p[1] for p in batch],
                [p[0] for p in batch],
                padding=True,
                truncation=True,
                max_length=MAX_LENGTH,
                return_tensors="pt",
            )
            inputs = {k: v.to(model.device) for k, v in enc.items()}
            logits = model(**inputs).logits  # (B, L) or (B,)
            if logits.dim() == 1:
                probs = torch.sigmoid(logits)
            else:
                probs = torch.softmax(logits, dim=-1)
                col = ent_idx if ent_idx is not None else 0
                if num_labels is not None and col >= int(num_labels):
                    col = 0
                probs = probs[:, col]
            out.extend(float(x) for x in probs.tolist())
    return out


def score_nli(model: NLICrossEncoder, claim: str, passage: str) -> float:
    """Entailment probability in ``[0, 1]`` for one (claim, passage) pair.

    ``model`` is anything exposing ``predict([(claim, passage)]) -> [float]``
    (an :class:`NLICrossEncoder` or a test double).
    """
    return float(model.predict([(claim, passage)])[0])


def score_nli_batch(
    model: NLICrossEncoder, pairs: list[tuple[str, str]]
) -> list[float]:
    """Entailment probabilities for many pairs (one batched forward pass).

    Returns a list aligned with ``pairs``; empty input → empty list.
    """
    if not pairs:
        return []
    return [float(p) for p in model.predict(list(pairs))]


def score_nli_batch_all(
    model: NLICrossEncoder, pairs: list[tuple[str, str]]
) -> list[tuple[float, float]]:
    """Entailment + contradiction probabilities for many pairs.

    Returns ``(ent, con)`` per pair; ``con`` is 0.0 for binary models.
    """
    if not pairs:
        return []
    return model.predict_all(list(pairs))


# ---------------------------------------------------------------------------
# D3 banding — pure functions (unit-testable without any model)
# ---------------------------------------------------------------------------


def nli_band(score: float) -> Status:
    """Map a raw NLI score to its D3 band (pinned thresholds)."""
    if score >= NLI_SUPPORTED_THRESHOLD:
        return Status.SUPPORTED
    if score >= NLI_AMBIGUOUS_THRESHOLD:
        return Status.AMBIGUOUS
    return Status.UNSUPPORTED


def status_for_nli(
    overlap_status: Status,
    nli_score: float | None,
    marker_locatable: bool = True,
    contradiction: float | None = None,
) -> Status:
    """Final D3 status given the tier-2 overlap status and the NLI score.

    See the module docstring for the full combination table. Rules:

    * JS-page ambiguity (``marker_locatable=False``) is sticky: there is no
      passage to score, so the verdict stays ``ambiguous`` (D3).
    * Otherwise the NLI band wins outright — it may upgrade
      (ambiguous→supported, unsupported→supported/ambiguous) or downgrade
      (supported→ambiguous/unsupported) relative to the lexical verdict.
    * Tier-1 statuses (unreachable/paywalled) never reach this function:
      NLI only runs on fetched pages.
    * D3.1 contradiction override: if ``contradiction`` is not None and
      ``>= CONTRADICTION_THRESHOLD``, the verdict is ``unsupported``
      regardless of the entailment band (only fires where ent < 0.5).
    """
    if not marker_locatable:
        return Status.AMBIGUOUS
    if nli_score is None:
        return overlap_status
    if contradiction is not None and contradiction >= CONTRADICTION_THRESHOLD:
        return Status.UNSUPPORTED
    return nli_band(nli_score)


def apply_nli_tier(
    verdict: Status,
    notes: list[str],
    *,
    nli_score: float | None,
    marker_locatable: bool,
    evidence: str,
    contradiction: float | None = None,
    pooled_contradiction: float | None = None,
) -> tuple[Status, int, float | None, list[str]]:
    """Apply the NLI tier to a tier-2 verdict. Returns
    ``(final_status, tier_reached, score, notes)``.

    * When ``nli_score is None`` (nothing to score — e.g. JS page with no
      passage) the verdict is held at its tier-2 status with
      ``tier_reached=2`` and an explanatory note.
    * Otherwise the D3 banding applies, ``tier_reached=3``, and the NLI score
      is recorded on the verdict.
    * D3.1: if ``contradiction`` is not None and >= CONTRADICTION_THRESHOLD,
      the verdict is forced unsupported regardless of the entailment band.
    * D3.2: if ``pooled_contradiction`` >= POOLED_CONTRADICTION_THRESHOLD, a
      claim-relevant sentence elsewhere in the captured evidence contradicts
      the claim, so support is vetoed regardless of the entailment band.
    """
    if nli_score is None:
        notes.append(
            "NLI tier: no scorable passage (marker not locatable) — "
            "verdict held at tier-2 status"
        )
        return verdict, 2, None, notes

    final = status_for_nli(
        verdict, nli_score, marker_locatable=marker_locatable, contradiction=contradiction
    )
    notes.append(
        f"NLI tier: entailment {nli_score:.3f} "
        f"(bands: >= {NLI_SUPPORTED_THRESHOLD} supported, "
        f">= {NLI_AMBIGUOUS_THRESHOLD} ambiguous, below unsupported)"
    )
    if contradiction is not None:
        notes.append(f"NLI tier: contradiction {contradiction:.3f}")
        # Only state the override as the CAUSE when it actually fired. Emitting
        # this note on any downgrade produced self-contradictory output such as
        # "contradiction 0.000 >= 0.5 - source contradicts claim" for cases that
        # were downgraded purely by a low entailment band.
        if contradiction >= CONTRADICTION_THRESHOLD:
            notes.append(
                f"NLI tier: contradiction {contradiction:.3f} >= "
                f"{CONTRADICTION_THRESHOLD} — source contradicts claim"
            )
    # D3.2: pooled contradiction over claim-relevant sentences. The
    # max-entailment pair's own contradiction is ~0.00 for most false-supports
    # even when another captured sentence contradicts the claim at 0.99+, so
    # pool the signal across the same premises. A strong claim-relevant
    # contradiction vetoes support whatever the winning sentence entails.
    if pooled_contradiction is not None:
        if pooled_contradiction >= POOLED_CONTRADICTION_THRESHOLD and final is Status.SUPPORTED:
            notes.append(
                f"pooled contradiction {pooled_contradiction:.3f} >= "
                f"{POOLED_CONTRADICTION_THRESHOLD} — a claim-relevant sentence "
                f"in the evidence contradicts the claim"
            )
            final = Status.UNSUPPORTED
    if final is not verdict:
        notes.append(f"NLI moved verdict {verdict.value} → {final.value}")
    return final, 3, round(float(nli_score), 4), notes
