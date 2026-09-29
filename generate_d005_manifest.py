#!/usr/bin/env python3
"""AAL-D-005 distribution manifest + merge. Reads the 5 built batches, asserts
realized counts match every D005-spec table (category §5, difficulty×clean +
traps + severity §10), writes distribution_manifest.json AND the merged
AAL-D-005-v1.0.json that the eval driver loads. Exits nonzero on any mismatch."""
import glob
import json
import os
import sys
from collections import Counter

import d005_common as c

DS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets", "AAL-D-005")


def main():
    files = sorted(glob.glob(os.path.join(DS_DIR, "AAL-D-005-batch-*.json")))
    if len(files) != 5:
        sys.exit(f"expected 5 batch files, found {len(files)}")
    cases = []
    for f in files:
        cases.extend(json.load(open(f)))
    assert len(cases) == 250, f"expected 250 cases, got {len(cases)}"
    ids = [x["case_id"] for x in cases]
    assert ids == [f"AAL-D-005-{i:03d}" for i in range(1, 251)], "case_ids not sequential"

    gt = lambda x: x["ground_truth"]
    realized = {
        "n_cases": len(cases),
        "categories": dict(Counter(gt(x)["primary_break"]["category"]
                                   for x in cases if gt(x)["break_exists"])),
        "difficulty_x_clean": dict(Counter(
            f"{x['difficulty']}|{'clean' if not gt(x)['break_exists'] else 'break'}"
            for x in cases)),
        "severity": dict(Counter(gt(x)["severity"] for x in cases)),
        "traps": dict(Counter(x["generation_metadata"]["trap_type"]
                              for x in cases if not gt(x)["break_exists"])),
        "product_types": dict(Counter(x["product_type"] for x in cases)),
        "break_sources": dict(Counter(x["break_source"] for x in cases)),
        "pnl_directions": dict(Counter(x["pnl_direction"] for x in cases)),
        "explain_null": sum(1 for x in cases if x["input"]["pnl_explain"] is None),
        "secondary_breaks": sum(1 for x in cases if gt(x).get("secondary_break")),
    }

    errors = []
    if realized["categories"] != c.EXPECTED_CATEGORY_COUNTS:
        errors.append(f"categories: {realized['categories']} != {c.EXPECTED_CATEGORY_COUNTS}")
    exp_dc = {f"{d}|{'clean' if cl else 'break'}": n
              for (d, cl), n in c.EXPECTED_DIFF_CLEAN.items()}
    if realized["difficulty_x_clean"] != exp_dc:
        errors.append(f"difficulty_x_clean: {realized['difficulty_x_clean']} != {exp_dc}")
    if realized["severity"] != c.EXPECTED_SEVERITY:
        errors.append(f"severity: {realized['severity']} != {c.EXPECTED_SEVERITY}")
    if realized["traps"] != c.EXPECTED_TRAPS:
        errors.append(f"traps: {realized['traps']} != {c.EXPECTED_TRAPS}")
    # every pnl_direction bucket populated (scorer breakdown needs them)
    if set(realized["pnl_directions"]) != {"gain_overstated", "gain_understated",
                                           "loss_overstated", "loss_understated"}:
        errors.append(f"pnl_directions incomplete: {realized['pnl_directions']}")
    if set(realized["break_sources"]) != {"market_data", "trade_lifecycle",
                                          "static_reference", "risk_model"}:
        errors.append(f"break_sources incomplete: {realized['break_sources']}")

    if errors:
        for e in errors:
            print("MISMATCH:", e)
        sys.exit(1)

    with open(os.path.join(DS_DIR, "distribution_manifest.json"), "w") as f:
        json.dump({"benchmark": "AAL-D-005", "version": c.BENCHMARK_VERSION,
                   "generator_version": c.GENERATOR_VERSION, "realized": realized}, f, indent=2)
    with open(os.path.join(DS_DIR, "AAL-D-005-v1.0.json"), "w") as f:
        json.dump(cases, f, indent=2, sort_keys=False)
    print("All distribution tables match spec.")
    print(f"  Written: {os.path.join(DS_DIR, 'distribution_manifest.json')}")
    print(f"  Written: {os.path.join(DS_DIR, 'AAL-D-005-v1.0.json')} (250 cases)")
    print("  product_types:", realized["product_types"])
    print("  break_sources:", realized["break_sources"])
    print("  pnl_directions:", realized["pnl_directions"])
    print("  explain_null:", realized["explain_null"], " secondary_breaks:", realized["secondary_breaks"])


if __name__ == "__main__":
    main()
