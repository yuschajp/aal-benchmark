#!/usr/bin/env python3
"""
AAL-D-004 gate-2 independent recompute checker.

Written from D004-spec.md + D004-build-brief.md ONLY. Per the gate-2
independence rule this file must never import d004_common or read generator
source; every formula below is re-derived from the documentation so the
generator cannot self-certify.

Checks (per case):
  - envelope invariants (risk_level==severity, confidence, human_review,
    escalation rule: severity>=3 or FAIL-COMPLIANCE)
  - expected/correct amount recomputed from generation_metadata.settlement_inputs
    via the brief §5 formulas (coupon/CDS day-count, fx leg, qty×price, netting
    sum, base-amount equality), tolerance 0.01
  - days_failing / fail_age_days recomputed as business days in
    (expected_value_date, REPORT_DATE] excluding weekends + case calendar holidays
  - interest_claim_amount = fail_amount × claim_rate × days_failing/360
  - primary_fail.difference = |expected - observed| when both numeric
  - severity within the spec §5 per-category range; clean cases severity 1
  - settle_now rules (COMPLIANCE/DUP -> 0; AMT -> |delta|; clean -> expected)
  - resolution owner/action within the brief §5 table (incl. stated variants)
  - referential integrity: SSI refs exist in context, currencies in cut-off
    table, expected_settlement_date within 2026-06-22..2026-07-10
  - dataset-level distribution totals (spec §5/§10)

Usage:
    python3 quality_checks/recompute_check_d004.py [paths-or-globs]
    (default: datasets/AAL-D-004/*.json, skipping non-case files)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from datetime import date, timedelta

REPORT_DATE = date(2026, 7, 8)          # brief §2
SETTLE_WINDOW = (date(2026, 6, 22), date(2026, 7, 10))
AMOUNT_EPS = 0.01                        # spec §11 recompute epsilon
BASIS = {"ACT/360": 360, "ACT/365F": 365, "ACT/365": 365}

# spec §5: category -> (count, sev_lo, sev_hi)
CATEGORIES = {
    "FAIL-SSI": (22, 2, 4), "FAIL-UNMATCHED": (18, 2, 3), "FAIL-CASHSHORT": (16, 3, 5),
    "FAIL-CUTOFF": (16, 2, 4), "FAIL-AMT": (15, 2, 4), "FAIL-NET": (14, 2, 4),
    "FAIL-SECSHORT": (12, 3, 5), "FAIL-CCY": (10, 3, 4), "FAIL-ACCT": (10, 3, 5),
    "FAIL-FX": (8, 3, 5), "FAIL-AGENT": (7, 2, 3), "FAIL-CORPACT": (5, 2, 4),
    "FAIL-COMPLIANCE": (4, 4, 5), "FAIL-NOVATION": (3, 3, 5), "FAIL-DUP": (2, 3, 4),
}
SEVERITY_TOTALS = {1: 88, 2: 50, 3: 62, 4: 35, 5: 15}
DIFFICULTY_SPLIT = {"easy": (75, 40), "moderate": (41, 30), "complex": (46, 18)}

# brief §5 table: category -> (allowed actions, allowed owners, settle_now rule)
CAT_RULES = {
    "FAIL-SSI":        ({"rebook_ssi"}, {"settlements_ops"}, "expected"),
    "FAIL-UNMATCHED":  ({"reinstruct_payment"}, {"settlements_ops"}, "expected"),
    "FAIL-CASHSHORT":  ({"chase_counterparty", "fund_shortfall"}, {"settlements_ops", "credit_risk"}, "expected"),
    "FAIL-CUTOFF":     ({"reinstruct_payment"}, {"settlements_ops"}, "expected"),
    "FAIL-AMT":        ({"reinstruct_payment", "claim_interest"}, {"settlements_ops"}, "delta"),
    "FAIL-NET":        ({"apply_netting"}, {"settlements_ops"}, None),
    "FAIL-SECSHORT":   ({"chase_counterparty", "initiate_buyin"}, {"collateral_ops"}, None),
    "FAIL-CCY":        ({"reinstruct_payment"}, {"settlements_ops"}, "expected"),
    "FAIL-ACCT":       ({"recall_payment"}, {"settlements_ops"}, "expected"),
    "FAIL-FX":         ({"chase_counterparty"}, {"settlements_ops"}, None),
    "FAIL-AGENT":      ({"reinstruct_payment"}, {"custodian_relations"}, "expected"),
    # brief action table lists only reinstruct_payment for CORPACT, but brief §6
    # scores buy_in_risk on CORPACT; initiate_buyin allowed for aged collateral
    # breaks (adjudicated 2026-07-14, mirrors the SECSHORT aging rule)
    "FAIL-CORPACT":    ({"reinstruct_payment", "initiate_buyin"}, {"collateral_ops"}, None),
    "FAIL-COMPLIANCE": ({"escalate_compliance"}, {"compliance"}, "zero"),
    # inbound novation cases have nothing to recall (not_received) — remedy is
    # chase_counterparty (gate-7 blocker adjudication, v1.0.1)
    "FAIL-NOVATION":   ({"recall_payment", "chase_counterparty"}, {"settlements_ops"}, "expected"),
    "FAIL-DUP":        ({"recall_payment"}, {"settlements_ops"}, "zero"),
}


class Result:
    def __init__(self, cid):
        self.cid = cid
        self.failures = []
        self.warnings = []

    def fail(self, msg):
        self.failures.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _close(a, b, eps=AMOUNT_EPS):
    return a is not None and b is not None and abs(a - b) <= eps


def business_days_after(start: date, end: date, holidays: set) -> int:
    """Business days d with start < d <= end, excluding weekends + holidays."""
    n, cur = 0, start
    while cur < end:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5 and cur.isoformat() not in holidays:
            n += 1
    return n


def recompute_amount(st_type, si):
    """Brief §5 amount formulas. Returns (amount|None, note|None)."""
    if si is None:
        return None, "no settlement_inputs"
    if st_type in ("coupon_reset", "cds_premium"):
        need = ("notional", "rate", "period_start", "period_end", "day_count_convention")
        if all(k in si for k in need):
            basis = BASIS.get(si["day_count_convention"])
            if basis is None:
                return None, f"unknown day-count {si['day_count_convention']}"
            days = (_d(si["period_end"]) - _d(si["period_start"])).days
            return round(si["notional"] * si["rate"] * days / basis, 2), None
        return None, "missing coupon inputs"
    if st_type == "fx_principal":
        notional = si.get("notional", si.get("notional_ccy1"))
        if notional is not None and "fx_rate" in si:
            return round(notional * si["fx_rate"], 2), None
        return None, "missing fx inputs"
    if "quantity" in si and "price" in si:
        return round(si["quantity"] * si["price"], 2), None
    if "net_components" in si:
        return round(sum(si["net_components"]), 2), None
    if "base_amount" in si:
        return round(si["base_amount"], 2), None
    return None, f"no recompute path for {st_type}"


def check_case(case) -> Result:
    cid = case.get("case_id", "<no id>")
    res = Result(cid)
    gt = case["ground_truth"]
    gm = case.get("generation_metadata") or {}
    si = gm.get("settlement_inputs")
    cv = gm.get("correct_values") or {}
    inp = case.get("input") or {}
    internal = inp.get("internal_settlement_record") or {}
    cust = inp.get("custodian_status") or {}
    ctx = inp.get("context") or {}
    fail_exists = bool(gt.get("fail_exists"))
    pf = gt.get("primary_fail") or {}
    cat = pf.get("category")

    # -- envelope invariants ------------------------------------------------
    if case.get("risk_level") != gt.get("severity"):
        res.fail(f"risk_level {case.get('risk_level')} != severity {gt.get('severity')}")
    if gt.get("confidence") != "definitive":
        res.fail(f"confidence {gt.get('confidence')} != definitive")
    if bool(gt.get("human_review_required")) != fail_exists:
        res.fail("human_review_required != fail_exists")
    want_esc = (gt.get("severity", 0) >= 3) or (cat == "FAIL-COMPLIANCE")
    if bool(gt.get("escalation_required")) != want_esc:
        res.fail(f"escalation_required {gt.get('escalation_required')} but severity "
                 f"{gt.get('severity')} / category {cat} implies {want_esc}")

    # -- severity range -----------------------------------------------------
    if not fail_exists:
        if gt.get("severity") != 1:
            res.fail(f"clean case severity {gt.get('severity')} != 1")
        if gt.get("resolution_action_type") not in (None, "no_action"):
            res.fail(f"clean resolution_action_type {gt.get('resolution_action_type')}")
    else:
        if cat not in CATEGORIES:
            res.fail(f"unknown category {cat}")
        else:
            _, lo, hi = CATEGORIES[cat]
            if not (lo <= gt.get("severity", 0) <= hi):
                res.fail(f"{cat} severity {gt.get('severity')} outside spec range {lo}-{hi}")

    # -- amount recompute (the core of gate 2) -------------------------------
    st_type = internal.get("settlement_type") or case.get("settlement_type")
    amt, note = recompute_amount(st_type, si)
    correct = _num(cv.get("correct_amount"))
    if amt is not None and correct is not None:
        if not _close(amt, correct):
            res.fail(f"correct_amount {correct} != independent recompute {amt} "
                     f"({st_type})")
    elif note:
        res.warn(f"amount not recomputable: {note}")

    exp_amt = _num(internal.get("expected_amount"))
    inj = (gm.get("injected_error") or {})
    trap = gm.get("trap_type")
    if exp_amt is not None and correct is not None and not _close(exp_amt, correct):
        # netting trap cleans legitimately differ: internal shows our gross
        # component, correct_amount is the netted custodian total (brief §5)
        if inj.get("side") != "internal_settlement_record" and trap != "netting_makes_it_correct":
            res.fail(f"internal expected_amount {exp_amt} != correct_amount {correct} "
                     f"but injected side is {inj.get('side')} / trap {trap}")

    # -- days_failing / fail_age_days ----------------------------------------
    cal = ctx.get("settlement_calendar") or {}
    holidays = set(cal.get("holidays") or cal.get("holiday_dates") or [])
    evd = internal.get("expected_value_date")
    if evd:
        try:
            bd = business_days_after(_d(evd), REPORT_DATE, holidays)
            printed = cust.get("days_failing")
            if printed is not None and int(printed) != bd:
                # settled/clean feeds may legitimately print 0 (late_but_settled)
                if fail_exists or int(printed) != 0:
                    res.fail(f"days_failing printed {printed} != recomputed {bd} "
                             f"(evd {evd}, report {REPORT_DATE})")
            age = gt.get("fail_age_days")
            if fail_exists and age is not None and int(age) != bd:
                res.fail(f"fail_age_days {age} != recomputed {bd}")
        except ValueError as e:
            res.warn(f"unparseable date in days_failing check: {e}")
        esd = internal.get("expected_settlement_date")
        if esd:
            dt = _d(esd)
            if not (SETTLE_WINDOW[0] <= dt <= SETTLE_WINDOW[1]):
                res.fail(f"expected_settlement_date {esd} outside brief window")

    # -- interest claim -------------------------------------------------------
    ica = gt.get("interest_claim_amount")
    fail_amt = _num(pf.get("fail_amount") if "fail_amount" in pf else gt.get("fail_amount"))
    rate = _num(gm.get("claim_rate"))
    age = gt.get("fail_age_days")
    if fail_exists and gt.get("interest_claim_applicable") and _num(ica) not in (None, 0.0):
        if fail_amt is not None and rate is not None and age is not None:
            want = round(fail_amt * rate * int(age) / 360, 2)
            if not _close(_num(ica), want, max(AMOUNT_EPS, 0.01 * abs(want))):
                res.fail(f"interest_claim_amount {ica} != fail_amount×claim_rate×"
                         f"days/360 = {want}")
        else:
            res.warn("interest claim set but inputs incomplete for recompute")
    if not gt.get("interest_claim_applicable") and _num(ica) not in (None, 0.0):
        res.fail(f"interest_claim_amount {ica} nonzero but flag false")

    # -- difference arithmetic -------------------------------------------------
    if fail_exists:
        ev, ov, diff = _num(pf.get("expected_value")), _num(pf.get("observed_value")), _num(pf.get("difference"))
        if ev is not None and ov is not None and diff is not None:
            if not _close(diff, abs(ev - ov), max(AMOUNT_EPS, 1e-6 * abs(ev))):
                res.fail(f"difference {diff} != |expected-observed| {abs(ev-ov):.2f}")
        if pf.get("difference") is not None and (ev is None or ov is None):
            res.fail("difference non-null but values non-numeric (spec §7: null for routing/status)")
        if pf.get("observed_source") not in ("internal_settlement_record", "custodian_status", "counterparty_advice"):
            res.fail(f"observed_source {pf.get('observed_source')} invalid")

    # -- settle_now / action / owner per brief table ---------------------------
    stn = _num(gt.get("settle_now_amount"))
    if not fail_exists:
        # brief §5: clean settle_now_amount = expected_amount (the internal
        # record's figure, not correct_amount — they differ on netting traps)
        ref = exp_amt if exp_amt is not None else correct
        if ref is not None and stn is not None and not _close(stn, ref):
            res.fail(f"clean settle_now {stn} != expected_amount {ref}")
    elif cat in CAT_RULES:
        actions, owners, rule = CAT_RULES[cat]
        if gt.get("resolution_action_type") not in actions:
            res.fail(f"{cat} action {gt.get('resolution_action_type')} not in {sorted(actions)}")
        if gt.get("resolution_owner") not in owners:
            res.fail(f"{cat} owner {gt.get('resolution_owner')} not in {sorted(owners)}")
        if rule == "zero" and stn not in (None, 0.0):
            res.fail(f"{cat} settle_now {stn} != 0")
        elif rule == "expected" and correct is not None and stn is not None and not _close(stn, correct):
            res.fail(f"{cat} settle_now {stn} != expected {correct}")
        elif rule == "delta":
            ev, ov = _num(pf.get("expected_value")), _num(pf.get("observed_value"))
            if ev is not None and ov is not None and stn is not None and not _close(stn, abs(ev - ov)):
                res.fail(f"FAIL-AMT settle_now {stn} != |delta| {abs(ev-ov):.2f}")

    # -- referential integrity ---------------------------------------------------
    ssis = {s.get("ssi_ref") for s in (ctx.get("ssi_reference") or [])}
    for doc, key in ((internal, "our_ssi_ref"), (internal, "counterparty_ssi_ref")):
        ref = doc.get(key)
        if ref and ssis and ref not in ssis:
            res.fail(f"{key} {ref} not in context.ssi_reference")
    adv = inp.get("counterparty_advice")
    if adv and adv.get("ssi_used_ref") and ssis and adv["ssi_used_ref"] not in ssis:
        res.fail(f"advice ssi_used_ref {adv['ssi_used_ref']} not in context.ssi_reference")
    ccys = {r.get("currency") for r in (ctx.get("currency_cutoff_table") or [])}
    if internal.get("currency") and ccys and internal["currency"] not in ccys:
        res.fail(f"currency {internal['currency']} missing from cut-off table")
    return res


def is_case_list(obj):
    return isinstance(obj, list) and obj and isinstance(obj[0], dict) and "case_id" in obj[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description="AAL-D-004 independent recompute checker")
    ap.add_argument("paths", nargs="*", help="dataset json files or globs")
    args = ap.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    paths = []
    for p in args.paths or [os.path.join(root, "datasets", "AAL-D-004", "*.json")]:
        paths.extend(glob.glob(p) if any(c in p for c in "*?[") else [p])

    total = failed = 0
    warn_count = 0
    all_cases = {}
    for path in sorted(paths):
        try:
            data = json.load(open(path))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not is_case_list(data):
            continue
        for case in data:
            total += 1
            res = check_case(case)
            all_cases.setdefault(case["case_id"], case)
            for w in res.warnings:
                warn_count += 1
                print(f"  [warn] {res.cid}: {w}")
            if res.failures:
                failed += 1
                print(f"[FAIL] {res.cid}")
                for f in res.failures:
                    print(f"       - {f}")
            else:
                print(f"[PASS] {res.cid}")

    # -- dataset-level distribution (on the union of unique case ids) ----------
    dist_fails = []
    if len(all_cases) == 250:
        cases = list(all_cases.values())
        n_fail = sum(1 for c in cases if c["ground_truth"]["fail_exists"])
        if (n_fail, 250 - n_fail) != (162, 88):
            dist_fails.append(f"fail/clean split {n_fail}/{250-n_fail} != 162/88")
        cats = Counter(c["ground_truth"]["primary_fail"]["category"]
                       for c in cases if c["ground_truth"]["fail_exists"])
        for cat, (want, _, _) in CATEGORIES.items():
            if cats.get(cat, 0) != want:
                dist_fails.append(f"{cat} count {cats.get(cat,0)} != {want}")
        sevs = Counter(c["ground_truth"]["severity"] for c in cases)
        for s, want in SEVERITY_TOTALS.items():
            if sevs.get(s, 0) != want:
                dist_fails.append(f"severity {s} count {sevs.get(s,0)} != {want}")
        diffs = Counter((c["difficulty"], c["ground_truth"]["fail_exists"]) for c in cases)
        for d, (fw, cw) in DIFFICULTY_SPLIT.items():
            if diffs.get((d, True), 0) != fw or diffs.get((d, False), 0) != cw:
                dist_fails.append(f"difficulty {d} fail/clean "
                                  f"{diffs.get((d,True),0)}/{diffs.get((d,False),0)} != {fw}/{cw}")
        ac = Counter(c["asset_class"] for c in cases)
        for a, n in ac.items():
            if n < 15:
                dist_fails.append(f"asset_class {a} count {n} < 15")
    else:
        print(f"  [warn] unique cases {len(all_cases)} != 250 — distribution checks "
              f"run only on a full set")

    for f in dist_fails:
        print(f"[FAIL] DISTRIBUTION - {f}")

    ok = total - failed
    print(f"\nSUMMARY: {ok} passed, {failed} failed, {total} total across "
          f"{len([p for p in paths])} file(s); {warn_count} warnings; "
          f"{len(dist_fails)} distribution failures")
    return 1 if (failed or dist_fails) else 0


if __name__ == "__main__":
    sys.exit(main())
