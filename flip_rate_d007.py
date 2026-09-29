"""
AAL-D-007 run-to-run flip rate.

Detection is already known to saturate near 100% across the corpus, but the
pooled Wilson-CI scorecard never asks a different question: on the SAME case,
across a model's own 3 runs, does it give a DIFFERENT answer? That's the
deterministic-engine version of the "reliability collapses under repeated
runs" finding AAL cites from third-party benchmarks (e.g. the CLEAR
framework's 60%->25% consistency drop) -- except here it's a first-party
number, computed from data already on disk, no new model calls required.

Two flip rates, both computed from existing eval_results_d007_*.json files:
  detection flip rate  -- did dispute_exists differ across a case's own runs?
  value flip rate       -- among cases where the model caught a real dispute
                            on at least 2 non-error runs, did value_correct
                            (right IM amount) differ across those runs?

Note on denominators: value_eligible varies by model. A model with run errors
(e.g. Grok 4.5's 22) leaves some cases without two scoreable runs, so its
denominator is smaller than a clean model's. The artifact records the
denominator per model so this is never silently lost.

Usage:
    python flip_rate_d007.py                                  # all models, stdout
    python flip_rate_d007.py --out                            # + write default JSON artifact
    python flip_rate_d007.py --out path/to/file.json          # + write to a specific path
    python flip_rate_d007.py eval_out_d007/eval_results_d007_claude-sonnet-5.json
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCORER_VERSION = "d007-flip-v1.1.0"
DEFAULT_OUT = "eval_out_d007/flip_rate_d007.json"


def flip_rates(path: Path) -> dict:
    data = json.loads(path.read_text())
    n_cases = len(data)
    detect_flip = 0
    value_flip = 0
    value_eligible = 0

    for rec in data:
        runs = [r for r in rec["runs"] if not r["score"].get("error")]
        if len(runs) < 2:
            continue

        dispute_calls = set(bool(r["prediction"].get("dispute_exists")) for r in runs)
        if len(dispute_calls) > 1:
            detect_flip += 1

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
        "value_eligible": value_eligible,
        "value_flip": value_flip,
        "value_flip_pct": round(100 * value_flip / value_eligible, 1) if value_eligible else None,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Compute run-to-run flip rates for AAL-D-007 results files.")
    ap.add_argument("results", nargs="*",
                    help="specific eval_results_d007_*.json files "
                         "(default: all under eval_out_d007/)")
    ap.add_argument("--out", nargs="?", const=DEFAULT_OUT, default=None, metavar="PATH",
                    help=f"also write a JSON artifact (default path: {DEFAULT_OUT})")
    args = ap.parse_args()

    targets = ([Path(p) for p in args.results] if args.results
               else [Path(p) for p in sorted(glob.glob("eval_out_d007/eval_results_d007_*.json"))])
    if not targets:
        sys.exit("no results files found under eval_out_d007/")

    models = {}
    for path in targets:
        model = path.stem.replace("eval_results_d007_", "")
        r = flip_rates(path)
        models[model] = r

        complete = "" if r["n_cases"] >= 250 else f"  [WARNING: only {r['n_cases']}/250 cases -- run may be incomplete]"
        print(f"{model}:{complete}")
        print(f"  detection flip rate:  {r['detect_flip']}/{r['n_cases']} cases "
              f"({r['detect_flip_pct']}%) gave a different dispute_exists verdict across its own runs")
        if r["value_eligible"]:
            print(f"  value flip rate:      {r['value_flip']}/{r['value_eligible']} eligible cases "
                  f"({r['value_flip_pct']}%) got the IM amount right on some runs and wrong on others, same case")
        else:
            print("  value flip rate:      n/a (no eligible cases -- check for errors or a 0-dispute-caught run)")
        print()

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)

        dets = [m["detect_flip_pct"] for m in models.values() if m["detect_flip_pct"] is not None]
        vals = [m["value_flip_pct"] for m in models.values() if m["value_flip_pct"] is not None]

        artifact = {
            "scorer_version": SCORER_VERSION,
            "dataset": "AAL-D-007-v1.1",
            "metric": "run-to-run flip rate (self-consistency across a model's own 3 runs)",
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_models": len(models),
            "models": models,
            "summary": {
                "detection_flip_pct_range": [min(dets), max(dets)] if dets else None,
                "value_flip_pct_range": [min(vals), max(vals)] if vals else None,
            },
            "notes": (
                "value_eligible is the per-model denominator: cases where the model caught a "
                "real dispute on at least two non-error runs. It varies by model because run "
                "errors reduce the number of scoreable runs. Compare value_flip against each "
                "model's own value_eligible, not against a fixed 162."
            ),
        }
        out.write_text(json.dumps(artifact, indent=2) + "\n")
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()
