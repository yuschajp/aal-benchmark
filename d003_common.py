#!/usr/bin/env python3
"""
AI Alpha Labs — AAL-D-003 shared generator infrastructure.

Everything the five `generate_d003_batch00N.py` scripts have in common lives
here: reference data (venues/underlyings), the deterministic 250-case master
allocation plan (venue x product-type x difficulty x error-category, built
to match D003-spec.md section 12 exactly), record builders, the 19-category
error-injection taxonomy, risk_level / failure_modes / scoring_criteria /
generation_metadata assembly, and the final case renderer.

`recompute_check_d003.py` (QA gate 2, an independent recomputation) must NOT
import this module or d003_pricing.py's ground-truth call sites in the same
way the generators do — that is a downstream deliverable owned separately.
Sharing this module *between the five batch generators* is fine and
intentional (spec build sequence step 2 treats them as one deliverable).

Determinism: every batch script calls `build_master_plan()` (module-level,
seeded) and slices its 50-case chunk. No wall-clock time, no dict-order
nondeterminism, no unseeded randomness anywhere in the render path.
"""

import copy
import math
import random
from datetime import datetime, timedelta

import d003_pricing as pr

GENERATOR_VERSION = "d003-v1.0.3"
PREMIUM_FLOOR = 0.05          # min correct per-option premium, quote units
BENCHMARK_VERSION = "1.0.2"
BASE_DATE = datetime(2026, 7, 1)
VALUATION_DATE = "2026-07-01"
DAY_COUNT = "ACT/365F"
BINOMIAL_STEPS = 1500
MASTER_SEED = 2026003

# ── REFERENCE DATA ─────────────────────────────────────────────────────────

# OTC (Bloomberg-style) single-name underlyings — discrete dividend schedule,
# European (BSM-on-forward, escrowed) or American (CRR, escrowed), r flat.
BBG_SINGLE_NAMES = [
    {"ticker": "AAPL US Equity", "spot": 218.50, "sigma": 0.24, "div": 0.26, "div_months": [1, 4, 7, 10]},
    {"ticker": "MSFT US Equity", "spot": 445.30, "sigma": 0.22, "div": 0.83, "div_months": [2, 5, 8, 11]},
    {"ticker": "NVDA US Equity", "spot": 131.85, "sigma": 0.45, "div": 0.01, "div_months": [3, 6, 9, 12]},
    {"ticker": "AMZN US Equity", "spot": 232.50, "sigma": 0.32, "div": 0.0, "div_months": []},
    {"ticker": "GOOGL US Equity", "spot": 178.40, "sigma": 0.28, "div": 0.21, "div_months": [3, 6, 9, 12]},
    {"ticker": "META US Equity", "spot": 628.40, "sigma": 0.34, "div": 0.525, "div_months": [3, 6, 9, 12]},
    {"ticker": "TSLA US Equity", "spot": 248.60, "sigma": 0.52, "div": 0.0, "div_months": []},
    {"ticker": "JPM US Equity", "spot": 265.90, "sigma": 0.24, "div": 1.40, "div_months": [1, 4, 7, 10]},
    {"ticker": "GS US Equity", "spot": 618.25, "sigma": 0.27, "div": 3.00, "div_months": [3, 6, 9, 12]},
    {"ticker": "UNH US Equity", "spot": 522.30, "sigma": 0.26, "div": 2.10, "div_months": [3, 6, 9, 12]},
    {"ticker": "V US Equity", "spot": 312.40, "sigma": 0.21, "div": 0.59, "div_months": [2, 5, 8, 11]},
    {"ticker": "XOM US Equity", "spot": 118.40, "sigma": 0.23, "div": 0.99, "div_months": [3, 6, 9, 12]},
    {"ticker": "PG US Equity", "spot": 168.75, "sigma": 0.16, "div": 1.0565, "div_months": [2, 5, 8, 11]},
    {"ticker": "HD US Equity", "spot": 378.20, "sigma": 0.24, "div": 2.30, "div_months": [3, 6, 9, 12]},
    {"ticker": "AVGO US Equity", "spot": 172.30, "sigma": 0.38, "div": 0.59, "div_months": [3, 6, 9, 12]},
    {"ticker": "LLY US Equity", "spot": 795.10, "sigma": 0.31, "div": 1.50, "div_months": [3, 6, 9, 12]},
    {"ticker": "COST US Equity", "spot": 905.60, "sigma": 0.20, "div": 1.16, "div_months": [2, 5, 8, 11]},
]

BBG_INDICES = [
    {"ticker": "SPX Index", "spot": 5900.00, "sigma": 0.16, "q": 0.0130},
    {"ticker": "NDX Index", "spot": 21200.00, "sigma": 0.20, "q": 0.0070},
]

EUREX_SINGLE_STOCKS = [
    {"ticker": "SAP GY Equity", "spot": 220.40, "sigma": 0.24, "div": 2.35, "div_month": 9},
    {"ticker": "SIE GY Equity", "spot": 185.60, "sigma": 0.27, "div": 5.20, "div_month": 11},
]

EUREX_INDICES = [
    {"ticker": "SX5E Index", "series": "OESX", "spot": 4950.00, "sigma": 0.17, "q": 0.0300, "mult": 10.0},
    {"ticker": "DAX Index", "series": "ODAX", "spot": 19800.00, "sigma": 0.19, "q": 0.0290, "mult": 5.0},
]

CME_PRODUCTS = [
    {"ticker": "ES", "name": "E-mini S&P 500", "spot": 5900.00, "sigma": 0.15, "mult": 50.0},
    {"ticker": "NQ", "name": "E-mini Nasdaq-100", "spot": 21200.00, "sigma": 0.21, "mult": 20.0},
]

RATE_OTC = 0.0450     # USD short rate proxy used for BBG/CME discounting
RATE_EUR = 0.0280     # EUR short rate proxy used for Eurex discounting

COUNTERPARTIES = ["Prime Broker Alpha", "Prime Broker Beta", "Prime Broker Gamma", "Prime Broker Delta",
                  "Dealer Bank Alpha", "Dealer Bank Beta"]
CLEARING_BROKERS = ["Clearing Broker Alpha", "Clearing Broker Beta", "Clearing Broker Gamma"]

# ── ERROR-CATEGORY TAXONOMY (spec section 5) ───────────────────────────────

CAT_COUNTS = {
    "EXC-PRICE": 20, "EXC-PROD": 6, "EXC-QTY": 4, "EXC-CCY": 4, "EXC-CPTY": 4, "EXC-COMM": 4,
    "EXC-TDATE": 2, "EXC-SDATE": 2,
    "EXC-GREEK": 16, "EXC-STRIKE": 14, "EXC-NOTIONAL": 12, "EXC-DIV": 12, "EXC-SETTLE": 12,
    "EXC-CORPACT": 10, "EXC-LEG": 10, "EXC-EXPIRY": 8, "EXC-STYLE": 8, "EXC-RATIO": 8, "EXC-MULT": 6,
}
assert sum(CAT_COUNTS.values()) == 162, sum(CAT_COUNTS.values())

SINGLE_NAME_ONLY_CATS = {"EXC-CORPACT", "EXC-DIV", "EXC-STYLE"}   # BBG single_name / Eurex single_stock, single-leg
CME_ONLY_CATS = {"EXC-MULT"}                                      # CME single-leg
EUREX_ONLY_CATS = {"EXC-CCY"}                                     # Eurex, any product
SPREAD_ONLY_CATS = {"EXC-LEG", "EXC-RATIO", "EXC-EXPIRY"}         # multi-leg only, any venue
UNRESTRICTED_CATS = (set(CAT_COUNTS) - SINGLE_NAME_ONLY_CATS - CME_ONLY_CATS
                      - EUREX_ONLY_CATS - SPREAD_ONLY_CATS)

# Failure-mode legend: FM-01..12 are the family (D-001) legend. D-001's
# authoritative text was never supplied (D003-spec.md open question O3) —
# these are inferred, best-effort, from how generate_cases_v2.py actually
# uses each code (field-level break / false-positive-resistance / severity /
# term-mismatch / dual-exception, etc.), not from a written legend.
FM_GENERIC_BREAK = "FM-01"          # basic field-level discrepancy detection
FM_FALSE_POSITIVE = "FM-02"         # clean-match false-positive resistance
FM_SSI_ATTENTION = "FM-03"          # settlement-instruction attention
FM_MAGNITUDE = "FM-04"              # subtle/small-magnitude sensitivity
FM_IDENTITY = "FM-05"               # counterparty/currency identity mismatch
FM_DATE_CONVENTION = "FM-06"        # date/convention handling
FM_NONPRICE_FIELD = "FM-07"         # attention to non-price fields
FM_EXPOSURE_CALC = "FM-08"          # exposure/notional arithmetic
FM_TERM_MISMATCH = "FM-09"          # economic term / convention mismatch
FM_DUAL_EXCEPTION = "FM-10"         # multiple simultaneous exceptions
FM_DUPLICATE = "FM-11"              # duplicate / re-affirmation handling
FM_SEVERITY = "FM-12"               # severity / high-impact classification
FM_LEG_ATTRIBUTION = "FM-13"
FM_STYLE_CONFUSION = "FM-14"
FM_GREEKS_UNIT_SIGN = "FM-15"
FM_CORPACT_LOGIC = "FM-16"
FM_PER_CONTRACT_VS_TOTAL = "FM-17"
FM_MULTIPLIER = "FM-18"
FM_DIVIDEND_TREATMENT = "FM-19"
FM_FUTURES_VS_SPOT = "FM-20"

CAT_FAILURE_MODES = {
    "EXC-PRICE": [FM_GENERIC_BREAK, FM_EXPOSURE_CALC],
    "EXC-PROD": [FM_GENERIC_BREAK, FM_TERM_MISMATCH],
    "EXC-QTY": [FM_GENERIC_BREAK, FM_EXPOSURE_CALC, FM_SEVERITY],
    "EXC-CCY": [FM_IDENTITY, FM_SEVERITY],
    "EXC-CPTY": [FM_IDENTITY, FM_NONPRICE_FIELD],
    "EXC-COMM": [FM_NONPRICE_FIELD, FM_PER_CONTRACT_VS_TOTAL],
    "EXC-TDATE": [FM_DATE_CONVENTION],
    "EXC-SDATE": [FM_DATE_CONVENTION, FM_SSI_ATTENTION],
    "EXC-GREEK": [FM_GREEKS_UNIT_SIGN, FM_MAGNITUDE],
    "EXC-STRIKE": [FM_GENERIC_BREAK, FM_EXPOSURE_CALC],
    "EXC-NOTIONAL": [FM_PER_CONTRACT_VS_TOTAL, FM_EXPOSURE_CALC],
    "EXC-DIV": [FM_DIVIDEND_TREATMENT, FM_MAGNITUDE],
    "EXC-SETTLE": [FM_SSI_ATTENTION, FM_SEVERITY],
    "EXC-CORPACT": [FM_CORPACT_LOGIC, FM_SEVERITY],
    "EXC-LEG": [FM_LEG_ATTRIBUTION, FM_DUAL_EXCEPTION],
    "EXC-EXPIRY": [FM_LEG_ATTRIBUTION, FM_SEVERITY],
    "EXC-STYLE": [FM_STYLE_CONFUSION, FM_TERM_MISMATCH],
    "EXC-RATIO": [FM_LEG_ATTRIBUTION, FM_MAGNITUDE],
    "EXC-MULT": [FM_MULTIPLIER, FM_FUTURES_VS_SPOT],
}

# ── DAY COUNT / DATE HELPERS ────────────────────────────────────────────────

def yearfrac(d0, d1):
    return pr.yearfrac(d0, d1)


def add_days(dstr, n):
    d = datetime.strptime(dstr, "%Y-%m-%d") + timedelta(days=n)
    return d.strftime("%Y-%m-%d")


def to_bd(dstr):
    """Roll forward to the next weekday if a weekend date is hit."""
    d = datetime.strptime(dstr, "%Y-%m-%d")
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def settle(dstr, n_days):
    return to_bd(add_days(dstr, n_days))


def rp2(x):
    return round(x, 2)


def rp4(x):
    return round(x, 4)


# ── STEP 1: build the 250-slot venue/product allocation ────────────────────
# Exact counts per D003-spec.md section 12. Slot dict fields:
#   venue in {"BBG","CME","Eurex"}; leg in {"single","spread"};
#   underlying_kind in {"index","single_name","single_stock","future"};
#   style in {"European","American"}; is_call (single-leg only, else None).

def _singleleg_slots(venue, underlying_kind, style, n_call, n_put):
    return ([{"venue": venue, "leg": "single", "underlying_kind": underlying_kind,
              "style": style, "is_call": True} for _ in range(n_call)]
            + [{"venue": venue, "leg": "single", "underlying_kind": underlying_kind,
                "style": style, "is_call": False} for _ in range(n_put)])


def _spread_slots(venue, underlying_kind, style, n):
    return [{"venue": venue, "leg": "spread", "underlying_kind": underlying_kind,
             "style": style, "is_call": None} for _ in range(n)]


def _build_slots():
    slots = []
    # BBG 100 = 34C/30P/36 spread
    slots += _singleleg_slots("BBG", "index", "European", 9, 7)                     # 16
    slots += _singleleg_slots("BBG", "single_name", "European", 13, 11)             # 24
    slots += _singleleg_slots("BBG", "single_name", "American", 12, 12)             # 24
    slots += _spread_slots("BBG", "single_name", "European", 16)
    slots += _spread_slots("BBG", "single_name", "American", 20)
    assert sum(1 for s in slots if s["venue"] == "BBG") == 100

    # CME 65 = 22C/18P/25 spread — options on ES/NQ futures only (O1)
    slots += _singleleg_slots("CME", "future", "American", 13, 11)                  # monthlies, 24
    slots += _singleleg_slots("CME", "future", "European", 9, 7)                    # EW weeklies, 16
    slots += _spread_slots("CME", "future", "American", 15)
    slots += _spread_slots("CME", "future", "European", 10)
    assert sum(1 for s in slots if s["venue"] == "CME") == 65

    # Eurex 85 = 24C/22P/39 spread
    slots += _singleleg_slots("Eurex", "index", "European", 9, 7)                   # OESX/ODAX, 16
    slots += _singleleg_slots("Eurex", "single_stock", "American", 15, 15)          # SAP/Siemens, 30
    slots += _spread_slots("Eurex", "index", "European", 15)
    slots += _spread_slots("Eurex", "single_stock", "American", 24)
    assert sum(1 for s in slots if s["venue"] == "Eurex") == 85

    assert len(slots) == 250
    return slots


# ── STEP 2: assign categories (and clean) to slots ─────────────────────────

def _assign_categories(slots, rng):
    idx_all = list(range(len(slots)))
    rng.shuffle(idx_all)   # deterministic (seeded) shuffle so category placement isn't clustered by construction order
    assigned = [None] * len(slots)

    def take(predicate, count):
        taken = []
        for i in idx_all:
            if len(taken) >= count:
                break
            if assigned[i] is None and predicate(slots[i]):
                taken.append(i)
        if len(taken) < count:
            raise RuntimeError(f"could not find {count} slots matching predicate; got {len(taken)}")
        return taken

    def is_single_name(s):
        return s["leg"] == "single" and s["underlying_kind"] in ("single_name", "single_stock")

    def is_cme_single(s):
        return s["venue"] == "CME" and s["leg"] == "single"

    def is_eurex(s):
        return s["venue"] == "Eurex"

    def is_spread(s):
        return s["leg"] == "spread"

    # concentration-restricted categories first (tightest pools first)
    for cat in ["EXC-CORPACT", "EXC-DIV", "EXC-STYLE"]:
        for i in take(is_single_name, CAT_COUNTS[cat]):
            assigned[i] = cat
    for cat in ["EXC-MULT"]:
        for i in take(is_cme_single, CAT_COUNTS[cat]):
            assigned[i] = cat
    for cat in ["EXC-CCY"]:
        for i in take(is_eurex, CAT_COUNTS[cat]):
            assigned[i] = cat
    for cat in ["EXC-LEG", "EXC-RATIO", "EXC-EXPIRY"]:
        for i in take(is_spread, CAT_COUNTS[cat]):
            assigned[i] = cat

    # unrestricted categories -> any remaining slot
    for cat in sorted(UNRESTRICTED_CATS):
        for i in take(lambda s: True, CAT_COUNTS[cat]):
            assigned[i] = cat

    n_exception = sum(1 for a in assigned if a is not None)
    assert n_exception == 162, n_exception
    n_clean = sum(1 for a in assigned if a is None)
    assert n_clean == 88, n_clean
    return assigned


# ── STEP 3: assign difficulty (+ clean flag already implied by category) ───

def _assign_difficulty(slots, categories, rng):
    n = len(slots)
    clean_idx = [i for i in range(n) if categories[i] is None]
    exc_idx = [i for i in range(n) if categories[i] is not None]
    rng.shuffle(clean_idx)
    rng.shuffle(exc_idx)

    diff = [None] * n
    # clean: easy 30 / moderate 40 / complex 18
    for i in clean_idx[:30]:
        diff[i] = "easy"
    for i in clean_idx[30:70]:
        diff[i] = "moderate"
    for i in clean_idx[70:88]:
        diff[i] = "complex"
    # exception: easy 30 / moderate 70 / complex 62
    for i in exc_idx[:30]:
        diff[i] = "easy"
    for i in exc_idx[30:100]:
        diff[i] = "moderate"
    for i in exc_idx[100:162]:
        diff[i] = "complex"
    assert all(d is not None for d in diff)

    # ~20 complex-exception cases are dual-exception
    complex_exc = [i for i in exc_idx if diff[i] == "complex"]
    rng.shuffle(complex_exc)
    dual = set(complex_exc[:20])
    return diff, dual


# ── STEP 4: bucket the 250 assigned slots into 5 thematic batches of 50 ────

def _theme_priority(slot, cat, clean):
    v, leg, uk = slot["venue"], slot["leg"], slot["underlying_kind"]
    # Batch 5 = "clean pool + Greeks + traps": clean cases and EXC-GREEK cases
    # prefer batch 5 first, falling back to their natural venue/leg batch.
    if clean or cat == "EXC-GREEK":
        if leg == "spread":
            fallback = (4, 3, 2, 1)
        elif v == "CME" or (v == "Eurex" and uk == "index"):
            fallback = (3, 1, 2, 4)
        elif v == "BBG":
            fallback = (1, 2, 3, 4)
        else:
            fallback = (2, 3, 1, 4)
        return (5,) + fallback
    if cat in ("EXC-CORPACT", "EXC-DIV", "EXC-STYLE"):
        return (2, 1, 3, 5, 4)
    if leg == "spread":
        return (4, 3, 2, 1, 5)
    if v == "CME" or (v == "Eurex" and uk == "index"):
        return (3, 1, 2, 4, 5)
    if v == "BBG":
        return (1, 2, 3, 4, 5)
    if v == "Eurex":  # single_stock single-leg, general category
        return (2, 3, 1, 4, 5)
    return (5, 1, 2, 3, 4)


def _bucket_into_batches(slots, categories, diff, dual, rng):
    n = len(slots)
    order = list(range(n))
    rng.shuffle(order)
    capacity = {b: 50 for b in (1, 2, 3, 4, 5)}
    batch_of = [None] * n
    for i in order:
        pri = _theme_priority(slots[i], categories[i], categories[i] is None)
        for b in pri:
            if capacity[b] > 0:
                batch_of[i] = b
                capacity[b] -= 1
                break
        assert batch_of[i] is not None, f"no capacity left for slot {i}"
    assert all(v == 0 for v in capacity.values())
    return batch_of


# ── MASTER PLAN (module-level, built once, deterministic) ──────────────────

_PLAN_CACHE = None


def build_master_plan():
    global _PLAN_CACHE
    if _PLAN_CACHE is not None:
        return _PLAN_CACHE
    rng = random.Random(MASTER_SEED)
    slots = _build_slots()
    categories = _assign_categories(slots, random.Random(MASTER_SEED + 1))
    diff, dual = _assign_difficulty(slots, categories, random.Random(MASTER_SEED + 2))
    batch_of = _bucket_into_batches(slots, categories, diff, dual, random.Random(MASTER_SEED + 3))

    plan = []
    for i in range(len(slots)):
        plan.append({
            "slot_index": i, "venue": slots[i]["venue"], "leg": slots[i]["leg"],
            "underlying_kind": slots[i]["underlying_kind"], "style": slots[i]["style"],
            "is_call": slots[i]["is_call"], "category": categories[i],
            "is_clean": categories[i] is None, "difficulty": diff[i],
            "dual_exception": i in dual, "batch": batch_of[i],
        })

    # order: batch, then a stable seeded shuffle within-batch so case_id order
    # doesn't trivially reveal category clustering, then renumber sequentially.
    plan.sort(key=lambda p: (p["batch"], p["slot_index"]))
    rng2 = random.Random(MASTER_SEED + 4)
    by_batch = {b: [p for p in plan if p["batch"] == b] for b in (1, 2, 3, 4, 5)}
    ordered = []
    for b in (1, 2, 3, 4, 5):
        chunk = by_batch[b]
        rng2.shuffle(chunk)
        assert len(chunk) == 50, (b, len(chunk))
        ordered.extend(chunk)
    for seq, p in enumerate(ordered, start=1):
        p["seq"] = seq
        p["case_id"] = f"AAL-D-003-{seq:03d}"

    _PLAN_CACHE = ordered
    return ordered


def plan_for_batch(batch_num):
    plan = build_master_plan()
    return [p for p in plan if p["batch"] == batch_num]


# ── REFERENCE-DATA PICKERS ──────────────────────────────────────────────────

def _pick_ref(venue, underlying_kind, rng):
    if venue == "BBG" and underlying_kind == "index":
        return dict(rng.choice(BBG_INDICES))
    if venue == "BBG":
        return dict(rng.choice(BBG_SINGLE_NAMES))
    if venue == "CME":
        return dict(rng.choice(CME_PRODUCTS))
    if venue == "Eurex" and underlying_kind == "index":
        return dict(rng.choice(EUREX_INDICES))
    return dict(rng.choice(EUREX_SINGLE_STOCKS))


def _strike_tick(venue, underlying_kind, ref):
    if venue == "BBG" and underlying_kind == "index":
        return 50.0 if ref["ticker"].startswith("NDX") else 25.0
    if venue == "BBG":
        return 2.5
    if venue == "CME":
        return 50.0 if ref["ticker"] == "NQ" else 25.0
    if venue == "Eurex" and underlying_kind == "index":
        return 50.0 if ref["series"] == "ODAX" else 25.0
    return 5.0  # Eurex single stock, EUR


def _pick_strike(rng, spot, tick):
    moneyness = rng.choice([0.85, 0.90, 0.95, 0.975, 1.0, 1.0, 1.025, 1.05, 1.10, 1.15])
    raw = spot * moneyness
    return max(tick, round(raw / tick) * tick)


def _pick_expiry_days(rng, style, venue, underlying_kind):
    if venue == "CME" and style == "European":  # EW weeklies
        return rng.choice([7, 14, 21, 28, 35])
    return rng.choice([30, 45, 60, 90, 120, 150, 180])


def _pick_expiry(rng, style, venue, underlying_kind):
    return to_bd(add_days(VALUATION_DATE, _pick_expiry_days(rng, style, venue, underlying_kind)))


def _add_months(dstr, n):
    d = datetime.strptime(dstr, "%Y-%m-%d")
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, 28)
    return datetime(y, m, day).strftime("%Y-%m-%d")


def _dividend_schedule(venue, ref, expiry):
    divs = []
    if venue == "BBG":
        if ref.get("div", 0) <= 0 or not ref.get("div_months"):
            return divs
        cands = []
        for yy in (2026, 2027):
            for mm in ref["div_months"]:
                cands.append(f"{yy}-{mm:02d}-15")
        for d in sorted(cands):
            if VALUATION_DATE < d <= expiry:
                divs.append({"date": to_bd(d), "amount": round(ref["div"], 4)})
        return divs
    # Eurex single stock: one annual dividend
    for yy in (2026, 2027):
        d = f"{yy}-{ref['div_month']:02d}-15"
        if VALUATION_DATE < d <= expiry:
            divs.append({"date": to_bd(d), "amount": round(ref["div"], 4)})
    return divs


def _rate_for(venue):
    return RATE_EUR if venue == "Eurex" else RATE_OTC


def _currency_for(venue):
    return "EUR" if venue == "Eurex" else "USD"


def _multiplier_for(venue, underlying_kind, ref):
    if venue == "CME":
        return ref["mult"]
    if venue == "Eurex" and underlying_kind == "index":
        return ref["mult"]
    return 100.0  # BBG (single-name & index), Eurex single-stock: 100 shares/contract convention


def _settlement_method(venue, underlying_kind, style):
    if underlying_kind in ("single_name", "single_stock"):
        return "Physical"
    if venue == "CME":
        # ES/NQ options (monthly AND EW weekly) exercise into the future —
        # booked consistently as Physical (gate-7 minor, v1.0.1)
        return "Physical"
    return "Cash"  # index products: always cash


def _premium_unit(venue, underlying_kind):
    if underlying_kind == "future":
        return "per_future_point"
    if underlying_kind == "index":
        return "per_index_point"
    return "per_share"


# ── PER-LEG PRICING ──────────────────────────────────────────────────────────

def _price_leg(venue, underlying_kind, style, is_call, S, K, r, sigma, T, div_times, div_amounts, q, need_greeks):
    american = (style == "American")
    if underlying_kind == "future":
        if american:
            premium = pr.crr_price_future(S, K, r, sigma, T, BINOMIAL_STEPS, is_call, True)
            greeks = pr.crr_greeks_future(S, K, r, sigma, T, BINOMIAL_STEPS, is_call, True) if need_greeks else None
        else:
            premium = pr.black76_price(S, K, r, sigma, T, is_call)
            greeks = pr.black76_greeks(S, K, r, sigma, T, is_call) if need_greeks else None
    elif underlying_kind == "index":
        premium = pr.bsm_price(S, K, r, q, sigma, T, is_call)
        greeks = pr.bsm_greeks(S, K, r, q, sigma, T, is_call) if need_greeks else None
    else:  # single_name / single_stock, discrete dividends
        if american:
            premium = pr.crr_price_spot(S, K, r, sigma, T, BINOMIAL_STEPS, is_call, True, div_times, div_amounts)
            greeks = (pr.crr_greeks_spot(S, K, r, sigma, T, BINOMIAL_STEPS, is_call, True, div_times, div_amounts)
                      if need_greeks else None)
        else:
            Sadj = pr.escrowed_spot(S, div_times, div_amounts, r)
            premium = pr.bsm_price(Sadj, K, r, 0.0, sigma, T, is_call)
            greeks = pr.bsm_greeks(Sadj, K, r, 0.0, sigma, T, is_call) if need_greeks else None
    return premium, greeks


GREEK_UNITS = {
    "delta": "$ per $1 underlying", "gamma": "$ per $1 underlying^2",
    "vega": "$ per +1 vol point", "theta": "$ per -1 calendar day", "rho": "$ per 1bp rate",
}
GREEK_TOL = {
    "delta": {"method": "absolute", "tolerance": 0.01},
    "gamma": {"method": "absolute", "tolerance": 0.005},
    "vega": {"method": "relative", "tolerance": 0.01, "abs_floor": 0.01},
    "theta": {"method": "relative", "tolerance": 0.02, "abs_floor": 0.01},
    "rho": {"method": "relative", "tolerance": 0.02, "abs_floor": 0.01},
}


def _round_greeks(g):
    return {k: round(v, 5) for k, v in g.items()}


def _pick_qty(rng, venue, underlying_kind):
    if underlying_kind in ("index",):
        return rng.choice([5, 10, 15, 20, 25, 30, 50])
    if venue == "CME":
        return rng.choice([5, 10, 15, 20, 25, 30, 50])
    return rng.choice([10, 20, 25, 50, 75, 100, 150, 200])


# ── TRAP SELECTION (clean-case "looks wrong but isn't" pool, spec section 5) ─

_TRAP_CACHE = None


def _select_traps():
    global _TRAP_CACHE
    if _TRAP_CACHE is not None:
        return _TRAP_CACHE
    plan = build_master_plan()
    pool = [p for p in plan if p["is_clean"] and p["leg"] == "single" and p["difficulty"] in ("moderate", "complex")]
    rng = random.Random(MASTER_SEED + 5)
    rng.shuffle(pool)
    # split (corporate-action) traps only make sense on single names — indices
    # and futures never split; an ES contract with multiplier 75 is impossible
    # (gate-7 finding, v1.0.1)
    splittable = [p for p in pool if p["underlying_kind"] in ("single_name", "single_stock")]
    assert len(splittable) >= 6, "not enough single-name clean singles for split traps"
    split_chosen = splittable[:6]
    split_seqs = {p["seq"] for p in split_chosen}
    rest = [p for p in pool if p["seq"] not in split_seqs][:12]
    types = ["deep_itm"] * 6 + ["neg_theta"] * 6
    rng.shuffle(types)
    _TRAP_CACHE = {p["seq"]: "split" for p in split_chosen}
    _TRAP_CACHE.update({p["seq"]: t for p, t in zip(rest, types)})
    return _TRAP_CACHE


# ── LEG CONSTRUCTION ─────────────────────────────────────────────────────────

def _build_legs(p, rng, need_greeks, trap_type=None):
    """Returns (ticker, currency, legs[list of raw pricing dicts], spread_type)."""
    venue, underlying_kind, style = p["venue"], p["underlying_kind"], p["style"]
    requires_div = p["category"] in ("EXC-DIV", "EXC-CORPACT") or trap_type == "split"

    def pick_ref():
        if requires_div and underlying_kind in ("single_name", "single_stock"):
            pool = BBG_SINGLE_NAMES if venue == "BBG" else EUREX_SINGLE_STOCKS
            pool = [x for x in pool if x.get("div", 0) > 0]
            return dict(rng.choice(pool))
        return _pick_ref(venue, underlying_kind, rng)

    ref = pick_ref()
    # quantize market inputs BEFORE pricing so valuation_inputs records exactly
    # the values used — the independent recompute (QA gate 2) reprices from the
    # recorded inputs at 1e-6 rel and any hidden precision breaks the gate
    spot = round(ref["spot"] * rng.uniform(0.97, 1.03), 4)
    sigma = round(max(0.05, ref["sigma"] * rng.uniform(0.9, 1.1)), 4)
    r = _rate_for(venue)
    tick = _strike_tick(venue, underlying_kind, ref)
    mult_base = _multiplier_for(venue, underlying_kind, ref)
    currency = _currency_for(venue)
    q = ref.get("q", 0.0) if underlying_kind == "index" else 0.0

    def make_leg(leg_index, is_call, expiry, K, side, qty, mult):
        T = yearfrac(VALUATION_DATE, expiry)
        divsched, div_times, div_amounts = [], [], []
        if underlying_kind in ("single_name", "single_stock"):
            divsched = _dividend_schedule(venue, ref, expiry)
            div_times, div_amounts = pr.dividend_times_amounts(divsched, VALUATION_DATE, expiry)
        K_disp = round(K, 2)
        premium, greeks = _price_leg(venue, underlying_kind, style, is_call, spot, K_disp, r, sigma, T,
                                      div_times, div_amounts, q, need_greeks)
        return {
            "leg_index": leg_index, "option_type": "Call" if is_call else "Put",
            "exercise_style": style, "strike": K_disp, "expiry": expiry, "side": side,
            "quantity": qty, "multiplier": round(mult, 4), "premium": round(premium, 2),
            "premium_exact": premium,
            "greeks": _round_greeks(greeks) if greeks else None,
            "greeks_exact": greeks,
            "dividend_schedule": divsched, "S": spot, "sigma": sigma, "r": r, "T": T, "q": q,
            "div_times": div_times, "div_amounts": div_amounts,
        }

    if p["leg"] == "single":
        is_call = p["is_call"]
        if trap_type == "deep_itm":
            # deep ITM: calls need K well BELOW spot, puts well ABOVE
            moneyness = rng.choice([0.55, 0.6, 0.67]) if is_call else rng.choice([1.5, 1.6, 1.75])
            K = max(tick, round(spot * moneyness / tick) * tick)
            expiry = _pick_expiry(rng, style, venue, underlying_kind)
            side = "Buy"
        elif trap_type == "split":
            base_strike = _pick_strike(rng, spot, tick)
            # real split ratios only: 3:2, 2:1, 3:1, 4:1 (gate-7 minor, v1.0.1)
            ratio = rng.choice([1.5, 2.0, 3.0, 4.0])
            K = round(base_strike / ratio, 2)
            mult_base = round(mult_base * ratio, 2)
            expiry = _pick_expiry(rng, style, venue, underlying_kind)
            side = rng.choice(["Buy", "Sell"])
        elif trap_type == "neg_theta":
            K = _pick_strike(rng, spot, tick)
            expiry = _pick_expiry(rng, style, venue, underlying_kind)
            side = "Buy"
        else:
            K = _pick_strike(rng, spot, tick)
            expiry = _pick_expiry(rng, style, venue, underlying_kind)
            side = rng.choice(["Buy", "Sell"])

        # EXC-DIV / EXC-CORPACT need at least one confirmed dividend before expiry;
        # widen the tenor deterministically if the drawn expiry missed the ticker's
        # dividend date(s) rather than silently falling back to a div-free case.
        if requires_div and underlying_kind in ("single_name", "single_stock"):
            for extra_days in (0, 60, 120, 200, 300, 400):
                cand_expiry = to_bd(add_days(expiry, extra_days)) if extra_days else expiry
                if _dividend_schedule(venue, ref, cand_expiry):
                    expiry = cand_expiry
                    break

        qty = _pick_qty(rng, venue, underlying_kind)
        leg = make_leg(None, is_call, expiry, K, side, qty, mult_base)
        return ref["ticker"], currency, r, [leg], None, ref

    # spread (2 legs)
    cat = p["category"]
    if cat == "EXC-RATIO":
        spread_type = "Ratio"
    elif cat == "EXC-EXPIRY":
        spread_type = "Calendar"
    else:
        spread_type = rng.choice(["Vertical", "Vertical", "Vertical", "Calendar"])

    is_call = rng.choice([True, False])
    qty1 = _pick_qty(rng, venue, underlying_kind)
    if spread_type == "Calendar":
        expiry1 = _pick_expiry(rng, style, venue, underlying_kind)
        expiry2 = to_bd(add_days(expiry1, rng.choice([30, 45, 60, 90])))
        K1 = K2 = _pick_strike(rng, spot, tick)
        qty2 = qty1
    else:
        expiry1 = expiry2 = _pick_expiry(rng, style, venue, underlying_kind)
        K1 = _pick_strike(rng, spot, tick)
        wing = tick * rng.choice([2, 3, 4, 5])
        K2 = max(tick, K1 + wing if is_call else K1 - wing)
        qty2 = qty1 * rng.choice([2, 3]) if spread_type == "Ratio" else qty1

    leg1 = make_leg(1, is_call, expiry1, K1, "Buy", qty1, mult_base)
    leg2 = make_leg(2, is_call, expiry2, K2, "Sell", qty2, mult_base)
    return ref["ticker"], currency, r, [leg1, leg2], spread_type, ref


# ── SERIALIZATION (raw pricing legs -> input-ready record) ─────────────────

def _leg_public(leg, premium_unit):
    d = {
        "leg_index": leg["leg_index"], "option_type": leg["option_type"],
        "exercise_style": leg["exercise_style"], "strike": leg["strike"], "expiry": leg["expiry"],
        "side": leg["side"], "quantity": leg["quantity"], "multiplier": leg["multiplier"],
        "premium_per_option": leg["premium"], "premium_unit": premium_unit,
    }
    if leg["dividend_schedule"]:
        d["dividend_schedule"] = [dict(x) for x in leg["dividend_schedule"]]
    if leg["greeks"]:
        d["greeks"] = dict(leg["greeks"])
        d["greek_units"] = dict(GREEK_UNITS)
    return d


def _build_serialized(p, ticker, currency, legs, spread_type, venue, counterparty, trade_id, seq):
    premium_unit = _premium_unit(venue, p["underlying_kind"])
    expiry_max = max(l["expiry"] for l in legs)
    settlement_method = _settlement_method(venue, p["underlying_kind"], p["style"])
    exchange_label = {"BBG": "OTC", "CME": "CME", "Eurex": "Eurex"}[venue]
    d = {
        "trade_id": trade_id,
        "trade_date": VALUATION_DATE,
        "underlying": ticker,
        "venue": exchange_label,
        "counterparty": counterparty,
        "currency": currency,
        "settlement_method": settlement_method,
        "settlement_date": settle(expiry_max, 1) if settlement_method == "Cash" else settle(expiry_max, 2),
        "commission": round(0.65 if venue != "CME" else 1.25, 2),
        "commission_unit": "per_contract",
    }
    legs_public = [_leg_public(l, premium_unit) for l in legs]
    if len(legs) == 1:
        leg0 = dict(legs_public[0])
        leg0.pop("leg_index", None)
        d.update(leg0)
        d["total_premium"] = round(legs[0]["premium"] * legs[0]["quantity"] * legs[0]["multiplier"], 2)
    else:
        d["spread_type"] = spread_type
        d["legs"] = legs_public
        net = 0.0
        for l in legs:
            sign = 1.0 if l["side"] == "Buy" else -1.0
            net += sign * l["premium"] * l["quantity"] * l["multiplier"]
        d["net_premium"] = round(net, 2)
    return d


def _apply_path(d, path, value):
    cur = d
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = value


def _get_path(d, path):
    cur = d
    for k in path:
        cur = cur[k]
    return cur


# ── EXCEPTION-INFO CONSTRUCTOR ──────────────────────────────────────────────

def _mk(category, field, path, wrong_value, correct_value, difference, difference_unit,
        exposure_usd, leg_index=None, greek=None, side=None):
    return {
        "category": category, "field": field, "path": path, "wrong_value": wrong_value,
        "correct_value": correct_value, "difference": difference, "difference_unit": difference_unit,
        "total_exposure_usd": exposure_usd, "leg_index": leg_index, "greek": greek, "side": side,
    }


GREEK_NAMES = ["delta", "gamma", "vega", "theta", "rho"]


# ── ERROR INJECTORS (19 categories, spec section 5) ─────────────────────────

def _leg_path(single, leg_idx0, field):
    return [field] if single else ["legs", leg_idx0, field]


def inj_price(ctx):
    legs, rng, single = ctx["legs"], ctx["rng"], len(ctx["legs"]) == 1
    i = rng.randrange(len(legs))
    correct = legs[i]["premium"]
    easy = ctx["p"]["difficulty"] == "easy"
    pct = rng.choice([0.15, 0.20, 0.25]) if easy else rng.choice([0.02, 0.03, 0.05])
    delta = round(max(correct * pct, 0.05), 2) * rng.choice([1, -1])
    wrong = round(correct + delta, 2)
    diff = round(abs(wrong - correct), 2)
    exposure = round(diff * legs[i]["quantity"] * legs[i]["multiplier"], 2)
    path = _leg_path(single, i, "premium_per_option")
    return _mk("EXC-PRICE", "premium_per_option", path, wrong, correct, diff, "per_option_premium",
               exposure, leg_index=None if single else i + 1)


def inj_prod(ctx):
    legs, rng, single = ctx["legs"], ctx["rng"], len(ctx["legs"]) == 1
    i = rng.randrange(len(legs))
    correct = legs[i]["option_type"]
    wrong = "Put" if correct == "Call" else "Call"
    exposure = round(legs[i]["quantity"] * legs[i]["multiplier"] * legs[i]["S"], 2)
    path = _leg_path(single, i, "option_type")
    return _mk("EXC-PROD", "option_type", path, wrong, correct, "Call/Put confusion", "option_type",
               exposure, leg_index=None if single else i + 1)


def inj_qty(ctx):
    legs, rng, single = ctx["legs"], ctx["rng"], len(ctx["legs"]) == 1
    i = rng.randrange(len(legs))
    correct = legs[i]["quantity"]
    easy = ctx["p"]["difficulty"] == "easy"
    pct = rng.choice([0.20, 0.30]) if easy else rng.choice([0.05, 0.10])
    delta = max(1, round(correct * pct))
    wrong = max(1, correct + delta * rng.choice([1, -1]))
    diff = abs(wrong - correct)
    exposure = round(diff * legs[i]["premium"] * legs[i]["multiplier"], 2)
    path = _leg_path(single, i, "quantity")
    return _mk("EXC-QTY", "quantity", path, wrong, correct, diff, "contracts",
               exposure, leg_index=None if single else i + 1)


def inj_ccy(ctx):
    correct = ctx["full"]["currency"]
    wrong = "USD" if correct == "EUR" else "EUR"
    legs = ctx["legs"]
    notional = sum(l["premium"] * l["quantity"] * l["multiplier"] for l in legs)
    return _mk("EXC-CCY", "currency", ["currency"], wrong, correct, "Trade currency differs", "currency",
               round(notional, 2))


def inj_cpty(ctx):
    correct = ctx["full"]["counterparty"]
    pool = [c for c in COUNTERPARTIES if c != correct]
    wrong = ctx["rng"].choice(pool)
    return _mk("EXC-CPTY", "counterparty", ["counterparty"], wrong, correct,
               "Different counterparty entity", "entity", None)


def inj_comm(ctx):
    rng = ctx["rng"]
    correct = ctx["full"]["commission"]
    bump = rng.choice([0.10, 0.15, 0.25]) if ctx["venue"] != "CME" else rng.choice([0.25, 0.50])
    wrong = round(correct + bump, 2)
    diff = round(abs(wrong - correct), 2)
    total_qty = sum(l["quantity"] for l in ctx["legs"])
    exposure = round(diff * total_qty, 2)
    return _mk("EXC-COMM", "commission", ["commission"], wrong, correct, diff, "per_contract", exposure)


def inj_tdate(ctx):
    rng = ctx["rng"]
    correct = ctx["full"]["trade_date"]
    shift = rng.choice([1, 2])
    wrong = to_bd(add_days(correct, shift))
    return _mk("EXC-TDATE", "trade_date", ["trade_date"], wrong, correct,
               f"{shift} business day(s)", "days", None)


def inj_sdate(ctx):
    rng = ctx["rng"]
    correct = ctx["full"]["settlement_date"]
    shift = rng.choice([1, 2])
    wrong = to_bd(add_days(correct, shift))
    return _mk("EXC-SDATE", "settlement_date", ["settlement_date"], wrong, correct,
               f"{shift} business day(s)", "days", None)


def inj_greek(ctx):
    """Mutate one displayed greek beyond its scoring tolerance (spec section 6),
    while keeping the wrong value sign-sane — a bad print/typo is plausible,
    a negative gamma or a call delta > 1 is not ("never an impossible value",
    HARD REQUIREMENTS). Delta/gamma/vega are clamped to their valid range;
    theta/rho have no hard sign constraint so are left unclamped."""
    legs, rng, single = ctx["legs"], ctx["rng"], len(ctx["legs"]) == 1
    candidates = [i for i, l in enumerate(legs) if l["greeks"]]
    i = rng.choice(candidates)
    gname = rng.choice(GREEK_NAMES)
    leg = legs[i]
    correct = leg["greeks"][gname]
    tol = GREEK_TOL[gname]
    if tol["method"] == "absolute":
        bump = tol["tolerance"] * rng.choice([3, 4, 5])
    else:
        bump = max(tol["abs_floor"] * rng.choice([3, 4]), abs(correct) * tol["tolerance"] * rng.choice([3, 4]))
    wrong = correct + bump * rng.choice([1, -1])

    if gname in ("gamma", "vega"):
        floor = max(1e-5, abs(correct) * 0.05)
        wrong = max(wrong, floor)
        # if the sign-sanity clamp collapsed the move back inside the section-6
        # detection threshold, re-push upward — always sign-safe for gamma/vega
        # (gate-7 blocker: sub-tolerance gamma "errors" were pure noise, v1.0.1)
        thresh = (tol["tolerance"] if tol["method"] == "absolute"
                  else max(abs(correct) * tol["tolerance"], tol["abs_floor"]))
        if abs(wrong - correct) <= 2 * thresh:
            wrong = correct + bump
    elif gname == "delta":
        if leg["option_type"] == "Call":
            wrong = min(max(wrong, 0.001), 0.999)
        else:
            wrong = max(min(wrong, -0.001), -0.999)
        # if clamping collapsed the move back inside tolerance, push to the far clamp edge
        if abs(wrong - correct) <= tol["tolerance"]:
            wrong = 0.999 if (leg["option_type"] == "Call" and correct < 0.5) else \
                    (0.001 if leg["option_type"] == "Call" else
                     (-0.999 if correct > -0.5 else -0.001))

    wrong = round(wrong, 5)
    diff = round(abs(wrong - correct), 5)
    _det = (tol["tolerance"] if tol["method"] == "absolute"
            else max(abs(correct) * tol["tolerance"], tol["abs_floor"]))
    assert diff > _det, f"injected {gname} error {diff} not above detection threshold {_det}"
    path = _leg_path(single, i, "greeks") + [gname]
    return _mk("EXC-GREEK", f"greeks.{gname}", path, wrong, correct, diff, "greek_units",
               None, leg_index=None if single else i + 1, greek=gname)


def inj_strike(ctx):
    legs, rng, single = ctx["legs"], ctx["rng"], len(ctx["legs"]) == 1
    i = rng.randrange(len(legs))
    correct = legs[i]["strike"]
    tick = max(0.5, round(correct * 0.01, 2))
    bump = tick * rng.choice([2, 3, 5, 10])
    wrong = round(correct + bump * rng.choice([1, -1]), 2)
    diff = round(abs(wrong - correct), 2)
    exposure = round(diff * legs[i]["quantity"] * legs[i]["multiplier"], 2)
    path = _leg_path(single, i, "strike")
    return _mk("EXC-STRIKE", "strike", path, wrong, correct, diff, "strike_points",
               exposure, leg_index=None if single else i + 1)


def inj_notional(ctx):
    rng = ctx["rng"]
    full = ctx["full"]
    field = "total_premium" if "total_premium" in full else "net_premium"
    correct = full[field]
    bump = round(rng.choice([500, 1500, 3500, 7500]) * rng.choice([1, -1]), 2)
    wrong = round(correct + bump, 2)
    diff = round(abs(wrong - correct), 2)
    return _mk("EXC-NOTIONAL", field, [field], wrong, correct, diff, "notional_usd", diff)


def inj_div(ctx):
    legs, rng, single = ctx["legs"], ctx["rng"], len(ctx["legs"]) == 1
    candidates = [i for i, l in enumerate(legs) if l["dividend_schedule"]]
    i = rng.choice(candidates)
    correct = legs[i]["dividend_schedule"][0]["amount"]
    bump_pct = rng.choice([0.25, 0.35, 0.5])
    wrong = round(correct * (1 + bump_pct * rng.choice([1, -1])), 4)
    diff = round(abs(wrong - correct), 4)
    exposure = round(diff * legs[i]["multiplier"] * legs[i]["quantity"], 2)
    path = _leg_path(single, i, "dividend_schedule") + [0, "amount"]
    return _mk("EXC-DIV", "dividend_schedule[0].amount", path, wrong, correct, diff, "per_share",
               exposure, leg_index=None if single else i + 1)


def inj_settle(ctx):
    correct = ctx["full"]["settlement_method"]
    wrong = "Physical" if correct == "Cash" else "Cash"
    legs = ctx["legs"]
    notional = sum(l["premium"] * l["quantity"] * l["multiplier"] for l in legs)
    return _mk("EXC-SETTLE", "settlement_method", ["settlement_method"], wrong, correct,
               "Cash vs Physical settlement method differs", "settlement_method", round(notional, 2))


def inj_corpact(ctx):
    """Back office failed to update contract size after a split (Eurex ratio
    method: contract size / R-factor). Strike stays correct and matched on
    both sides; only the multiplier (deliverable size) is stale on one side —
    a single, cleanly-declared field, not a bundled strike+multiplier pair."""
    legs, rng, single = ctx["legs"], ctx["rng"], len(ctx["legs"]) == 1
    i = rng.randrange(len(legs))
    leg = legs[i]
    ratio = rng.choice([2.0, 3.0, 1.5])
    stale_mult = round(leg["multiplier"] / ratio, 4)
    path = _leg_path(single, i, "multiplier")
    exposure = round(abs(leg["multiplier"] - stale_mult) * leg["quantity"] * leg["S"], 2)
    return _mk("EXC-CORPACT", "multiplier", path, stale_mult, leg["multiplier"],
               round(abs(leg["multiplier"] - stale_mult), 4), "contract_multiplier", exposure,
               leg_index=None if single else i + 1)


def inj_leg(ctx):
    legs, rng = ctx["legs"], ctx["rng"]
    i = rng.randrange(len(legs))
    correct = legs[i]["side"]
    wrong = "Sell" if correct == "Buy" else "Buy"
    exposure = round(legs[i]["premium"] * legs[i]["quantity"] * legs[i]["multiplier"], 2)
    path = ["legs", i, "side"]
    return _mk("EXC-LEG", "side", path, wrong, correct, "Buy/Sell attribution swapped on leg",
               "side", exposure, leg_index=i + 1)


def inj_expiry(ctx):
    legs, rng = ctx["legs"], ctx["rng"]
    i = rng.randrange(len(legs))
    correct = legs[i]["expiry"]
    shift_days = rng.choice([-14, -7, 7, 14, 30])
    wrong = to_bd(add_days(correct, shift_days))
    exposure = round(legs[i]["premium"] * legs[i]["quantity"] * legs[i]["multiplier"], 2)
    path = ["legs", i, "expiry"]
    near_dated = legs[i]["T"] < (45.0 / 365.0)
    missed_exercise = near_dated and legs[i]["exercise_style"] == "American" and shift_days < 0
    info = _mk("EXC-EXPIRY", "expiry", path, wrong, correct, f"{abs(shift_days)} calendar days",
               "days", exposure, leg_index=i + 1)
    info["_missed_exercise_risk"] = missed_exercise
    return info


def inj_style(ctx):
    legs, single = ctx["legs"], len(ctx["legs"]) == 1
    i = 0
    correct = legs[i]["exercise_style"]
    wrong = "European" if correct == "American" else "American"
    exposure = round(legs[i]["premium"] * legs[i]["quantity"] * legs[i]["multiplier"], 2)
    path = _leg_path(single, i, "exercise_style")
    return _mk("EXC-STYLE", "exercise_style", path, wrong, correct,
               "American/European exercise style confused", "exercise_style", exposure,
               leg_index=None if single else i + 1)


def inj_ratio(ctx):
    legs, rng = ctx["legs"], ctx["rng"]
    i = 1  # the "ratio" leg (leg 2) carries the multiple
    correct = legs[i]["quantity"]
    wrong = legs[0]["quantity"]  # mis-shown as a flat 1x1 instead of the true ratio
    diff = abs(correct - wrong)
    exposure = round(diff * legs[i]["premium"] * legs[i]["multiplier"], 2)
    path = ["legs", i, "quantity"]
    return _mk("EXC-RATIO", "quantity", path, wrong, correct, diff, "contracts", exposure, leg_index=i + 1)


def inj_mult(ctx):
    legs, rng, single = ctx["legs"], ctx["rng"], len(ctx["legs"]) == 1
    i = 0
    correct = legs[i]["multiplier"]
    other = [x["mult"] for x in CME_PRODUCTS if x["mult"] != correct]
    wrong = ctx["rng"].choice(other) if other else correct * 2
    diff = abs(correct - wrong)
    exposure = round(diff * legs[i]["quantity"] * legs[i]["premium"], 2)
    path = _leg_path(single, i, "multiplier")
    return _mk("EXC-MULT", "multiplier", path, wrong, correct, diff, "contract_multiplier",
               exposure, leg_index=None if single else i + 1)


CATEGORY_INJECTORS = {
    "EXC-PRICE": inj_price, "EXC-PROD": inj_prod, "EXC-QTY": inj_qty, "EXC-CCY": inj_ccy,
    "EXC-CPTY": inj_cpty, "EXC-COMM": inj_comm, "EXC-TDATE": inj_tdate, "EXC-SDATE": inj_sdate,
    "EXC-GREEK": inj_greek, "EXC-STRIKE": inj_strike, "EXC-NOTIONAL": inj_notional, "EXC-DIV": inj_div,
    "EXC-SETTLE": inj_settle, "EXC-CORPACT": inj_corpact, "EXC-LEG": inj_leg, "EXC-EXPIRY": inj_expiry,
    "EXC-STYLE": inj_style, "EXC-RATIO": inj_ratio, "EXC-MULT": inj_mult,
}

SECONDARY_COMPATIBLE = ["EXC-COMM", "EXC-CPTY", "EXC-TDATE", "EXC-SDATE", "EXC-QTY", "EXC-PRICE", "EXC-STRIKE"]


# ── RISK LEVEL (spec section 10): risk_level = max(exposure_band, type_floor) ─

def _exposure_band(exposure_usd):
    if exposure_usd is None:
        return 3
    e = abs(exposure_usd)
    if e < 10000:
        return 2
    if e < 250000:
        return 3
    if e < 2000000:
        return 4
    return 5


def _type_floor(category, missed_exercise_risk=False):
    if category == "EXC-PROD":
        return 5
    if category == "EXC-CORPACT":
        return 5   # physical single-name settlement/exercise miss, by construction (single-name only)
    if category == "EXC-EXPIRY":
        return 5 if missed_exercise_risk else 4
    if category in ("EXC-SETTLE", "EXC-STYLE", "EXC-CCY"):
        return 4
    return 1


def _risk_level(category, exposure_usd, missed_exercise_risk=False):
    if category is None:
        return 1
    return min(5, max(_exposure_band(exposure_usd), _type_floor(category, missed_exercise_risk)))


# ── SCORING CRITERIA ─────────────────────────────────────────────────────────

SC_CLEAN = {"exception_detection": "exact_match", "fabrication_check": "binary"}

NUMERIC_TOLERANCE = {
    "EXC-PRICE": 0.01, "EXC-STRIKE": 0.0, "EXC-QTY": 0.0, "EXC-NOTIONAL": 1000.0,
    "EXC-DIV": 0.005, "EXC-COMM": 0.01, "EXC-MULT": 0.0,
}


def _build_scoring(primary_cat, secondary_cat, need_greeks, multi_leg):
    sc = {
        "exception_detection": "exact_match", "category_identification": "exact_match",
        "field_identification": "exact_match", "action_recommendation": "semantic_match",
        "fabrication_check": "binary",
    }
    tol = NUMERIC_TOLERANCE.get(primary_cat)
    if tol is not None:
        sc["numeric_values"] = {"method": "numeric_match", "tolerance": tol}
    sc["exposure_calculation"] = {"method": "numeric_match", "tolerance": 1000.0}
    if primary_cat == "EXC-GREEK":
        sc["greeks"] = {"greek": None, "method": None, "tolerance": None, "abs_floor": None}  # filled by caller
    if multi_leg:
        sc["leg_matching"] = {"method": "exact",
                               "required_for_credit": primary_cat in ("EXC-LEG", "EXC-RATIO", "EXC-EXPIRY")}
    if secondary_cat:
        sc["both_exceptions_identified"] = "required"
    return sc


# ── GENERATION METADATA (non-prompt) ────────────────────────────────────────

def _build_metadata(p, ticker, legs, r, primary, secondary, seq, currency):
    underlying_kind = p["underlying_kind"]
    spot_key = "futures_price" if underlying_kind == "future" else "spot"
    valuation_inputs = {
        spot_key: round(legs[0]["S"], 4),
        "sigma": round(legs[0]["sigma"], 4),
        "r": r,
        "day_count": DAY_COUNT,
        "valuation_date": VALUATION_DATE,
    }
    if underlying_kind == "index":
        valuation_inputs["q"] = legs[0]["q"]
    if underlying_kind in ("single_name", "single_stock"):
        valuation_inputs["dividend_schedule"] = [dict(x) for x in legs[0]["dividend_schedule"]]
    if p["style"] == "American":
        valuation_inputs["binomial_steps"] = BINOMIAL_STEPS

    pricing_model = {
        ("index", "European"): "BSM (continuous q)",
        ("single_name", "European"): "BSM-on-forward (escrowed discrete dividends)",
        ("single_name", "American"): "CRR binomial >=1500 steps (escrowed discrete dividends)",
        ("single_stock", "American"): "CRR binomial >=1500 steps (escrowed discrete dividends)",
        ("future", "American"): "CRR-on-future, carry=0, >=1500 steps",
        ("future", "European"): "Black-76",
    }[(underlying_kind, p["style"])]

    # full-precision model values — the confirm prints display-rounded copies,
    # but gate 2 recomputes against these at 1e-6 rel
    correct_values = {
        f"leg_{l['leg_index'] or 1}_premium": l["premium_exact"] for l in legs
    }
    for l in legs:
        if l["greeks_exact"]:
            correct_values[f"leg_{l['leg_index'] or 1}_greeks"] = l["greeks_exact"]

    # spec section 7: injected_error is a single object {field, side, kind,
    # magnitude}; leg_index locates the field inside legs[] on multi-leg cases
    # (null for single-leg / whole-trade fields). Dual-exception cases carry
    # the second injection in injected_error_secondary, same shape.
    injected_error = None
    injected_error_secondary = None
    if primary:
        injected_error = {
            "field": primary["field"], "side": primary["side"],
            "kind": primary["category"], "magnitude": primary["difference"],
            "leg_index": primary["leg_index"],
        }
        if secondary:
            injected_error_secondary = {
                "field": secondary["field"], "side": secondary["side"],
                "kind": secondary["category"], "magnitude": secondary["difference"],
                "leg_index": secondary["leg_index"],
            }

    distribution_cell = {
        "venue": p["venue"], "underlying_kind": underlying_kind, "leg": p["leg"],
        "is_call": p["is_call"], "difficulty": p["difficulty"], "category": p["category"],
        "dual_exception": p["dual_exception"],
    }

    return {
        "generator_version": GENERATOR_VERSION, "pricing_model": pricing_model,
        "valuation_inputs": valuation_inputs, "correct_values": correct_values,
        "injected_error": injected_error,
        "injected_error_secondary": injected_error_secondary,
        "distribution_cell": distribution_cell,
    }


# ── MAIN CASE RENDERER ──────────────────────────────────────────────────────

def _next_side(rng):
    return rng.choice(["counterparty", "internal"])


def _apply_exception(cp, int_, side, path, value):
    target = cp if side == "counterparty" else int_
    _apply_path(target, path, value)


AGGREGATE_TRIGGER_FIELDS = {"premium_per_option", "quantity", "multiplier", "side"}


def _recompute_aggregate(d, single):
    """Keep total_premium/net_premium internally consistent with this side's
    own leg data after a leg-level mutation (premium/qty/multiplier/side).
    Never called for EXC-NOTIONAL, where the aggregate field *is* the error."""
    if single:
        d["total_premium"] = round(d["premium_per_option"] * d["quantity"] * d["multiplier"], 2)
    else:
        net = 0.0
        for l in d["legs"]:
            sign = 1.0 if l["side"] == "Buy" else -1.0
            net += sign * l["premium_per_option"] * l["quantity"] * l["multiplier"]
        d["net_premium"] = round(net, 2)


def _maybe_recompute(cp, int_, side, info, single):
    if info["path"][-1] in AGGREGATE_TRIGGER_FIELDS:
        target = cp if side == "counterparty" else int_
        _recompute_aggregate(target, single)


def render_case(p):
    rng = random.Random(MASTER_SEED * 97 + p["seq"])
    trap_type = _select_traps().get(p["seq"]) if p["is_clean"] else None
    need_greeks = (p["category"] == "EXC-GREEK") or (trap_type in ("deep_itm", "neg_theta"))

    ticker, currency, r, legs, spread_type, ref = _build_legs(p, rng, need_greeks, trap_type)
    # redraw until every leg clears the premium floor: a 0.00-premium option on
    # a confirm is not market-plausible and degenerates greeks to all-zero
    # (deterministic — retries continue the same seeded rng stream)
    for _attempt in range(60):
        if all(l["premium_exact"] >= PREMIUM_FLOOR for l in legs):
            break
        ticker, currency, r, legs, spread_type, ref = _build_legs(p, rng, need_greeks, trap_type)
    else:
        raise AssertionError(f"seq {p['seq']}: no leg draw above premium floor after 60 attempts")
    counterparty = rng.choice(COUNTERPARTIES if p["venue"] != "CME" else CLEARING_BROKERS)
    trade_id = f"OPT-{p['seq']:03d}-{p['venue']}"
    full = _build_serialized(p, ticker, currency, legs, spread_type, p["venue"], counterparty, trade_id, p["seq"])
    single = len(legs) == 1

    cp = copy.deepcopy(full)
    int_ = copy.deepcopy(full)

    primary = secondary = None
    missed_exercise_risk = False
    label_swapped = False
    if not p["is_clean"]:
        ctx = {"p": p, "legs": legs, "full": full, "rng": rng, "venue": p["venue"], "currency": currency}
        primary = CATEGORY_INJECTORS[p["category"]](ctx)
        if p["category"] == "EXC-GREEK":
            gname = primary["greek"]
            tol = GREEK_TOL[gname]
            primary_greek_sc = {"greek": gname, "method": tol["method"], "tolerance": tol["tolerance"],
                                 "abs_floor": tol.get("abs_floor")}
        else:
            primary_greek_sc = None
        if p["category"] == "EXC-EXPIRY":
            missed_exercise_risk = primary.get("_missed_exercise_risk", False)

        side = _next_side(rng)
        primary["side"] = side
        _apply_exception(cp, int_, side, primary["path"], primary["wrong_value"])
        if "_extra_path" in primary:
            _apply_exception(cp, int_, side, primary["_extra_path"], primary["_extra_wrong"])
        _maybe_recompute(cp, int_, side, primary, single)

        if p["dual_exception"]:
            candidates = [c for c in SECONDARY_COMPATIBLE if c != p["category"]]
            rng.shuffle(candidates)
            for cand in candidates:
                ctx2 = {"p": p, "legs": legs, "full": full, "rng": rng, "venue": p["venue"], "currency": currency}
                try:
                    cand_info = CATEGORY_INJECTORS[cand](ctx2)
                except (ValueError, IndexError):
                    continue
                if cand_info["path"] == primary["path"]:
                    continue
                secondary = cand_info
                side2 = _next_side(rng)
                secondary["side"] = side2
                _apply_exception(cp, int_, side2, secondary["path"], secondary["wrong_value"])
                _maybe_recompute(cp, int_, side2, secondary, single)
                break

        # v1.0.2: the primary label belongs to the higher-risk break per the
        # section-10 rule (exposure band + type criticality floor); ties keep
        # the planned order. Both injections and every RNG draw are already
        # done, so only the ground-truth labels swap — non-dual cases and RNG
        # streams are untouched.
        if secondary is not None and (
                _risk_level(secondary["category"], secondary["total_exposure_usd"])
                > _risk_level(primary["category"], primary["total_exposure_usd"],
                              missed_exercise_risk)):
            primary, secondary = secondary, primary
            label_swapped = True
    else:
        primary_greek_sc = None

    if single:
        cp.pop("legs", None); int_.pop("legs", None)
        if "net_premium" in cp:
            cp["total_premium"] = cp.pop("net_premium")
        if "net_premium" in int_:
            int_["total_premium"] = int_.pop("net_premium")

    int_["account"] = "FUND-A-OPTIONS"
    int_["book"] = f"{p['venue']}-OPTIONS"
    int_["status"] = "Unconfirmed"

    multi_leg = not single
    primary_cat = primary["category"] if primary else p["category"]
    exposure = primary["total_exposure_usd"] if primary else None
    risk = _risk_level(primary_cat, exposure, missed_exercise_risk)

    fms = list(CAT_FAILURE_MODES.get(primary_cat, [FM_GENERIC_BREAK])) if primary else [FM_FALSE_POSITIVE]
    if secondary:
        fms = sorted(set(fms + [FM_DUAL_EXCEPTION]))
    if multi_leg and primary_cat in ("EXC-LEG", "EXC-RATIO", "EXC-EXPIRY"):
        fms = sorted(set(fms + [FM_LEG_ATTRIBUTION]))
    if not primary:
        # clean cases: tag the specific temptation the case presents, not just
        # generic false-positive resistance
        if trap_type == "split":
            fms.append(FM_CORPACT_LOGIC)
        if need_greeks:
            fms.append(FM_GREEKS_UNIT_SIGN)
        if multi_leg:
            fms.append(FM_LEG_ATTRIBUTION)
        if p["venue"] == "CME":
            fms.append(FM_FUTURES_VS_SPOT)
        if any(l.get("dividend_schedule") for l in legs):
            fms.append(FM_DIVIDEND_TREATMENT)
        fms = sorted(set(fms))
    elif p["difficulty"] == "complex" and len(fms) < 2:
        fms = sorted(set(fms + ([FM_FUTURES_VS_SPOT] if p["venue"] == "CME"
                                else [FM_LEG_ATTRIBUTION] if multi_leg
                                else [FM_DIVIDEND_TREATMENT] if any(l.get("dividend_schedule") for l in legs)
                                else [FM_MAGNITUDE])))

    scoring = SC_CLEAN if p["is_clean"] else _build_scoring(primary_cat, secondary["category"] if secondary else None,
                                                              need_greeks, multi_leg)
    if primary_greek_sc:
        scoring["greeks"] = primary_greek_sc

    def gt_exception(info, side):
        return {
            "category": info["category"], "field": info["field"],
            "counterparty_value": info["wrong_value"] if side == "counterparty" else info["correct_value"],
            "internal_value": info["wrong_value"] if side == "internal" else info["correct_value"],
            "difference": info["difference"], "difference_unit": info["difference_unit"],
            "total_exposure_usd": info["total_exposure_usd"],
            "leg_index": info["leg_index"], "greek": info["greek"],
        }

    if p["is_clean"]:
        ground_truth = {
            "exception_exists": False, "primary_exception": None, "secondary_exception": None,
            "recommended_action": "No action required. Confirmation matched.",
            "escalation_required": False, "escalation_target": None,
            "human_review_required": False, "severity": 1, "confidence": "definitive",
        }
    else:
        gt_primary = gt_exception(primary, primary["side"])
        gt_secondary = gt_exception(secondary, secondary["side"]) if secondary else None
        escalation_required = risk >= 3
        action = f"{primary['category']} discrepancy on {primary['field']}: counterparty={gt_primary['counterparty_value']} vs internal={gt_primary['internal_value']}."
        if secondary:
            action += f" Secondary {secondary['category']} discrepancy on {secondary['field']} also present."
        action += " Escalate; do not settle until resolved." if escalation_required else " Log and verify with counterparty."
        ground_truth = {
            "exception_exists": True, "primary_exception": gt_primary, "secondary_exception": gt_secondary,
            "recommended_action": action, "escalation_required": escalation_required,
            "escalation_target": "Trading desk, operations" if escalation_required else "Operations",
            "human_review_required": True, "severity": risk, "confidence": "definitive",
        }

    metadata = _build_metadata(p, ticker, legs, r, primary, secondary, p["seq"], currency)

    trap_note = ""
    if trap_type == "split":
        trap_note = " Strike/multiplier reflect a genuine prior corporate-action adjustment (both sides agree) — not an error."
    elif trap_type == "deep_itm":
        trap_note = " Deep in-the-money option; delta near +/-1 is expected and correct, not a data error."
    elif trap_type == "neg_theta":
        trap_note = " Negative theta is expected for a long option position, not a data error."

    venue_desc = {"BBG": "Bloomberg-confirmed OTC", "CME": "CME-listed", "Eurex": "Eurex-listed"}[p["venue"]]
    leg_desc = "multi-leg spread" if multi_leg else "single-leg"
    scenario = (f"{venue_desc} {leg_desc} equity option{'s' if multi_leg else ''} on {ticker}. "
                + ("Clean confirmation match." if p["is_clean"] else f"{p['category']} discrepancy between counterparty confirmation and internal record."))
    business_context = (f"{p['venue']} options desk confirmation matching. {p['underlying_kind'].replace('_', ' ').title()} "
                         f"underlying, {p['style']} exercise.{trap_note}")
    reviewer_notes = (f"Distribution cell: venue={p['venue']}, underlying_kind={p['underlying_kind']}, "
                       f"leg={p['leg']}, difficulty={p['difficulty']}, category={p['category']}."
                       + (f" Dual exception with {secondary['category']}." if secondary else "")
                       + (" Primary/secondary assigned by section-10 risk ordering (planned primary demoted)."
                          if label_swapped else "") + trap_note)

    case = {
        "case_id": p["case_id"], "benchmark_version": BENCHMARK_VERSION,
        "workflow": "trade_confirmation_exception", "asset_class": "options",
        "difficulty": p["difficulty"], "risk_level": risk,
        "scenario_description": scenario, "business_context": business_context,
        "input": {"counterparty_confirmation": cp, "internal_record": int_},
        "ground_truth": ground_truth, "scoring_criteria": scoring,
        "failure_modes": fms, "reviewer_notes": reviewer_notes,
        "references": ["D003-sourcing.md public venue/contract-spec references"],
        "version_history": [
            {"version": "1.0", "date": "2026-07-09", "change": "Initial build (QA gates 1-6)"},
            {"version": "1.0.1", "date": "2026-07-10",
             "change": "Gate-7 fixes: split traps single-name only; greek errors above detection "
                       "threshold; CME settlement uniform; multiplier domain invariant"},
            {"version": "1.0.2", "date": "2026-07-11",
             "change": "Dual-exception primary/secondary assigned by section-10 risk ordering "
                       "(2 cases relabelled: 012 NOTIONAL->TDATE, 161 GREEK->STRIKE); risk_level, "
                       "escalation, scoring criteria re-derived from the ordered primary"},
        ],
        "generation_metadata": metadata,
    }
    return case


def render_batch(batch_num):
    return [render_case(p) for p in plan_for_batch(batch_num)]


# ── QA GATE 1: in-generator invariants, run on every emitted case ──────────

LEAKAGE_SUBSTRINGS = ("correct_", "error_", "injected", "ground_truth")


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def qa_assert_case(case):
    cid = case["case_id"]
    gt = case["ground_truth"]

    # 1. exactly one primary exception (or none for clean)
    if gt["exception_exists"]:
        assert gt["primary_exception"] is not None, f"{cid}: exception_exists but no primary_exception"
    else:
        assert gt["primary_exception"] is None and gt["secondary_exception"] is None, \
            f"{cid}: clean case must have no primary/secondary exception"
    if gt["secondary_exception"] is not None:
        assert gt["primary_exception"] is not None, f"{cid}: secondary without primary"

    # 2. input contains ONLY counterparty_confirmation + internal_record, no leakage
    assert set(case["input"].keys()) == {"counterparty_confirmation", "internal_record"}, \
        f"{cid}: input has extra top-level keys"
    for k in _walk_keys(case["input"]):
        low = k.lower()
        assert not any(s in low for s in LEAKAGE_SUBSTRINGS), f"{cid}: leakage-like key '{k}' in input"

    # 3. generation_metadata present, non-prompt, with required sub-fields
    meta = case.get("generation_metadata")
    assert meta is not None, f"{cid}: missing generation_metadata"
    for key in ("generator_version", "pricing_model", "valuation_inputs", "correct_values",
                "injected_error", "distribution_cell"):
        assert key in meta, f"{cid}: generation_metadata missing '{key}'"
    vi = meta["valuation_inputs"]
    assert vi.get("day_count") == "ACT/365F", f"{cid}: day_count must be ACT/365F"
    assert vi.get("valuation_date") == VALUATION_DATE, f"{cid}: valuation_date mismatch"

    # 4. venue <-> terms validity (checked against the correct/ground-truth side;
    #    EXC-STYLE cases deliberately show the wrong style on one side, so that
    #    field is exempted here — it's the thing under test, not a generator bug).
    cell = meta["distribution_cell"]
    venue, uk = cell["venue"], cell["underlying_kind"]
    style_is_under_test = gt["exception_exists"] and gt["primary_exception"]["field"] == "exercise_style"
    if venue == "CME":
        assert uk == "future", f"{cid}: CME must be options-on-futures only (O1)"
    if uk in ("future", "index"):
        # futures and indices never have corporate actions: multiplier must stay
        # in the venue base domain on BOTH records (gate-7 blocker, v1.0.1)
        legal = {5.0, 10.0, 20.0, 50.0, 100.0}
        for rec in (case["input"]["counterparty_confirmation"], case["input"]["internal_record"]):
            for leg in (rec.get("legs") or [rec]):
                mult = leg.get("multiplier")
                assert mult in legal, f"{cid}: {uk} multiplier {mult} outside venue base domain"
    if venue == "Eurex" and uk == "single_stock" and not style_is_under_test:
        # American, physical, 100-share, EUR — checked on internal_record, which is
        # only the "wrong" side for categories other than exercise_style (excluded above).
        rec = case["input"]["internal_record"]
        style = rec.get("exercise_style") or (rec.get("legs", [{}])[0].get("exercise_style"))
        assert style == "American", f"{cid}: Eurex single-stock must be American"
        mult = rec.get("multiplier") or (rec.get("legs", [{}])[0].get("multiplier"))
        # multiplier may be corpact-adjusted; 100 is the base convention, tolerate integer multiples/divisors
        assert mult and mult > 0, f"{cid}: Eurex single-stock multiplier must be positive"

    # 5. greek sign sanity, wherever greeks are displayed (both sides — inj_greek
    #    clamps injected wrong values to stay sign-sane, since an impossible value
    #    like negative gamma is disallowed by the HARD REQUIREMENTS).
    for rec in (case["input"]["counterparty_confirmation"], case["input"]["internal_record"]):
        leg_dicts = rec.get("legs") if "legs" in rec else [rec]
        for leg in leg_dicts:
            g = leg.get("greeks")
            if not g:
                continue
            otype = leg.get("option_type")
            eps = 1e-4
            if otype == "Call":
                assert -eps <= g["delta"] <= 1.0 + eps, f"{cid}: call delta out of [0,1] : {g['delta']}"
            elif otype == "Put":
                assert -1.0 - eps <= g["delta"] <= eps, f"{cid}: put delta out of [-1,0] : {g['delta']}"
            assert g["gamma"] >= -1e-6, f"{cid}: gamma < 0 : {g['gamma']}"
            assert g["vega"] >= -1e-6, f"{cid}: vega < 0 : {g['vega']}"

    # 6. risk_level rule
    assert 1 <= case["risk_level"] <= 5, f"{cid}: risk_level out of range"
    if not gt["exception_exists"]:
        assert case["risk_level"] == 1, f"{cid}: clean case must be risk_level 1"

    # 7. schema-shape basics
    assert case["case_id"].startswith("AAL-D-003-"), f"{cid}: bad case_id"
    assert case["asset_class"] == "options"
    assert case["workflow"] == "trade_confirmation_exception"
    assert case["difficulty"] in ("easy", "moderate", "complex")
    assert len(case["failure_modes"]) >= 1
    return True
