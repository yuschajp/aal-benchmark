"""
AAL-D-006 independent recompute check.

A SEPARATELY-WRITTEN second implementation of the collateral-eligibility rules.
It re-derives haircut, concentration, the eligibility verdict, and the exception
magnitude for every case straight from the case's own schedule/basket/piece, and
compares against the stored ground_truth. It deliberately does NOT import
d006_common — the whole point is a second path that would disagree if the
generator had a bug.

Usage:  python quality_checks/recompute_check_d006.py [path-to-json]
Default path: datasets/AAL-D-006-v1.0.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# --- independent reference data (mirrors the published rules, retyped) ---------
RATINGS = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
           "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-"]
ORD = {r: i for i, r in enumerate(RATINGS)}
BASE_HC = {"CASH": 0.0, "MMF": 1.0, "SOV": 1.0, "CORP": 4.0, "EQUITY": 15.0}
FX_HC = 8.0


def hc(piece, base_ccy):
    h = BASE_HC[piece["asset_type"]]
    if piece["asset_type"] in ("SOV", "CORP"):
        h += 0.5 * max(0, ORD[piece["rating"]] - ORD["AA"])
        h += min(6.0, 0.25 * max(0, piece["maturity_years"] - 5))
    if piece["asset_type"] == "EQUITY" and ORD[piece["rating"]] > ORD["A-"]:
        h += 5.0
    if piece["asset_type"] != "CASH" and piece["currency"] != base_ccy:
        h += FX_HC
    return round(h, 2)


def php(mv, h):
    return round(mv * (1.0 - h / 100.0), 2)


def conc(pieces, base):
    scored = [p for p in pieces if not (p["asset_type"] == "CASH" and p["currency"] == base)]
    tot = sum(p["market_value"] for p in scored) or 1.0
    bi, bc, by = {}, {}, {}
    for p in scored:
        bi[p["issuer"]] = bi.get(p["issuer"], 0.0) + p["market_value"]
        bc[p["asset_type"]] = bc.get(p["asset_type"], 0.0) + p["market_value"]
        by[p["currency"]] = by.get(p["currency"], 0.0) + p["market_value"]
    f = lambda d: {k: 100.0 * v / tot for k, v in d.items()}
    return f(bi), f(bc), f(by)


def verdict(case):
    s = case["csa_schedule"]
    base = s["base_currency"]
    lim = s["concentration_limits_pct"]
    p = case["proposed_piece"]
    combined = case["existing_basket"] + [p]
    bi, bc, by = conc(combined, base)
    req = case["required_amount"]

    def R(cat, val):
        return cat, round(float(val), 2)

    if p["asset_type"] not in s["eligible_asset_types"]:
        return R("ELIG-ASSETTYPE", p["market_value"])
    if p["asset_type"] != "CASH" and ORD[p["rating"]] > ORD[s["min_rating"]]:
        return R("ELIG-RATING", p["market_value"])
    if p["currency"] not in s["eligible_currencies"]:
        return R("ELIG-CCY", p["market_value"])
    if p["issuer"] in s["wrong_way_issuers"]:
        return R("ELIG-WRONGWAY", p["market_value"])
    if p["maturity_years"] > s["max_maturity_years"]:
        return R("ELIG-MATURITY", p["market_value"])
    if p["market_value"] < s["min_denomination"]:
        return R("ELIG-DENOM", p["market_value"])
    if not p.get("docs_current", True):
        return R("ELIG-DOCS", p["market_value"])
    ch = hc(p, base)
    if abs(p["applied_haircut_pct"] - ch) > 0.001:
        return R("ELIG-HAIRCUT", abs(p["market_value"] * (ch - p["applied_haircut_pct"]) / 100.0))
    if bi.get(p["issuer"], 0) > lim["per_issuer"]:
        return R("ELIG-CONC-ISSUER", p["market_value"])
    if bc.get(p["asset_type"], 0) > lim["per_asset_class"]:
        return R("ELIG-CONC-CLASS", p["market_value"])
    if by.get(p["currency"], 0) > lim["per_currency"]:
        return R("ELIG-CONC-CCY", p["market_value"])
    total_php = sum(php(q["market_value"], hc(q, base)) for q in combined)
    if total_php + 0.005 < req:
        return R("ELIG-SHORTFALL", req - total_php)
    return None, 0.0


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("datasets/AAL-D-006-v1.0.json")
    data = json.loads(path.read_text())
    cases = data["cases"]
    mismatches = []
    for c in cases:
        cat, val = verdict(c)
        gt = c["ground_truth"]
        exp_elig = cat is None
        if exp_elig != gt["eligible"]:
            mismatches.append((c["case_id"], f"eligible: recompute={exp_elig} vs stored={gt['eligible']}"))
            continue
        if not exp_elig:
            if cat != gt["reason_category"]:
                mismatches.append((c["case_id"], f"category: recompute={cat} vs stored={gt['reason_category']}"))
            elif abs(val - gt["exception_value"]) > 0.01:
                mismatches.append((c["case_id"], f"value: recompute={val} vs stored={gt['exception_value']}"))

    print(f"AAL-D-006 recompute check: {len(cases)} cases, {len(mismatches)} mismatch(es).")
    if mismatches:
        for cid, msg in mismatches[:25]:
            print(f"  FAIL {cid}: {msg}")
        sys.exit(1)
    print("PASS: every eligibility verdict, category, and exception value reconciles independently.")


if __name__ == "__main__":
    main()
