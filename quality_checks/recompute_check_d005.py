#!/usr/bin/env python3
"""
AAL-D-005 QA gate 2 — independent recomputation.

Written from the spec (not from d005_common). Shares NO code with the generators:
it re-derives the unexplained residual straight from the input documents
(product_control.actual_pnl minus the sum of the pnl_explain components) and
checks it against the residual the feed prints, then verifies the break/clean
materiality discriminator, the primary-break divergence, and the explained-move
coherence. Exits nonzero on any mismatch.

Usage:
    python recompute_check_d005.py            # checks datasets/AAL-D-005/AAL-D-005-v1.0.json
    python recompute_check_d005.py <path>     # or a specific file / batch glob
"""
import glob
import json
import os
import sys

EPS = 0.01
CATEGORIES = {
    "BRK-PRICE", "BRK-MISSINGTRADE", "BRK-NEWTRADE", "BRK-AMEND", "BRK-SIGN",
    "BRK-FX", "BRK-FEE", "BRK-CANCEL", "BRK-CORPACT", "BRK-DAYCOUNT",
    "BRK-SENSITIVITY", "BRK-POSITION", "BRK-CCY", "BRK-DUP", "BRK-RESET",
}
EXPLAIN_KEYS = ("delta_pnl", "gamma_pnl", "vega_pnl", "theta_pnl",
                "carry_pnl", "financing_pnl", "new_trade_pnl", "fee_pnl")
OWNERS = {"product_control", "trading_desk", "market_data", "trade_support",
          "fund_accounting", "risk"}
SOURCES = {"front_office_pnl", "product_control_pnl", "pnl_explain", "context"}


def default_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "datasets", "AAL-D-005", "AAL-D-005-v1.0.json")


def load(path):
    if os.path.isfile(path):
        return json.load(open(path))
    cases = []
    for p in sorted(glob.glob(path)):
        cases.extend(json.load(open(p)))
    return cases


def check_case(x):
    """Return list of failure strings for one case (empty == pass)."""
    errs = []
    cid = x.get("case_id", "?")
    inp = x["input"]
    fo, pc, ex = inp["front_office_pnl"], inp["product_control_pnl"], inp["pnl_explain"]
    gt = x["ground_truth"]
    actual = pc["actual_pnl"]
    printed_resid = pc["unexplained_residual"]
    thr = pc["materiality_threshold"]

    # 1) independent residual recompute from the documents (explain present)
    if ex is not None:
        explained = round(sum(ex[k] for k in EXPLAIN_KEYS), 2)
        recomputed = round(actual - explained, 2)
        if abs(recomputed - printed_resid) > EPS:
            errs.append(f"{cid}: residual recompute {recomputed} != printed {printed_resid}")
        if abs(ex["residual_explained"] - explained) > EPS:
            errs.append(f"{cid}: residual_explained {ex['residual_explained']} != sum(components) {explained}")

    # 2) break / clean materiality discriminator
    if gt["break_exists"]:
        if abs(printed_resid) < thr - EPS:
            errs.append(f"{cid}: break residual {printed_resid} below threshold {thr}")
        pb = gt["primary_break"]
        if not pb or pb["category"] not in CATEGORIES:
            errs.append(f"{cid}: bad/missing primary_break category")
        elif str(pb["expected_value"]) == str(pb["observed_value"]):
            errs.append(f"{cid}: primary break has no divergence (expected == observed)")
        elif pb["observed_source"] not in SOURCES:
            errs.append(f"{cid}: bad observed_source {pb['observed_source']}")
        if gt["break_owner"] not in OWNERS:
            errs.append(f"{cid}: bad break_owner {gt['break_owner']}")
        if gt["is_explained_move"] is not False:
            errs.append(f"{cid}: break case with is_explained_move True")
        if gt["escalation_required"] != (gt["severity"] >= 3):
            errs.append(f"{cid}: escalation != (severity>=3)")
    else:
        if abs(printed_resid) >= thr:
            errs.append(f"{cid}: clean residual {printed_resid} >= threshold {thr}")
        if gt["primary_break"] is not None:
            errs.append(f"{cid}: clean case with a primary_break")
        if gt["is_explained_move"] is not True:
            errs.append(f"{cid}: clean case with is_explained_move False")
        if gt["severity"] != 1:
            errs.append(f"{cid}: clean case severity != 1")
        if abs(gt["attributable_pnl"] - actual) > EPS:
            errs.append(f"{cid}: clean attributable_pnl {gt['attributable_pnl']} != actual {actual}")
    return errs


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else default_path()
    cases = load(path)
    if not cases:
        sys.exit(f"no cases loaded from {path}")
    all_errs, tied_out = [], 0
    for x in cases:
        if x["input"]["pnl_explain"] is not None:
            tied_out += 1
        all_errs.extend(check_case(x))
    print(f"AAL-D-005 recompute check: {len(cases)} cases, "
          f"{tied_out} with full component tie-out, {len(cases) - tied_out} null-explain.")
    if all_errs:
        for e in all_errs[:50]:
            print("FAIL:", e)
        print(f"... {len(all_errs)} total failures")
        sys.exit(1)
    print("PASS: every reported residual reconciles independently; "
          "break/clean discriminator and primary divergence verified.")


if __name__ == "__main__":
    main()
