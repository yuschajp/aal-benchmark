"""
AAL-D-006 run-to-run flip rate.

Adapted from flip_rate_d007.py — same question, same logic, different field
names. D-006 (Collateral Eligibility & Substitution) uses `eligible` where
D-007 uses `dispute_exists`, and additionally exposes `category_correct`
per run, which D-007 does not track as a separate flip metric. This script
adds a third flip rate (category) that flip_rate_d007.py doesn't compute.

Three flip rates, all computed from existing eval_results_d006_*.json files:
  detection flip rate  -- did `eligible` differ across a case's own runs?
  category flip rate   -- among cases with >=2 non-error runs, did
                            category_correct differ across those runs?
  value flip rate       -- among cases with >=2 non-error runs where
                            value_correct is not None, did value_correct
                            differ across those runs?

Note on denominators: eligible run counts vary by model, same as D-007 --
a model with run errors leaves some cases without two scoreable runs, so
its denominator is smaller than a clean model's. The artifact records the
denominator per model so this is never silently lost.

Usage:
    python flip_rate_d006.py                                  # all models, stdout
    python flip_rate_d006.py --out                            # + write default JSON artifact
    python flip_rate_d006.py --out path/to/file.json          # + write to a specific path
    python flip_rate_d006.py eval_out_d006/eval_results_d006_claude-opus-5.json
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCORER_VERSION = "d006-flip-v1.0.0"
DEFAULT_OUT = "eval_out_d006/flip_rate_d006.json"


def flip_rates(path: Path) -> dict:
    data = json.loads(path.read_text())
    n_cases = len(data)
    detect_flip = 0
    category_flip = 0
    category_eligible = 0
    value_flip = 0
    value_eligible = 0

    for rec in data:
        runs = [r for r in rec["runs"] if not r["score"].get("error")]
        if len(runs) < 2:
            continue

        # Detection flip: does the eligibility call itself differ across runs?
        eligible_calls = set(bool(r["prediction"].get("eligible")) for r in runs)
        if len(eligible_calls) > 1:
            detect_flip += 1

        # Category flip: among runs with a scoreable category_correct value,
        # does correctness itself flip? (New vs. D-007 -- D-006 exposes this
        # per-run field, D-007's schema did not track it separately.)
        ccs = [r["score"].get("category_correct") for r in runs
               if r["score"].get("category_correct") is not None]
        if len(ccs) >= 2:
            category_eligible += 1
            if len(set(ccs)) > 1:
                category_flip += 1

        # Value flip: same logic as D-007 -- among runs with a scoreable
        # value_correct, does correctness flip across runs on the same case?
        vcs = [r["score"].get("value_correct") for r in runs
               if r["score"].get("value_correct") is not None]
        if len(vcs) >= 2:
            value_eligible += 1
            if len(set(vcs)) > 1:
                value_flip += 1

    return {
        "n_cases": n_cases,
        "detect_flip": detect_flip,
        "detect_flip_pct": round(100 * detect_flip / n_cases, 1) if n_cases else None,
        "category_eligible": category_eligible,
        "category_flip": category_flip,
        "category_flip_pct": round(100 * category_flip / category_eligible, 1) if category_eligible else None,
        "value_eligible": value_eligible,
        "value_flip": value_flip,
        "value_flip_pct": round(100 * value_flip / value_eligible, 1) if value_eligible else None,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Compute run-to-run flip rates for AAL-D-006 results files.")
    ap.add_argument("results", nargs="*",
                    help="specific eval_results_d006_*.json files "
                         "(default: all under eval_out_d006/)")
    ap.add_argument("--out", nargs="?", const=DEFAULT_OUT, default=None, metavar="PATH",
                    help=f"also write a JSON artifact (default path: {DEFAULT_OUT})")
    args = ap.parse_args()

    targets = ([Path(p) for p in args.results] if args.results
               else [Path(p) for p in sorted(glob.glob("eval_out_d006/eval_results_d006_*.json"))])
    if not targets:
        sys.exit("no results files found under eval_out_d006/")

    models = {}
    for path in targets:
        model = path.stem.replace("eval_results_d006_", "")
        r = flip_rates(path)
        models[model] = r

        complete = "" if r["n_cases"] >= 250 else f"  [WARNING: only {r['n_cases']}/250 cases -- run may be incomplete]"
        print(f"{model}:{complete}")
        print(f"  detection flip rate:  {r['detect_flip']}/{r['n_cases']} cases "
              f"({r['detect_flip_pct']}%) gave a different eligible verdict across its own runs")
        if r["category_eligible"]:
            print(f"  category flip rate:   {r['category_flip']}/{r['category_eligible']} eligible cases "
                  f"({r['category_flip_pct']}%) got the reason category right on some runs and wrong on others, same case")
        else:
            print("  category flip rate:   n/a (no eligible cases)")
        if r["value_eligible"]:
            print(f"  value flip rate:      {r['value_flip']}/{r['value_eligible']} eligible cases "
                  f"({r['value_flip_pct']}%) got the value right on some runs and wrong on others, same case")
        else:
            print("  value flip rate:      n/a (no eligible cases -- check for errors or a 0-exception-caught run)")
        print()

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)

        dets = [m["detect_flip_pct"] for m in models.values() if m["detect_flip_pct"] is not None]
        cats = [m["category_flip_pct"] for m in models.values() if m["category_flip_pct"] is not None]
        vals = [m["value_flip_pct"] for m in models.values() if m["value_flip_pct"] is not None]

        artifact = {
            "scorer_version": SCORER_VERSION,
            "dataset": "AAL-D-006-v1.1",
            "metric": "run-to-run flip rate (self-consistency across a model's own 3 runs)",
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_models": len(models),
            "models": models,
            "summary": {
                "detection_flip_pct_range": [min(dets), max(dets)] if dets else None,
                "category_flip_pct_range": [min(cats), max(cats)] if cats else None,
                "value_flip_pct_range": [min(vals), max(vals)] if vals else None,
            },
            "notes": (
                "category_eligible and value_eligible are per-model denominators: cases where "
                "the model had at least two non-error runs with a scoreable field. They vary by "
                "model because run errors reduce the number of scoreable runs. Compare each flip "
                "count against its own eligible denominator, not a fixed 250."
            ),
        }
        out.write_text(json.dumps(artifact, indent=2) + "\n")
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()
