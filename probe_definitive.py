"""Definitive: for each missed case, is ANY scored premise containing the claim?"""
import asyncio
import json
import os
import sys

sys.path.insert(0, "src")
os.environ["CITECHECK_CACHE_DIR"] = "/tmp/citesure-v2"

from citesure.citations import Citation
from citesure.reachability import verify_citations

MODEL = ("/home/hermes/.cache/huggingface/hub/models--cross-encoder--nli-deberta-v3-base"
         "/snapshots/6c749ce3425cd33b46d187e45b92bbf96ee12ec7")

TARGETS = ["ind-104", "ind-131", "ind-161", "ind-164", "ind-179", "ind-185", "ind-191", "ind-194"]

FURNITURE = ("[view email]", "[v1]", "UTC (", "Submitted on", "Computer Science >",
             "Bibliographic", "ISSN", "DOI)", "arXiv:")


async def main() -> int:
    from citesure.nli import get_nli_model
    enc = get_nli_model(MODEL)
    real = enc.predict_all
    captured = []

    def spy(pairs):
        captured.clear()
        captured.extend(pairs)
        return real(pairs)

    enc.predict_all = spy

    items = {i["id"]: i for i in json.load(open("evals/independent_set_v2.json"))}
    for tid in TARGETS:
        c = items[tid]
        cit = Citation(citation_id=tid, url=c["url"], claim=c["claim"])
        await verify_citations([cit], use_overlap=True, use_nli=True, nli_model=MODEL)
        # examine EVERY premise scored, not just the longest
        hits = [p for _, p in captured if c["claim"][:50].lower() in p.lower()]
        furn = [p for _, p in captured if any(f in p for f in FURNITURE)]
        print(f"{tid}  n_pairs={len(captured)}  "
              f"ANY premise contains claim: {bool(hits)}  furniture premises: {len(furn)}")
        for h in hits:
            print(f"      MATCH: {h[:150]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
