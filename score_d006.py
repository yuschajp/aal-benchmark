"""
AAL-D-006 deterministic scorer.

score_case_d006(case, prediction) -> per-metric booleans for one (case, run).
aggregate_d006(records, runs) -> scorecard with pooled Wilson 95% CIs.

Metrics:
  detection            — predicted eligible == ground-truth eligible
  false_break_rate     — on ground-truth-ELIGIBLE cases: fraction wrongly flagged ineligible
  category_accuracy    — on ground-truth-ineligible cases the model caught: right ELIG-* code
  field_accuracy       — right offending_field (alias-tolerant)
  value_accuracy       — exception_value within tolerance of ground truth
  substitution_validity— recommended an eligible asset type (and not the offending one)
  escalation_accuracy  — should_escalate matches (ineligible -> True)
Scorer version: d006-score-v1.0.0
"""
from __future__ import annotations

import math

SCORER_VERSION = "d006-score-v1.0.0"

FIELD_ALIASES = {
    "asset_type": {"asset_type", "asset type", "assettype", "asset class", "asset_class", "type"},
    "rating": {"rating", "credit rating", "credit_rating"},
    "currency": {"currency", "ccy", "fx"},
    "issuer": {"issuer", "obligor", "name"},
    "maturity_years": {"maturity_years", "maturity", "tenor", "maturity years"},
    "market_value": {"market_value", "market value", "notional", "amount", "denomination"},
    "applied_haircut_pct": {"applied_haircut_pct", "haircut", "applied haircut", "haircut_pct"},
    "docs_current": {"docs_current", "docs", "documentation", "eligibility docs"},
    "post_haircut_value": {"post_haircut_value", "post-haircut value", "collateral value", "shortfall"},
}


def _norm(s):
    return str(s).strip().lower().replace("-", " ").replace("_", " ") if s is not None else ""


def _field_ok(pred_field, gt_field):
    if gt_field is None:
        return pred_field in (None, "", "null")
    aliases = FIELD_ALIASES.get(gt_field, {gt_field})
    return _norm(pred_field) in {_norm(a) for a in aliases}


def _value_ok(pred_val, gt_val):
    try:
        pv = float(pred_val)
    except (TypeError, ValueError):
        return False
    return abs(pv - float(gt_val)) <= max(1.0, 0.01 * abs(float(gt_val)))


def score_case_d006(case: dict, prediction: dict) -> dict:
    gt = case["ground_truth"]
    if not isinstance(prediction, dict) or prediction.get("_error"):
        return {"error": True, "detection_correct": False}

    pred_elig = bool(prediction.get("eligible"))
    gt_elig = bool(gt["eligible"])
    s = {"error": False, "gt_eligible": gt_elig, "detection_correct": pred_elig == gt_elig}

    if gt_elig:
        # eligible / trap case: the discriminator is whether the model false-flags
        s["is_clean"] = True
        s["false_break"] = (pred_elig is False)
    else:
        s["is_clean"] = False
        if pred_elig is False:  # caught it — now grade the attribution
            s["category_correct"] = (prediction.get("reason_category") == gt["reason_category"])
            s["field_correct"] = _field_ok(prediction.get("offending_field"), gt["offending_field"])
            s["value_correct"] = _value_ok(prediction.get("exception_value"), gt["exception_value"])
        else:
            s["category_correct"] = s["field_correct"] = s["value_correct"] = False
        subs = gt.get("valid_substitute_asset_types", [])
        rec = str(prediction.get("substitution_recommendation", "")).strip().upper()
        s["substitution_valid"] = rec in {x.upper() for x in subs}
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


def aggregate_d006(records: list[dict], runs: int) -> dict:
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
        "false_break_rate": rate(lambda s: s.get("false_break"), clean),
        "attribution_category_accuracy": rate(lambda s: s.get("category_correct"), caught),
        "field_accuracy": rate(lambda s: s.get("field_correct"), caught),
        "value_accuracy": rate(lambda s: s.get("value_correct"), caught),
        "substitution_validity": rate(lambda s: s.get("substitution_valid"), dirty),
        "escalation_accuracy": rate(lambda s: s.get("escalation_correct"), dirty),
    }
