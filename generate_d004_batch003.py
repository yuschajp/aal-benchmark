#!/usr/bin/env python3
"""AAL-D-004 batch 3/5 generator. Deterministic slice of d004_common.build_master_plan();
every case passes qa_assert_case (QA gate 1) before writing."""
import json, os
import d004_common as c

BATCH_NUM = 3
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "datasets", "AAL-D-004", f"AAL-D-004-batch-{BATCH_NUM:03d}.json")

def main():
    plan = c.plan_for_batch(BATCH_NUM)
    assert len(plan) == 50
    cases = []
    for p in plan:
        case = c.render_case(p)
        c.qa_assert_case(case)
        cases.append(case)
    from collections import Counter
    print(f"Batch {BATCH_NUM}: {cases[0]['case_id']} .. {cases[-1]['case_id']}")
    print("  clean:", sum(1 for p in plan if p["is_clean"]),
          " cats:", dict(Counter(p["category"] for p in plan if p["category"])))
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(cases, f, indent=2, sort_keys=False)
    print(f"Written: {OUT_PATH}")

if __name__ == "__main__":
    main()
