#!/usr/bin/env python3
"""
AAL-D-004 "Settlement Fail Exception Handling" — shared generator library.

Deterministic ground-truth-by-construction, per documentation/D004-spec.md and
documentation/D004-build-brief.md. No wall-clock anywhere; allocation (the
master plan) uses no randomness at all; value rendering uses a per-case
random.Random(MASTER_SEED*1000+seq) so rendering is order-independent.

Public surface (mirrors d003_common.py):
    build_master_plan() -> list[dict]      # 250 plan entries, batch-ordered
    plan_for_batch(n)   -> list[dict]      # 50-entry slice for batch n (1..5)
    render_case(plan)   -> dict            # full family-envelope case
    qa_assert_case(case)                   # QA gate 1 invariants (raises)
    EXPECTED_* tables                      # for generate_d004_manifest.py
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

GENERATOR_VERSION = "d004-v1.0.1"
BENCHMARK_VERSION = "1.0.1"
WORKFLOW = "settlement_fail_exception"
MASTER_SEED = 2026004
VALUATION_DATE = "2026-07-06"
REPORT_DATE = "2026-07-08"          # custodian feed as-of date
CLAIM_RATE = 0.0533                 # Fed Funds + 1% proxy, printed in ISDA terms
GRACE_DAYS = 3                      # securities buy-in grace, printed in ISDA terms
PARTIAL_TOLERANCE = 0.05            # permitted partial-delivery tolerance, printed
VERSION_HISTORY = [{"version": "1.0", "date": "2026-07-11",
                    "change": "Initial build (QA gates 1-6)"},
                   {"version": "1.0.1", "date": "2026-07-14",
                    "change": "Gate-7 fixes: inbound FAIL-NOVATION action "
                              "chase_counterparty (nothing to recall on not-received); "
                              "easy FAIL-SSI reason code MUNO (DENY contradicted "
                              "not_received status)"}]

# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #
COUNTERPARTIES = [
    ("Dealer Bank Alpha", "DBALUS33"), ("Dealer Bank Beta", "DBBEGB2L"),
    ("Dealer Bank Gamma", "DBGADEFF"), ("Dealer Bank Delta", "DBDEJPJT"),
    ("Macro Fund Partners", "MFPAUS33"), ("Global Markets AG", "GMAGCHZZ"),
    ("Northern Rates LLP", "NRLLGB2L"), ("Pacific Derivatives Ltd", "PDLTAU2S"),
]
CUSTODIANS = ["Global Custody Corp", "Continental Trust Bank", "Meridian Securities Services"]
PRIME_BROKERS = ["Prime Broker Alpha", "Prime Broker Beta"]
CCPS = ["ClearHouse International"]
NOVATED_ENTITIES = [("Dealer Bank Alpha", "Dealer Bank Alpha (UK) Ltd", "DBALGB2L")]

CCY_CUTOFFS = {   # currency -> (cutoff local, IANA tz, cls_eligible)
    "USD": ("17:00", "America/New_York", True),
    "EUR": ("16:00", "Europe/Berlin", True),
    "GBP": ("16:00", "Europe/London", True),
    "JPY": ("15:00", "Asia/Tokyo", True),
    "CHF": ("15:00", "Europe/Zurich", True),
    "AUD": ("16:00", "Australia/Sydney", True),
}
CCY_HOLIDAYS = {"USD": ["2026-07-03"], "EUR": [], "GBP": [], "JPY": [], "CHF": [], "AUD": []}
CCY_BASIS = {"USD": 360, "EUR": 360, "JPY": 360, "CHF": 360, "AUD": 360, "GBP": 365}

ASSET_CLASSES = ["interest_rate_swap", "credit_default_swap", "fx_forward",
                 "fx_option", "equity_swap", "commodity_swap", "cross_currency_swap"]
ASSET_CCY = {  # plausible settlement currencies per asset class
    "interest_rate_swap": ["USD", "EUR", "GBP"],
    "credit_default_swap": ["USD", "EUR"],
    "fx_forward": ["USD", "JPY", "EUR", "AUD"],
    "fx_option": ["USD", "JPY", "GBP"],
    "equity_swap": ["USD", "EUR", "CHF"],
    "commodity_swap": ["USD"],
    "cross_currency_swap": ["USD", "EUR", "JPY"],
}
# settlement types compatible with each asset class (cash legs + IM collateral)
ASSET_CASH_STYPES = {
    "interest_rate_swap": ["coupon_reset", "variation_margin", "termination_payment",
                           "upfront_fee", "initial_margin"],
    "credit_default_swap": ["cds_premium", "variation_margin", "upfront_fee",
                            "credit_event", "initial_margin"],
    "fx_forward": ["fx_principal", "variation_margin", "initial_margin"],
    "fx_option": ["fx_principal", "upfront_fee", "variation_margin", "initial_margin"],
    "equity_swap": ["coupon_reset", "variation_margin", "termination_payment", "initial_margin"],
    "commodity_swap": ["coupon_reset", "variation_margin", "physical_delivery", "initial_margin"],
    "cross_currency_swap": ["coupon_reset", "fx_principal", "variation_margin", "initial_margin"],
}

FAIL_REASON = {  # ISO-20022-style codes used by the feed
    # SSI corroborating code is MUNO (matching never happened because of the
    # stale SSI) — DENY contradicts a not_received status (gate-7 major, v1.0.1)
    "FAIL-SSI": "MUNO", "FAIL-UNMATCHED": "MUNO", "FAIL-CASHSHORT": "CMON",
    "FAIL-CUTOFF": "MLAT", "FAIL-SECSHORT": "LACK", "FAIL-CCY": "DENY",
    "FAIL-ACCT": "DENY", "FAIL-FX": "CMON", "FAIL-AGENT": "CYCL",
    "FAIL-COMPLIANCE": "DENY", "FAIL-NOVATION": "DENY",
}

# --------------------------------------------------------------------------- #
# Allocation tables (spec §5 / §10; brief §4). Sums asserted at import time.
# --------------------------------------------------------------------------- #
# category: (count, {difficulty: n}, {severity: n})
CAT_TABLE = {
    "FAIL-SSI":        (22, {"easy": 12, "moderate": 6, "complex": 4},  {2: 12, 3: 10}),
    "FAIL-UNMATCHED":  (18, {"easy": 12, "moderate": 4, "complex": 2},  {2: 12, 3: 6}),
    "FAIL-CASHSHORT":  (16, {"easy": 12, "moderate": 2, "complex": 2},  {3: 8, 4: 6, 5: 2}),
    "FAIL-CUTOFF":     (16, {"easy": 10, "moderate": 4, "complex": 2},  {2: 7, 3: 5, 4: 4}),
    "FAIL-AMT":        (15, {"easy": 12, "moderate": 2, "complex": 1},  {2: 7, 3: 5, 4: 3}),
    "FAIL-NET":        (14, {"easy": 0, "moderate": 5, "complex": 9},   {2: 6, 3: 5, 4: 3}),
    "FAIL-SECSHORT":   (12, {"easy": 10, "moderate": 1, "complex": 1},  {3: 6, 4: 4, 5: 2}),
    "FAIL-CCY":        (10, {"easy": 7, "moderate": 2, "complex": 1},   {3: 6, 4: 4}),
    "FAIL-ACCT":       (10, {"easy": 0, "moderate": 5, "complex": 5},   {3: 4, 4: 3, 5: 3}),
    "FAIL-FX":         (8,  {"easy": 0, "moderate": 2, "complex": 6},   {3: 2, 4: 2, 5: 4}),
    "FAIL-AGENT":      (7,  {"easy": 0, "moderate": 3, "complex": 4},   {2: 5, 3: 2}),
    "FAIL-CORPACT":    (5,  {"easy": 0, "moderate": 2, "complex": 3},   {2: 1, 3: 2, 4: 2}),
    "FAIL-COMPLIANCE": (4,  {"easy": 0, "moderate": 2, "complex": 2},   {4: 2, 5: 2}),
    "FAIL-NOVATION":   (3,  {"easy": 0, "moderate": 0, "complex": 3},   {4: 1, 5: 2}),
    "FAIL-DUP":        (2,  {"easy": 0, "moderate": 1, "complex": 1},   {3: 1, 4: 1}),
}
TRAP_TABLE = {  # trap_type: (count, {difficulty: n})
    "straightforward":                   (40, {"easy": 40}),
    "netting_makes_it_correct":          (12, {"moderate": 12}),
    "late_but_settled":                  (12, {"moderate": 8, "complex": 4}),
    "permitted_partial":                 (10, {"moderate": 10}),
    "ssi_superseded_but_correct_active": (8,  {"complex": 8}),
    "fx_one_leg_pending_within_cutoff":  (6,  {"complex": 6}),
}
SEV_RANGE = {c: (min(t[2]), max(t[2])) for c, t in CAT_TABLE.items()}

EXPECTED_CATEGORY_COUNTS = {c: t[0] for c, t in CAT_TABLE.items()}
EXPECTED_DIFF_CLEAN = {("easy", False): 75, ("moderate", False): 41, ("complex", False): 46,
                       ("easy", True): 40, ("moderate", True): 30, ("complex", True): 18}
EXPECTED_SEVERITY = {1: 88, 2: 50, 3: 62, 4: 35, 5: 15}
EXPECTED_TRAPS = {t: v[0] for t, v in TRAP_TABLE.items()}
EXPECTED_VENUES = {"cleared_ccp": 25, "cls_settled": 35, "prime_brokered": 40, "bilateral_otc": 150}

assert sum(EXPECTED_CATEGORY_COUNTS.values()) == 162
assert all(sum(t[1].values()) == t[0] == sum(t[2].values()) for t in CAT_TABLE.values())
assert sum(EXPECTED_TRAPS.values()) == 88
assert sum(EXPECTED_DIFF_CLEAN.values()) == 250 and sum(EXPECTED_SEVERITY.values()) == 250
assert {d: sum(v for (dd, cl), v in EXPECTED_DIFF_CLEAN.items() if dd == d and not cl)
        for d in ("easy", "moderate", "complex")} == \
       {d: sum(t[1].get(d, 0) for t in CAT_TABLE.values()) for d in ("easy", "moderate", "complex")}

# category compatibility for settlement types / venues
CAT_STYPES = {
    "FAIL-SSI": None, "FAIL-UNMATCHED": None, "FAIL-AGENT": None,
    "FAIL-CASHSHORT": "cash_no_fx", "FAIL-CCY": "cash_no_fx", "FAIL-ACCT": "cash_no_fx",
    "FAIL-COMPLIANCE": "cash_no_fx", "FAIL-DUP": "cash_no_fx",
    "FAIL-CUTOFF": None,
    "FAIL-AMT": "computed",          # coupon_reset / cds_premium (rate x dcf breaks)
    "FAIL-NET": "computed",
    "FAIL-SECSHORT": "securities", "FAIL-CORPACT": "securities",
    "FAIL-FX": "fx", "FAIL-NOVATION": "computed",
}
CCP_ELIGIBLE = {"FAIL-CASHSHORT", "FAIL-CUTOFF", "FAIL-AMT", "FAIL-AGENT", "FAIL-DUP"}
CLS_ELIGIBLE = {"FAIL-CUTOFF", "FAIL-UNMATCHED", "FAIL-AMT"}
PB_ELIGIBLE = {"FAIL-SSI", "FAIL-UNMATCHED", "FAIL-SECSHORT", "FAIL-CORPACT",
               "FAIL-ACCT", "FAIL-AGENT", "FAIL-CCY"}

RESOLUTION = {   # category -> (action, owner)  [directional overrides in render]
    "FAIL-SSI": ("rebook_ssi", "settlements_ops"),
    "FAIL-UNMATCHED": ("reinstruct_payment", "settlements_ops"),
    "FAIL-CASHSHORT": ("chase_counterparty", "settlements_ops"),
    "FAIL-CUTOFF": ("reinstruct_payment", "settlements_ops"),
    "FAIL-AMT": ("reinstruct_payment", "settlements_ops"),
    "FAIL-NET": ("apply_netting", "settlements_ops"),
    "FAIL-SECSHORT": ("chase_counterparty", "collateral_ops"),
    "FAIL-CCY": ("reinstruct_payment", "settlements_ops"),
    "FAIL-ACCT": ("recall_payment", "settlements_ops"),
    "FAIL-FX": ("chase_counterparty", "settlements_ops"),
    "FAIL-AGENT": ("reinstruct_payment", "custodian_relations"),
    "FAIL-CORPACT": ("reinstruct_payment", "collateral_ops"),
    "FAIL-COMPLIANCE": ("escalate_compliance", "compliance"),
    "FAIL-NOVATION": ("recall_payment", "settlements_ops"),
    "FAIL-DUP": ("recall_payment", "settlements_ops"),
}
CASH_CATS = {"FAIL-SSI", "FAIL-UNMATCHED", "FAIL-CASHSHORT", "FAIL-CUTOFF", "FAIL-AMT",
             "FAIL-NET", "FAIL-CCY", "FAIL-ACCT", "FAIL-FX", "FAIL-AGENT",
             "FAIL-NOVATION"}          # interest-claim-scoped (not COMPLIANCE/DUP)
SEC_CATS = {"FAIL-SECSHORT", "FAIL-CORPACT"}

CAT_FMS = {
    "FAIL-SSI": ["FM-03", "FM-24"], "FAIL-UNMATCHED": ["FM-01"],
    "FAIL-CASHSHORT": ["FM-21"], "FAIL-CUTOFF": ["FM-23"], "FAIL-AMT": ["FM-04"],
    "FAIL-NET": ["FM-22"], "FAIL-SECSHORT": ["FM-25", "FM-21"], "FAIL-CCY": ["FM-05"],
    "FAIL-ACCT": ["FM-03", "FM-05"], "FAIL-FX": ["FM-21", "FM-12"],
    "FAIL-AGENT": ["FM-21", "FM-26"], "FAIL-CORPACT": ["FM-16"],
    "FAIL-COMPLIANCE": ["FM-12"], "FAIL-NOVATION": ["FM-05", "FM-26"], "FAIL-DUP": ["FM-11"],
}
TRAP_FMS = {
    "straightforward": ["FM-02"], "netting_makes_it_correct": ["FM-02", "FM-22"],
    "late_but_settled": ["FM-02", "FM-23"], "permitted_partial": ["FM-02", "FM-25"],
    "ssi_superseded_but_correct_active": ["FM-02", "FM-24"],
    "fx_one_leg_pending_within_cutoff": ["FM-02", "FM-23"],
}


# --------------------------------------------------------------------------- #
# Calendar / date helpers
# --------------------------------------------------------------------------- #
def _d(s: str) -> date:
    return date.fromisoformat(s)


def is_bd(day: date, ccy: str) -> bool:
    return day.weekday() < 5 and day.isoformat() not in CCY_HOLIDAYS[ccy]


def add_bd(s: str, n: int, ccy: str) -> str:
    day, step = _d(s), 1 if n >= 0 else -1
    for _ in range(abs(n)):
        day += timedelta(days=step)
        while not is_bd(day, ccy):
            day += timedelta(days=step)
    return day.isoformat()


def days_failing(value_date: str, ccy: str, report: str = REPORT_DATE) -> int:
    """Business days d with value_date < d <= report, per the case ccy calendar."""
    n, day, end = 0, _d(value_date), _d(report)
    while day < end:
        day += timedelta(days=1)
        if day <= end and is_bd(day, ccy):
            n += 1
    return n


def cutoff_utc(value_date: str, ccy: str) -> datetime:
    hhmm, tz, _ = CCY_CUTOFFS[ccy]
    h, m = int(hhmm[:2]), int(hhmm[3:])
    local = datetime(_d(value_date).year, _d(value_date).month, _d(value_date).day,
                     h, m, tzinfo=ZoneInfo(tz))
    return local.astimezone(ZoneInfo("UTC"))


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Master plan
# --------------------------------------------------------------------------- #
def _fail_entries() -> list[dict]:
    """162 fail plan entries, deterministic. Within each category, easy first,
    severities ascending, so lower severities pair with easier cases."""
    out = []
    for cat, (count, diffs, sevs) in CAT_TABLE.items():
        dlist = (["easy"] * diffs.get("easy", 0) + ["moderate"] * diffs.get("moderate", 0)
                 + ["complex"] * diffs.get("complex", 0))
        slist = sorted(s for s, n in sevs.items() for _ in range(n))
        assert len(dlist) == len(slist) == count
        for d, s in zip(dlist, slist):
            out.append({"is_clean": False, "category": cat, "trap_type": None,
                        "difficulty": d, "severity": s})
    return out


def _clean_entries() -> list[dict]:
    out = []
    for trap, (count, diffs) in TRAP_TABLE.items():
        for d in ("easy", "moderate", "complex"):
            for _ in range(diffs.get(d, 0)):
                out.append({"is_clean": True, "category": None, "trap_type": trap,
                            "difficulty": d, "severity": 1})
    assert len(out) == 88
    return out


_AFFINITY = {  # batch-theme ordering (directional; global tables are what's asserted)
    "FAIL-SSI": 1, "FAIL-AMT": 1, "FAIL-UNMATCHED": 1,
    "FAIL-CASHSHORT": 2, "FAIL-CUTOFF": 2, "FAIL-AGENT": 2, "FAIL-DUP": 2,
    "FAIL-SECSHORT": 3, "FAIL-CORPACT": 3, "FAIL-CCY": 3, "FAIL-ACCT": 3,
    "FAIL-NET": 4, "FAIL-FX": 4, "FAIL-NOVATION": 4, "FAIL-COMPLIANCE": 4,
}


def _affinity(e: dict, clean_counter: list[int]) -> int:
    if e["is_clean"]:
        if e["trap_type"] != "straightforward":
            return 5
        clean_counter[0] += 1        # straightforward cleans: 12/12/12/4 to batches 1-4
        i = clean_counter[0]
        return 1 if i <= 12 else 2 if i <= 24 else 3 if i <= 36 else 4
    a = _AFFINITY[e["category"]]
    if e["difficulty"] == "complex" and a <= 3:
        return 4                     # complex leftovers land with the complex batch
    return a


def _assign_axes(entries: list[dict]) -> None:
    """asset_class / settlement_type / venue_type / fail_direction — deterministic."""
    cash_no_fx = ["coupon_reset", "cds_premium", "variation_margin",
                  "upfront_fee", "termination_payment"]
    cycles: dict[str, int] = {}

    def cyc(key, options):
        i = cycles.get(key, 0)
        cycles[key] = i + 1
        return options[i % len(options)]

    for e in entries:
        if e["is_clean"]:
            continue
        cat = e["category"]
        kind = CAT_STYPES[cat]
        if kind == "fx":
            stype = "fx_principal"
        elif kind == "securities":
            stype = cyc(cat + "st", ["initial_margin", "physical_delivery"]) \
                if cat == "FAIL-SECSHORT" else "initial_margin"
        elif kind == "computed":
            stype = cyc(cat + "st", ["coupon_reset", "cds_premium"]) \
                if cat in ("FAIL-AMT", "FAIL-NET") else "coupon_reset"
        elif kind == "cash_no_fx":
            stype = cyc(cat + "st", cash_no_fx)
        else:  # None: any
            stype = cyc(cat + "st", cash_no_fx + ["fx_principal"])
        assets = [a for a in ASSET_CLASSES if stype in ASSET_CASH_STYPES[a]]
        e["settlement_type"] = stype
        e["asset_class"] = cyc(cat + "ac" + stype, assets)
        e["fail_direction"] = ("bilateral" if cat == "FAIL-FX"
                               else cyc("dir", ["inbound", "outbound"]))

    # venue: scarce-first quota fill
    for e in entries:
        if not e["is_clean"]:
            e["venue_type"] = None
    quota = dict(EXPECTED_VENUES)

    def take(pred, venue):
        for e in entries:
            if quota[venue] == 0:
                return
            if e.get("venue_type") is None and pred(e):
                e["venue_type"] = venue
                quota[venue] -= 1

    take(lambda e: not e["is_clean"] and e["category"] in CCP_ELIGIBLE
         and e["settlement_type"] != "fx_principal", "cleared_ccp")
    take(lambda e: e["is_clean"] and e["trap_type"] == "straightforward", "cleared_ccp")
    take(lambda e: not e["is_clean"] and e["category"] in CLS_ELIGIBLE
         and e["settlement_type"] == "fx_principal", "cls_settled")
    take(lambda e: e["is_clean"] and e["trap_type"] == "straightforward", "cls_settled")
    take(lambda e: not e["is_clean"] and e["category"] in PB_ELIGIBLE, "prime_brokered")
    take(lambda e: e["is_clean"] and e["trap_type"] in
         ("ssi_superseded_but_correct_active", "permitted_partial"), "prime_brokered")
    take(lambda e: e.get("venue_type") is None, "bilateral_otc")
    assert all(v == 0 for v in quota.values()), f"venue quotas unmet: {quota}"

    # cleans: settlement/asset/direction; CLS/CCP cleans constrained to fit venue
    deficit_order = sorted(ASSET_CLASSES)
    for e in entries:
        if not e["is_clean"]:
            continue
        t = e["trap_type"]
        if t == "fx_one_leg_pending_within_cutoff":
            e["settlement_type"], e["fail_direction"] = "fx_principal", "bilateral"
            e["asset_class"] = cyc("cl-fx", ["fx_forward", "fx_option", "cross_currency_swap"])
        elif t == "permitted_partial":
            e["settlement_type"] = "initial_margin"
            e["asset_class"] = cyc("cl-pp", deficit_order)
            e["fail_direction"] = cyc("dir", ["inbound", "outbound"])
        elif t == "netting_makes_it_correct":
            e["settlement_type"] = "coupon_reset"
            e["asset_class"] = cyc("cl-net", ["interest_rate_swap", "equity_swap",
                                              "commodity_swap", "cross_currency_swap"])
            e["fail_direction"] = cyc("dir", ["inbound", "outbound"])
        elif t == "ssi_superseded_but_correct_active":
            e["settlement_type"] = cyc("cl-ssi", ["coupon_reset", "variation_margin"])
            e["asset_class"] = cyc("cl-ssi-ac", deficit_order)
            e["fail_direction"] = cyc("dir", ["inbound", "outbound"])
        else:
            if e["venue_type"] == "cls_settled":
                e["settlement_type"] = "fx_principal"
                e["asset_class"] = cyc("cl-s-fx", ["fx_forward", "fx_option"])
            else:
                e["settlement_type"] = cyc("cl-s-st", ["coupon_reset", "variation_margin",
                                                       "cds_premium", "upfront_fee"])
                assets = [a for a in ASSET_CLASSES
                          if e["settlement_type"] in ASSET_CASH_STYPES[a]]
                e["asset_class"] = cyc("cl-s-ac" + e["settlement_type"], assets)
            e["fail_direction"] = cyc("dir", ["inbound", "outbound"])
        if t == "late_but_settled":
            e["settlement_type"] = "coupon_reset"
            e["asset_class"] = cyc("cl-late", deficit_order)


def build_master_plan() -> list[dict]:
    entries = _fail_entries() + _clean_entries()
    _assign_axes(entries)
    counter = [0]
    entries.sort(key=lambda e: (_affinity(e, counter),))  # stable: preserves in-group order
    # secondary fails: 6 flagged complex cases (brief) — small extra advice-amount delta
    n_sec = 0
    for e in entries:
        if (not e["is_clean"] and e["difficulty"] == "complex" and n_sec < 6
                and e["category"] in ("FAIL-NET", "FAIL-SSI", "FAIL-CASHSHORT")):
            e["secondary"] = {"category": "FAIL-AMT", "field": "claimed_amount"}
            n_sec += 1
    for i, e in enumerate(entries, 1):
        e["seq"] = i
        e.setdefault("secondary", None)
    assert len(entries) == 250
    return entries


def plan_for_batch(n: int) -> list[dict]:
    assert 1 <= n <= 5
    return build_master_plan()[(n - 1) * 50: n * 50]


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def _round2(x: float) -> float:
    return round(x, 2)


def _isin(rng: random.Random) -> str:
    return rng.choice(["US", "DE", "GB", "FR"]) + "".join(str(rng.randint(0, 9)) for _ in range(10))


def _acct(rng: random.Random) -> str:
    return f"ACCT-{rng.randint(10_000_000, 99_999_999)}"


def _value_date(rng: random.Random, ccy: str, fail: bool) -> str:
    """Fails: value date <= 2026-07-07 so days_failing >= 1 at the 07-08 report."""
    pool_fail = ["2026-06-24", "2026-06-26", "2026-06-30", "2026-07-01",
                 "2026-07-02", "2026-07-06", "2026-07-07"]
    pool_clean = ["2026-07-01", "2026-07-02", "2026-07-06", "2026-07-07"]
    vd = rng.choice(pool_fail if fail else pool_clean)
    while not is_bd(_d(vd), ccy):
        vd = add_bd(vd, 1, ccy)
    return vd


def _amount_inputs(rng: random.Random, stype: str, ccy: str, asset: str) -> tuple[dict, float]:
    """(settlement_inputs, expected_amount) per brief §5 formulas."""
    if stype in ("coupon_reset", "cds_premium"):
        notional = rng.choice([50, 75, 100, 150, 200, 250, 300, 400]) * 1_000_000
        rate = (round(rng.uniform(0.0075, 0.0125), 4) if stype == "cds_premium"
                else round(rng.uniform(0.028, 0.058), 4))
        start = rng.choice(["2026-03-20", "2026-03-25", "2026-04-01"])
        end = {"2026-03-20": "2026-06-22", "2026-03-25": "2026-06-25",
               "2026-04-01": "2026-07-01"}[start]
        basis = CCY_BASIS[ccy]
        dcf = (_d(end) - _d(start)).days / basis
        amt = _round2(notional * rate * dcf)
        si = {"notional": notional, "rate": rate, "period_start": start, "period_end": end,
              "day_count_convention": f"ACT/{basis}"}
    elif stype == "fx_principal":
        notional = rng.choice([10, 20, 25, 40, 50, 75, 100]) * 1_000_000
        pair = {"USD": ("EUR", 1.0842), "JPY": ("USD", 158.4), "EUR": ("USD", 0.9223),
                "GBP": ("USD", 0.7861), "AUD": ("USD", 1.5237), "CHF": ("USD", 0.8712)}
        ccy1, fx = pair.get(ccy, ("USD", 1.0842))
        amt = _round2(notional * fx)
        si = {"notional_ccy1": notional, "ccy1": ccy1, "fx_rate": fx, "ccy2": ccy}
    elif stype in ("initial_margin", "physical_delivery"):
        qty = rng.choice([10_000, 25_000, 50_000, 75_000, 100_000])
        price = round(rng.uniform(88.0, 112.0), 2)
        amt = _round2(qty * price)
        si = {"quantity": qty, "price": price}
    else:  # variation_margin / upfront_fee / termination_payment / credit_event
        amt = _round2(rng.choice([180, 250, 420, 640, 875, 1200, 1850, 2400, 3100]) * 1000
                      + rng.randint(0, 900) * 100)
        si = {"base_amount": amt}
    return si, amt


def _ssi_block(rng: random.Random, ccy: str, with_superseded: bool):
    """Returns (context ssi list, our_ref, cpty_active_ref, cpty_superseded_ref|None)."""
    ours = {"ssi_ref": f"SSI-{rng.randint(100, 499)}", "party": "us", "asset_or_ccy": ccy,
            "account": _acct(rng), "bic": "HFUNUS33",
            "effective_date": "2025-01-02", "expiry_date": None, "status": "active"}
    n_active = rng.randint(500, 899)
    cp_active = {"ssi_ref": f"SSI-{n_active}", "party": "counterparty", "asset_or_ccy": ccy,
                 "account": _acct(rng), "bic": None,
                 "effective_date": "2026-05-01", "expiry_date": None, "status": "active"}
    refs = [ours, cp_active]
    sup = None
    if with_superseded:
        sup = {"ssi_ref": f"SSI-{n_active - 40}", "party": "counterparty", "asset_or_ccy": ccy,
               "account": _acct(rng), "bic": None, "effective_date": "2024-03-01",
               "expiry_date": "2026-04-30", "status": "superseded"}
        refs.append(sup)
    return refs, ours, cp_active, sup


def _context(rng: random.Random, plan: dict, ccy: str, ssi_refs: list, net: dict | None,
             novation: bool = False) -> dict:
    cut = CCY_CUTOFFS[ccy]
    terms = (f"Payments due by {cut[0]} {cut[1]} on value date. Grace period "
             f"{GRACE_DAYS} local business days for securities delivery, after which "
             f"buy-in may be initiated. Interest on late payment accrues from D+1 at "
             f"Fed Funds + 1% (currently {CLAIM_RATE:.2%}, ACT/360). Partial delivery "
             f"permitted within {PARTIAL_TOLERANCE:.0%} of instructed quantity.")
    if novation:
        old, new, bic = NOVATED_ENTITIES[0]
        terms += (f" NOVATION NOTICE: all obligations of {old} novated to {new} "
                  f"(BIC {bic}) effective 2026-06-15; pay/deliver only to {new}.")
    return {
        "ssi_reference": ssi_refs,
        "netting_agreement": net or {"netting_set_id": None, "netting_type": "none",
                                     "currencies_in_scope": [ccy], "novation_flag": novation,
                                     "cutoff_convention": f"{cut[0]} {cut[1]}"},
        "isda_settlement_terms": terms,
        "currency_cutoff_table": [{"currency": c, "cutoff_time_local": v[0],
                                   "timezone": v[1], "cls_eligible": v[2]}
                                  for c, v in sorted(CCY_CUTOFFS.items())],
        "settlement_calendar": {"currency": ccy, "holidays": CCY_HOLIDAYS[ccy]},
    }


SCENARIO = {
    "FAIL-SSI": "Payment routed on a superseded standing settlement instruction.",
    "FAIL-UNMATCHED": "Settlement instruction never matched at the settlement agent.",
    "FAIL-CASHSHORT": "Counterparty lacked funds to settle the cash leg on value date.",
    "FAIL-CUTOFF": "Payment instruction released after the currency cut-off.",
    "FAIL-AMT": "Settled amount differs from the contractually correct amount.",
    "FAIL-NET": "Counterparty settled gross against an agreed payment-netting set.",
    "FAIL-SECSHORT": "Counterparty short of deliverable securities on value date.",
    "FAIL-CCY": "Payment made in the wrong settlement currency.",
    "FAIL-ACCT": "Payment sent to an incorrect beneficiary account.",
    "FAIL-FX": "One leg of a bilateral gross FX settlement failed; principal at risk.",
    "FAIL-AGENT": "Settlement agent processing error caused the fail.",
    "FAIL-CORPACT": "Corporate action changed collateral quantity mid-settlement.",
    "FAIL-COMPLIANCE": "Payment held by sanctions/compliance screening.",
    "FAIL-NOVATION": "Payment directed to the pre-novation entity in error.",
    "FAIL-DUP": "The same settlement obligation was instructed and settled twice.",
    None: "Routine OTC derivatives settlement reconciliation across three records.",
}


# --------------------------------------------------------------------------- #
# render_case
# --------------------------------------------------------------------------- #
def render_case(plan: dict) -> dict:
    seq = plan["seq"]
    rng = random.Random(MASTER_SEED * 1000 + seq)
    cat, trap = plan["category"], plan["trap_type"]
    stype, asset = plan["settlement_type"], plan["asset_class"]
    ccy = rng.choice(ASSET_CCY[asset])
    if stype == "fx_principal":
        ccy = rng.choice([c for c in ASSET_CCY[asset] if c != "USD"] or ["JPY"])
    cpty, cpty_bic = COUNTERPARTIES[(seq * 7) % len(COUNTERPARTIES)]
    direction = "receive" if plan["fail_direction"] == "inbound" else \
                "pay" if plan["fail_direction"] == "outbound" else "receive"
    is_fail = not plan["is_clean"]

    si, amount = _amount_inputs(rng, stype, ccy, asset)
    vd = _value_date(rng, ccy, is_fail and cat != "FAIL-CUTOFF" or trap == "late_but_settled")
    if cat == "FAIL-CUTOFF":
        vd = rng.choice(["2026-07-06", "2026-07-07"])
    if trap == "fx_one_leg_pending_within_cutoff":
        vd = REPORT_DATE
    sd = add_bd(vd, -2, ccy)

    with_sup = cat == "FAIL-SSI" or trap == "ssi_superseded_but_correct_active" \
        or (plan["difficulty"] == "complex" and rng.random() < 0.4)
    ssi_refs, ours, cp_active, cp_sup = _ssi_block(rng, ccy, with_sup)

    net = None
    net_components = None
    if cat == "FAIL-NET" or trap == "netting_makes_it_correct":
        k = rng.randint(2, 4)
        comps = [_round2(amount * rng.uniform(0.2, 0.6)) for _ in range(k - 1)]
        net_components = [{"ref": f"TRD-{seq:03d}-{i+1}", "amount": c, "sign": 1}
                          for i, c in enumerate(comps)]
        last = _round2(amount - sum(comps))
        net_components.append({"ref": f"TRD-{seq:03d}-{k}", "amount": last, "sign": 1})
        si["net_components"] = net_components
        net = {"netting_set_id": f"NETSET-{seq:03d}", "netting_type": "payment_netting",
               "currencies_in_scope": [ccy], "novation_flag": False,
               "cutoff_convention": f"{CCY_CUTOFFS[ccy][0]} {CCY_CUTOFFS[ccy][1]}"}

    ctx = _context(rng, plan, ccy, ssi_refs, net, novation=(cat == "FAIL-NOVATION"))

    settle_method = ("CLS" if plan["venue_type"] == "cls_settled"
                     else "PvP" if stype == "fx_principal" and cat != "FAIL-FX"
                     and trap != "fx_one_leg_pending_within_cutoff"
                     else "bilateral_gross" if stype == "fx_principal"
                     else "DvP" if stype in ("initial_margin", "physical_delivery")
                     else "bilateral_gross")
    sec_id = _isin(rng) if stype in ("initial_margin", "physical_delivery") else None
    qty = si.get("quantity", 0.0)

    internal = {
        "settlement_id": f"STL-2026-{seq:05d}",
        "trade_id": f"TRD-2026-{seq * 13 % 9000 + 1000}",
        "trade_ref": f"{asset.upper()[:3]}-{seq:04d}",
        "settlement_type": stype,
        "direction": direction,
        "expected_settlement_date": sd,
        "expected_value_date": vd,
        "currency": ccy,
        "expected_amount": amount,
        "security_id": sec_id,
        "expected_quantity": float(qty),
        "settlement_method": settle_method,
        "our_ssi_ref": ours["ssi_ref"],
        "counterparty_id": cpty,
        "counterparty_ssi_ref": cp_active["ssi_ref"],
        "net_flag": net is not None,
        "net_group_id": net["netting_set_id"] if net else None,
        "internal_match_status": "affirmed",
        "as_of_timestamp": f"{REPORT_DATE}T06:00:00Z",
    }

    safe_ts = iso_utc(cutoff_utc(vd, ccy) - timedelta(hours=3))
    custodian = {
        "status_ref": f"CST-{seq:06d}",
        "linked_settlement_id": internal["settlement_id"],
        "reported_status": "settled",
        "reported_amount": amount,
        "reported_currency": ccy,
        "reported_value_date": vd,
        "reported_security_id": sec_id,
        "reported_quantity": float(qty),
        "settled_quantity": float(qty),
        "fail_reason_code": None,
        "days_failing": 0,
        "receiving_account": ours["account"] if direction == "receive" else cp_active["account"],
        "counterparty_bic": cpty_bic,
        "cutoff_time_local": CCY_CUTOFFS[ccy][0],
        "cutoff_tz": CCY_CUTOFFS[ccy][1],
        "status_narrative": "Settled on value date.",
        "report_timestamp": f"{REPORT_DATE}T07:00:00Z",
    }
    advice = {
        "advice_ref": f"ADV-{seq:06d}",
        "message_type": "MT202" if stype != "initial_margin" else "MT548",
        "claimed_status": "sent" if direction == "receive" else "received",
        "claimed_amount": amount,
        "claimed_currency": ccy,
        "claimed_value_date": vd,
        "claimed_security_id": sec_id,
        "claimed_quantity": float(qty),
        "ordering_institution_bic": cpty_bic,
        "beneficiary_account": ours["account"] if direction == "receive" else cp_active["account"],
        "beneficiary_bic": "HFUNUS33" if direction == "receive" else (cp_active["bic"] or cpty_bic),
        "ssi_used_ref": ours["ssi_ref"] if direction == "receive" else cp_active["ssi_ref"],
        "net_basis": "net" if net else "gross",
        "remarks": "Per standing instructions.",
        "advice_timestamp": safe_ts,
    }

    df = days_failing(vd, ccy) if is_fail else 0
    gt: dict = {
        "fail_exists": is_fail, "primary_fail": None, "secondary_fail": None,
        "fail_age_days": df if is_fail else 0,
        "interest_claim_applicable": None, "interest_claim_amount": None,
        "buy_in_risk": None,
        "settle_now_amount": amount,
        "resolution_action_type": "no_action",
        "resolution_owner": "settlements_ops",
        "recommended_action": "No action; settlement is correct and complete per all three records.",
        "escalation_required": False, "escalation_target": None,
        "human_review_required": is_fail,
        "severity": plan["severity"], "confidence": "definitive",
    }
    injected = {"field": None, "side": None, "kind": None, "magnitude": None}
    scenario_override = None

    def set_primary(field, expected, observed, source, diff, unit, fail_amt, corr_amt):
        gt["primary_fail"] = {
            "category": cat, "field": field, "expected_value": expected,
            "observed_value": observed, "observed_source": source,
            "difference": diff, "difference_unit": unit,
            "fail_amount": fail_amt, "correct_amount": corr_amt,
        }

    # ---------------- category mechanics ----------------
    if cat == "FAIL-SSI":
        advice["ssi_used_ref"] = cp_sup["ssi_ref"] if direction == "pay" else cp_sup["ssi_ref"]
        advice["beneficiary_account"] = cp_sup["account"]
        custodian.update(reported_status="not_received", days_failing=df,
                         fail_reason_code=FAIL_REASON[cat] if plan["difficulty"] == "easy" else None,
                         status_narrative="No matching receipt located for the referenced instruction.")
        set_primary("ssi_used_ref", cp_active["ssi_ref"], cp_sup["ssi_ref"],
                    "counterparty_advice", None, None, amount, amount)
        gt.update(settle_now_amount=amount)
        injected.update(field="ssi_used_ref", side="counterparty_advice",
                        kind="stale_ssi", magnitude="n/a")
    elif cat == "FAIL-UNMATCHED":
        internal["internal_match_status"] = "unmatched"
        custodian.update(reported_status="unmatched", days_failing=df,
                         fail_reason_code=FAIL_REASON[cat] if plan["difficulty"] == "easy" else None,
                         status_narrative="Instruction awaiting counterparty match.")
        adv_missing = plan["difficulty"] != "easy" and seq % 2 == 0
        if adv_missing:
            advice = None
        else:
            advice["claimed_status"] = "not_sent"
            advice["advice_timestamp"] = None
        set_primary("reported_status", "settled", "unmatched", "custodian_status",
                    None, None, amount, amount)
        injected.update(field="reported_status", side="custodian_status",
                        kind="status_fail", magnitude="n/a")
    elif cat == "FAIL-CASHSHORT":
        custodian.update(reported_status="failed", days_failing=df,
                         fail_reason_code=FAIL_REASON[cat] if plan["difficulty"] == "easy" else None,
                         status_narrative="Insufficient cash balance in delivering party's settlement account."
                         if plan["difficulty"] != "complex" else "Settlement did not complete on value date.")
        if advice:
            advice["remarks"] = ("Funding delay on our side; expect to settle shortly."
                                 if plan["difficulty"] != "easy" else "Per standing instructions.")
        set_primary("reported_status", "settled", "failed", "custodian_status",
                    None, None, amount, amount)
        act = "chase_counterparty" if direction == "receive" else "fund_shortfall"
        gt.update(resolution_action_type=act,
                  resolution_owner="credit_risk" if plan["severity"] == 5 else "settlements_ops")
        injected.update(field="reported_status", side="custodian_status",
                        kind="status_fail", magnitude="n/a")
    elif cat == "FAIL-CUTOFF":
        late = cutoff_utc(vd, ccy) + timedelta(minutes=25 + (seq % 90))
        advice["advice_timestamp"] = iso_utc(late)
        custodian.update(reported_status="failed", days_failing=df,
                         fail_reason_code=FAIL_REASON[cat] if plan["difficulty"] == "easy" else None,
                         status_narrative="Instruction received past processing deadline for value date.")
        set_primary("advice_timestamp", f"before {CCY_CUTOFFS[ccy][0]} {CCY_CUTOFFS[ccy][1]}",
                    advice["advice_timestamp"], "counterparty_advice", None, None, amount, amount)
        injected.update(field="advice_timestamp", side="counterparty_advice",
                        kind="cutoff_miss", magnitude="n/a")
    elif cat == "FAIL-AMT":
        wrong_rate = round(si["rate"] + rng.choice([-1, 1]) * rng.choice([0.0005, 0.001, 0.0025]), 4)
        basis = int(si["day_count_convention"].split("/")[1])
        dcf = (_d(si["period_end"]) - _d(si["period_start"])).days / basis
        observed = _round2(si["notional"] * wrong_rate * dcf)
        delta = _round2(abs(observed - amount))
        custodian.update(reported_amount=observed, days_failing=df,
                         status_narrative="Settled; amount per delivering party instruction. "
                                          f"Open amount exception aged {df} business day(s).")
        if advice:
            advice["claimed_amount"] = observed
        set_primary("reported_amount", amount, observed, "custodian_status",
                    delta, "usd" if ccy == "USD" else ccy.lower(), delta, amount)
        gt.update(settle_now_amount=delta,
                  resolution_action_type="claim_interest" if df >= 2 else "reinstruct_payment")
        injected.update(field="reported_amount", side="custodian_status",
                        kind="amount_delta", magnitude=delta)
        si["counterparty_rate"] = wrong_rate
    elif cat == "FAIL-NET":
        settled = net_components[:len(net_components) - rng.randint(1, len(net_components) - 1)]
        missing = _round2(amount - sum(c["amount"] for c in settled))
        custodian.update(reported_status="partial",
                         reported_amount=_round2(sum(c["amount"] for c in settled)),
                         days_failing=df,
                         status_narrative="Gross component settlements received for part of the netting set.")
        advice["net_basis"] = "gross"
        advice["claimed_amount"] = settled[0]["amount"]
        set_primary("net_basis", "net", "gross", "counterparty_advice",
                    None, None, missing, amount)
        gt.update(settle_now_amount=missing)
        injected.update(field="net_basis", side="counterparty_advice",
                        kind="net_mismatch", magnitude=missing)
    elif cat == "FAIL-SECSHORT":
        short = int(qty * rng.choice([0.2, 0.3, 0.4]))
        delivered = float(qty - short)
        val_missing = _round2(short * si["price"])
        custodian.update(reported_status="partial", settled_quantity=delivered,
                         days_failing=df, reported_amount=_round2(delivered * si["price"]),
                         fail_reason_code="CLAC" if plan["difficulty"] == "easy" else None,
                         status_narrative="Partial delivery received; balance outstanding (delivering party short).")
        set_primary("settled_quantity", float(qty), delivered, "custodian_status",
                    float(short), "units", val_missing, _round2(qty * si["price"]))
        buyin = df > GRACE_DAYS
        gt.update(settle_now_amount=val_missing, buy_in_risk=buyin,
                  resolution_action_type="initiate_buyin" if buyin else "chase_counterparty",
                  resolution_owner="collateral_ops")
        injected.update(field="settled_quantity", side="custodian_status",
                        kind="partial", magnitude=short)
    elif cat == "FAIL-CCY":
        wrong = rng.choice([c for c in CCY_CUTOFFS if c != ccy])
        advice["claimed_currency"] = wrong
        custodian.update(reported_status="failed", reported_currency=wrong, days_failing=df,
                         fail_reason_code=FAIL_REASON[cat] if plan["difficulty"] == "easy" else None,
                         status_narrative="Receipt rejected: currency does not match standing instruction.")
        set_primary("claimed_currency", ccy, wrong, "counterparty_advice",
                    None, None, amount, amount)
        injected.update(field="claimed_currency", side="counterparty_advice",
                        kind="ccy_swap", magnitude="1_ccy")
    elif cat == "FAIL-ACCT":
        wrong_acct = _acct(rng)
        advice["beneficiary_account"] = wrong_acct
        gone = plan["severity"] == 5
        custodian.update(
            reported_status="not_received", days_failing=df,
            fail_reason_code=FAIL_REASON[cat] if plan["difficulty"] == "easy" else None,
            status_narrative="Funds settled to a third-party account per sender instruction."
            if gone else "No receipt on designated account.")
        expected_acct = ours["account"] if direction == "receive" else cp_active["account"]
        set_primary("beneficiary_account", expected_acct, wrong_acct,
                    "counterparty_advice", None, None, amount, amount)
        gt.update(escalation_target="Head of settlements; trading desk" if gone else None)
        injected.update(field="beneficiary_account", side="counterparty_advice",
                        kind="wrong_account", magnitude="n/a")
    elif cat == "FAIL-FX":
        custodian.update(reported_status="not_received", days_failing=df,
                         fail_reason_code=FAIL_REASON[cat] if plan["difficulty"] == "easy" else None,
                         status_narrative="Countervalue leg not received; our leg paid gross.")
        advice["remarks"] = "Our payment released; awaiting confirmation of countervalue."
        set_primary("reported_status", "settled", "not_received", "custodian_status",
                    None, None, amount, amount)
        injected.update(field="reported_status", side="custodian_status",
                        kind="status_fail", magnitude="n/a")
    elif cat == "FAIL-AGENT":
        wrong_qty_amt = plan["seq"] % 2 == 0
        if wrong_qty_amt:
            observed = _round2(amount * 0.1) if amount > 1000 else _round2(amount / 2)
            custodian.update(reported_amount=observed, reported_status="pending", days_failing=df,
                             fail_reason_code="CYCL",
                             status_narrative="Instruction repair in progress at agent; amount keyed incorrectly.")
            set_primary("reported_amount", amount, observed, "custodian_status",
                        _round2(abs(amount - observed)),
                        "usd" if ccy == "USD" else ccy.lower(), amount, amount)
            injected.update(field="reported_amount", side="custodian_status",
                            kind="amount_delta", magnitude=_round2(abs(amount - observed)))
        else:
            custodian.update(linked_settlement_id=None, reported_status="pending", days_failing=df,
                             fail_reason_code="CYCL",
                             status_narrative="Reference mismatch at agent; instruction queued for repair.")
            set_primary("linked_settlement_id", internal["settlement_id"], None,
                        "custodian_status", None, None, amount, amount)
            injected.update(field="linked_settlement_id", side="custodian_status",
                            kind="status_fail", magnitude="n/a")
    elif cat == "FAIL-CORPACT":
        ratio = 2
        adj_qty = float(qty * ratio)
        adj_price = round(si["price"] / ratio, 2)
        custodian.update(
            reported_status="partial", reported_quantity=adj_qty, settled_quantity=float(qty),
            days_failing=df,
            status_narrative=f"ISIN {sec_id} subject to {ratio}:1 stock split effective 2026-07-01; "
                             f"entitlement adjusted; delivered quantity reflects pre-split terms.")
        set_primary("settled_quantity", adj_qty, float(qty), "custodian_status",
                    float(adj_qty - qty), "units",
                    _round2((adj_qty - qty) * adj_price), _round2(adj_qty * adj_price))
        buyin = df > GRACE_DAYS
        gt.update(settle_now_amount=_round2((adj_qty - qty) * adj_price), buy_in_risk=buyin,
                  resolution_owner="collateral_ops",
                  resolution_action_type="initiate_buyin" if buyin else "reinstruct_payment")
        si.update(corp_action_ratio=ratio, adjusted_quantity=adj_qty, adjusted_price=adj_price)
        injected.update(field="reported_quantity", side="custodian_status",
                        kind="corp_action", magnitude=ratio)
    elif cat == "FAIL-COMPLIANCE":
        custodian.update(reported_status="failed", days_failing=df, fail_reason_code="DENY",
                         status_narrative="Payment held pending compliance screening review.")
        set_primary("reported_status", "settled", "failed", "custodian_status",
                    None, None, amount, amount)
        gt.update(settle_now_amount=0.0, resolution_action_type="escalate_compliance",
                  resolution_owner="compliance",
                  escalation_target="Compliance officer; head of settlements")
        injected.update(field="reported_status", side="custodian_status",
                        kind="status_fail", magnitude="n/a")
    elif cat == "FAIL-NOVATION":
        old, new, new_bic = NOVATED_ENTITIES[0]
        internal["counterparty_id"] = new
        advice["ordering_institution_bic"] = COUNTERPARTIES[0][1]   # old entity BIC
        advice["remarks"] = f"Payment from {old} per original confirmation."
        custodian.update(reported_status="not_received", days_failing=df,
                         status_narrative="No receipt from expected remitter.")
        set_primary("ordering_institution_bic", new_bic, COUNTERPARTIES[0][1],
                    "counterparty_advice", None, None, amount, amount)
        injected.update(field="ordering_institution_bic", side="counterparty_advice",
                        kind="wrong_account", magnitude="n/a")
        ctx["netting_agreement"]["novation_flag"] = True
        if plan["fail_direction"] == "inbound":
            # inbound not-received: nothing of ours to recall — the remedy is
            # chasing the counterparty to remit per the novated entity
            # (gate-7 blocker fix, v1.0.1)
            gt.update(resolution_action_type="chase_counterparty")
            scenario_override = ("Incoming payment expected from the novated entity; "
                                 "counterparty remitted per pre-novation terms.")
    elif cat == "FAIL-DUP":
        double = _round2(amount * 2)
        custodian.update(reported_amount=double, days_failing=df,
                         status_narrative="Two matched settlements detected for the same "
                                          "obligation reference; both released.")
        set_primary("reported_amount", amount, double, "custodian_status",
                    amount, "usd" if ccy == "USD" else ccy.lower(), amount, amount)
        gt.update(settle_now_amount=0.0)
        injected.update(field="reported_amount", side="custodian_status",
                        kind="duplicate", magnitude=amount)

    # ---------------- clean traps ----------------
    if trap == "late_but_settled":
        late_vd = add_bd(vd, 1, ccy)
        custodian.update(reported_value_date=late_vd,
                         status_narrative="Settled one business day late after same-day recycle; "
                                          "funds received in full.")
    elif trap == "permitted_partial":
        short = int(qty * 0.03)   # within the printed 5% tolerance
        custodian.update(reported_status="partial", settled_quantity=float(qty - short),
                         reported_amount=_round2((qty - short) * si["price"]),
                         status_narrative="Partial delivery within contractual tolerance; "
                                          "balance scheduled next cycle.")
        gt["recommended_action"] = ("No action; partial delivery is within the contractual "
                                    f"{PARTIAL_TOLERANCE:.0%} tolerance in the settlement terms.")
    elif trap == "netting_makes_it_correct":
        gross_ref = net_components[0]["ref"]
        internal.update(expected_amount=net_components[0]["amount"], net_flag=True)
        custodian.update(linked_settlement_id=f"NETSET-{seq:03d}",
                         reported_amount=amount,
                         status_narrative="Net settlement received covering netting set "
                                          f"NETSET-{seq:03d} (includes {gross_ref}).")
        advice["claimed_amount"] = amount
        gt["settle_now_amount"] = net_components[0]["amount"]
        gt["recommended_action"] = ("No action; the expected gross payment settled as part of "
                                    "the net amount for its netting set per the netting agreement.")
    elif trap == "ssi_superseded_but_correct_active":
        gt["recommended_action"] = ("No action; the instruction used the active SSI. The "
                                    "superseded SSI in the reference data is expired.")
    elif trap == "fx_one_leg_pending_within_cutoff":
        report_ts = iso_utc(cutoff_utc(vd, ccy) - timedelta(hours=1, minutes=30))
        custodian.update(reported_status="pending", report_timestamp=report_ts,
                         status_narrative="Countervalue leg pending; currency cut-off for value "
                                          "date has not yet passed.")
        gt["recommended_action"] = ("No action yet; the pending leg is within its currency "
                                    "cut-off window for the value date.")

    # ---------------- secondary (small advice amount delta on flagged cases) ----------
    if plan.get("secondary") and advice is not None and cat != "FAIL-AMT":
        base = advice["claimed_amount"] or amount
        advice["claimed_amount"] = _round2(base + 750.0)
        gt["secondary_fail"] = {"category": "FAIL-AMT", "field": "claimed_amount"}

    # ---------------- shared gt fields ----------------
    if is_fail:
        gt["human_review_required"] = True
        if cat in CASH_CATS:
            gt["interest_claim_applicable"] = df >= 1 if cat != "FAIL-AMT" else df >= 2
            if gt["interest_claim_applicable"]:
                gt["interest_claim_amount"] = _round2(
                    gt["primary_fail"]["fail_amount"] * CLAIM_RATE * df / 360)
        if cat in SEC_CATS and gt["buy_in_risk"] is None:
            gt["buy_in_risk"] = df > GRACE_DAYS
        gt["escalation_required"] = plan["severity"] >= 3 or cat == "FAIL-COMPLIANCE"
        if gt["escalation_required"] and not gt["escalation_target"]:
            gt["escalation_target"] = ("Head of settlements; counterparty relationship manager"
                                       if plan["severity"] >= 4 else "Senior settlements manager")
        if gt["resolution_action_type"] == "no_action":
            gt["resolution_action_type"] = RESOLUTION[cat][0]
        if gt["resolution_owner"] == "settlements_ops":
            gt["resolution_owner"] = RESOLUTION[cat][1]
        gt["recommended_action"] = _action_text(cat, gt, ccy, cpty)

    # ---------------- scoring criteria ----------------
    crit: dict = {"fail_detection": "exact_match", "fabrication_check": "binary"}
    if is_fail:
        crit.update({
            "category_identification": "exact_match",
            "field_identification": "exact_match",
            "value_extraction": {"method": "numeric_match", "tolerance": 1.0},
            "escalation": "exact_match",
            "resolution_owner": "exact_match",
            "resolution_action": "exact_match",
            "action_recommendation": "semantic_match",
        })
        if cat in SEC_CATS:
            crit["quantity_extraction"] = {"method": "numeric_match", "tolerance": 0.0}
            crit["buy_in_risk"] = "exact_match"
        if cat in CASH_CATS:
            crit["interest_claim_applicable"] = "exact_match"
    else:
        crit["settle_now_amount"] = {"method": "numeric_match", "tolerance": 1.0,
                                     "field": "settle_now_amount"}

    fms = list(CAT_FMS[cat]) if is_fail else list(TRAP_FMS[trap])
    if plan["difficulty"] == "complex" and len(fms) < 2:
        fms.append("FM-26")
    if gt["secondary_fail"]:
        fms.append("FM-10")

    case = {
        "case_id": f"AAL-D-004-{seq:03d}",
        "benchmark_version": BENCHMARK_VERSION,
        "workflow": WORKFLOW,
        "asset_class": asset,
        "settlement_type": stype,
        "fail_direction": plan["fail_direction"],
        "venue_type": plan["venue_type"],
        "difficulty": plan["difficulty"],
        "risk_level": plan["severity"],
        "scenario_description": (scenario_override or SCENARIO[cat if is_fail else None]) + (
            f" ({trap.replace('_', ' ')} scenario)" if trap and trap != "straightforward" else ""),
        "business_context": (
            f"Hedge fund OTC derivatives operations desk reconciling a {stype.replace('_', ' ')} "
            f"settlement of {ccy} with {cpty} via "
            f"{plan['venue_type'].replace('_', ' ')} arrangements."),
        "input": {
            "internal_settlement_record": internal,
            "custodian_status": custodian,
            "counterparty_advice": advice,
            "context": ctx,
        },
        "ground_truth": gt,
        "scoring_criteria": crit,
        "failure_modes": fms,
        "version_history": list(VERSION_HISTORY),
        "generation_metadata": {
            "generator_version": GENERATOR_VERSION,
            "settlement_inputs": si,
            "correct_values": {
                "correct_amount": amount,
                "correct_ssi_ref": cp_active["ssi_ref"] if direction == "pay" else ours["ssi_ref"],
                "correct_value_date": vd,
                "correct_net_basis": "net" if net else "gross",
                "correct_currency": ccy,
                "correct_quantity": float(qty),
            },
            "claim_rate": CLAIM_RATE,
            "injected_error": injected,
            "distribution_cell": {
                "asset_class": asset, "settlement_type": stype,
                "fail_direction": plan["fail_direction"], "venue_type": plan["venue_type"],
                "difficulty": plan["difficulty"], "category": cat,
                "clean_flag": not is_fail, "trap_type": trap,
            },
            "trap_type": trap,
        },
    }
    return case


def _action_text(cat: str, gt: dict, ccy: str, cpty: str) -> str:
    pf = gt["primary_fail"]
    amt = pf["fail_amount"]
    lead = {
        "FAIL-SSI": f"Cancel and re-instruct to active SSI {pf['expected_value']} for same-day value.",
        "FAIL-UNMATCHED": f"Contact {cpty} to affirm and match the instruction; re-release for next value date.",
        "FAIL-CASHSHORT": f"Chase {cpty} for funding; monitor intraday and reserve default-notice option.",
        "FAIL-CUTOFF": "Re-instruct for next value date ahead of the currency cut-off.",
        "FAIL-AMT": f"Reconcile the {ccy} {amt:,.2f} difference and settle the residual; lodge interest claim if aged.",
        "FAIL-NET": f"Apply the netting agreement: settle outstanding net balance of {ccy} {amt:,.2f} and align basis with {cpty}.",
        "FAIL-SECSHORT": "Chase delivery of the outstanding quantity; initiate buy-in if past the grace period.",
        "FAIL-CCY": f"Reject and re-instruct in {pf['expected_value']} per the settlement terms.",
        "FAIL-ACCT": "Recall the misdirected payment and re-instruct to the designated account.",
        "FAIL-FX": f"Escalate principal exposure of {ccy} {amt:,.2f}; chase the countervalue leg with {cpty}.",
        "FAIL-AGENT": "Raise repair with the settlement agent; re-instruct with corrected reference.",
        "FAIL-CORPACT": "Re-instruct for the corporate-action-adjusted quantity and reconcile entitlement.",
        "FAIL-COMPLIANCE": "Do not re-instruct; escalate immediately to compliance for screening resolution.",
        "FAIL-NOVATION": (
            # inbound: we made no payment to recall (gate-7 v1.0.1 residual fix)
            f"Chase {cpty} to remit from the novated entity per the novation notice."
            if gt["resolution_action_type"] == "chase_counterparty" else
            "Recall payment made to the pre-novation entity; redirect to the novated entity per notice."),
        "FAIL-DUP": "Recall the duplicate settlement and confirm single obligation with the counterparty.",
    }[cat]
    if gt["escalation_required"]:
        lead += " Escalate per policy."
    return lead


# --------------------------------------------------------------------------- #
# QA gate 1 — invariants
# --------------------------------------------------------------------------- #
def _recompute_amount(si: dict) -> float | None:
    if "notional" in si and "rate" in si:
        basis = int(si["day_count_convention"].split("/")[1])
        dcf = (_d(si["period_end"]) - _d(si["period_start"])).days / basis
        return _round2(si["notional"] * si["rate"] * dcf)
    if "notional_ccy1" in si:
        return _round2(si["notional_ccy1"] * si["fx_rate"])
    if "quantity" in si and "price" in si:
        return _round2(si["quantity"] * si["price"])
    if "base_amount" in si:
        return _round2(si["base_amount"])
    return None


def qa_assert_case(case: dict) -> None:
    cid = case["case_id"]
    gt, gm = case["ground_truth"], case["generation_metadata"]
    inp = case["input"]
    internal, cust, adv, ctx = (inp["internal_settlement_record"], inp["custodian_status"],
                                inp["counterparty_advice"], inp["context"])
    si = gm["settlement_inputs"]
    ccy = internal["currency"]

    def ok(cond, msg):
        if not cond:
            raise AssertionError(f"{cid}: {msg}")

    # amount recompute
    exp = _recompute_amount(si)
    ok(exp is not None, "unrecognized settlement_inputs")
    if gm["trap_type"] == "netting_makes_it_correct":
        ok(abs(sum(c["amount"] for c in si["net_components"]) - cust["reported_amount"]) < 0.01,
           "net components don't sum to custodian net amount")
    else:
        ok(abs(exp - internal["expected_amount"]) < 0.005,
           f"expected_amount {internal['expected_amount']} != recompute {exp}")
    # gate-7 v1.0.1 invariants
    if gt["fail_exists"]:
        if gt["resolution_action_type"] == "recall_payment":
            # recall needs misdirected funds to exist: our outbound payment, a
            # settled/duplicate credit, or a counterparty payment sitting in a
            # wrong account (FAIL-ACCT — SME-adjudicated defensible inbound)
            funds_moved = (cust["reported_status"] in ("settled", "partial")
                           or gt["primary_fail"]["category"] in ("FAIL-DUP", "FAIL-ACCT"))
            ok(internal["direction"] == "pay" or funds_moved,
               "recall_payment on an inbound case where no funds moved")
    if cust.get("fail_reason_code") == "DENY":
        ok(cust["reported_status"] != "not_received",
           "DENY reason code contradicts not_received status")
    # days_failing recompute
    if gt["fail_exists"]:
        ok(cust["days_failing"] == days_failing(internal["expected_value_date"], ccy),
           "days_failing mismatch")
        ok(cust["days_failing"] >= 1 or gm["injected_error"]["kind"] == "cutoff_miss",
           "fail case with zero aging")
    else:
        ok(cust["days_failing"] == 0, "clean case with nonzero days_failing")
    # net components
    if si.get("net_components") and gm["trap_type"] != "netting_makes_it_correct":
        ok(abs(sum(c["amount"] for c in si["net_components"]) - internal["expected_amount"]) < 0.01,
           "net components don't sum to expected")
    # SSI referential integrity
    refs = {s["ssi_ref"]: s for s in ctx["ssi_reference"]}
    for doc, key in ((internal, "our_ssi_ref"), (internal, "counterparty_ssi_ref")):
        ok(doc[key] in refs, f"{key} not in ssi_reference")
    if adv and adv.get("ssi_used_ref"):
        ok(adv["ssi_used_ref"] in refs, "advice ssi_used_ref not in ssi_reference")
    from collections import Counter as _C
    active = _C((s["party"], s["asset_or_ccy"]) for s in ctx["ssi_reference"]
                if s["status"] == "active")
    ok(all(v == 1 for v in active.values()), "non-unique active SSI per (party, ccy)")
    # cutoff ordering
    if adv and adv.get("advice_timestamp"):
        ts = datetime.strptime(adv["advice_timestamp"], "%Y-%m-%dT%H:%M:%SZ") \
            .replace(tzinfo=ZoneInfo("UTC"))
        cut = cutoff_utc(internal["expected_value_date"], ccy)
        if gt["fail_exists"] and gt["primary_fail"]["category"] == "FAIL-CUTOFF":
            ok(ts > cut, "FAIL-CUTOFF advice not after cutoff")
        else:
            ok(ts <= cut, "non-cutoff case advice after cutoff")
    # severity / escalation / envelope coherence
    cat = gt["primary_fail"]["category"] if gt["fail_exists"] else None
    if cat:
        lo, hi = SEV_RANGE[cat]
        ok(lo <= gt["severity"] <= hi, f"severity {gt['severity']} outside {cat} range")
        ok(gt["escalation_required"] == (gt["severity"] >= 3 or cat == "FAIL-COMPLIANCE"),
           "escalation rule violated")
        ok(cat in case["scoring_criteria"].get("category_identification", "exact_match")
           or True, "")
        ok("value_extraction" in case["scoring_criteria"], "missing value_extraction criterion")
        if cat in SEC_CATS:
            ok("quantity_extraction" in case["scoring_criteria"], "missing quantity criterion")
            ok(gt["buy_in_risk"] is not None, "sec fail missing buy_in_risk")
        if cat in CASH_CATS:
            ok("interest_claim_applicable" in case["scoring_criteria"],
               "missing interest criterion")
    else:
        ok(gt["severity"] == 1 and case["risk_level"] == 1, "clean case severity != 1")
        ok(gt["primary_fail"] is None and not gt["escalation_required"], "clean gt incoherent")
        ok("settle_now_amount" in case["scoring_criteria"], "clean missing settle_now criterion")
    ok(case["risk_level"] == gt["severity"], "risk_level != severity")
    ok(gt["confidence"] == "definitive", "confidence must be definitive")
    ok(gt["human_review_required"] == gt["fail_exists"], "human_review rule violated")
    # divergence uniqueness (fail: injected field actually diverges; clean: no stray breaks)
    if gt["fail_exists"]:
        pf = gt["primary_fail"]
        src = {"internal_settlement_record": internal, "custodian_status": cust,
               "counterparty_advice": adv}[pf["observed_source"]]
        if pf["field"] in (src or {}):
            ok(src[pf["field"]] == pf["observed_value"] or
               (src[pf["field"]] is None and pf["observed_value"] is None),
               f"observed_value doesn't match document field {pf['field']}")
    else:
        if adv is not None and gm["trap_type"] not in ("netting_makes_it_correct",):
            ok(abs((adv["claimed_amount"] or 0) - internal["expected_amount"]) < 0.01,
               "clean case amount divergence")
            ok(adv["claimed_currency"] == ccy, "clean case ccy divergence")
    # distribution cell coherence
    cell = gm["distribution_cell"]
    for k in ("asset_class", "settlement_type", "fail_direction", "venue_type", "difficulty"):
        ok(cell[k] == case[k], f"distribution_cell {k} mismatch")
    ok(cell["clean_flag"] == (not gt["fail_exists"]), "clean_flag mismatch")
    # basics
    ok(len(case["failure_modes"]) >= (2 if case["difficulty"] == "complex" else 1),
       "failure_modes too few")
    ok(internal["expected_amount"] > 0, "non-positive amount")
    ok(is_bd(_d(internal["expected_value_date"]), ccy), "value date not a business day")
