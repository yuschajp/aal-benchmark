#!/usr/bin/env python3
"""
AAL-D-007 — Common-base accuracy

The per-model scorecards compute accuracy over whatever observations that model
managed to return. Denominators therefore differ by model (237 to 486 on the
v1.2 roster), which makes a side-by-side ranking a comparison across different
bases.

This script restricts every model to the intersection: only (case_id, run_index)
positions where EVERY model returned a valid, non-errored, gt_disputed
observation. All models are then scored on an identical denominator.

Usage:
    python common_base_d007.py --results-dir eval_out_d007_v12
    python common_base_d007.py --results-dir eval_out_d007_v12 --exclude qwen
"""

from __future__ import annotations
import argparse
import glob
import json
import os
from collections import defaultdict


def model_name(path: str) -> str:
    return os.path.basename(path).replace("eval_results_d007_", "").replace(".json", "")


def load_observations(path: str) -> dict[tuple[str, int], dict]:
    """Return {(case_id, run_index): score_dict} for every valid disputed observation."""
    out = {}
    with open(path) as fh:
        cases = json.load(fh)
    for case in cases:
        cid = case["case_id"]
        for i, run in enumerate(case.get("runs", [])):
            score = run.get("score") or {}
            if score.get("error"):
                continue
            # category accuracy is only defined on disputed cases
            if not score.get("gt_disputed"):
                continue
            out[(cid, i)] = score
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="eval_out_d007_v12")
    ap.add_argument("--exclude", nargs="*", default=["qwen"],
                    help="substrings; any model file matching one is skipped "
                         "(default excludes qwen, which is not part of the published roster)")
    args = ap.parse_args()

    pattern = os.path.join(args.results_dir, "eval_results_d007_*.json")
    paths = sorted(glob.glob(pattern))
    paths = [p for p in paths if not any(x.lower() in p.lower() for x in args.exclude)]

    if not paths:
        raise SystemExit(f"no results files found under {args.results_dir}/")

    per_model = {model_name(p): load_observations(p) for p in paths}

    print(f"results dir: {args.results_dir}")
    print(f"models: {len(per_model)}")
    if args.exclude:
        print(f"excluded (substring match): {', '.join(args.exclude)}")
    print()

    print("=== per-model denominators, as scored today ===")
    for name, obs in sorted(per_model.items()):
        print(f"  {name:30s} {len(obs):4d} valid disputed observations")
    print()

    # Intersection across all models
    common = set.intersection(*(set(obs.keys()) for obs in per_model.values()))
    print(f"=== common base ===")
    print(f"  {len(common)} (case, run) observations valid across ALL {len(per_model)} models")
    common_cases = {cid for cid, _ in common}
    print(f"  spanning {len(common_cases)} distinct cases")
    print()

    if not common:
        raise SystemExit("intersection is empty — nothing comparable to report")

    print("=== accuracy on the common base (identical denominator for every model) ===")
    print(f"{'model':30s} {'category':>10s} {'value':>10s} {'component':>10s} {'n':>6s}")
    print("-" * 70)

    rows = []
    for name, obs in per_model.items():
        cat_hits = sum(1 for k in common if obs[k].get("category_correct"))
        val_hits = sum(1 for k in common if obs[k].get("value_correct"))
        cmp_hits = sum(1 for k in common if obs[k].get("component_correct"))
        n = len(common)
        rows.append((name, cat_hits / n, val_hits / n, cmp_hits / n, n))

    for name, cat, val, cmp_, n in sorted(rows, key=lambda r: -r[1]):
        print(f"{name:30s} {cat*100:9.1f}% {val*100:9.1f}% {cmp_*100:9.1f}% {n:6d}")

    print()
    cats = [r[1] for r in rows]
    print(f"category accuracy range on common base: {min(cats)*100:.1f}% to {max(cats)*100:.1f}%")
    vals = [r[2] for r in rows]
    print(f"value accuracy range on common base:    {min(vals)*100:.1f}% to {max(vals)*100:.1f}%")
    print()
    print("Note: these figures are directly comparable across models. The")
    print("per-model scorecard figures are not, because each uses its own")
    print("denominator. Publish one or the other with its basis stated; do")
    print("not mix them in a single table.")


if __name__ == "__main__":
    main()
