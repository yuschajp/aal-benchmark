"""
AAL-D-008 ablation: does the model compute, or only read?

Condition A (the dataset as published): each counterparty's computed figure,
accrued interest or required collateral, is printed in its record. The model
reads both and judges which one the confirmation supports.

Condition B: those computed figures are withheld from every record, and the
calculation conventions are stated in the prompt. Where a case turns on a
computed figure, the model has to produce it.

Same cases, same ground truth, same scorer. The only thing that changes is
whether the answer is printed on the page. If frontier models hold near 100%
in A and drop in B, that measures the arithmetic gap directly on identical
cases, rather than inferring it across different datasets.

Design notes:

  * Figures are stripped from ALL records uniformly, never by category.
    Stripping only in some categories would make the presence or absence of a
    printed figure a clue to the answer.

  * RL-DIS-ACCRUAL is excluded from condition B. Its two records are
    identical except for the printed interest, so removing it leaves no
    visible dispute, which is the defect fixed earlier in this dataset.

  * The conventions are stated exactly. The HAIRCUT defect came from a model
    using a different, equally standard gross-up convention; condition B
    tests computation, not convention guessing.

  * This module wraps d008_eval_common rather than editing it, so condition A
    is byte-for-byte unchanged.
"""
from __future__ import annotations

import copy

from d008_eval_common import SYSTEM_INSTRUCTION, build_prompt

COMPUTED_FIELDS = ("accrued_interest", "required_collateral")
EXCLUDED_FROM_B = {"RL-DIS-ACCRUAL"}

_READ_SENTENCE = (
    "Where a case turns on accrued interest, each side's computed figure is printed in "
    "its record -- read the printed figures and judge which one the confirmation supports "
    "rather than recomputing from scratch."
)
_COMPUTE_SENTENCE = (
    "Computed figures such as accrued interest and required collateral are not printed "
    "in the records. Where a case turns on one, compute it yourself using the calculation "
    "conventions given in the prompt."
)
if _READ_SENTENCE not in SYSTEM_INSTRUCTION:
    raise RuntimeError("Condition-A system instruction sentence not found; "
                       "d008_eval_common has changed. Update _READ_SENTENCE.")
SYSTEM_INSTRUCTION_B = SYSTEM_INSTRUCTION.replace(_READ_SENTENCE, _COMPUTE_SENTENCE, 1)

_PROMPT_READ_LINE = (
    "Where a record includes accrued_interest, that figure is each side's own computed "
    "result -- read it rather than recomputing.\n"
)
CONVENTIONS_BLOCK = (
    "CALCULATION CONVENTIONS (use these exactly; computed figures are not printed):\n"
    "- Accrual days = settlement_date minus trade_date, in calendar days. Where the "
    "confirmation states an agreed settlement date, use it.\n"
    "- Accrued interest = notional x rate x accrual days / basis. Basis is 360 for "
    "ACT/360 and 365 for ACT/365. Where the confirmation states an agreed day count "
    "convention, use it.\n"
    "- Required collateral = notional x (1 + haircut_pct).\n"
    "- Post-recall notional = original_notional minus recall_notice_amount. Accrue "
    "interest on the post-recall notional.\n"
)


def build_prompt_b(case: dict) -> str:
    """Condition-B prompt. Works on a copy; the original case is untouched,
    so scoring still uses the full, unmodified case."""
    c = copy.deepcopy(case)
    ci = c["case_input"]
    for side in ("counterparty_a", "counterparty_b"):
        for f in COMPUTED_FIELDS:
            ci[side].pop(f, None)
    prompt = build_prompt(c)
    if _PROMPT_READ_LINE not in prompt:
        raise RuntimeError("Condition-A prompt line not found; build_prompt has changed. "
                           "Update _PROMPT_READ_LINE.")
    return prompt.replace(_PROMPT_READ_LINE, "\n" + CONVENTIONS_BLOCK, 1)


def cases_for_b(cases: list[dict]) -> list[dict]:
    return [c for c in cases
            if c["ground_truth"]["correct_root_cause"] not in EXCLUDED_FROM_B]
