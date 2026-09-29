"""
AAL shared telemetry — per-case latency, token, and cost instrumentation.

Motivation (see AAL-D-006 run sheet, "METHODOLOGY GAP"): every AAL benchmark to
date measures accuracy only. For capital-markets operations that is a material
omission — a model at 95% accuracy that needs 60s per case is unusable inside an
intraday margin cycle or a T+1 settlement window. Buyers evaluate "accurate
enough, fast enough, cheap enough"; we measured one of three.

This module adds the other two. It is deliberately dependency-free and
backward compatible: records written without telemetry simply aggregate to
`n_timed: 0` and are omitted from the performance block.

Usage in a driver:

    from aal_telemetry import Timer, extract_usage, price_for, summarize

    with Timer() as t:
        resp = client.chat.completions.create(...)
    tel = extract_usage(resp)
    tel["latency_s"] = t.elapsed
    tel["attempts"]  = attempt + 1
    ...
    rec["runs"].append({"prediction": pred, "score": ..., "telemetry": tel})

    # at the end
    sc["performance"] = summarize(records, price_in=..., price_out=...)

Design notes
------------
* p50 and p95 are reported, not just mean. Tail latency is what breaks an
  operational window; a good mean with a bad p95 is still unusable.
* Cost is computed from *observed* token counts at list price supplied by the
  caller — never estimated from character counts.
* Latency is wall-clock around the API call only, excluding our own retry
  sleeps and inter-case `--sleep`, so it measures the model, not the harness.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class Timer:
    """Context manager measuring wall-clock seconds around a block."""

    def __enter__(self) -> "Timer":
        self._t0 = time.perf_counter()
        self.elapsed = 0.0
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed = time.perf_counter() - self._t0


def extract_usage(resp: Any) -> Dict[str, Optional[int]]:
    """Pull token counts off an OpenAI-compatible response. Never raises."""
    out: Dict[str, Optional[int]] = {
        "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
        "reasoning_tokens": None,
    }
    usage = getattr(resp, "usage", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage")
    if usage is None:
        return out

    def g(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    out["prompt_tokens"] = g(usage, "prompt_tokens") or g(usage, "promptTokenCount")
    out["completion_tokens"] = g(usage, "completion_tokens") or g(usage, "candidatesTokenCount")
    out["total_tokens"] = g(usage, "total_tokens") or g(usage, "totalTokenCount")

    # reasoning models expose hidden thinking tokens separately; capture when present
    details = g(usage, "completion_tokens_details")
    if details is not None:
        out["reasoning_tokens"] = g(details, "reasoning_tokens")
    return out


def price_for(tel: Dict[str, Any], price_in: Optional[float],
              price_out: Optional[float]) -> Optional[float]:
    """USD cost for one call. Prices are $ per 1M tokens. None if unpriced."""
    if price_in is None and price_out is None:
        return None
    pt = tel.get("prompt_tokens") or 0
    ct = tel.get("completion_tokens") or 0
    return round((pt / 1_000_000.0) * (price_in or 0.0)
                 + (ct / 1_000_000.0) * (price_out or 0.0), 6)


def _pct(sorted_vals: List[float], q: float) -> float:
    """Nearest-rank percentile. q in [0,1]."""
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def summarize(records: List[Dict[str, Any]], price_in: Optional[float] = None,
              price_out: Optional[float] = None) -> Dict[str, Any]:
    """Aggregate telemetry across all (case, run) records.

    Returns a block safe to drop straight into the scorecard JSON. Records
    lacking telemetry are ignored, so this is safe on older result files.
    """
    lat: List[float] = []
    pt_tot = ct_tot = rt_tot = 0
    n_tok = 0
    cost_tot = 0.0
    n_cost = 0
    attempts_tot = 0
    n_att = 0

    for rec in records:
        for run in rec.get("runs", []):
            tel = run.get("telemetry")
            if not tel:
                continue
            if isinstance(tel.get("latency_s"), (int, float)):
                lat.append(float(tel["latency_s"]))
            if tel.get("prompt_tokens") is not None:
                pt_tot += tel.get("prompt_tokens") or 0
                ct_tot += tel.get("completion_tokens") or 0
                rt_tot += tel.get("reasoning_tokens") or 0
                n_tok += 1
            c = tel.get("cost_usd")
            if c is None:
                c = price_for(tel, price_in, price_out)
            if c is not None:
                cost_tot += c
                n_cost += 1
            if isinstance(tel.get("attempts"), int):
                attempts_tot += tel["attempts"]
                n_att += 1

    if not lat and not n_tok:
        return {"n_timed": 0,
                "note": "No telemetry captured. Re-run with an instrumented driver."}

    lat_sorted = sorted(lat)
    block: Dict[str, Any] = {"n_timed": len(lat)}

    if lat_sorted:
        block["latency_s"] = {
            "p50": round(_pct(lat_sorted, 0.50), 3),
            "p95": round(_pct(lat_sorted, 0.95), 3),
            "mean": round(sum(lat_sorted) / len(lat_sorted), 3),
            "max": round(lat_sorted[-1], 3),
            "total": round(sum(lat_sorted), 1),
        }

    if n_tok:
        block["tokens"] = {
            "prompt_mean": round(pt_tot / n_tok, 1),
            "completion_mean": round(ct_tot / n_tok, 1),
            "reasoning_mean": round(rt_tot / n_tok, 1) if rt_tot else None,
            "prompt_total": pt_tot,
            "completion_total": ct_tot,
        }

    if n_cost:
        block["cost_usd"] = {
            "per_call_mean": round(cost_tot / n_cost, 6),
            "run_total": round(cost_tot, 4),
            "priced_calls": n_cost,
            "price_in_per_1m": price_in,
            "price_out_per_1m": price_out,
        }

    if n_att:
        block["attempts_mean"] = round(attempts_tot / n_att, 3)

    return block


def print_performance(block: Dict[str, Any]) -> None:
    """Console summary to sit under the accuracy scorecard."""
    if not block or not block.get("n_timed"):
        return
    lat = block.get("latency_s") or {}
    print("\n--- Performance ---")
    if lat:
        print(f"{'latency p50 / p95':<32}{lat.get('p50')}s / {lat.get('p95')}s"
              f"   (max {lat.get('max')}s, total {lat.get('total')}s)")
    tok = block.get("tokens") or {}
    if tok:
        line = f"{tok.get('prompt_mean')} in / {tok.get('completion_mean')} out"
        if tok.get("reasoning_mean"):
            line += f"   (reasoning {tok['reasoning_mean']})"
        print(f"{'tokens per call (mean)':<32}{line}")
    cost = block.get("cost_usd") or {}
    if cost:
        print(f"{'cost per call / run total':<32}${cost.get('per_call_mean')} / "
              f"${cost.get('run_total')}")
    if block.get("attempts_mean"):
        print(f"{'attempts per call (mean)':<32}{block['attempts_mean']}")
