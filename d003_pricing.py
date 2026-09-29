#!/usr/bin/env python3
"""
AI Alpha Labs — AAL-D-003 shared pricing/greeks module.

Deterministic, dependency-free (stdlib only) option pricing for the D-003
equity-options exception-identification benchmark generators.  Every value
that ends up as "ground truth" in a D-003 case is computed here in Python —
no LLM ever computes a number for this benchmark family.

Day count: ACT/365F everywhere (T = actual_days / 365.0).

Pricing models implemented (per D003-spec.md section 4):
    bsm_price / bsm_greeks              European, continuous dividend yield q
                                         (OTC index, Eurex index)
    escrowed_spot                       discrete-dividend "escrow" adjustment
                                         S_adj = S0 - PV(discrete dividends)
    crr_price_spot / crr_greeks_spot    CRR binomial on the escrowed spot,
                                         American *or* European, discrete
                                         dividends added back node-by-node
                                         (OTC single-name, Eurex single-stock)
    black76_price / black76_greeks      European options on a forward/future
                                         (CME EW weeklies)
    crr_price_future / crr_greeks_future
                                         CRR binomial on a future, carry = 0,
                                         American exercise
                                         (CME ES/NQ standard monthlies)

Greek unit convention (fixed, printed on every confirm):
    delta : $ price change per $1 of underlying            (standard ∂P/∂S)
    gamma : $ price change per $1 of underlying, per $1     (standard ∂²P/∂S²)
    vega  : $ price change per +1 full vol point (0.01 of sigma)
    theta : $ price change per -1 calendar day (ACT/365F)
    rho   : $ price change per 1bp (0.0001) change in the discount rate

Bump-and-reprice recipe for American (tree) greeks, central differences
where the spec calls for them:
    delta/gamma : spot bumped ±0.5% (central)
    vega        : sigma bumped +1 vol point only (one-sided, "+1 vol-pt")
    theta       : time bumped -1 day only (one-sided, "-1 day")
    rho         : rate bumped ±1bp (central)

European greeks are closed-form analytic formulas, algebraically rescaled
into the same fixed units above (so a European and an American leg in the
same spread print Greeks on a common, comparable scale).

Self-test (run this file directly): put-call parity for BSM/Black-76,
CRR European-limit convergence to BSM, American >= European premium,
deep-ITM call delta ~= 1.
"""

import math
from datetime import date, datetime, timedelta

DAY_COUNT = "ACT/365F"
DAYS_PER_YEAR = 365.0


# ── DAY COUNT ──────────────────────────────────────────────────────────────

def _as_date(d):
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(d, "%Y-%m-%d").date()


def yearfrac(d0, d1):
    """ACT/365F year fraction between two dates (d1 - d0) / 365."""
    d0, d1 = _as_date(d0), _as_date(d1)
    return (d1 - d0).days / DAYS_PER_YEAR


# ── NORMAL DISTRIBUTION (stdlib only) ─────────────────────────────────────

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ── DIVIDEND HANDLING (discrete, escrowed) ────────────────────────────────

def dividend_times_amounts(dividend_schedule, valuation_date, expiry_date):
    """
    dividend_schedule: list of {"date": "YYYY-MM-DD", "amount": float}
    Returns two parallel lists (t, amt) with t = ACT/365F(valuation_date, date),
    restricted to 0 < t <= T_expiry (dividends between now and expiry, excl.
    a dividend paid exactly on the valuation date itself).
    """
    T_exp = yearfrac(valuation_date, expiry_date)
    ts, amts = [], []
    for d in dividend_schedule or []:
        t = yearfrac(valuation_date, d["date"])
        if 1e-9 < t <= T_exp + 1e-9:
            ts.append(t)
            amts.append(float(d["amount"]))
    return ts, amts


def pv_dividends(div_times, div_amounts, r):
    return sum(amt * math.exp(-r * t) for t, amt in zip(div_times, div_amounts))


def escrowed_spot(S0, div_times, div_amounts, r):
    """S0 minus the present value of discrete dividends paid before expiry."""
    return S0 - pv_dividends(div_times, div_amounts, r)


# ── BSM (European, continuous dividend yield q) ───────────────────────────

def _d1_d2(S, K, r, q, sigma, T):
    if T <= 0 or sigma <= 0:
        raise ValueError("bsm requires T>0 and sigma>0")
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def bsm_price(S, K, r, q, sigma, T, is_call):
    d1, d2 = _d1_d2(S, K, r, q, sigma, T)
    if is_call:
        return S * math.exp(-q * T) * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * math.exp(-q * T) * norm_cdf(-d1)


def bsm_greeks(S, K, r, q, sigma, T, is_call):
    d1, d2 = _d1_d2(S, K, r, q, sigma, T)
    nd1 = norm_pdf(d1)
    if is_call:
        delta = math.exp(-q * T) * norm_cdf(d1)
        rho_raw = K * T * math.exp(-r * T) * norm_cdf(d2)
        theta_annual = (-(S * math.exp(-q * T) * nd1 * sigma) / (2 * math.sqrt(T))
                         - r * K * math.exp(-r * T) * norm_cdf(d2)
                         + q * S * math.exp(-q * T) * norm_cdf(d1))
    else:
        delta = math.exp(-q * T) * (norm_cdf(d1) - 1.0)
        rho_raw = -K * T * math.exp(-r * T) * norm_cdf(-d2)
        theta_annual = (-(S * math.exp(-q * T) * nd1 * sigma) / (2 * math.sqrt(T))
                         + r * K * math.exp(-r * T) * norm_cdf(-d2)
                         - q * S * math.exp(-q * T) * norm_cdf(-d1))
    gamma = math.exp(-q * T) * nd1 / (S * sigma * math.sqrt(T))
    vega_raw = S * math.exp(-q * T) * nd1 * math.sqrt(T)
    return {
        "delta": delta,
        "gamma": gamma,
        "vega": vega_raw * 0.01,          # per 1 vol point
        "theta": theta_annual / DAYS_PER_YEAR,  # per 1 calendar day
        "rho": rho_raw * 0.0001,          # per 1bp
    }


# ── Black-76 (European options on a forward/future) ───────────────────────

def _d1_d2_black76(F, K, r, sigma, T):
    if T <= 0 or sigma <= 0:
        raise ValueError("black76 requires T>0 and sigma>0")
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def black76_price(F, K, r, sigma, T, is_call):
    d1, d2 = _d1_d2_black76(F, K, r, sigma, T)
    disc = math.exp(-r * T)
    if is_call:
        return disc * (F * norm_cdf(d1) - K * norm_cdf(d2))
    return disc * (K * norm_cdf(-d2) - F * norm_cdf(-d1))


def black76_greeks(F, K, r, sigma, T, is_call):
    d1, d2 = _d1_d2_black76(F, K, r, sigma, T)
    disc = math.exp(-r * T)
    nd1 = norm_pdf(d1)
    price = black76_price(F, K, r, sigma, T, is_call)
    if is_call:
        delta = disc * norm_cdf(d1)
    else:
        delta = disc * (norm_cdf(d1) - 1.0)
    gamma = disc * nd1 / (F * sigma * math.sqrt(T))
    vega_raw = F * disc * nd1 * math.sqrt(T)
    theta_annual = r * price - (F * disc * nd1 * sigma) / (2 * math.sqrt(T))
    rho_raw = -T * price  # d1,d2 have no r-dependence in Black-76; only the discount factor does
    return {
        "delta": delta,
        "gamma": gamma,
        "vega": vega_raw * 0.01,
        "theta": theta_annual / DAYS_PER_YEAR,
        "rho": rho_raw * 0.0001,
    }


# ── CRR binomial: escrowed-dividend spot tree (American or European) ─────

def _crr_price_spot_raw(S0, K, r, sigma, T, N, is_call, american, div_times=None, div_amounts=None):
    """
    Single-tree CRR binomial on the escrowed (dividend-adjusted) spot.  At
    every node the *actual* stock price used for the exercise/payoff test is
    the tree price plus the PV (at that node's time) of dividends not yet
    paid, which keeps the tree recombining while still handling American
    exercise correctly around ex-dividend dates.

    CRR has a well-known "sawtooth" convergence pattern (worst right at the
    money) — this raw single-tree function is intentionally kept cheap and
    is what `crr_greeks_spot` bumps-and-reprices with; the bias mostly
    cancels in the finite difference. Reported *prices* should go through
    `crr_price_spot` below, which averages N and N+1 (Boyle averaging) to
    kill the oscillation for ground-truth premiums.
    """
    div_times = div_times or []
    div_amounts = div_amounts or []
    dt = T / N
    Sadj0 = escrowed_spot(S0, div_times, div_amounts, r)
    if Sadj0 <= 0:
        raise ValueError("escrowed spot must be positive (dividends too large vs spot)")
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    growth = math.exp(r * dt)
    p = (growth - d) / (u - d)
    disc = math.exp(-r * dt)
    if not (0.0 < p < 1.0):
        raise ValueError("CRR risk-neutral probability out of (0,1) — check sigma/N/dt")

    # remaining PV of unpaid dividends at each time step i
    remaining_pv = []
    for i in range(N + 1):
        t_i = i * dt
        rpv = sum(amt * math.exp(-r * (t - t_i)) for t, amt in zip(div_times, div_amounts) if t > t_i + 1e-12)
        remaining_pv.append(rpv)

    # terminal payoffs
    values = [0.0] * (N + 1)
    for j in range(N + 1):
        Sadj_T = Sadj0 * (u ** (N - j)) * (d ** j)
        S_actual = Sadj_T + remaining_pv[N]  # == Sadj_T, remaining_pv[N] == 0
        payoff = max(S_actual - K, 0.0) if is_call else max(K - S_actual, 0.0)
        values[j] = payoff

    for i in range(N - 1, -1, -1):
        rpv_i = remaining_pv[i]
        for j in range(i + 1):
            cont = disc * (p * values[j] + (1 - p) * values[j + 1])
            if american:
                Sadj_i = Sadj0 * (u ** (i - j)) * (d ** j)
                S_actual = Sadj_i + rpv_i
                exer = max(S_actual - K, 0.0) if is_call else max(K - S_actual, 0.0)
                values[j] = max(cont, exer)
            else:
                values[j] = cont
    return values[0]


def crr_price_spot(S0, K, r, sigma, T, N, is_call, american, div_times=None, div_amounts=None):
    """Ground-truth premium: average of the N-step and (N+1)-step raw CRR
    trees (Boyle averaging) — removes the odd/even sawtooth bias so reported
    premiums are stable to well under a cent even exactly at the money."""
    p_n = _crr_price_spot_raw(S0, K, r, sigma, T, N, is_call, american, div_times, div_amounts)
    p_n1 = _crr_price_spot_raw(S0, K, r, sigma, T, N + 1, is_call, american, div_times, div_amounts)
    return (p_n + p_n1) / 2.0


def crr_greeks_spot(S0, K, r, sigma, T, N, is_call, american, div_times=None, div_amounts=None):
    div_times = div_times or []
    div_amounts = div_amounts or []

    def price(S_, sigma_, r_, T_, dts_, das_):
        # bump-and-reprice on the Boyle-averaged tree — greeks off the raw
        # single-N tree inherit the odd/even sawtooth (worst for gamma, where
        # the 0.5% bump is comparable to the node spacing)
        return crr_price_spot(S_, K, r_, sigma_, T_, N, is_call, american, dts_, das_)

    h_S = 0.005 * S0
    p_up = price(S0 + h_S, sigma, r, T, div_times, div_amounts)
    p_dn = price(S0 - h_S, sigma, r, T, div_times, div_amounts)
    p_mid = price(S0, sigma, r, T, div_times, div_amounts)
    delta = (p_up - p_dn) / (2 * h_S)
    gamma = (p_up - 2 * p_mid + p_dn) / (h_S ** 2)

    vega = price(S0, sigma + 0.01, r, T, div_times, div_amounts) - p_mid

    dt_day = 1.0 / DAYS_PER_YEAR
    T_theta = max(T - dt_day, 1e-6)
    dts_theta = [t - dt_day for t in div_times if t - dt_day > 1e-9]
    das_theta = [a for t, a in zip(div_times, div_amounts) if t - dt_day > 1e-9]
    theta = price(S0, sigma, r, T_theta, dts_theta, das_theta) - p_mid

    rho = (price(S0, sigma, r + 0.0001, T, div_times, div_amounts)
           - price(S0, sigma, r - 0.0001, T, div_times, div_amounts)) / 2.0

    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}


# ── CRR binomial on a future, carry = 0 (American-on-future) ─────────────

def _crr_price_future_raw(F0, K, r, sigma, T, N, is_call, american):
    dt = T / N
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    p = (1.0 - d) / (u - d)   # carry-0 futures growth factor = 1
    disc = math.exp(-r * dt)
    if not (0.0 < p < 1.0):
        raise ValueError("CRR risk-neutral probability out of (0,1) — check sigma/N/dt")

    values = [0.0] * (N + 1)
    for j in range(N + 1):
        F_T = F0 * (u ** (N - j)) * (d ** j)
        values[j] = max(F_T - K, 0.0) if is_call else max(K - F_T, 0.0)

    for i in range(N - 1, -1, -1):
        for j in range(i + 1):
            cont = disc * (p * values[j] + (1 - p) * values[j + 1])
            if american:
                F_i = F0 * (u ** (i - j)) * (d ** j)
                exer = max(F_i - K, 0.0) if is_call else max(K - F_i, 0.0)
                values[j] = max(cont, exer)
            else:
                values[j] = cont
    return values[0]


def crr_price_future(F0, K, r, sigma, T, N, is_call, american):
    """Ground-truth premium: N/(N+1) Boyle-averaged CRR-on-future (carry 0)."""
    p_n = _crr_price_future_raw(F0, K, r, sigma, T, N, is_call, american)
    p_n1 = _crr_price_future_raw(F0, K, r, sigma, T, N + 1, is_call, american)
    return (p_n + p_n1) / 2.0


def crr_greeks_future(F0, K, r, sigma, T, N, is_call, american):
    def price(F_, sigma_, r_, T_):
        # Boyle-averaged for the same sawtooth reason as crr_greeks_spot
        return crr_price_future(F_, K, r_, sigma_, T_, N, is_call, american)

    h_F = 0.005 * F0
    p_up = price(F0 + h_F, sigma, r, T)
    p_dn = price(F0 - h_F, sigma, r, T)
    p_mid = price(F0, sigma, r, T)
    delta = (p_up - p_dn) / (2 * h_F)
    gamma = (p_up - 2 * p_mid + p_dn) / (h_F ** 2)

    vega = price(F0, sigma + 0.01, r, T) - p_mid

    dt_day = 1.0 / DAYS_PER_YEAR
    T_theta = max(T - dt_day, 1e-6)
    theta = price(F0, sigma, r, T_theta) - p_mid

    rho = (price(F0, sigma, r + 0.0001, T) - price(F0, sigma, r - 0.0001, T)) / 2.0

    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}


# ── SELF TEST ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ok = True

    def check(name, cond, detail=""):
        global ok
        status = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"[{status}] {name}  {detail}")

    # 1. Put-call parity, BSM (European, continuous q)
    S, K, r, q, sigma, T = 100.0, 100.0, 0.045, 0.015, 0.22, 0.75
    c = bsm_price(S, K, r, q, sigma, T, True)
    p = bsm_price(S, K, r, q, sigma, T, False)
    lhs = c - p
    rhs = S * math.exp(-q * T) - K * math.exp(-r * T)
    check("BSM put-call parity", abs(lhs - rhs) < 1e-8, f"|{lhs - rhs:.2e}| < 1e-8")

    # 2. Put-call parity, Black-76
    F, K2, r2, sigma2, T2 = 5900.0, 5950.0, 0.045, 0.19, 0.5
    cc = black76_price(F, K2, r2, sigma2, T2, True)
    pp = black76_price(F, K2, r2, sigma2, T2, False)
    lhs2 = cc - pp
    rhs2 = math.exp(-r2 * T2) * (F - K2)
    check("Black-76 put-call parity", abs(lhs2 - rhs2) < 1e-8, f"|{lhs2 - rhs2:.2e}| < 1e-8")

    # 3. CRR European-limit convergence to BSM (escrowed divs) within 0.01 @ 1500 steps
    div_times = [0.25, 0.5]
    div_amounts = [0.60, 0.60]
    S0, K3, r3, sigma3, T3, N = 150.0, 150.0, 0.04, 0.28, 1.0, 1500
    Sadj0 = escrowed_spot(S0, div_times, div_amounts, r3)
    bsm_ref_call = bsm_price(Sadj0, K3, r3, 0.0, sigma3, T3, True)
    crr_euro_call = crr_price_spot(S0, K3, r3, sigma3, T3, N, True, american=False,
                                    div_times=div_times, div_amounts=div_amounts)
    check("CRR European-limit -> BSM (call)", abs(bsm_ref_call - crr_euro_call) < 0.01,
          f"BSM={bsm_ref_call:.4f} CRR={crr_euro_call:.4f} |Δ|={abs(bsm_ref_call - crr_euro_call):.5f}")

    bsm_ref_put = bsm_price(Sadj0, K3, r3, 0.0, sigma3, T3, False)
    crr_euro_put = crr_price_spot(S0, K3, r3, sigma3, T3, N, False, american=False,
                                   div_times=div_times, div_amounts=div_amounts)
    check("CRR European-limit -> BSM (put)", abs(bsm_ref_put - crr_euro_put) < 0.01,
          f"BSM={bsm_ref_put:.4f} CRR={crr_euro_put:.4f} |Δ|={abs(bsm_ref_put - crr_euro_put):.5f}")

    # 4. American >= European premium (same inputs, put is the interesting case with divs)
    crr_amer_put = crr_price_spot(S0, K3, r3, sigma3, T3, N, False, american=True,
                                   div_times=div_times, div_amounts=div_amounts)
    check("American >= European (put)", crr_amer_put >= crr_euro_put - 1e-9,
          f"American={crr_amer_put:.4f} European={crr_euro_put:.4f}")

    crr_amer_call = crr_price_spot(S0, K3, r3, sigma3, T3, N, True, american=True,
                                    div_times=div_times, div_amounts=div_amounts)
    check("American >= European (call)", crr_amer_call >= crr_euro_call - 1e-9,
          f"American={crr_amer_call:.4f} European={crr_euro_call:.4f}")

    # 4b. American-on-future >= European-on-future (Black-76 vs CRR-future, no divs -> should be ~equal, still >=)
    F0, K4, r4, sigma4, T4, N4 = 5900.0, 5900.0, 0.045, 0.18, 0.33, 1500
    black76_c = black76_price(F0, K4, r4, sigma4, T4, True)
    crr_fut_euro_c = crr_price_future(F0, K4, r4, sigma4, T4, N4, True, american=False)
    crr_fut_amer_c = crr_price_future(F0, K4, r4, sigma4, T4, N4, True, american=True)
    check("CRR-future European-limit -> Black-76", abs(black76_c - crr_fut_euro_c) < 0.01,
          f"Black76={black76_c:.4f} CRR={crr_fut_euro_c:.4f} |Δ|={abs(black76_c - crr_fut_euro_c):.5f}")
    check("American-on-future >= European-on-future", crr_fut_amer_c >= crr_fut_euro_c - 1e-9,
          f"American={crr_fut_amer_c:.4f} European={crr_fut_euro_c:.4f}")

    # 5. Deep-ITM call delta ~= 1 (BSM)
    deep_delta = bsm_greeks(300.0, 100.0, 0.04, 0.01, 0.20, 0.5, True)["delta"]
    check("Deep-ITM call delta ~= 1", abs(deep_delta - 1.0) < 0.01, f"delta={deep_delta:.5f}")

    # 5b. Deep-ITM put delta ~= -1
    deep_put_delta = bsm_greeks(30.0, 100.0, 0.04, 0.01, 0.20, 0.5, False)["delta"]
    check("Deep-ITM put delta ~= -1", abs(deep_put_delta + 1.0) < 0.01, f"delta={deep_put_delta:.5f}")

    # 6. Greek sign sanity across the board
    g_call = bsm_greeks(100, 100, 0.04, 0.01, 0.20, 0.5, True)
    g_put = bsm_greeks(100, 100, 0.04, 0.01, 0.20, 0.5, False)
    check("Call delta in (0,1]", 0.0 < g_call["delta"] <= 1.0, f"delta={g_call['delta']:.4f}")
    check("Put delta in [-1,0)", -1.0 <= g_put["delta"] < 0.0, f"delta={g_put['delta']:.4f}")
    check("Gamma >= 0 (call)", g_call["gamma"] >= 0, f"gamma={g_call['gamma']:.5f}")
    check("Gamma >= 0 (put)", g_put["gamma"] >= 0, f"gamma={g_put['gamma']:.5f}")
    check("Vega >= 0 (call)", g_call["vega"] >= 0, f"vega={g_call['vega']:.5f}")
    check("Vega >= 0 (put)", g_put["vega"] >= 0, f"vega={g_put['vega']:.5f}")
    check("Long call theta <= 0", g_call["theta"] <= 0, f"theta={g_call['theta']:.5f}")

    # American greeks sign sanity (spot tree)
    ag_call = crr_greeks_spot(150.0, 150.0, 0.04, 0.28, 1.0, 800, True, american=True,
                               div_times=div_times, div_amounts=div_amounts)
    ag_put = crr_greeks_spot(150.0, 150.0, 0.04, 0.28, 1.0, 800, False, american=True,
                              div_times=div_times, div_amounts=div_amounts)
    check("American call delta in (0,1]", 0.0 < ag_call["delta"] <= 1.0, f"delta={ag_call['delta']:.4f}")
    check("American put delta in [-1,0)", -1.0 <= ag_put["delta"] < 0.0, f"delta={ag_put['delta']:.4f}")
    check("American gamma >= 0 (call)", ag_call["gamma"] >= -1e-6, f"gamma={ag_call['gamma']:.5f}")
    check("American vega >= 0 (call)", ag_call["vega"] >= -1e-6, f"vega={ag_call['vega']:.5f}")

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    if not ok:
        raise SystemExit(1)
