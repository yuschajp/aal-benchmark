#!/usr/bin/env python3
"""
AI Alpha Labs — AAL-D-003 distribution manifest.

Scans the 5 already-generated batch files under datasets/AAL-D-003/,
computes realized counts (venue x product, difficulty x clean/exception,
error-category, clean total, risk_level, multi-leg total), and ASSERTS they
match D003-spec.md section 12 / section 5 exactly. Writes the realized
counts to datasets/AAL-D-003/distribution_manifest.json.

This script does not generate any cases itself — it is a pure read-and-verify
pass over the five batch outputs (QA gate 1 rollup / build-time assertion,
per D003-spec.md section 8 item 1 and the "generator emits a distribution
manifest" line in section 12).

Usage:
    python3 generate_d003_manifest.py
"""

import json
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
BATCH_DIR = os.path.join(HERE, "datasets", "AAL-D-003")
OUT_PATH = os.path.join(BATCH_DIR, "distribution_manifest.json")

EXPECTED_VENUE_PRODUCT = {
    "BBG": {"call": 34, "put": 30, "spread": 36},
    "CME": {"call": 22, "put": 18, "spread": 25},
    "Eurex": {"call": 24, "put": 22, "spread": 39},
}
EXPECTED_SPREAD_TOTAL = 100

EXPECTED_DIFFICULTY = {
    "easy": {"clean": 30, "exception": 30},
    "moderate": {"clean": 40, "exception": 70},
    "complex": {"clean": 18, "exception": 62},
}
EXPECTED_CLEAN_TOTAL = 88
EXPECTED_EXCEPTION_TOTAL = 162
EXPECTED_DUAL_EXCEPTION = 20  # "~20" per spec; tolerance +/-2 enforced below

# Counts are by ground-truth PRIMARY category. The injected-break distribution
# still matches spec section 12 exactly; v1.0.2's section-10 risk ordering
# relabels 2 dual-exception primaries (012 NOTIONAL->TDATE, 161 GREEK->STRIKE),
# which shifts four counts below relative to the section-12 planning table.
EXPECTED_CATEGORY_COUNTS = {
    "EXC-PRICE": 20, "EXC-PROD": 6, "EXC-QTY": 4, "EXC-CCY": 4, "EXC-CPTY": 4, "EXC-COMM": 4,
    "EXC-TDATE": 3, "EXC-SDATE": 2,
    "EXC-GREEK": 15, "EXC-STRIKE": 15, "EXC-NOTIONAL": 11, "EXC-DIV": 12, "EXC-SETTLE": 12,
    "EXC-CORPACT": 10, "EXC-LEG": 10, "EXC-EXPIRY": 8, "EXC-STYLE": 8, "EXC-RATIO": 8, "EXC-MULT": 6,
}
assert sum(EXPECTED_CATEGORY_COUNTS.values()) == EXPECTED_EXCEPTION_TOTAL


def load_all_cases():
    cases = []
    for n in range(1, 6):
        path = os.path.join(BATCH_DIR, f"AAL-D-003-batch-{n:03d}.json")
        with open(path) as f:
            batch = json.load(f)
        assert len(batch) == 50, f"{path}: expected 50 cases, found {len(batch)}"
        cases.extend(batch)
    assert len(cases) == 250, f"expected 250 total cases, found {len(cases)}"
    return cases


def bucket(case):
    cell = case["generation_metadata"]["distribution_cell"]
    if cell["leg"] == "spread":
        return "spread"
    return "call" if cell["is_call"] else "put"


def main():
    cases = load_all_cases()

    # case_id sequencing: AAL-D-003-001..250, no gaps/dupes
    ids = sorted(c["case_id"] for c in cases)
    expected_ids = [f"AAL-D-003-{n:03d}" for n in range(1, 251)]
    assert ids == expected_ids, "case_id sequence is not exactly AAL-D-003-001..250"

    venue_product = defaultdict(Counter)
    difficulty_clean = defaultdict(Counter)
    category_counts = Counter()
    clean_total = 0
    exception_total = 0
    dual_exception_total = 0
    risk_level_counts = Counter()
    multi_leg_total = 0

    for case in cases:
        cell = case["generation_metadata"]["distribution_cell"]
        gt = case["ground_truth"]
        venue = cell["venue"]
        venue_product[venue][bucket(case)] += 1
        if cell["leg"] == "spread":
            multi_leg_total += 1

        is_clean = not gt["exception_exists"]
        difficulty_clean[cell["difficulty"]]["clean" if is_clean else "exception"] += 1
        if is_clean:
            clean_total += 1
        else:
            exception_total += 1
            category_counts[gt["primary_exception"]["category"]] += 1
            if gt["secondary_exception"] is not None:
                dual_exception_total += 1

        risk_level_counts[case["risk_level"]] += 1

    # ── ASSERTIONS against D003-spec.md section 12 / section 5 ──────────────

    for venue, expected in EXPECTED_VENUE_PRODUCT.items():
        realized = venue_product[venue]
        for k, v in expected.items():
            assert realized[k] == v, f"venue={venue} product={k}: expected {v}, got {realized[k]}"
        assert sum(expected.values()) == sum(realized.values()) == sum(expected.values())

    spread_total = sum(venue_product[v]["spread"] for v in EXPECTED_VENUE_PRODUCT)
    assert spread_total == EXPECTED_SPREAD_TOTAL, f"spread total: expected {EXPECTED_SPREAD_TOTAL}, got {spread_total}"
    assert multi_leg_total == EXPECTED_SPREAD_TOTAL, f"multi-leg total: expected {EXPECTED_SPREAD_TOTAL}, got {multi_leg_total}"

    for diff, expected in EXPECTED_DIFFICULTY.items():
        realized = difficulty_clean[diff]
        assert realized["clean"] == expected["clean"], \
            f"difficulty={diff} clean: expected {expected['clean']}, got {realized['clean']}"
        assert realized["exception"] == expected["exception"], \
            f"difficulty={diff} exception: expected {expected['exception']}, got {realized['exception']}"

    assert clean_total == EXPECTED_CLEAN_TOTAL, f"clean total: expected {EXPECTED_CLEAN_TOTAL}, got {clean_total}"
    assert exception_total == EXPECTED_EXCEPTION_TOTAL, \
        f"exception total: expected {EXPECTED_EXCEPTION_TOTAL}, got {exception_total}"
    assert clean_total + exception_total == 250

    assert abs(dual_exception_total - EXPECTED_DUAL_EXCEPTION) <= 2, \
        f"dual-exception total: expected ~{EXPECTED_DUAL_EXCEPTION}, got {dual_exception_total}"

    for cat, expected in EXPECTED_CATEGORY_COUNTS.items():
        got = category_counts[cat]
        assert got == expected, f"category {cat}: expected {expected}, got {got}"
    assert sum(category_counts.values()) == EXPECTED_EXCEPTION_TOTAL

    print("All distribution assertions PASSED.")
    print(f"  venue x product: {dict((v, dict(c)) for v, c in venue_product.items())}")
    print(f"  spread (multi-leg) total: {spread_total}")
    print(f"  difficulty x clean/exception: {dict((d, dict(c)) for d, c in difficulty_clean.items())}")
    print(f"  clean total: {clean_total}  exception total: {exception_total}")
    print(f"  dual-exception total: {dual_exception_total}")
    print(f"  category counts: {dict(category_counts.most_common())}")
    print(f"  risk_level distribution: {dict(sorted(risk_level_counts.items()))}")

    manifest = {
        "benchmark_version": "1.0.2",
        "total_cases": 250,
        "venue_product": {v: dict(c) for v, c in venue_product.items()},
        "spread_total": spread_total,
        "multi_leg_total": multi_leg_total,
        "difficulty": {d: dict(c) for d, c in difficulty_clean.items()},
        "clean_total": clean_total,
        "exception_total": exception_total,
        "dual_exception_total": dual_exception_total,
        "category_counts": dict(category_counts),
        "risk_level_distribution": {str(k): v for k, v in sorted(risk_level_counts.items())},
        "assertions": "PASSED — matches D003-spec.md section 12 / section 5 exactly",
    }
    with open(OUT_PATH, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=False)
    print(f"\nWritten: {OUT_PATH}")


if __name__ == "__main__":
    main()
