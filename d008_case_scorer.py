"""
AAL-D-008 — per-case scoring adapter for the eval drivers.

score_d008.py scores a whole model at once: score_model() takes every run's
responses together and returns pooled figures. The drivers work the other
way round, scoring each prediction as it comes back so a run is resume-safe
at (case, run) and a crash loses at most one call.

This module bridges the two, exposing the same pair of functions the D-007
driver expects:

    score_case_d008(case, pred)  -> flat score dict, written into results
    aggregate_d008(records, n)   -> scorecard dict, written at end of run

Scoring itself is not reimplemented here. The comparison functions in
score_d008.py are the single source of truth; this module parses the model's
raw JSON into the schema objects they expect and flattens what they return.
"""
from __future__ import annotations

import math

from pydantic import ValidationError

from schemas.d008 import CaseInput, GroundTruth, ModelResponse, RootCauseCategory
from score_d008 import (score_attribution, score_detection, score_false_positive,
                        score_value)


def _wilson(successes: int, n: int, z: float = 1.96) -> list[float]:
    """Wilson score interval, matching the corpus convention of reporting
    accuracy with a 95% interval rather than a bare point estimate."""
    if n == 0:
        return [0.0, 0.0]
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def score_case_d008(case: dict, pred: dict) -> dict:
    """Score one prediction against one case.

    case is an entry from the exported dataset: {case_id, case_input,
    ground_truth}. pred is whatever the model returned, already parsed from
    JSON, or {"_error": ...} if the call failed after retries.

    A malformed response is an error, not a wrong answer. Scoring it as
    incorrect would understate the model and quietly inflate the error rate
    into the accuracy figure.
    """
    if "_error" in pred:
        return {"error": True, "error_detail": str(pred["_error"])[:300]}

    gt = GroundTruth(**case["ground_truth"])
    ci = CaseInput(**case["case_input"])

    try:
        # case_id is required by the schema but deliberately not asked of the
        # model: the driver already knows which case it sent, and making the
        # model echo it back adds a way to fail that has nothing to do with
        # the task being measured.
        resp = ModelResponse(case_id=case["case_id"], **{k: v for k, v in pred.items()
                                                         if k != "case_id"})
    except ValidationError as e:
        return {"error": True, "error_detail": f"schema validation: {e}"[:300]}

    value_correct = score_value(gt, resp)

    return {
        "error": False,
        "gt_root_cause": gt.correct_root_cause.value,
        "predicted_root_cause": resp.root_cause.value,
        "gt_discrepancy": gt.correct_discrepancy_detected,
        "is_trap": ci.is_trap,
        "detection_correct": score_detection(gt, resp),
        "attribution_correct": score_attribution(gt, resp),
        # None where value scoring does not apply (NO_DISPUTE and
        # INSUFFICIENT_DATA cases). Kept as None rather than coerced to False
        # so it drops out of the denominator instead of counting against the
        # model, matching score_value's own contract.
        "value_correct": value_correct,
        "false_positive_on_trap": score_false_positive(ci, resp),
        "stated_confidence": resp.confidence,
        "boundary_distance": gt.boundary_distance,
    }


def aggregate_d008(records: list[dict], n_runs: int) -> dict:
    """Build the end-of-run scorecard from the per-case scores.

    records is the driver's results structure:
        [{"case_id": ..., "runs": [{"prediction", "score", "telemetry"}, ...]}]
    """
    scored = [r["score"] for rec in records for r in rec["runs"]]
    ok = [s for s in scored if not s.get("error")]
    n_errors = len(scored) - len(ok)

    def rate(key: str) -> dict:
        vals = [s[key] for s in ok if s.get(key) is not None]
        hits = sum(1 for v in vals if v)
        return {
            "accuracy": hits / len(vals) if vals else 0.0,
            "n": len(vals),
            "wilson_95": _wilson(hits, len(vals)),
        }

    # False-positive rate is over trap cases only, so it needs its own
    # denominator rather than the full observation count.
    trap_obs = [s for s in ok if s.get("is_trap")]
    fp_hits = sum(1 for s in trap_obs if s.get("false_positive_on_trap"))

    # Attribution flip rate: did the model's root_cause answer differ across
    # its own runs on the same case? A case needs two non-error runs to be
    # eligible, so the denominator is reported alongside.
    eligible = 0
    flipped = 0
    for rec in records:
        causes = [r["score"]["predicted_root_cause"] for r in rec["runs"]
                  if not r["score"].get("error")]
        if len(causes) < 2:
            continue
        eligible += 1
        if len(set(causes)) > 1:
            flipped += 1

    # Brier score over attribution: (stated confidence - outcome)^2.
    brier_terms = [(s["stated_confidence"] - (1.0 if s["attribution_correct"] else 0.0)) ** 2
                   for s in ok if s.get("stated_confidence") is not None]

    per_category: dict[str, list[bool]] = {}
    for s in ok:
        per_category.setdefault(s["gt_root_cause"], []).append(s["attribution_correct"])

    return {
        "dataset": "AAL-D-008",
        "n_cases": len(records),
        "n_runs": n_runs,
        "n_observations": len(scored),
        "n_errors": n_errors,
        "detection_accuracy": rate("detection_correct"),
        "attribution_accuracy": rate("attribution_correct"),
        "value_accuracy": rate("value_correct"),
        "false_positive_rate": {
            "rate": fp_hits / len(trap_obs) if trap_obs else 0.0,
            "n_trap_observations": len(trap_obs),
            "wilson_95": _wilson(fp_hits, len(trap_obs)),
        },
        "attribution_flip_rate": {
            "rate": flipped / eligible if eligible else 0.0,
            "flipped": flipped,
            "eligible_cases": eligible,
        },
        "brier_score": sum(brier_terms) / len(brier_terms) if brier_terms else None,
        "per_category_attribution": {
            cat: {"accuracy": sum(1 for v in vals if v) / len(vals), "n": len(vals)}
            for cat, vals in sorted(per_category.items())
        },
    }
