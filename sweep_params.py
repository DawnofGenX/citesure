"""Sweep min_overlap x margin for the pooled-contradiction veto.

One capture pass over all 108 cases; the sweep is then pure arithmetic over the
cached per-sentence scores, so the whole grid costs one NLI run instead of N.

Gate logic mirrors apply_nli_tier:
    supported  <=>  best_entailment >= 0.7
                  AND NOT (pooled_contr >= T_CONTRAD
                           AND pooled_contr > best_entailment + margin)
"""
import asyncio
import glob
import json
import os
import re
import sys

sys.path.insert(0, "src")
os.environ["CITECHECK_CACHE_DIR"] = "/tmp/citesure-v2"

from citesure.citations import Citation
from citesure.overlap import clean_claim, content_terms
from citesure.reachability import verify_citations

MODEL = ("/home/hermes/.cache/huggingface/hub/models--cross-encoder--nli-deberta-v3-base"
         "/snapshots/6c749ce3425cd33b46d187e45b92bbf96ee12ec7")

SENT = re.compile(r"(?<=[.!?])\s+")
MIN_CHARS = 25
SUPPORTED_T = 0.7
T_CONTRAD = 0.5

# (min_overlap, margin) grid
OVERLAPS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
MARGINS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30]


async def main() -> int:
    from citesure.nli import NLICrossEncoder, get_nli_model, score_nli_batch_all

    enc = get_nli_model(MODEL)
    items = json.load(open("evals/independent_set_v2.json"))
    real_all = NLICrossEncoder.predict_all
    caps: list[tuple[str, str]] = []

    def spy(_i, pairs, _r=real_all, _c=caps):
        _c.clear()
        _c.extend(pairs)
        return _r(_i, pairs)

    NLICrossEncoder.predict_all = spy

    # one pass: capture premises, score every sentence once at every overlap cut
    store = {}
    for n, it in enumerate(items, 1):
        cid = it["id"]
        cit = Citation(citation_id=cid, url=it["url"], claim=it["claim"])
        await verify_citations([cit], use_overlap=True, use_nli=True,
                               nli_model=MODEL)
        claim = it["claim"]
        ct = content_terms(clean_claim(claim))
        sent_scores = []
        for cl, p in caps:
            for s in SENT.split(p):
                s = s.strip()
                if len(s) < MIN_CHARS:
                    continue
                st = content_terms(s)
                ov = (len(ct & st) / len(ct)) if ct else 0.0
                sent_scores.append((ov, s))
        if sent_scores:
            sc = score_nli_batch_all(enc, [(claim, s) for _o, s in sent_scores])
        else:
            sc = []
        base = score_nli_batch_all(enc, list(caps)) if caps else []
        best_ent = max((e for e, _c in base), default=0.0)
        store[cid] = {
            "expected": it["expected_status"],
            "category": it["category"],
            "best_entail": best_ent,
            "sents": [(ov, e, c) for (ov, _s), (e, c) in zip(sent_scores, sc)],
        }
        if n % 25 == 0:
            print(f"  [{n}/{len(items)}]", flush=True)

    NLICrossEncoder.predict_all = real_all
    json.dump(store, open("/tmp/sweep_cache.json", "w"))
    print(f"captured {len(store)} cases -> /tmp/sweep_cache.json\n")

    def verdict(rec, mo, mg):
        ent = rec["best_entail"]
        if ent < SUPPORTED_T:
            return "unsupported"
        pool = [c for ov, _e, c in rec["sents"] if ov >= mo]
        pc = max(pool) if pool else 0.0
        if pc >= T_CONTRAD and pc > ent + mg:
            return "unsupported"
        return "supported"

    def score_cfg(mo, mg):
        ok = 0
        fs = {"negation-flip": [0, 0], "entity-swap": [0, 0]}
        for _cid, r in store.items():
            v = verdict(r, mo, mg)
            ok += int(v == r["expected"])
            if r["expected"] == "unsupported" and v == "supported":
                if r["category"] in fs:
                    fs[r["category"]][0] += 1
                fs[r["category"]][1] += 1
        return ok, fs["negation-flip"][0], fs["entity-swap"][0]

    print(f"{'overlap':>8s} " + " ".join(f"{('m'+str(m)):>7s}" for m in MARGINS))
    print("  (cells = agreement/108; ! = entity-swap regression vs 6)")
    best = []
    for mo in OVERLAPS:
        row = []
        for mg in MARGINS:
            ok, nf, ef = score_cfg(mo, mg)
            flag = "!" if ef > 6 else " "
            row.append(f"{ok:>6}{flag}")
            best.append((ok, ef, nf, mo, mg))
        print(f"{mo:>8.1f} " + " ".join(row))

    best.sort(key=lambda t: (-t[0], t[1], -t[2]))
    print("\nTop configs (agreement, entity-swap-FS, negation-FS, overlap, margin):")
    for ok, ef, nf, mo, mg in best[:10]:
        print(f"  {ok:>3}/108  ent-FS={ef:<3d} neg-FS={nf:<3d}  min_overlap={mo}  margin={mg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
