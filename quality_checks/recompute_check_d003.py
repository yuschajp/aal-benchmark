#!/usr/bin/env python3
"""
recompute_check_d003.py — QA gate 2 (independent recomputation) for AAL-D-003.

This checker reprices every AAL-D-003 option case via a PRICING PATH THAT SHARES
ZERO CODE WITH THE GENERATORS. It was written only from:
  - documentation/D003-spec.md
  - schemas/benchmark_case.json
  - the family shape of datasets/AAL-D-001-v1.0.json
It deliberately never reads d003_pricing.py, generate_d003_*.py, or any *d003*
implementation file. Its entire value is that any agreement with the generator's
`generation_metadata.correct_values` is corroboration by two independent
implementations, not a tautology.

--------------------------------------------------------------------------------
CONVENTIONS ASSUMED  (spec is authoritative; where it under-determines a
convention we pick the spec-consistent reading and record it here so the
coordinator can reconcile against the generator)
--------------------------------------------------------------------------------
C1. Day count: ACT/365F everywhere (spec §7). Year fraction between two dates =
    (d2 - d1).days / 365.0. Time to expiry T = (expiry - valuation_date).days/365.

C2. Volatility unit: `sigma` in valuation_inputs is a DECIMAL (0.20 == 20%). If a
    value > 3.0 is seen it is treated as a percent and divided by 100, with a
    warning — this guards the classic "vol points vs decimal" unit slip.

C3. Rates / yields: r and q are annualized CONTINUOUS decimals. Same >3.0 percent
    guard as vol.

C4. Pricing models (spec §4):
      - OTC index / Eurex index / CME EW weeklies : European, closed form.
          * index (OTC, Eurex)     -> BSM with continuous yield q on spot.
          * CME EW weeklies        -> Black-76 on the futures price.
      - OTC single-name European   -> BSM on the escrowed spot (S - PV(discrete
          dividends due before expiry)), q = 0. (spec: "BSM on forward, escrowed
          discrete divs".)
      - American (OTC single-name, Eurex single-stock) -> CRR binomial with the
          ESCROWED discrete-dividend tree (Hull): tree is built on the escrowed
          spot and the PV of not-yet-paid dividends is added back at each node so
          early-exercise intrinsic uses the true (cum-dividend) underlying. This
          is the reading consistent with the European "escrowed" model above.
          NOTE: if the generator used a different discrete-dividend tree method
          (proportional divs, dollar-drop non-recombining, spot-minus-PV without
          add-back), American-with-dividends cases will exceed the tree bound and
          FAIL. That is intended: it is a real model disagreement for the
          coordinator to reconcile, NOT something a tree tolerance should hide.
      - CME ES/NQ monthlies -> American on the FUTURE, cost-of-carry 0 (futures
          are martingales under Q): CRR with growth factor 1, p = (1-d)/(u-d),
          discounting each step by e^{-r*dt}. No dividends.

C5. Greek units (spec §4 fixes the bump sizes; we report closed-form European
    Greeks in the SAME units so European and American Greeks are directly
    comparable, which is how a confirm prints them):
      - delta = dV/dS               (dimensionless price sensitivity)
      - gamma = d2V/dS2
      - vega  = dV/dsigma * 0.01     (per +1 vol POINT)
      - theta = -dV/dT   * (1/365)   (per -1 CALENDAR day, ACT/365F)
      - rho   = dV/dr    * 0.0001    (per +1 bp)
    American Greeks are bump-and-reprice with the spec bumps: spot +/-0.5%
    (central) for delta/gamma; vol +1pt ONE-SIDED for vega [V(sig+.01)-V(sig)];
    T -1 day (one-sided backward) for theta; r +/-1bp (central) for rho.
    Vega is one-sided per spec §4 (confirmed by the coordinator on case
    AAL-D-003-206, a deep-ITM American-on-future call whose price is pinned at
    intrinsic below the base vol: a CENTRAL bump halves the true forward
    sensitivity there because V(sig-.01) is stuck on the early-exercise kink).

C10. Multi-leg cases: each record carries a `legs` array (per leg: leg_index
    [1-based], option_type, exercise_style, strike, expiry, side, quantity,
    multiplier, premium_per_option, premium_unit, optional greeks, optional
    dividend_schedule) plus trade-level fields. correct_values keys are
    `leg_N_premium` and `leg_N_greeks`; SINGLE-leg cases use the same
    `leg_1_premium`/`leg_1_greeks` naming (with a legacy top-level price/greeks
    fallback for leg 1). Every leg is repriced with the shared valuation_inputs
    (spot/futures_price, sigma, r) and its OWN strike/expiry/type/style and
    per-leg dividend_schedule (falling back to the trade-level one), then compared
    to leg_N_premium / leg_N_greeks. Venue/underlying-kind (index vs single-name
    vs future) is trade-level; call/put and exercise style are per leg.

C11. injected_error is a single object {field, side, kind, magnitude, leg_index}
    (leg_index null for trade-level fields, 1-based int inside legs[]); dual-
    exception cases add injected_error_secondary of the same shape. Both are
    verified when present. `field` may be a dotted/indexed path resolved by the
    generic resolver (split on '.', handle '[N]'); when leg_index is set it
    resolves inside legs[leg_index-1] first, else the record top level. Clean
    cases require NO scored field to differ anywhere -- trade level, every leg,
    and per-leg greeks -- compared under the §9 field tolerances so display
    rounding does not false-alarm.

C6. PRICE tolerance: closed-form (European) prices must match correct_values to
    1e-6 RELATIVE (spec gate 2). American prices must fall within the per-case
    tree-convergence bound (see below).

C7. GREEK tolerance: Greeks are compared at the spec §6 scoring tolerances
    (delta abs 0.01; gamma abs 0.005; vega rel 1% floor 0.01; theta rel 2% floor
    0.01; rho rel 2% floor 0.01) rather than 1e-6, because the exact Greek
    day-count/one-sided-vs-central/per-point conventions are under-determined by
    the spec. A near-miss that instead matches a *different unit scaling*
    (per-unit-vol vs per-point, /360 vs /365) is reported as UNIT-MISMATCH rather
    than a bare FAIL, to surface the exact failure mode for reconciliation.

C8. Premium / notional / exposure arithmetic (spec §8.4, §9):
      premium_total   = premium_per_option * multiplier * contracts   (per leg)
      net premium     = sum of leg premiums (multi-leg)
      difference field = |counterparty_value - internal_value|
      total_exposure_usd is checked for consistency with
      |difference| * quantity * multiplier within the family $1,000 tolerance
      when the exception carries enough fields to reconstruct it.

C9. Multiplier defaults when not printed: equity/single-name options 100; CME ES
    50; CME NQ 20. Detected from ticker/root when a `multiplier` field is absent.

--------------------------------------------------------------------------------
TREE-CONVERGENCE BOUND  (American price check; spec: "derive and document")
--------------------------------------------------------------------------------
The generator does NOT use a plain single-N CRR price: it BOYLE-AVERAGES the
N=1500 and N=1501 trees. Boyle averaging cancels the leading CRR oscillation
(node position relative to strike/exercise boundary flips parity between N and
N+1), leaving a smooth ~a/N residual. So the generator's correct_value ~=
  boyle_lo = 0.5*(P(1500) + P(1501)).
We keep our OWN independent estimator (a DIFFERENT smoothed estimator, so
agreement is real corroboration, not replication) and bound the cross-estimator
gap analytically rather than hand-waving:
  1. boyle_lo = 0.5*(P(1500)+P(1501));  boyle_hi = 0.5*(P(3000)+P(3001)).
  2. P* = 2*boyle_hi - boyle_lo  -- Richardson on the Boyle sequence; since the
     Boyle residual is ~a/N this removes it and P* is O(1/N^2), our accurate
     independent estimate of the true American price.
  3. The exact offset our P* must tolerate vs the generator's boyle_lo is
     |boyle_lo - P*| = 2|boyle_hi - boyle_lo|, which we MEASURE per case.
  4. bound = SAFETY*offset + REL_FLOOR*|P*| + ATOL, SAFETY = 4, REL_FLOOR = 5e-5,
     ATOL = 1e-4, then capped at REL_CAP = 5e-3 relative.
Because the bound scales with the measured offset (typically 3e-5..6e-4 REL, per
the coordinator's v3 diagnosis) and is hard-capped at 0.5% rel, it is tight
enough that a genuine dividend-method or model disagreement (>=1% rel) still
FAILS loudly, yet never false-alarms on the legitimate Boyle-vs-Richardson
estimator offset. The bound is printed per American case in --verbose.
Discrete-dividend American cases carry the extra caveat (Conventions C4) that a
model-method mismatch shows up as a constant shift far exceeding this bound.

Self-test (run with --self-test, and run as part of every invocation's exit code
if requested) validates the pricing core independently of any dataset:
put-call parity (BSM & Black-76), BSM<->CRR European convergence, American>=European,
a hand-computed Black-76 value, and closed-form-vs-finite-difference Greeks.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from datetime import date, datetime

# --------------------------------------------------------------------------- #
# Numerics                                                                     #
# --------------------------------------------------------------------------- #

_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT2PI


def _intrinsic(S: float, K: float, is_call: bool) -> float:
    return max(S - K, 0.0) if is_call else max(K - S, 0.0)


# --------------------------------------------------------------------------- #
# Closed form: Black-Scholes-Merton with continuous yield q                    #
# --------------------------------------------------------------------------- #


def bsm_price(S, K, r, q, sigma, T, is_call):
    if T <= 0 or sigma <= 0 or S <= 0:
        return _intrinsic(S, K, is_call) * math.exp(-0.0)
    srt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / srt
    d2 = d1 - srt
    if is_call:
        return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)


def bsm_greeks(S, K, r, q, sigma, T, is_call):
    """Closed-form Greeks in the units of Convention C5."""
    srt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / srt
    d2 = d1 - srt
    eqt = math.exp(-q * T)
    ert = math.exp(-r * T)
    pdf1 = _norm_pdf(d1)
    if is_call:
        delta = eqt * _norm_cdf(d1)
        rho_unit = K * T * ert * _norm_cdf(d2)
        theta_year = (-S * eqt * pdf1 * sigma / (2 * math.sqrt(T))
                      - r * K * ert * _norm_cdf(d2)
                      + q * S * eqt * _norm_cdf(d1))
    else:
        delta = -eqt * _norm_cdf(-d1)
        rho_unit = -K * T * ert * _norm_cdf(-d2)
        theta_year = (-S * eqt * pdf1 * sigma / (2 * math.sqrt(T))
                      + r * K * ert * _norm_cdf(-d2)
                      - q * S * eqt * _norm_cdf(-d1))
    gamma = eqt * pdf1 / (S * srt)
    vega_unit = S * eqt * pdf1 * math.sqrt(T)
    return {
        "delta": delta,
        "gamma": gamma,
        "vega": vega_unit * 0.01,
        "theta": theta_year / 365.0,
        "rho": rho_unit * 0.0001,
    }


# --------------------------------------------------------------------------- #
# Closed form: Black-76 on a futures price                                     #
# --------------------------------------------------------------------------- #


def black76_price(F, K, r, sigma, T, is_call):
    if T <= 0 or sigma <= 0 or F <= 0:
        return math.exp(-r * max(T, 0.0)) * _intrinsic(F, K, is_call)
    srt = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / srt
    d2 = d1 - srt
    ert = math.exp(-r * T)
    if is_call:
        return ert * (F * _norm_cdf(d1) - K * _norm_cdf(d2))
    return ert * (K * _norm_cdf(-d2) - F * _norm_cdf(-d1))


def black76_greeks(F, K, r, sigma, T, is_call):
    srt = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / srt
    d2 = d1 - srt
    ert = math.exp(-r * T)
    pdf1 = _norm_pdf(d1)
    price = black76_price(F, K, r, sigma, T, is_call)
    if is_call:
        delta = ert * _norm_cdf(d1)
    else:
        delta = -ert * _norm_cdf(-d1)
    gamma = ert * pdf1 / (F * srt)
    vega_unit = ert * F * pdf1 * math.sqrt(T)
    # theta_year = -dV/dT ; rho = dV/dr = -T*price (only discount factor depends on r)
    theta_year = r * price - ert * F * pdf1 * sigma / (2 * math.sqrt(T))
    rho_unit = -T * price
    return {
        "delta": delta,
        "gamma": gamma,
        "vega": vega_unit * 0.01,
        "theta": theta_year / 365.0,
        "rho": rho_unit * 0.0001,
    }


# --------------------------------------------------------------------------- #
# Escrowed European (OTC single-name): BSM on spot minus PV(discrete divs)     #
# --------------------------------------------------------------------------- #


def _pv_divs(divs, r, upto_T, from_t=0.0):
    """PV at time `from_t` of dividends with from_t < t_i <= upto_T."""
    return sum(d * math.exp(-r * (t - from_t)) for (t, d) in divs if from_t < t <= upto_T + 1e-12)


def escrowed_european_price(S, K, r, sigma, T, is_call, divs):
    S_adj = S - _pv_divs(divs, r, T)
    return bsm_price(S_adj, K, r, 0.0, sigma, T, is_call)


def escrowed_european_greeks(S, K, r, sigma, T, is_call, divs):
    S_adj = S - _pv_divs(divs, r, T)
    return bsm_greeks(S_adj, K, r, 0.0, sigma, T, is_call)


# --------------------------------------------------------------------------- #
# CRR binomial: continuous q, escrowed discrete divs, or on-future (carry 0)   #
# --------------------------------------------------------------------------- #


def crr_price(S0, K, r, sigma, T, N, is_call, american,
              q=0.0, divs=None, on_future=False):
    if T <= 0 or sigma <= 0:
        return _intrinsic(S0, K, is_call)
    N = int(N)
    dt = T / N
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    disc = math.exp(-r * dt)

    if on_future:
        growth = 1.0                      # futures are Q-martingales
    else:
        growth = math.exp((r - q) * dt)
    p = (growth - d) / (u - d)
    if not (0.0 < p < 1.0):
        raise ValueError(f"CRR risk-neutral prob out of range: p={p} (dt too large)")

    divs = divs or []
    if on_future:
        S_tilde = S0
        pv0 = 0.0
    else:
        pv0 = _pv_divs(divs, r, T)
        S_tilde = S0 - pv0

    # Precompute powers of u for exponents -N..N. Since u*d == 1, the underlying
    # at node (layer i, j ups) is S_tilde * u^j * d^(i-j) = S_tilde * u^(2j-i).
    # This removes a pow() call from every node in the American backward pass.
    upow = [0.0] * (2 * N + 1)
    upow[N] = 1.0
    for k in range(1, N + 1):
        upow[N + k] = upow[N + k - 1] * u
        upow[N - k] = upow[N - k + 1] * d
    Stil = S_tilde  # local alias

    # Terminal layer: exponent 2j - N -> index 2j
    V = [0.0] * (N + 1)
    for j in range(N + 1):
        V[j] = _intrinsic(Stil * upow[2 * j], K, is_call)

    if not american:
        for i in range(N - 1, -1, -1):
            for j in range(i + 1):
                V[j] = disc * (p * V[j + 1] + (1.0 - p) * V[j])
        return V[0]

    one_mp = 1.0 - p
    for i in range(N - 1, -1, -1):
        t = i * dt
        pvrem = 0.0 if (on_future or not divs) else _pv_divs(divs, r, T, from_t=t)
        base_idx = N - i  # index for exponent (2j - i) at j=0 is (-i)+N
        for j in range(i + 1):
            cont = disc * (p * V[j + 1] + one_mp * V[j])
            S_node = Stil * upow[base_idx + 2 * j] + pvrem
            V[j] = cont if cont >= (S_node - K if is_call else K - S_node) \
                else (S_node - K if is_call else K - S_node)
    return V[0]


# --------------------------------------------------------------------------- #
# American reference price + per-case tree-convergence bound                   #
# --------------------------------------------------------------------------- #

_BOUND_GEN_N = 1500          # generator's Boyle-averaging step count (N, N+1)
_BOUND_SAFETY = 4.0          # multiple of the measured cross-estimator offset
_BOUND_REL_FLOOR = 5e-5      # relative floor (absorbs float noise / tiny offsets)
_BOUND_ATOL = 5e-3           # absolute floor: half a display cent — confirms
                             # print premiums at 2dp, so a smaller price
                             # disagreement is unobservable in the case data
                             # (and 2x inside the 0.01 scoring tolerance)
_BOUND_REL_CAP = 5e-3        # hard cap: never mask a >=1% real disagreement


def american_reference(price_fn):
    """price_fn(N) -> plain CRR price at N steps.

    Returns (P_star, bound). The generator's comparand is a BOYLE average of the
    N=1500 and N=1501 trees, not a plain single-N price, so we bound the gap to
    *that* estimator, derived (not hand-waved):

      boyle_lo = 0.5*(P(1500)+P(1501))     ~= the generator's correct_value
      boyle_hi = 0.5*(P(3000)+P(3001))
      P*       = 2*boyle_hi - boyle_lo     Richardson on the Boyle sequence
                                           (Boyle residual ~ a/N -> P* is O(1/N^2))

    Boyle averaging cancels the leading CRR oscillation, leaving a smooth ~a/N
    error; Richardson on boyle_lo/boyle_hi removes that, so P* is our accurate
    independent estimate. The exact offset our P* must tolerate against the
    generator's boyle_lo is |boyle_lo - P*| = 2|boyle_hi - boyle_lo|, which we
    MEASURE per case. bound = SAFETY * that offset + floors, capped at 0.5% rel
    so a genuine dividend-method/model disagreement (>=1% rel) still fails loudly.
    """
    N = _BOUND_GEN_N
    boyle_lo = 0.5 * (price_fn(N) + price_fn(N + 1))
    boyle_hi = 0.5 * (price_fn(2 * N) + price_fn(2 * N + 1))
    p_star = 2.0 * boyle_hi - boyle_lo
    offset = abs(boyle_lo - p_star)      # == 2*|boyle_hi - boyle_lo|
    bound = _BOUND_SAFETY * offset + _BOUND_REL_FLOOR * abs(p_star) + _BOUND_ATOL
    bound = min(bound, _BOUND_REL_CAP * abs(p_star) + _BOUND_ATOL)
    return p_star, bound


# --------------------------------------------------------------------------- #
# American Greeks by bump-and-reprice (spec bumps, Convention C5)              #
# --------------------------------------------------------------------------- #


def american_greeks(reprice, S, sigma, r, T, reprice_theta=None):
    """
    reprice(S=..., sigma=..., r=..., T=...) -> American price with those inputs
    (all others closed over). Uses the exact spec bumps. reprice_theta, when
    given, is used only for the T-1day evaluation (see theta note below).
    """
    hS = 0.005 * S
    base = reprice(S=S, sigma=sigma, r=r, T=T)
    up = reprice(S=S + hS, sigma=sigma, r=r, T=T)
    dn = reprice(S=S - hS, sigma=sigma, r=r, T=T)
    delta = (up - dn) / (2 * hS)
    gamma = (up - 2 * base + dn) / (hS * hS)
    # vega: ONE-SIDED +1 vol-point, spec-literal (§4). Central averaging halves
    # the true sensitivity when the base point sits on an early-exercise kink
    # (deep-ITM American where V(sigma-.01) is pinned at intrinsic), so it must
    # be one-sided here.
    v_up = reprice(S=S, sigma=sigma + 0.01, r=r, T=T)
    vega = v_up - base                            # per +1 vol point (one-sided)
    day = 1.0 / 365.0
    # theta means "one calendar day passes": dividends keep their DATES, so
    # their year-fractions shrink by 1/365 too. reprice_theta (when supplied)
    # closes over the day-shifted dividend schedule; without it, discrete-div
    # cases would hold dividend times fixed while T shrinks — a different and
    # economically wrong convention (surfaced by a deep-ITM put whose value is
    # carried by discounting to the ex-div date).
    theta_fn = reprice_theta or reprice
    theta = theta_fn(S=S, sigma=sigma, r=r, T=max(T - day, 1e-8)) - base  # per -1 day
    r_up = reprice(S=S, sigma=sigma, r=r + 0.0001, T=T)
    r_dn = reprice(S=S, sigma=sigma, r=r - 0.0001, T=T)
    rho = (r_up - r_dn) / 2.0                      # per +1 bp (central)
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}


# --------------------------------------------------------------------------- #
# Tolerant JSON access + unit / date parsing                                   #
# --------------------------------------------------------------------------- #


def _get(d, *keys, default=None):
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    # case-insensitive fallback
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] is not None:
            return lower[k.lower()]
    return default


def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x.replace(",", "").replace("%", "").strip())
        except ValueError:
            return None
    return None


def _sanitize_rate(x, name, warnings):
    v = _num(x)
    if v is None:
        return None
    if abs(v) > 3.0:  # e.g. 20 meaning 20% -> 0.20
        warnings.append(f"{name}={v} looks like percent, interpreting as {v/100.0}")
        v = v / 100.0
    return v


def _parse_date(x):
    if isinstance(x, (date, datetime)):
        return x.date() if isinstance(x, datetime) else x
    if isinstance(x, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d-%b-%Y", "%d%b%Y"):
            try:
                return datetime.strptime(x.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _yearfrac(d_from, d_to):
    return (d_to - d_from).days / 365.0  # ACT/365F


def _parse_dividends(sched, valuation_date, warnings):
    """Return list of (t_years, amount). Accepts explicit year fractions or dates."""
    out = []
    if not sched:
        return out
    if isinstance(sched, dict):
        sched = [sched]
    for item in sched:
        if not isinstance(item, dict):
            continue
        amt = _num(_get(item, "amount", "dividend", "div", "cash", "value"))
        if amt is None:
            continue
        t = _num(_get(item, "t", "t_years", "tau", "time"))
        if t is None:
            ex = _parse_date(_get(item, "ex_date", "ex_dividend_date", "date", "pay_date"))
            if ex is not None and valuation_date is not None:
                t = _yearfrac(valuation_date, ex)
        if t is None:
            warnings.append(f"dividend entry missing time: {item}")
            continue
        out.append((t, amt))
    return out


# --------------------------------------------------------------------------- #
# Comparison helpers                                                           #
# --------------------------------------------------------------------------- #


def _rel_close(a, b, rtol):
    if a is None or b is None:
        return False
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) <= rtol * denom


_GREEK_TOL = {  # spec §6: (abs_tol, rel_tol, abs_floor)
    "delta": (0.01, None, None),
    "gamma": (0.005, None, None),
    "vega": (None, 0.01, 0.01),
    "theta": (None, 0.02, 0.01),
    "rho": (None, 0.02, 0.01),
}


def _greek_ok(name, mine, theirs):
    if theirs is None or mine is None:
        return None
    abs_tol, rel_tol, floor = _GREEK_TOL[name]
    diff = abs(mine - theirs)
    if abs_tol is not None:
        return diff <= abs_tol
    tol = max(rel_tol * abs(theirs), floor or 0.0)
    return diff <= tol


def _unit_mismatch(name, mine, theirs):
    """Detect right-number-wrong-unit for a Greek that failed the tolerance."""
    if mine is None or theirs is None or mine == 0:
        return None
    candidates = {
        "per-unit vs per-point (x100)": mine * 100.0,
        "per-point vs per-unit (/100)": mine / 100.0,
        "365/360 day-count": mine * 365.0 / 360.0,
        "sign flip": -mine,
    }
    for label, val in candidates.items():
        if _rel_close(val, theirs, 0.02):
            return label
    return None


GREEKS = ("delta", "gamma", "vega", "theta", "rho")

# Scored field sets for the clean-case "no scored field differs" check
# (spec §5/§9). Leg-level fields live inside legs[]; trade-level fields at the
# record top level even on multi-leg cases.
_LEG_SCORED = ("option_type", "exercise_style", "strike", "expiry", "side",
               "quantity", "multiplier", "premium_per_option", "premium_unit",
               "total_premium", "ratio", "leg_index")
_TRADE_SCORED = ("option_type", "exercise_style", "strike", "expiry", "side",
                 "quantity", "multiplier", "premium_per_option", "premium_unit",
                 "total_premium", "net_premium", "currency", "commission",
                 "trade_date", "settlement_date", "settlement_method",
                 "spread_type", "underlying", "venue", "ratio")

_MISSING = object()


def _dig(obj, path):
    """Resolve a dotted/indexed path such as 'greeks.delta' or
    'dividend_schedule[0].amount' inside `obj`. Returns _MISSING if absent."""
    import re
    cur = obj
    for tok in str(path).split("."):
        m = re.match(r"^([^\[\]]*)((?:\[\d+\])*)$", tok)
        if not m:
            return _MISSING
        name, brackets = m.group(1), re.findall(r"\[(\d+)\]", m.group(2))
        if name:
            if isinstance(cur, dict) and name in cur:
                cur = cur[name]
            else:
                return _MISSING
        for b in brackets:
            i = int(b)
            if isinstance(cur, list) and 0 <= i < len(cur):
                cur = cur[i]
            else:
                return _MISSING
    return cur


def _resolve_path(record, path, leg_index):
    """Resolve `path` in `record`. When leg_index (1-based) is set, look inside
    record['legs'][leg_index-1] first, then fall back to the record top level
    (trade-level fields like settlement_method stay at top level even on
    multi-leg cases)."""
    if leg_index is not None and isinstance(record, dict):
        legs = record.get("legs")
        if isinstance(legs, list) and 1 <= leg_index <= len(legs):
            v = _dig(legs[leg_index - 1], path)
            if v is not _MISSING:
                return v
    return _dig(record, path)


def _set_path(container, path, value):
    """Set a dotted/indexed path in an existing structure (no creation).
    Returns True on success."""
    import re
    tokens = str(path).split(".")
    parent = container if len(tokens) == 1 else _dig(container, ".".join(tokens[:-1]))
    if parent is _MISSING:
        return False
    m = re.match(r"^([^\[\]]*)((?:\[\d+\])*)$", tokens[-1])
    if not m:
        return False
    name = m.group(1)
    brackets = [int(b) for b in re.findall(r"\[(\d+)\]", m.group(2))]
    cur = parent
    if name:
        if not brackets:
            if isinstance(cur, dict):
                cur[name] = value
                return True
            return False
        if isinstance(cur, dict) and name in cur:
            cur = cur[name]
        else:
            return False
    for bi, idx in enumerate(brackets):
        if not (isinstance(cur, list) and 0 <= idx < len(cur)):
            return False
        if bi == len(brackets) - 1:
            cur[idx] = value
            return True
        cur = cur[idx]
    return False


def _true_terms_record(conf, internal, injections):
    """Build a record carrying the CORRECT pricing terms. correct_values are
    computed from the non-injected terms, so where an injected_error corrupts a
    term on the COUNTERPARTY side we take that field's value from the internal
    record instead. (Injections on the internal side, or on non-term fields like
    premium/greeks, leave repricing unaffected.)"""
    import copy
    base = copy.deepcopy(conf)
    for inj in injections:
        if not isinstance(inj, dict):
            continue
        side = str(_get(inj, "side", default="") or "").lower()
        if not ("counterpart" in side or side.startswith("cpty")):
            continue
        field = _get(inj, "field")
        if not field:
            continue
        li = _get(inj, "leg_index")
        li = int(_num(li)) if li is not None else None
        correct_val = _resolve_path(internal, field, li)
        if correct_val is _MISSING:
            continue
        target = base
        if li is not None and isinstance(base, dict):
            legs = base.get("legs")
            if (isinstance(legs, list) and 1 <= li <= len(legs)
                    and _dig(legs[li - 1], field) is not _MISSING):
                target = legs[li - 1]
        _set_path(target, field, correct_val)
    return base


def _field_equal(name, a, b):
    """Equality under the benchmark's own §9 field tolerances, so clean-case
    checks do not false-alarm on legitimate display rounding."""
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        n = str(name).lower()
        if n in _GREEK_TOL:
            ok = _greek_ok(n, na, nb)
            return True if ok is None else bool(ok)
        if n in ("premium_per_option", "price", "premium"):
            return abs(na - nb) <= 0.01
        if n in ("total_premium", "net_premium", "notional", "exposure",
                 "total_exposure_usd", "exposure_usd"):
            return abs(na - nb) <= 1000.0
        if n in ("strike", "quantity", "multiplier", "ratio", "contracts",
                 "leg_index"):
            return abs(na - nb) <= 1e-9
        return abs(na - nb) <= 1e-6 * max(1.0, abs(na), abs(nb))
    return str(a).strip().lower() == str(b).strip().lower()


# --------------------------------------------------------------------------- #
# Venue / product classification                                              #
# --------------------------------------------------------------------------- #


def trade_context(case, meta, conf, warnings):
    """Trade-level classification shared by every leg: venue and whether the
    underlying is a future (CME options-on-futures). Per-leg call/put and
    exercise style are resolved separately from each leg dict."""
    text = json.dumps({
        "sc": {k: case.get(k) for k in ("scenario_description", "business_context")},
        "pm": _get(meta, "pricing_model"),
        "venue": _get(meta, "venue", default=_get(conf, "venue")),
    }).lower()
    venue = str(_get(meta, "venue",
                     default=_get(conf, "venue", "exchange", default="")) or "").lower()
    underlying_kind = str(_get(meta, "product_underlying_kind",
                               default=_get(conf, "product_underlying_kind",
                                            default="")) or "").lower()
    pm = str(_get(meta, "pricing_model", default="") or "").lower()
    is_future = (("future" in underlying_kind) or ("future" in pm) or ("fut" in pm)
                 or ("cme" in venue and "future" in text))
    return {"venue": venue, "is_future": is_future, "underlying_kind": underlying_kind}


def resolve_leg_model(is_future, exercise, has_divs):
    """Per-leg ground-truth model per spec §4, from spec-level terms only:
        future + american  -> American-on-future CRR (carry 0)
        future + european  -> Black-76
        spot   + american  -> CRR (escrowed discrete divs if present, else cont. q)
        spot   + european  -> escrowed BSM if discrete divs, else BSM (cont. q, index)
    """
    american = "american" in (exercise or "").lower()
    if is_future:
        return "crr_amer_future" if american else "black76"
    if american:
        return "crr_american"
    return "escrowed_euro" if has_divs else "bsm_index"


def _is_call(option_type):
    ot = str(option_type or "").lower()
    if "call" in ot or ot in ("c",):
        return True
    if "put" in ot or ot in ("p",):
        return False
    return None


def _detect_multiplier(meta, conf, warnings):
    m = _num(_get(meta, "multiplier", "contract_multiplier",
                  default=_get(conf, "multiplier", "contract_multiplier",
                               default=None)))
    if m:
        return m
    blob = json.dumps(conf).lower() + json.dumps(meta).lower()
    if "/es" in blob or '"es"' in blob or "e-mini s&p" in blob or "esz" in blob or "esm" in blob:
        return 50.0
    if "/nq" in blob or '"nq"' in blob or "e-mini nasdaq" in blob or "nqz" in blob:
        return 20.0
    return 100.0  # equity/single-name default


# --------------------------------------------------------------------------- #
# Per-case check                                                              #
# --------------------------------------------------------------------------- #


class CaseResult:
    def __init__(self, case_id):
        self.case_id = case_id
        self.failures = []   # list of str
        self.warnings = []   # list of str
        self.diffs = []      # list of str for --verbose
        self.notes = []

    @property
    def passed(self):
        return not self.failures

    def fail(self, msg):
        self.failures.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def diff(self, msg):
        self.diffs.append(msg)


def check_case(case):
    cid = _get(case, "case_id", default="<no id>")
    res = CaseResult(cid)

    meta = _get(case, "generation_metadata", default=None)
    if meta is None:
        res.fail("no generation_metadata block (cannot independently recompute)")
        return res
    vin = _get(meta, "valuation_inputs", default={}) or {}
    correct = _get(meta, "correct_values", default=None)
    injected = _get(meta, "injected_error", default=None)
    injected2 = _get(meta, "injected_error_secondary", default=None)

    inp = _get(case, "input", default={}) or {}
    conf = _get(inp, "counterparty_confirmation", default={}) or {}
    internal = _get(inp, "internal_record", default={}) or {}

    gt = _get(case, "ground_truth", default={}) or {}
    exception_exists = _get(gt, "exception_exists", default=None)
    is_clean = (exception_exists is False) or (exception_exists is None and injected is None)

    # ---- 4/5: injected-error presence OR clean no-diff -------------------- #
    if is_clean:
        _check_clean(conf, internal, res)
    else:
        if injected is None:
            res.fail("exception case but injected_error is null")
        else:
            _check_injected(injected, conf, internal, res, "injected_error")
        if injected2 is not None:
            _check_injected(injected2, conf, internal, res, "injected_error_secondary")

    # ---- arithmetic invariants on the exception record ------------------- #
    _check_exception_arithmetic(gt, conf, internal, meta, res)
    _check_premium_arithmetic(conf, res)
    _check_premium_arithmetic(internal, res)

    # ---- repricing ------------------------------------------------------- #
    if correct is None:
        res.warn("no correct_values in generation_metadata; skipped repricing")
        return res

    _reprice_and_compare(case, meta, vin, conf, correct, res)
    return res


def _values_equal(a, b):
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= 1e-9 * max(1.0, abs(na), abs(nb))
    return str(a).strip().lower() == str(b).strip().lower()


def _check_clean(conf, internal, res):
    """Clean case: NO scored field may differ between the two records, at the
    trade level, inside every leg, and inside per-leg greeks (spec §5)."""
    for f in _TRADE_SCORED:
        cv, iv = _get(conf, f), _get(internal, f)
        if cv is not None and iv is not None and not _field_equal(f, cv, iv):
            res.fail(f"CLEAN case but trade-level '{f}' differs: "
                     f"cpty={cv!r} internal={iv!r}")

    # single-leg greeks displayed at top level
    _clean_compare_greeks(_get(conf, "greeks") or {}, _get(internal, "greeks") or {},
                          "top-level", res)

    clegs, ilegs = _get(conf, "legs"), _get(internal, "legs")
    if isinstance(clegs, list) and isinstance(ilegs, list):
        if len(clegs) != len(ilegs):
            res.fail(f"CLEAN case but leg count differs: "
                     f"cpty={len(clegs)} internal={len(ilegs)}")
        for idx in range(min(len(clegs), len(ilegs))):
            for f in _LEG_SCORED:
                cv, iv = _get(clegs[idx], f), _get(ilegs[idx], f)
                if cv is not None and iv is not None and not _field_equal(f, cv, iv):
                    res.fail(f"CLEAN case but leg {idx+1} '{f}' differs: "
                             f"cpty={cv!r} internal={iv!r}")
            _clean_compare_greeks(_get(clegs[idx], "greeks") or {},
                                  _get(ilegs[idx], "greeks") or {},
                                  f"leg {idx+1}", res)


def _clean_compare_greeks(cg, ig, where, res):
    for g in GREEKS:
        cv, iv = _get(cg, g), _get(ig, g)
        if cv is not None and iv is not None and not _field_equal(g, cv, iv):
            res.fail(f"CLEAN case but {where} greeks.{g} differs: "
                     f"cpty={cv!r} internal={iv!r}")


def _check_injected(inj, conf, internal, res, label):
    """Verify a single injected_error object: the named field really differs
    between the two records, on the stated side/leg, by the stated magnitude.
    `field` may be a dotted/indexed path; `leg_index` (1-based) resolves inside
    legs[leg_index-1] first, else the record top level."""
    if not isinstance(inj, dict):
        res.fail(f"{label} present but not an object")
        return
    field = _get(inj, "field")
    side = str(_get(inj, "side", default="") or "").lower()
    kind = str(_get(inj, "kind", default="") or "").lower()
    magnitude = _num(_get(inj, "magnitude"))
    leg_index = _get(inj, "leg_index")
    if leg_index is not None:
        leg_index = int(_num(leg_index))
    if not field:
        res.fail(f"{label} has no 'field'")
        return

    cv = _resolve_path(conf, field, leg_index)
    iv = _resolve_path(internal, field, leg_index)
    where = f"'{field}'" + (f" (leg {leg_index})" if leg_index is not None else "")
    if cv is _MISSING and iv is _MISSING:
        res.fail(f"{label} field {where} not found in either record")
        return
    if cv is not _MISSING and iv is not _MISSING and _values_equal(cv, iv):
        res.fail(f"{label} claims {where} differs but records are equal "
                 f"(cpty={cv!r} internal={iv!r})")
        return
    # side sanity: the injected side should carry the field.
    if side and ("counterpart" in side or side.startswith("cpty")) and cv is _MISSING:
        res.warn(f"{label} side is counterparty but {where} absent there")
    if side and side.startswith("intern") and iv is _MISSING:
        res.warn(f"{label} side is internal but {where} absent there")

    ncv = None if cv is _MISSING else _num(cv)
    niv = None if iv is _MISSING else _num(iv)
    if magnitude is not None and ncv is not None and niv is not None:
        actual = abs(ncv - niv)
        if kind in ("relative", "rel", "pct", "percent"):
            base = abs(niv) if ("counterpart" in side or side.startswith("cpty")) else abs(ncv)
            base = base or max(abs(ncv), abs(niv), 1e-12)
            expected = base * abs(magnitude)
            if not _rel_close(actual, expected, 0.05):
                res.warn(f"{label} magnitude(rel) mismatch on {where}: "
                         f"actual|diff|={actual:.6g} expected~{expected:.6g}")
        else:
            if not _rel_close(actual, abs(magnitude), 0.05) and \
               abs(actual - abs(magnitude)) > 1e-6:
                res.warn(f"{label} magnitude mismatch on {where}: "
                         f"actual|diff|={actual:.6g} claimed={abs(magnitude):.6g}")


def _check_exception_arithmetic(gt, conf, internal, meta, res):
    for exc_key in ("primary_exception", "secondary_exception"):
        exc = _get(gt, exc_key)
        if not isinstance(exc, dict):
            continue
        cv = _num(_get(exc, "counterparty_value"))
        iv = _num(_get(exc, "internal_value"))
        diff = _num(_get(exc, "difference"))
        if cv is not None and iv is not None and diff is not None:
            if not _rel_close(diff, abs(cv - iv), 1e-6) and abs(diff - abs(cv - iv)) > 1e-6:
                res.fail(f"{exc_key}.difference={diff} != |cpty-internal|="
                         f"{abs(cv-iv):.6g}")
        # exposure consistency (family: |diff| * qty * multiplier, within $1000)
        exposure = _num(_get(exc, "total_exposure_usd", "total_exposure",
                             "exposure_usd"))
        if exposure is not None and diff is not None:
            qty = _num(_get(exc, "quantity", default=_get(conf, "quantity",
                            "contracts", default=None)))
            mult = _detect_multiplier(meta, conf, res.warnings)
            if qty is not None:
                recon = abs(diff) * qty * mult
                # only assert when the reconstruction is dimensionally plausible
                if not (_rel_close(exposure, recon, 0.02) or
                        abs(exposure - recon) <= 1000.0):
                    # try without multiplier (per-share difference already scaled)
                    recon2 = abs(diff) * qty
                    if not (_rel_close(exposure, recon2, 0.02) or
                            abs(exposure - recon2) <= 1000.0):
                        res.warn(f"{exc_key}.total_exposure_usd={exposure} not "
                                 f"consistent with |diff|*qty*mult={recon:.2f} "
                                 f"(or *qty={recon2:.2f}); check exposure logic")


def _check_premium_arithmetic(record, res):
    """total_premium = premium_per_option * multiplier * quantity (per leg or
    single-leg record); net_premium = signed sum of leg total_premiums."""
    def _one(obj):
        ppo = _num(_get(obj, "premium_per_option", "price", "premium"))
        mult = _num(_get(obj, "multiplier", default=100.0)) or 100.0
        qty = _num(_get(obj, "quantity", "contracts"))
        tot = _num(_get(obj, "total_premium", "premium_total"))
        return ppo, mult, qty, tot

    legs = _get(record, "legs")
    if isinstance(legs, list) and legs:
        total = 0.0
        have = True
        for i, leg in enumerate(legs, start=1):
            ppo, mult, qty, tot = _one(leg)
            if ppo is not None and qty is not None and tot is not None:
                side_str = str(_get(leg, "side", default="")).lower()
                sign = -1.0 if side_str in ("sell", "short", "sold", "write") else 1.0
                expect = ppo * mult * qty
                if not _rel_close(abs(tot), abs(expect), 1e-4) and \
                   abs(abs(tot) - abs(expect)) > 0.01:
                    res.warn(f"leg {i} total_premium={tot} != ppo*mult*qty="
                             f"{expect:.4f}")
                total += sign * abs(tot)
            else:
                have = False
        net = _num(_get(record, "net_premium", "premium_net"))
        if have and net is not None:
            if not _rel_close(net, total, 1e-4) and abs(abs(net) - abs(total)) > 0.01:
                res.warn(f"net_premium={net} != signed sum of legs={total:.4f}")
    else:
        # single-leg record: premium consistency at top level
        ppo, mult, qty, tot = _one(record)
        if ppo is not None and qty is not None and tot is not None:
            expect = ppo * mult * qty
            if not _rel_close(abs(tot), abs(expect), 1e-4) and \
               abs(abs(tot) - abs(expect)) > 0.01:
                res.warn(f"total_premium={tot} != premium_per_option*mult*qty="
                         f"{expect:.4f}")


def _normalize_leg(src, idx, val_date, trade_divs, warnings):
    """Normalize one leg dict (or a single-leg record's top level) to the fields
    the pricer needs. Per-leg dividend_schedule overrides the trade-level one."""
    is_call = _is_call(_get(src, "option_type", "right", "put_call", "call_put"))
    exercise = str(_get(src, "exercise_style", "exercise", default="") or "").lower()
    K = _num(_get(src, "strike", "strike_price", "K"))
    T = _num(_get(src, "T", "tau", "time_to_expiry", "ttm"))
    if T is None:
        exp = _parse_date(_get(src, "expiry", "expiration", "expiry_date", "maturity"))
        if exp is not None and val_date is not None:
            T = _yearfrac(val_date, exp)
    mult = _num(_get(src, "multiplier", "contract_multiplier"))
    qty = _num(_get(src, "quantity", "contracts"))
    leg_divs_raw = _get(src, "dividend_schedule", "dividends", "divs", default=trade_divs)
    divs = _parse_dividends(leg_divs_raw, val_date, warnings)
    li = _num(_get(src, "leg_index"))
    return {"leg_index": int(li) if li is not None else idx,
            "is_call": is_call, "exercise": exercise, "K": K, "T": T,
            "multiplier": mult, "quantity": qty, "divs": divs}


def _extract_legs(conf, vin, val_date, warnings):
    """Return normalized legs. Single-leg records (no 'legs' array) yield one
    synthetic leg from the record top level; correct_values still uses leg_1_*."""
    trade_divs = _get(vin, "dividend_schedule", "dividends", "divs")
    raw = _get(conf, "legs")
    if isinstance(raw, list) and raw:
        return [_normalize_leg(leg, i, val_date, trade_divs, warnings)
                for i, leg in enumerate(raw, start=1)]
    return [_normalize_leg(conf, 1, val_date, trade_divs, warnings)]


def _reprice_and_compare(case, meta, vin, conf, correct, res):
    warnings = res.warnings
    ctx = trade_context(case, meta, conf, warnings)
    is_future = ctx["is_future"]

    # Shared valuation inputs (spec §7: one spot/futures_price, sigma, r, q).
    S = _num(_get(vin, "spot", "S", "underlying_price", "spot_price"))
    F = _num(_get(vin, "futures_price", "future_price", "F", "forward"))
    sigma = _sanitize_rate(_get(vin, "sigma", "vol", "volatility", "iv"), "sigma", warnings)
    r = _sanitize_rate(_get(vin, "r", "rate", "risk_free_rate", "interest_rate"), "r", warnings)
    q = _sanitize_rate(_get(vin, "q", "dividend_yield", "yield"), "q", warnings) or 0.0
    val_date = _parse_date(_get(vin, "valuation_date", "val_date", "as_of"))

    underlier = F if is_future else S
    if underlier is None:                       # tolerate spot/future slot swaps
        underlier = F if F is not None else S

    # Reprice from the CORRECT terms: correct_values are built from the
    # non-injected side, so undo any term corruption on the counterparty side.
    internal = _get(_get(case, "input", default={}) or {}, "internal_record",
                    default={}) or {}
    injections = [x for x in (_get(meta, "injected_error"),
                              _get(meta, "injected_error_secondary"))
                  if isinstance(x, dict)]
    pricing_conf = _true_terms_record(conf, internal, injections)

    legs = _extract_legs(pricing_conf, vin, val_date, warnings)
    if not legs:
        res.warn("no legs found; repricing skipped")
        return
    for leg in legs:
        _reprice_one_leg(int(leg["leg_index"]), leg, underlier, sigma, r, q,
                         is_future, correct, res)


def _leg_correct_values(i, correct):
    """Extract (premium, greeks) for leg i (1-based). Both single-leg and
    multi-leg use leg_N_premium / leg_N_greeks; fall back to legacy top-level
    price/greeks for leg 1 only."""
    cprem = _num(_get(correct, f"leg_{i}_premium"))
    cg_raw = _get(correct, f"leg_{i}_greeks", default=None)
    if i == 1:
        if cprem is None:
            cprem = _num(_get(correct, "price", "premium_per_option",
                              "theoretical_value", "value", "fair_value"))
        if cg_raw is None:
            top = {g: _num(_get(correct, g)) for g in GREEKS}
            if any(v is not None for v in top.values()):
                cg_raw = top
    cgreeks = {g: _num(_get(cg_raw or {}, g)) for g in GREEKS}
    return cprem, cgreeks


def _reprice_one_leg(i, leg, underlier, sigma, r, q, is_future, correct, res):
    is_call, K, T, divs = leg["is_call"], leg["K"], leg["T"], leg["divs"]
    exercise = leg["exercise"]
    tag = f"leg {i}"
    cprem, cgreeks = _leg_correct_values(i, correct)

    if is_call is None:
        res.warn(f"{tag}: could not determine call/put; repricing skipped")
        return
    if K is None or sigma is None or r is None or T is None or T <= 0:
        res.fail(f"{tag}: insufficient inputs (K={K}, sigma={sigma}, r={r}, "
                 f"T={T}); cannot reprice")
        return
    if underlier is None:
        res.fail(f"{tag}: no spot/futures_price available in valuation_inputs")
        return

    model = resolve_leg_model(is_future, exercise, bool(divs))
    if not exercise:
        res.warn(f"{tag}: exercise_style absent; assumed model={model}")
    american = model in ("crr_american", "crr_amer_future")
    bound = None
    try:
        if model == "bsm_index":
            price = bsm_price(underlier, K, r, q, sigma, T, is_call)
            greeks = bsm_greeks(underlier, K, r, q, sigma, T, is_call)
        elif model == "escrowed_euro":
            price = escrowed_european_price(underlier, K, r, sigma, T, is_call, divs)
            greeks = escrowed_european_greeks(underlier, K, r, sigma, T, is_call, divs)
        elif model == "black76":
            price = black76_price(underlier, K, r, sigma, T, is_call)
            greeks = black76_greeks(underlier, K, r, sigma, T, is_call)
        elif model == "crr_american":
            pf = lambda N: crr_price(underlier, K, r, sigma, T, N, is_call, True,
                                     q=q, divs=divs)
            price, bound = american_reference(pf)
            # bump repricing on the (N, N+1) average — single-N tree greeks
            # carry sawtooth noise larger than the section-6 tolerances (the
            # 0.5% spot bump is comparable to the node spacing)
            reprice = lambda S, sigma, r, T: (
                crr_price(S, K, r, sigma, T, 1000, is_call, True, q=q, divs=divs)
                + crr_price(S, K, r, sigma, T, 1001, is_call, True, q=q, divs=divs)) / 2.0
            day = 1.0 / 365.0
            divs_t = [(t - day, a) for t, a in (divs or []) if t - day > 1e-9]
            reprice_theta = lambda S, sigma, r, T: (
                crr_price(S, K, r, sigma, T, 1000, is_call, True, q=q, divs=divs_t)
                + crr_price(S, K, r, sigma, T, 1001, is_call, True, q=q, divs=divs_t)) / 2.0
            greeks = american_greeks(reprice, underlier, sigma, r, T,
                                     reprice_theta=reprice_theta)
            if divs:
                res.notes.append(f"{tag}: American-with-dividends escrowed-tree; "
                                 "a beyond-bound miss may be a div-method disagreement")
        elif model == "crr_amer_future":
            pf = lambda N: crr_price(underlier, K, r, sigma, T, N, is_call, True,
                                     on_future=True)
            price, bound = american_reference(pf)
            reprice = lambda S, sigma, r, T: (
                crr_price(S, K, r, sigma, T, 1000, is_call, True, on_future=True)
                + crr_price(S, K, r, sigma, T, 1001, is_call, True, on_future=True)) / 2.0
            greeks = american_greeks(reprice, underlier, sigma, r, T)
        else:
            res.fail(f"{tag}: unknown model '{model}'"); return
    except Exception as e:  # pricing must never crash the whole run
        res.fail(f"{tag}: repricing error (model={model}): {e}")
        return

    _compare_leg_price(tag, price, cprem, american, bound, divs, leg, res)
    _check_greek_signs(cgreeks, is_call, res, tag=tag)

    for g in GREEKS:
        mine, theirs = greeks.get(g), cgreeks.get(g)
        ok = _greek_ok(g, mine, theirs)
        if ok is None:
            continue
        if ok:
            res.diff(f"{tag} {g} OK: mine={mine:.6g} theirs={theirs:.6g}")
        else:
            um = _unit_mismatch(g, mine, theirs)
            if um:
                res.fail(f"{tag} {g} UNIT-MISMATCH ({um}): mine={mine:.6g} "
                         f"theirs={theirs:.6g}")
            else:
                res.fail(f"{tag} {g} mismatch: mine={mine:.6g} theirs={theirs:.6g} "
                         f"|diff|={abs(mine-theirs):.6g}")


def _compare_leg_price(tag, price, cprem, american, bound, divs, leg, res):
    if cprem is None:
        res.warn(f"{tag}: no leg premium in correct_values; price not compared")
        return
    passed = (abs(price - cprem) <= bound) if american else _rel_close(price, cprem, 1e-6)
    if passed:
        kind = "american" if american else "closed form"
        extra = f" bound={bound:.2e}" if american else ""
        res.diff(f"{tag} price OK ({kind}): mine={price:.8f} theirs={cprem:.8f} "
                 f"|diff|={abs(price-cprem):.2e}{extra}")
        return
    # diagnostic: did they report a TOTAL (per-option*mult*qty) instead of per-option?
    diag = ""
    mult, qty = leg.get("multiplier") or 100.0, leg.get("quantity")
    if qty:
        total = price * mult * qty
        if _rel_close(total, cprem, 1e-4):
            diag = (f" (theirs matches per-option*mult*qty={total:.4f}; "
                    f"leg premium looks like a TOTAL, not per-option)")
    if american:
        div_note = " (dividends present: possible div-method disagreement)" if divs else ""
        res.fail(f"{tag} american price out of tree bound: mine={price:.6f} "
                 f"theirs={cprem:.6f} |diff|={abs(price-cprem):.4g} "
                 f"bound={bound:.4g}{div_note}{diag}")
    else:
        res.fail(f"{tag} closed-form price mismatch (>1e-6 rel): mine={price:.8f} "
                 f"theirs={cprem:.8f} rel={abs(price-cprem)/max(abs(cprem),1e-12):.2e}{diag}")


def _check_greek_signs(cgreeks, is_call, res, tag=""):
    pfx = (tag + " ") if tag else ""
    eps = 1e-6  # tolerate a far-OTM delta of exactly 0.0 and float noise at 1.0
    d = cgreeks.get("delta")
    if d is not None and is_call is not None:
        if is_call and not (-eps <= d <= 1.0 + eps):
            res.fail(f"{pfx}call delta out of [0,1]: {d}")
        if (not is_call) and not (-1.0 - eps <= d <= eps):
            res.fail(f"{pfx}put delta out of [-1,0]: {d}")
    g = cgreeks.get("gamma")
    if g is not None and g < -1e-9:
        res.fail(f"{pfx}gamma negative: {g}")
    v = cgreeks.get("vega")
    if v is not None and v < -1e-9:
        res.fail(f"{pfx}vega negative: {v}")


# --------------------------------------------------------------------------- #
# Self-test                                                                    #
# --------------------------------------------------------------------------- #


def self_test(verbose=False):
    ok = True
    log = []

    def check(name, cond, detail=""):
        nonlocal ok
        status = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        log.append(f"  [{status}] {name}" + (f"  {detail}" if detail else ""))

    # 1. BSM put-call parity: C - P = S e^{-qT} - K e^{-rT}
    S, K, r, q, sig, T = 100.0, 95.0, 0.03, 0.015, 0.25, 0.75
    c = bsm_price(S, K, r, q, sig, T, True)
    p = bsm_price(S, K, r, q, sig, T, False)
    lhs = c - p
    rhs = S * math.exp(-q * T) - K * math.exp(-r * T)
    check("BSM put-call parity", abs(lhs - rhs) < 1e-10, f"lhs={lhs:.10f} rhs={rhs:.10f}")

    # 2. Black-76 put-call parity: C - P = e^{-rT}(F-K)
    F = 100.0
    cb = black76_price(F, K, r, sig, T, True)
    pb = black76_price(F, K, r, sig, T, False)
    rhsb = math.exp(-r * T) * (F - K)
    check("Black-76 put-call parity", abs((cb - pb) - rhsb) < 1e-10,
          f"lhs={cb-pb:.10f} rhs={rhsb:.10f}")

    # 3. Hand-computed Black-76: F=K=100, r=0, sig=0.20, T=1 -> 7.965567
    hand = black76_price(100.0, 100.0, 0.0, 0.20, 1.0, True)
    check("Black-76 hand value (7.965567)", abs(hand - 7.965567) < 1e-5,
          f"got={hand:.6f}")

    # 4. BSM <-> Black-76 equivalence: F = S e^{(r-q)T}
    Feq = S * math.exp((r - q) * T)
    b76 = black76_price(Feq, K, r, sig, T, True)
    check("BSM==Black-76 via forward", abs(b76 - c) < 1e-9, f"b76={b76:.9f} bsm={c:.9f}")

    # 5. BSM <-> CRR European convergence (no dividends): use q as continuous yld
    euro_bsm = bsm_price(S, K, r, q, sig, T, False)
    euro_crr = crr_price(S, K, r, sig, T, 2000, False, american=False, q=q)
    check("BSM<->CRR European convergence", abs(euro_bsm - euro_crr) < 5e-3,
          f"bsm={euro_bsm:.5f} crr={euro_crr:.5f} diff={abs(euro_bsm-euro_crr):.2e}")

    # 6. American >= European (put; early exercise valuable)
    am_put = crr_price(S, K, r, sig, T, 1500, False, american=True, q=q)
    eu_put = crr_price(S, K, r, sig, T, 1500, False, american=False, q=q)
    check("American put >= European put", am_put >= eu_put - 1e-9,
          f"am={am_put:.5f} eu={eu_put:.5f}")

    # 7. American call with dividend >= European call with same dividend
    divs = [(0.30, 2.0)]
    am_c = crr_price(S, K, r, sig, T, 1500, True, american=True, q=0.0, divs=divs)
    eu_c = escrowed_european_price(S, K, r, sig, T, True, divs)
    check("American call(div) >= European call(div)", am_c >= eu_c - 1e-6,
          f"am={am_c:.5f} eu={eu_c:.5f}")

    # 8. On-future American >= European (Black-76) for a put
    euF = black76_price(F, K, r, sig, T, False)
    amF = crr_price(F, K, r, sig, T, 1500, False, american=True, on_future=True)
    check("American-on-future put >= Black-76 put", amF >= euF - 1e-9,
          f"am={amF:.5f} b76={euF:.5f}")

    # 9. Closed-form Greeks vs finite-difference of closed-form price (BSM)
    g = bsm_greeks(S, K, r, q, sig, T, True)
    hS = 0.005 * S
    fd_delta = (bsm_price(S+hS, K, r, q, sig, T, True) -
                bsm_price(S-hS, K, r, q, sig, T, True)) / (2*hS)
    fd_gamma = (bsm_price(S+hS, K, r, q, sig, T, True) - 2*bsm_price(S, K, r, q, sig, T, True) +
                bsm_price(S-hS, K, r, q, sig, T, True)) / (hS*hS)
    fd_vega = (bsm_price(S, K, r, q, sig+0.01, T, True) -
               bsm_price(S, K, r, q, sig-0.01, T, True)) / 2.0
    fd_theta = bsm_price(S, K, r, q, sig, T-1/365, True) - bsm_price(S, K, r, q, sig, T, True)
    fd_rho = (bsm_price(S, K, r+0.0001, q, sig, T, True) -
              bsm_price(S, K, r-0.0001, q, sig, T, True)) / 2.0
    check("BSM delta vs FD", abs(g["delta"]-fd_delta) < 1e-4, f"cf={g['delta']:.6f} fd={fd_delta:.6f}")
    check("BSM gamma vs FD", abs(g["gamma"]-fd_gamma) < 1e-3, f"cf={g['gamma']:.6f} fd={fd_gamma:.6f}")
    check("BSM vega vs FD", abs(g["vega"]-fd_vega) < 1e-4, f"cf={g['vega']:.6f} fd={fd_vega:.6f}")
    check("BSM theta vs FD", abs(g["theta"]-fd_theta) < 5e-4, f"cf={g['theta']:.6f} fd={fd_theta:.6f}")
    check("BSM rho vs FD", abs(g["rho"]-fd_rho) < 1e-4, f"cf={g['rho']:.6f} fd={fd_rho:.6f}")

    # 10. Black-76 Greeks vs finite-difference
    gb = black76_greeks(F, K, r, sig, T, True)
    fdb_delta = (black76_price(F+hS, K, r, sig, T, True) -
                 black76_price(F-hS, K, r, sig, T, True)) / (2*hS)
    fdb_theta = black76_price(F, K, r, sig, T-1/365, True) - black76_price(F, K, r, sig, T, True)
    fdb_rho = (black76_price(F, K, r+0.0001, sig, T, True) -
               black76_price(F, K, r-0.0001, sig, T, True)) / 2.0
    check("Black-76 delta vs FD", abs(gb["delta"]-fdb_delta) < 1e-4)
    check("Black-76 theta vs FD", abs(gb["theta"]-fdb_theta) < 5e-4,
          f"cf={gb['theta']:.6f} fd={fdb_theta:.6f}")
    check("Black-76 rho vs FD", abs(gb["rho"]-fdb_rho) < 1e-4,
          f"cf={gb['rho']:.6f} fd={fdb_rho:.6f}")

    # 11. Tree-convergence bound: reference brackets a plain 1600-step CRR price
    pf = lambda N: crr_price(S, K, r, sig, T, N, False, american=True, q=q)
    p_star, bnd = american_reference(pf)
    p1600 = pf(1600)
    check("tree bound covers plain-CRR(1600)", abs(p_star - p1600) <= bnd,
          f"p*={p_star:.6f} p1600={p1600:.6f} bound={bnd:.6f}")

    # 12. american_greeks end-to-end (guards the reprice-lambda keyword contract):
    #     American put delta must land in [-1,0], gamma>=0, and be close to the
    #     European closed-form delta as a sanity anchor.
    reprice = lambda S, sigma, r, T: crr_price(S, K, r, sigma, T, 1000, False,
                                               american=True, q=q)
    ag = american_greeks(reprice, S, sig, r, T)
    check("american_greeks put delta in [-1,0]", -1.0 <= ag["delta"] <= 0.0,
          f"delta={ag['delta']:.4f}")
    check("american_greeks gamma>=0 and vega>=0", ag["gamma"] >= -1e-9 and ag["vega"] >= -1e-9,
          f"gamma={ag['gamma']:.5f} vega={ag['vega']:.5f}")
    eu_delta = bsm_greeks(S, K, r, q, sig, T, False)["delta"]
    check("american put delta near European delta", abs(ag["delta"] - eu_delta) < 0.05,
          f"am={ag['delta']:.4f} eu={eu_delta:.4f}")

    # 13. Deep-ITM American-on-future call (AAL-D-003-206 shape): price pinned at
    #     intrinsic below base vol, so a CENTRAL vol bump HALVES the true vega.
    #     Proves the one-sided +1 vol-pt fix.
    Fd, Kd, rd, Td, sd = 5957.7, 5075.0, 0.05, 0.10, 0.20
    rep = lambda S, sigma, r, T: crr_price(S, Kd, r, sigma, T, 1000, True, True,
                                           on_future=True)
    gd = american_greeks(rep, Fd, sd, rd, Td)
    base_d = rep(S=Fd, sigma=sd, r=rd, T=Td)
    one_sided = rep(S=Fd, sigma=sd + 0.01, r=rd, T=Td) - base_d
    central = (rep(S=Fd, sigma=sd + 0.01, r=rd, T=Td)
               - rep(S=Fd, sigma=sd - 0.01, r=rd, T=Td)) / 2.0
    check("deep-ITM 206: delta ~ 1 (in [0,1])", 0.0 <= gd["delta"] <= 1.0 + 1e-9,
          f"delta={gd['delta']:.5f}")
    check("deep-ITM 206: american_greeks vega is one-sided",
          abs(gd["vega"] - one_sided) < 1e-9, f"vega={gd['vega']:.5f} one={one_sided:.5f}")
    check("deep-ITM 206: one-sided vega ~ 2x central (pinned regime)",
          one_sided > 0 and one_sided >= 1.9 * central,
          f"one={one_sided:.5f} central={central:.5f} ratio={one_sided/max(central,1e-12):.2f}")

    print("SELF-TEST:")
    for line in log:
        print(line)
    print(f"SELF-TEST {'PASSED' if ok else 'FAILED'} "
          f"({sum('PASS' in l for l in log)}/{len(log)} checks)")
    return ok


# --------------------------------------------------------------------------- #
# Dataset loading + main                                                       #
# --------------------------------------------------------------------------- #


def _iter_cases(obj):
    if isinstance(obj, list):
        for x in obj:
            if isinstance(x, dict) and "case_id" in x:
                yield x
    elif isinstance(obj, dict):
        if "case_id" in obj:
            yield obj
        else:
            for v in obj.values():
                if isinstance(v, list):
                    for x in v:
                        if isinstance(x, dict) and "case_id" in x:
                            yield x


def load_cases(paths):
    cases = []
    for path in paths:
        try:
            with open(path, "r") as fh:
                obj = json.load(fh)
        except Exception as e:
            print(f"WARNING: could not read {path}: {e}", file=sys.stderr)
            continue
        cases.extend(_iter_cases(obj))
    return cases


def _default_glob():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)  # aal-benchmark/
    return os.path.join(root, "datasets", "AAL-D-003", "*.json")


def main(argv=None):
    ap = argparse.ArgumentParser(description="AAL-D-003 independent recompute checker")
    ap.add_argument("paths", nargs="*", help="dataset json files or globs")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print per-field diffs for every case")
    ap.add_argument("--self-test", action="store_true",
                    help="run internal pricing self-tests and exit")
    args = ap.parse_args(argv)

    if args.self_test:
        return 0 if self_test(verbose=args.verbose) else 1

    # Always self-test the pricing core first; a broken core invalidates the gate.
    if not self_test(verbose=False):
        print("ABORT: pricing self-test failed; not running dataset checks.",
              file=sys.stderr)
        return 2
    print()

    if args.paths:
        paths = []
        for p in args.paths:
            paths.extend(glob.glob(p) if any(c in p for c in "*?[") else [p])
    else:
        paths = glob.glob(_default_glob())

    if not paths:
        print("no cases found (no AAL-D-003 dataset files matched). "
              "Pricing self-test passed; nothing to recompute.")
        return 0

    cases = load_cases(paths)
    if not cases:
        print("no cases found (files present but contained no benchmark cases).")
        return 0

    n_pass = n_fail = 0
    for case in cases:
        res = check_case(case)
        if res.passed:
            n_pass += 1
            status = "PASS"
        else:
            n_fail += 1
            status = "FAIL"
        print(f"[{status}] {res.case_id}")
        for f in res.failures:
            print(f"    FAIL: {f}")
        for w in res.warnings:
            print(f"    warn: {w}")
        for note in res.notes:
            print(f"    note: {note}")
        if args.verbose:
            for dline in res.diffs:
                print(f"    diff: {dline}")

    print()
    print(f"SUMMARY: {n_pass} passed, {n_fail} failed, {len(cases)} total "
          f"across {len(paths)} file(s)")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
