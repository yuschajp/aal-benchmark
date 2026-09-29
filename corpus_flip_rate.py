#!/usr/bin/env python3
"""
corpus_flip_rate.py — run-to-run flip rate across the whole AAL corpus.

WHAT THIS MEASURES
------------------
Every AAL dataset runs each case three times. Published accuracy pools those
three runs and reports one number. Flip rate asks what pooling discards: on a
given case, did the model's own answer change between its own runs?

A model that is 70% accurate because it is reliably right on 70% of cases is a
different proposition from a model that is 70% accurate by averaging over a
partly random process. Pooled accuracy reports both as 70%.

AAL-D-007 v1.2 established this on one dataset — value flip rate ranged 2.0% to
37.5% across nine models, a wider spread than the accuracy range itself. This
script asks whether the pattern holds across every dataset already published.

No model calls. Everything is computed from results files already on disk.

DIMENSIONS
----------
detection   did the model change its mind about whether a problem exists
category    did it change which root cause it named
value       did it change the number

The value field is named differently across dataset generations (amount_correct
in D-002, numeric_correct in D-003, value_correct in D-004 onward). The mapping
is explicit below rather than guessed at run time.

DENOMINATORS
------------
A case counts toward a dimension only if it has at least two non-errored runs
where that dimension was scored. Cases scored on fewer runs cannot flip by
definition and are excluded rather than counted as stable — counting them as
stable would flatter models with more errors, which is backwards.

Denominators are reported alongside every rate. They differ by model and by
dataset, and the rates are not on a common base.

USAGE
-----
    python corpus_flip_rate.py
    python corpus_flip_rate.py --json corpus_flip_rate.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent

# Dataset -> (results glob, value-field name).
# Directories are named explicitly. The corpus contains dryrun, smoke and
# per-batch directories that are not the published runs; listing the intended
# ones by hand avoids silently averaging a smoke test into a published figure.
DATASETS = [
    ("D-002", "aal-eval-platform/eval_out_d002_full/eval_results_*.json", "amount_correct"),
    ("D-003", "aal-eval-platform/eval_out_d003_v11/eval_results_d003_*.json",  "numeric_correct"),
    ("D-004", "aal-eval-platform/eval_out_d004/eval_results_d004_*.json",      "value_correct"),
    ("D-006", "aal-benchmark/eval_out_d006/eval_results_d006_*.json",          "value_correct"),
    ("D-007", "aal-benchmark/eval_out_d007_v12/eval_results_d007_*.json",      "value_correct"),
]

# Qwen 3.8-Max was attempted four times on D-007 and never completed. It is
# excluded by name from the published entry and is excluded here for the same
# reason.
EXCLUDE = ["qwen"]


def model_name(path: str) -> str:
    base = os.path.basename(path)
    for prefix in ("eval_results_d002_", "eval_results_d003_",
                   "eval_results_d004_", "eval_results_d006_",
                   "eval_results_d007_", "eval_results_"):
        base = base.replace(prefix, "")
    for provider in ("openai_", "anthropic_", "google_"):
        if base.startswith(provider):
            base = base[len(provider):]
    return base.replace(".json", "")


def flip_rates(path: str, value_field: str) -> dict:
    """Per-dimension flip rate for one model on one dataset."""
    with open(path) as fh:
        cases = json.load(fh)

    if not any(c.get("runs") for c in cases):
        return None      # aborted launch, no runs recorded

    dims = {
        "detection": "detection_correct",
        "category":  "category_correct",
        "value":     value_field,
    }

    flipped = defaultdict(int)
    eligible = defaultdict(int)
    n_errors = 0
    n_runs = 0

    for case in cases:
        runs = case.get("runs") or []
        n_runs += len(runs)

        scores = []
        for run in runs:
            s = run.get("score") or {}
            if s.get("error"):
                n_errors += 1
                continue
            scores.append(s)

        for dim, field in dims.items():
            vals = [s[field] for s in scores if s.get(field) is not None]
            if len(vals) < 2:
                # Cannot flip on fewer than two scored runs. Excluded rather
                # than counted as stable.
                continue
            eligible[dim] += 1
            if len(set(vals)) > 1:
                flipped[dim] += 1

    return {
        "model": model_name(path),
        "n_cases": len(cases),
        "n_runs": n_runs,
        "n_errors": n_errors,
        "rates": {
            dim: {
                "flip_rate": (flipped[dim] / eligible[dim] * 100) if eligible[dim] else None,
                "flipped": flipped[dim],
                "eligible": eligible[dim],
            }
            for dim in dims
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=str, default=None, help="also write results to this path")
    args = ap.parse_args()

    all_results = {}

    for label, pattern, value_field in DATASETS:
        paths = sorted(glob.glob(str(REPO / pattern)))
        paths = [p for p in paths if not any(x in p.lower() for x in EXCLUDE)]
        if not paths:
            print(f"{label}: no results found at {pattern}\n")
            continue

        rows = [flip_rates(p, value_field) for p in paths]
        skipped = [p for p, r in zip(paths, rows) if r is None]
        rows = [r for r in rows if r is not None]
        for s in skipped:
            print(f"  (skipped {os.path.basename(s)} — no runs recorded)")
        if not rows:
            print(f"{label}: no usable results\n"); continue
        all_results[label] = {"value_field": value_field, "models": rows}

        print("=" * 78)
        print(f"{label}   ({len(rows)} models, value scored as '{value_field}')")
        print("=" * 78)
        print(f"{'model':<32}{'detection':>12}{'category':>12}{'value':>12}{'errors':>9}")
        print("-" * 78)

        for r in sorted(rows, key=lambda x: (x["rates"]["value"]["flip_rate"] is None,
                                             x["rates"]["value"]["flip_rate"] or 0)):
            def fmt(dim):
                v = r["rates"][dim]["flip_rate"]
                return "  n/a" if v is None else f"{v:.1f}%"
            print(f"{r['model']:<32}{fmt('detection'):>12}{fmt('category'):>12}"
                  f"{fmt('value'):>12}{r['n_errors']:>9}")

        vals = [r["rates"]["value"]["flip_rate"] for r in rows
                if r["rates"]["value"]["flip_rate"] is not None]
        cats = [r["rates"]["category"]["flip_rate"] for r in rows
                if r["rates"]["category"]["flip_rate"] is not None]
        dets = [r["rates"]["detection"]["flip_rate"] for r in rows
                if r["rates"]["detection"]["flip_rate"] is not None]
        print("-" * 78)
        if dets:
            print(f"  detection flip spread: {min(dets):.1f}% – {max(dets):.1f}%")
        if cats:
            print(f"  category flip spread:  {min(cats):.1f}% – {max(cats):.1f}%")
        if vals:
            print(f"  value flip spread:     {min(vals):.1f}% – {max(vals):.1f}%")
        print()

        # Eligible-case counts, since the rates above are not on a common base.
        print("  eligible cases per model (value dimension):")
        for r in sorted(rows, key=lambda x: x["model"]):
            print(f"    {r['model']:<30} {r['rates']['value']['eligible']:>4} "
                  f"of {r['n_cases']}")
        print()

    print("=" * 78)
    print("READING THIS")
    print("=" * 78)
    print("Flip rate is the share of eligible cases where a model gave different")
    print("answers across its own runs on the identical case. It is not an error")
    print("rate — a model can flip between two wrong answers, or between right and")
    print("wrong. High accuracy with a high flip rate means the accuracy figure is")
    print("an average over a partly random process.")
    print()
    print("Eligible-case counts differ by model because errored runs are excluded")
    print("rather than imputed. The rates are not computed on a common base and")
    print("should be read with the counts above.")

    if args.json:
        Path(args.json).write_text(json.dumps(all_results, indent=2))
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
