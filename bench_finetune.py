"""Is a targeted contrast-set fine-tune viable on this CPU-only box?

Measures real training throughput for the smallest intervention that could work
(LoRA-style / partial unfreeze on deberta-v3-base) so the option-4 estimate is
measured, not guessed.
"""
import os
import time

os.environ.setdefault("CITECHECK_CACHE_DIR", "/tmp/citesure-v2")

MODEL = ("/home/hermes/.cache/huggingface/hub/models--cross-encoder--nli-deberta-v3-base"
         "/snapshots/6c749ce3425cd33b46d187e45b92bbf96ee12ec7")

import torch
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer

torch.manual_seed(0)


class Pairs(Dataset):
    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n

    def __getitem__(self, i):
        return (["claim number %d says a fact about a subject" % i,
                 "claim number %d says a fact about a subject" % (i + 1)],
                ["premise number %d states something different" % (i % 7),
                 "premise number %d agrees exactly" % i])


def collate(batch):
    a = [b[0][0] for b in batch]
    b = [b[1][0] for b in batch]
    return a, b


def bench(freeze_encoder: bool, n: int, bs: int) -> float:
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL, num_labels=3)
    if freeze_encoder:
        for p in model.base_model.parameters():
            p.requires_grad = False
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=2e-5)
    dl = DataLoader(Pairs(n), batch_size=bs, collate_fn=collate)
    model.train()
    t0 = time.perf_counter()
    steps = 0
    for a, b in dl:
        enc = tok(a, b, truncation=True, max_length=256, padding=True,
                  return_tensors="pt")
        out = model(**enc)
        out.logits.sum().backward()
        opt.step()
        opt.zero_grad()
        steps += 1
    return time.perf_counter() - t0, steps


if __name__ == "__main__":
    for freeze in (True, False):
        el, steps = bench(freeze, n=8, bs=4)
        per_step = el / steps
        print(f"freeze_encoder={freeze!s:<5} {steps} steps in {el:.1f}s "
              f"-> {per_step:.2f}s/step")
        for target, name in ((3600, "1 hour"), (4 * 3600, "4 hours")):
            n_steps = target / per_step
            seen = n_steps * 4
            print(f"    {name}: ~{n_steps:.0f} steps = {seen:.0f} examples "
                  f"(bs=4)")
