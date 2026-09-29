"""
AAL-D-006 one-shot build: generate all 250 cases, run generator QA, and write
the dataset to datasets/AAL-D-006-v1.0.json with a manifest header.

Usage:  python build_d006.py
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from d006_common import (CATEGORIES, TRAP_TYPES, build_master_plan, qa_assert_case,
                         render_case)


def main() -> None:
    plan = build_master_plan()
    cases = [render_case(i + 1, plan[i]) for i in range(len(plan))]
    for c in cases:
        qa_assert_case(c)

    n_inelig = sum(1 for c in cases if not c["ground_truth"]["eligible"])
    n_trap = sum(1 for c in cases if c["meta"]["kind"] == "trap")
    cat_dist = Counter(c["ground_truth"]["reason_category"] for c in cases
                       if not c["ground_truth"]["eligible"])
    trap_dist = Counter(c["meta"]["intended"] for c in cases if c["meta"]["kind"] == "trap")

    payload = {
        "dataset": "AAL-D-006",
        "title": "Collateral Eligibility & Substitution",
        "version": "v1.0",
        "n_cases": len(cases),
        "n_ineligible": n_inelig,
        "n_traps": n_trap,
        "categories": CATEGORIES,
        "trap_types": TRAP_TYPES,
        "architecture": "LLM classifies eligibility + recommends substitution; all arithmetic generator-side.",
        "cases": cases,
    }
    body = json.dumps(payload, indent=2)
    out = Path("datasets/AAL-D-006-v1.0.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(body)

    sha = hashlib.sha256(body.encode()).hexdigest()[:16]
    print(f"Wrote {out}  ({len(cases)} cases, sha256:{sha})")
    print(f"  ineligible: {n_inelig}  traps: {n_trap}")
    print(f"  category distribution: {dict(cat_dist)}")
    print(f"  trap distribution:     {dict(trap_dist)}")


if __name__ == "__main__":
    main()
