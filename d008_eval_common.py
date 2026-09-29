"""
AAL-D-008 eval-common: prompt construction + case loading, shared by every
model driver.

The model is a repo / securities lending operations analyst. It sees two
counterparties' records of the same trade, plus the governing trade
confirmation where one exists, and must decide whether a genuine rate or
amount discrepancy exists, attribute the root cause, and report the correct
figure.

Two design points carried from the rest of the corpus:

  1. The model must not aggregate. Where a case turns on an accrual, both
     sides' computed figures are printed in their records. The model reads,
     judges which side the confirmation supports, and reports. This matches
     D-007, where every margin figure is pre-computed and the prompt says so
     explicitly.

  2. Neither side is correct by default. The confirmation agrees with
     counterparty A in roughly half of cases and counterparty B in the other
     half, by design -- a model that always copies one side scores at chance.
     The system instruction says so, as D-007's does.

INSUFFICIENT_DATA is a first-class answer, not a fallback for uncertainty.
Some cases are built to be unanswerable: the records disagree and nothing in
them explains why. Saying so is scored correct on those cases and wrong
everywhere else.
"""
from __future__ import annotations

import json
from pathlib import Path

# Tolerances the generator scores against. The model is judging whether a
# difference exceeds these, so it has to be told what they are -- the trap
# cases sit deliberately just inside them.
from generate_d008 import DOLLAR_TOLERANCE_USD, RATE_TOLERANCE_BPS

DATASET_DEFAULT = "datasets/AAL-D-008-v0.1.json"
PROMPT_VERSION = "d008-prompt-v0.1"

CATEGORY_MENU = [
    "RL-DIS-DAYCOUNT", "RL-DIS-BENCHRATE", "RL-DIS-HAIRCUT",
    "RL-DIS-SETTLEDATE", "RL-DIS-NOTIONAL", "RL-DIS-CORPACTION",
    "RL-DIS-FEESPLIT", "RL-DIS-ACCRUAL", "RL-DIS-RECALL",
]

SYSTEM_INSTRUCTION = (
    "You are a repo and securities lending operations analyst reconciling two counterparties' "
    "records of the same trade. Do NOT assume either side's record is correct by default: in "
    "this dataset the error, when one exists, is equally likely to be on counterparty A's side "
    "or counterparty B's side, so you must judge which one to trust in every case. Where a "
    "governing trade confirmation is provided, it is authoritative and settles which terms were "
    "agreed. Decide whether a genuine DISCREPANCY exists (the difference exceeds tolerance) or "
    "whether the two records agree within tolerance; if there is a discrepancy, attribute its "
    "root cause and report the figure you judge to be correct. Where a case turns on accrued "
    "interest, each side's computed figure is printed in its record -- read the printed figures "
    "and judge which one the confirmation supports rather than recomputing from scratch. "
    "When you report a corrected amount, report the quantity the disputed term itself "
    "determines, not some other figure derived from it. If the notional is disputed, report "
    "the agreed notional. If the haircut is disputed, report the required collateral. If an "
    "accrual, settlement date, day count or recall is disputed, report the accrued interest. "
    "If a corporate action adjustment is disputed, report the adjustment amount. Do not apply "
    "a haircut to a disputed notional, or otherwise combine terms that are not themselves in "
    "dispute. If the "
    "records disagree but nothing in them explains why, answer RL-DIS-INSUFFICIENT-DATA: that is "
    "a correct answer on cases built to be unanswerable, not a fallback for uncertainty. Return "
    "ONLY a JSON object, no prose."
)

RESPONSE_SCHEMA = {
    "discrepancy_detected": "boolean -- true if a rate or amount discrepancy exists between "
                            "the two records beyond tolerance",
    "root_cause": f"one of {CATEGORY_MENU}, or 'RL-NO-DISPUTE' if the records agree within "
                  "tolerance, or 'RL-DIS-INSUFFICIENT-DATA' if they disagree but the records "
                  "do not contain enough information to determine why",
    "corrected_rate": "the rate you judge to be correct, if the case turns on a rate; else null",
    "corrected_amount": "if the case turns on an amount, the RESULTING computed figure -- the "
                        "required collateral amount, the accrued interest, or the adjustment "
                        "payable -- not the underlying term that caused the discrepancy. Where a "
                        "confirmation fixes a term (an agreed notional, haircut or recall amount), "
                        "compute the figure that term produces rather than restating the term "
                        "itself. Null if the case turns on a rate instead.",
    "confidence": "your confidence in the root_cause classification, 0.0 to 1.0",
    "rationale": "brief free-text explanation of your reasoning",
}

# Internal generator flags that must never reach the model.
_HIDDEN_CASE_FIELDS = {"is_trap"}


def build_prompt(case: dict) -> str:
    """Build the prompt from a case's input side only.

    case is one entry from the exported dataset: {case_id, case_input,
    ground_truth}. Ground truth is never referenced here.
    """
    ci = dict(case["case_input"])
    for field in _HIDDEN_CASE_FIELDS:
        ci.pop(field, None)

    governing = ci.get("governing_record")
    governing_block = (
        json.dumps(governing, indent=2) if governing is not None
        else "None. No trade confirmation is available for this trade."
    )

    return (
        f"TRADE TYPE: {ci.get('instrument_type')}\n\n"
        "COUNTERPARTY A'S RECORD:\n"
        + json.dumps(ci["counterparty_a"], indent=2)
        + "\n\nCOUNTERPARTY B'S RECORD:\n"
        + json.dumps(ci["counterparty_b"], indent=2)
        + "\n\nGOVERNING TRADE CONFIRMATION (authoritative where present):\n"
        + governing_block
        + f"\n\nDISPUTE TOLERANCE: rates matching within {RATE_TOLERANCE_BPS} basis points, "
          f"or amounts within ${DOLLAR_TOLERANCE_USD:,}, are considered in agreement and are "
          "NOT a discrepancy.\n"
        + "\n\nWhere a record includes accrued_interest, that figure is each side's own "
          "computed result -- read it rather than recomputing.\n"
        + "\nReturn ONLY this JSON object:\n"
        + json.dumps(RESPONSE_SCHEMA, indent=2)
    )


def load_cases(path: str = DATASET_DEFAULT, limit: int | None = None) -> list[dict]:
    data = json.loads(Path(path).read_text())
    cases = data["cases"]
    return cases[:limit] if limit else cases


def strip_json_fences(text: str) -> str:
    """Models sometimes wrap JSON in markdown fences despite instructions."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
