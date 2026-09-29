#!/usr/bin/env python3
"""
AI Alpha Labs — Benchmark Case Generator
AAL-D-001 · v1.0

Generates the first 50 production benchmark cases for
Trade Confirmation Exception Identification.

Distribution:
  20 Easy · 20 Moderate · 10 Complex
  ~15 Clean matches (30%)
  7 Asset classes represented
  12+ exception categories represented

Usage:
    python generate_cases.py --count 50 --output datasets/AAL-D-001-v1.0-batch1.json
    python generate_cases.py --count 50 --validate
"""

import json
import random
import argparse
from datetime import datetime, timedelta

random.seed(2026001)  # Reproducible generation

# ── Reference Data ────────────────────────────────────────────────────────────

EQUITIES = [
    {"instrument": "AAPL US Equity", "cusip": "037833100", "typical_price": 218.50},
    {"instrument": "MSFT US Equity", "cusip": "594918104", "typical_price": 445.30},
    {"instrument": "NVDA US Equity", "cusip": "67066G104", "typical_price": 131.85},
    {"instrument": "AMZN US Equity", "cusip": "023135106", "typical_price": 232.50},
    {"instrument": "GOOGL US Equity", "cusip": "02079K305", "typical_price": 178.40},
    {"instrument": "META US Equity", "cusip": "30303M102", "typical_price": 628.40},
    {"instrument": "TSLA US Equity", "cusip": "88160R101", "typical_price": 248.60},
    {"instrument": "JPM US Equity", "cusip": "46625H100", "typical_price": 265.90},
    {"instrument": "GS US Equity", "cusip": "38141G104", "typical_price": 618.25},
    {"instrument": "BRK/B US Equity", "cusip": "084670702", "typical_price": 484.50},
    {"instrument": "UNH US Equity", "cusip": "91324P102", "typical_price": 522.30},
    {"instrument": "V US Equity", "cusip": "92826C839", "typical_price": 312.40},
    {"instrument": "XOM US Equity", "cusip": "30231G102", "typical_price": 118.40},
    {"instrument": "PG US Equity", "cusip": "742718109", "typical_price": 168.75},
    {"instrument": "HD US Equity", "cusip": "437076102", "typical_price": 378.20},
]

FIXED_INCOME = [
    {"instrument": "US Treasury 4.25% 2028", "cusip": "91282CJR4", "face_typical": 5000000, "price": 98.750},
    {"instrument": "US Treasury 4.50% 2034", "cusip": "91282CKL7", "face_typical": 10000000, "price": 97.125},
    {"instrument": "US Treasury 3.875% 2030", "cusip": "91282CHT1", "face_typical": 10000000, "price": 96.250},
    {"instrument": "US Treasury 4.00% 2026", "cusip": "91282CHP9", "face_typical": 25000000, "price": 99.875},
    {"instrument": "Corporate Bond Alpha 5.00% 2031", "cusip": "38141GXZ2", "face_typical": 2000000, "price": 101.250},
    {"instrument": "Corporate Bond Beta 4.75% 2029", "cusip": "46625HYA3", "face_typical": 3000000, "price": 99.500},
    {"instrument": "Corporate Bond Gamma 5.25% 2033", "cusip": "594918YB1", "face_typical": 5000000, "price": 102.375},
]

FUTURES = [
    {"instrument": "ES Sep26", "exchange": "CME", "multiplier": 50, "price": 5842.25},
    {"instrument": "NQ Sep26", "exchange": "CME", "multiplier": 20, "price": 21250.50},
    {"instrument": "YM Sep26", "exchange": "CBOT", "multiplier": 5, "price": 42180.00},
    {"instrument": "CL Aug26", "exchange": "NYMEX", "multiplier": 1000, "price": 78.45},
    {"instrument": "GC Aug26", "exchange": "COMEX", "multiplier": 100, "price": 2385.60},
    {"instrument": "ZN Sep26", "exchange": "CBOT", "multiplier": 1000, "price": 108.156},
]

OPTIONS = [
    {"instrument": "AAPL Jul26 220 Call", "underlying": "AAPL US Equity", "strike": 220.00, "expiry": "2026-07-18", "type": "Call", "price": 5.20, "multiplier": 100},
    {"instrument": "NVDA Jul26 140 Call", "underlying": "NVDA US Equity", "strike": 140.00, "expiry": "2026-07-18", "type": "Call", "price": 4.85, "multiplier": 100},
    {"instrument": "SPY Jul26 590 Put", "underlying": "SPY US Equity", "strike": 590.00, "expiry": "2026-07-18", "type": "Put", "price": 8.40, "multiplier": 100},
    {"instrument": "TSLA Aug26 260 Call", "underlying": "TSLA US Equity", "strike": 260.00, "expiry": "2026-08-15", "type": "Call", "price": 12.30, "multiplier": 100},
    {"instrument": "QQQ Jul26 500 Put", "underlying": "QQQ US Equity", "strike": 500.00, "expiry": "2026-07-18", "type": "Put", "price": 6.15, "multiplier": 100},
]

IRS = [
    {"instrument": "USD SOFR IRS 5Y", "notional": 50000000, "fixed_rate": 4.125, "tenor": "5Y"},
    {"instrument": "USD SOFR IRS 10Y", "notional": 100000000, "fixed_rate": 4.350, "tenor": "10Y"},
    {"instrument": "USD SOFR IRS 2Y", "notional": 25000000, "fixed_rate": 3.875, "tenor": "2Y"},
    {"instrument": "EUR EURIBOR IRS 5Y", "notional": 30000000, "fixed_rate": 2.950, "tenor": "5Y", "currency": "EUR"},
]

FX = [
    {"instrument": "EUR/USD FWD 1M", "buy_ccy": "EUR", "sell_ccy": "USD", "buy_amt": 10000000, "rate": 1.0850},
    {"instrument": "GBP/USD FWD 3M", "buy_ccy": "GBP", "sell_ccy": "USD", "buy_amt": 5000000, "rate": 1.2720},
    {"instrument": "USD/JPY FWD 1M", "buy_ccy": "USD", "sell_ccy": "JPY", "buy_amt": 20000000, "rate": 157.50},
    {"instrument": "USD/CHF FWD 2M", "buy_ccy": "USD", "sell_ccy": "CHF", "buy_amt": 8000000, "rate": 0.8840},
]

CREDIT = [
    {"instrument": "CDS 5Y", "ref_entity": "Investment Grade Corp A", "notional": 10000000, "spread": 75},
    {"instrument": "CDS 5Y", "ref_entity": "Investment Grade Corp B", "notional": 5000000, "spread": 120},
    {"instrument": "CDS 3Y", "ref_entity": "High Yield Corp C", "notional": 3000000, "spread": 350},
]

COUNTERPARTIES = ["Prime Broker Alpha", "Prime Broker Beta", "Prime Broker Gamma", "Prime Broker Delta"]
CLEARING_BROKERS = ["Clearing Broker Alpha", "Clearing Broker Beta", "Clearing Broker Gamma"]
EXEC_BROKERS = ["Execution Broker One", "Execution Broker Two", "Execution Broker Three", "Execution Broker Four"]
SIDES = ["Buy", "Sell"]
ACCOUNTS_EQ = ["FUND-A-EQUITY", "FUND-B-EQUITY", "FUND-C-EQUITY"]
ACCOUNTS_FI = ["FUND-A-FIXED-INCOME", "FUND-B-FIXED-INCOME"]
ACCOUNTS_DRV = ["FUND-A-DERIVATIVES", "FUND-B-DERIVATIVES"]
ACCOUNTS_FX = ["FUND-A-FX", "FUND-B-FX"]

SSI_DTC = [
    "DTC 0001 / Account 44821",
    "DTC 0002 / Account 77103",
    "DTC 0003 / Account 55290",
    "DTC 0001 / Account 88412",
]
SSI_FED = [
    "Fed Wire / ABA 021000089",
    "Fed Wire / ABA 021000018",
]
SSI_EUR = [
    "Correspondent Bank X / SWIFT: COBADEFF / Account: DE89370400440532013000",
    "Correspondent Bank Y / SWIFT: DEUTDEFF / Account: DE27100777770209299700",
]
SSI_USD_NOSTRO = [
    "JPMorgan / ABA 021000021 / Account 88412",
    "Citibank / ABA 021000089 / Account 99201",
]

BASE_DATE = datetime(2026, 7, 1)

# ── Case Definitions ─────────────────────────────────────────────────────────

def make_trade_id(asset_prefix, seq):
    return f"{asset_prefix}-20260701-{seq:04d}"

def settlement_date(td, convention="T+1"):
    days = int(convention.replace("T+", ""))
    sd = td + timedelta(days=days)
    while sd.weekday() >= 5:
        sd += timedelta(days=1)
    return sd.strftime("%Y-%m-%d")

def round_price(p, tick=0.01):
    return round(p, 2)


# ── CASE GENERATORS ──────────────────────────────────────────────────────────

CASES = []
SEQ = 10  # Start after the 10 representative cases

def next_id():
    global SEQ
    SEQ += 1
    return f"AAL-D-001-{SEQ:03d}"


# ── CLEAN MATCHES (15 cases) ─────────────────────────────────────────────────

def gen_clean_equity(eq):
    cid = next_id()
    qty = random.choice([1000, 2000, 3000, 5000, 8000, 10000, 15000])
    side = random.choice(SIDES)
    cp = random.choice(COUNTERPARTIES)
    ssi = random.choice(SSI_DTC)
    broker = random.choice(EXEC_BROKERS)
    comm = round(random.choice([0.01, 0.015, 0.02]), 3)
    acct = random.choice(ACCOUNTS_EQ)
    price = round_price(eq["typical_price"] + random.uniform(-2, 2))
    tid = make_trade_id("EQ", SEQ)
    sd = settlement_date(BASE_DATE)
    
    shared = {
        "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
        "instrument": eq["instrument"], "cusip": eq["cusip"],
        "counterparty": cp, "side": side, "quantity": qty,
        "price": price, "currency": "USD",
        "settlement_instructions": ssi, "broker": broker, "commission": comm
    }
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "equities", "difficulty": "easy", "risk_level": 1,
        "scenario_description": f"Clean equity confirmation match — {eq['instrument']}. All fields match.",
        "business_context": f"Standard US equity {side.lower()}. T+1 settlement. Routine end-of-day confirmation.",
        "input": {
            "counterparty_confirmation": {**shared},
            "internal_record": {**shared, "account": acct, "book": "US-EQUITY-LONG", "status": "Unconfirmed"}
        },
        "ground_truth": {
            "exception_exists": False, "primary_exception": None, "secondary_exception": None,
            "recommended_action": "No action required. Confirmation matched.",
            "escalation_required": False, "escalation_target": None,
            "human_review_required": False, "severity": 1, "confidence": "definitive"
        },
        "scoring_criteria": {"exception_detection": "exact_match", "fabrication_check": "binary"},
        "failure_modes": ["FM-02"],
        "reviewer_notes": "Clean match. Tests false positive resistance.",
        "references": [], "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

def gen_clean_fi(fi):
    cid = next_id()
    cp = random.choice(COUNTERPARTIES)
    ssi = random.choice(SSI_FED)
    acct = random.choice(ACCOUNTS_FI)
    tid = make_trade_id("FI", SEQ)
    side = random.choice(SIDES)
    sd = settlement_date(BASE_DATE)
    
    shared = {
        "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
        "instrument": fi["instrument"], "cusip": fi["cusip"],
        "counterparty": cp, "side": side,
        "face_value": fi["face_typical"], "price": fi["price"],
        "currency": "USD", "settlement_instructions": ssi, "broker": "Direct"
    }
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "fixed_income", "difficulty": "easy", "risk_level": 1,
        "scenario_description": f"Clean fixed income confirmation — {fi['instrument']}. All fields match.",
        "business_context": f"Institutional {fi['instrument'].split()[0]} {fi['instrument'].split()[1]} {side.lower()}. Standard settlement.",
        "input": {
            "counterparty_confirmation": {**shared},
            "internal_record": {**shared, "account": acct, "book": "FIXED-INCOME", "status": "Unconfirmed"}
        },
        "ground_truth": {
            "exception_exists": False, "primary_exception": None, "secondary_exception": None,
            "recommended_action": "No action required. Confirmation matched.",
            "escalation_required": False, "escalation_target": None,
            "human_review_required": False, "severity": 1, "confidence": "definitive"
        },
        "scoring_criteria": {"exception_detection": "exact_match", "fabrication_check": "binary"},
        "failure_modes": ["FM-02"],
        "reviewer_notes": "Clean match — fixed income. Tests false positive resistance on bond confirmations.",
        "references": [], "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

def gen_clean_futures(fut):
    cid = next_id()
    cp = random.choice(CLEARING_BROKERS)
    qty = random.choice([10, 20, 25, 50, 100])
    side = random.choice(SIDES)
    tid = make_trade_id("FUT", SEQ)
    
    shared = {
        "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": "2026-07-01",
        "instrument": fut["instrument"], "exchange": fut["exchange"],
        "counterparty": cp, "side": side, "quantity": qty,
        "price": fut["price"], "currency": "USD",
        "multiplier": fut["multiplier"], "commission": 1.25
    }
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "listed_futures", "difficulty": "easy", "risk_level": 1,
        "scenario_description": f"Clean futures confirmation — {fut['instrument']}. All fields match.",
        "business_context": f"{fut['exchange']} {fut['instrument']} — standard cleared futures trade.",
        "input": {
            "counterparty_confirmation": {**shared},
            "internal_record": {**shared, "account": "FUND-A-FUTURES", "book": "FUTURES", "status": "Unconfirmed"}
        },
        "ground_truth": {
            "exception_exists": False, "primary_exception": None, "secondary_exception": None,
            "recommended_action": "No action required. Confirmation matched.",
            "escalation_required": False, "escalation_target": None,
            "human_review_required": False, "severity": 1, "confidence": "definitive"
        },
        "scoring_criteria": {"exception_detection": "exact_match", "fabrication_check": "binary"},
        "failure_modes": ["FM-02"],
        "reviewer_notes": "Clean futures match.",
        "references": [], "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

# ── PRICE BREAKS (7 cases) ───────────────────────────────────────────────────

def gen_price_break_equity(eq, difficulty="easy"):
    cid = next_id()
    qty = random.choice([2000, 3000, 5000, 8000, 10000])
    side = random.choice(SIDES)
    cp = random.choice(COUNTERPARTIES)
    ssi = random.choice(SSI_DTC)
    broker = random.choice(EXEC_BROKERS)
    acct = random.choice(ACCOUNTS_EQ)
    tid = make_trade_id("EQ", SEQ)
    sd = settlement_date(BASE_DATE)
    
    if difficulty == "easy":
        delta = round(random.choice([0.25, 0.50, 0.75, 1.00, 1.25]), 2)
    else:
        delta = round(random.choice([0.03, 0.05, 0.08, 0.10]), 2)
    
    int_price = round_price(eq["typical_price"] + random.uniform(-1, 1))
    cp_price = round_price(int_price + delta * random.choice([1, -1]))
    exposure = round(abs(cp_price - int_price) * qty, 2)
    diff = round(abs(cp_price - int_price), 2)
    rl = 3 if exposure > 1000 else 2
    
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "equities", "difficulty": difficulty,
        "risk_level": rl,
        "scenario_description": f"Equity price break on {eq['instrument']}. {'Subtle' if difficulty == 'moderate' else 'Clear'} price difference of ${diff}/share.",
        "business_context": f"US equity {side.lower()}. End-of-day confirmation matching. {'Small price variance — tests attention to detail.' if difficulty == 'moderate' else 'Standard price discrepancy.'}",
        "input": {
            "counterparty_confirmation": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": qty,
                "price": cp_price, "currency": "USD",
                "settlement_instructions": ssi, "broker": broker, "commission": 0.01
            },
            "internal_record": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": qty,
                "price": int_price, "currency": "USD",
                "settlement_instructions": ssi, "broker": broker, "commission": 0.01,
                "account": acct, "book": "US-EQUITY-LONG", "status": "Unconfirmed"
            }
        },
        "ground_truth": {
            "exception_exists": True,
            "primary_exception": {
                "category": "EXC-PRICE", "field": "price",
                "counterparty_value": cp_price, "internal_value": int_price,
                "difference": diff, "difference_unit": "per_share",
                "total_exposure_usd": exposure
            },
            "secondary_exception": None,
            "recommended_action": f"Escalate price break to trading desk. ${diff}/share difference on {qty:,} shares = ${exposure:,.2f} exposure. Do not settle until resolved.",
            "escalation_required": True, "escalation_target": "Trading desk",
            "human_review_required": True, "severity": rl, "confidence": "definitive"
        },
        "scoring_criteria": {
            "exception_detection": "exact_match", "category_identification": "exact_match",
            "field_identification": "exact_match",
            "numeric_values": {"method": "numeric_match", "tolerance": 0.01},
            "exposure_calculation": {"method": "numeric_match", "tolerance": 1.00},
            "action_recommendation": "semantic_match", "fabrication_check": "binary"
        },
        "failure_modes": ["FM-01", "FM-08"] if difficulty == "easy" else ["FM-01", "FM-04", "FM-08"],
        "reviewer_notes": f"Price break: CP={cp_price}, INT={int_price}, diff={diff}, exposure=${exposure:,.2f}",
        "references": ["US equity T+1 settlement"],
        "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

def gen_price_break_fi(fi, difficulty="easy"):
    cid = next_id()
    side = random.choice(SIDES)
    cp = random.choice(COUNTERPARTIES)
    ssi = random.choice(SSI_FED)
    acct = random.choice(ACCOUNTS_FI)
    tid = make_trade_id("FI", SEQ)
    sd = settlement_date(BASE_DATE)
    fv = fi["face_typical"]
    
    if difficulty == "easy":
        delta = round(random.choice([0.125, 0.250, 0.500]), 3)
    else:
        delta = round(random.choice([0.015625, 0.03125, 0.0625]), 6)
    
    int_price = fi["price"]
    cp_price = round(int_price + delta * random.choice([1, -1]), 6)
    exposure = round(abs(cp_price - int_price) / 100 * fv, 2)
    diff = round(abs(cp_price - int_price), 6)
    rl = 4 if exposure > 50000 else 3
    
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "fixed_income", "difficulty": difficulty,
        "risk_level": rl,
        "scenario_description": f"Fixed income price break on {fi['instrument']}. Price differs by {diff} points on ${fv:,} face.",
        "business_context": f"Institutional fixed income {side.lower()}. {fi['instrument']}.",
        "input": {
            "counterparty_confirmation": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": fi["instrument"], "cusip": fi["cusip"],
                "counterparty": cp, "side": side,
                "face_value": fv, "price": cp_price,
                "currency": "USD", "settlement_instructions": ssi, "broker": "Direct"
            },
            "internal_record": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": fi["instrument"], "cusip": fi["cusip"],
                "counterparty": cp, "side": side,
                "face_value": fv, "price": int_price,
                "currency": "USD", "settlement_instructions": ssi, "broker": "Direct",
                "account": acct, "book": "FIXED-INCOME", "status": "Unconfirmed"
            }
        },
        "ground_truth": {
            "exception_exists": True,
            "primary_exception": {
                "category": "EXC-PRICE", "field": "price",
                "counterparty_value": cp_price, "internal_value": int_price,
                "difference": diff, "difference_unit": "points",
                "total_exposure_usd": exposure
            },
            "secondary_exception": None,
            "recommended_action": f"Escalate price break to trading desk. {diff} point difference on ${fv:,} face = ${exposure:,.2f} exposure.",
            "escalation_required": True, "escalation_target": "Trading desk",
            "human_review_required": True, "severity": rl, "confidence": "definitive"
        },
        "scoring_criteria": {
            "exception_detection": "exact_match", "category_identification": "exact_match",
            "field_identification": "exact_match",
            "numeric_values": {"method": "numeric_match", "tolerance": 0.01},
            "exposure_calculation": {"method": "numeric_match", "tolerance": 100.00},
            "action_recommendation": "semantic_match", "fabrication_check": "binary"
        },
        "failure_modes": ["FM-01", "FM-04", "FM-08"],
        "reviewer_notes": f"FI price break: {diff} pts on ${fv:,} face = ${exposure:,.2f}",
        "references": ["US Treasury settlement conventions"],
        "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

# ── QUANTITY BREAKS (4 cases) ─────────────────────────────────────────────────

def gen_qty_break_equity(eq, difficulty="easy"):
    cid = next_id()
    side = random.choice(SIDES)
    cp = random.choice(COUNTERPARTIES)
    ssi = random.choice(SSI_DTC)
    broker = random.choice(EXEC_BROKERS)
    acct = random.choice(ACCOUNTS_EQ)
    tid = make_trade_id("EQ", SEQ)
    sd = settlement_date(BASE_DATE)
    price = round_price(eq["typical_price"] + random.uniform(-1, 1))
    
    int_qty = random.choice([3000, 5000, 8000, 10000, 15000])
    if difficulty == "moderate":
        cp_qty = int_qty + random.choice([-100, 100, -500, 500])
    else:
        cp_qty = int_qty + random.choice([-1000, 1000, -2000, 2000, -5000, 5000])
    
    diff = abs(cp_qty - int_qty)
    exposure = round(diff * price, 2)
    
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "equities", "difficulty": difficulty,
        "risk_level": 4 if exposure > 100000 else 3,
        "scenario_description": f"Equity quantity break on {eq['instrument']}. Counterparty shows {cp_qty:,}, internal shows {int_qty:,}.",
        "business_context": f"US equity {side.lower()}. Quantity mismatch creates settlement risk.",
        "input": {
            "counterparty_confirmation": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": cp_qty,
                "price": price, "currency": "USD",
                "settlement_instructions": ssi, "broker": broker, "commission": 0.01
            },
            "internal_record": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": int_qty,
                "price": price, "currency": "USD",
                "settlement_instructions": ssi, "broker": broker, "commission": 0.01,
                "account": acct, "book": "US-EQUITY-LONG", "status": "Unconfirmed"
            }
        },
        "ground_truth": {
            "exception_exists": True,
            "primary_exception": {
                "category": "EXC-QTY", "field": "quantity",
                "counterparty_value": cp_qty, "internal_value": int_qty,
                "difference": diff, "difference_unit": "shares",
                "total_exposure_usd": exposure
            },
            "secondary_exception": None,
            "recommended_action": f"Escalate quantity break to trading desk immediately. {diff:,} share difference = ${exposure:,.2f} exposure. Do not settle until resolved.",
            "escalation_required": True, "escalation_target": "Trading desk",
            "human_review_required": True,
            "severity": 4 if exposure > 100000 else 3,
            "confidence": "definitive"
        },
        "scoring_criteria": {
            "exception_detection": "exact_match", "category_identification": "exact_match",
            "field_identification": "exact_match",
            "numeric_values": {"method": "numeric_match", "tolerance": 0},
            "exposure_calculation": {"method": "numeric_match", "tolerance": 1.00},
            "action_recommendation": "semantic_match", "fabrication_check": "binary"
        },
        "failure_modes": ["FM-01", "FM-04", "FM-08", "FM-12"],
        "reviewer_notes": f"Qty break: CP={cp_qty:,}, INT={int_qty:,}, diff={diff:,}, exposure=${exposure:,.2f}",
        "references": ["US equity T+1 settlement"],
        "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

# ── SETTLEMENT DATE BREAKS (3 cases) ─────────────────────────────────────────

def gen_sdate_break(eq_or_fi, asset_class="equities", difficulty="moderate"):
    cid = next_id()
    side = random.choice(SIDES)
    cp = random.choice(COUNTERPARTIES)
    tid = make_trade_id("EQ" if asset_class == "equities" else "FI", SEQ)
    
    int_sd = settlement_date(BASE_DATE, "T+1")
    if difficulty == "moderate":
        cp_sd = settlement_date(BASE_DATE, "T+2")
        diff_desc = "1 business day"
    else:
        cp_sd = settlement_date(BASE_DATE + timedelta(days=5), "T+1")
        diff_desc = "multiple business days"
    
    if asset_class == "equities":
        price = round_price(eq_or_fi["typical_price"])
        qty = random.choice([3000, 5000, 10000])
        input_shared = {
            "trade_id": tid, "trade_date": "2026-07-01",
            "instrument": eq_or_fi["instrument"], "cusip": eq_or_fi["cusip"],
            "counterparty": cp, "side": side, "quantity": qty,
            "price": price, "currency": "USD",
            "settlement_instructions": random.choice(SSI_DTC),
            "broker": random.choice(EXEC_BROKERS), "commission": 0.01
        }
        acct = random.choice(ACCOUNTS_EQ)
        book = "US-EQUITY-LONG"
    else:
        price = eq_or_fi["price"]
        fv = eq_or_fi["face_typical"]
        input_shared = {
            "trade_id": tid, "trade_date": "2026-07-01",
            "instrument": eq_or_fi["instrument"], "cusip": eq_or_fi["cusip"],
            "counterparty": cp, "side": side, "face_value": fv,
            "price": price, "currency": "USD",
            "settlement_instructions": random.choice(SSI_FED), "broker": "Direct"
        }
        acct = random.choice(ACCOUNTS_FI)
        book = "FIXED-INCOME"
    
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": asset_class, "difficulty": difficulty,
        "risk_level": 3 if difficulty == "moderate" else 4,
        "scenario_description": f"Settlement date mismatch on {eq_or_fi['instrument']}. Counterparty shows {cp_sd}, internal shows {int_sd}.",
        "business_context": f"Settlement convention discrepancy. {diff_desc} difference.",
        "input": {
            "counterparty_confirmation": {**input_shared, "settlement_date": cp_sd},
            "internal_record": {**input_shared, "settlement_date": int_sd, "account": acct, "book": book, "status": "Unconfirmed"}
        },
        "ground_truth": {
            "exception_exists": True,
            "primary_exception": {
                "category": "EXC-SDATE", "field": "settlement_date",
                "counterparty_value": cp_sd, "internal_value": int_sd,
                "difference": diff_desc, "difference_unit": "days",
                "total_exposure_usd": None
            },
            "secondary_exception": None,
            "recommended_action": f"Settlement date break — confirm correct settlement convention with counterparty. CP shows {cp_sd}, internal shows {int_sd}.",
            "escalation_required": True, "escalation_target": "Operations / counterparty",
            "human_review_required": True,
            "severity": 3 if difficulty == "moderate" else 4,
            "confidence": "definitive"
        },
        "scoring_criteria": {
            "exception_detection": "exact_match", "category_identification": "exact_match",
            "field_identification": "exact_match", "action_recommendation": "semantic_match",
            "fabrication_check": "binary"
        },
        "failure_modes": ["FM-06", "FM-07"],
        "reviewer_notes": f"Settlement date break: CP={cp_sd}, INT={int_sd}",
        "references": ["Settlement convention T+1/T+2"],
        "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

# ── COUNTERPARTY MISMATCH (2 cases) ──────────────────────────────────────────

def gen_cpty_mismatch(difficulty="moderate"):
    cid = next_id()
    eq = random.choice(EQUITIES)
    qty = random.choice([5000, 10000])
    price = round_price(eq["typical_price"])
    side = random.choice(SIDES)
    tid = make_trade_id("EQ", SEQ)
    sd = settlement_date(BASE_DATE)
    
    if difficulty == "moderate":
        cp_cp = "Prime Broker Alpha"
        int_cp = "Prime Broker Beta"
    else:
        cp_cp = "Global Bank Securities LLC"
        int_cp = "Global Bank Capital Markets Inc"
    
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "equities", "difficulty": difficulty,
        "risk_level": 4 if difficulty == "complex" else 3,
        "scenario_description": f"Counterparty mismatch. Confirmation shows {cp_cp}, internal shows {int_cp}.",
        "business_context": f"{'Different legal entities within same group — ISDA implications.' if difficulty == 'complex' else 'Different counterparty entirely.'}",
        "input": {
            "counterparty_confirmation": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp_cp, "side": side, "quantity": qty,
                "price": price, "currency": "USD",
                "settlement_instructions": random.choice(SSI_DTC),
                "broker": random.choice(EXEC_BROKERS), "commission": 0.01
            },
            "internal_record": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": int_cp, "side": side, "quantity": qty,
                "price": price, "currency": "USD",
                "settlement_instructions": random.choice(SSI_DTC),
                "broker": random.choice(EXEC_BROKERS), "commission": 0.01,
                "account": random.choice(ACCOUNTS_EQ), "book": "US-EQUITY-LONG", "status": "Unconfirmed"
            }
        },
        "ground_truth": {
            "exception_exists": True,
            "primary_exception": {
                "category": "EXC-CPTY", "field": "counterparty",
                "counterparty_value": cp_cp, "internal_value": int_cp,
                "difference": "Different counterparty" if difficulty == "moderate" else "Different legal entity",
                "difference_unit": "entity",
                "total_exposure_usd": None
            },
            "secondary_exception": None,
            "recommended_action": f"Counterparty mismatch. Confirmation shows {cp_cp}, internal shows {int_cp}. Escalate immediately. Do not settle.",
            "escalation_required": True,
            "escalation_target": "Operations supervisor" if difficulty == "moderate" else "Legal, documentation, operations",
            "human_review_required": True,
            "severity": 4 if difficulty == "complex" else 3,
            "confidence": "definitive" if difficulty == "moderate" else "expert_consensus"
        },
        "scoring_criteria": {
            "exception_detection": "exact_match", "category_identification": "exact_match",
            "field_identification": "exact_match", "action_recommendation": "semantic_match",
            "fabrication_check": "binary"
        },
        "failure_modes": ["FM-01", "FM-05", "FM-07"],
        "reviewer_notes": f"Counterparty mismatch: {cp_cp} vs {int_cp}",
        "references": [],
        "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

# ── COMMISSION BREAKS (3 cases) ───────────────────────────────────────────────

def gen_comm_break(asset_class="equities"):
    cid = next_id()
    eq = random.choice(EQUITIES)
    qty = random.choice([2000, 5000, 8000, 10000])
    price = round_price(eq["typical_price"])
    side = random.choice(SIDES)
    cp = random.choice(COUNTERPARTIES)
    tid = make_trade_id("EQ", SEQ)
    sd = settlement_date(BASE_DATE)
    
    int_comm = round(random.choice([0.01, 0.015, 0.02]), 3)
    cp_comm = round(int_comm + random.choice([0.005, 0.01, 0.015]), 3)
    diff = round(abs(cp_comm - int_comm), 3)
    exposure = round(diff * qty, 2)
    
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "equities", "difficulty": "moderate",
        "risk_level": 2,
        "scenario_description": f"Commission discrepancy on {eq['instrument']}. CP shows ${cp_comm}/share, internal shows ${int_comm}/share.",
        "business_context": f"All economic fields match. Commission rate differs. Tests attention to non-price fields.",
        "input": {
            "counterparty_confirmation": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": qty,
                "price": price, "currency": "USD",
                "settlement_instructions": random.choice(SSI_DTC),
                "broker": random.choice(EXEC_BROKERS), "commission": cp_comm
            },
            "internal_record": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": qty,
                "price": price, "currency": "USD",
                "settlement_instructions": random.choice(SSI_DTC),
                "broker": random.choice(EXEC_BROKERS), "commission": int_comm,
                "account": random.choice(ACCOUNTS_EQ), "book": "US-EQUITY-LONG", "status": "Unconfirmed"
            }
        },
        "ground_truth": {
            "exception_exists": True,
            "primary_exception": {
                "category": "EXC-COMM", "field": "commission",
                "counterparty_value": cp_comm, "internal_value": int_comm,
                "difference": diff, "difference_unit": "per_share",
                "total_exposure_usd": exposure
            },
            "secondary_exception": None,
            "recommended_action": f"Commission discrepancy. Verify agreed rate with broker. Difference: ${diff}/share × {qty:,} = ${exposure:,.2f}.",
            "escalation_required": False, "escalation_target": None,
            "human_review_required": True, "severity": 2, "confidence": "definitive"
        },
        "scoring_criteria": {
            "exception_detection": "exact_match", "category_identification": "exact_match",
            "field_identification": "exact_match",
            "numeric_values": {"method": "numeric_match", "tolerance": 0.001},
            "action_recommendation": "semantic_match", "fabrication_check": "binary"
        },
        "failure_modes": ["FM-01", "FM-07"],
        "reviewer_notes": f"Commission break: {cp_comm} vs {int_comm}, diff={diff}, exposure=${exposure:,.2f}",
        "references": [],
        "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

# ── SSI MISMATCH (2 cases) ───────────────────────────────────────────────────

def gen_ssi_break():
    cid = next_id()
    eq = random.choice(EQUITIES)
    qty = random.choice([5000, 10000, 15000])
    price = round_price(eq["typical_price"])
    side = random.choice(SIDES)
    cp = random.choice(COUNTERPARTIES)
    tid = make_trade_id("EQ", SEQ)
    sd = settlement_date(BASE_DATE)
    cp_ssi = SSI_DTC[0]
    int_ssi = SSI_DTC[2]
    exposure = round(qty * price, 2)
    
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "equities", "difficulty": "moderate",
        "risk_level": 4,
        "scenario_description": f"SSI mismatch on {eq['instrument']}. All economic fields match but settlement instructions differ.",
        "business_context": f"SSI discrepancy — incorrect settlement instructions lead to failed delivery. Full trade value at risk.",
        "input": {
            "counterparty_confirmation": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": qty,
                "price": price, "currency": "USD",
                "settlement_instructions": cp_ssi,
                "broker": random.choice(EXEC_BROKERS), "commission": 0.01
            },
            "internal_record": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": qty,
                "price": price, "currency": "USD",
                "settlement_instructions": int_ssi,
                "broker": random.choice(EXEC_BROKERS), "commission": 0.01,
                "account": random.choice(ACCOUNTS_EQ), "book": "US-EQUITY-LONG", "status": "Unconfirmed"
            }
        },
        "ground_truth": {
            "exception_exists": True,
            "primary_exception": {
                "category": "EXC-SSI", "field": "settlement_instructions",
                "counterparty_value": cp_ssi, "internal_value": int_ssi,
                "difference": "Different DTC participant and account",
                "difference_unit": "settlement_instruction",
                "total_exposure_usd": exposure
            },
            "secondary_exception": None,
            "recommended_action": f"SSI mismatch — escalate immediately. Incorrect settlement instructions will cause failed delivery. Full exposure: ${exposure:,.2f}. Verify correct SSI before settlement.",
            "escalation_required": True, "escalation_target": "Operations, treasury",
            "human_review_required": True, "severity": 4, "confidence": "definitive"
        },
        "scoring_criteria": {
            "exception_detection": "exact_match", "category_identification": "exact_match",
            "field_identification": "exact_match", "action_recommendation": "semantic_match",
            "fabrication_check": "binary"
        },
        "failure_modes": ["FM-01", "FM-03", "FM-12"],
        "reviewer_notes": f"SSI break — full notional at risk: ${exposure:,.2f}",
        "references": ["DTC settlement instructions"],
        "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

# ── TRADE DATE BREAK (2 cases) ───────────────────────────────────────────────

def gen_tdate_break():
    cid = next_id()
    eq = random.choice(EQUITIES)
    qty = random.choice([3000, 5000, 10000])
    price = round_price(eq["typical_price"])
    side = random.choice(SIDES)
    cp = random.choice(COUNTERPARTIES)
    tid = make_trade_id("EQ", SEQ)
    
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "equities", "difficulty": "easy",
        "risk_level": 2,
        "scenario_description": f"Trade date mismatch on {eq['instrument']}. CP shows 2026-06-30, internal shows 2026-07-01.",
        "business_context": "Trade date discrepancy. May indicate booking on wrong date.",
        "input": {
            "counterparty_confirmation": {
                "trade_id": tid, "trade_date": "2026-06-30", "settlement_date": "2026-07-02",
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": qty,
                "price": price, "currency": "USD",
                "settlement_instructions": random.choice(SSI_DTC),
                "broker": random.choice(EXEC_BROKERS), "commission": 0.01
            },
            "internal_record": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": "2026-07-02",
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": qty,
                "price": price, "currency": "USD",
                "settlement_instructions": random.choice(SSI_DTC),
                "broker": random.choice(EXEC_BROKERS), "commission": 0.01,
                "account": random.choice(ACCOUNTS_EQ), "book": "US-EQUITY-LONG", "status": "Unconfirmed"
            }
        },
        "ground_truth": {
            "exception_exists": True,
            "primary_exception": {
                "category": "EXC-TDATE", "field": "trade_date",
                "counterparty_value": "2026-06-30", "internal_value": "2026-07-01",
                "difference": "1 business day", "difference_unit": "days",
                "total_exposure_usd": None
            },
            "secondary_exception": None,
            "recommended_action": "Trade date mismatch. Verify correct trade date with trading desk.",
            "escalation_required": True, "escalation_target": "Trading desk",
            "human_review_required": True, "severity": 2, "confidence": "definitive"
        },
        "scoring_criteria": {
            "exception_detection": "exact_match", "category_identification": "exact_match",
            "field_identification": "exact_match", "action_recommendation": "semantic_match",
            "fabrication_check": "binary"
        },
        "failure_modes": ["FM-06", "FM-07"],
        "reviewer_notes": "Trade date break: 2026-06-30 vs 2026-07-01",
        "references": [],
        "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

# ── COMPLEX: MULTI-EXCEPTION (3 cases) ───────────────────────────────────────

def gen_multi_exception_equity():
    cid = next_id()
    eq = random.choice(EQUITIES[:5])
    side = random.choice(SIDES)
    tid = make_trade_id("EQ", SEQ)
    sd = settlement_date(BASE_DATE)
    int_price = round_price(eq["typical_price"])
    cp_price = round_price(int_price + 0.30)
    int_qty = 10000
    cp_qty = 10500
    qty_diff = abs(cp_qty - int_qty)
    price_diff = round(abs(cp_price - int_price), 2)
    price_exposure = round(price_diff * cp_qty, 2)
    qty_exposure = round(qty_diff * cp_price, 2)
    
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "equities", "difficulty": "complex",
        "risk_level": 4,
        "scenario_description": f"Dual exception on {eq['instrument']}: price break AND quantity break. Both must be identified.",
        "business_context": "Complex confirmation with two simultaneous discrepancies. Tests whether model identifies both exceptions or stops at the first.",
        "input": {
            "counterparty_confirmation": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": "Prime Broker Alpha", "side": side, "quantity": cp_qty,
                "price": cp_price, "currency": "USD",
                "settlement_instructions": "DTC 0001 / Account 44821",
                "broker": "Execution Broker One", "commission": 0.01
            },
            "internal_record": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": "Prime Broker Alpha", "side": side, "quantity": int_qty,
                "price": int_price, "currency": "USD",
                "settlement_instructions": "DTC 0001 / Account 44821",
                "broker": "Execution Broker One", "commission": 0.01,
                "account": "FUND-A-EQUITY", "book": "US-EQUITY-LONG", "status": "Unconfirmed"
            }
        },
        "ground_truth": {
            "exception_exists": True,
            "primary_exception": {
                "category": "EXC-QTY", "field": "quantity",
                "counterparty_value": cp_qty, "internal_value": int_qty,
                "difference": qty_diff, "difference_unit": "shares",
                "total_exposure_usd": qty_exposure
            },
            "secondary_exception": {
                "category": "EXC-PRICE", "field": "price",
                "counterparty_value": cp_price, "internal_value": int_price,
                "difference": price_diff, "difference_unit": "per_share",
                "total_exposure_usd": price_exposure
            },
            "recommended_action": f"Two exceptions identified. (1) Quantity break: {qty_diff:,} shares, ${qty_exposure:,.2f} exposure. (2) Price break: ${price_diff}/share. Escalate both to trading desk. Do not settle.",
            "escalation_required": True, "escalation_target": "Trading desk, operations supervisor",
            "human_review_required": True, "severity": 4, "confidence": "definitive"
        },
        "scoring_criteria": {
            "exception_detection": "exact_match", "category_identification": "exact_match",
            "both_exceptions_identified": "required",
            "action_recommendation": "semantic_match", "fabrication_check": "binary"
        },
        "failure_modes": ["FM-01", "FM-04", "FM-07", "FM-08", "FM-10"],
        "reviewer_notes": f"Dual exception. Must identify BOTH qty ({qty_diff} shares) and price (${price_diff}) breaks.",
        "references": [],
        "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }

# ── ACCOUNT MISMATCH (2 cases) ───────────────────────────────────────────────

def gen_acct_mismatch():
    cid = next_id()
    eq = random.choice(EQUITIES)
    qty = random.choice([5000, 10000])
    price = round_price(eq["typical_price"])
    side = random.choice(SIDES)
    cp = random.choice(COUNTERPARTIES)
    tid = make_trade_id("EQ", SEQ)
    sd = settlement_date(BASE_DATE)
    
    return {
        "case_id": cid, "benchmark_version": "1.0",
        "workflow": "trade_confirmation_exception",
        "asset_class": "equities", "difficulty": "moderate",
        "risk_level": 3,
        "scenario_description": f"Account mismatch on {eq['instrument']}. Counterparty references FUND-A, internal shows FUND-B.",
        "business_context": "Block allocation may have been sent incorrectly. Total matches but account assignment differs.",
        "input": {
            "counterparty_confirmation": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": qty,
                "price": price, "currency": "USD",
                "settlement_instructions": random.choice(SSI_DTC),
                "broker": random.choice(EXEC_BROKERS), "commission": 0.01,
                "account_reference": "FUND-A-EQUITY"
            },
            "internal_record": {
                "trade_id": tid, "trade_date": "2026-07-01", "settlement_date": sd,
                "instrument": eq["instrument"], "cusip": eq["cusip"],
                "counterparty": cp, "side": side, "quantity": qty,
                "price": price, "currency": "USD",
                "settlement_instructions": random.choice(SSI_DTC),
                "broker": random.choice(EXEC_BROKERS), "commission": 0.01,
                "account": "FUND-B-EQUITY", "book": "US-EQUITY-LONG", "status": "Unconfirmed"
            }
        },
        "ground_truth": {
            "exception_exists": True,
            "primary_exception": {
                "category": "EXC-ACCT", "field": "account",
                "counterparty_value": "FUND-A-EQUITY", "internal_value": "FUND-B-EQUITY",
                "difference": "Different account/fund allocation",
                "difference_unit": "account",
                "total_exposure_usd": None
            },
            "secondary_exception": None,
            "recommended_action": "Account mismatch. Verify correct allocation with portfolio manager. Update counterparty with correct account before settlement.",
            "escalation_required": True, "escalation_target": "Portfolio manager",
            "human_review_required": True, "severity": 3, "confidence": "definitive"
        },
        "scoring_criteria": {
            "exception_detection": "exact_match", "category_identification": "exact_match",
            "field_identification": "exact_match", "action_recommendation": "semantic_match",
            "fabrication_check": "binary"
        },
        "failure_modes": ["FM-01", "FM-07"],
        "reviewer_notes": "Account mismatch: FUND-A vs FUND-B",
        "references": [],
        "version_history": [{"version": "1.0", "date": "2026-07", "change": "Initial"}]
    }


# ── GENERATE ALL 50 ──────────────────────────────────────────────────────────

def generate_batch():
    """Generate 40 new cases (011-050) to add to the 10 representative cases."""
    cases = []
    
    # 8 clean matches (various asset classes)
    for eq in random.sample(EQUITIES, 5):
        cases.append(gen_clean_equity(eq))
    for fi in random.sample(FIXED_INCOME, 2):
        cases.append(gen_clean_fi(fi))
    cases.append(gen_clean_futures(random.choice(FUTURES)))
    
    # 4 easy price breaks
    for eq in random.sample(EQUITIES, 2):
        cases.append(gen_price_break_equity(eq, "easy"))
    for fi in random.sample(FIXED_INCOME, 2):
        cases.append(gen_price_break_fi(fi, "easy"))
    
    # 3 moderate price breaks
    for eq in random.sample(EQUITIES, 2):
        cases.append(gen_price_break_equity(eq, "moderate"))
    cases.append(gen_price_break_fi(random.choice(FIXED_INCOME), "moderate"))
    
    # 3 quantity breaks (1 easy, 2 moderate)
    cases.append(gen_qty_break_equity(random.choice(EQUITIES), "easy"))
    cases.append(gen_qty_break_equity(random.choice(EQUITIES), "moderate"))
    cases.append(gen_qty_break_equity(random.choice(EQUITIES), "moderate"))
    
    # 3 settlement date breaks
    cases.append(gen_sdate_break(random.choice(EQUITIES), "equities", "moderate"))
    cases.append(gen_sdate_break(random.choice(FIXED_INCOME), "fixed_income", "moderate"))
    cases.append(gen_sdate_break(random.choice(EQUITIES), "equities", "complex"))
    
    # 2 counterparty mismatches (1 moderate, 1 complex)
    cases.append(gen_cpty_mismatch("moderate"))
    cases.append(gen_cpty_mismatch("complex"))
    
    # 3 commission breaks
    for _ in range(3):
        cases.append(gen_comm_break())
    
    # 2 SSI mismatches
    cases.append(gen_ssi_break())
    cases.append(gen_ssi_break())
    
    # 2 trade date breaks
    cases.append(gen_tdate_break())
    cases.append(gen_tdate_break())
    
    # 2 account mismatches
    cases.append(gen_acct_mismatch())
    cases.append(gen_acct_mismatch())
    
    # 3 complex multi-exception
    cases.append(gen_multi_exception_equity())
    cases.append(gen_multi_exception_equity())
    cases.append(gen_multi_exception_equity())
    
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AAL-D-001 Case Generator")
    parser.add_argument("--output", type=str, default="datasets/AAL-D-001-v1.0-batch1.json")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--merge", type=str, help="Merge with representative cases file")
    args = parser.parse_args()
    
    # Load representative cases
    import os
    rep_path = os.path.join(os.path.dirname(__file__), "examples", "representative_cases.json")
    
    if os.path.exists(rep_path):
        with open(rep_path) as f:
            representative = json.load(f)
        print(f"Loaded {len(representative)} representative cases")
    else:
        representative = []
        print("No representative cases found — generating standalone batch")
    
    # Generate new cases
    new_cases = generate_batch()
    print(f"Generated {len(new_cases)} new cases")
    
    # Combine
    all_cases = representative + new_cases
    print(f"Total: {len(all_cases)} cases")
    
    # Write
    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(all_cases, f, indent=2)
    print(f"Written to: {args.output}")
    
    # Validate if requested
    if args.validate:
        print("\nValidating...")
        os.system(f"python validation/validate.py --file {args.output}")
