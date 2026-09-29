"""
AAL-D-007 eval-common: prompt construction + case loading, shared by every
model driver. The model is an IM ops analyst: it decides whether a SIMM/UMR
Initial Margin dispute exists between the firm's and counterparty's reported
numbers, and if so, attributes root cause AND determines which side's total is
actually correct -- the erring side varies case-by-case (balanced 125/125
firm vs. counterparty across the corpus as of v1.1; see d007_common.py's
module docstring for why v1.0's always-the-counterparty design was a flaw).
It must NOT recompute the margin aggregation itself -- every figure (bucket
margins, risk-class margins, total IM for both sides, the dollar and
percentage difference) is printed in the margin breakdown. The model reads,
judges which side to trust, and reports.
"""
from __future__ import annotations

import json
from pathlib import Path

DATASET_DEFAULT = "datasets/AAL-D-007-v1.2.json"
PROMPT_VERSION = "d007-prompt-v1.1"

CATEGORY_MENU = [
    "IM-DIS-SENS", "IM-DIS-TRADEPOP", "IM-DIS-BUCKET", "IM-DIS-FX",
    "IM-DIS-CALCDATE", "IM-DIS-CRIF", "IM-DIS-METHODOLOGY",
    "IM-DIS-NETTING", "IM-DIS-CONCENTRATION",
]

SYSTEM_INSTRUCTION = (
    "You are an Initial Margin operations analyst reconciling two SIMM/UMR Initial Margin "
    "calculations for the same netting set -- one produced by the firm, one reported by the "
    "counterparty. Do NOT assume either side's number is correct by default: in this dataset "
    "the error, when one exists, is equally likely to be on the firm's side or the "
    "counterparty's side, so you must judge which one to trust in every case. Decide whether "
    "a genuine DISPUTE exists (the difference exceeds the printed tolerance) or whether it is "
    "explainable and within tolerance; if disputed, determine which side's total is actually "
    "right by comparing the two sensitivity sets and each party's disclosed methodology "
    "metadata. Every numeric figure you need (each side's bucket margins, risk-class margins, "
    "total IM, and the dollar/percentage difference) is already computed and printed in the "
    "margin breakdown -- DO NOT perform any aggregation yourself; read the printed figures and "
    "judge which side's printed total is trustworthy. Return ONLY a JSON object, no prose."
)

RESPONSE_SCHEMA = {
    "dispute_exists": "boolean -- true if the difference exceeds the printed tolerance_pct",
    "primary_dispute_category": f"if disputed, one of {CATEGORY_MENU}; else null",
    "offending_component": "the specific risk class / bucket / risk-factor id responsible "
                           "(e.g. 'CREDIT:HY:RF-003'); else null",
    "correct_im_amount": "the total IM you judge to be correct -- copy the exact figure from "
                         "whichever side's total_im in the printed margin_breakdown (firm's or "
                         "counterparty's) you determine is accurate; do not recompute it",
    "dispute_difference": "the dollar difference between the two totals, from the printed "
                          "figures (0 if no dispute)",
    "should_escalate": "boolean",
    "escalation_target": "who this should go to if disputed (e.g. 'IM disputes desk'); else null",
}


def build_prompt(case: dict) -> str:
    return (
        "NETTING SET:\n"
        + json.dumps(case["netting_set"], indent=2)
        + "\n\nFIRM'S REPORTED SENSITIVITIES:\n"
        + json.dumps(case["firm_sensitivities"], indent=2)
        + "\n\nCOUNTERPARTY'S REPORTED SENSITIVITIES:\n"
        + json.dumps(case["counterparty_sensitivities"], indent=2)
        + "\n\nRISK WEIGHTS (illustrative, SIMM-style -- the published table both sides are meant to use):\n"
        + json.dumps(case["risk_weights"], indent=2)
        + "\n\nCORRELATIONS:\n"
        + json.dumps(case["correlations"], indent=2)
        + "\n\nMETHODOLOGY METADATA (as disclosed by each party in the reconciliation packet):\n"
        + json.dumps(case["methodology_metadata"], indent=2)
        + "\n\nCONCENTRATION ADD-ON (published rule + whether each side actually applied it):\n"
        + json.dumps(case["concentration_addon_metadata"], indent=2)
        + f"\n\nDISPUTE TOLERANCE: {case['tolerance_pct']}% relative difference\n"
        + "\nMARGIN BREAKDOWN (already computed for both sides -- do not recompute):\n"
        + json.dumps(case["margin_breakdown"], indent=2)
        + "\n\nReturn ONLY this JSON object:\n"
        + json.dumps(RESPONSE_SCHEMA, indent=2)
    )


def load_cases(path: str = DATASET_DEFAULT, limit=None, case_ids_path=None):
    data = json.loads(Path(path).read_text())
    cases = data["cases"] if isinstance(data, dict) else data
    if case_ids_path:
        ids = set(json.loads(Path(case_ids_path).read_text()))
        cases = [c for c in cases if c["case_id"] in ids]
    if limit:
        cases = cases[:limit]
    return cases


def strip_json_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    return t.strip()
