"""
AAL-D-006 eval-common: prompt construction + case loading, shared by every
model driver. The model is a collateral-ops analyst: it classifies eligibility
and recommends a substitute. It must NOT compute — every figure (haircut,
post-haircut value, concentration %, shortfall) is printed in the valuation
summary. The model reads and judges.
"""
from __future__ import annotations

import json
from pathlib import Path

DATASET_DEFAULT = "datasets/AAL-D-006-v1.0.json"
PROMPT_VERSION = "d006-prompt-v1.0"

CATEGORY_MENU = [
    "ELIG-ASSETTYPE", "ELIG-RATING", "ELIG-CCY", "ELIG-WRONGWAY", "ELIG-MATURITY",
    "ELIG-DENOM", "ELIG-DOCS", "ELIG-HAIRCUT", "ELIG-CONC-ISSUER",
    "ELIG-CONC-CLASS", "ELIG-CONC-CCY", "ELIG-SHORTFALL",
]

SYSTEM_INSTRUCTION = (
    "You are a collateral operations analyst validating a proposed collateral posting "
    "against a bilateral CSA eligibility schedule. Decide whether the proposed piece is "
    "ELIGIBLE as-is. Every numeric figure you need (schedule-correct haircut, post-haircut "
    "value, resulting concentration percentages, and any shortfall) is already computed and "
    "printed in the valuation summary — DO NOT perform any arithmetic yourself; read the "
    "printed figures. Return ONLY a JSON object, no prose."
)

RESPONSE_SCHEMA = {
    "eligible": "boolean — true if the proposed piece is acceptable as-is",
    "reason_category": f"if ineligible, one of {CATEGORY_MENU}; else null",
    "offending_field": "the specific field that fails (e.g. asset_type, rating, currency, "
                       "issuer, maturity_years, market_value, applied_haircut_pct, "
                       "docs_current, post_haircut_value); else null",
    "exception_value": "the dollar magnitude of the exception, taken from the printed figures "
                       "(0 if eligible)",
    "substitution_recommendation": "an eligible asset type to post instead (e.g. CASH, SOV, MMF)",
    "should_escalate": "boolean",
}


def build_prompt(case: dict) -> str:
    return (
        "CSA ELIGIBILITY SCHEDULE:\n"
        + json.dumps(case["csa_schedule"], indent=2)
        + "\n\nEXISTING POSTED BASKET:\n"
        + json.dumps(case["existing_basket"], indent=2)
        + "\n\nPROPOSED NEW COLLATERAL PIECE:\n"
        + json.dumps(case["proposed_piece"], indent=2)
        + f"\n\nREQUIRED COLLATERAL AMOUNT: {case['required_amount']}\n"
        + "\nVALUATION SUMMARY (already computed — do not recompute):\n"
        + json.dumps(case["valuation_summary"], indent=2)
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
