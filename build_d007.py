"""
AAL-D-007 one-shot build: generate all 250 cases, run generator QA, and write
the dataset to datasets/AAL-D-007-v1.2.json with a manifest header.

v1.2: IM-DIS-CALCDATE is answerable. Through v1.1 the category scaled every
sensitivity but emitted no date evidence -- one shared calculation_date, no
per-side disclosure -- so "the perturbed side's calc is stale" could not be
recovered from anything the model saw. All nine models in the published roster
scored 0.0% on it, correctly. See d007_common.py's _inject_dispute.

v1.1: fixes the design flaw where correct_im_amount was always the firm's
total, by construction, in every case. See d007_common.py's module docstring.

Usage:  python build_d007.py
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from d007_common import (CATEGORIES, TRAP_TYPES, build_master_plan, qa_assert_case,
                         render_case)


def main() -> None:
    plan = build_master_plan()
    cases = [render_case(i + 1, plan[i]) for i in range(len(plan))]
    for c in cases:
        qa_assert_case(c)

    n_dispute = sum(1 for c in cases if c["ground_truth"]["dispute_exists"])
    n_trap = sum(1 for c in cases if c["meta"]["kind"] == "trap")
    cat_dist = Counter(c["ground_truth"]["primary_dispute_category"] for c in cases
                       if c["ground_truth"]["dispute_exists"])
    trap_dist = Counter(c["meta"]["intended"] for c in cases if c["meta"]["kind"] == "trap")

    n_firm_perturbed = sum(1 for c in cases if c["meta"]["perturbed_side"] == "firm")

    payload = {
        "dataset": "AAL-D-007",
        "title": "SIMM/UMR Initial Margin Dispute Detection",
        "version": "v1.2",
        "n_cases": len(cases),
        "n_disputes": n_dispute,
        "n_traps": n_trap,
        "categories": CATEGORIES,
        "trap_types": TRAP_TYPES,
        "architecture": "LLM classifies dispute existence + attributes root cause + judges which side's total is correct; all SIMM-style margin aggregation generator-side.",
        "methodology_note": ("SIMM-style margin methodology: real ISDA SIMM aggregation "
                             "structure (risk-weighted sensitivities, correlation-aggregated "
                             "within and across buckets, summed across risk classes), with "
                             "AAL's own illustrative risk-weight and correlation constants -- "
                             "NOT ISDA's licensed SIMM calibration. See d007_common.py docstring."),
        "changelog": ("v1.2: IM-DIS-CALCDATE is now answerable. Each party's ACTUAL calculation "
                     "date is disclosed per side in methodology_metadata, alongside its "
                     "risk_weight_set, and netting_set carries the agreed reconciliation_date "
                     "the two are being compared against. Through v1.1 the category applied a "
                     "uniform sensitivity shift and emitted no date evidence at all -- a single "
                     "shared calculation_date, identical methodology metadata -- so the stated "
                     "root cause was not recoverable from the case. All nine models in the "
                     "published roster scored exactly 0.0% on this category across three runs, "
                     "and were right to: the evidence they were shown supports IM-DIS-SENS. "
                     "TRAP-CALCDATE-INBAND also now carries differing dates (one day, staying "
                     "within tolerance) so the presence of the field cannot substitute for "
                     "judging materiality. No ground truth, margin figure or sensitivity value "
                     "changed in this revision -- only the disclosed evidence. | "
                     "v1.1: the injected error is now assigned to either the firm's or the "
                     "counterparty's reported figures, balanced exactly 125/125 across the "
                     "corpus, instead of always the counterparty. v1.0 always made "
                     "correct_im_amount the firm's total by construction, which combined with "
                     "a prompt that named the firm's total directly, made value_accuracy "
                     "measure whether a model can copy a labeled field rather than whether it "
                     "can judge which side is right."),
        "cases": cases,
    }
    body = json.dumps(payload, indent=2)
    out = Path("datasets/AAL-D-007-v1.2.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(body)

    sha = hashlib.sha256(body.encode()).hexdigest()[:16]
    print(f"Wrote {out}  ({len(cases)} cases, sha256:{sha})")
    print(f"  disputes: {n_dispute}  traps: {n_trap}")
    print(f"  perturbed side: firm={n_firm_perturbed}  counterparty={len(cases) - n_firm_perturbed}")
    print(f"  category distribution: {dict(cat_dist)}")
    print(f"  trap distribution:     {dict(trap_dist)}")


if __name__ == "__main__":
    main()
