"""
AAL-D-006 — Collateral Eligibility & Substitution — generator core.

Same architecture as D-001..D-005: cases are generated deterministically with
ground truth *by construction*. The evaluated model classifies eligibility and
recommends a substitute; it NEVER performs arithmetic — every figure (haircut,
post-haircut value, concentration %, shortfall) is computed here and printed
into the case's valuation summary.

Task (per case): given a bilateral CSA eligibility schedule, an already-posted
collateral basket, and a proposed new collateral piece, decide:
  - eligible (bool)
  - reason_category (one ELIG-* code) if not eligible
  - offending_field
  - exception_value (dollar magnitude of the exception; components are printed)
  - substitution_recommendation (an eligible asset type to post instead)

Scope (v1.0): bilateral CSA eligibility only (reg-IM/SIMM -> D-007); substitution
= "name one eligible asset type"; asset universe CASH/SOV/CORP/EQUITY/MMF; 12
ELIG-* categories. 250 cases = 162 ineligible + 88 traps.

Stdlib only. Deterministic: every case seeded by its index.
Concentration convention: measured on market value, base-currency cash excluded.
Limits: per-issuer 20%, per-asset-class 50%, per-currency 60%.
"""
from __future__ import annotations

import json
import random
from collections import Counter

ASSET_TYPES = ["CASH", "SOV", "CORP", "EQUITY", "MMF"]
RATINGS = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
           "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-"]
RATING_ORD = {r: i for i, r in enumerate(RATINGS)}
CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF"]

SOV_ISSUERS = ["US Treasury", "German Bund", "UK Gilt", "JGB", "Swiss Confederation"]
CORP_ISSUERS = ["Apple Inc", "Toyota Motor", "Nestle SA", "Siemens AG", "BP plc",
                "JPMorgan Chase", "Volkswagen AG", "Unilever plc"]
EQUITY_ISSUERS = CORP_ISSUERS
MMF_ISSUERS = ["BlackRock Liquidity", "Fidelity Govt MMF", "JPM Liquidity MMF", "Vanguard MMF"]

BASE_HAIRCUT = {"CASH": 0.0, "MMF": 1.0, "SOV": 1.0, "CORP": 4.0, "EQUITY": 15.0}
FX_HAIRCUT = 8.0

CATEGORIES = [
    "ELIG-ASSETTYPE", "ELIG-RATING", "ELIG-CCY",
    "ELIG-WRONGWAY", "ELIG-MATURITY", "ELIG-DENOM", "ELIG-DOCS",
    "ELIG-HAIRCUT", "ELIG-CONC-ISSUER", "ELIG-CONC-CLASS",
    "ELIG-CONC-CCY", "ELIG-SHORTFALL",
]  # NOTE: order == oracle priority order

TRAP_TYPES = [
    "TRAP-RATING-AT-FLOOR", "TRAP-CONC-JUST-UNDER", "TRAP-HAIRCUT-CORRECT",
    "TRAP-MATURITY-IN-BAND", "TRAP-CCY-PERMITTED", "TRAP-COVERS-EXACTLY",
]


def rating_eligible(rating, min_rating):
    return RATING_ORD[rating] <= RATING_ORD[min_rating]


def correct_haircut(piece, base_ccy):
    h = BASE_HAIRCUT[piece["asset_type"]]
    if piece["asset_type"] in ("SOV", "CORP"):
        h += 0.5 * max(0, RATING_ORD[piece["rating"]] - RATING_ORD["AA"])
        h += min(6.0, 0.25 * max(0, piece["maturity_years"] - 5))
    if piece["asset_type"] == "EQUITY" and RATING_ORD[piece["rating"]] > RATING_ORD["A-"]:
        h += 5.0
    if piece["asset_type"] != "CASH" and piece["currency"] != base_ccy:
        h += FX_HAIRCUT
    return round(h, 2)


def php(market_value, haircut_pct):
    return round(market_value * (1.0 - haircut_pct / 100.0), 2)


def concentrations(pieces, base_ccy):
    scored = [p for p in pieces if not (p["asset_type"] == "CASH" and p["currency"] == base_ccy)]
    total = sum(p["market_value"] for p in scored) or 1.0
    bi, bc, bccy = {}, {}, {}
    for p in scored:
        bi[p["issuer"]] = bi.get(p["issuer"], 0.0) + p["market_value"]
        bc[p["asset_type"]] = bc.get(p["asset_type"], 0.0) + p["market_value"]
        bccy[p["currency"]] = bccy.get(p["currency"], 0.0) + p["market_value"]
    pct = lambda d: {k: round(100.0 * v / total, 2) for k, v in d.items()}
    return {"by_issuer": pct(bi), "by_class": pct(bc), "by_ccy": pct(bccy)}


def build_schedule(rng):
    base_ccy = rng.choice(CURRENCIES)
    eligible_ccys = sorted(set([base_ccy] + rng.sample([c for c in CURRENCIES if c != base_ccy], 2)),
                           key=CURRENCIES.index)
    corp_or_eq = rng.choice(["CORP", "EQUITY"])          # exactly one of CORP/EQUITY eligible
    eligible_types = ["CASH", "SOV", "MMF", corp_or_eq]  # 3 non-cash classes; the other stays ineligible
    return {
        "base_currency": base_ccy,
        "eligible_asset_types": sorted(set(eligible_types), key=ASSET_TYPES.index),
        "eligible_currencies": eligible_ccys,
        "min_rating": rng.choice(["AA-", "A", "A-", "BBB+"]),
        "max_maturity_years": 30,
        "min_denomination": 10_000,
        "concentration_limits_pct": {"per_issuer": 20, "per_asset_class": 50, "per_currency": 60},
        "wrong_way_issuers": rng.sample(CORP_ISSUERS, 2),
        "notes": "Concentration on market value; base-currency cash excluded.",
    }


def _issuer(asset_type, rng):
    return {"CASH": "—", "SOV": rng.choice(SOV_ISSUERS), "CORP": rng.choice(CORP_ISSUERS),
            "EQUITY": rng.choice(EQUITY_ISSUERS), "MMF": rng.choice(MMF_ISSUERS)}[asset_type]


def clean_piece(rng, sched, *, asset_type=None, currency=None, issuer=None, mv=None):
    non_cash = [t for t in sched["eligible_asset_types"] if t != "CASH"]
    at = asset_type or rng.choice(non_cash)
    ccy = currency or rng.choice(sched["eligible_currencies"])
    if at == "CASH":
        rating, maturity = "AAA", 0
    else:
        rating = rng.choice([r for r in RATINGS if rating_eligible(r, sched["min_rating"])])
        maturity = rng.choice([1, 2, 3, 5, 7, 10]) if at in ("SOV", "CORP") else 0
    iss = issuer or _issuer(at, rng)
    while iss in sched["wrong_way_issuers"]:
        iss = _issuer(at, rng)
    p = {"asset_type": at, "issuer": iss, "currency": ccy, "rating": rating,
         "maturity_years": maturity, "market_value": float(mv or rng.choice([500_000, 1_000_000, 1_500_000])),
         "docs_current": True}
    p["applied_haircut_pct"] = correct_haircut(p, sched["base_currency"])
    return p


def oracle(piece, basket, sched, required):
    base = sched["base_currency"]
    lim = sched["concentration_limits_pct"]
    ch = correct_haircut(piece, base)
    combined = basket + [piece]
    conc = concentrations(combined, base)

    def fail(cat, field, value):
        return {"eligible": False, "reason_category": cat, "offending_field": field,
                "exception_value": round(float(value), 2)}

    if piece["asset_type"] not in sched["eligible_asset_types"]:
        return fail("ELIG-ASSETTYPE", "asset_type", piece["market_value"])
    if piece["asset_type"] != "CASH" and not rating_eligible(piece["rating"], sched["min_rating"]):
        return fail("ELIG-RATING", "rating", piece["market_value"])
    if piece["currency"] not in sched["eligible_currencies"]:
        return fail("ELIG-CCY", "currency", piece["market_value"])
    if piece["issuer"] in sched["wrong_way_issuers"]:
        return fail("ELIG-WRONGWAY", "issuer", piece["market_value"])
    if piece["maturity_years"] > sched["max_maturity_years"]:
        return fail("ELIG-MATURITY", "maturity_years", piece["market_value"])
    if piece["market_value"] < sched["min_denomination"]:
        return fail("ELIG-DENOM", "market_value", piece["market_value"])
    if not piece.get("docs_current", True):
        return fail("ELIG-DOCS", "docs_current", piece["market_value"])
    if abs(piece["applied_haircut_pct"] - ch) > 0.001:
        overstatement = piece["market_value"] * (ch - piece["applied_haircut_pct"]) / 100.0
        return fail("ELIG-HAIRCUT", "applied_haircut_pct", abs(overstatement))
    if conc["by_issuer"].get(piece["issuer"], 0) > lim["per_issuer"]:
        return fail("ELIG-CONC-ISSUER", "issuer", piece["market_value"])
    if conc["by_class"].get(piece["asset_type"], 0) > lim["per_asset_class"]:
        return fail("ELIG-CONC-CLASS", "asset_type", piece["market_value"])
    if conc["by_ccy"].get(piece["currency"], 0) > lim["per_currency"]:
        return fail("ELIG-CONC-CCY", "currency", piece["market_value"])
    total_php = sum(php(p["market_value"], correct_haircut(p, base)) for p in combined)
    if total_php + 0.005 < required:
        return fail("ELIG-SHORTFALL", "post_haircut_value", required - total_php)
    return {"eligible": True, "reason_category": None, "offending_field": None, "exception_value": 0.0}


def wide_basket(rng, sched):
    """6 distinct-issuer non-cash pieces (2 per class over 3 classes, currencies
    spread) + a base-currency cash cushion — comfortably within all limits."""
    base = sched["base_currency"]
    non_cash = [t for t in sched["eligible_asset_types"] if t != "CASH"]  # 3 classes
    ccys = sched["eligible_currencies"]
    used = set()
    pieces = []
    for i in range(6):
        at = non_cash[i % len(non_cash)]
        ccy = ccys[i % len(ccys)]
        iss = _issuer(at, rng)
        guard = 0
        while (iss in used or iss in sched["wrong_way_issuers"]) and guard < 50:
            iss = _issuer(at, rng); guard += 1
        used.add(iss)
        pieces.append(clean_piece(rng, sched, asset_type=at, currency=ccy, issuer=iss, mv=1_000_000))
    pieces.append({"asset_type": "CASH", "issuer": "—", "currency": base, "rating": "AAA",
                   "maturity_years": 0, "market_value": 4_000_000.0, "docs_current": True,
                   "applied_haircut_pct": 0.0})
    return pieces, used


def _valuation_summary(piece, basket, sched, required):
    base = sched["base_currency"]
    ch = correct_haircut(piece, base)
    combined = basket + [piece]
    total_php = sum(php(p["market_value"], correct_haircut(p, base)) for p in combined)
    return {
        "schedule_correct_haircut_pct": ch,
        "applied_haircut_pct": piece["applied_haircut_pct"],
        "post_haircut_value_applied": php(piece["market_value"], piece["applied_haircut_pct"]),
        "post_haircut_value_correct": php(piece["market_value"], ch),
        "required_amount": round(required, 2),
        "basket_plus_piece_post_haircut_total": round(total_php, 2),
        "shortfall_if_any": round(max(0.0, required - total_php), 2),
        "resulting_concentrations_pct": concentrations(combined, base),
    }


def render_case(idx, item):
    rng = random.Random(600_000 + idx)
    sched = build_schedule(rng)
    base = sched["base_currency"]
    kind = item["kind"]
    required = None

    if kind == "trap":
        piece, basket, required = _make_trap(item["trap"], rng, sched)
    else:
        piece, basket, required = _make_violation(item["category"], rng, sched)

    if required is None:
        total_php = sum(php(p["market_value"], correct_haircut(p, base)) for p in basket + [piece])
        required = round(0.5 * total_php, 2)

    gt = oracle(piece, basket, sched, required)
    gt["valid_substitute_asset_types"] = [t for t in sched["eligible_asset_types"]
                                          if t != piece["asset_type"]] or ["CASH"]
    return {
        "case_id": f"AAL-D-006-{idx:03d}", "dataset": "AAL-D-006",
        "csa_schedule": sched, "existing_basket": basket, "proposed_piece": piece,
        "required_amount": round(required, 2),
        "valuation_summary": _valuation_summary(piece, basket, sched, required),
        "ground_truth": gt,
        "meta": {"kind": kind, "intended": item.get("category") or item.get("trap")},
    }


def _make_violation(cat, rng, sched):
    base = sched["base_currency"]
    basket, _ = wide_basket(rng, sched)
    non_cash = [t for t in sched["eligible_asset_types"] if t != "CASH"]

    if cat == "ELIG-ASSETTYPE":
        bad = [t for t in ASSET_TYPES if t not in sched["eligible_asset_types"]]
        piece = clean_piece(rng, sched, asset_type=non_cash[0])
        piece["asset_type"] = rng.choice(bad); piece["issuer"] = _issuer(piece["asset_type"], rng)
        piece["applied_haircut_pct"] = correct_haircut(piece, base)
        return piece, basket, None
    if cat == "ELIG-RATING":
        at = "CORP" if "CORP" in non_cash else "SOV"
        piece = clean_piece(rng, sched, asset_type=at)
        piece["rating"] = rng.choice([r for r in RATINGS if not rating_eligible(r, sched["min_rating"])])
        piece["applied_haircut_pct"] = correct_haircut(piece, base)
        return piece, basket, None
    if cat == "ELIG-CCY":
        piece = clean_piece(rng, sched)
        piece["currency"] = rng.choice([c for c in CURRENCIES if c not in sched["eligible_currencies"]])
        piece["applied_haircut_pct"] = correct_haircut(piece, base)
        return piece, basket, None
    if cat == "ELIG-WRONGWAY":
        at = "CORP" if "CORP" in non_cash else "EQUITY" if "EQUITY" in non_cash else non_cash[0]
        piece = clean_piece(rng, sched, asset_type=at)
        piece["issuer"] = sched["wrong_way_issuers"][0]
        piece["applied_haircut_pct"] = correct_haircut(piece, base)
        return piece, basket, None
    if cat == "ELIG-MATURITY":
        piece = clean_piece(rng, sched, asset_type="SOV")
        piece["maturity_years"] = sched["max_maturity_years"] + rng.choice([2, 5, 10])
        piece["applied_haircut_pct"] = correct_haircut(piece, base)
        return piece, basket, None
    if cat == "ELIG-DENOM":
        piece = clean_piece(rng, sched, mv=float(rng.choice([2_000, 5_000, 8_000])))
        piece["applied_haircut_pct"] = correct_haircut(piece, base)
        return piece, basket, None
    if cat == "ELIG-DOCS":
        piece = clean_piece(rng, sched); piece["docs_current"] = False
        return piece, basket, None
    if cat == "ELIG-HAIRCUT":
        piece = clean_piece(rng, sched)
        piece["applied_haircut_pct"] = round(max(0.0, correct_haircut(piece, base) - rng.choice([3.0, 5.0, 8.0])), 2)
        return piece, basket, None
    if cat in ("ELIG-CONC-ISSUER", "ELIG-CONC-CLASS", "ELIG-CONC-CCY"):
        return _make_concentration(cat, rng, sched)
    if cat == "ELIG-SHORTFALL":
        piece = {"asset_type": "CASH", "issuer": "—", "currency": base, "rating": "AAA",
                 "maturity_years": 0, "market_value": 1_000_000.0, "docs_current": True,
                 "applied_haircut_pct": 0.0}
        return piece, [], piece["market_value"] * 5.0
    raise ValueError(cat)


def _make_concentration(cat, rng, sched):
    base = sched["base_currency"]
    non_cash = [t for t in sched["eligible_asset_types"] if t != "CASH"]
    at = non_cash[0]
    piece = clean_piece(rng, sched, asset_type=at, currency=sched["eligible_currencies"][0], mv=1_000_000)
    piece["applied_haircut_pct"] = correct_haircut(piece, base)
    small = {"asset_type": "CASH", "issuer": "—", "currency": base, "rating": "AAA",
             "maturity_years": 0, "market_value": 500_000.0, "docs_current": True, "applied_haircut_pct": 0.0}
    if cat == "ELIG-CONC-ISSUER":
        filler = clean_piece(rng, sched, asset_type=at, currency=piece["currency"], issuer=piece["issuer"], mv=4_000_000)
    elif cat == "ELIG-CONC-CLASS":
        other_iss = _issuer(at, rng)
        while other_iss == piece["issuer"] or other_iss in sched["wrong_way_issuers"]:
            other_iss = _issuer(at, rng)
        filler = clean_piece(rng, sched, asset_type=at, currency=piece["currency"], issuer=other_iss, mv=9_000_000)
    else:  # ELIG-CONC-CCY: spread across classes+issuers but same currency -> >60%
        f1 = clean_piece(rng, sched, asset_type=non_cash[1 % len(non_cash)], currency=piece["currency"], mv=6_000_000)
        f2 = clean_piece(rng, sched, asset_type=non_cash[2 % len(non_cash)], currency=piece["currency"], mv=6_000_000)
        return piece, [f1, f2, small], None
    return piece, [filler, small], None


def _make_trap(trap, rng, sched):
    base = sched["base_currency"]
    non_cash = [t for t in sched["eligible_asset_types"] if t != "CASH"]
    basket, used = wide_basket(rng, sched)
    required = None

    if trap == "TRAP-RATING-AT-FLOOR":
        at = "CORP" if "CORP" in non_cash else "SOV"
        piece = clean_piece(rng, sched, asset_type=at)
        piece["rating"] = sched["min_rating"]
    elif trap == "TRAP-CONC-JUST-UNDER":
        # issuer concentration lands just under 20%: piece 1.0M against a 4.6M same-... no, distinct issuer.
        piece = clean_piece(rng, sched, mv=1_000_000)
        # ensure its issuer is distinct from basket so it stays a small share (~1/(7) ≈ 14%)
        while piece["issuer"] in used:
            piece = clean_piece(rng, sched, mv=1_000_000)
    elif trap == "TRAP-HAIRCUT-CORRECT":
        at = "EQUITY" if "EQUITY" in non_cash else "CORP" if "CORP" in non_cash else "SOV"
        piece = clean_piece(rng, sched, asset_type=at)  # high but correct haircut
    elif trap == "TRAP-MATURITY-IN-BAND":
        piece = clean_piece(rng, sched, asset_type="SOV")
        piece["maturity_years"] = sched["max_maturity_years"]
    elif trap == "TRAP-CCY-PERMITTED":
        non_base = [c for c in sched["eligible_currencies"] if c != base] or [base]
        piece = clean_piece(rng, sched, currency=rng.choice(non_base))
    elif trap == "TRAP-COVERS-EXACTLY":
        piece = clean_piece(rng, sched)
        total = sum(php(p["market_value"], correct_haircut(p, base)) for p in basket + [piece])
        required = round(total, 2)  # covered to the dollar
    else:
        raise ValueError(trap)

    while piece["issuer"] in used:  # keep issuer concentration safe
        alt = _issuer(piece["asset_type"], rng)
        if alt in sched["wrong_way_issuers"]:
            continue
        piece["issuer"] = alt
        if piece["issuer"] not in used:
            break
    piece["applied_haircut_pct"] = correct_haircut(piece, base)
    return piece, basket, required


def build_master_plan():
    plan = []
    per = 162 // len(CATEGORIES); rem = 162 - per * len(CATEGORIES)
    for i, c in enumerate(CATEGORIES):
        plan += [{"kind": "ineligible", "category": c}] * (per + (1 if i < rem else 0))
    per_t = 88 // len(TRAP_TYPES); rem_t = 88 - per_t * len(TRAP_TYPES)
    for i, t in enumerate(TRAP_TYPES):
        plan += [{"kind": "trap", "trap": t}] * (per_t + (1 if i < rem_t else 0))
    assert len(plan) == 250, len(plan)
    random.Random(6006).shuffle(plan)
    return plan


def qa_assert_case(case):
    gt, kind, intended, cid = case["ground_truth"], case["meta"]["kind"], case["meta"]["intended"], case["case_id"]
    if kind == "trap":
        assert gt["eligible"] is True, f"{cid}: trap {intended} scored INELIGIBLE ({gt['reason_category']})"
    else:
        assert gt["eligible"] is False, f"{cid}: ineligible {intended} scored eligible"
        assert gt["reason_category"] == intended, f"{cid}: intended {intended}, oracle says {gt['reason_category']}"
        assert gt["exception_value"] >= 0, f"{cid}: negative exception value"
    for k in ("schedule_correct_haircut_pct", "post_haircut_value_correct", "required_amount",
              "shortfall_if_any", "resulting_concentrations_pct"):
        assert k in case["valuation_summary"], f"{cid}: valuation_summary missing {k}"


if __name__ == "__main__":
    plan = build_master_plan()
    cases = [render_case(i + 1, plan[i]) for i in range(len(plan))]
    for c in cases:
        qa_assert_case(c)
    n_inelig = sum(1 for c in cases if not c["ground_truth"]["eligible"])
    n_trap = sum(1 for c in cases if c["meta"]["kind"] == "trap")
    cats = Counter(c["ground_truth"]["reason_category"] for c in cases if not c["ground_truth"]["eligible"])
    print(f"D-006 generated {len(cases)} cases: {n_inelig} ineligible, {n_trap} traps. QA PASSED.")
    print("Category distribution:", dict(cats))
