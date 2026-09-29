"""
AAL-D-006 eval driver — Anthropic Claude (Sonnet 5 / Sonnet 4.6).
Resume-safe at (case, run); deterministic scorer; same scorecard shape as the
other D-006 drivers.

  export ANTHROPIC_API_KEY=...
  python run_d006_claude.py --model claude-sonnet-4-6 --runs 3 --temperature 0.0
  python run_d006_claude.py --model claude-sonnet-5   --runs 3 --thinking-budget 4000

Note: with --thinking-budget set (extended thinking), temperature is omitted
(the API rejects it alongside thinking), matching how Sonnet 5 ran on D-005.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from aal_telemetry import (Timer, extract_usage, price_for, print_performance,
                           summarize)
from d006_eval_common import (DATASET_DEFAULT, PROMPT_VERSION, SYSTEM_INSTRUCTION,
                              build_prompt, load_cases, strip_json_fences)
from score_d006 import aggregate_d006, score_case_d006

MAX_TOKENS = 16384


def build_client(api_key):
    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("anthropic not installed.\n  pip install --break-system-packages anthropic")
    return Anthropic(api_key=api_key)


# which extended-thinking API this model wants — discovered once, reused
_THINK = {"mode": "adaptive"}   # newer models (Opus 5): adaptive + output_config.effort;
                                # older (Sonnet 4.6-era): enabled + budget_tokens


def call_model(client, model, prompt, *, temperature, top_p, thinking, thinking_budget,
               effort, max_tokens=MAX_TOKENS, max_retries=8):
    """Anthropic call, adaptive across both thinking APIs. Opus 5 requires
    thinking.type=adaptive + output_config.effort; older models use
    thinking.type=enabled + budget_tokens. We try adaptive first and fall back."""
    last_err, text = None, ""
    tel = {"latency_s": 0.0, "attempts": 0}
    for attempt in range(max_retries):
        tel["attempts"] = attempt + 1
        kwargs = {"model": model, "max_tokens": max_tokens, "system": SYSTEM_INSTRUCTION,
                  "messages": [{"role": "user", "content": prompt}]}
        if thinking:
            if _THINK["mode"] == "adaptive":
                kwargs["thinking"] = {"type": "adaptive"}
                kwargs["extra_body"] = {"output_config": {"effort": effort}}
            else:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            # temperature/top_p are not allowed alongside thinking
        else:
            if temperature is not None:
                kwargs["temperature"] = temperature
            if top_p is not None:
                kwargs["top_p"] = top_p
        try:
            with Timer() as _t:
                with client.messages.stream(**kwargs) as stream:   # stream: avoids the SDK's
                    final = stream.get_final_message()               # non-streaming 10-min guard
            tel["latency_s"] = round(tel["latency_s"] + _t.elapsed, 4)
            _u = getattr(final, "usage", None)
            if _u is not None:
                tel["prompt_tokens"] = getattr(_u, "input_tokens", None)
                tel["completion_tokens"] = getattr(_u, "output_tokens", None)
                if tel["prompt_tokens"] is not None and tel["completion_tokens"] is not None:
                    tel["total_tokens"] = tel["prompt_tokens"] + tel["completion_tokens"]
            text = "".join(b.text for b in final.content if getattr(b, "type", None) == "text")
            return json.loads(strip_json_fences(text.strip())), tel
        except json.JSONDecodeError as e:
            last_err = f"JSON parse error: {e}; raw={text[:200]!r}"
            print(f"  [retry {attempt+1}/{max_retries}] {last_err}")
        except Exception as e:
            low = str(e).lower()
            if thinking and "enabled" in low and ("not supported" in low or "adaptive" in low):
                _THINK["mode"] = "adaptive"; print("  [adapt] thinking -> adaptive"); continue
            if thinking and ("adaptive" in low or "output_config" in low or "effort" in low) \
                    and ("not supported" in low or "unexpected" in low or "unknown" in low):
                _THINK["mode"] = "enabled"; print("  [adapt] thinking -> enabled+budget"); continue
            last_err = f"{type(e).__name__}: {e}"
            print(f"  [retry {attempt+1}/{max_retries}] {last_err}")
        time.sleep(min(2 ** attempt, 60) + attempt * 2)
    return {"_error": last_err}, tel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=DATASET_DEFAULT)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out-dir", default="./eval_out_d006")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--top-p", dest="top_p", type=float, default=None)
    ap.add_argument("--max-tokens", dest="max_tokens", type=int, default=MAX_TOKENS)
    ap.add_argument("--no-temperature", action="store_true")
    ap.add_argument("--thinking-budget", type=int, default=None,
                    help="enable extended thinking (budget used only for older enabled-API models)")
    ap.add_argument("--effort", choices=["low", "medium", "high"], default="high",
                    help="reasoning effort for adaptive-thinking models (e.g. Opus 5)")
    ap.add_argument("--price-in", dest="price_in", type=float, default=None,
                    help="USD per 1M input tokens (list price) for cost reporting")
    ap.add_argument("--price-out", dest="price_out", type=float, default=None,
                    help="USD per 1M output tokens (list price) for cost reporting")
    args = ap.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Set your API key:  export ANTHROPIC_API_KEY=...")

    cases = load_cases(args.dataset, limit=args.limit)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    slug = args.model.replace("/", "_").replace(".", "-")
    results_path = out_dir / f"eval_results_d006_{slug}.json"
    scorecard_path = out_dir / f"scorecard_d006_{slug}.json"

    by_id = {}
    if results_path.exists():
        for rec in json.loads(results_path.read_text()):
            by_id[rec["case_id"]] = rec
        print(f"Resuming: {sum(len(r['runs']) for r in by_id.values())} (case,run) on disk")

    client = build_client(api_key)
    thinking_on = args.thinking_budget is not None
    temp = None if (args.no_temperature or thinking_on) else args.temperature
    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        rec = by_id.get(cid) or {"case_id": cid, "runs": []}
        while len(rec["runs"]) < args.runs:
            pred, tel = call_model(client, args.model, build_prompt(case), temperature=temp,
                              top_p=args.top_p, thinking=thinking_on,
                              thinking_budget=args.thinking_budget or 4000,
                              effort=args.effort, max_tokens=args.max_tokens)
            cost = price_for(tel, args.price_in, args.price_out)
            if cost is not None:
                tel["cost_usd"] = cost
            rec["runs"].append({"prediction": pred, "score": score_case_d006(case, pred),
                                "telemetry": tel})
            by_id[cid] = rec
            results_path.write_text(json.dumps(list(by_id.values()), indent=2))
            if args.sleep:
                time.sleep(args.sleep)
        errs = sum(1 for r in rec["runs"] if r["score"].get("error"))
        print(f"[{i:>3}/{len(cases)}] {cid}  {'ERR' if errs else 'ok'}")

    records = list(by_id.values())
    sc = aggregate_d006(records, args.runs)
    sc.update({"model": args.model, "provider": "anthropic", "dataset": args.dataset,
               "prompt_version": PROMPT_VERSION,
               "sampling": (f"thinking={_THINK['mode']}, effort={args.effort}" if thinking_on
                            else "provider_default" if temp is None
                            else f"temperature={args.temperature}"
                            + (f", top_p={args.top_p}" if args.top_p is not None else ""))})
    sc["performance"] = summarize(records, price_in=args.price_in, price_out=args.price_out)
    scorecard_path.write_text(json.dumps(sc, indent=2))

    print("\n=== Scorecard ===")
    print(f"Model: {args.model} (anthropic)   Runs: {args.runs}   "
          f"Cases: {sc['n_cases']}   Errors: {sc['n_errors']}")
    for k in ("detection_accuracy", "false_break_rate", "attribution_category_accuracy",
              "field_accuracy", "value_accuracy", "substitution_validity", "escalation_accuracy"):
        v = sc.get(k)
        if v:
            pl = v["pooled"]
            print(f"{k:<32}{pl['accuracy']:.1%}  95% CI [{pl['wilson_95'][0]:.1%}, {pl['wilson_95'][1]:.1%}]")
    print_performance(sc.get("performance"))
    print(f"\nWrote {results_path}\nWrote {scorecard_path}")


if __name__ == "__main__":
    main()
