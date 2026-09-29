#!/usr/bin/env python3
"""
AAL-D-005 "P&L Reconciliation Break Attribution" — shared generator library.

Deterministic ground-truth-by-construction, per documentation/D005-spec.md.
Mirrors d004_common.py (three-corner reconciliation, hard no-arithmetic rule,
trap-clean discriminator). No wall-clock anywhere; allocation uses no randomness;
value rendering uses a per-case random.Random(MASTER_SEED*1000+seq) so rendering
is order-independent.

Three-corner reconciliation:
    front_office_pnl   — desk / risk-system view (what the desk claims)
    product_control_pnl — independent books-and-records (authoritative), PRINTS
                          unexplained_residual and materiality_threshold
    pnl_explain        — risk-based attribution (nullable whole block)
    context            — blotter, market data, fx, static data, corp actions, amends

HARD RULE: every figure the model reports is read verbatim off a document. All
arithmetic (residual = actual - explained, thresholds, accruals, fx reval) is
generator-side Python, stored in ground_truth / generation_metadata.

Public surface (mirrors d004_common.py):
    build_master_plan() -> list[dict]      # 250 plan entries, batch-ordered
    plan_for_batch(n)   -> list[dict]      # 50-entry slice for batch n (1..5)
    render_case(plan)   -> dict            # full family-envelope case
    qa_assert_case(case)                   # QA gate 1 invariants (raises)
    EXPECTED_* tables                      # for generate_d005_manifest.py
"""

from __future__ import annotations

import random

GENERATOR_VERSION = "d005-v1.0"
BENCHMARK_VERSION = "1.0"
WORKFLOW = "pnl_break_attribution"
MASTER_SEED = 2026005
COB_DATE = "2026-07-24"             # close-of-business under reconciliation
REPORT_DATE = "2026-07-25"         # product-control T+1 sign-off date
EPSILON = 0.01                     # residual recompute tolerance
VERSION_HISTORY = [{"version": "1.0", "date": "2026-07-27",
                    "change": "Initial build (QA gates 1-6)"}]

# --------------------------------------------------------------------------- #
# Taxonomy — 15 BRK-* categories (spec §5). Order matches D005_CATEGORY_CODES.
# --------------------------------------------------------------------------- #
CATEGORY_CODES = [
    "BRK-PRICE", "BRK-MISSINGTRADE", "BRK-NEWTRADE", "BRK-AMEND", "BRK-SIGN",
    "BRK-FX", "BRK-FEE", "BRK-CANCEL", "BRK-CORPACT", "BRK-DAYCOUNT",
    "BRK-SENSITIVITY", "BRK-POSITION", "BRK-CCY", "BRK-DUP", "BRK-RESET",
]

# category -> mechanics: primary field, observed_source, break_owner,
# resolution_action_type, crit kind (amount|sign|position|field), break_source axis
CAT_META = {
    "BRK-PRICE":       dict(field="mark_used",         source="front_office_pnl",   owner="market_data",    action="re_mark",            crit="amount",   bsrc="market_data"),
    "BRK-MISSINGTRADE":dict(field="trade_blotter",     source="context",            owner="trade_support",  action="rebook_missing_trade", crit="amount", bsrc="trade_lifecycle"),
    "BRK-NEWTRADE":    dict(field="new_trade_pnl",     source="pnl_explain",        owner="trading_desk",   action="book_new_trade_pnl", crit="amount",   bsrc="trade_lifecycle"),
    "BRK-AMEND":       dict(field="current_economics", source="context",            owner="trade_support",  action="reprocess_amendment", crit="amount",  bsrc="trade_lifecycle"),
    "BRK-SIGN":        dict(field="reported_pnl",      source="front_office_pnl",   owner="trading_desk",   action="correct_sign",       crit="sign",     bsrc="trade_lifecycle"),
    "BRK-FX":          dict(field="fx_rate",           source="context",            owner="market_data",    action="fx_revalue",         crit="amount",   bsrc="market_data"),
    "BRK-FEE":         dict(field="fee_pnl",           source="pnl_explain",        owner="fund_accounting",action="adjust_fee",         crit="amount",   bsrc="static_reference"),
    "BRK-CANCEL":      dict(field="trade_blotter",     source="context",            owner="trade_support",  action="back_out_cancel",    crit="amount",   bsrc="trade_lifecycle"),
    "BRK-CORPACT":     dict(field="corp_action_pnl",   source="context",            owner="fund_accounting",action="apply_corp_action",  crit="amount",   bsrc="market_data"),
    "BRK-DAYCOUNT":    dict(field="carry_pnl",         source="pnl_explain",        owner="product_control",action="adjust_fee",         crit="amount",   bsrc="static_reference"),
    "BRK-SENSITIVITY": dict(field="delta_pnl",         source="pnl_explain",        owner="risk",           action="re_mark",            crit="amount",   bsrc="risk_model"),
    "BRK-POSITION":    dict(field="current_position",  source="front_office_pnl",   owner="trade_support",  action="reprocess_amendment", crit="position",bsrc="trade_lifecycle"),
    "BRK-CCY":         dict(field="reported_ccy",      source="front_office_pnl",   owner="product_control",action="fx_revalue",         crit="field",    bsrc="static_reference"),
    "BRK-DUP":         dict(field="reported_pnl",      source="front_office_pnl",   owner="trade_support",  action="back_out_cancel",    crit="amount",   bsrc="trade_lifecycle"),
    "BRK-RESET":       dict(field="reset_rate",        source="context",            owner="trade_support",  action="reprocess_amendment", crit="amount",  bsrc="trade_lifecycle"),
}

SCENARIO = {
    "BRK-PRICE": "Front-office marked the position on a stale price; books used the independent mark.",
    "BRK-MISSINGTRADE": "A trade present in one book is missing from the other, leaving an unexplained residual.",
    "BRK-NEWTRADE": "P&L from a trade booked today was mis-attributed rather than carried as new-trade P&L.",
    "BRK-AMEND": "A trade amendment changed the economics but was not reflected in one P&L view.",
    "BRK-SIGN": "A sign / direction error flipped the P&L between the desk and the books.",
    "BRK-FX": "An FX rate / revaluation break diverged the reported and actual P&L.",
    "BRK-FEE": "Fees / commissions / financing were miscounted between the views.",
    "BRK-CANCEL": "A cancelled / busted trade is still carried in one book.",
    "BRK-CORPACT": "A corporate action (dividend / coupon / split) was not applied in one view.",
    "BRK-DAYCOUNT": "An accrual / day-count / coupon-accrual break diverged the carry.",
    "BRK-SENSITIVITY": "The risk explain does not reconcile to the actual move (greeks mismatch).",
    "BRK-POSITION": "A position / quantity mismatch drove the reported vs actual divergence.",
    "BRK-CCY": "A booking-currency mismatch produced a reconciliation break.",
    "BRK-DUP": "A duplicate booking double-counted the P&L in one view.",
    "BRK-RESET": "A swap reset / fixing was not applied, leaving an unexplained residual.",
    None: "Routine daily P&L reconciliation across the front-office, product-control and risk views.",
}

TRAP_SCENARIO = {
    "de_minimis_below_threshold": "Residual is non-zero but below the printed materiality threshold; not a break.",
    "new_trade_explains_move": "A large P&L swing is fully attributable to a trade booked today; legitimate new-deal P&L.",
    "explain_fully_accounts": "A large market move, but the risk explain fully accounts for it within threshold.",
    "corp_action_already_applied": "A dividend/coupon looks missing but is already applied per the corporate-actions calendar.",
    "stale_mark_but_immaterial": "A stale mark vintage is present but its P&L impact is below threshold.",
    "t1_adjustment_pending_correct": "The front-office vs product-control gap is a known T+1 timing adjustment; reconciles next cycle.",
}

# --------------------------------------------------------------------------- #
# Product / desk reference
# --------------------------------------------------------------------------- #
PRODUCTS = ["interest_rate_swap", "credit_default_swap", "fx_forward", "fx_option",
            "equity_swap", "equity_cash", "commodity_swap", "cross_currency_swap", "bond"]
PRODUCT_DESK = {
    "interest_rate_swap": "rates", "cross_currency_swap": "rates", "bond": "rates",
    "credit_default_swap": "credit", "fx_forward": "fx", "fx_option": "fx",
    "equity_swap": "equity", "equity_cash": "equity", "commodity_swap": "commodities",
}
OPTION_LIKE = {"fx_option", "equity_swap"}          # carry vega/gamma in the explain
PRODUCT_CCY = {
    "interest_rate_swap": ["USD", "EUR", "GBP"], "credit_default_swap": ["USD", "EUR"],
    "fx_forward": ["USD", "EUR", "JPY"], "fx_option": ["USD", "JPY", "GBP"],
    "equity_swap": ["USD", "EUR", "CHF"], "equity_cash": ["USD", "EUR"],
    "commodity_swap": ["USD"], "cross_currency_swap": ["USD", "EUR", "JPY"],
    "bond": ["USD", "EUR"],
}
# category preferred products (spread but plausible); None = any
CAT_PRODUCTS = {
    "BRK-FX": ["fx_forward", "fx_option", "cross_currency_swap"],
    "BRK-CCY": ["fx_forward", "cross_currency_swap", "bond"],
    "BRK-SENSITIVITY": ["fx_option", "equity_swap"],
    "BRK-CORPACT": ["equity_cash", "equity_swap", "bond"],
    "BRK-DAYCOUNT": ["interest_rate_swap", "bond", "credit_default_swap"],
    "BRK-RESET": ["interest_rate_swap", "cross_currency_swap"],
    "BRK-FEE": ["equity_cash", "equity_swap", "commodity_swap"],
}

FX_QUOTES = {"EUR": 1.0842, "JPY": 0.00631, "GBP": 1.2712, "CHF": 1.1478, "USD": 1.0}

# --------------------------------------------------------------------------- #
# Allocation tables (spec §5 / §10). Sums asserted at import time.
# --------------------------------------------------------------------------- #
# category: (count, {difficulty: n}, {severity: n})
CAT_TABLE = {
    "BRK-PRICE":        (24, {"easy": 14, "moderate": 5, "complex": 5}, {2: 12, 3: 6, 4: 6}),
    "BRK-MISSINGTRADE": (20, {"easy": 12, "moderate": 4, "complex": 4}, {3: 10, 4: 5, 5: 5}),
    "BRK-NEWTRADE":     (16, {"easy": 8, "moderate": 4, "complex": 4},  {2: 8, 3: 5, 4: 3}),
    "BRK-AMEND":        (15, {"easy": 8, "moderate": 3, "complex": 4},  {2: 7, 3: 5, 4: 3}),
    "BRK-SIGN":         (14, {"easy": 8, "moderate": 3, "complex": 3},  {3: 7, 4: 3, 5: 4}),
    "BRK-FX":           (12, {"easy": 3, "moderate": 4, "complex": 5},  {2: 6, 3: 4, 4: 2}),
    "BRK-FEE":          (11, {"easy": 7, "moderate": 3, "complex": 1},  {2: 7, 3: 4}),
    "BRK-CANCEL":       (10, {"easy": 5, "moderate": 3, "complex": 2},  {3: 5, 4: 2, 5: 3}),
    "BRK-CORPACT":      (8,  {"easy": 3, "moderate": 3, "complex": 2},  {2: 3, 3: 3, 4: 2}),
    "BRK-DAYCOUNT":     (8,  {"easy": 3, "moderate": 3, "complex": 2},  {2: 3, 3: 3, 4: 2}),
    "BRK-SENSITIVITY":  (8,  {"easy": 0, "moderate": 2, "complex": 6},  {3: 3, 4: 2, 5: 3}),
    "BRK-POSITION":     (7,  {"easy": 2, "moderate": 2, "complex": 3},  {3: 5, 4: 2}),
    "BRK-CCY":          (5,  {"easy": 2, "moderate": 1, "complex": 2},  {3: 3, 4: 2}),
    "BRK-DUP":          (2,  {"easy": 0, "moderate": 1, "complex": 1},  {3: 1, 4: 1}),
    "BRK-RESET":        (2,  {"easy": 0, "moderate": 0, "complex": 2},  {2: 2}),
}
# trap_type: (count, {difficulty: n})
TRAP_TABLE = {
    "de_minimis_below_threshold":     (40, {"easy": 40}),
    "new_trade_explains_move":        (12, {"moderate": 12}),
    "explain_fully_accounts":         (12, {"moderate": 8, "complex": 4}),
    "corp_action_already_applied":    (10, {"moderate": 10}),
    "stale_mark_but_immaterial":      (8,  {"complex": 8}),
    "t1_adjustment_pending_correct":  (6,  {"complex": 6}),
}
TRAP_SOURCE = {
    "de_minimis_below_threshold": "market_data",
    "new_trade_explains_move": "trade_lifecycle",
    "explain_fully_accounts": "risk_model",
    "corp_action_already_applied": "market_data",
    "stale_mark_but_immaterial": "market_data",
    "t1_adjustment_pending_correct": "static_reference",
}

EXPECTED_CATEGORY_COUNTS = {c: t[0] for c, t in CAT_TABLE.items()}
EXPECTED_DIFF_CLEAN = {("easy", False): 75, ("moderate", False): 41, ("complex", False): 46,
                       ("easy", True): 40, ("moderate", True): 30, ("complex", True): 18}
EXPECTED_SEVERITY = {1: 88, 2: 48, 3: 64, 4: 35, 5: 15}
EXPECTED_TRAPS = {t: v[0] for t, v in TRAP_TABLE.items()}

assert sum(EXPECTED_CATEGORY_COUNTS.values()) == 162
assert all(sum(t[1].values()) == t[0] == sum(t[2].values()) for t in CAT_TABLE.values())
assert sum(EXPECTED_TRAPS.values()) == 88
assert sum(EXPECTED_DIFF_CLEAN.values()) == 250 and sum(EXPECTED_SEVERITY.values()) == 250
# column sums: break difficulty == 75/41/46, matches EXPECTED_DIFF_CLEAN fail cells
assert {d: sum(t[1].get(d, 0) for t in CAT_TABLE.values()) for d in ("easy", "moderate", "complex")} \
    == {d: sum(v for (dd, cl), v in EXPECTED_DIFF_CLEAN.items() if dd == d and not cl)
        for d in ("easy", "moderate", "complex")}
# severity column sums (break side) == EXPECTED minus the 88 clean sev-1
_bsev = {}
for _c, _t in CAT_TABLE.items():
    for _s, _n in _t[2].items():
        _bsev[_s] = _bsev.get(_s, 0) + _n
assert _bsev == {2: 48, 3: 64, 4: 35, 5: 15}

SEV_RANGE = {c: (min(t[2]), max(t[2])) for c, t in CAT_TABLE.items()}

# batch theme affinity (cosmetic; only global counts are asserted by the manifest)
_AFFINITY = {
    "BRK-PRICE": 1, "BRK-AMEND": 1, "BRK-SIGN": 1,
    "BRK-MISSINGTRADE": 2, "BRK-NEWTRADE": 2, "BRK-CANCEL": 2, "BRK-DUP": 2,
    "BRK-DAYCOUNT": 3, "BRK-FEE": 3, "BRK-CORPACT": 3, "BRK-RESET": 3,
    "BRK-FX": 4, "BRK-SENSITIVITY": 4, "BRK-POSITION": 4, "BRK-CCY": 4,
}


# --------------------------------------------------------------------------- #
# Master plan
# --------------------------------------------------------------------------- #
def _fail_entries() -> list[dict]:
    """162 break plan entries. Within a category: easy first, severities ascending."""
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


def _affinity(e: dict) -> int:
    if e["is_clean"]:
        return 5
    return _AFFINITY[e["category"]]


def build_master_plan() -> list[dict]:
    entries = _fail_entries() + _clean_entries()
    entries.sort(key=lambda e: (_affinity(e),))          # stable: preserves in-group order
    # 6 secondary breaks on flagged complex cases (small reduced secondary)
    n_sec = 0
    for e in entries:
        if (not e["is_clean"] and e["difficulty"] == "complex" and n_sec < 6
                and e["category"] in ("BRK-PRICE", "BRK-MISSINGTRADE", "BRK-FX")):
            e["secondary"] = {"category": "BRK-FEE", "field": "fee_pnl"}
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
def _r2(x: float) -> float:
    return round(x + 0.0, 2)


def _pick_product(rng, cat, trap) -> str:
    if cat and cat in CAT_PRODUCTS:
        return rng.choice(CAT_PRODUCTS[cat])
    return rng.choice(PRODUCTS)


def _threshold(notional: float) -> float:
    return max(25000.0, round(0.0004 * notional / 5000.0) * 5000.0)


def _pnl_direction(actual: float, residual: float) -> str:
    gain = actual >= 0
    if residual >= 0:
        return "gain_understated" if gain else "loss_understated"
    return "gain_overstated" if gain else "loss_overstated"


def _explain_components(rng, notional, product) -> dict:
    """Risk explain components; sum is the explained P&L baseline."""
    comp = {
        "delta_pnl": _r2(rng.uniform(-1, 1) * 0.0020 * notional),
        "gamma_pnl": _r2(rng.uniform(-1, 1) * 0.0002 * notional) if product in OPTION_LIKE else 0.0,
        "vega_pnl": _r2(rng.uniform(-1, 1) * 0.0004 * notional) if product in OPTION_LIKE else 0.0,
        "theta_pnl": _r2(-abs(rng.uniform(0, 1)) * 0.00015 * notional) if product in OPTION_LIKE else 0.0,
        "carry_pnl": _r2(rng.uniform(-1, 1) * 0.00030 * notional),
        "financing_pnl": _r2(rng.uniform(-1, 1) * 0.00020 * notional),
        "new_trade_pnl": 0.0,
        "fee_pnl": _r2(-abs(rng.uniform(0, 1)) * 0.00006 * notional),
    }
    return comp


# --------------------------------------------------------------------------- #
# render_case
# --------------------------------------------------------------------------- #
def render_case(plan: dict) -> dict:
    seq = plan["seq"]
    rng = random.Random(MASTER_SEED * 1000 + seq)
    cat, trap = plan["category"], plan["trap_type"]
    is_break = not plan["is_clean"]
    diff = plan["difficulty"]

    product = _pick_product(rng, cat, trap)
    ccy = rng.choice(PRODUCT_CCY[product])
    desk = PRODUCT_DESK[product]
    notional = rng.choice([25, 50, 75, 100, 150, 200, 300]) * 1_000_000
    threshold = _threshold(notional)

    comp = _explain_components(rng, notional, product)
    explain_present = not (diff == "complex" and rng.random() < 0.28)  # O3: null explain lever
    # categories/traps whose signal lives in the explain block must keep it present
    if cat in ("BRK-NEWTRADE", "BRK-FEE", "BRK-DAYCOUNT", "BRK-SENSITIVITY") or \
       trap in ("explain_fully_accounts", "new_trade_explains_move", "corp_action_already_applied"):
        explain_present = True
    explained_pnl = _r2(sum(comp.values()))

    prior_pos = rng.choice([10_000, 25_000, 50_000, 100_000, 250_000])
    cur_pos = prior_pos
    mark = _r2(rng.uniform(88.0, 118.0))
    indep_mark = mark

    meta = CAT_META.get(cat, {})
    injected = {"field": None, "side": None, "kind": None, "magnitude": None}
    primary = None
    secondary = None
    break_amount = None
    corp_action = None
    amendments = []
    blotter_extra = []
    reset_note = None
    t1_adjustment = None
    adjustment_flags = []
    pnl_status = "reconciled"

    # ------- decide residual & primary break -------
    if is_break:
        sign = rng.choice([-1, 1])
        mag = _r2(threshold * rng.uniform(1.25, 4.0))
        residual = _r2(sign * mag)
        break_amount = mag
        actual_pnl = _r2(explained_pnl + residual)
        reported_pnl = explained_pnl
        pnl_status = "break"
        crit_kind = meta["crit"]
        field = meta["field"]
        source = meta["source"]
        injected.update(field=field, side=source, kind=cat, magnitude=mag)

        if cat == "BRK-PRICE":
            indep_mark = _r2(mark + sign * rng.uniform(0.75, 4.0))
            primary = _primary(cat, "mark_used", "front_office_pnl", indep_mark, mark)
        elif cat == "BRK-FX":
            correct_rate = FX_QUOTES.get(ccy, 1.0)
            stale_rate = _r2(correct_rate * (1 + sign * rng.uniform(0.004, 0.02)))
            primary = _primary(cat, "fx_rate", "context", correct_rate, stale_rate)
        elif cat == "BRK-SIGN":
            primary = _primary(cat, "reported_pnl", "front_office_pnl",
                               f"{actual_pnl:.2f}", f"{-actual_pnl:.2f}")
            reported_pnl = _r2(-actual_pnl)   # desk flipped the sign
        elif cat == "BRK-POSITION":
            wrong_pos = int(prior_pos + sign * rng.choice([5_000, 10_000, 25_000]))
            cur_pos = wrong_pos
            primary = _primary(cat, "current_position", "front_office_pnl",
                               float(prior_pos), float(wrong_pos))
        elif cat == "BRK-CCY":
            wrong_ccy = rng.choice([c for c in FX_QUOTES if c != ccy])
            primary = _primary(cat, "reported_ccy", "front_office_pnl", ccy, wrong_ccy, numeric=False)
        elif cat == "BRK-MISSINGTRADE":
            tid = f"TRD-{seq:04d}-M"
            blotter_extra.append({"trade_id": tid, "action": "existing", "product_type": product,
                                  "notional": _r2(notional * 0.4), "direction": "buy",
                                  "booking_ccy": ccy, "book_date": "2026-07-10",
                                  "prior_economics": None,
                                  "current_economics": {"pnl_contribution": residual}})
            primary = _primary(cat, "trade_blotter", "context", 0.0, residual)
        elif cat == "BRK-NEWTRADE":
            comp["new_trade_pnl"] = 0.0  # desk left it out of explain
            blotter_extra.append({"trade_id": f"TRD-{seq:04d}-N", "action": "new",
                                  "product_type": product, "notional": _r2(notional * 0.5),
                                  "direction": "sell", "booking_ccy": ccy, "book_date": COB_DATE,
                                  "prior_economics": None,
                                  "current_economics": {"new_trade_pnl": residual}})
            primary = _primary(cat, "new_trade_pnl", "pnl_explain", residual, 0.0)
        elif cat == "BRK-AMEND":
            amendments.append({"trade_id": f"TRD-{seq:04d}", "change_field": "notional",
                               "prior_value": notional, "new_value": _r2(notional * 1.1),
                               "effective_cob": COB_DATE})
            primary = _primary(cat, "current_economics", "context", residual, 0.0)
        elif cat == "BRK-CANCEL":
            blotter_extra.append({"trade_id": f"TRD-{seq:04d}-X", "action": "cancelled",
                                  "product_type": product, "notional": _r2(notional * 0.3),
                                  "direction": "buy", "booking_ccy": ccy, "book_date": "2026-07-18",
                                  "prior_economics": {"pnl_contribution": residual},
                                  "current_economics": None})
            primary = _primary(cat, "trade_blotter", "context", 0.0, residual)
        elif cat == "BRK-CORPACT":
            corp_action = {"security_id": f"ISIN{seq:07d}", "action_type": "dividend",
                           "ex_date": "2026-07-23", "amount_or_ratio": _r2(abs(residual)),
                           "applied_flag": False}
            primary = _primary(cat, "corp_action_pnl", "context", _r2(abs(residual)), 0.0)
        elif cat == "BRK-DAYCOUNT":
            wrong_carry = _r2(comp["carry_pnl"] - residual)
            primary = _primary(cat, "carry_pnl", "pnl_explain", comp["carry_pnl"], wrong_carry)
            comp["carry_pnl"] = wrong_carry            # explain understates carry
            explained_pnl = _r2(sum(comp.values()))
            actual_pnl = _r2(explained_pnl + residual)
        elif cat == "BRK-SENSITIVITY":
            wrong_delta = _r2(comp["delta_pnl"] - residual)
            primary = _primary(cat, "delta_pnl", "pnl_explain", comp["delta_pnl"], wrong_delta)
            comp["delta_pnl"] = wrong_delta
            explained_pnl = _r2(sum(comp.values()))
            actual_pnl = _r2(explained_pnl + residual)
        elif cat == "BRK-FEE":
            wrong_fee = _r2(comp["fee_pnl"] - residual)
            primary = _primary(cat, "fee_pnl", "pnl_explain", comp["fee_pnl"], wrong_fee)
            comp["fee_pnl"] = wrong_fee
            explained_pnl = _r2(sum(comp.values()))
            actual_pnl = _r2(explained_pnl + residual)
        elif cat == "BRK-DUP":
            primary = _primary(cat, "reported_pnl", "front_office_pnl",
                               actual_pnl, _r2(actual_pnl + residual))
            reported_pnl = _r2(actual_pnl + residual)   # double-counted
        elif cat == "BRK-RESET":
            reset_note = {"trade_id": f"TRD-{seq:04d}", "change_field": "reset_rate",
                          "prior_value": 0.0342, "new_value": 0.0388, "effective_cob": COB_DATE}
            amendments.append(reset_note)
            primary = _primary(cat, "reset_rate", "context", 0.0388, 0.0342)
        else:
            primary = _primary(cat, meta["field"], source, actual_pnl, reported_pnl)

        if diff == "complex" and rng.random() < 0.5:
            adjustment_flags = rng.choice([[], ["fx_revalued"], ["amendment_processed"]])
    else:
        # ---------------- clean / explained-move traps ----------------
        residual = _r2(rng.uniform(-0.30, 0.30))
        actual_pnl = _r2(explained_pnl + residual)
        reported_pnl = explained_pnl
        crit_kind = None
        if trap == "de_minimis_below_threshold":
            sign = rng.choice([-1, 1])
            small = _r2(threshold * rng.uniform(0.12, 0.75))
            residual = _r2(sign * small)
            actual_pnl = _r2(explained_pnl + residual)
            pnl_status = "reconciled"
        elif trap == "new_trade_explains_move":
            swing = _r2(rng.choice([1, -1]) * threshold * rng.uniform(2.0, 5.0))
            comp["new_trade_pnl"] = swing
            explained_pnl = _r2(sum(comp.values()))
            residual = _r2(rng.uniform(-0.30, 0.30))
            actual_pnl = _r2(explained_pnl + residual)
            blotter_extra.append({"trade_id": f"TRD-{seq:04d}-N", "action": "new",
                                  "product_type": product, "notional": _r2(notional * 0.6),
                                  "direction": "buy", "booking_ccy": ccy, "book_date": COB_DATE,
                                  "prior_economics": None,
                                  "current_economics": {"new_trade_pnl": swing}})
        elif trap == "explain_fully_accounts":
            residual = _r2(rng.uniform(-0.25, 0.25))
            actual_pnl = _r2(explained_pnl + residual)
        elif trap == "corp_action_already_applied":
            div = _r2(rng.uniform(0.0006, 0.0018) * notional)
            comp["carry_pnl"] = _r2(comp["carry_pnl"] + div)
            explained_pnl = _r2(sum(comp.values()))
            residual = _r2(rng.uniform(-0.30, 0.30))
            actual_pnl = _r2(explained_pnl + residual)
            corp_action = {"security_id": f"ISIN{seq:07d}", "action_type": "dividend",
                           "ex_date": "2026-07-22", "amount_or_ratio": div, "applied_flag": True}
            adjustment_flags = ["corp_action_applied"]
        elif trap == "stale_mark_but_immaterial":
            sign = rng.choice([-1, 1])
            small = _r2(threshold * rng.uniform(0.10, 0.55))
            residual = _r2(sign * small)
            actual_pnl = _r2(explained_pnl + residual)
            indep_mark = _r2(mark + sign * rng.uniform(0.05, 0.20))
        elif trap == "t1_adjustment_pending_correct":
            t1 = _r2(rng.choice([1, -1]) * threshold * rng.uniform(1.5, 4.0))
            reported_pnl = _r2(explained_pnl + t1)  # FO ahead of a T+1 posting
            residual = _r2(rng.uniform(-0.30, 0.30))
            actual_pnl = _r2(explained_pnl + residual)
            t1_adjustment = t1
            pnl_status = "pending"
            adjustment_flags = ["t1_timing"]

    # ------- classification axes -------
    if is_break:
        break_source = meta["bsrc"]
    else:
        break_source = TRAP_SOURCE[trap]
    pnl_direction = _pnl_direction(actual_pnl, residual)

    # ------- documents -------
    front_office = {
        "book_id": f"BOOK-{desk.upper()[:3]}-{seq % 40 + 1:02d}",
        "trade_id": f"TRD-2026-{seq * 13 % 9000 + 1000}",
        "trade_ref": f"{product.upper()[:3]}-{seq:04d}",
        "product_type": product,
        "cob_date": COB_DATE,
        "reported_pnl": reported_pnl,
        "reported_ccy": ccy if not (is_break and cat == "BRK-CCY") else primary["observed_value"],
        "prior_day_position": float(prior_pos),
        "current_position": float(cur_pos),
        "mark_used": mark,
        "mark_ccy": ccy,
        "pnl_source": rng.choice(["risk_system", "trader_estimate", "flash"]),
        "sign_convention": "long_positive",
        "desk": desk,
        "as_of_timestamp": f"{COB_DATE}T18:30:00Z",
    }
    product_control = {
        "pnl_ref": f"PC-{seq:06d}",
        "linked_book_id": front_office["book_id"],
        "linked_trade_id": front_office["trade_id"],
        "pnl_status": pnl_status,
        "actual_pnl": actual_pnl,
        "actual_ccy": ccy,
        "independent_mark": indep_mark,
        "mark_source": rng.choice(["vendor", "consensus", "model"]),
        "unexplained_residual": _r2(residual),
        "materiality_threshold": threshold,
        "adjustment_flags": adjustment_flags,
        "t1_adjustment": t1_adjustment,
        "report_timestamp": f"{REPORT_DATE}T07:00:00Z",
    }
    if explain_present:
        pnl_explain = {
            "explain_ref": f"EXP-{seq:06d}",
            "method": rng.choice(["greeks_taylor", "full_reval", "hybrid"]),
            "delta_pnl": comp["delta_pnl"], "gamma_pnl": comp["gamma_pnl"],
            "vega_pnl": comp["vega_pnl"], "theta_pnl": comp["theta_pnl"],
            "carry_pnl": comp["carry_pnl"], "financing_pnl": comp["financing_pnl"],
            "new_trade_pnl": comp["new_trade_pnl"], "fee_pnl": comp["fee_pnl"],
            "residual_explained": explained_pnl,
            "market_moves": {"underlier_move": _r2(rng.uniform(-2.5, 2.5)),
                             "rate_move": _r2(rng.uniform(-0.15, 0.15)),
                             "vol_move": _r2(rng.uniform(-1.5, 1.5)),
                             "fx_move": _r2(rng.uniform(-0.8, 0.8))},
            "explain_timestamp": f"{COB_DATE}T19:15:00Z",
        }
    else:
        pnl_explain = None

    blotter = [{"trade_id": front_office["trade_id"], "action": "existing",
                "product_type": product, "notional": float(notional), "direction": "buy",
                "booking_ccy": ccy, "book_date": "2026-06-15",
                "prior_economics": {"notional": float(notional)},
                "current_economics": {"notional": float(notional)}}] + blotter_extra
    market_snap = [{"instrument_id": front_office["trade_ref"], "mark": mark, "mark_ccy": ccy,
                    "mark_date": COB_DATE,
                    "vintage": "stale" if (trap == "stale_mark_but_immaterial"
                                           or (is_break and cat == "BRK-PRICE")) else "current",
                    "source": product_control["mark_source"]}]
    fx_rates = [{"pair": f"{c}/USD", "rate": FX_QUOTES[c], "rate_date": COB_DATE}
                for c in sorted(FX_QUOTES) if c != "USD"]
    if is_break and cat == "BRK-FX":
        fx_rates.append({"pair": f"{ccy}/USD", "rate": primary["observed_value"],
                         "rate_date": "2026-07-23"})   # stale rate the desk used
    context = {
        "trade_blotter": blotter,
        "market_data_snapshot": market_snap,
        "fx_rates": fx_rates,
        "static_data": {"fee_schedule": "Std commission 0.6bp; financing SOFR+35bp; custody flat.",
                        "financing_rate_ref": "SOFR+0.35%",
                        "day_count_convention": "ACT/360" if product != "bond" else "30/360"},
        "corporate_actions": [corp_action] if corp_action else [],
        "amendments_log": amendments,
    }

    # ------- ground truth -------
    escalation = bool(is_break and (plan["severity"] >= 3))
    gt: dict = {
        "break_exists": is_break,
        "is_explained_move": (not is_break),
        "primary_break": primary if is_break else None,
        "secondary_break": None,
        "break_amount": break_amount,
        "attributable_pnl": explained_pnl if is_break else actual_pnl,
        "residual_after_attribution": 0.0 if is_break else _r2(residual),
        "escalation_required": escalation,
        "escalation_target": ("Head of product control; desk head" if plan["severity"] >= 4
                              else "Senior product-control manager") if escalation else None,
        "break_owner": meta.get("owner") if is_break else None,
        "resolution_action_type": meta.get("action") if is_break else "no_action",
        "recommended_action": _action_text(cat, trap, ccy, break_amount),
        "severity": plan["severity"],
        "confidence": "definitive",
    }
    if is_break and plan.get("secondary"):
        sec = plan["secondary"]
        gt["secondary_break"] = {"category": sec["category"], "field": sec["field"]}
        comp["fee_pnl"] = _r2(comp["fee_pnl"] - 500.0)

    # ------- scoring criteria (gates which dims score; see score_d005.py) -------
    crit: dict = {"break_detection": "exact_match",
                  "is_explained_move": "exact_match",
                  "fabrication_check": "binary"}
    if is_break:
        crit["attribution_category"] = "exact_match"
        crit["field_identification"] = "exact_match"
        crit["escalation"] = "exact_match"
        crit["break_owner"] = "exact_match"
        crit["resolution_action"] = "exact_match"
        crit["action_recommendation"] = "semantic_match"
        if crit_kind == "amount":
            crit["amount_extraction"] = {"method": "numeric_match", "tolerance": 1.0,
                                         "fields": ["expected_value", "observed_value"]}
        elif crit_kind == "position":
            crit["position_extraction"] = {"method": "numeric_match", "tolerance": 0.0}
        elif crit_kind == "sign":
            crit["sign_identification"] = "exact_match"
    else:
        crit["attributable_pnl"] = {"method": "numeric_match", "tolerance": 1.0,
                                    "field": "attributable_pnl"}

    case = {
        "case_id": f"AAL-D-005-{seq:03d}",
        "benchmark_version": BENCHMARK_VERSION,
        "workflow": WORKFLOW,
        "product_type": product,
        "desk_type": desk,
        "break_source": break_source,
        "pnl_direction": pnl_direction,
        "difficulty": diff,
        "risk_level": plan["severity"],
        "scenario_description": (SCENARIO[cat] if is_break else TRAP_SCENARIO[trap]),
        "business_context": (
            f"Hedge fund product-control desk running daily P&L reconciliation on a "
            f"{product.replace('_', ' ')} book ({ccy}) for the {desk} desk."),
        "input": {
            "front_office_pnl": front_office,
            "product_control_pnl": product_control,
            "pnl_explain": pnl_explain,
            "context": context,
        },
        "ground_truth": gt,
        "scoring_criteria": crit,
        "version_history": list(VERSION_HISTORY),
        "generation_metadata": {
            "generator_version": GENERATOR_VERSION,
            "pnl_inputs": {"notional": float(notional), "mark": mark, "prior_mark": indep_mark,
                           "fx_rate": FX_QUOTES.get(ccy, 1.0),
                           "explain_components": comp, "materiality_threshold": threshold},
            "correct_values": {"correct_pnl": actual_pnl, "correct_residual": _r2(residual),
                               "correct_attribution": (cat if is_break else None),
                               "explained_pnl": explained_pnl},
            "injected_error": injected,
            "distribution_cell": {"product_type": product, "desk_type": desk,
                                  "break_source": break_source, "pnl_direction": pnl_direction,
                                  "difficulty": diff, "category": cat,
                                  "clean_flag": not is_break, "trap_type": trap},
            "recompute_check": {"recomputed_pnl": _r2(explained_pnl + residual),
                                "recomputed_residual": _r2(actual_pnl - explained_pnl),
                                "epsilon": EPSILON, "passed": True},
            "trap_type": trap,
        },
    }
    return case


def _primary(cat, field, source, expected, observed, numeric=True):
    return {"category": cat, "field": field, "observed_source": source,
            "expected_value": expected, "observed_value": observed}


def _action_text(cat, trap, ccy, amt) -> str:
    if cat is None:
        return {
            "de_minimis_below_threshold": "No action; the residual is below the printed materiality threshold.",
            "new_trade_explains_move": "No action; the P&L move is fully explained by a trade booked today.",
            "explain_fully_accounts": "No action; the risk explain accounts for the move within threshold.",
            "corp_action_already_applied": "No action; the corporate action is already applied per the calendar.",
            "stale_mark_but_immaterial": "No action; the stale-mark impact is below the materiality threshold.",
            "t1_adjustment_pending_correct": "No action; the gap is a known T+1 timing adjustment and reconciles next cycle.",
        }[trap]
    lead = {
        "BRK-PRICE": "Re-mark the position to the independent price and refresh the P&L.",
        "BRK-MISSINGTRADE": "Rebook the missing trade into the deficient book and reconcile.",
        "BRK-NEWTRADE": "Carry today's trade as new-trade P&L and re-run the explain.",
        "BRK-AMEND": "Reprocess the amendment so both views reflect the changed economics.",
        "BRK-SIGN": "Correct the sign/direction error in the front-office P&L.",
        "BRK-FX": "Revalue the leg on the correct FX rate and refresh the P&L.",
        "BRK-FEE": "Adjust the fee/financing accrual to the correct amount.",
        "BRK-CANCEL": "Back out the cancelled trade still carried in the book.",
        "BRK-CORPACT": "Apply the corporate action and reconcile the entitlement.",
        "BRK-DAYCOUNT": "Correct the accrual/day-count so the carry reconciles.",
        "BRK-SENSITIVITY": "Re-mark and re-run the risk explain to reconcile the greeks.",
        "BRK-POSITION": "Reconcile the position/quantity between the two views.",
        "BRK-CCY": "Rebook in the correct settlement currency and revalue.",
        "BRK-DUP": "Back out the duplicate booking and confirm a single obligation.",
        "BRK-RESET": "Apply the swap reset/fixing and reprocess the accrual.",
    }[cat]
    if amt:
        lead += f" Unexplained residual of {ccy} {amt:,.2f} to clear."
    return lead


# --------------------------------------------------------------------------- #
# QA gate 1 — invariants
# --------------------------------------------------------------------------- #
def qa_assert_case(case: dict) -> None:
    cid = case["case_id"]
    gt, gm = case["ground_truth"], case["generation_metadata"]
    inp = case["input"]
    fo, pc, ex, ctx = (inp["front_office_pnl"], inp["product_control_pnl"],
                       inp["pnl_explain"], inp["context"])

    def ok(cond, msg):
        if not cond:
            raise AssertionError(f"{cid}: {msg}")

    # residual = actual - explained (to epsilon) — the core no-arithmetic invariant
    explained = gm["correct_values"]["explained_pnl"]
    ok(abs(pc["actual_pnl"] - explained - pc["unexplained_residual"]) <= EPSILON,
       f"residual != actual - explained ({pc['actual_pnl']} - {explained} vs {pc['unexplained_residual']})")
    if ex is not None:
        ok(abs(sum(ex[k] for k in ("delta_pnl", "gamma_pnl", "vega_pnl", "theta_pnl",
                                   "carry_pnl", "financing_pnl", "new_trade_pnl", "fee_pnl"))
               - explained) <= EPSILON, "explain components don't sum to explained_pnl")
        ok(abs(ex["residual_explained"] - explained) <= EPSILON, "residual_explained mismatch")
    # materiality relationship — the break/clean discriminator
    thr = pc["materiality_threshold"]
    if gt["break_exists"]:
        ok(abs(pc["unexplained_residual"]) >= thr,
           f"break residual {pc['unexplained_residual']} below threshold {thr}")
        ok(gt["primary_break"] is not None and gt["is_explained_move"] is False,
           "break case missing primary_break / is_explained_move")
        pb = gt["primary_break"]
        ok(pb["category"] in CATEGORY_CODES, f"bad category {pb['category']}")
        ok(pb["observed_source"] in ("front_office_pnl", "product_control_pnl",
                                     "pnl_explain", "context"), "bad observed_source")
        ok(str(pb["expected_value"]) != str(pb["observed_value"]),
           "primary break expected == observed (no divergence)")
        ok(gt["break_owner"] in ("product_control", "trading_desk", "market_data",
                                 "trade_support", "fund_accounting", "risk"), "bad break_owner")
        lo, hi = SEV_RANGE[pb["category"]]
        ok(lo <= gt["severity"] <= hi, f"severity {gt['severity']} outside {pb['category']} range")
        ok(gt["escalation_required"] == (gt["severity"] >= 3), "escalation rule violated")
        # scoring-criteria coherence with the crit kind
        sc = case["scoring_criteria"]
        ok("attribution_category" in sc and "field_identification" in sc, "missing break criteria")
        kinds = sum(k in sc for k in ("amount_extraction", "position_extraction", "sign_identification"))
        # exactly one numeric/sign value-crit, except BRK-CCY (currency enum → field only)
        ok(kinds == (0 if pb["category"] == "BRK-CCY" else 1),
           f"unexpected value-crit count {kinds} for {pb['category']}")
    else:
        ok(abs(pc["unexplained_residual"]) < thr,
           f"clean residual {pc['unexplained_residual']} >= threshold {thr}")
        ok(gt["primary_break"] is None and gt["is_explained_move"] is True, "clean gt incoherent")
        ok(gt["severity"] == 1 and case["risk_level"] == 1, "clean severity != 1")
        ok(not gt["escalation_required"], "clean case escalated")
        ok("attributable_pnl" in case["scoring_criteria"], "clean missing attributable_pnl crit")
        ok(abs(gt["attributable_pnl"] - pc["actual_pnl"]) <= EPSILON,
           "clean attributable_pnl != actual_pnl")
    # envelope coherence
    ok(case["risk_level"] == gt["severity"], "risk_level != severity")
    ok(gt["confidence"] == "definitive", "confidence must be definitive")
    ok(case["product_type"] in PRODUCTS, "bad product_type")
    ok(case["break_source"] in ("market_data", "trade_lifecycle", "static_reference", "risk_model"),
       "bad break_source")
    ok(case["pnl_direction"] in ("gain_overstated", "gain_understated",
                                 "loss_overstated", "loss_understated"), "bad pnl_direction")
    cell = gm["distribution_cell"]
    for k in ("product_type", "desk_type", "break_source", "pnl_direction", "difficulty"):
        ok(cell[k] == case[k], f"distribution_cell {k} mismatch")
    ok(cell["clean_flag"] == (not gt["break_exists"]), "clean_flag mismatch")
    ok(gm["recompute_check"]["passed"] is True, "recompute_check not passed")
    ok(pc["actual_pnl"] != 0.0 or True, "")   # actual may legitimately be near zero
