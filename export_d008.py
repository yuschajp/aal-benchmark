"""
AAL-D-008 — export generated cases to a dataset file.

Every other AAL dataset lives as JSON on disk and is loaded by the eval
drivers. D-008's cases come out of a generator instead, so they have to be
frozen to a file before a run: a benchmark whose cases are regenerated at
eval time is not reproducible, and the published figures could not be
checked against the cases that produced them.

The exported file carries both the case input (what the model sees) and the
ground truth (what the scorer uses). build_prompt in d008_eval_common.py
selects only the input side.

Usage:
    python export_d008.py                          # 250 cases, seed 8
    python export_d008.py --n-cases 250 --seed 8
    python export_d008.py --out datasets/AAL-D-008-v0.1.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_d008 import generate_batch

DEFAULT_OUT = "datasets/AAL-D-008-v0.1.json"


def main():
    ap = argparse.ArgumentParser(description="Freeze D-008 cases to a dataset file.")
    ap.add_argument("--n-cases", type=int, default=250)
    ap.add_argument("--seed", type=int, default=8)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    batch = generate_batch(n_cases=args.n_cases, seed=args.seed)

    cases = []
    for g in batch:
        cases.append({
            "case_id": g.case_input.case_id,
            # mode="json" so dates and enums serialize to strings
            "case_input": g.case_input.model_dump(mode="json"),
            "ground_truth": g.ground_truth.model_dump(mode="json"),
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "dataset": "AAL-D-008",
        "version": "v0.1",
        "n_cases": len(cases),
        "generator_seed": args.seed,
        "cases": cases,
    }, indent=2) + "\n")

    print(f"Wrote {len(cases)} cases to {out}")
    print()
    print("NOTE: is_trap is an internal generator flag and is present in case_input.")
    print("build_prompt must not include it. See d008_eval_common.py.")


if __name__ == "__main__":
    main()
