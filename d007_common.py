"""
AAL-D-007 — SIMM/UMR Initial Margin Dispute Detection — generator core.

Same architecture as D-001..D-006: cases are generated deterministically with
ground truth *by construction*. The evaluated model classifies whether an IM
dispute exists and attributes root cause; it NEVER performs the margin
aggregation itself — every figure (bucket margins, risk-class margins, total
IM for both sides, the dollar and percentage difference) is computed here and
printed into the case's margin_breakdown.

METHODOLOGY NOTE (read before citing this dataset): this benchmark uses a
SIMM-STYLE margin methodology -- the same conceptual structure as ISDA's
published SIMM (sensitivity-based margin, weighted by risk weight, aggregated
within a bucket and across buckets via correlation parameters, summed across
risk classes). The risk weights and correlation parameters below are AAL's
own illustrative constants, NOT ISDA's licensed SIMM calibration, which is
published under separate license terms and is not reproduced here. The point
of the benchmark is whether a model can correctly read a prescribed
multi-step aggregation it is given and correctly attribute *why* two parties'
numbers diverge -- not whether it recalls ISDA's actual risk-weight table.
Every case prints the risk weights and correlations it uses; no external
methodology knowledge is required or assumed.

Task (per case): given a netting set's risk sensitivities as reported by the
firm and by the counterparty, plus the already-computed margin breakdown for
both sides, decide:
  - dispute_exists (bool) -- does the difference exceed the tolerance?
  - primary_dispute_category (one IM-DIS-* code) if disputed
  - offending_component (the specific risk class / bucket / factor at fault)
  - correct_im_amount / dispute_difference -- WHICH side's number is right
    (this varies case-by-case as of v1.1, see below)
  - should_escalate

Scope (v1.0/v1.1): 3 risk classes (RATES_FX, CREDIT, EQUITY) -- COMMODITY
deferred to a future extension. Small netting sets (2-6 sensitivities) so any
single perturbed factor carries a verifiable share of the total. 250 cases =
162 disputes + 88 traps. Dispute tolerance: 10% relative difference (a common
industry-referenced IM dispute-resolution threshold; exact thresholds vary by
CSA and jurisdiction -- this dataset does not claim to reproduce any specific
regulator's number).

v1.1 CHANGE (design fix, Aug 19 2026): v1.0 always injected the error into the
counterparty's copy of the sensitivities, so "correct_im_amount" was always
the firm's total, by construction, in every single case -- combined with a
prompt that told the model exactly that ("the firm's total IM, taken from the
printed margin_breakdown"), this made value_accuracy measure whether a model
can copy a labeled JSON field, not whether it can judge which side is right.
v1.1 fixes this at the generator level: build_master_plan() now assigns a
`perturb_side` ("firm" or "counterparty") to each case, balanced exactly
125/125 and shuffled independently of the category order, and render_case()
maps the side-agnostic base/perturbed sensitivities onto whichever physical
party that case calls for. "Always trust the firm" is now a ~50% strategy at
best; the model has to use the disclosed sensitivities, risk weights, and
methodology metadata to determine which side's printed total is trustworthy.
See oracle(), _inject_dispute(), _inject_trap(), render_case().

Stdlib only. Deterministic: every case seeded by its index.
"""
from __future__ import annotations

import copy
import datetime as _dt
import math
import random
from collections import Counter

RISK_CLASSES = ["RATES_FX", "CREDIT", "EQUITY"]
BUCKETS = {
    "RATES_FX": ["USD", "EUR", "JPY", "GBP", "OTHER"],
    "CREDIT": ["IG", "HY"],
    "EQUITY": ["LARGE_CAP", "SMALL_CAP", "EM"],
}

# Illustrative risk weights -- AAL's own synthetic constants, NOT ISDA's SIMM
# calibration. See module docstring.
RISK_WEIGHT = {
    "RATES_FX": {"USD": 65.0, "EUR": 60.0, "JPY": 55.0, "GBP": 62.0, "OTHER": 70.0},
    "CREDIT": {"IG": 75.0, "HY": 140.0},
    "EQUITY": {"LARGE_CAP": 25.0, "SMALL_CAP": 35.0, "EM": 40.0},
}
INTRA_BUCKET_CORR = 0.98      # between risk factors within the same bucket
CROSS_BUCKET_CORR = {"RATES_FX": 0.50, "CREDIT": 0.40, "EQUITY": 0.15}  # across buckets, same class

TOLERANCE_PCT = 10.0          # relative difference above which a dispute exists
CONC_THRESHOLD = 2_000_000.0  # single-bucket margin above this triggers the add-on
CONC_ADDON_PCT = 0.15         # proportional surcharge on any bucket over threshold
                               # (proportional, not flat -- so the surcharge stays a
                               # meaningful fraction of the total regardless of portfolio size)

CATEGORIES = [
    "IM-DIS-SENS", "IM-DIS-TRADEPOP", "IM-DIS-BUCKET", "IM-DIS-FX",
    "IM-DIS-CALCDATE", "IM-DIS-CRIF", "IM-DIS-METHODOLOGY",
    "IM-DIS-NETTING", "IM-DIS-CONCENTRATION",
]  # NOTE: order == oracle priority order used by the recompute check narrative

TRAP_TYPES = [
    "TRAP-CALCDATE-INBAND", "TRAP-BUCKET-BOTH-VALID", "TRAP-ROUNDING",
    "TRAP-FX-CONVENTION", "TRAP-NETTING-IMMATERIAL", "TRAP-METHODOLOGY-INBAND",
]

COUNTERPARTIES = ["Dealer Bank Alpha", "Dealer Bank Beta", "Dealer Bank Gamma",
                  "Dealer Bank Delta", "Prime Broker Epsilon"]

# The agreed as-of date both parties are reconciling to. Each side's ACTUAL
# calculation date is disclosed separately in methodology_metadata and may be
# earlier -- see the IM-DIS-CALCDATE notes in _inject_dispute.
RECONCILIATION_DATE = "2026-08-15"


def _shift_date(iso: str, days: int) -> str:
    """Shift an ISO date by `days` (negative = earlier). Stdlib only, to keep
    this module dependency-free like the rest of the generator."""
    return (_dt.date.fromisoformat(iso) + _dt.timedelta(days=days)).isoformat()


# ---- margin aggregation (the deterministic engine) --------------------------

def bucket_margin(values, rw):
    """SIMM-style bucket margin: risk-weighted sensitivities, aggregated with
    a flat intra-bucket correlation. `values` are raw sensitivities; `rw` is
    the bucket's risk weight."""
    ws = [rw * v for v in values]
    sumsq = sum(w * w for w in ws)
    cross = 0.0
    n = len(ws)
    for i in range(n):
        for j in range(n):
            if i != j:
                cross += INTRA_BUCKET_CORR * ws[i] * ws[j]
    return math.sqrt(max(sumsq + cross, 0.0))


def class_margin(bucket_margins, cross_corr):
    """Aggregate bucket margins within one risk class via cross-bucket correlation."""
    vals = list(bucket_margins.values())
    sumsq = sum(k * k for k in vals)
    cross = 0.0
    n = len(vals)
    for i in range(n):
        for j in range(n):
            if i != j:
                cross += cross_corr * vals[i] * vals[j]
    return math.sqrt(max(sumsq + cross, 0.0))


def aggregate(sens, rw_table=None, apply_conc_addon=False):
    """sens: {risk_class: {bucket: [(factor_id, value), ...]}}
    Returns (total_im, class_margins, bucket_margins, conc_addon_applied)."""
    rw_table = rw_table or RISK_WEIGHT
    bucket_margins, class_margins = {}, {}
    addon = 0.0
    for cls in RISK_CLASSES:
        buckets = sens.get(cls, {})
        bm = {}
        for bucket, factors in buckets.items():
            if not factors:
                continue
            values = [v for _, v in factors]
            m = bucket_margin(values, rw_table[cls][bucket])
            bm[bucket] = m
            if m > CONC_THRESHOLD and apply_conc_addon:
                addon += CONC_ADDON_PCT * m   # proportional surcharge, scales with the breach
        bucket_margins[cls] = bm
        class_margins[cls] = class_margin(bm, CROSS_BUCKET_CORR[cls]) if bm else 0.0
    total = sum(class_margins.values()) + addon
    return round(total, 2), {k: round(v, 2) for k, v in class_margins.items()}, \
        {k: {b: round(v, 2) for b, v in d.items()} for k, d in bucket_margins.items()}, addon > 0


def scaled_rw(scale):
    return {cls: {b: rw * scale for b, rw in buckets.items()} for cls, buckets in RISK_WEIGHT.items()}


# ---- portfolio construction --------------------------------------------------

def build_sensitivities(rng, n_classes=2, factors_per_class=(1, 2)):
    """A small, realistic netting set: n_classes risk classes in scope, 1-2
    buckets per class, 1-2 risk factors per bucket. Small on purpose so any
    single perturbed factor carries a verifiable share of the total."""
    classes = rng.sample(RISK_CLASSES, n_classes)
    sens = {cls: {} for cls in RISK_CLASSES}
    fid = 0
    for cls in classes:
        n_buckets = rng.choice([1, 2])
        buckets = rng.sample(BUCKETS[cls], min(n_buckets, len(BUCKETS[cls])))
        for b in buckets:
            n_factors = rng.randint(*factors_per_class)
            factors = []
            for _ in range(n_factors):
                fid += 1
                val = round(rng.uniform(400, 3200) * rng.choice([1, -1]), 1)
                factors.append((f"RF-{fid:03d}", val))
            sens[cls][b] = factors
    return sens


def _flatten(sens):
    out = []
    for cls, buckets in sens.items():
        for b, factors in buckets.items():
            for fid, v in factors:
                out.append((cls, b, fid, v))
    return out


def _scale_factor(sens, cls, bucket, fid, mult):
    sens[cls][bucket] = [(f, v * mult if f == fid else v) for f, v in sens[cls][bucket]]


def _remove_factor(sens, cls, bucket, fid):
    sens[cls][bucket] = [(f, v) for f, v in sens[cls][bucket] if f != fid]


def _dominant_factor(sens):
    """The single risk factor with the largest absolute sensitivity -- perturbing
    this one gives the most reliable, verifiable impact on the total."""
    flat = _flatten(sens)
    return max(flat, key=lambda t: abs(t[3]))


def _dominant_class(sens):
    """The risk class contributing the largest margin -- targeting this one
    guarantees a perturbation is not diluted by the rest of the portfolio."""
    _, class_margins, _, _ = aggregate(sens, RISK_WEIGHT, apply_conc_addon=False)
    return max(class_margins, key=lambda c: class_margins[c])


# ---- dispute / trap injection -----------------------------------------------
# `strength` scales how aggressive each perturbation is; render_case() retries
# with escalating strength if a draw doesn't clear (or stays under, for traps)
# the tolerance threshold, so every case reliably lands in its intended bucket
# regardless of the random portfolio it was dealt.
#
# `base` is the side that stays correct for this case -- i.e. the sensitivities
# that get printed, unmodified, as WHICHEVER physical party (firm or
# counterparty) render_case's perturb_side says is the correct one. These
# functions never see or decide that physical mapping; they just produce the
# perturbed copy and a comp string using __BASE__/__PERTURBED__ placeholders,
# which render_case fills in with the actual "firm"/"counterparty" labels.

def _inject_dispute(cat, rng, base, strength=1.0):
    perturbed = copy.deepcopy(base)
    cls, bucket, fid, val = _dominant_factor(base)

    if cat == "IM-DIS-SENS":
        mult = rng.choice([1.5, 1.6]) * strength if val >= 0 else 1.0 / (rng.choice([1.5, 1.6]) * strength)
        _scale_factor(perturbed, cls, bucket, fid, mult)
        comp = f"{cls}:{bucket}:{fid}"
        return perturbed, comp, None

    if cat == "IM-DIS-TRADEPOP":
        _remove_factor(perturbed, cls, bucket, fid)
        comp = f"{cls}:{bucket}:{fid} (missing from __PERTURBED__'s file)"
        return perturbed, comp, None

    if cat == "IM-DIS-BUCKET":
        other_buckets = [b for b in BUCKETS[cls] if b != bucket]
        new_bucket = max(other_buckets, key=lambda b: abs(RISK_WEIGHT[cls][b] - RISK_WEIGHT[cls][bucket]))
        _remove_factor(perturbed, cls, bucket, fid)
        perturbed[cls].setdefault(new_bucket, [])
        perturbed[cls][new_bucket] = perturbed[cls][new_bucket] + [(fid, val * strength)]
        comp = f"{cls}:{bucket}->{new_bucket}:{fid}"
        return perturbed, comp, None

    if cat == "IM-DIS-FX":
        target_cls = _dominant_class(base)
        mult = (1.0 + 0.22 * strength) * rng.choice([1, 1])
        if rng.random() < 0.5:
            mult = 1.0 / mult
        for b, factors in perturbed.get(target_cls, {}).items():
            perturbed[target_cls][b] = [(f, v * mult) for f, v in factors]
        comp = f"{target_cls} (FX conversion applied to all buckets)"
        return perturbed, comp, None

    if cat == "IM-DIS-CALCDATE":
        # v1.2 FIX. Through v1.1 this category scaled every sensitivity and
        # emitted NO date evidence -- the case carried a single shared
        # calculation_date, so "the perturbed side's calc is stale" was not
        # recoverable from anything the model could see. All nine models in the
        # published roster scored 0.0% on this category across 3 runs, and were
        # right to: a uniform sensitivity shift with matching metadata reads as
        # IM-DIS-SENS. Compare IM-DIS-METHODOLOGY, which applies a similar
        # uniform distortion but returns an ("rw_scale", ...) hook that surfaces
        # as a disclosed metadata difference -- and which every model scores
        # 100% on. The two-line difference was the whole gap.
        #
        # The hook below emits per-side calculation dates the same way. Lag
        # scales with strength so a staler calc moves the number further, which
        # is what makes the trap below coherent rather than a giveaway.
        mult = 1.0 + 0.18 * strength
        for c, buckets in perturbed.items():
            for b, factors in buckets.items():
                perturbed[c][b] = [(f, v * mult) for f, v in factors]
        comp = "ALL (__PERTURBED__'s calculation is stale vs. __BASE__'s calc date)"
        return perturbed, comp, ("calc_date_lag", rng.choice([3, 4, 5]))

    if cat == "IM-DIS-CRIF":
        _scale_factor(perturbed, cls, bucket, fid, -1.0)
        comp = f"{cls}:{bucket}:{fid} (sign flipped -- CRIF mapping error)"
        return perturbed, comp, None

    if cat == "IM-DIS-METHODOLOGY":
        comp = "ALL (__PERTURBED__ on a stale risk-weight calibration)"
        scale = 1.0 - min(0.6, 0.30 * strength) if rng.random() < 0.5 else 1.0 + 0.30 * strength
        return perturbed, comp, ("rw_scale", scale)

    if cat == "IM-DIS-NETTING":
        drop_cls = _dominant_class(base)
        perturbed[drop_cls] = {}
        comp = f"{drop_cls} (excluded entirely from __PERTURBED__'s netting set)"
        return perturbed, comp, None

    if cat == "IM-DIS-CONCENTRATION":
        # Inflate the dominant factor on BOTH sides identically (mutate base
        # in place before the copy) so the bucket margin clears the
        # concentration threshold; the only asymmetry is which side applies
        # the resulting add-on -- isolates the intended dispute mechanism.
        mult = 6.0 * strength
        _scale_factor(base, cls, bucket, fid, mult)
        perturbed = copy.deepcopy(base)
        comp = f"{cls}:{bucket} (concentration add-on applied by __BASE__ but not __PERTURBED__)"
        return perturbed, comp, ("addon_only_base", True)

    raise ValueError(cat)


def _inject_trap(trap, rng, base):
    perturbed = copy.deepcopy(base)
    flat = _flatten(base)
    cls, bucket, fid, val = rng.choice(flat)

    if trap == "TRAP-CALCDATE-INBAND":
        # v1.2: this trap MUST also carry differing calculation dates. Once the
        # IM-DIS-CALCDATE dispute emits them (see _inject_dispute), a case where
        # only disputes show two dates would let a model answer CALCDATE from
        # the presence of the field alone, without judging materiality -- the
        # opposite defect to the one being fixed. Here the dates differ by a
        # single day and the resulting move stays inside tolerance, so the model
        # still has to decide whether staleness is material rather than merely
        # present. That is the judgement the trap exists to test.
        mult = rng.choice([1.02, 1.03, 0.97, 0.98])
        for c, buckets in perturbed.items():
            for b, factors in buckets.items():
                perturbed[c][b] = [(f, v * mult) for f, v in factors]
        return perturbed, ("calc_date_lag", 1)

    if trap == "TRAP-BUCKET-BOTH-VALID":
        other_buckets = [b for b in BUCKETS[cls] if b != bucket]
        new_bucket = rng.choice(other_buckets)
        # small factor only -- moving it barely changes the total
        small_val = val * 0.15
        _remove_factor(perturbed, cls, bucket, fid)
        perturbed[cls][bucket] = perturbed[cls][bucket] + [(fid + "-adj", val - small_val)]
        perturbed[cls].setdefault(new_bucket, [])
        perturbed[cls][new_bucket] = perturbed[cls][new_bucket] + [(fid, small_val)]
        return perturbed, None

    if trap == "TRAP-ROUNDING":
        for c, buckets in perturbed.items():
            for b, factors in buckets.items():
                perturbed[c][b] = [(f, round(v * rng.uniform(0.99, 1.01), 1)) for f, v in factors]
        return perturbed, None

    if trap == "TRAP-FX-CONVENTION":
        target_cls = _dominant_class(base)
        mult = rng.choice([1.015, 1.02, 0.985, 0.98])
        for b, factors in perturbed.get(target_cls, {}).items():
            perturbed[target_cls][b] = [(f, v * mult) for f, v in factors]
        return perturbed, None

    if trap == "TRAP-NETTING-IMMATERIAL":
        # add one tiny extra factor to the perturbed side's file -- immaterial size
        perturbed[cls][bucket] = perturbed[cls][bucket] + [(fid + "-imm", rng.uniform(5, 25))]
        return perturbed, None

    if trap == "TRAP-METHODOLOGY-INBAND":
        return perturbed, ("rw_scale", rng.choice([0.97, 0.98, 1.02, 1.03]))

    raise ValueError(trap)


# ---- oracle -------------------------------------------------------------------

def oracle(base_sens, perturbed_sens, extra):
    """Independently derive the dispute verdict from the two sensitivity sets.
    `base_sens` is always the genuinely correct side for this case; `perturbed_sens`
    carries whatever error/trap was injected. Which PHYSICAL party (firm or
    counterparty) each one maps to is decided by render_case's perturb_side --
    oracle() itself is side-agnostic on purpose, so the "correct" answer is
    never structurally tied to one label.
    extra: ("rw_scale", x) | ("addon_only_base", True) | ("calc_date_lag", n)
    | None -- side conditions on the perturbed side. rw_scale changes which
    risk-weight table applies; addon_only_base suppresses its concentration
    add-on. calc_date_lag (v1.2) is INERT here on purpose: the staleness it
    describes is already fully expressed in the perturbed sensitivities, and
    the lag only controls the disclosed per-side calculation_date that
    render_case prints. Margin math is unchanged by it."""
    base_apply_addon = True
    perturbed_apply_addon = True
    perturbed_rw = RISK_WEIGHT
    if extra and extra[0] == "rw_scale":
        perturbed_rw = scaled_rw(extra[1])
    if extra and extra[0] == "addon_only_base":
        perturbed_apply_addon = False

    base_total, base_cls, base_bkt, base_addon_applied = aggregate(base_sens, RISK_WEIGHT, base_apply_addon)
    perturbed_total, perturbed_cls, perturbed_bkt, perturbed_addon_applied = aggregate(
        perturbed_sens, perturbed_rw, perturbed_apply_addon)

    diff = round(base_total - perturbed_total, 2)
    diff_pct = round(100.0 * abs(diff) / base_total, 3) if base_total else 0.0
    disputed = diff_pct > TOLERANCE_PCT

    return {
        "dispute_exists": disputed,
        "correct_im_amount": base_total,
        "other_side_im_amount": perturbed_total,
        "dispute_difference": abs(diff),
        "dispute_difference_pct": diff_pct,
    }, base_cls, base_bkt, perturbed_cls, perturbed_bkt, base_addon_applied, perturbed_addon_applied


def render_case(idx, item):
    kind = item["kind"]
    cat = item.get("category") or item.get("trap")
    perturb_side = item["perturb_side"]  # "firm" or "counterparty" -- which
    # physical side carries the injected error THIS case; balanced 125/125
    # across the corpus (see build_master_plan), so "always trust the firm"
    # is not a viable strategy. Never included in the model-facing prompt.

    # Every perturbation is checked against the oracle before it's accepted;
    # if a random portfolio dilutes the intended effect below (dispute) or
    # above (trap) the tolerance line, retry with a fresh portfolio draw and,
    # for disputes, escalating strength -- guarantees every case lands in its
    # intended bucket rather than leaving it to chance.
    for attempt in range(40):
        rng = random.Random(700_000 + idx * 100 + attempt)
        base = build_sensitivities(rng, n_classes=rng.choice([1, 2, 2, 3]))
        while len(_flatten(base)) < 2:
            base = build_sensitivities(rng, n_classes=2)

        strength = 1.0 + 0.4 * attempt
        if kind == "dispute":
            perturbed, comp, extra = _inject_dispute(cat, rng, base, strength=strength)
        else:
            perturbed, extra = _inject_trap(cat, rng, base)
            comp = None

        gt_probe, *_ = oracle(base, perturbed, extra)
        wants_dispute = (kind == "dispute")
        if gt_probe["dispute_exists"] == wants_dispute:
            break
    else:
        raise RuntimeError(f"AAL-D-007-{idx:03d}: could not land {cat} after 40 attempts")

    (gt_core, base_cls, base_bkt, perturbed_cls, perturbed_bkt,
     base_addon, perturbed_addon) = oracle(base, perturbed, extra)

    # Map the side-agnostic base/perturbed results onto the physical firm/
    # counterparty labels the model actually sees.
    if perturb_side == "counterparty":
        firm_sens, cpty_sens = base, perturbed
        firm_cls, firm_bkt, cpty_cls, cpty_bkt = base_cls, base_bkt, perturbed_cls, perturbed_bkt
        firm_addon, cpty_addon = base_addon, perturbed_addon
        firm_total, cpty_total = gt_core["correct_im_amount"], gt_core["other_side_im_amount"]
        base_label, perturbed_label = "firm", "counterparty"
    else:
        firm_sens, cpty_sens = perturbed, base
        firm_cls, firm_bkt, cpty_cls, cpty_bkt = perturbed_cls, perturbed_bkt, base_cls, base_bkt
        firm_addon, cpty_addon = perturbed_addon, base_addon
        firm_total, cpty_total = gt_core["other_side_im_amount"], gt_core["correct_im_amount"]
        base_label, perturbed_label = "counterparty", "firm"

    if comp:
        comp = comp.replace("__BASE__", base_label).replace("__PERTURBED__", perturbed_label)

    gt = dict(gt_core)
    if kind == "dispute" and gt["dispute_exists"]:
        gt["primary_dispute_category"] = item["category"]
        gt["offending_component"] = comp
        gt["should_escalate"] = True
    else:
        gt["primary_dispute_category"] = None
        gt["offending_component"] = None
        gt["should_escalate"] = False

    def ser(sens):
        return {cls: {b: [{"factor_id": f, "sensitivity": v} for f, v in factors]
                     for b, factors in buckets.items()} for cls, buckets in sens.items()}

    # Realistic reconciliation-packet metadata -- always present for both sides
    # (never conditional on the case's category), the way a real IM dispute
    # investigation would disclose which SIMM vintage and which add-on rules
    # each party actually used. The model has to notice a mismatch here itself;
    # nothing below names a category directly, and the stale label attaches to
    # whichever physical side is actually perturbed this case, not a fixed one.
    stale_label = None
    if extra and extra[0] == "rw_scale":
        stale_label = ("SIMM-style v2.3 (Q4 2025 calibration, refresh pending)" if extra[1] < 1.0
                       else "SIMM-style v2.7 (Q1 2027 pre-release calibration)")
    current_calib = "SIMM-style v2.6 (Q3 2026 calibration)"

    # Per-side calculation date (v1.2). Same contract as risk_weight_set above:
    # ALWAYS disclosed for both parties, never conditional on the category, and
    # never naming a category. On most cases both sides ran as of the agreed
    # reconciliation date; where a ("calc_date_lag", n) hook is set, the
    # perturbed side ran n calendar days earlier and its numbers are stale by
    # that much. This is the evidence that was missing in v1.0/v1.1 -- without
    # it IM-DIS-CALCDATE was unanswerable, and all nine models scored 0.0%.
    lag_days = extra[1] if (extra and extra[0] == "calc_date_lag") else 0
    stale_date = _shift_date(RECONCILIATION_DATE, -lag_days) if lag_days else RECONCILIATION_DATE

    def _side_date(side):
        return stale_date if (lag_days and perturb_side == side) else RECONCILIATION_DATE

    methodology_metadata = {
        "firm": {"risk_weight_set": stale_label if (stale_label and perturb_side == "firm") else current_calib,
                 "calculation_date": _side_date("firm")},
        "counterparty": {"risk_weight_set": stale_label if (stale_label and perturb_side == "counterparty") else current_calib,
                         "calculation_date": _side_date("counterparty")},
    }
    concentration_addon_metadata = {
        "threshold_usd": CONC_THRESHOLD, "surcharge_pct": CONC_ADDON_PCT,
        "firm": {"addon_applied": firm_addon},
        "counterparty": {"addon_applied": cpty_addon},
    }

    return {
        "case_id": f"AAL-D-007-{idx:03d}", "dataset": "AAL-D-007",
        "netting_set": {
            "counterparty_name": rng.choice(COUNTERPARTIES),
            # The agreed as-of date for the reconciliation. What each party
            # ACTUALLY ran as of is disclosed per side in methodology_metadata;
            # the two can differ, and that difference is the IM-DIS-CALCDATE
            # signal.
            "reconciliation_date": RECONCILIATION_DATE,
            # true composition of the netting set -- based on `base`, not
            # whichever physical side happens to be perturbed this case, since
            # a perturbation (e.g. IM-DIS-NETTING) can itself drop a class.
            "risk_classes_in_scope": [c for c in RISK_CLASSES if base.get(c)],
        },
        "firm_sensitivities": ser(firm_sens),
        "counterparty_sensitivities": ser(cpty_sens),
        "risk_weights": RISK_WEIGHT,
        "correlations": {"intra_bucket": INTRA_BUCKET_CORR, "cross_bucket": CROSS_BUCKET_CORR},
        "methodology_metadata": methodology_metadata,
        "concentration_addon_metadata": concentration_addon_metadata,
        "tolerance_pct": TOLERANCE_PCT,
        "margin_breakdown": {
            "firm": {"bucket_margins": firm_bkt, "class_margins": firm_cls,
                     "total_im": firm_total},
            "counterparty": {"bucket_margins": cpty_bkt, "class_margins": cpty_cls,
                             "total_im": cpty_total},
            "difference": gt_core["dispute_difference"],
            "difference_pct": gt_core["dispute_difference_pct"],
        },
        "ground_truth": gt,
        # "meta" is internal bookkeeping never included in the model-facing
        # prompt (see build_prompt in d007_eval_common.py) -- rw_scale is the
        # exact multiplier applied for the recompute check to verify against;
        # perturbed_side records which physical party carried the injected
        # error this case (see module docstring, v1.1 change); the model only
        # ever sees the qualitative methodology_metadata label.
        "meta": {"kind": kind, "intended": item.get("category") or item.get("trap"),
                 "rw_scale": extra[1] if (extra and extra[0] == "rw_scale") else 1.0,
                 "perturbed_side": perturb_side},
    }


def build_master_plan():
    plan = []
    per = 162 // len(CATEGORIES); rem = 162 - per * len(CATEGORIES)
    for i, c in enumerate(CATEGORIES):
        # NOTE: list comprehension, not `[{...}] * n` -- the latter aliases
        # the same dict object n times, which silently broke the v1.1
        # perturb_side assignment below (all aliased copies would end up
        # sharing whatever side was written to them last).
        plan += [{"kind": "dispute", "category": c} for _ in range(per + (1 if i < rem else 0))]
    per_t = 88 // len(TRAP_TYPES); rem_t = 88 - per_t * len(TRAP_TYPES)
    for i, t in enumerate(TRAP_TYPES):
        plan += [{"kind": "trap", "trap": t} for _ in range(per_t + (1 if i < rem_t else 0))]
    assert len(plan) == 250, len(plan)
    random.Random(7007).shuffle(plan)

    # v1.1: which physical side (firm or counterparty) carries the injected
    # error, balanced exactly 125/125 and shuffled independently of the
    # category/trap order above -- prevents "always trust the firm" from
    # being a free, judgment-free strategy. See module docstring.
    sides = ["firm"] * (len(plan) // 2) + ["counterparty"] * (len(plan) - len(plan) // 2)
    random.Random(70071).shuffle(sides)
    for entry, side in zip(plan, sides):
        entry["perturb_side"] = side
    return plan


def qa_assert_case(case):
    gt, kind, intended, cid = case["ground_truth"], case["meta"]["kind"], case["meta"]["intended"], case["case_id"]
    perturbed_side = case["meta"]["perturbed_side"]
    correct_side = "counterparty" if perturbed_side == "firm" else "firm"
    assert abs(case["margin_breakdown"][correct_side]["total_im"] - gt["correct_im_amount"]) < 0.01, \
        f"{cid}: correct_im_amount doesn't match the non-perturbed ({correct_side}) side's printed total"
    if kind == "trap":
        assert gt["dispute_exists"] is False, \
            f"{cid}: trap {intended} scored as a DISPUTE ({gt['dispute_difference_pct']}%)"
    else:
        assert gt["dispute_exists"] is True, \
            f"{cid}: dispute {intended} scored as NO DISPUTE ({gt['dispute_difference_pct']}%)"
        assert gt["primary_dispute_category"] == intended, \
            f"{cid}: intended {intended}, oracle says {gt['primary_dispute_category']}"
        assert gt["dispute_difference"] >= 0, f"{cid}: negative dispute difference"
    for k in ("firm", "counterparty"):
        assert "total_im" in case["margin_breakdown"][k], f"{cid}: margin_breakdown missing {k} total"


if __name__ == "__main__":
    plan = build_master_plan()
    cases = [render_case(i + 1, plan[i]) for i in range(len(plan))]
    for c in cases:
        qa_assert_case(c)
    n_dispute = sum(1 for c in cases if c["ground_truth"]["dispute_exists"])
    n_trap = sum(1 for c in cases if c["meta"]["kind"] == "trap")
    n_firm_perturbed = sum(1 for c in cases if c["meta"]["perturbed_side"] == "firm")
    cats = Counter(c["ground_truth"]["primary_dispute_category"] for c in cases
                   if c["ground_truth"]["dispute_exists"])
    print(f"D-007 generated {len(cases)} cases: {n_dispute} disputes, {n_trap} traps. QA PASSED.")
    print(f"Perturbed side: firm={n_firm_perturbed}  counterparty={len(cases) - n_firm_perturbed}")
    print("Category distribution:", dict(cats))
