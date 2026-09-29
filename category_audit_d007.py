"""
AAL-D-007 per-category accuracy audit — the defect check.

WHY THIS EXISTS
---------------
On 2026-09-05 the published D-007 headline ("category attribution is the wall
at 45.7-63.4%") was found to be depressed by a dataset defect. IM-DIS-CALCDATE
described a stale calculation date but the case disclosed only ONE shared date,
so the stated root cause was unrecoverable from anything the model could see.
All nine models scored exactly 0.0% on it, across three runs, and were right to.

Nothing caught it. The 22-check pre-publish audit compared scorecards to the
registry entry -- both derived from the same scoring, so both agreed. What was
missing was a check against the *shape* of the results rather than their
consistency.

THE SIGNATURE
-------------
A category on which EVERY model scores 0.0% is almost never a capability
finding. Independent models from different labs do not agree perfectly on
anything hard. Near-perfect agreement at either extreme means the task itself
is degenerate: unanswerable (no evidence in the case), or trivial (the answer
is given away).

This script reports per-category accuracy for every model and fails loudly on
either signature. Run it BEFORE the gate, not after.

Usage:
    python category_audit_d007.py
    python category_audit_d007.py --dataset datasets/AAL-D-007-v1.2.json \
                                  --results-dir eval_out_d007_v12
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

from d007_eval_common import DATASET_DEFAULT

DEAD_THRESHOLD = 0.005      # <0.5% on every model -> unanswerable
TRIVIAL_THRESHOLD = 0.995   # >99.5% on every model -> giveaway (informational)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=DATASET_DEFAULT)
    ap.add_argument("--results-dir", default="./eval_out_d007")
    ap.add_argument("--strict-trivial", action="store_true",
                    help="also fail on categories every model gets right")
    args = ap.parse_args()

    ds = json.load(open(args.dataset))
    gt = {c["case_id"]: c["ground_truth"] for c in ds["cases"]}
    cats = sorted({g["primary_dispute_category"] for g in gt.values()
                   if g["dispute_exists"]})

    n_expected = len(ds["cases"])
    per_model, incomplete = {}, []
    for path in sorted(glob.glob(os.path.join(args.results_dir,
                                              "eval_results_d007_*.json"))):
        if path.endswith(".bak"):
            continue
        slug = os.path.basename(path)[len("eval_results_d007_"):-len(".json")]
        records = json.load(open(path))

        # Skip abandoned or in-flight runs. A partial roster silently widens the
        # reported range -- the Qwen 3.8-Max partial (146/250, excluded from the
        # published entry by name) sat in eval_out_d007 for weeks and would
        # otherwise appear here as a tenth model with a misleading row.
        if len(records) < n_expected:
            incomplete.append((slug, len(records)))
            continue

        per = collections.defaultdict(lambda: [0, 0])
        for rec in records:
            g = gt.get(rec["case_id"])
            if not g or not g["dispute_exists"]:
                continue
            truth = g["primary_dispute_category"]
            for r in rec["runs"]:
                s = r.get("score") or {}
                if s.get("category_correct") is None:
                    continue
                per[truth][1] += 1
                per[truth][0] += bool(s["category_correct"])
        if per:
            per_model[slug] = per

    if not per_model:
        print(f"No results found in {args.results_dir}")
        return 1

    names = list(per_model)
    width = max(12, min(14, max(len(n) for n in names) + 1))
    print(f"dataset: {args.dataset}   version: {ds.get('version')}")
    print(f"results: {args.results_dir}   models: {len(names)}")
    if incomplete:
        print(f"  excluded as incomplete (<{n_expected} cases): "
              + ", ".join(f"{s} [{n}]" for s, n in incomplete))
    print()

    header = f"{'category':22}" + "".join(f"{n[:width-1]:>{width}}" for n in names)
    print(header)
    print("-" * len(header))

    dead, trivial = [], []
    for c in cats:
        row = f"{c:22}"
        rates = []
        for n in names:
            h, tot = per_model[n][c]
            rate = (h / tot) if tot else 0.0
            rates.append(rate)
            row += f"{rate * 100:{width - 1}.0f}%"
        print(row)
        if rates and max(rates) <= DEAD_THRESHOLD:
            dead.append(c)
        elif rates and min(rates) >= TRIVIAL_THRESHOLD:
            trivial.append(c)

    print("-" * len(header))
    row = f"{'OVERALL':22}"
    for n in names:
        h = sum(v[0] for v in per_model[n].values())
        tot = sum(v[1] for v in per_model[n].values())
        row += f"{(100 * h / tot if tot else 0):{width - 1}.1f}%"
    print(row)

    print()
    failed = False

    if dead:
        failed = True
        n_cases = sum(1 for g in gt.values()
                      if g["dispute_exists"] and g["primary_dispute_category"] in dead)
        share = 100 * n_cases / sum(1 for g in gt.values() if g["dispute_exists"])
        print("FAIL — category answered correctly by NO model:")
        for c in dead:
            print(f"    {c}")
        print(f"  {len(dead)} of {len(cats)} categories · {n_cases} disputed cases · "
              f"{share:.0f}% of the category denominator.")
        print("  Independent models do not agree perfectly on hard problems. Check whether")
        print("  the case actually discloses the evidence its ground truth names, and compare")
        print("  against a category that scores ~100% to see what the difference is.")
        print("  DO NOT publish a category-accuracy figure that includes these.")

    if trivial:
        label = "FAIL" if args.strict_trivial else "NOTE"
        if args.strict_trivial:
            failed = True
        print(f"{label} — category answered correctly by EVERY model:")
        for c in trivial:
            print(f"    {c}")
        print("  Saturation can be a real finding (detection is), but check the answer")
        print("  is not simply given away by a field present only on these cases.")

    if not dead and not trivial:
        print("PASS — no degenerate categories. Every category discriminates.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
