#!/usr/bin/env python3
"""Before/after comparison for the clause-mixture rule experiment.

Reads the baseline and rule-active result JSONs for a set and prints a
per-family before/after table plus the acceptance-gate evaluation.
"""
import json
import glob


def latest(pattern):
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"no result JSON matching {pattern}")
    return json.load(open(files[-1]))


def family_counts(d):
    out = {}
    for cat, v in d["per_category_recall"].items():
        if v["total"]:
            out[cat] = (v["correct"], v["total"])
    return out


def per_case(d):
    return {r["id"]: r for r in d["records"]}


def compare(base_path, rule_path, label):
    base = latest(base_path)
    rule = latest(rule_path)
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    print(f"baseline : {base['matched']}/{base['total']} = {base['agreement_pct']}%")
    print(f"rule     : {rule['matched']}/{rule['total']} = {rule['agreement_pct']}%")
    delta = rule["matched"] - base["matched"]
    print(f"delta    : {delta:+d} cases")

    bf, rf = family_counts(base), family_counts(rule)
    cats = sorted(set(bf) | set(rf))
    print(f"\n{'family':28s} {'base':>8s} {'rule':>8s} {'delta':>7s}")
    for cat in cats:
        b, r = bf.get(cat, (0, 0)), rf.get(cat, (0, 0))
        d = r[0] - b[0]
        flag = "  <-- LOSS" if d < 0 else ("  <-- GAIN" if d > 0 else "")
        print(f"{cat:28s} {b[0]:3d}/{b[1]:<3d} {r[0]:3d}/{r[1]:<3d} {d:+5d}{flag}")

    bc, rc = per_case(base), per_case(rule)
    gained = [i for i in rc if rc[i]["match"] and not bc.get(i, {}).get("match")]
    lost = [i for i in bc if bc[i]["match"] and not rc.get(i, {}).get("match")]
    print(f"\ngained ({len(gained)}): {sorted(gained)}")
    print(f"lost   ({len(lost)}): {sorted(lost)}")
    for i in sorted(lost):
        r = rc[i]
        print(f"   LOST {i} [{r['category']}] exp={r['expected']} "
              f"base={bc[i]['predicted']} rule={r['predicted']}")
    for i in sorted(gained):
        r = rc[i]
        print(f"   GAIN {i} [{r['category']}] exp={r['expected']} "
              f"base={bc[i]['predicted']} rule={r['predicted']}")
    return base, rule


if __name__ == "__main__":
    v4b, v4r = compare(
        "evals/results/clause_baseline_v4/*.json",
        "evals/results/clause_rule_v4/*.json",
        "v4 (held-out, 83 cases)",
    )
    v2b, v2r = compare(
        "evals/results/clause_baseline_v2/*.json",
        "evals/results/clause_rule_v2/*.json",
        "v2 (in-sample, 108 cases)",
    )

    # ---- acceptance gate (pre-committed) ---------------------------------
    print(f"\n{'='*70}\nACCEPTANCE GATE (pre-committed)\n{'='*70}")
    v4b_f, v4r_f = family_counts(v4b), family_counts(v4r)
    v2b_f, v2r_f = family_counts(v2b), family_counts(v2r)

    mixed_gain = v4r_f["partially-true-mixed"][0] - v4b_f["partially-true-mixed"][0]
    v4_delta = v4r["matched"] - v4b["matched"]
    v2_delta = v2r["matched"] - v2b["matched"]

    def max_family_loss(bf, rf):
        worst, worst_cat = 0, None
        for cat in set(bf) & set(rf):
            loss = bf[cat][0] - rf[cat][0]
            if loss > worst:
                worst, worst_cat = loss, cat
        return worst, worst_cat

    v4_loss, v4_loss_cat = max_family_loss(v4b_f, v4r_f)
    v2_loss, v2_loss_cat = max_family_loss(v2b_f, v2r_f)

    checks = [
        ("v4 mixed recall improves by >= 5 of 11", mixed_gain >= 5, f"gain={mixed_gain}"),
        ("v4 overall does not fall by more than 2", v4_delta >= -2, f"delta={v4_delta:+d}"),
        ("v2 overall does not fall by more than 2", v2_delta >= -2, f"delta={v2_delta:+d}"),
        ("no family in either set loses more than 2",
         v4_loss <= 2 and v2_loss <= 2,
         f"v4 worst={v4_loss} ({v4_loss_cat}), v2 worst={v2_loss} ({v2_loss_cat})"),
    ]
    all_pass = True
    for name, ok, detail in checks:
        all_pass = all_pass and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:45s} {detail}")
    print(f"\n  GATE: {'PASS — keep the change' if all_pass else 'FAIL — revert and record negative result'}")
