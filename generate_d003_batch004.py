#!/usr/bin/env python3
"""
AI Alpha Labs — AAL-D-003 batch 4/5 generator.
Theme (spec section 13): multi-leg spreads, all venues.

Deterministic: pulls the fixed 50-case slice for batch 4 from
d003_common.build_master_plan() (module-level seeded, no wall-clock,
no unseeded randomness) and renders each case via d003_common.render_case().
Every case is validated with d003_common.qa_assert_case() (QA gate 1)
before being written.

Usage:
    python3 generate_d003_batch004.py
"""

import json
import os

import d003_common as c

BATCH_NUM = 4
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "datasets", "AAL-D-003", f"AAL-D-003-batch-{BATCH_NUM:03d}.json")


def main():
    plan_slice = c.plan_for_batch(BATCH_NUM)
    assert len(plan_slice) == 50, f"batch {BATCH_NUM} plan slice must be 50 cases, got {len(plan_slice)}"

    cases = []
    for p in plan_slice:
        case = c.render_case(p)
        c.qa_assert_case(case)
        cases.append(case)

    seqs = [p["seq"] for p in plan_slice]
    print(f"Batch {BATCH_NUM}: case_id range AAL-D-003-{min(seqs):03d} .. AAL-D-003-{max(seqs):03d} "
          f"({len(cases)} cases)")
    from collections import Counter
    print("  venue:", dict(Counter(p["venue"] for p in plan_slice)))
    print("  leg:", dict(Counter(p["leg"] for p in plan_slice)))
    print("  clean:", sum(1 for p in plan_slice if p["is_clean"]))
    print("  categories:", dict(Counter(p["category"] for p in plan_slice if p["category"])))

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(cases, f, indent=2, sort_keys=False)
    print(f"Written: {OUT_PATH}")


if __name__ == "__main__":
    main()
