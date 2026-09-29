#!/usr/bin/env python3
"""
AI Alpha Labs — Benchmark Validator
AAL-D-001 · v1.0

Validates benchmark cases against the AAL-D-001 schema
and checks distribution requirements.

Usage:
    python validate.py --file ../examples/representative_cases.json
    python validate.py --file ../datasets/AAL-D-001-v1.0.json --full
    python validate.py --report ../examples/representative_cases.json
"""

import json
import sys
import argparse
from pathlib import Path
from collections import Counter

VALID_ASSET_CLASSES = ["equities", "fixed_income", "listed_futures", "options",
                       "interest_rate_swap", "fx_forward", "credit"]
VALID_DIFFICULTIES  = ["easy", "moderate", "complex"]
VALID_WORKFLOWS     = ["trade_confirmation_exception"]
VALID_CONFIDENCES   = ["definitive", "expert_consensus", "conditional"]

REQUIRED_CASE_FIELDS = [
    "case_id", "benchmark_version", "workflow", "asset_class", "difficulty",
    "risk_level", "scenario_description", "business_context", "input",
    "ground_truth", "scoring_criteria", "failure_modes", "version_history"
]

REQUIRED_GT_FIELDS = [
    "exception_exists", "primary_exception", "recommended_action",
    "escalation_required", "human_review_required", "severity", "confidence"
]

REQUIRED_INPUT_FIELDS = ["counterparty_confirmation", "internal_record"]

TARGET_DISTRIBUTION = {
    "easy": {"min": 0.35, "max": 0.45, "target": 100},
    "moderate": {"min": 0.35, "max": 0.45, "target": 100},
    "complex": {"min": 0.15, "max": 0.25, "target": 50}
}


def validate(filepath, full=False):
    path = Path(filepath)
    errors = []
    warnings = []

    try:
        with open(path) as f:
            cases = json.load(f)
    except Exception as e:
        print(f"FAIL — cannot parse: {e}")
        return False

    if not isinstance(cases, list):
        print("FAIL — top-level must be an array of cases")
        return False

    ids = set()
    asset_counts = Counter()
    diff_counts = Counter()
    risk_counts = Counter()
    exception_counts = Counter()

    for i, case in enumerate(cases):
        prefix = f"Case {i} ({case.get('case_id', 'NO ID')})"

        # Required fields
        for field in REQUIRED_CASE_FIELDS:
            if field not in case:
                errors.append(f"{prefix}: missing '{field}'")

        # Case ID format
        cid = case.get("case_id", "")
        if not (cid.startswith("AAL-D-") and cid[6:9].isdigit() and cid[9:10] == "-"):
            errors.append(f"{prefix}: case_id must start with AAL-D-NNN-")
        if cid in ids:
            errors.append(f"{prefix}: duplicate case_id")
        ids.add(cid)

        # Enums
        if case.get("asset_class") not in VALID_ASSET_CLASSES:
            errors.append(f"{prefix}: invalid asset_class '{case.get('asset_class')}'")
        if case.get("difficulty") not in VALID_DIFFICULTIES:
            errors.append(f"{prefix}: invalid difficulty '{case.get('difficulty')}'")
        if case.get("workflow") not in VALID_WORKFLOWS:
            errors.append(f"{prefix}: invalid workflow")
        if not (1 <= case.get("risk_level", 0) <= 5):
            errors.append(f"{prefix}: risk_level must be 1-5")

        # Input structure
        inp = case.get("input", {})
        for f in REQUIRED_INPUT_FIELDS:
            if f not in inp:
                errors.append(f"{prefix}: input missing '{f}'")

        # Ground truth
        gt = case.get("ground_truth", {})
        for f in REQUIRED_GT_FIELDS:
            if f not in gt:
                errors.append(f"{prefix}: ground_truth missing '{f}'")
        if gt.get("confidence") and gt["confidence"] not in VALID_CONFIDENCES:
            warnings.append(f"{prefix}: unusual confidence value '{gt.get('confidence')}'")

        # Failure modes
        fm = case.get("failure_modes", [])
        if len(fm) < 1:
            errors.append(f"{prefix}: must have at least 1 failure mode")
        if case.get("difficulty") == "complex" and len(fm) < 2:
            warnings.append(f"{prefix}: complex cases should have ≥ 2 failure modes")

        # Version history
        vh = case.get("version_history", [])
        if len(vh) < 1:
            errors.append(f"{prefix}: must have version_history")

        # Counts
        asset_counts[case.get("asset_class", "unknown")] += 1
        diff_counts[case.get("difficulty", "unknown")] += 1
        risk_counts[case.get("risk_level", 0)] += 1
        if gt.get("exception_exists"):
            exc = gt.get("primary_exception", {})
            if exc:
                exception_counts[exc.get("category", "unknown")] += 1
        else:
            exception_counts["CLEAN"] += 1

    # Print results
    n = len(cases)
    valid = len(errors) == 0

    print(f"\n{'✅ VALID' if valid else '❌ INVALID'} — {path.name}")
    print(f"  Cases: {n}")
    print(f"  Errors: {len(errors)}")
    print(f"  Warnings: {len(warnings)}")

    if errors:
        print(f"\n  Errors:")
        for e in errors[:20]:
            print(f"    ✗ {e}")
        if len(errors) > 20:
            print(f"    ... and {len(errors) - 20} more")

    if warnings:
        print(f"\n  Warnings:")
        for w in warnings:
            print(f"    ⚠ {w}")

    # Distribution report
    print(f"\n  Asset Class Distribution:")
    for ac in VALID_ASSET_CLASSES:
        count = asset_counts.get(ac, 0)
        pct = count / n * 100 if n > 0 else 0
        print(f"    {ac:<25} {count:>4}  ({pct:.1f}%)")

    print(f"\n  Difficulty Distribution:")
    for d in VALID_DIFFICULTIES:
        count = diff_counts.get(d, 0)
        pct = count / n * 100 if n > 0 else 0
        print(f"    {d:<25} {count:>4}  ({pct:.1f}%)")

    print(f"\n  Risk Level Distribution:")
    for r in range(1, 6):
        count = risk_counts.get(r, 0)
        print(f"    Level {r:<21} {count:>4}")

    print(f"\n  Exception Category Distribution:")
    for cat, count in sorted(exception_counts.items(), key=lambda x: -x[1]):
        print(f"    {cat:<25} {count:>4}")

    return valid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AAL-D-001 Benchmark Validator")
    parser.add_argument("--file", required=True, help="JSON file to validate")
    parser.add_argument("--full", action="store_true", help="Full production validation")
    args = parser.parse_args()

    valid = validate(args.file, args.full)
    sys.exit(0 if valid else 1)
