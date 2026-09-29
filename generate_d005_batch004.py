#!/usr/bin/env python3
"""AAL-D-005 batch 4/5 generator. Deterministic slice of d005_common.build_master_plan();
every case passes qa_assert_case (QA gate 1) before writing."""
import json, os
from collections import Counter
import d005_common as c

BATCH_NUM = 4
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "datasets", "AAL-D-005", f"AAL-D-005-batch-{BATCH_NUM:03d}.json")


def main():
    plan = c.plan_for_batch(BATCH_NUM)
    assert len(plan) == 50
    cases = []
    for p in plan:
        case = c.render_case(p)
        c.qa_assert_case(case)
        cases.append(case)
    print(f"Batch {BATCH_NUM}: {cases[0]['case_id']} .. {cases[-1]['case_id']}")
    print("  clean:", sum(1 for p in plan if p["is_clean"]),
          " cats:", dict(Counter(p["category"] for p in plan if p["category"])))
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(cases, f, indent=2, sort_keys=False)
    print(f"Written: {OUT_PATH}")


if __name__ == "__main__":
    main()
