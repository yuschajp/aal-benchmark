"""
AAL-D-008 — Repo / Securities Lending Rate Disputes
Schema definitions (draft v0.1)

Follows the D-001–D-007 pattern: one case in, one structured JSON response out,
scored deterministically against generator-side ground truth.

New in this draft relative to prior series schemas (per D-007 v1.2 lessons):
  - `RootCauseCategory.INSUFFICIENT_DATA` as a first-class, scoreable-as-correct option
  - `confidence` field on the model response, for calibration scoring
  - `boundary_distance` on ground truth, for near-boundary stratification
  - `CaseInput.is_trap` explicit flag (within-tolerance / no-dispute cases)

Do not treat this as final — case volume, category list, and field names need
domain validation before the generator is built (see spec draft, Open Questions).
"""

from __future__ import annotations
from datetime import date
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Root cause taxonomy (draft — needs domain validation, see spec)
# ---------------------------------------------------------------------------

class RootCauseCategory(str, Enum):
    DAYCOUNT = "RL-DIS-DAYCOUNT"          # Day-count convention mismatch
    BENCHRATE = "RL-DIS-BENCHRATE"        # Benchmark rate reset mismatch
    HAIRCUT = "RL-DIS-HAIRCUT"            # Haircut/margin schedule mismatch
    SETTLEDATE = "RL-DIS-SETTLEDATE"      # Settlement date convention mismatch
    NOTIONAL = "RL-DIS-NOTIONAL"          # Notional/principal mismatch
    CORPACTION = "RL-DIS-CORPACTION"      # Corporate action adjustment error
    FEESPLIT = "RL-DIS-FEESPLIT"          # Rebate/fee split error
    ACCRUAL = "RL-DIS-ACCRUAL"            # Accrual period boundary error
    RECALL = "RL-DIS-RECALL"              # Recall/return timing mismatch

    # New: first-class "cannot be determined" response, distinct from a wrong
    # guess among the above. Scored as CORRECT when the case is genuinely
    # unanswerable from the data given. Prevents the CALCDATE failure mode,
    # where every model gave the same wrong-but-defensible forced-choice
    # answer because no legitimate "insufficient information" option existed.
    INSUFFICIENT_DATA = "RL-DIS-INSUFFICIENT-DATA"

    # No dispute — used for trap cases scored as "no discrepancy found"
    NO_DISPUTE = "RL-NO-DISPUTE"


class DayCountConvention(str, Enum):
    ACT_360 = "ACT/360"
    ACT_365 = "ACT/365"
    THIRTY_360 = "30/360"


class BenchmarkRate(str, Enum):
    SOFR = "SOFR"
    TERM_SOFR = "TERM_SOFR"
    GC_RATE = "GC_RATE"


# ---------------------------------------------------------------------------
# Case input — what the model sees
# ---------------------------------------------------------------------------

class CounterpartyRecord(BaseModel):
    """One side's record of the trade. Both sides shown to the model verbatim;
    no pre-computed discrepancy or hint fields."""
    counterparty_id: str
    trade_id: str
    notional: float
    rate: float                                  # as recorded by this counterparty
    day_count_convention: DayCountConvention
    benchmark_rate: Optional[BenchmarkRate] = None
    haircut_pct: Optional[float] = None
    trade_date: date
    settlement_date: date
    maturity_date: Optional[date] = None          # None for open/evergreen repo
    collateral_description: Optional[str] = None
    corporate_action_adjustment: Optional[float] = None
    rebate_rate: Optional[float] = None            # securities lending only
    required_collateral: Optional[float] = None
    """Each side's own computed required collateral amount.

    Added after a pilot finding: both pilot models scored 0% on value for
    HAIRCUT cases, because the required-collateral figure existed only in the
    scorer. One model grossed up by dividing by (1 - haircut) instead of
    multiplying by (1 + haircut), which is also standard practice, and missed
    by six figures on a quarter-billion notional against a $1 tolerance.
    Printing each side's figure makes the dispute readable instead of testing
    which convention the model guesses."""

    accrued_interest: Optional[float] = None
    """Each side's own computed accrual figure.

    Added after a verification finding: an accrual dispute where both sides
    show identical dates and rates is invisible in the case data -- the
    disagreement existed only inside the generator. A real accrual break
    surfaces as two different interest numbers on matching terms, which is
    what this field makes inspectable."""


class GoverningRecord(BaseModel):
    """The trade confirmation / master agreement terms that adjudicate a
    dispute. Added in response to a verification finding: with only two
    disagreeing counterparty records and nothing authoritative, a labeled
    "correct" value is ground truth the case data cannot support -- the
    CALCDATE failure mode. This record is what an ops analyst would actually
    pull to settle the question.

    CRITICAL: the generator must balance which side this record agrees with.
    If it always matches counterparty A, a model scores full marks by copying
    A without reading anything -- the exact flaw that invalidated AAL-D-007
    v1.0, where the perturbation was always injected into the counterparty's
    copy. Balance across sides is a correctness requirement, not a nicety.

    Fields are optional because a real confirmation does not restate every
    term -- only what was agreed. An absent field means the confirmation is
    silent, and the dispute is genuinely unadjudicable on that dimension."""
    confirmation_id: str
    agreed_notional: Optional[float] = None
    agreed_rate: Optional[float] = None
    agreed_day_count_convention: Optional[DayCountConvention] = None
    agreed_benchmark_rate: Optional[BenchmarkRate] = None
    agreed_haircut_pct: Optional[float] = None
    agreed_settlement_date: Optional[date] = None
    agreed_rebate_spread_bps: Optional[float] = None
    agreed_accrual_days: Optional[int] = None
    """Accrual day count the confirmation fixes.

    Added after a pilot finding: two frontier models from different labs both
    read ACCRUAL cases as unanswerable, and they were right. Both records
    showed identical terms with different interest, and the confirmation only
    restated a settlement date neither side disputed, so nothing in the case
    decided which day count was correct. The prompt also tells the model to
    read the printed interest rather than recompute, which is the one step
    that would have resolved it. This field is what adjudicates the case."""
    corporate_action_notice_amount: Optional[float] = None
    recall_notice_amount: Optional[float] = None
    original_notional: Optional[float] = None
    """Notional as originally booked, before any recall.

    Added after a pilot finding: RECALL cases put the pre-recall balance in
    agreed_notional, the same field that holds the correct answer in NOTIONAL
    cases. One field meant opposite things in two categories, and two models
    from different labs were led by it in different ways. agreed_notional now
    always means the correct current notional; the pre-recall figure lives
    here."""


class CaseInput(BaseModel):
    """The full case as presented to the model. No ground truth, no category
    hints, no pre-flagged discrepancy fields — the model must derive
    everything from the two counterparty records and the governing record."""
    case_id: str = Field(..., description="e.g. AAL-D-008-0001")
    instrument_type: str                          # "repo" | "sec_lending"
    counterparty_a: CounterpartyRecord
    counterparty_b: CounterpartyRecord
    governing_record: Optional[GoverningRecord] = Field(
        None,
        description=(
            "Trade confirmation terms that adjudicate the dispute. None when "
            "the case is deliberately unadjudicable (INSUFFICIENT_DATA)."
        ),
    )
    is_trap: bool = Field(
        ...,
        description=(
            "Internal generator flag, NEVER shown to the model or included "
            "in the prompt. Used only by the scorer to check false-positive "
            "rate on within-tolerance cases."
        ),
    )


# ---------------------------------------------------------------------------
# Model response — what the model must return
# ---------------------------------------------------------------------------

class ModelResponse(BaseModel):
    """Structured JSON the model must emit per case. Mirrors D-007's
    single-field-scoring pattern: detection, attribution, quantification
    all live in one validated object."""

    case_id: str

    discrepancy_detected: bool = Field(
        ..., description="Does a rate/amount discrepancy exist between the two records?"
    )

    root_cause: RootCauseCategory = Field(
        ...,
        description=(
            "Required even if discrepancy_detected is False (use NO_DISPUTE). "
            "Use INSUFFICIENT_DATA if the case cannot be resolved from the "
            "given records — this is a legitimate, scoreable answer, not a "
            "fallback for uncertainty."
        ),
    )

    corrected_rate: Optional[float] = Field(
        None, description="The model's computed correct rate, if determinable."
    )

    corrected_amount: Optional[float] = Field(
        None, description="The model's computed correct dollar amount, if determinable."
    )

    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Model's stated confidence in its own root_cause classification. "
            "New field vs. D-007 — required for calibration scoring "
            "(Brier-score-style: is confidence justified by accuracy)."
        ),
    )

    rationale: str = Field(
        ..., description="Free-text explanation, not scored directly but retained for audit."
    )


# ---------------------------------------------------------------------------
# Ground truth — generator-side, never shown to the model
# ---------------------------------------------------------------------------

class GroundTruth(BaseModel):
    case_id: str
    correct_discrepancy_detected: bool
    correct_root_cause: RootCauseCategory
    correct_rate: Optional[float] = None
    correct_amount: Optional[float] = None

    boundary_distance: Optional[float] = Field(
        None,
        description=(
            "Distance from the dispute-tolerance threshold, in the same "
            "units as the rate/amount. New field vs. D-007 — enables "
            "near-boundary stratification without a schema migration later. "
            "None for cases far from any threshold."
        ),
    )

    # Validation trail — filled in during the mandatory pre-publication
    # check, NOT derived from the scorer. See spec: "Ground-truth-vs-source-
    # data check, independent of the scorer."
    manually_verified_against_source: bool = Field(
        default=False,
        description=(
            "Must be True before this case enters the published roster. "
            "Set by a human reviewing the underlying trade records "
            "directly, not by the scorer agreeing with itself."
        ),
    )


# ---------------------------------------------------------------------------
# Per-case score record — one row of eval_results_d008_*.json
# ---------------------------------------------------------------------------

class CaseScore(BaseModel):
    case_id: str
    model_id: str
    run_number: int                                # 1, 2, or 3 — for flip-rate calc

    detection_correct: bool
    attribution_correct: bool
    value_correct: Optional[bool] = None           # None if not applicable (e.g. NO_DISPUTE)
    false_positive_on_trap: bool = False

    # Calibration inputs
    stated_confidence: float
    was_correct: bool                              # for Brier score: (confidence - was_correct)^2

    # Cost/latency, tracked from v0.1 (not retrofitted, per corpus-wide metrics)
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ModelCaseHistory(BaseModel):
    """All runs of one model against one case — the unit flip rate is
    computed from. Reported from v1.0, not added retroactively."""
    case_id: str
    model_id: str
    runs: list[CaseScore]

    @property
    def flipped(self) -> bool:
        """True if root_cause (or discrepancy_detected) differs across runs
        on the identical case. This is the metric D-007 pooled away and
        had to recover retroactively — build it in from the start."""
        distinct_causes = {r.attribution_correct for r in self.runs}
        return len(distinct_causes) > 1
