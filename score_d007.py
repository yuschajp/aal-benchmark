"""
AAL-D-007 deterministic scorer.

score_case_d007(case, prediction) -> per-metric booleans for one (case, run).
aggregate_d007(records, runs) -> scorecard with pooled Wilson 95% CIs.

Metrics:
  detection             — predicted dispute_exists == ground-truth dispute_exists
  false_flag_rate        — on ground-truth-CLEAN (trap) cases: fraction wrongly flagged as disputes
  category_accuracy      — on ground-truth-disputed cases the model caught: right IM-DIS-* code
  component_accuracy     — right offending_component (lenient prefix/substring match)
  value_accuracy         — correct_im_amount within tolerance of ground truth
  difference_accuracy    — dispute_difference within tolerance of ground truth
  escalation_accuracy    — should_escalate matches (disputed -> True, clean -> False)
Scorer version: d007-score-v1.0.0
"""
from __future__ import annotations

import math

SCORER_VERSION = "d007-score-v1.0.0"


def _norm(s):
    return str(s).strip().upper() if s is not None else ""


def _component_core(s):
    if s is None:
        return None
    return _norm(s).split(" (")[0].strip()


def _component_ok(pred_component, gt_component):
    if gt_component is None:
        return pred_component in (None, "", "null")
    core = _component_core(gt_component)
    pred = _norm(pred_component)
    if not pred:
        return False
    return core in pred or pred in core


def _value_ok(pred_val, gt_val, min_tol=1.0, rel_tol=0.01):
    try:
        pv = float(pred_val)
    except (TypeError, ValueError):
        return False
    return abs(pv - float(gt_val)) <= max(min_tol, rel_tol * abs(float(gt_val)))


def score_case_d007(case: dict, prediction: dict) -> dict:
    gt = case["ground_truth"]
    if not isinstance(prediction, dict) or prediction.get("_error"):
        return {"error": True, "detection_correct": False}

    pred_disputed = bool(prediction.get("dispute_exists"))
    gt_disputed = bool(gt["dispute_exists"])
    s = {"error": False, "gt_disputed": gt_disputed, "detection_correct": pred_disputed == gt_disputed}

    if not gt_disputed:
        # trap / clean case: the discriminator is whether the model false-flags it
        s["is_clean"] = True
        s["false_flag"] = (pred_disputed is True)
        s["escalation_correct"] = (bool(prediction.get("should_escalate")) is False)
    else:
        s["is_clean"] = False
        if pred_disputed:  # caught it — now grade the attribution
            s["category_correct"] = (prediction.get("primary_dispute_category") == gt["primary_dispute_category"])
            s["component_correct"] = _component_ok(prediction.get("offending_component"), gt["offending_component"])
            s["value_correct"] = _value_ok(prediction.get("correct_im_amount"), gt["correct_im_amount"])
            s["difference_correct"] = _value_ok(prediction.get("dispute_difference"), gt["dispute_difference"])
        else:
            s["category_correct"] = s["component_correct"] = False
            s["value_correct"] = s["difference_correct"] = False
        s["escalation_correct"] = (bool(prediction.get("should_escalate")) is True)
    return s


# ---- aggregation with pooled Wilson 95% CI ----------------------------------
def _wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(p, 4), round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


def _metric(hits, n):
    p, lo, hi = _wilson(hits, n)
    return {"pooled": {"accuracy": p, "n": n, "hits": hits, "wilson_95": [lo, hi]}} if n else None


def aggregate_d007(records: list[dict], runs: int) -> dict:
    flat = [r for rec in records for r in rec["runs"]]
    scores = [r["score"] for r in flat]
    n_err = sum(1 for s in scores if s.get("error"))
    ok = [s for s in scores if not s.get("error")]

    def rate(pred, cond=lambda s: True):
        sub = [s for s in ok if cond(s)]
        return _metric(sum(1 for s in sub if pred(s)), len(sub))

    clean = lambda s: s.get("is_clean")
    dirty = lambda s: s.get("is_clean") is False
    caught = lambda s: dirty(s) and s.get("detection_correct")

    return {
        "scorer_version": SCORER_VERSION,
        "n_cases": len(records),
        "n_runs_total": len(flat),
        "n_errors": n_err,
        "detection_accuracy": rate(lambda s: s["detection_correct"]),
        "false_flag_rate": rate(lambda s: s.get("false_flag"), clean),
        "category_accuracy": rate(lambda s: s.get("category_correct"), caught),
        "component_accuracy": rate(lambda s: s.get("component_correct"), caught),
        "value_accuracy": rate(lambda s: s.get("value_correct"), caught),
        "difference_accuracy": rate(lambda s: s.get("difference_correct"), caught),
        "escalation_accuracy": rate(lambda s: s.get("escalation_correct")),
    }
