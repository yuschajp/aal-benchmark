#!/usr/bin/env python3
"""AAL-D-004 distribution manifest — reads built batches, asserts realized counts
match every D004-spec.md table (category §5, difficulty×clean + traps + severity §10,
venue quotas per build brief §4), writes distribution_manifest.json. Exits nonzero
on any mismatch."""
import glob
import json
import os
import sys
from collections import Counter

import d004_common as c

DS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets", "AAL-D-004")


def main():
    files = sorted(glob.glob(os.path.join(DS_DIR, "AAL-D-004-batch-*.json")))
    if len(files) != 5:
        sys.exit(f"expected 5 batch files, found {len(files)}")
    cases = []
    for f in files:
        cases.extend(json.load(open(f)))
    assert len(cases) == 250, f"expected 250 cases, got {len(cases)}"
    ids = [x["case_id"] for x in cases]
    assert ids == [f"AAL-D-004-{i:03d}" for i in range(1, 251)], "case_ids not sequential"

    gt = lambda x: x["ground_truth"]
    realized = {
        "n_cases": len(cases),
        "categories": dict(Counter(gt(x)["primary_fail"]["category"]
                                   for x in cases if gt(x)["fail_exists"])),
        "difficulty_x_clean": dict(Counter(
            f"{x['difficulty']}|{'clean' if not gt(x)['fail_exists'] else 'fail'}"
            for x in cases)),
        "severity": dict(Counter(gt(x)["severity"] for x in cases)),
        "traps": dict(Counter(x["generation_metadata"]["trap_type"]
                              for x in cases if not gt(x)["fail_exists"])),
        "venues": dict(Counter(x["venue_type"] for x in cases)),
        "asset_classes": dict(Counter(x["asset_class"] for x in cases)),
        "fail_directions": dict(Counter(x["fail_direction"] for x in cases)),
        "settlement_types": dict(Counter(x["settlement_type"] for x in cases)),
        "secondary_fails": sum(1 for x in cases if gt(x).get("secondary_fail")),
    }

    errors = []
    if realized["categories"] != c.EXPECTED_CATEGORY_COUNTS:
        errors.append(f"categories: {realized['categories']} != {c.EXPECTED_CATEGORY_COUNTS}")
    exp_dc = {f"{d}|{'clean' if cl else 'fail'}": n
              for (d, cl), n in c.EXPECTED_DIFF_CLEAN.items()}
    if realized["difficulty_x_clean"] != exp_dc:
        errors.append(f"difficulty_x_clean: {realized['difficulty_x_clean']} != {exp_dc}")
    if realized["severity"] != c.EXPECTED_SEVERITY:
        errors.append(f"severity: {realized['severity']} != {c.EXPECTED_SEVERITY}")
    if realized["traps"] != c.EXPECTED_TRAPS:
        errors.append(f"traps: {realized['traps']} != {c.EXPECTED_TRAPS}")
    if realized["venues"] != c.EXPECTED_VENUES:
        errors.append(f"venues: {realized['venues']} != {c.EXPECTED_VENUES}")
    if any(v < 15 for v in realized["asset_classes"].values()):
        errors.append(f"asset class below 15: {realized['asset_classes']}")

    if errors:
        for e in errors:
            print("MISMATCH:", e)
        sys.exit(1)

    out = os.path.join(DS_DIR, "distribution_manifest.json")
    with open(out, "w") as f:
        json.dump({"benchmark": "AAL-D-004", "version": c.BENCHMARK_VERSION,
                   "generator_version": c.GENERATOR_VERSION, "realized": realized}, f, indent=2)
    print(f"All distribution tables match spec. Written: {out}")


if __name__ == "__main__":
    main()
