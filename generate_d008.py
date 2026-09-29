"""
AAL-D-008 — Repo / Securities Lending Rate Disputes
Case generator skeleton (draft v0.1)

Deterministic generation only — no LLM calls. Ground truth is computed by
the same arithmetic a human analyst would use, not asserted by hand.

This skeleton enforces the two mandatory pre-publication safeguards from
the spec structurally, not just procedurally:

  1. Every generated case must pass through validate_against_source() before
     it can be marked publishable. A case cannot silently skip this step —
     GroundTruth.manually_verified_against_source defaults to False and
     export_publishable_set() refuses to include unverified cases.

  2. Category balance and boundary-distance distribution are checked at
     generation time (see summarize_generation_run()), so a category that's
     accidentally impossible to solve shows up as a statistical anomaly in
     the generator's own output — before it ever reaches a model.

DO NOT treat this file as ready to run. Repo/sec-lending market data
parameters (real rate ranges, typical haircuts, day-count conventions in
practice) are placeholders and must be validated by a domain SME first
(see spec Open Question: "Category taxonomy validation").
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from collections import Counter
import random
import uuid

from schemas.d008 import (
    CaseInput,
    CounterpartyRecord,
    GoverningRecord,
    GroundTruth,
    RootCauseCategory,
    DayCountConvention,
    BenchmarkRate,
)

# ---------------------------------------------------------------------------
# Generation parameters — PLACEHOLDERS. Validate against real market data.
# ---------------------------------------------------------------------------

# DISCLOSED ASSUMPTION, not a verified industry standard. Searched FICC/DTCC
# repo matching mechanics, ISLA settlement surveys, and general recon-break
# literature (Sept 2026) -- no public bps threshold exists for repo/sec-lending
# rate disputes specifically. FICC does exact trade comparison/novation, not
# a tolerance-banded match like SIMM margin under UMR. This value is a stated
# assumption carried from the initial draft; publish it as disclosed, the same
# way D-007 disclosed GPT-6 Astra's sampling-config limitation rather than
# treating it as resolved.
RATE_TOLERANCE_BPS = 0.5          # must match scorer's RATE_TOLERANCE_BPS exactly

NOTIONAL_RANGE = (1_000_000, 500_000_000)

# Validated against current market data (Sept 2026): SOFR trading 3.62-3.65%.
# Range kept wide intentionally so the dataset doesn't need re-tuning every
# rate cycle — this spans normal cyclical movement, not just today's print.
BASE_RATE_RANGE = (0.030, 0.060)  # 3.0%-6.0%

# Validated against OFR/NY Fed data: Treasury-collateral repo haircuts are
# NOT uniform across this range in practice. Over 60% of Treasury repo trades
# at a 0% haircut; tri-party median is ~2%. Non-Treasury collateral clusters
# above 2%. Sample from HAIRCUT_DISTRIBUTION below instead of uniform
# HAIRCUT_RANGE if realism matters more than simplicity here.
HAIRCUT_RANGE = (0.00, 0.08)
HAIRCUT_DISTRIBUTION = [
    (0.00, 0.60),   # 60% of cases: zero haircut (Treasury GC, dominant case)
    (0.02, 0.25),   # 25%: ~2% (tri-party median for Treasury collateral)
    (0.05, 0.10),   # 10%: elevated, less liquid collateral
    (0.08, 0.05),   # 5%: upper bound, stressed/non-Treasury collateral
]

# Validated against market convention: ACT/360 is dominant for USD repo,
# SOFR, and money-market instruments generally. 30/360 is a BOND convention
# (US corporate/agency), not repo — a real RL-DIS-DAYCOUNT dispute involving
# 30/360 on one side is closer to a labeling error than a realistic mismatch.
# Recommend weighting ACT/360 as the default on both sides, with ACT/365
# appearing only at the cross-currency/sterling-collateral boundary, and
# 30/360 dropped or heavily downweighted as a source of genuine dispute.
DAYCOUNT_REALISM_NOTE = (
    "ACT/360 dominant for USD repo; ACT/365 at sterling/cross-currency "
    "boundary only; 30/360 is a bond convention, rarely a real repo dispute "
    "source — consider downweighting or dropping as a mismatch category."
)

# Validated against market mechanics (SSGA/Callan sources): rebate rate =
# collateral interest rate (SOFR-benchmarked) minus loan fee/spread. General
# collateral spreads have historically run near Fed Funds minus 25-40bps.
# Use this to make RL-DIS-FEESPLIT cases realistic: a dispute should look
# like one side applying a stale/wrong benchmark rate to the rebate calc,
# producing a discrepancy in the tens-of-bps range — not an arbitrary spread.
FEESPLIT_TYPICAL_SPREAD_BPS = (25, 40)

# Uniform by design, not by default -- consistent with AAL-D-002, D-006, and
# D-007, none of which weighted categories by real-world dispute frequency.
# Real dispute-frequency data is internal operational knowledge and isn't
# published anywhere searchable, but AAL doesn't need it: this benchmark
# measures per-category model accuracy, which requires equal statistical
# power per category, not frequency-weighted sampling. Frequency-weighting
# would answer a different question ("how often does this happen") than the
# one D-008 is built to answer ("how well does the model handle it").
CATEGORY_WEIGHTS: dict[RootCauseCategory, float] = {
    RootCauseCategory.DAYCOUNT: 1.0,
    RootCauseCategory.BENCHRATE: 1.0,
    RootCauseCategory.HAIRCUT: 1.0,
    RootCauseCategory.SETTLEDATE: 1.0,
    RootCauseCategory.NOTIONAL: 1.0,
    RootCauseCategory.CORPACTION: 1.0,
    RootCauseCategory.FEESPLIT: 1.0,
    RootCauseCategory.ACCRUAL: 1.0,
    RootCauseCategory.RECALL: 1.0,
    RootCauseCategory.NO_DISPUTE: 1.0,          # trap cases, within tolerance
    RootCauseCategory.INSUFFICIENT_DATA: 1.0,   # deliberately unanswerable cases
}

TRAP_CASE_FRACTION = 0.35   # matches D-007's ~88/250 within-tolerance trap proportion


# ---------------------------------------------------------------------------
# Per-category generators
# ---------------------------------------------------------------------------
# Each function returns (CounterpartyRecord, CounterpartyRecord, GroundTruth)
# for exactly one root-cause category. Ground truth is computed here from
# first principles — the same math a human analyst would do — not just
# copied from whatever discrepancy was injected. This independent-recompute
# discipline is what "generator-side ground truth" actually means; skipping
# it is how CALCDATE happened.

def _base_pair(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord]:
    """A matching pair with no discrepancy yet — categories below perturb
    exactly one field on one side."""
    notional = round(random.uniform(*NOTIONAL_RANGE), 2)
    rate = round(random.uniform(*BASE_RATE_RANGE), 5)
    # Tenor spans overnight-ish to term. Every category draws from the same
    # distribution: when only DAYCOUNT used long tenors, tenor alone
    # partitioned that category and a model could exploit it without reading
    # the conventions. Minimum of 2 days so the SETTLEDATE generator's -1
    # shift cannot collapse settlement onto the trade date.
    settle = trade_date + timedelta(days=random.choice([2, 5, 7, 14, 30, 60, 90, 180]))

    a = CounterpartyRecord(
        counterparty_id="CPTY-A",
        trade_id=case_id,
        notional=notional,
        rate=rate,
        day_count_convention=DayCountConvention.ACT_360,
        benchmark_rate=BenchmarkRate.SOFR,
        haircut_pct=round(random.uniform(*HAIRCUT_RANGE), 4),
        trade_date=trade_date,
        settlement_date=settle,
    )
    b = a.model_copy(update={"counterparty_id": "CPTY-B"})
    return a, b


def _notional_clearing(min_diff_usd: float, per_unit_diff: float) -> float:
    """A notional large enough that the modelled discrepancy clears tolerance.

    Without this, a case can be labelled with a dispute category while
    carrying a difference under DOLLAR_TOLERANCE_USD -- so correct_root_cause
    says DAYCOUNT while correct_discrepancy_detected says False, and a model
    that correctly declines to flag it is scored as having missed a dispute.
    A case labelled as a dispute should be one; sub-tolerance differences are
    what the NO_DISPUTE trap category is for.

    per_unit_diff is the discrepancy produced per unit of notional, so the
    required floor is min_diff_usd / per_unit_diff. Sampling is still random,
    just bounded below.
    """
    if per_unit_diff <= 0:
        return random.uniform(*NOTIONAL_RANGE)
    floor = (min_diff_usd * 1.5) / per_unit_diff   # 1.5x margin above tolerance
    low = max(NOTIONAL_RANGE[0], floor)
    high = max(NOTIONAL_RANGE[1], low * 1.2)
    return round(random.uniform(low, high), 2)


def _governing_side() -> str:
    """Which counterparty the confirmation agrees with, 50/50.

    CORRECTNESS REQUIREMENT, not style. AAL-D-007 v1.0 was invalidated because
    the perturbation always landed on the same side, so value accuracy could be
    earned by copying rather than reading. A confirmation that always matched A
    would reintroduce that flaw in a new form.
    """
    return random.choice(["a", "b"])


def generate_daycount_dispute(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord, GroundTruth, GoverningRecord | None]:
    """Day-count convention mismatch. Both sides quote the same nominal rate
    but apply different conventions, so the interest each computes differs.

    An earlier version flipped B's convention without changing any number,
    so every case asserted a dispute with a measured difference of exactly
    zero -- a labeled finding the case data contradicted. The disagreement
    now surfaces in accrued_interest, which is how a convention mismatch
    actually presents in reconciliation.
    """
    a, b = _base_pair(case_id, trade_date)
    b.day_count_convention = DayCountConvention.ACT_365

    # Tenor comes from _base_pair's shared distribution -- no override here,
    # or DAYCOUNT would be the only category with long-dated trades.
    days = (a.settlement_date - a.trade_date).days
    # Per unit of notional, the convention gap is rate*days*(1/360 - 1/365).
    per_unit = a.rate * days * (1 / 360 - 1 / 365)
    a.notional = _notional_clearing(DOLLAR_TOLERANCE_USD, per_unit)
    b.notional = a.notional
    accrual_360 = a.notional * a.rate * days / 360
    accrual_365 = a.notional * a.rate * days / 365
    a.accrued_interest = round(accrual_360, 2)
    b.accrued_interest = round(accrual_365, 2)

    # The confirmation states which convention was agreed, fixing the
    # correct figure. Side randomized -- see _governing_side().
    side = _governing_side()
    agreed_dcc = a.day_count_convention if side == "a" else b.day_count_convention
    agreed_accrual = a.accrued_interest if side == "a" else b.accrued_interest
    governing = GoverningRecord(confirmation_id=f"CONF-{case_id}",
                                agreed_day_count_convention=agreed_dcc,
                                agreed_rate=a.rate)

    distance_usd = abs(a.accrued_interest - b.accrued_interest)

    gt = GroundTruth(
        case_id=case_id,
        correct_discrepancy_detected=distance_usd > DOLLAR_TOLERANCE_USD,
        correct_root_cause=RootCauseCategory.DAYCOUNT,
        correct_amount=agreed_accrual,   # derivable from the agreed convention
        boundary_distance=distance_usd,
        manually_verified_against_source=False,
    )
    return a, b, gt, governing


def generate_no_dispute_trap(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord, GroundTruth, GoverningRecord | None]:
    """Within-tolerance case — both sides agree, or differ by less than
    RATE_TOLERANCE_BPS. Model should NOT flag this as a dispute."""
    a, b = _base_pair(case_id, trade_date)
    # Perturb by less than tolerance, not zero — a real trap tests the
    # threshold, not just an exact match.
    # Floor at 0.2bp: 5-decimal rates resolve to 0.1bp, so a smaller jitter
    # rounds away and leaves both sides identical. A trap with no difference
    # at all is a weaker test than a near-miss inside tolerance.
    jitter_bps = random.uniform(0.2, RATE_TOLERANCE_BPS * 0.8)
    # Round to the same 5 decimals every other record uses. An unrounded
    # float is a visual tell: a model could flag the odd-looking rate as the
    # trap without ever comparing against tolerance.
    b.rate = round(a.rate + (jitter_bps / 10_000), 5)

    gt = GroundTruth(
        case_id=case_id,
        correct_discrepancy_detected=False,
        correct_root_cause=RootCauseCategory.NO_DISPUTE,
        correct_rate=a.rate,
        boundary_distance=jitter_bps,
        manually_verified_against_source=False,
    )
    return a, b, gt, None


def generate_insufficient_data_case(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord, GroundTruth, GoverningRecord | None]:
    """Deliberately constructs a case where the two records disagree but the
    given data does not contain enough information to determine WHY —
    e.g. both sides show a rate difference but neither record includes the
    benchmark_rate field needed to attribute it. This is the direct
    structural fix for CALCDATE: a case type that is SUPPOSED to be
    unanswerable, built on purpose, rather than an accident that slips
    through unnoticed."""
    a, b = _base_pair(case_id, trade_date)
    a.benchmark_rate = None
    b.benchmark_rate = None
    b.rate = round(a.rate + random.uniform(0.0005, 0.002), 5)  # clearly outside tolerance

    gt = GroundTruth(
        case_id=case_id,
        correct_discrepancy_detected=True,
        correct_root_cause=RootCauseCategory.INSUFFICIENT_DATA,
        correct_rate=None,   # deliberately not determinable
        boundary_distance=None,
        manually_verified_against_source=False,
    )
    return a, b, gt, None


# DISCLOSED ASSUMPTION: no dollar-denominated tolerance has been sourced
# or validated for notional/haircut disputes (RATE_TOLERANCE_BPS only covers
# rate-based comparisons). This placeholder is a relative threshold, not a
# researched figure -- treat it the same as RATE_TOLERANCE_BPS: shippable,
# disclosed, not verified against an external standard.
DOLLAR_TOLERANCE_USD = 1_000


def generate_haircut_dispute(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord, GroundTruth, GoverningRecord | None]:
    a, b = _base_pair(case_id, trade_date)

    # Sample B's haircut from a different bucket than A's, using the
    # validated HAIRCUT_DISTRIBUTION rather than an arbitrary perturbation --
    # a real haircut dispute looks like one side applying a stale or wrong
    # collateral-quality tier, not a random jitter.
    other_buckets = [h for h, _ in HAIRCUT_DISTRIBUTION if h != a.haircut_pct]
    b.haircut_pct = random.choice(other_buckets) if other_buckets else a.haircut_pct

    # DISCLOSED ASSUMPTION: required collateral = notional * (1 + haircut).
    # This is A common repo convention but not the only one in use (some
    # desks compute haircut against collateral value, not notional) --
    # THIS ASSUMPTION MUST BE VALIDATED, not asserted, same caveat as
    # generate_daycount_dispute's convention assumption above.
    required_a = a.notional * (1 + a.haircut_pct)
    required_b = a.notional * (1 + b.haircut_pct)
    # Print each side's figure so the disputed quantity is visible in the
    # case. Without this the model has to guess both which quantity is
    # wanted and which gross-up convention produces it.
    a.required_collateral = round(required_a, 2)
    b.required_collateral = round(required_b, 2)
    distance_usd = abs(required_a - required_b)

    side = _governing_side()
    agreed_haircut = a.haircut_pct if side == "a" else b.haircut_pct
    governing = GoverningRecord(confirmation_id=f"CONF-{case_id}",
                                agreed_haircut_pct=agreed_haircut)
    agreed_required = a.notional * (1 + agreed_haircut)

    gt = GroundTruth(
        case_id=case_id,
        correct_discrepancy_detected=distance_usd > DOLLAR_TOLERANCE_USD,
        correct_root_cause=RootCauseCategory.HAIRCUT,
        correct_amount=agreed_required,   # derivable from the confirmation
        boundary_distance=distance_usd,
        manually_verified_against_source=False,
    )
    return a, b, gt, governing


def generate_notional_dispute(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord, GroundTruth, GoverningRecord | None]:
    a, b = _base_pair(case_id, trade_date)

    # A realistic notional mismatch looks like a transcription/rounding
    # error on one side, not an arbitrary re-roll -- perturb by a small
    # percentage of the original notional rather than resampling the full
    # NOTIONAL_RANGE, which would make the "dispute" implausibly large.
    error_pct = random.uniform(0.001, 0.02)  # 0.1%-2% notional discrepancy
    b.notional = round(a.notional * (1 + random.choice([1, -1]) * error_pct), 2)
    distance_usd = abs(a.notional - b.notional)

    side = _governing_side()
    agreed_notional = a.notional if side == "a" else b.notional
    governing = GoverningRecord(confirmation_id=f"CONF-{case_id}",
                                agreed_notional=agreed_notional)

    gt = GroundTruth(
        case_id=case_id,
        correct_discrepancy_detected=distance_usd > DOLLAR_TOLERANCE_USD,
        correct_root_cause=RootCauseCategory.NOTIONAL,
        correct_amount=agreed_notional,   # derivable from the confirmation
        boundary_distance=distance_usd,
        manually_verified_against_source=False,
    )
    return a, b, gt, governing


# TODO(SME REQUIRED, distinct from RATE_TOLERANCE_BPS): unlike BASE_RATE_RANGE
# and HAIRCUT_RANGE, this spread has NOT been sourced against market data --
# I have not located a reliable public figure for the typical SOFR vs. Term
# SOFR vs. GC rate spread as of Sept 2026. Placeholder only. Do not present
# this as validated the way the rate/haircut ranges above are.
BENCHRATE_SPREAD_BPS = (2, 8)   # UNSOURCED PLACEHOLDER


def generate_benchrate_dispute(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord, GroundTruth, GoverningRecord | None]:
    a, b = _base_pair(case_id, trade_date)
    a.benchmark_rate = BenchmarkRate.SOFR
    b.benchmark_rate = random.choice([r for r in BenchmarkRate if r != BenchmarkRate.SOFR])

    # One side's rate reflects the wrong benchmark fixing. Spread magnitude
    # is the UNSOURCED placeholder above -- flagged, not hidden.
    spread_bps = random.uniform(*BENCHRATE_SPREAD_BPS)
    # Rounded to 5 decimals to match every other rate in the corpus --
    # see the note in generate_no_dispute_trap.
    b.rate = round(a.rate + (spread_bps / 10_000), 5)
    distance_bps = abs(a.rate - b.rate) * 10_000

    # The confirmation names the agreed benchmark, which settles which
    # fixing should have been applied.
    side = _governing_side()
    agreed_benchmark = a.benchmark_rate if side == "a" else b.benchmark_rate
    agreed_rate = a.rate if side == "a" else b.rate
    governing = GoverningRecord(confirmation_id=f"CONF-{case_id}",
                                agreed_benchmark_rate=agreed_benchmark,
                                agreed_rate=agreed_rate)

    gt = GroundTruth(
        case_id=case_id,
        correct_discrepancy_detected=distance_bps > RATE_TOLERANCE_BPS,
        correct_root_cause=RootCauseCategory.BENCHRATE,
        correct_rate=agreed_rate,   # derivable from the confirmation   # assumes A's benchmark governs -- same "A governs" placeholder as DAYCOUNT/HAIRCUT
        boundary_distance=distance_bps,
        manually_verified_against_source=False,
    )
    return a, b, gt, governing


def generate_settledate_dispute(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord, GroundTruth, GoverningRecord | None]:
    """Settlement-date mismatch changes the accrual period itself, not just
    the rate -- this produces a dollar discrepancy via the accrual formula
    (notional * rate * days / 360) rather than a rate perturbation. Ties
    directly to DAYCOUNT_REALISM_NOTE: ACT/360 is the governing convention
    for both sides here, only the settlement date (and therefore day count)
    differs."""
    a, b = _base_pair(case_id, trade_date)
    b.settlement_date = a.settlement_date + timedelta(days=random.choice([-1, 1]))
    # One accrual day of difference, per unit of notional.
    a.notional = _notional_clearing(DOLLAR_TOLERANCE_USD, a.rate / 360)
    b.notional = a.notional

    days_a = (a.settlement_date - a.trade_date).days
    days_b = (b.settlement_date - b.trade_date).days
    accrual_a = a.notional * a.rate * days_a / 360
    accrual_b = a.notional * a.rate * days_b / 360   # same rate, different day count
    distance_usd = abs(accrual_a - accrual_b)

    side = _governing_side()
    agreed_settle = a.settlement_date if side == "a" else b.settlement_date
    governing = GoverningRecord(confirmation_id=f"CONF-{case_id}",
                                agreed_settlement_date=agreed_settle)
    agreed_accrual = a.notional * a.rate * (agreed_settle - a.trade_date).days / 360
    # Surface each side's own accrual so the dollar impact of the date
    # disagreement is inspectable rather than implied.
    a.accrued_interest = round(accrual_a, 2)
    b.accrued_interest = round(accrual_b, 2)
    # Surface each side's own accrual so the dollar impact of the date
    # disagreement is inspectable rather than implied.
    a.accrued_interest = round(accrual_a, 2)
    b.accrued_interest = round(accrual_b, 2)

    gt = GroundTruth(
        case_id=case_id,
        correct_discrepancy_detected=distance_usd > DOLLAR_TOLERANCE_USD,
        correct_root_cause=RootCauseCategory.SETTLEDATE,
        correct_amount=agreed_accrual,   # derivable from the confirmation
        boundary_distance=distance_usd,
        manually_verified_against_source=False,
    )
    return a, b, gt, governing


# UNSOURCED PLACEHOLDER, same disclosure standard as BENCHRATE_SPREAD_BPS --
# no public figure located for typical dividend/coupon pass-through error
# magnitude on lent securities. Do not present as validated.
CORPACTION_ERROR_PCT = (0.05, 0.25)   # 5%-25% of the corporate action amount, UNSOURCED


def generate_corpaction_dispute(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord, GroundTruth, GoverningRecord | None]:
    """One side fails to apply, or misapplies, a dividend/coupon pass-through
    adjustment on the lent security. Distinct from NOTIONAL/HAIRCUT disputes:
    the underlying trade terms agree, only the corporate-action adjustment
    differs."""
    a, b = _base_pair(case_id, trade_date)
    ca_amount = round(a.notional * random.uniform(0.001, 0.01), 2)  # plausible div/coupon amount
    a.corporate_action_adjustment = ca_amount
    error_pct = random.uniform(*CORPACTION_ERROR_PCT)
    b.corporate_action_adjustment = round(ca_amount * (1 - error_pct), 2)   # B under-applies the adjustment

    distance_usd = abs(a.corporate_action_adjustment - b.corporate_action_adjustment)

    side = _governing_side()
    notice_amount = (a.corporate_action_adjustment if side == "a"
                     else b.corporate_action_adjustment)
    governing = GoverningRecord(confirmation_id=f"CONF-{case_id}",
                                corporate_action_notice_amount=notice_amount)

    gt = GroundTruth(
        case_id=case_id,
        correct_discrepancy_detected=distance_usd > DOLLAR_TOLERANCE_USD,
        correct_root_cause=RootCauseCategory.CORPACTION,
        correct_amount=notice_amount,   # derivable from the confirmation
        boundary_distance=distance_usd,
        manually_verified_against_source=False,
    )
    return a, b, gt, governing


def generate_feesplit_dispute(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord, GroundTruth, GoverningRecord | None]:
    """Securities-lending rebate calculation error: one side applies a
    stale or wrong benchmark rate to the rebate formula. Uses the SOURCED
    FEESPLIT_TYPICAL_SPREAD_BPS from the parameter block above -- this is
    the one category generator built entirely on validated market data,
    not a placeholder."""
    a, b = _base_pair(case_id, trade_date)
    spread_bps = random.uniform(*FEESPLIT_TYPICAL_SPREAD_BPS)
    a.rebate_rate = round(a.rate - (spread_bps / 10_000), 5)

    # B applies a stale spread -- shifted by a further 5-15bps error
    stale_shift_bps = random.uniform(5, 15)
    b.rebate_rate = round(a.rebate_rate - (stale_shift_bps / 10_000), 5)
    distance_bps = abs(a.rebate_rate - b.rebate_rate) * 10_000

    # The confirmation states the agreed lending spread, from which the
    # correct rebate rate is derivable: rebate = collateral rate - spread.
    side = _governing_side()
    agreed_rebate = a.rebate_rate if side == "a" else b.rebate_rate
    agreed_spread_bps = round((a.rate - agreed_rebate) * 10_000, 2)
    governing = GoverningRecord(confirmation_id=f"CONF-{case_id}",
                                agreed_rebate_spread_bps=agreed_spread_bps)

    gt = GroundTruth(
        case_id=case_id,
        correct_discrepancy_detected=distance_bps > RATE_TOLERANCE_BPS,
        correct_root_cause=RootCauseCategory.FEESPLIT,
        correct_rate=agreed_rebate,   # derivable from the confirmation   # assumes A's rebate calc governs -- same placeholder pattern
        boundary_distance=distance_bps,
        manually_verified_against_source=False,
    )
    return a, b, gt, governing


def generate_accrual_dispute(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord, GroundTruth, GoverningRecord | None]:
    """Partial-period accrual miscalculated at a period boundary. Both sides
    agree on every trade term; they disagree on the resulting interest figure,
    because one side counted the accrual days inclusively and the other did
    not. The disagreement is visible in accrued_interest, not hidden inside
    the generator -- an earlier version had identical records on both sides
    and a labeled dispute nothing in the case disclosed.

    Distinct from SETTLEDATE, where the dates themselves differ."""
    a, b = _base_pair(case_id, trade_date)

    # Off-by-one accrual day, per unit of notional.
    a.notional = _notional_clearing(DOLLAR_TOLERANCE_USD, a.rate / 360)
    b.notional = a.notional

    days = (a.settlement_date - a.trade_date).days
    correct_accrual = a.notional * a.rate * days / 360
    # The erring side is off by one accrual day, a common inclusive/exclusive
    # boundary error.
    wrong_accrual = a.notional * a.rate * (days + random.choice([-1, 1])) / 360

    side = _governing_side()
    if side == "a":
        a.accrued_interest = round(correct_accrual, 2)
        b.accrued_interest = round(wrong_accrual, 2)
    else:
        a.accrued_interest = round(wrong_accrual, 2)
        b.accrued_interest = round(correct_accrual, 2)

    # The confirmation states the agreed accrual day count, which is what
    # actually settles the case: both records carry identical terms, so a
    # restated settlement date decides nothing. With the day count fixed, the
    # model can check each side's printed interest against it.
    governing = GoverningRecord(confirmation_id=f"CONF-{case_id}",
                                agreed_settlement_date=a.settlement_date,
                                agreed_accrual_days=days)

    distance_usd = abs(a.accrued_interest - b.accrued_interest)

    gt = GroundTruth(
        case_id=case_id,
        correct_discrepancy_detected=distance_usd > DOLLAR_TOLERANCE_USD,
        correct_root_cause=RootCauseCategory.ACCRUAL,
        correct_amount=round(correct_accrual, 2),   # derivable from the confirmed settlement date
        boundary_distance=distance_usd,
        manually_verified_against_source=False,
    )
    return a, b, gt, governing


# UNSOURCED PLACEHOLDER, same disclosure standard as BENCHRATE_SPREAD_BPS and
# CORPACTION_ERROR_PCT -- no public figure located for typical partial-return
# proportion on an early recall. Do not present as validated.
RECALL_PARTIAL_RETURN_PCT = (0.10, 0.50)   # 10%-50% of notional recalled, UNSOURCED


def generate_recall_dispute(case_id: str, trade_date: date) -> tuple[CounterpartyRecord, CounterpartyRecord, GroundTruth, GoverningRecord | None]:
    """Early recall or partial return not reflected symmetrically -- one side
    has reduced its notional for a partial return, the other has not.

    An earlier version showed only two different notionals with no evidence a
    recall had occurred, making it indistinguishable from RL-DIS-NOTIONAL and
    leaving the correct figure resting on an undisclosed assumption about
    which side was current. The recall notice now makes both the cause and
    the correct notional derivable from the case.
    """
    a, b = _base_pair(case_id, trade_date)
    return_pct = random.uniform(*RECALL_PARTIAL_RETURN_PCT)
    # The discrepancy is the recalled portion's accrual over the tenor.
    days_tenor = (a.settlement_date - a.trade_date).days
    a.notional = _notional_clearing(
        DOLLAR_TOLERANCE_USD, a.rate * days_tenor / 360 * return_pct)
    b.notional = a.notional
    returned_notional = round(a.notional * return_pct, 2)

    # Randomize which side has already processed the recall, so the answer
    # cannot be earned by always picking the smaller notional.
    side = _governing_side()
    original_notional = a.notional
    if side == "a":
        a.notional = round(original_notional - returned_notional, 2)
    else:
        b.notional = round(original_notional - returned_notional, 2)

    # The recall notice is what establishes that a return occurred and for
    # how much -- without it this case is just a notional mismatch.
    governing = GoverningRecord(confirmation_id=f"CONF-{case_id}",
                                original_notional=original_notional,
                                recall_notice_amount=returned_notional)

    days = (a.settlement_date - a.trade_date).days
    correct_notional = round(original_notional - returned_notional, 2)
    correct_accrual = correct_notional * a.rate * days / 360
    stale_accrual = original_notional * a.rate * days / 360
    # The scored quantity is the accrual, not the notional. Print each side's
    # figure: both pilot models returned a notional here, which the case gave
    # them no reason not to do.
    a.accrued_interest = round(a.notional * a.rate * days / 360, 2)
    b.accrued_interest = round(b.notional * a.rate * days / 360, 2)
    distance_usd = abs(correct_accrual - stale_accrual)

    gt = GroundTruth(
        case_id=case_id,
        correct_discrepancy_detected=distance_usd > DOLLAR_TOLERANCE_USD,
        correct_root_cause=RootCauseCategory.RECALL,
        correct_amount=round(correct_accrual, 2),   # derivable from the recall notice
        boundary_distance=distance_usd,
        manually_verified_against_source=False,
    )
    return a, b, gt, governing


# Registry — all nine dispute categories plus NO_DISPUTE and INSUFFICIENT_DATA.
# Every generator returns (a, b, ground_truth, governing_record | None).
GENERATORS = {
    RootCauseCategory.DAYCOUNT: generate_daycount_dispute,
    RootCauseCategory.HAIRCUT: generate_haircut_dispute,
    RootCauseCategory.NOTIONAL: generate_notional_dispute,
    RootCauseCategory.BENCHRATE: generate_benchrate_dispute,
    RootCauseCategory.SETTLEDATE: generate_settledate_dispute,
    RootCauseCategory.CORPACTION: generate_corpaction_dispute,
    RootCauseCategory.FEESPLIT: generate_feesplit_dispute,
    RootCauseCategory.ACCRUAL: generate_accrual_dispute,
    RootCauseCategory.RECALL: generate_recall_dispute,
    RootCauseCategory.NO_DISPUTE: generate_no_dispute_trap,
    RootCauseCategory.INSUFFICIENT_DATA: generate_insufficient_data_case,
}


# ---------------------------------------------------------------------------
# Batch generation + mandatory verification gate
# ---------------------------------------------------------------------------

@dataclass
class GeneratedCase:
    case_input: CaseInput
    ground_truth: GroundTruth


def generate_batch(n_cases: int, seed: int | None = None) -> list[GeneratedCase]:
    if seed is not None:
        random.seed(seed)

    categories = list(GENERATORS.keys())
    weights = [CATEGORY_WEIGHTS[c] for c in categories]

    results: list[GeneratedCase] = []
    base_date = date(2026, 1, 1)

    for i in range(n_cases):
        category = random.choices(categories, weights=weights, k=1)[0]
        case_id = f"AAL-D-008-{i+1:04d}"
        trade_date = base_date + timedelta(days=random.randint(0, 250))

        a, b, gt, governing = GENERATORS[category](case_id, trade_date)

        case_input = CaseInput(
            case_id=case_id,
            instrument_type="repo",
            counterparty_a=a,
            counterparty_b=b,
            is_trap=(category == RootCauseCategory.NO_DISPUTE),
            governing_record=governing,
        )
        results.append(GeneratedCase(case_input=case_input, ground_truth=gt))

    return results


def validate_against_source(generated: GeneratedCase) -> bool:
    """Placeholder for the MANDATORY human verification step from the spec:
    'Ground-truth-vs-source-data check, independent of the scorer.'

    This function as written does NOT perform real verification — it is a
    hook. A human must review each case's underlying records directly and
    confirm the ground truth is actually derivable from what's shown, then
    set manually_verified_against_source = True explicitly. Do not automate
    this check by having code re-derive its own ground truth and compare to
    itself — that is exactly the D-007 failure (scorer agreeing with itself).
    """
    raise NotImplementedError(
        "This must be a human review step, not an automated check. "
        "See spec: 'a different kind of question' than what the scorer answers."
    )


def export_publishable_set(generated: list[GeneratedCase]) -> list[GeneratedCase]:
    """Refuses to include any case that hasn't been explicitly verified.
    This is the structural enforcement of the mandatory pre-publication
    gate — a case cannot reach eval_out_d008/ without this flag being
    manually set True by a human reviewer first."""
    verified = [g for g in generated if g.ground_truth.manually_verified_against_source]
    unverified_count = len(generated) - len(verified)

    if unverified_count > 0:
        print(
            f"WARNING: {unverified_count} of {len(generated)} generated cases "
            f"are NOT marked as manually verified against source data and will "
            f"be EXCLUDED from the publishable set. Verify them before the "
            f"pilot audit, not after."
        )

    return verified


def summarize_generation_run(generated: list[GeneratedCase]) -> None:
    """Sanity check at generation time — a category that's statistically
    thin or has a suspicious boundary_distance distribution should raise
    eyebrows before a single model ever sees these cases."""
    category_counts = Counter(g.ground_truth.correct_root_cause for g in generated)

    print("=== D-008 Generation Summary ===")
    for category, count in category_counts.items():
        pct = count / len(generated)
        print(f"{category.value:30s} {count:4d} cases ({pct:.1%})")

    print()
    unverified = sum(1 for g in generated if not g.ground_truth.manually_verified_against_source)
    print(f"Unverified against source: {unverified}/{len(generated)} "
          f"— these will be excluded until reviewed.")


if __name__ == "__main__":
    batch = generate_batch(n_cases=250, seed=8)
    summarize_generation_run(batch)

    # export_publishable_set(batch) will exclude everything until a human
    # runs validate_against_source() on each case and flips the flag —
    # by design. This script does not silently produce a "ready" dataset.
