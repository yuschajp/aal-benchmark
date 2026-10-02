"""
Fail if any model-facing field in a dataset tells the model it is being tested.

Scans every case in each dataset file and flags any string that names AAL, a
dataset ID (D-001 and so on), or uses the words "dataset" or "benchmark".
Fields that never reach the model are skipped: case IDs, ground truth,
scoring criteria and metadata.

Usage:
    python quality_checks/check_prompt_leakage.py                 # all datasets
    python quality_checks/check_prompt_leakage.py datasets/X.json # specific files

Exit code 0 = clean, 1 = leak found.
"""
from __future__ import annotations

import glob
import json
import re
import sys

PATTERN = re.compile(r"\bAAL\b|\bD-0\d\d\b|\bdataset\b|\bbenchmark\b", re.IGNORECASE)

# Keys whose contents are not sent to the model.
SKIP_KEYS = {"case_id", "id", "dataset", "ground_truth", "meta", "scoring_criteria",
             "expected", "expected_output", "answer", "notes", "gt", "provenance"}

# Files that are not case datasets.
SKIP_FILES = ("eval_results", "distribution_manifest", "AAL-RS-")

# Known, publicly disclosed exceptions (README): AAL-D-008 trade and confirmation
# IDs carry the benchmark name. Counted and reported, but they do not fail the check.
KNOWN = {("AAL-D-008", "trade_id"), ("AAL-D-008", "confirmation_id")}

# Superseded files kept unchanged so published runs stay reproducible.
# AAL-D-002 v1.0 case 250 named the dataset; fixed in v1.0.1.
SUPERSEDED = {"datasets/AAL-D-002/AAL-D-002-v1.0.json"}


def cases_in(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("cases", "data", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def walk(obj, path, hits):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in SKIP_KEYS:
                continue
            walk(v, f"{path}.{k}", hits)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{path}[{i}]", hits)
    elif isinstance(obj, str) and PATTERN.search(obj):
        hits.append((path, obj))


def main():
    files = sys.argv[1:] or sorted(glob.glob("datasets/**/*.json", recursive=True))
    files = [f for f in files if not any(s in f for s in SKIP_FILES)]
    if not sys.argv[1:]:
        for f in sorted(SUPERSEDED & set(files)):
            print(f"skipped (superseded, kept for reproducibility): {f}")
        files = [f for f in files if f not in SUPERSEDED]
    total = known = 0
    for f in files:
        with open(f, encoding="utf-8") as fh:
            cases = cases_in(json.load(fh))
        for c in cases:
            hits = []
            walk(c, "", hits)
            for path, text in hits:
                field = path.rsplit(".", 1)[-1]
                if any(ds in f and field == fld for ds, fld in KNOWN):
                    known += 1
                    continue
                total += 1
                print(f"LEAK {f} {c.get('case_id', '?')} {path}: {text[:120]}")
    print(f"{len(files)} files scanned, {total} leak(s) found, "
          f"{known} known and disclosed (AAL-D-008 IDs)")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
