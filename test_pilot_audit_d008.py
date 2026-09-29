"""
AAL-D-008 — pilot audit gate test.

Exercises pilot_audit() with synthetic model responses. No API calls, no cost.

The pilot audit is the spec's mandatory pre-publication gate: it is supposed to
refuse to pass when a category shows a defect signature, meaning multiple models
from different labs all scoring at or near zero on the same category. That
pattern is what CALCDATE looked like in D-007, and it went unnoticed until after
publication.

A gate that has never been shown to fire is not a gate. This tests both
directions:

  1. Healthy pilot   -> must PASS
  2. One dead category across two models -> must FAIL and name that category
  3. One weak model on one category, other model fine -> must PASS
     (a single model struggling is a finding about the model, not the data --
      the gate should not block on it)

Run:
    python test_pilot_audit_d008.py
"""
from __future__ import annotations

import random
import sys

from generate_d008 import generate_batch
from schemas.d008 import GroundTruth, ModelResponse, RootCauseCategory
from score_d008 import pilot_audit


def _response(case_id: str, root_cause: RootCauseCategory,
              gt: GroundTruth) -> ModelResponse:
    return ModelResponse(
        case_id=case_id,
        discrepancy_detected=root_cause != RootCauseCategory.NO_DISPUTE,
        root_cause=root_cause,
        corrected_rate=gt.correct_rate,
        corrected_amount=gt.correct_amount,
        confidence=0.8,
        rationale="synthetic test response",
    )


def _wrong_cause(actual: RootCauseCategory) -> RootCauseCategory:
    """Any category other than the right one -- what a model does when it
    detects a dispute but misattributes it."""
    options = [c for c in RootCauseCategory if c != actual]
    return random.choice(options)


def build_responses(cases, accuracy_by_category: dict, model_seed: int):
    """Synthesize one model's responses. accuracy_by_category maps a category
    to the probability the model gets it right; anything unlisted defaults
    to 0.85."""
    rng = random.Random(model_seed)
    out = {}
    for g in cases:
        gt = g.ground_truth
        cat = gt.correct_root_cause
        p = accuracy_by_category.get(cat, 0.85)
        answer = cat if rng.random() < p else _wrong_cause(cat)
        out[g.case_input.case_id] = _response(g.case_input.case_id, answer, gt)
    return out


def run_scenario(name: str, cases, per_model_accuracy: dict,
                 expect_pass: bool, expect_flagged: set[str] | None = None) -> bool:
    ground_truths = {g.case_input.case_id: g.ground_truth for g in cases}
    responses = {
        model_id: build_responses(cases, acc, seed)
        for seed, (model_id, acc) in enumerate(per_model_accuracy.items())
    }

    report = pilot_audit(ground_truths, responses)
    flagged = {r.category.value for r in report.results if r.is_defect_signature}

    print(f"\n{'=' * 70}")
    print(f"SCENARIO: {name}")
    print(f"{'=' * 70}")
    report.print_summary()

    ok = report.passed == expect_pass
    if expect_flagged is not None and flagged != expect_flagged:
        ok = False

    print()
    if ok:
        print(f"TEST OK — gate behaved as expected (passed={report.passed}).")
    else:
        print(f"TEST FAILED — expected passed={expect_pass}", end="")
        if expect_flagged is not None:
            print(f" and flagged={expect_flagged or '{}'}", end="")
        print(f", got passed={report.passed}, flagged={flagged or '{}'}")
    return ok


def main():
    cases = generate_batch(n_cases=250, seed=8)
    results = []

    # 1. Healthy pilot -- both models competent everywhere.
    results.append(run_scenario(
        "healthy pilot, two competent models",
        cases,
        {"model-alpha": {}, "model-beta": {}},
        expect_pass=True,
        expect_flagged=set(),
    ))

    # 2. The CALCDATE shape: both models dead on the same category. This is
    #    the case the gate exists to catch.
    dead = {RootCauseCategory.HAIRCUT: 0.0}
    results.append(run_scenario(
        "CALCDATE shape — both models at 0% on RL-DIS-HAIRCUT",
        cases,
        {"model-alpha": dead, "model-beta": dead},
        expect_pass=False,
        expect_flagged={"RL-DIS-HAIRCUT"},
    ))

    # 3. One weak model only. A single model struggling is a finding about
    #    that model, not evidence of bad case data -- the gate should let
    #    this through rather than blocking the run.
    results.append(run_scenario(
        "one weak model on RL-DIS-RECALL, the other fine",
        cases,
        {"model-alpha": {RootCauseCategory.RECALL: 0.0}, "model-beta": {}},
        expect_pass=True,
        expect_flagged=set(),
    ))

    print(f"\n{'=' * 70}")
    if all(results):
        print(f"ALL {len(results)} SCENARIOS PASSED — pilot audit gate works.")
        return 0
    print(f"{results.count(False)} of {len(results)} SCENARIOS FAILED.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
