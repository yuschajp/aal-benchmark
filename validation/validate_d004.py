#!/usr/bin/env python3
"""
AI Alpha Labs — AAL-D-004 Benchmark Validator (QA gate 3)
Modeled on validate.py; D-004 enums and required fields per D004-spec.md
and D004-build-brief.md §3.

Usage:
    python3 validate_d004.py --file ../datasets/AAL-D-004/AAL-D-004-v1.0.json
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

VALID_ASSET_CLASSES = ["interest_rate_swap", "credit_default_swap", "fx_forward",
                       "fx_option", "equity_swap", "commodity_swap", "cross_currency_swap"]
VALID_DIFFICULTIES = ["easy", "moderate", "complex"]
VALID_WORKFLOWS = ["settlement_fail_exception"]
VALID_CONFIDENCES = ["definitive"]
VALID_SETTLEMENT_TYPES = ["coupon_reset", "variation_margin", "initial_margin", "upfront_fee",
                          "termination_payment", "cds_premium", "credit_event", "fx_principal",
                          "physical_delivery", "novation_fee"]
VALID_DIRECTIONS = ["inbound", "outbound", "bilateral"]
VALID_VENUES = ["bilateral_otc", "cleared_ccp", "prime_brokered", "cls_settled"]
VALID_CATEGORIES = ["FAIL-SSI", "FAIL-UNMATCHED", "FAIL-CASHSHORT", "FAIL-CUTOFF", "FAIL-AMT",
                    "FAIL-NET", "FAIL-SECSHORT", "FAIL-CCY", "FAIL-ACCT", "FAIL-FX",
                    "FAIL-AGENT", "FAIL-CORPACT", "FAIL-COMPLIANCE", "FAIL-NOVATION", "FAIL-DUP"]
VALID_ACTIONS = ["rebook_ssi", "reinstruct_payment", "fund_shortfall", "chase_counterparty",
                 "recall_payment", "apply_netting", "escalate_compliance", "initiate_buyin",
                 "claim_interest", "no_action"]
VALID_OWNERS = ["settlements_ops", "collateral_ops", "trading_desk", "credit_risk",
                "compliance", "custodian_relations"]

REQUIRED_CASE_FIELDS = [
    "case_id", "benchmark_version", "workflow", "asset_class", "settlement_type",
    "fail_direction", "venue_type", "difficulty", "risk_level", "scenario_description",
    "business_context", "input", "ground_truth", "scoring_criteria", "failure_modes",
    "version_history", "generation_metadata",
]
REQUIRED_GT_FIELDS = [
    "fail_exists", "primary_fail", "recommended_action", "escalation_required",
    "human_review_required", "severity", "confidence", "settle_now_amount",
    "resolution_action_type", "resolution_owner",
]
REQUIRED_INPUT_FIELDS = ["internal_settlement_record", "custodian_status",
                         "counterparty_advice", "context"]
REQUIRED_PF_FIELDS = ["category", "field", "expected_value", "observed_value",
                      "observed_source", "difference", "difference_unit",
                      "fail_amount", "correct_amount"]


def validate(filepath):
    path = Path(filepath)
    errors, warnings = [], []
    try:
        cases = json.loads(path.read_text())
    except Exception as e:
        print(f"FAIL — cannot parse: {e}")
        return False
    if not isinstance(cases, list):
        print("FAIL — top-level must be an array")
        return False

    ids = set()
    for i, case in enumerate(cases):
        prefix = f"Case {i} ({case.get('case_id', 'NO ID')})"
        for f in REQUIRED_CASE_FIELDS:
            if f not in case:
                errors.append(f"{prefix}: missing '{f}'")
        cid = case.get("case_id", "")
        if not (cid.startswith("AAL-D-004-") and cid[10:].isdigit() and len(cid) == 13):
            errors.append(f"{prefix}: bad case_id format")
        if cid in ids:
            errors.append(f"{prefix}: duplicate case_id")
        ids.add(cid)
        checks = [("asset_class", VALID_ASSET_CLASSES), ("difficulty", VALID_DIFFICULTIES),
                  ("workflow", VALID_WORKFLOWS), ("settlement_type", VALID_SETTLEMENT_TYPES),
                  ("fail_direction", VALID_DIRECTIONS), ("venue_type", VALID_VENUES)]
        for key, valid in checks:
            if case.get(key) not in valid:
                errors.append(f"{prefix}: invalid {key} '{case.get(key)}'")
        if not (1 <= case.get("risk_level", 0) <= 5):
            errors.append(f"{prefix}: risk_level must be 1-5")
        inp = case.get("input", {})
        for f in REQUIRED_INPUT_FIELDS:
            if f not in inp:
                errors.append(f"{prefix}: input missing '{f}'")
        gt = case.get("ground_truth", {})
        for f in REQUIRED_GT_FIELDS:
            if f not in gt:
                errors.append(f"{prefix}: ground_truth missing '{f}'")
        if gt.get("confidence") not in VALID_CONFIDENCES:
            errors.append(f"{prefix}: confidence must be definitive")
        if gt.get("resolution_action_type") not in VALID_ACTIONS:
            errors.append(f"{prefix}: invalid resolution_action_type")
        if gt.get("resolution_owner") not in VALID_OWNERS:
            errors.append(f"{prefix}: invalid resolution_owner")
        pf = gt.get("primary_fail")
        if gt.get("fail_exists"):
            if not isinstance(pf, dict):
                errors.append(f"{prefix}: fail case with null primary_fail")
            else:
                for f in REQUIRED_PF_FIELDS:
                    if f not in pf:
                        errors.append(f"{prefix}: primary_fail missing '{f}'")
                if pf.get("category") not in VALID_CATEGORIES:
                    errors.append(f"{prefix}: invalid category '{pf.get('category')}'")
        else:
            if pf is not None:
                errors.append(f"{prefix}: clean case with primary_fail")
            if gt.get("severity") != 1:
                errors.append(f"{prefix}: clean severity != 1")
        if len(case.get("failure_modes", [])) < 1:
            errors.append(f"{prefix}: no failure modes")
        if case.get("difficulty") == "complex" and len(case.get("failure_modes", [])) < 2:
            warnings.append(f"{prefix}: complex case with <2 failure modes")
        if not case.get("version_history"):
            errors.append(f"{prefix}: missing version_history")
        # no-leakage: gt/metadata keys must not appear inside input
        blob = json.dumps(inp)
        for leak in ("ground_truth", "injected_error", "correct_values",
                     "generation_metadata", "trap_type", "distribution_cell"):
            if leak in blob:
                errors.append(f"{prefix}: input leaks '{leak}'")

    n = len(cases)
    valid = not errors
    print(f"\n{'VALID' if valid else 'INVALID'} — {path.name}   cases={n} "
          f"errors={len(errors)} warnings={len(warnings)}")
    for e in errors[:20]:
        print("   ✗", e)
    if len(errors) > 20:
        print(f"   ... and {len(errors) - 20} more")
    for w in warnings[:10]:
        print("   ⚠", w)
    for key in ("difficulty", "venue_type", "asset_class"):
        print(f"  {key}: {dict(Counter(c.get(key) for c in cases))}")
    return valid


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args()
    sys.exit(0 if validate(args.file) else 1)
