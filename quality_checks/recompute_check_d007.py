"""
AAL-D-007 independent recompute check.

A SEPARATELY-WRITTEN second implementation of the SIMM-style margin
aggregation. It re-derives each side's bucket margins, risk-class margins,
total IM, and the dispute verdict straight from the case's own printed
sensitivities / risk weights / correlations / methodology metadata, and
compares against the stored margin_breakdown and ground_truth. It
deliberately does NOT import d007_common -- the whole point is a second path
that would disagree if the generator had a bug.

Usage:  python quality_checks/recompute_check_d007.py [path-to-json]
Default path: datasets/AAL-D-007-v1.1.json

v1.1: which physical side (firm/counterparty) is "correct" now varies
case-by-case (see d007_common.py's module docstring), so this checker also
independently re-derives, from case["meta"]["perturbed_side"], that
correct_im_amount actually lines up with the non-perturbed side's total --
the exact property v1.0's design flaw would have violated undetected.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

TOLERANCE_PCT = 10.0
CONC_THRESHOLD = 2_000_000.0
CONC_ADDON_PCT = 0.15
INTRA_BUCKET_CORR = 0.98
CROSS_BUCKET_CORR = {"RATES_FX": 0.50, "CREDIT": 0.40, "EQUITY": 0.15}


def bkt_margin(values, rw):
    ws = [rw * v for v in values]
    sumsq = sum(w * w for w in ws)
    cross = 0.0
    n = len(ws)
    for i in range(n):
        for j in range(n):
            if i != j:
                cross += INTRA_BUCKET_CORR * ws[i] * ws[j]
    return math.sqrt(max(sumsq + cross, 0.0))


def cls_margin(bkt_vals, corr):
    vals = list(bkt_vals)
    sumsq = sum(v * v for v in vals)
    cross = 0.0
    n = len(vals)
    for i in range(n):
        for j in range(n):
            if i != j:
                cross += corr * vals[i] * vals[j]
    return math.sqrt(max(sumsq + cross, 0.0))


def side_total(sens, rw_table, apply_addon):
    total = 0.0
    for cls, buckets in sens.items():
        bms = []
        for bucket, factors in buckets.items():
            vals = [f["sensitivity"] for f in factors]
            if not vals:
                continue
            rw = rw_table[cls][bucket]
            m = bkt_margin(vals, rw)
            bms.append(m)
            if apply_addon and m > CONC_THRESHOLD:
                total += CONC_ADDON_PCT * m
        if bms:
            total += cls_margin(bms, CROSS_BUCKET_CORR[cls])
    return total


def rw_for(case, side):
    """Re-derive which risk-weight table a side actually used. The exact
    multiplier lives in case["meta"]["rw_scale"] and always applies to
    whichever physical side (firm or counterparty) carried the injected
    error that case -- case["meta"]["perturbed_side"], never a hardcoded
    side (v1.0's bug was effectively hardcoding this to "counterparty").
    Both fields are internal bookkeeping never shown to the evaluated model
    (see build_prompt), analogous to how every other AAL dataset's recompute
    check re-derives ground truth against fields the generator populated,
    not against the model-facing prose."""
    base = case["risk_weights"]
    if side != case["meta"]["perturbed_side"]:
        return base
    scale = case["meta"]["rw_scale"]
    if scale == 1.0:
        return base
    return {c: {b: v * scale for b, v in bs.items()} for c, bs in base.items()}


def verdict(case):
    firm_addon_flag = case["concentration_addon_metadata"]["firm"]["addon_applied"]
    cpty_addon_flag = case["concentration_addon_metadata"]["counterparty"]["addon_applied"]

    firm_rw = rw_for(case, "firm")
    cpty_rw = rw_for(case, "counterparty")

    firm_total = side_total(case["firm_sensitivities"], firm_rw, firm_addon_flag)
    cpty_total = side_total(case["counterparty_sensitivities"], cpty_rw, cpty_addon_flag)

    diff = abs(firm_total - cpty_total)
    # v1.1: the pct base must be the CORRECT (non-perturbed) side's total, not
    # always "firm" -- generator's oracle() uses base_total the same way, and
    # perturbed_side now varies case-by-case (see rw_for's docstring).
    perturbed_side = case["meta"]["perturbed_side"]
    base_total = cpty_total if perturbed_side == "firm" else firm_total
    diff_pct = 100.0 * diff / base_total if base_total else 0.0
    disputed = diff_pct > TOLERANCE_PCT
    return firm_total, cpty_total, diff, diff_pct, disputed


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("datasets/AAL-D-007-v1.1.json")
    data = json.loads(path.read_text())
    cases = data["cases"]
    mismatches = []
    for c in cases:
        firm_total, cpty_total, diff, diff_pct, disputed = verdict(c)
        gt = c["ground_truth"]
        mb = c["margin_breakdown"]

        if abs(firm_total - mb["firm"]["total_im"]) > 1.0:
            mismatches.append((c["case_id"], f"firm total: recompute={firm_total:.2f} vs stored={mb['firm']['total_im']}"))
            continue
        if abs(cpty_total - mb["counterparty"]["total_im"]) > 1.0:
            mismatches.append((c["case_id"], f"cpty total: recompute={cpty_total:.2f} vs stored={mb['counterparty']['total_im']}"))
            continue
        if disputed != gt["dispute_exists"]:
            mismatches.append((c["case_id"], f"dispute_exists: recompute={disputed} vs stored={gt['dispute_exists']}"))
            continue
        if abs(diff - gt["dispute_difference"]) > 1.0:
            mismatches.append((c["case_id"], f"difference: recompute={diff:.2f} vs stored={gt['dispute_difference']}"))
            continue

        # v1.1: independently confirm correct_im_amount lines up with the
        # non-perturbed side's recomputed total -- not hardcoded to "firm".
        perturbed_side = c["meta"]["perturbed_side"]
        correct_side_total = cpty_total if perturbed_side == "firm" else firm_total
        if abs(correct_side_total - gt["correct_im_amount"]) > 1.0:
            mismatches.append((c["case_id"],
                f"correct_im_amount: recompute(non-perturbed side)={correct_side_total:.2f} "
                f"vs stored={gt['correct_im_amount']} (perturbed_side={perturbed_side})"))
            continue

    print(f"AAL-D-007 recompute check: {len(cases)} cases, {len(mismatches)} mismatch(es).")
    if mismatches:
        for cid, msg in mismatches[:25]:
            print(f"  FAIL {cid}: {msg}")
        sys.exit(1)
    n_cpty_perturbed = sum(1 for c in cases if c["meta"]["perturbed_side"] == "counterparty")
    n_firm_perturbed = len(cases) - n_cpty_perturbed
    print(f"  perturbed side balance: firm={n_firm_perturbed} counterparty={n_cpty_perturbed}")
    print("PASS: every side's total IM, the dispute verdict, the difference, and which side is "
          "correct all reconcile independently.")


if __name__ == "__main__":
    main()
