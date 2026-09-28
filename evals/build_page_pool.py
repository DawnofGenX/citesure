#!/usr/bin/env python3
"""Verify every page_pool.json URL is fetchable and yields extractable text.

Usage:
    .venv/bin/python evals/build_page_pool.py --in evals/page_pool.json \
        --out evals/page_pool_verified.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def check(entry: dict) -> dict:
    """Fetch one URL and record whether it is usable as a positive-claim source."""
    from citesure.fetcher import fetch

    try:
        page = await fetch(entry["url"])
    except Exception as exc:  # noqa: BLE001 - pool building must not abort
        return {**entry, "ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    text = page.text or ""
    slug = entry["url"].split("//", 1)[1].replace("/", "_")
    return {
        **entry,
        "ok": bool(page.ok and len(text) >= 800),
        "reason": "" if page.ok else (page.error or "fetch failed"),
        "extracted_chars": len(text),
        "paywall_detected": bool(getattr(page, "paywall_detected", False)),
        "retraction_detected": bool(getattr(page, "retraction_detected", False)),
        "text_path": f"evals/page_text/{slug}.txt",
        "text": text,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="evals/page_pool.json")
    ap.add_argument("--out", default="evals/page_pool_verified.json")
    ap.add_argument("--text-dir", default="evals/page_text")
    args = ap.parse_args()

    os.environ.setdefault("CITECHECK_CACHE_DIR", "/tmp/citesure-pool")

    entries = json.loads(Path(args.src).read_text(encoding="utf-8"))
    out: list[dict] = []
    for i, entry in enumerate(entries, 1):
        print(f"[{i}/{len(entries)}] {entry['url']}", flush=True)
        out.append(await check(entry))

    # Persist extracted text so claim authoring reads exactly what the pipeline sees.
    text_dir = Path(args.text_dir)
    text_dir.mkdir(parents=True, exist_ok=True)
    for rec in out:
        if rec.get("text"):
            Path(rec["text_path"]).write_text(rec["text"], encoding="utf-8")
            rec.pop("text")  # keep the manifest small

    ok = [r for r in out if r["ok"]]
    print(f"\n[pool] {len(ok)}/{len(out)} fetchable and extractable")
    for r in out:
        if not r["ok"]:
            print(f"  UNUSABLE {r['url']}  ({r['reason'][:70]})")
    paywalled = [r for r in out if r.get("paywall_detected")]
    print(f"[pool] {len(paywalled)} paywalled candidates: {[r['url'] for r in paywalled]}")

    Path(args.out).write_text(
        json.dumps(
            {
                "generated_utc": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
                "fetchable": len(ok),
                "total": len(out),
                "entries": out,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[pool] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
