"""
AAL-D-008 — Repo / Securities Lending Rate Disputes
Deterministic scorer (draft v0.1)

No LLM calls anywhere in this module — same non-negotiable rule as every
other AAL scorer and as AlphaPod's risk_engine.py. All scoring is plain
Python arithmetic and comparison against generator-side ground truth.

Two modes:
  - pilot_audit()   Run BEFORE the full nine-model roster. Refuses to pass
                     if any category shows a defect signature. This is the
                     check that would have caught D-007's CALCDATE category
                     before publication instead of after.
  - full_score()    The normal per-case, per-model scoring pass, run once
                     the pilot audit has passed.

Do not skip pilot_audit(). It is not optional tooling — it is the fix.
"""

from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
import json
import math
import sys

from schemas.d008 import (
    CaseInput,
    GroundTruth,
    ModelResponse,
    RootCauseCategory,
)

# ---------------------------------------------------------------------------
# Tolerances — business rules, not scorer judgment calls.
# These must come from domain SME input (see spec Open Questions), not be
# invented here. Placeholder values shown; DO NOT ship with these unvalidated.
# ---------------------------------------------------------------------------

RATE_TOLERANCE_BPS = 0.5        # basis points; below this, treat as matching
AMOUNT_TOLERANCE_USD = 1.00     # dollar tolerance for value_correct

# Defect-signature threshold for the pilot audit. A category scoring at or
# below this across every model in the pilot is treated as a probable
# generator defect, not a genuine finding, and blocks the full run.
DEFECT_SIGNATURE_THRESHOLD = 0.02   # 2% — allows for rare legitimate misses
DEFECT_SIGNATURE_MIN_MODELS = 2     # must hold across at least this many models


# ---------------------------------------------------------------------------
# Core per-case comparison
# ---------------------------------------------------------------------------

def score_detection(gt: GroundTruth, resp: ModelResponse) -> bool:
    predicted_dispute = resp.root_cause != RootCauseCategory.NO_DISPUTE
    actual_dispute = gt.correct_root_cause != RootCauseCategory.NO_DISPUTE
    return predicted_dispute == actual_dispute


def score_attribution(gt: GroundTruth, resp: ModelResponse) -> bool:
    """INSUFFICIENT_DATA scores as correct when ground truth agrees the case
    is genuinely unanswerable — this is the direct fix for the CALCDATE
    failure mode, where every model gave the same wrong forced-choice answer
    because no legitimate 'cannot be determined' option existed."""
    return resp.root_cause == gt.correct_root_cause


def score_value(gt: GroundTruth, resp: ModelResponse) -> bool | None:
    """Returns None when value scoring doesn't apply (NO_DISPUTE or
    INSUFFICIENT_DATA cases) rather than forcing a True/False that would
    silently distort the pooled accuracy the way CALCDATE did."""
    if gt.correct_root_cause in (RootCauseCategory.NO_DISPUTE, RootCauseCategory.INSUFFICIENT_DATA):
        return None

    if gt.correct_rate is not None:
        if resp.corrected_rate is None:
            return False
        return abs(resp.corrected_rate - gt.correct_rate) * 10_000 <= RATE_TOLERANCE_BPS

    if gt.correct_amount is not None:
        if resp.corrected_amount is None:
            return False
        return abs(resp.corrected_amount - gt.correct_amount) <= AMOUNT_TOLERANCE_USD

    return None


def score_false_positive(case: CaseInput, resp: ModelResponse) -> bool:
    """True if this was a trap case and the model wrongly flagged a dispute."""
    if not case.is_trap:
        return False
    return resp.root_cause != RootCauseCategory.NO_DISPUTE


# ---------------------------------------------------------------------------
# Pilot audit — MANDATORY pre-publication gate
# ---------------------------------------------------------------------------

@dataclass
class CategoryAuditResult:
    category: RootCauseCategory
    per_model_accuracy: dict[str, float]
    is_defect_signature: bool
    note: str = ""


@dataclass
class PilotAuditReport:
    results: list[CategoryAuditResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(r.is_defect_signature for r in self.results)

    def print_summary(self) -> None:
        print("=== D-008 Pilot Audit — Per-Category Accuracy ===")
        for r in self.results:
            flag = "  <-- DEFECT SIGNATURE, DO NOT PUBLISH" if r.is_defect_signature else ""
            accs = ", ".join(f"{m}={a:.1%}" for m, a in r.per_model_accuracy.items())
            print(f"{r.category.value:30s} {accs}{flag}")
        print()
        if self.passed:
            print("PASSED — no defect signatures. Safe to proceed to full nine-model roster.")
        else:
            print("FAILED — one or more categories show a defect signature.")
            print("Do not scale to the full roster. Check these categories' cases against")
            print("source trade data directly (not the scorer) before proceeding. This is")
            print("the exact check that would have caught D-007's CALCDATE defect pre-publication.")


def pilot_audit(
    ground_truths: dict[str, GroundTruth],
    pilot_responses: dict[str, dict[str, ModelResponse]],  # model_id -> case_id -> response
) -> PilotAuditReport:
    """Run this against 1–2 models on the FULL case set before committing to
    the nine-model roster. Any category at or near 0% across multiple models
    is treated as a probable generator defect, not a finding, and blocks
    the full run until manually resolved.

    This function is the direct implementation of the spec's mandatory
    'Per-category accuracy audit as a pre-publication gate, not a post-hoc
    check.' It must run and pass BEFORE eval_out_d008/ is populated at scale.
    """
    by_category: dict[RootCauseCategory, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))

    for model_id, responses in pilot_responses.items():
        for case_id, resp in responses.items():
            gt = ground_truths[case_id]
            correct = score_attribution(gt, resp)
            by_category[gt.correct_root_cause][model_id].append(correct)

    report = PilotAuditReport()
    for category, per_model in by_category.items():
        per_model_accuracy = {
            model_id: sum(results) / len(results) if results else 0.0
            for model_id, results in per_model.items()
        }

        models_at_defect_level = sum(
            1 for acc in per_model_accuracy.values() if acc <= DEFECT_SIGNATURE_THRESHOLD
        )
        is_defect = models_at_defect_level >= min(DEFECT_SIGNATURE_MIN_MODELS, len(per_model_accuracy))

        note = ""
        if is_defect:
            note = (
                f"{models_at_defect_level}/{len(per_model_accuracy)} models scored "
                f"<= {DEFECT_SIGNATURE_THRESHOLD:.0%} on this category. Models from "
                f"different labs agreeing on near-zero accuracy is a case-data problem, "
                f"not a shared model weakness — verify against source data before trusting this number."
            )

        report.results.append(
            CategoryAuditResult(
                category=category,
                per_model_accuracy=per_model_accuracy,
                is_defect_signature=is_defect,
                note=note,
            )
        )

    return report


# ---------------------------------------------------------------------------
# Full scoring pass — run only after pilot_audit().passed is True
# ---------------------------------------------------------------------------

@dataclass
class FullScoreReport:
    model_id: str
    detection_accuracy: float
    attribution_accuracy: float
    value_accuracy: float                    # excludes None-scored cases from denominator
    false_positive_rate: float
    flip_rate: float                         # fraction of cases with inconsistent answers across runs
    brier_score: float                       # calibration; lower is better
    per_category_accuracy: dict[str, float]
    per_boundary_bucket_accuracy: dict[str, float]


def score_model(
    model_id: str,
    cases: dict[str, CaseInput],
    ground_truths: dict[str, GroundTruth],
    responses_by_run: list[dict[str, ModelResponse]],  # one dict per run (typically 3)
) -> FullScoreReport:
    detection_results: list[bool] = []
    attribution_results: list[bool] = []
    value_results: list[bool] = []
    fp_results: list[bool] = []
    brier_terms: list[float] = []

    per_category: dict[RootCauseCategory, list[bool]] = defaultdict(list)
    per_boundary: dict[str, list[bool]] = defaultdict(list)

    # Track per-case attribution across runs for flip rate
    attribution_by_case: dict[str, set[RootCauseCategory]] = defaultdict(set)

    for run in responses_by_run:
        for case_id, resp in run.items():
            gt = ground_truths[case_id]
            case = cases[case_id]

            det = score_detection(gt, resp)
            attr = score_attribution(gt, resp)
            val = score_value(gt, resp)
            fp = score_false_positive(case, resp)

            detection_results.append(det)
            attribution_results.append(attr)
            if val is not None:
                value_results.append(val)
            fp_results.append(fp)

            # Brier score: (confidence - outcome)^2, outcome = 1 if attribution correct else 0
            brier_terms.append((resp.confidence - (1.0 if attr else 0.0)) ** 2)

            per_category[gt.correct_root_cause].append(attr)

            bucket = _boundary_bucket(gt.boundary_distance)
            per_boundary[bucket].append(attr)

            attribution_by_case[case_id].add(resp.root_cause)

    flipped_cases = sum(1 for causes in attribution_by_case.values() if len(causes) > 1)
    flip_rate = flipped_cases / len(attribution_by_case) if attribution_by_case else 0.0

    return FullScoreReport(
        model_id=model_id,
        detection_accuracy=_avg(detection_results),
        attribution_accuracy=_avg(attribution_results),
        value_accuracy=_avg(value_results),
        false_positive_rate=_avg(fp_results),
        flip_rate=flip_rate,
        brier_score=_avg(brier_terms),
        per_category_accuracy={c.value: _avg(v) for c, v in per_category.items()},
        per_boundary_bucket_accuracy={b: _avg(v) for b, v in per_boundary.items()},
    )


def _boundary_bucket(distance: float | None) -> str:
    """Near-boundary stratification buckets. Thresholds are placeholders —
    validate against actual tolerance distribution once real case data exists."""
    if distance is None:
        return "not_applicable"
    if distance < 0.5:
        return "near_boundary (<0.5bp)"
    if distance < 2.0:
        return "moderate (0.5-2bp)"
    return "far_from_boundary (>2bp)"


def _avg(values: list[bool] | list[float]) -> float:
    if not values:
        return 0.0
    return sum(1.0 if v is True else (0.0 if v is False else v) for v in values) / len(values)


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("pilot", "full"):
        print("Usage:")
        print("  python score_d008.py pilot <ground_truth.json> <pilot_results_dir>")
        print("  python score_d008.py full  <ground_truth.json> <cases.json> <eval_out_dir>")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "pilot":
        # Load ground truth + pilot responses, run pilot_audit(), print report,
        # exit non-zero if it fails so this can gate a CI/build step.
        # (Loading logic omitted — wire up to actual eval_out_d008_pilot/ layout.)
        print("Wire up ground_truth + pilot response loading, then call pilot_audit().")
        print("Full nine-model roster MUST NOT start until this exits 0.")

    elif mode == "full":
        print("Wire up ground_truth + cases + eval_out_d008/*.json loading,")
        print("then call score_model() per model and write scorecard_d008_<model>.json,")
        print("matching the D-007 scorecard output format.")
