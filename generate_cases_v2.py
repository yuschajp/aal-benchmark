#!/usr/bin/env python3
"""
AI Alpha Labs — Benchmark Case Generator (v2 / scale-to-250)
AAL-D-001 · v1.0

Scales the Trade Confirmation Exception Identification benchmark from the
seed 50 to the full v1.0 target of 250 cases.

Design
------
The v1 generator parameterised only equities / fixed_income / listed_futures;
the four derivative classes (options, interest_rate_swap, fx_forward, credit)
existed solely as four hand-authored representative cases. v2 makes every asset
class first-class via a single config-driven assembler so each class gets the
full spread of exception types (price/rate, qty/notional, dates, counterparty,
SSI, commission, account/allocation, convention/term, currency, multi-exception)
plus clean matches.

Targets (validated, not asserted):
  Difficulty : 100 easy · 100 moderate · 50 complex   (40/40/20)
  Asset mix  : operational-reality weighting for a multi-strat (see ASSET_TARGETS)
  Clean      : ~28% false-positive-resistance cases
  Categories : 14+ of the AAL-D-001 exception catalog represented

The 10 hand-authored representative cases (IDs 001-010) are preserved verbatim
as the canonical seed; this script generates 011-250.

Usage
-----
    python generate_cases_v2.py --output datasets/AAL-D-001-v1.0.json
    python generate_cases_v2.py --output datasets/AAL-D-001-v1.0.json --validate
    python generate_cases_v2.py --report-only        # print plan, write nothing
"""

import os
import json
import random
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta

random.seed(2026001)  # Reproducible generation (matches v1)

BASE_DATE = datetime(2026, 7, 1)
VERSION = "1.0"

# ── REFERENCE DATA ────────────────────────────────────────────────────────────
# Expanded vs v1: more names per class, and full coverage of the four
# derivative classes that v1 only hand-authored.

EQUITIES = [
    {"instrument": "AAPL US Equity", "cusip": "037833100", "px": 218.50},
    {"instrument": "MSFT US Equity", "cusip": "594918104", "px": 445.30},
    {"instrument": "NVDA US Equity", "cusip": "67066G104", "px": 131.85},
    {"instrument": "AMZN US Equity", "cusip": "023135106", "px": 232.50},
    {"instrument": "GOOGL US Equity", "cusip": "02079K305", "px": 178.40},
    {"instrument": "META US Equity", "cusip": "30303M102", "px": 628.40},
    {"instrument": "TSLA US Equity", "cusip": "88160R101", "px": 248.60},
    {"instrument": "JPM US Equity", "cusip": "46625H100", "px": 265.90},
    {"instrument": "GS US Equity", "cusip": "38141G104", "px": 618.25},
    {"instrument": "BRK/B US Equity", "cusip": "084670702", "px": 484.50},
    {"instrument": "UNH US Equity", "cusip": "91324P102", "px": 522.30},
    {"instrument": "V US Equity", "cusip": "92826C839", "px": 312.40},
    {"instrument": "XOM US Equity", "cusip": "30231G102", "px": 118.40},
    {"instrument": "PG US Equity", "cusip": "742718109", "px": 168.75},
    {"instrument": "HD US Equity", "cusip": "437076102", "px": 378.20},
    {"instrument": "AVGO US Equity", "cusip": "11135F101", "px": 172.30},
    {"instrument": "LLY US Equity", "cusip": "532457108", "px": 795.10},
    {"instrument": "COST US Equity", "cusip": "22160K105", "px": 905.60},
]

FIXED_INCOME = [
    {"instrument": "US Treasury 4.25% 2028", "cusip": "91282CJR4", "face": 5000000, "px": 98.750},
    {"instrument": "US Treasury 4.50% 2034", "cusip": "91282CKL7", "face": 10000000, "px": 97.125},
    {"instrument": "US Treasury 3.875% 2030", "cusip": "91282CHT1", "face": 10000000, "px": 96.250},
    {"instrument": "US Treasury 4.00% 2026", "cusip": "91282CHP9", "face": 25000000, "px": 99.875},
    {"instrument": "US Treasury 4.625% 2055", "cusip": "912810UC9", "face": 8000000, "px": 95.500},
    {"instrument": "Corporate Bond Alpha 5.00% 2031", "cusip": "38141GXZ2", "face": 2000000, "px": 101.250},
    {"instrument": "Corporate Bond Beta 4.75% 2029", "cusip": "46625HYA3", "face": 3000000, "px": 99.500},
    {"instrument": "Corporate Bond Gamma 5.25% 2033", "cusip": "594918YB1", "face": 5000000, "px": 102.375},
    {"instrument": "Corporate Bond Delta 5.50% 2030", "cusip": "037833YK1", "face": 4000000, "px": 100.625},
]

FUTURES = [
    {"instrument": "ES Sep26", "exchange": "CME", "multiplier": 50, "px": 5842.25},
    {"instrument": "NQ Sep26", "exchange": "CME", "multiplier": 20, "px": 21250.50},
    {"instrument": "YM Sep26", "exchange": "CBOT", "multiplier": 5, "px": 42180.00},
    {"instrument": "CL Aug26", "exchange": "NYMEX", "multiplier": 1000, "px": 78.45},
    {"instrument": "GC Aug26", "exchange": "COMEX", "multiplier": 100, "px": 2385.60},
    {"instrument": "ZN Sep26", "exchange": "CBOT", "multiplier": 1000, "px": 108.156},
    {"instrument": "ZF Sep26", "exchange": "CBOT", "multiplier": 1000, "px": 106.078},
    {"instrument": "6E Sep26", "exchange": "CME", "multiplier": 125000, "px": 1.0855},
    {"instrument": "SR3 Dec26", "exchange": "CME", "multiplier": 2500, "px": 96.125},
]

OPTIONS = [
    # listed equity / ETF — American
    {"instrument": "AAPL Jul26 220 Call", "underlying": "AAPL US Equity", "exchange": "CBOE",
     "strike": 220.00, "expiry": "2026-07-18", "otype": "Call", "style": "American", "px": 5.20},
    {"instrument": "NVDA Jul26 140 Call", "underlying": "NVDA US Equity", "exchange": "CBOE",
     "strike": 140.00, "expiry": "2026-07-18", "otype": "Call", "style": "American", "px": 4.85},
    {"instrument": "SPY Jul26 590 Put", "underlying": "SPY US Equity", "exchange": "CBOE",
     "strike": 590.00, "expiry": "2026-07-18", "otype": "Put", "style": "American", "px": 8.40},
    {"instrument": "TSLA Aug26 260 Call", "underlying": "TSLA US Equity", "exchange": "CBOE",
     "strike": 260.00, "expiry": "2026-08-15", "otype": "Call", "style": "American", "px": 12.30},
    {"instrument": "QQQ Jul26 500 Put", "underlying": "QQQ US Equity", "exchange": "NASDAQ",
     "strike": 500.00, "expiry": "2026-07-18", "otype": "Put", "style": "American", "px": 6.15},
    {"instrument": "MSFT Sep26 450 Call", "underlying": "MSFT US Equity", "exchange": "CBOE",
     "strike": 450.00, "expiry": "2026-09-18", "otype": "Call", "style": "American", "px": 18.70},
    # index — European, cash-settled
    {"instrument": "SPX Aug26 5900 Put", "underlying": "SPX Index", "exchange": "CBOE",
     "strike": 5900.00, "expiry": "2026-08-21", "otype": "Put", "style": "European", "px": 92.50},
    {"instrument": "SPX Sep26 6000 Call", "underlying": "SPX Index", "exchange": "CBOE",
     "strike": 6000.00, "expiry": "2026-09-18", "otype": "Call", "style": "European", "px": 78.30},
]

# Interest rate swaps — tenors, indices, currencies, conventions
IRS = [
    {"instrument": "USD SOFR IRS 2Y", "notional": 25000000, "fixed": 3.875, "tenor": "2Y",
     "ccy": "USD", "float_idx": "SOFR", "dcf_fixed": "30/360", "dcf_float": "ACT/360",
     "freq_fixed": "Semi-Annual", "freq_float": "Quarterly", "book": "USD-RATES", "acct": "FUND-A-RATES"},
    {"instrument": "USD SOFR IRS 5Y", "notional": 50000000, "fixed": 4.125, "tenor": "5Y",
     "ccy": "USD", "float_idx": "SOFR", "dcf_fixed": "30/360", "dcf_float": "ACT/360",
     "freq_fixed": "Semi-Annual", "freq_float": "Quarterly", "book": "USD-RATES", "acct": "FUND-A-RATES"},
    {"instrument": "USD SOFR IRS 10Y", "notional": 100000000, "fixed": 4.350, "tenor": "10Y",
     "ccy": "USD", "float_idx": "SOFR", "dcf_fixed": "30/360", "dcf_float": "ACT/360",
     "freq_fixed": "Semi-Annual", "freq_float": "Quarterly", "book": "USD-RATES", "acct": "FUND-A-RATES"},
    {"instrument": "USD SOFR IRS 30Y", "notional": 40000000, "fixed": 4.480, "tenor": "30Y",
     "ccy": "USD", "float_idx": "SOFR", "dcf_fixed": "30/360", "dcf_float": "ACT/360",
     "freq_fixed": "Semi-Annual", "freq_float": "Quarterly", "book": "USD-RATES", "acct": "FUND-B-RATES"},
    {"instrument": "EUR ESTR IRS 5Y", "notional": 30000000, "fixed": 2.950, "tenor": "5Y",
     "ccy": "EUR", "float_idx": "ESTR", "dcf_fixed": "30/360", "dcf_float": "ACT/360",
     "freq_fixed": "Annual", "freq_float": "Annual", "book": "EUR-RATES", "acct": "FUND-A-RATES"},
    {"instrument": "EUR EURIBOR IRS 7Y", "notional": 35000000, "fixed": 3.050, "tenor": "7Y",
     "ccy": "EUR", "float_idx": "EURIBOR-6M", "dcf_fixed": "30/360", "dcf_float": "ACT/360",
     "freq_fixed": "Annual", "freq_float": "Semi-Annual", "book": "EUR-RATES", "acct": "FUND-B-RATES"},
    {"instrument": "GBP SONIA IRS 10Y", "notional": 25000000, "fixed": 4.050, "tenor": "10Y",
     "ccy": "GBP", "float_idx": "SONIA", "dcf_fixed": "ACT/365F", "dcf_float": "ACT/365F",
     "freq_fixed": "Annual", "freq_float": "Annual", "book": "GBP-RATES", "acct": "FUND-A-RATES"},
]

# FX forwards & NDFs — majors, crosses, deliverable + non-deliverable
FX = [
    {"instrument": "EUR/USD FWD 1M", "buy": "EUR", "sell": "USD", "buy_amt": 10000000, "rate": 1.0850, "ndf": False, "tenor": "1M"},
    {"instrument": "GBP/USD FWD 3M", "buy": "GBP", "sell": "USD", "buy_amt": 5000000, "rate": 1.2720, "ndf": False, "tenor": "3M"},
    {"instrument": "USD/JPY FWD 1M", "buy": "USD", "sell": "JPY", "buy_amt": 20000000, "rate": 157.50, "ndf": False, "tenor": "1M"},
    {"instrument": "USD/CHF FWD 2M", "buy": "USD", "sell": "CHF", "buy_amt": 8000000, "rate": 0.8840, "ndf": False, "tenor": "2M"},
    {"instrument": "AUD/USD FWD 1M", "buy": "AUD", "sell": "USD", "buy_amt": 12000000, "rate": 0.6610, "ndf": False, "tenor": "1M"},
    {"instrument": "EUR/GBP FWD 3M", "buy": "EUR", "sell": "GBP", "buy_amt": 9000000, "rate": 0.8530, "ndf": False, "tenor": "3M"},
    {"instrument": "USD/CAD FWD 1M", "buy": "USD", "sell": "CAD", "buy_amt": 15000000, "rate": 1.3680, "ndf": False, "tenor": "1M"},
    {"instrument": "USD/INR NDF 1M", "buy": "USD", "sell": "INR", "buy_amt": 10000000, "rate": 83.45, "ndf": True, "tenor": "1M"},
    {"instrument": "USD/KRW NDF 1M", "buy": "USD", "sell": "KRW", "buy_amt": 8000000, "rate": 1378.0, "ndf": True, "tenor": "1M"},
    {"instrument": "USD/BRL NDF 2M", "buy": "USD", "sell": "BRL", "buy_amt": 6000000, "rate": 5.470, "ndf": True, "tenor": "2M"},
]

# Credit default swaps — single-name (IG/HY) + index
CREDIT = [
    {"instrument": "CDS 5Y", "ref": "Investment Grade Corp A", "notional": 10000000, "spread": 75,
     "coupon": 100, "restructuring": "Modified Restructuring", "kind": "single_name", "book": "CREDIT-PROTECTION", "acct": "FUND-B-CREDIT"},
    {"instrument": "CDS 5Y", "ref": "Investment Grade Corp B", "notional": 5000000, "spread": 120,
     "coupon": 100, "restructuring": "No Restructuring", "kind": "single_name", "book": "CREDIT-PROTECTION", "acct": "FUND-B-CREDIT"},
    {"instrument": "CDS 3Y", "ref": "High Yield Corp C", "notional": 3000000, "spread": 350,
     "coupon": 500, "restructuring": "No Restructuring", "kind": "single_name", "book": "CREDIT-HY", "acct": "FUND-A-CREDIT"},
    {"instrument": "CDS 5Y", "ref": "High Yield Corp D", "notional": 4000000, "spread": 420,
     "coupon": 500, "restructuring": "No Restructuring", "kind": "single_name", "book": "CREDIT-HY", "acct": "FUND-A-CREDIT"},
    {"instrument": "CDX.IG.42 5Y", "ref": "CDX North American IG Series 42", "notional": 25000000, "spread": 62,
     "coupon": 100, "restructuring": "No Restructuring", "kind": "index", "book": "CREDIT-INDEX", "acct": "FUND-A-CREDIT"},
    {"instrument": "CDX.HY.42 5Y", "ref": "CDX North American HY Series 42", "notional": 15000000, "spread": 340,
     "coupon": 500, "restructuring": "No Restructuring", "kind": "index", "book": "CREDIT-INDEX", "acct": "FUND-A-CREDIT"},
    {"instrument": "iTraxx Main S42 5Y", "ref": "iTraxx Europe Main Series 42", "notional": 20000000, "spread": 58,
     "coupon": 100, "restructuring": "Modified Modified Restructuring", "kind": "index", "book": "CREDIT-INDEX", "acct": "FUND-B-CREDIT"},
]

COUNTERPARTIES = ["Prime Broker Alpha", "Prime Broker Beta", "Prime Broker Gamma", "Prime Broker Delta"]
CLEARING_BROKERS = ["Clearing Broker Alpha", "Clearing Broker Beta", "Clearing Broker Gamma"]
EXEC_BROKERS = ["Execution Broker One", "Execution Broker Two", "Execution Broker Three", "Execution Broker Four"]
DEALERS = ["Dealer Bank Alpha", "Dealer Bank Beta", "Dealer Bank Gamma", "Dealer Bank Delta"]
SIDES = ["Buy", "Sell"]
ACCOUNTS_EQ = ["FUND-A-EQUITY", "FUND-B-EQUITY", "FUND-C-EQUITY"]
ACCOUNTS_FI = ["FUND-A-FIXED-INCOME", "FUND-B-FIXED-INCOME"]

SSI_DTC = [
    "DTC 0001 / Account 44821", "DTC 0002 / Account 77103",
    "DTC 0003 / Account 55290", "DTC 0001 / Account 88412",
]
SSI_FED = ["Fed Wire / ABA 021000089", "Fed Wire / ABA 021000018"]
SSI_USD_NOSTRO = [
    "JPMorgan / ABA 021000021 / Account 88412",
    "Citibank / ABA 021000089 / Account 99201",
]
SSI_EUR_NOSTRO = [
    "Correspondent Bank X / SWIFT: COBADEFF / Account: DE89370400440532013000",
    "Correspondent Bank Y / SWIFT: DEUTDEFF / Account: DE27100777770209299700",
]

# ── ID / DATE HELPERS ─────────────────────────────────────────────────────────

_SEQ = 10  # 001-010 are the hand-authored representative cases


def next_id():
    global _SEQ
    _SEQ += 1
    return f"AAL-D-001-{_SEQ:03d}", _SEQ


def trade_id(prefix, seq):
    return f"{prefix}-20260701-{seq:04d}"


def settle(td, convention="T+1"):
    days = int(convention.replace("T+", ""))
    sd = td + timedelta(days=days)
    while sd.weekday() >= 5:
        sd += timedelta(days=1)
    return sd.strftime("%Y-%m-%d")


def add_months(d, months):
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, 28)
    nd = datetime(y, m, day)
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd.strftime("%Y-%m-%d")


def rp(p):
    return round(p, 2)


def vh():
    return [{"version": VERSION, "date": "2026-07", "change": "Initial"}]


# ── BASE RECORD BUILDERS ──────────────────────────────────────────────────────
# Each returns the matched economic record for the asset class plus the
# internal-only fields. Exception generators mutate one field on the
# counterparty side and write ground truth accordingly.

def base_equity(inst):
    seq = _SEQ
    side = random.choice(SIDES)
    rec = {
        "trade_id": trade_id("EQ", seq), "trade_date": "2026-07-01", "settlement_date": settle(BASE_DATE),
        "instrument": inst["instrument"], "cusip": inst["cusip"],
        "counterparty": random.choice(COUNTERPARTIES), "side": side,
        "quantity": random.choice([2000, 3000, 5000, 8000, 10000, 15000]),
        "price": rp(inst["px"] + random.uniform(-1.5, 1.5)), "currency": "USD",
        "settlement_instructions": random.choice(SSI_DTC),
        "broker": random.choice(EXEC_BROKERS), "commission": round(random.choice([0.01, 0.015, 0.02]), 3),
    }
    internal = {"account": random.choice(ACCOUNTS_EQ), "book": "US-EQUITY-LONG", "status": "Unconfirmed"}
    return rec, internal


def base_fi(inst):
    seq = _SEQ
    rec = {
        "trade_id": trade_id("FI", seq), "trade_date": "2026-07-01", "settlement_date": settle(BASE_DATE),
        "instrument": inst["instrument"], "cusip": inst["cusip"],
        "counterparty": random.choice(COUNTERPARTIES), "side": random.choice(SIDES),
        "face_value": inst["face"], "price": inst["px"], "currency": "USD",
        "settlement_instructions": random.choice(SSI_FED), "broker": "Direct",
    }
    internal = {"account": random.choice(ACCOUNTS_FI), "book": "FIXED-INCOME", "status": "Unconfirmed"}
    return rec, internal


def base_futures(inst):
    seq = _SEQ
    rec = {
        "trade_id": trade_id("FUT", seq), "trade_date": "2026-07-01", "settlement_date": "2026-07-01",
        "instrument": inst["instrument"], "exchange": inst["exchange"],
        "counterparty": random.choice(CLEARING_BROKERS), "side": random.choice(SIDES),
        "quantity": random.choice([10, 20, 25, 50, 100, 200]),
        "price": inst["px"], "currency": "USD",
        "multiplier": inst["multiplier"], "commission": round(random.choice([1.00, 1.25, 1.50]), 2),
    }
    internal = {"account": "FUND-A-FUTURES", "book": "FUTURES", "status": "Unconfirmed"}
    return rec, internal


def base_option(inst):
    seq = _SEQ
    rec = {
        "trade_id": trade_id("OPT", seq), "trade_date": "2026-07-01", "settlement_date": settle(BASE_DATE),
        "instrument": inst["instrument"], "underlying": inst["underlying"], "exchange": inst["exchange"],
        "counterparty": random.choice(COUNTERPARTIES), "side": random.choice(SIDES),
        "quantity": random.choice([50, 100, 200, 300, 500]), "multiplier": 100,
        "price": inst["px"], "currency": "USD",
        "strike": inst["strike"], "expiry": inst["expiry"],
        "option_type": inst["otype"], "exercise_style": inst["style"],
        "commission": round(random.choice([0.50, 0.65, 0.75]), 2), "commission_unit": "per_contract",
    }
    internal = {"account": "FUND-A-OPTIONS", "book": "US-OPTIONS", "status": "Unconfirmed"}
    return rec, internal


def base_irs(inst):
    seq = _SEQ
    eff = add_months(BASE_DATE, 0)
    eff_dt = datetime.strptime(settle(BASE_DATE, "T+2"), "%Y-%m-%d")
    years = int(inst["tenor"].replace("Y", ""))
    mat = datetime(eff_dt.year + years, eff_dt.month, eff_dt.day).strftime("%Y-%m-%d")
    rec = {
        "trade_id": trade_id("IRS", seq), "trade_date": "2026-07-01",
        "effective_date": eff_dt.strftime("%Y-%m-%d"), "maturity_date": mat,
        "instrument": inst["instrument"], "counterparty": random.choice(DEALERS),
        "notional": inst["notional"], "currency": inst["ccy"],
        "fixed_rate": inst["fixed"], "floating_rate": inst["float_idx"], "floating_spread": 0,
        "payment_frequency_fixed": inst["freq_fixed"], "payment_frequency_float": inst["freq_float"],
        "day_count_fixed": inst["dcf_fixed"], "day_count_float": inst["dcf_float"],
        "usi": f"1030F00AB7TX{seq:06d}",
    }
    internal = {"account": inst["acct"], "book": inst["book"], "status": "Unconfirmed"}
    return rec, internal


def base_fx(inst):
    seq = _SEQ
    months = int(inst["tenor"].replace("M", ""))
    sd = add_months(BASE_DATE, months)
    sell_amt = round(inst["buy_amt"] * inst["rate"], 2) if inst["sell"] != "JPY" else round(inst["buy_amt"] * inst["rate"])
    rec = {
        "trade_id": trade_id("FX", seq), "trade_date": "2026-07-01", "settlement_date": sd,
        "instrument": inst["instrument"], "counterparty": random.choice(COUNTERPARTIES),
        "buy_currency": inst["buy"], "buy_amount": inst["buy_amt"],
        "sell_currency": inst["sell"], "sell_amount": sell_amt,
        "forward_rate": inst["rate"], "non_deliverable": inst["ndf"],
    }
    if inst["ndf"]:
        rec["fixing_source"] = {"INR": "RBI Reference Rate", "KRW": "KFTC18", "BRL": "PTAX"}.get(inst["sell"], "WMR")
        rec["settlement_currency"] = "USD"
    else:
        # deliverable legs carry SSI per currency
        rec["buy_settlement"] = SSI_EUR_NOSTRO[0] if inst["buy"] == "EUR" else SSI_USD_NOSTRO[0]
        rec["sell_settlement"] = SSI_USD_NOSTRO[0] if inst["sell"] == "USD" else SSI_EUR_NOSTRO[0]
    internal = {"account": "FUND-A-FX", "book": "FX-HEDGING", "status": "Unconfirmed"}
    return rec, internal


def base_credit(inst):
    seq = _SEQ
    eff = settle(BASE_DATE, "T+1")
    eff_dt = datetime.strptime(eff, "%Y-%m-%d")
    years = int(inst["instrument"].split()[-1].replace("Y", "")) if inst["instrument"].split()[-1].endswith("Y") else 5
    mat = datetime(eff_dt.year + years, eff_dt.month, eff_dt.day).strftime("%Y-%m-%d")
    rec = {
        "trade_id": trade_id("CDS", seq), "trade_date": "2026-07-01",
        "effective_date": eff, "maturity_date": mat,
        "instrument": inst["instrument"], "reference_entity": inst["ref"],
        "counterparty": random.choice(DEALERS), "notional": inst["notional"], "currency": "USD",
        "spread": inst["spread"], "spread_unit": "bps", "running_coupon": inst["coupon"],
        "protection": random.choice(["Buy", "Sell"]),
        "payment_frequency": "Quarterly", "day_count": "ACT/360",
        "restructuring": inst["restructuring"], "index_kind": inst["kind"],
    }
    internal = {"account": inst["acct"], "book": inst["book"], "status": "Unconfirmed"}
    return rec, internal


# Per-asset configuration the assembler reads.
ASSET_CFG = {
    "equities":           {"data": EQUITIES,     "base": base_equity,  "ac": "equities"},
    "fixed_income":       {"data": FIXED_INCOME, "base": base_fi,      "ac": "fixed_income"},
    "listed_futures":     {"data": FUTURES,      "base": base_futures, "ac": "listed_futures"},
    "options":            {"data": OPTIONS,      "base": base_option,  "ac": "options"},
    "interest_rate_swap": {"data": IRS,          "base": base_irs,     "ac": "interest_rate_swap"},
    "fx_forward":         {"data": FX,           "base": base_fx,      "ac": "fx_forward"},
    "credit":             {"data": CREDIT,       "base": base_credit,  "ac": "credit"},
}


# ── CASE ASSEMBLER ────────────────────────────────────────────────────────────

def assemble(ac, difficulty, risk, scen, ctx, cp_conf, int_rec, gt, scoring, fms, notes, refs=None):
    cid, _ = next_id()
    return {
        "case_id": cid, "benchmark_version": VERSION,
        "workflow": "trade_confirmation_exception",
        "asset_class": ac, "difficulty": difficulty, "risk_level": risk,
        "scenario_description": scen, "business_context": ctx,
        "input": {"counterparty_confirmation": cp_conf, "internal_record": int_rec},
        "ground_truth": gt, "scoring_criteria": scoring,
        "failure_modes": fms, "reviewer_notes": notes,
        "references": refs or [], "version_history": vh(),
    }


def clean_gt():
    return {
        "exception_exists": False, "primary_exception": None, "secondary_exception": None,
        "recommended_action": "No action required. Confirmation matched.",
        "escalation_required": False, "escalation_target": None,
        "human_review_required": False, "severity": 1, "confidence": "definitive",
    }


SC_CLEAN = {"exception_detection": "exact_match", "fabrication_check": "binary"}
SC_FIELD = {
    "exception_detection": "exact_match", "category_identification": "exact_match",
    "field_identification": "exact_match", "action_recommendation": "semantic_match",
    "fabrication_check": "binary",
}
SC_NUM = {
    "exception_detection": "exact_match", "category_identification": "exact_match",
    "field_identification": "exact_match",
    "numeric_values": {"method": "numeric_match", "tolerance": 0.01},
    "exposure_calculation": {"method": "numeric_match", "tolerance": 1.00},
    "action_recommendation": "semantic_match", "fabrication_check": "binary",
}


# ── GENERATORS ────────────────────────────────────────────────────────────────
# Signature: gen_*(asset_key, inst, difficulty) -> case dict
# They split into (cp, internal_only) via the base builder, then break a field.

def _split(asset_key, inst):
    rec, internal = ASSET_CFG[asset_key]["base"](inst)
    cp = dict(rec)
    int_rec = {**rec, **internal}
    return cp, int_rec


def gen_clean(asset_key, inst, difficulty="easy"):
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    return assemble(
        ac, "easy", 1,
        f"Clean {ac.replace('_', ' ')} confirmation — {inst['instrument']}. All fields match.",
        "Routine confirmation. All economic and settlement fields agree across the two records.",
        cp, int_rec, clean_gt(), SC_CLEAN, ["FM-02"],
        f"Clean match ({ac}). Tests false-positive resistance.",
    )


# economic rate field per asset class: (field, unit, exposure_fn(cp,int,rec))
def gen_rate_break(asset_key, inst, difficulty="moderate"):
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    easy = difficulty == "easy"

    if ac in ("equities",):
        f = "price"; unit = "per_share"
        d = round(random.choice([0.25, 0.50, 1.00]) if easy else random.choice([0.03, 0.05, 0.08]), 2)
        intv = int_rec[f]; cpv = rp(intv + d * random.choice([1, -1]))
        diff = round(abs(cpv - intv), 2); exp = round(diff * cp["quantity"], 2)
        rl = 3 if exp > 1000 else 2
        action = f"Escalate price break to trading desk. ${diff}/share on {cp['quantity']:,} = ${exp:,.2f}."
        tgt = "Trading desk"
    elif ac == "options":
        f = "price"; unit = "per_contract_premium"
        d = round(random.choice([0.25, 0.50]) if easy else random.choice([0.05, 0.10, 0.15]), 2)
        intv = int_rec[f]; cpv = rp(intv + d * random.choice([1, -1]))
        diff = round(abs(cpv - intv), 2)
        exp = round(diff * cp["quantity"] * cp["multiplier"], 2); rl = 3
        action = f"Premium break. ${diff} × {cp['quantity']} contracts × {cp['multiplier']} = ${exp:,.2f}. Escalate to desk."
        tgt = "Trading desk"
    elif ac == "fixed_income":
        f = "price"; unit = "points"
        d = round(random.choice([0.125, 0.250, 0.500]) if easy else random.choice([0.015625, 0.03125, 0.0625]), 6)
        intv = int_rec[f]; cpv = round(intv + d * random.choice([1, -1]), 6)
        diff = round(abs(cpv - intv), 6); exp = round(diff / 100 * cp["face_value"], 2)
        rl = 4 if exp > 50000 else 3
        action = f"Price break on bond. {diff} pts on ${cp['face_value']:,} face = ${exp:,.2f}. Escalate to desk."
        tgt = "Trading desk"
    elif ac == "interest_rate_swap":
        f = "fixed_rate"; unit = "rate_pct"
        bp = random.choice([1.0, 2.5, 5.0]) if easy else random.choice([0.25, 0.5])
        intv = int_rec[f]; cpv = round(intv + (bp / 100.0) * random.choice([1, -1]), 4)
        diff = round(abs(cpv - intv), 4)
        # ~ DV01 rough proxy: notional * tenor_years * 0.0001 per bp; report annual coupon delta instead (clean & checkable)
        exp = round(abs(cpv - intv) / 100.0 * cp["notional"], 2)
        rl = 4 if cp["notional"] >= 50000000 else 3
        action = f"Fixed-rate break ({round(diff*100,1)}bp) on {inst['instrument']}. Annual fixed-leg delta ≈ ${exp:,.2f}. Escalate to rates desk; do not affirm."
        tgt = "Rates trading desk"
    elif ac == "fx_forward":
        f = "forward_rate"; unit = "fx_rate"
        pip = random.choice([10, 25, 50]) if easy else random.choice([2, 5])
        scale = 0.01 if cp["sell_currency"] in ("JPY", "KRW") else 0.0001
        intv = int_rec[f]; cpv = round(intv + pip * scale * random.choice([1, -1]),
                                       2 if cp["sell_currency"] in ("JPY", "KRW") else 4)
        diff = round(abs(cpv - intv), 4)
        exp = round(diff * cp["buy_amount"] if cp["sell_currency"] != "JPY" else diff * cp["buy_amount"], 2)
        rl = 4 if cp["buy_amount"] >= 10000000 else 3
        action = f"Forward-rate break ({pip} pips) on {inst['instrument']}. P&L impact ≈ {cp['sell_currency']} {exp:,.2f}. Escalate to FX desk."
        tgt = "FX trading desk"
    elif ac == "credit":
        f = "spread"; unit = "bps"
        d = random.choice([5, 10, 25]) if easy else random.choice([1, 2, 3])
        intv = int_rec[f]; cpv = intv + d * random.choice([1, -1])
        diff = abs(cpv - intv)
        # rough risky-PV01 proxy: notional * tenor * 0.0001 per bp; report 1bp running-spread annual delta
        exp = round(diff / 10000.0 * cp["notional"], 2)
        rl = 4 if cp["notional"] >= 10000000 else 3
        action = f"CDS spread break ({diff}bp) on {inst['reference_entity']}. Annual running-spread delta ≈ ${exp:,.2f}. Escalate to credit desk."
        tgt = "Credit trading desk"
    else:
        return gen_clean(asset_key, inst)

    cp[f] = cpv
    gt = {
        "exception_exists": True,
        "primary_exception": {
            "category": "EXC-PRICE", "field": f,
            "counterparty_value": cpv, "internal_value": intv,
            "difference": diff, "difference_unit": unit, "total_exposure_usd": exp,
        },
        "secondary_exception": None, "recommended_action": action + " Do not settle until resolved.",
        "escalation_required": True, "escalation_target": tgt,
        "human_review_required": True, "severity": rl, "confidence": "definitive",
    }
    fms = ["FM-01", "FM-08"] if easy else ["FM-01", "FM-04", "FM-08"]
    return assemble(
        ac, difficulty, rl,
        f"{'Subtle ' if not easy else ''}{ac.replace('_',' ')} rate/price break on {inst['instrument']}. {f} differs.",
        f"Economic term break on {ac.replace('_',' ')}. {'Small variance — tests attention to detail.' if not easy else 'Clear discrepancy.'}",
        cp, int_rec, gt, SC_NUM, fms,
        f"Rate/price break: CP={cpv} INT={intv} diff={diff} exp=${exp:,.2f}",
    )


def gen_qty_break(asset_key, inst, difficulty="moderate"):
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    easy = difficulty == "easy"

    if ac in ("equities",):
        f = "quantity"; unit = "shares"; px = cp["price"]
        intv = int_rec[f]
        cpv = intv + (random.choice([-100, 100, -500, 500]) if not easy else random.choice([-2000, 2000, -5000, 5000]))
        diff = abs(cpv - intv); exp = round(diff * px, 2)
        rl = 4 if exp > 100000 else 3
        action = f"Quantity break. {diff:,} share difference = ${exp:,.2f} exposure. Escalate to trading desk; do not settle."
        tgt = "Trading desk"
    elif ac == "listed_futures":
        f = "quantity"; unit = "contracts"
        intv = int_rec[f]
        cpv = intv + (random.choice([-1, 1, -2, 2]) if not easy else random.choice([-5, 5, -10, 10]))
        cpv = max(cpv, 1)
        diff = abs(cpv - intv); exp = round(diff * cp["price"] * cp["multiplier"], 2)
        rl = 4 if exp > 250000 else 3
        action = f"Lot-count break. {diff} contracts × {cp['multiplier']} × {cp['price']} = ${exp:,.2f} notional. Escalate to futures desk."
        tgt = "Futures desk / clearing"
    elif ac == "options":
        f = "quantity"; unit = "contracts"
        intv = int_rec[f]
        cpv = intv + (random.choice([-10, 10, -25, 25]) if not easy else random.choice([-50, 50, -100, 100]))
        cpv = max(cpv, 10)
        diff = abs(cpv - intv); exp = round(diff * cp["price"] * cp["multiplier"], 2)
        rl = 3
        action = f"Contract-count break. {diff} contracts × {cp['multiplier']} × ${cp['price']} premium = ${exp:,.2f}. Escalate to desk."
        tgt = "Trading desk"
    else:  # notional break for IRS / FX / credit
        f = "notional" if ac != "fx_forward" else "buy_amount"
        unit = "notional"
        intv = int_rec[f]
        step = 0.01 if not easy else 0.05
        cpv = int(round(intv * (1 + step * random.choice([1, -1]))))
        diff = abs(cpv - intv); exp = float(diff)
        rl = 4 if intv >= 25000000 else 3
        action = f"Notional break on {inst['instrument']}. {ac.replace('_',' ')} notional differs by {diff:,}. Material economic mismatch — escalate; do not affirm."
        tgt = "Trading desk / middle office"

    cp[f] = cpv
    gt = {
        "exception_exists": True,
        "primary_exception": {
            "category": "EXC-QTY", "field": f,
            "counterparty_value": cpv, "internal_value": intv,
            "difference": diff, "difference_unit": unit, "total_exposure_usd": exp,
        },
        "secondary_exception": None, "recommended_action": action,
        "escalation_required": True, "escalation_target": tgt,
        "human_review_required": True, "severity": rl, "confidence": "definitive",
    }
    return assemble(
        ac, difficulty, rl,
        f"{ac.replace('_',' ').title()} quantity/notional break on {inst['instrument']}. CP {cpv:,} vs internal {intv:,}.",
        "Size mismatch creates settlement and risk-position exposure.",
        cp, int_rec, gt, SC_NUM, ["FM-01", "FM-04", "FM-08", "FM-12"],
        f"Qty/notional break: CP={cpv:,} INT={intv:,} diff={diff:,}",
    )


def gen_date_break(asset_key, inst, difficulty="moderate"):
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    date_field = "value_date" if ac == "fx_forward" else "settlement_date"
    if ac in ("interest_rate_swap", "credit"):
        date_field = "effective_date"
    intv = int_rec.get(date_field) or int_rec.get("settlement_date")
    base_dt = datetime.strptime(intv, "%Y-%m-%d")
    shift = 1 if difficulty == "moderate" else random.choice([3, 5])
    cpv = settle(base_dt, f"T+{shift}")
    cp[date_field] = cpv
    if date_field not in cp:
        cp[date_field] = cpv
    rl = 3 if difficulty == "moderate" else 4
    gt = {
        "exception_exists": True,
        "primary_exception": {
            "category": "EXC-SDATE", "field": date_field,
            "counterparty_value": cpv, "internal_value": intv,
            "difference": f"{shift} business day(s)", "difference_unit": "days", "total_exposure_usd": None,
        },
        "secondary_exception": None,
        "recommended_action": f"{date_field.replace('_',' ').title()} break — confirm convention with counterparty. CP {cpv} vs internal {intv}.",
        "escalation_required": True, "escalation_target": "Operations / counterparty",
        "human_review_required": True, "severity": rl, "confidence": "definitive",
    }
    return assemble(
        ac, difficulty, rl,
        f"{date_field.replace('_',' ').title()} mismatch on {inst['instrument']}. CP {cpv}, internal {intv}.",
        f"Date convention discrepancy. {shift} business day(s) difference; affects settlement / accrual.",
        cp, int_rec, gt, SC_FIELD, ["FM-06", "FM-07"],
        f"{date_field} break: CP={cpv} INT={intv}",
        refs=["Settlement / value-date conventions"],
    )


def gen_cpty_break(asset_key, inst, difficulty="complex"):
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    if difficulty == "moderate":
        cp["counterparty"] = "Prime Broker Alpha"; int_rec["counterparty"] = "Prime Broker Beta"
        diff = "Different counterparty"; rl = 3; conf = "definitive"
        tgt = "Operations supervisor"
    else:
        cp["counterparty"] = "Global Bank Securities LLC"; int_rec["counterparty"] = "Global Bank Capital Markets Inc"
        diff = "Different legal entity (same group) — ISDA/master-agreement implications"; rl = 4; conf = "expert_consensus"
        tgt = "Legal, documentation, operations"
    gt = {
        "exception_exists": True,
        "primary_exception": {
            "category": "EXC-CPTY", "field": "counterparty",
            "counterparty_value": cp["counterparty"], "internal_value": int_rec["counterparty"],
            "difference": diff, "difference_unit": "entity", "total_exposure_usd": None,
        },
        "secondary_exception": None,
        "recommended_action": f"Counterparty mismatch — CP shows {cp['counterparty']}, internal shows {int_rec['counterparty']}. Escalate; do not settle.",
        "escalation_required": True, "escalation_target": tgt,
        "human_review_required": True, "severity": rl, "confidence": conf,
    }
    return assemble(
        ac, difficulty, rl,
        f"Counterparty mismatch on {inst['instrument']}. CP {cp['counterparty']} vs internal {int_rec['counterparty']}.",
        diff + ".", cp, int_rec, gt, SC_FIELD, ["FM-01", "FM-05", "FM-07"],
        f"Counterparty mismatch: {cp['counterparty']} vs {int_rec['counterparty']}",
    )


def gen_comm_break(asset_key, inst, difficulty="moderate"):
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    if "commission" not in cp:
        return gen_rate_break(asset_key, inst, difficulty)
    unit = cp.get("commission_unit", "per_share")
    intv = int_rec["commission"]
    bump = random.choice([0.005, 0.01, 0.015]) if ac != "listed_futures" else random.choice([0.25, 0.50])
    cpv = round(intv + bump, 3)
    diff = round(abs(cpv - intv), 3)
    qty = cp.get("quantity", 0)
    exp = round(diff * qty * (cp.get("multiplier", 1) if ac in ("options",) else 1), 2)
    cp["commission"] = cpv
    gt = {
        "exception_exists": True,
        "primary_exception": {
            "category": "EXC-COMM", "field": "commission",
            "counterparty_value": cpv, "internal_value": intv,
            "difference": diff, "difference_unit": unit, "total_exposure_usd": exp,
        },
        "secondary_exception": None,
        "recommended_action": f"Commission discrepancy. Verify agreed rate with broker. Difference ${diff} ({unit}) → ${exp:,.2f}.",
        "escalation_required": False, "escalation_target": None,
        "human_review_required": True, "severity": 2, "confidence": "definitive",
    }
    return assemble(
        ac, "moderate", 2,
        f"Commission discrepancy on {inst['instrument']}. CP ${cpv}, internal ${intv} ({unit}).",
        "All economic fields match; only commission differs. Tests attention to non-price fields.",
        cp, int_rec, gt,
        {**SC_FIELD, "numeric_values": {"method": "numeric_match", "tolerance": 0.001}},
        ["FM-01", "FM-07"], f"Commission break: {cpv} vs {intv} ({unit})",
    )


def gen_ssi_break(asset_key, inst, difficulty="moderate"):
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    if ac == "fx_forward" and not inst["ndf"]:
        field = "buy_settlement" if cp["buy_currency"] == "EUR" else "sell_settlement"
        if field not in cp:
            field = "sell_settlement"
        cp[field] = SSI_EUR_NOSTRO[0]; int_rec[field] = SSI_EUR_NOSTRO[1]
        cpv, intv = cp[field], int_rec[field]
        exp = float(cp["buy_amount"])
        diff = "Different correspondent bank, SWIFT, and account on the deliverable leg"
    elif ac == "fixed_income":
        field = "settlement_instructions"; cp[field] = SSI_FED[0]; int_rec[field] = SSI_FED[1]
        cpv, intv = cp[field], int_rec[field]
        exp = round(cp["face_value"] * cp["price"] / 100, 2)
        diff = "Different Fed wire ABA"
    else:
        field = "settlement_instructions"
        if field not in cp:
            return gen_cpty_break(asset_key, inst, "moderate")
        cp[field] = SSI_DTC[0]; int_rec[field] = SSI_DTC[2]
        cpv, intv = cp[field], int_rec[field]
        exp = round(cp.get("quantity", 0) * cp.get("price", 0), 2)
        diff = "Different DTC participant and account"
    gt = {
        "exception_exists": True,
        "primary_exception": {
            "category": "EXC-SSI", "field": field,
            "counterparty_value": cpv, "internal_value": intv,
            "difference": diff, "difference_unit": "settlement_instruction", "total_exposure_usd": exp,
        },
        "secondary_exception": None,
        "recommended_action": f"SSI mismatch — escalate immediately. Incorrect instructions cause failed delivery. Exposure ${exp:,.2f}. Verify correct SSI before settlement.",
        "escalation_required": True, "escalation_target": "Operations, treasury",
        "human_review_required": True, "severity": 4, "confidence": "definitive",
    }
    return assemble(
        ac, "moderate", 4,
        f"SSI mismatch on {inst['instrument']}. Economic fields match; settlement instructions differ.",
        "Incorrect settlement instructions lead to failed delivery. Full leg value at risk.",
        cp, int_rec, gt, SC_FIELD, ["FM-01", "FM-03", "FM-12"],
        f"SSI break on {field}; exposure ${exp:,.2f}", refs=["Settlement instruction controls"],
    )


def gen_acct_break(asset_key, inst, difficulty="moderate"):
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    cp["account_reference"] = "FUND-A-" + ac.split("_")[0].upper()
    int_rec["account"] = "FUND-B-" + ac.split("_")[0].upper()
    gt = {
        "exception_exists": True,
        "primary_exception": {
            "category": "EXC-ACCT", "field": "account",
            "counterparty_value": cp["account_reference"], "internal_value": int_rec["account"],
            "difference": "Different account / fund allocation", "difference_unit": "account",
            "total_exposure_usd": None,
        },
        "secondary_exception": None,
        "recommended_action": "Account mismatch. Verify allocation with portfolio manager and correct with counterparty before settlement.",
        "escalation_required": True, "escalation_target": "Portfolio manager",
        "human_review_required": True, "severity": 3, "confidence": "definitive",
    }
    return assemble(
        ac, "moderate", 3,
        f"Account/allocation mismatch on {inst['instrument']}. CP {cp['account_reference']} vs internal {int_rec['account']}.",
        "Total economics match but account assignment differs — likely allocation routing error.",
        cp, int_rec, gt, SC_FIELD, ["FM-01", "FM-07"],
        f"Account mismatch: {cp['account_reference']} vs {int_rec['account']}",
    )


def gen_convention_break(asset_key, inst, difficulty="complex"):
    """Derivatives term/convention break → EXC-PROD. Day-count, payment
    frequency, floating index, restructuring clause, or exercise style."""
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    if ac == "interest_rate_swap":
        choice = random.choice(["day_count_float", "payment_frequency_float", "floating_rate"])
        if choice == "day_count_float":
            cp[choice] = "30/360"; what = "floating-leg day count"
        elif choice == "payment_frequency_float":
            cp[choice] = "Semi-Annual"; what = "floating-leg payment frequency"
        else:
            cp[choice] = {"SOFR": "Term SOFR", "ESTR": "EURIBOR-3M", "SONIA": "SONIA Compounded"}.get(int_rec[choice], "Term SOFR")
            what = "floating rate index/basis"
        field = choice
    elif ac == "credit":
        if random.random() < 0.5:
            field = "restructuring"
            cp[field] = "No Restructuring" if int_rec[field] != "No Restructuring" else "Modified Restructuring"
            what = "restructuring clause"
        else:
            field = "running_coupon"
            cp[field] = 500 if int_rec[field] == 100 else 100
            what = "running coupon convention"
    elif ac == "options":
        field = "exercise_style"
        cp[field] = "European" if int_rec[field] == "American" else "American"
        what = "exercise style"
    else:
        return gen_rate_break(asset_key, inst, "moderate")
    rl = 4
    gt = {
        "exception_exists": True,
        "primary_exception": {
            "category": "EXC-PROD", "field": field,
            "counterparty_value": cp[field], "internal_value": int_rec[field],
            "difference": f"Mismatched {what}", "difference_unit": "term", "total_exposure_usd": None,
        },
        "secondary_exception": None,
        "recommended_action": f"Economic term mismatch ({what}). This changes cashflows/payoff — do not affirm. Reconcile confirmation against trade ticket and ISDA terms; escalate to middle office and trading.",
        "escalation_required": True, "escalation_target": "Middle office, trading desk",
        "human_review_required": True, "severity": rl, "confidence": "expert_consensus",
    }
    return assemble(
        ac, "complex", rl,
        f"Economic term ({what}) mismatch on {inst['instrument']}. CP {cp[field]} vs internal {int_rec[field]}.",
        f"A {what} discrepancy alters the contract's cashflows or payoff. Subtle but materially changes valuation and risk.",
        cp, int_rec, gt, SC_FIELD, ["FM-01", "FM-04", "FM-05", "FM-09"],
        f"Convention break ({what}): {cp[field]} vs {int_rec[field]}",
        refs=["ISDA economic terms / product conventions"],
    )


def gen_ccy_break(asset_key, inst, difficulty="complex"):
    """Currency mismatch (EXC-CCY)."""
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    if ac == "fx_forward":
        field = "sell_currency"
        swap = {"USD": "EUR", "JPY": "USD", "CHF": "USD", "CAD": "USD", "GBP": "USD", "INR": "USD", "KRW": "USD", "BRL": "USD"}
        cp[field] = swap.get(int_rec[field], "USD")
        diff = "Sell-side currency differs — wrong cross booked"
    else:
        field = "currency"
        cp[field] = "EUR" if int_rec.get("currency") != "EUR" else "GBP"
        diff = "Trade currency differs"
    rl = 5
    gt = {
        "exception_exists": True,
        "primary_exception": {
            "category": "EXC-CCY", "field": field,
            "counterparty_value": cp[field], "internal_value": int_rec[field],
            "difference": diff, "difference_unit": "currency", "total_exposure_usd": None,
        },
        "secondary_exception": None,
        "recommended_action": "Currency mismatch — high severity. Wrong currency implies wrong settlement and FX exposure. Halt settlement, escalate to trading and operations immediately.",
        "escalation_required": True, "escalation_target": "Trading desk, operations",
        "human_review_required": True, "severity": rl, "confidence": "definitive",
    }
    return assemble(
        ac, "complex", rl,
        f"Currency mismatch on {inst['instrument']}. CP {cp[field]} vs internal {int_rec[field]}.",
        diff + ". Currency errors create unhedged FX exposure and settlement failure.",
        cp, int_rec, gt, SC_FIELD, ["FM-01", "FM-05", "FM-07"],
        f"Currency break: {cp[field]} vs {int_rec[field]}",
    )


def gen_dup(asset_key, inst, difficulty="moderate"):
    """Duplicate confirmation (EXC-DUP)."""
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    cp["confirmation_id"] = f"CONF-{_SEQ:05d}-A"
    cp["duplicate_of"] = f"CONF-{_SEQ:05d}-A (already affirmed earlier today)"
    int_rec["confirmation_status"] = "Already affirmed under CONF-{}-A".format(f"{_SEQ:05d}")
    gt = {
        "exception_exists": True,
        "primary_exception": {
            "category": "EXC-DUP", "field": "confirmation_id",
            "counterparty_value": cp["confirmation_id"], "internal_value": int_rec.get("trade_id"),
            "difference": "Second confirmation received for an already-affirmed trade",
            "difference_unit": "confirmation", "total_exposure_usd": None,
        },
        "secondary_exception": None,
        "recommended_action": "Duplicate confirmation. Do not affirm twice — booking twice would double the position. Tag as duplicate and notify counterparty.",
        "escalation_required": False, "escalation_target": None,
        "human_review_required": True, "severity": 2, "confidence": "definitive",
    }
    return assemble(
        ac, "moderate", 2,
        f"Duplicate confirmation for {inst['instrument']} — trade already affirmed earlier today.",
        "A second confirmation arrives for an already-matched trade. Affirming again would double-book.",
        cp, int_rec, gt, SC_FIELD, ["FM-02", "FM-11"],
        "Duplicate confirmation — must not re-affirm.",
    )


def gen_multi(asset_key, inst, difficulty="complex"):
    """Two simultaneous exceptions. Asset-aware pairing."""
    ac = ASSET_CFG[asset_key]["ac"]
    cp, int_rec = _split(asset_key, inst)
    fms = ["FM-01", "FM-04", "FM-07", "FM-08", "FM-10"]

    if ac in ("equities",):
        intp = int_rec["price"]; cpp = rp(intp + 0.30)
        intq = 10000; cpq = 10500
        cp["price"] = cpp; cp["quantity"] = cpq; int_rec["quantity"] = intq; int_rec["price"] = intp
        pdiff = round(abs(cpp - intp), 2); qdiff = abs(cpq - intq)
        pexp = round(pdiff * cpq, 2); qexp = round(qdiff * cpp, 2)
        primary = {"category": "EXC-QTY", "field": "quantity", "counterparty_value": cpq, "internal_value": intq,
                   "difference": qdiff, "difference_unit": "shares", "total_exposure_usd": qexp}
        secondary = {"category": "EXC-PRICE", "field": "price", "counterparty_value": cpp, "internal_value": intp,
                     "difference": pdiff, "difference_unit": "per_share", "total_exposure_usd": pexp}
        action = f"Two exceptions. (1) Quantity: {qdiff:,} shares ${qexp:,.2f}. (2) Price: ${pdiff}/share. Escalate both; do not settle."
    elif ac == "interest_rate_swap":
        intr = int_rec["fixed_rate"]; cpr = round(intr + 0.02, 4)
        cp["fixed_rate"] = cpr
        cp["counterparty"] = "Global Bank Securities LLC"; int_rec["counterparty"] = "Global Bank Capital Markets Inc"
        rdiff = round(abs(cpr - intr), 4); rexp = round(rdiff / 100 * cp["notional"], 2)
        primary = {"category": "EXC-CPTY", "field": "counterparty", "counterparty_value": cp["counterparty"],
                   "internal_value": int_rec["counterparty"], "difference": "Different legal entity (ISDA implications)",
                   "difference_unit": "entity", "total_exposure_usd": None}
        secondary = {"category": "EXC-PRICE", "field": "fixed_rate", "counterparty_value": cpr, "internal_value": intr,
                     "difference": rdiff, "difference_unit": "rate_pct", "total_exposure_usd": rexp}
        action = f"Two exceptions. (1) Legal-entity / ISDA mismatch. (2) Fixed-rate break {round(rdiff*100,1)}bp ≈ ${rexp:,.2f}/yr. Escalate to legal + rates; do not affirm."
        fms = ["FM-01", "FM-04", "FM-05", "FM-07", "FM-10"]
    elif ac == "credit":
        ints = int_rec["spread"]; cps = ints + 10
        cp["spread"] = cps
        cp["restructuring"] = "No Restructuring" if int_rec["restructuring"] != "No Restructuring" else "Modified Restructuring"
        sdiff = abs(cps - ints); sexp = round(sdiff / 10000.0 * cp["notional"], 2)
        primary = {"category": "EXC-PROD", "field": "restructuring", "counterparty_value": cp["restructuring"],
                   "internal_value": int_rec["restructuring"], "difference": "Mismatched restructuring clause",
                   "difference_unit": "term", "total_exposure_usd": None}
        secondary = {"category": "EXC-PRICE", "field": "spread", "counterparty_value": cps, "internal_value": ints,
                     "difference": sdiff, "difference_unit": "bps", "total_exposure_usd": sexp}
        action = f"Two exceptions. (1) Restructuring clause mismatch (changes protection). (2) Spread break {sdiff}bp ≈ ${sexp:,.2f}/yr. Escalate to credit desk + docs; do not affirm."
        fms = ["FM-01", "FM-04", "FM-05", "FM-07", "FM-10"]
    elif ac == "fx_forward":
        intr = int_rec["forward_rate"]; scale = 0.01 if cp["sell_currency"] in ("JPY", "KRW") else 0.0001
        cpr = round(intr + 30 * scale, 2 if cp["sell_currency"] in ("JPY", "KRW") else 4)
        cp["forward_rate"] = cpr
        intsd = int_rec["settlement_date"]; cpsd = settle(datetime.strptime(intsd, "%Y-%m-%d"), "T+2")
        cp["settlement_date"] = cpsd
        rdiff = round(abs(cpr - intr), 4); rexp = round(rdiff * cp["buy_amount"], 2)
        primary = {"category": "EXC-PRICE", "field": "forward_rate", "counterparty_value": cpr, "internal_value": intr,
                   "difference": rdiff, "difference_unit": "fx_rate", "total_exposure_usd": rexp}
        secondary = {"category": "EXC-SDATE", "field": "settlement_date", "counterparty_value": cpsd,
                     "internal_value": intsd, "difference": "2 business days", "difference_unit": "days",
                     "total_exposure_usd": None}
        action = f"Two exceptions. (1) Forward-rate break ≈ {cp['sell_currency']} {rexp:,.2f}. (2) Value-date mismatch ({intsd} vs {cpsd}). Escalate to FX desk + ops."
    else:  # options / futures / fi default: price + commission
        intp = int_rec["price"]; cpp = rp(intp + (0.30 if ac != "options" else 0.15))
        cp["price"] = cpp
        if "commission" in cp:
            intc = int_rec["commission"]; cpc = round(intc + 0.01, 3); cp["commission"] = cpc
            cdiff = round(abs(cpc - intc), 3)
        else:
            intc = cpc = 0; cdiff = 0
        pdiff = round(abs(cpp - intp), 2)
        mult = cp.get("multiplier", 1) if ac in ("options", "listed_futures") else 1
        pexp = round(pdiff * cp.get("quantity", 0) * mult, 2)
        primary = {"category": "EXC-PRICE", "field": "price", "counterparty_value": cpp, "internal_value": intp,
                   "difference": pdiff, "difference_unit": "per_unit", "total_exposure_usd": pexp}
        secondary = {"category": "EXC-COMM", "field": "commission", "counterparty_value": cpc, "internal_value": intc,
                     "difference": cdiff, "difference_unit": cp.get("commission_unit", "per_unit"),
                     "total_exposure_usd": round(cdiff * cp.get("quantity", 0), 2)}
        action = f"Two exceptions. (1) Price break ${pdiff} ≈ ${pexp:,.2f}. (2) Commission break ${cdiff}. Escalate to desk; do not settle."

    gt = {
        "exception_exists": True, "primary_exception": primary, "secondary_exception": secondary,
        "recommended_action": action, "escalation_required": True,
        "escalation_target": "Trading desk, operations supervisor",
        "human_review_required": True, "severity": 4, "confidence": "definitive",
    }
    return assemble(
        ac, "complex", 4,
        f"Dual exception on {inst['instrument']}: {primary['category']} and {secondary['category']}. Both must be identified.",
        "Two simultaneous discrepancies. Tests whether the model finds both or stops at the first.",
        cp, int_rec, gt,
        {**SC_FIELD, "both_exceptions_identified": "required",
         "numeric_values": {"method": "numeric_match", "tolerance": 0.01}},
        fms, f"Dual exception: {primary['category']} + {secondary['category']}",
    )


# ── BUILD PLAN ────────────────────────────────────────────────────────────────
# Operational-reality asset weighting for a multi-strat confirmations book.
ASSET_TARGETS = {
    "equities": 75, "fixed_income": 35, "listed_futures": 30,
    "fx_forward": 35, "interest_rate_swap": 30, "options": 25, "credit": 20,
}  # = 250 total incl. the 10 seed cases

# Exception-type menu per asset class (generator, weight). Weights bias toward
# the operationally common categories per the README severity catalog.
MENU = {
    "equities": [(gen_clean, 28), (gen_rate_break, 16), (gen_qty_break, 14), (gen_comm_break, 8),
                 (gen_date_break, 8), (gen_ssi_break, 7), (gen_cpty_break, 5), (gen_acct_break, 5),
                 (gen_dup, 3), (gen_multi, 9)],
    "fixed_income": [(gen_clean, 26), (gen_rate_break, 24), (gen_date_break, 12), (gen_ssi_break, 12),
                     (gen_qty_break, 8), (gen_cpty_break, 6), (gen_multi, 14)],
    "listed_futures": [(gen_clean, 30), (gen_qty_break, 22), (gen_rate_break, 16), (gen_comm_break, 14),
                       (gen_cpty_break, 8), (gen_multi, 12)],
    "options": [(gen_clean, 22), (gen_rate_break, 18), (gen_qty_break, 16), (gen_comm_break, 12),
                (gen_convention_break, 14), (gen_date_break, 8), (gen_multi, 14)],
    "interest_rate_swap": [(gen_clean, 18), (gen_rate_break, 20), (gen_convention_break, 18),
                           (gen_cpty_break, 14), (gen_qty_break, 10), (gen_date_break, 10), (gen_multi, 14)],
    "fx_forward": [(gen_clean, 22), (gen_rate_break, 22), (gen_ssi_break, 14), (gen_ccy_break, 14),
                   (gen_qty_break, 10), (gen_date_break, 8), (gen_multi, 14)],
    "credit": [(gen_clean, 16), (gen_rate_break, 22), (gen_convention_break, 22), (gen_cpty_break, 12),
               (gen_qty_break, 10), (gen_multi, 22)],
}

# Which generators are intrinsically which difficulty (clean=easy; multi/convention/ccy=complex).
INHERENT_DIFF = {
    gen_clean: "easy", gen_multi: "complex", gen_convention_break: "complex", gen_ccy_break: "complex",
}


def weighted_pick(menu):
    total = sum(w for _, w in menu)
    r = random.uniform(0, total)
    upto = 0
    for gen, w in menu:
        upto += w
        if r <= upto:
            return gen
    return menu[-1][0]


def build():
    cases = []
    # difficulty budget across the 240 generated cases (seed 10 are fixed)
    # Clean cases are inherently "easy" and number ~80, so give the
    # easy/moderate-flexible generators a small easy budget and route the
    # rest to moderate. Lands the full set near 100 easy / 100 moderate / 50 complex.
    diff_budget = {"easy": 100, "moderate": 150, "complex": 50}
    diff_used = Counter()

    def pick_diff(gen):
        inh = INHERENT_DIFF.get(gen)
        if inh:
            return inh
        # choose among easy/moderate to fill budget, biased by remaining headroom
        opts = []
        for d in ("easy", "moderate"):
            head = diff_budget[d] - diff_used[d]
            if head > 0:
                opts += [d] * head
        return random.choice(opts) if opts else random.choice(["easy", "moderate"])

    for asset_key, target in ASSET_TARGETS.items():
        # subtract seed cases already present for this class
        seed_for_class = SEED_ASSET_COUNTS.get(ASSET_CFG[asset_key]["ac"], 0)
        n = target - seed_for_class
        data = ASSET_CFG[asset_key]["data"]
        for _ in range(n):
            gen = weighted_pick(MENU[asset_key])
            inst = random.choice(data)
            diff = pick_diff(gen)
            try:
                case = gen(asset_key, inst, diff)
            except Exception as e:  # safety net — fall back to a clean case
                case = gen_clean(asset_key, inst)
            cases.append(case)
            diff_used[case["difficulty"]] += 1
    return cases


# ── MAIN ──────────────────────────────────────────────────────────────────────

SEED_ASSET_COUNTS = {}  # filled at runtime from representative cases


def main():
    parser = argparse.ArgumentParser(description="AAL-D-001 Case Generator v2 (scale to 250)")
    parser.add_argument("--output", default="datasets/AAL-D-001-v1.0.json")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    rep_path = os.path.join(here, "examples", "representative_cases.json")
    with open(rep_path) as f:
        seed = json.load(f)
    global SEED_ASSET_COUNTS
    SEED_ASSET_COUNTS = Counter(c["asset_class"] for c in seed)
    print(f"Seed (representative) cases: {len(seed)}  by class: {dict(SEED_ASSET_COUNTS)}")

    generated = build()
    all_cases = seed + generated
    print(f"Generated {len(generated)} new cases → total {len(all_cases)}")

    # de-dup safety on case_id
    seen, deduped = set(), []
    for c in all_cases:
        if c["case_id"] in seen:
            continue
        seen.add(c["case_id"]); deduped.append(c)
    all_cases = deduped

    # coverage report
    print("\nAsset class:", dict(Counter(c["asset_class"] for c in all_cases)))
    print("Difficulty :", dict(Counter(c["difficulty"] for c in all_cases)))
    cats = Counter()
    for c in all_cases:
        gt = c["ground_truth"]
        if gt["exception_exists"]:
            cats[gt["primary_exception"]["category"]] += 1
        else:
            cats["CLEAN"] += 1
    print("Categories :", dict(cats.most_common()))

    if args.report_only:
        print("\n--report-only: nothing written.")
        return

    out_path = os.path.join(here, args.output)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_cases, f, indent=2)
    print(f"\nWritten: {out_path}")

    if args.validate:
        print("\nValidating...")
        os.system(f"cd {here} && python3 validation/validate.py --file {args.output} --full")


if __name__ == "__main__":
    main()
