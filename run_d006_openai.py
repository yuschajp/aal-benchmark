"""
AAL-D-006 eval driver — OpenAI-compatible (GPT-4o first-party; KIMI K3 / DeepSeek
via OpenRouter). Resume-safe at (case, run). Deterministic scorer. Same plumbing
as run_d005_openai.py; only the dataset/prompt/scorer imports change.

  python run_d006_openai.py --provider openai     --model gpt-4o-2024-08-06 --runs 3
  python run_d006_openai.py --provider openrouter --model moonshotai/kimi-k3  --runs 3 \
         --temperature 1.0 --top-p 0.95 --sleep 5
  python run_d006_openai.py --provider openrouter --model deepseek/deepseek-v3.2 --runs 3 \
         --temperature 0.6 --top-p 0.95 --max-tokens 24000 --sleep 5

Reasoning models: use vendor-recommended sampling (not temperature 0).
Claude/Gemini: copy this file and swap the client (mirrors run_d005_claude.py /
run_d005_gemini.py).
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

PROVIDERS = {
    "openai": {"base_url": None, "api_key_env": "OPENAI_API_KEY",
               "default_model": "gpt-5.6-sol", "json_mode": True},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY",
                   "default_model": "moonshotai/kimi-k3", "json_mode": False},
    # Alibaba Qwen via DashScope's OpenAI-compatible endpoint
    "dashscope": {"base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                  "api_key_env": "DASHSCOPE_API_KEY", "default_model": "qwen3.8-max", "json_mode": False},
}
MAX_TOKENS = 16384


def build_client(api_key, base_url):
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("openai not installed.\n  pip install --break-system-packages openai")
    return OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)


# endpoint quirks discovered at runtime, remembered across cases so we adapt once
_DROP: set[str] = set()          # params this model/endpoint rejects
_STATE = {"completion_key": False, "min_tokens": 0}


def call_model(client, model, prompt, *, json_mode, temperature, top_p,
               max_tokens=MAX_TOKENS, token_key="max_tokens", max_retries=8):
    """OpenAI-compatible call, adaptive for newer models: GPT-5.x require
    max_completion_tokens, reject custom temperature/top_p, and (as reasoning
    models) can spend the whole output budget on hidden reasoning -> empty
    content. We fix each of these once and remember it for the rest of the run."""
    last_err, text = None, ""
    tel = {"latency_s": 0.0, "attempts": 0}      # telemetry accumulated across retries
    if token_key == "max_completion_tokens":
        _STATE["completion_key"] = True

    def build():
        tk = "max_completion_tokens" if _STATE["completion_key"] else "max_tokens"
        k = {tk: max(max_tokens, _STATE["min_tokens"] or 0) or max_tokens}
        if temperature is not None and "temperature" not in _DROP:
            k["temperature"] = temperature
        if top_p is not None and "top_p" not in _DROP:
            k["top_p"] = top_p
        if json_mode and "response_format" not in _DROP:
            k["response_format"] = {"type": "json_object"}
        return k

    for attempt in range(max_retries):
        try:
            tel["attempts"] = attempt + 1
            with Timer() as _t:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": SYSTEM_INSTRUCTION},
                              {"role": "user", "content": prompt}],
                    **build())
            tel["latency_s"] = round(tel["latency_s"] + _t.elapsed, 4)
            tel.update({k: v for k, v in extract_usage(resp).items() if v is not None})
            if not resp.choices:
                raise ValueError("no choices in response")
            text = strip_json_fences((resp.choices[0].message.content or "").strip())
            if not text:                                   # reasoning ate the whole budget
                if _STATE["min_tokens"] < 32000:
                    _STATE["min_tokens"] = 32000
                    print("  [adapt] empty output -> raising output budget to 32000")
                    continue
                raise ValueError("empty completion even after raising budget")
            return json.loads(text), tel
        except json.JSONDecodeError as e:
            last_err = f"JSON parse error: {e}; raw={text[:200]!r}"
            print(f"  [retry {attempt+1}/{max_retries}] {last_err}")
        except Exception as e:
            low = str(e).lower()
            if "max_completion_tokens" in low and not _STATE["completion_key"]:
                _STATE["completion_key"] = True
                print("  [adapt] max_tokens -> max_completion_tokens"); continue
            hit = next((p for p in ("temperature", "top_p", "response_format")
                        if p in low and p not in _DROP
                        and ("unsupported" in low or "not supported" in low
                             or "does not support" in low or "only the default" in low)), None)
            if hit:
                _DROP.add(hit)
                print(f"  [adapt] dropping unsupported {hit}"); continue
            last_err = f"{type(e).__name__}: {e}"
            print(f"  [retry {attempt+1}/{max_retries}] {last_err}")
        time.sleep(min(2 ** attempt, 60) + attempt * 2)
    return {"_error": last_err}, tel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="openai")
    ap.add_argument("--dataset", default=DATASET_DEFAULT)
    ap.add_argument("--model", default=None)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out-dir", default="./eval_out_d006")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--top-p", dest="top_p", type=float, default=None)
    ap.add_argument("--max-tokens", dest="max_tokens", type=int, default=MAX_TOKENS)
    ap.add_argument("--no-temperature", action="store_true")
    ap.add_argument("--json-mode", dest="json_mode", action="store_true", default=None)
    ap.add_argument("--no-json-mode", dest="json_mode", action="store_false")
    ap.add_argument("--api-key-env", default=None)
    ap.add_argument("--price-in", dest="price_in", type=float, default=None,
                    help="USD per 1M input tokens (list price) for cost reporting")
    ap.add_argument("--price-out", dest="price_out", type=float, default=None,
                    help="USD per 1M output tokens (list price) for cost reporting")
    args = ap.parse_args()

    prov = PROVIDERS[args.provider]
    model = args.model or prov["default_model"]
    json_mode = prov["json_mode"] if args.json_mode is None else args.json_mode
    api_key = os.environ.get(args.api_key_env or prov["api_key_env"])
    if not api_key:
        sys.exit(f"Set your API key:  export {args.api_key_env or prov['api_key_env']}=...")

    cases = load_cases(args.dataset, limit=args.limit)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    slug = model.replace("/", "_").replace(".", "-")
    results_path = out_dir / f"eval_results_d006_{slug}.json"
    scorecard_path = out_dir / f"scorecard_d006_{slug}.json"

    by_id = {}
    if results_path.exists():
        for rec in json.loads(results_path.read_text()):
            by_id[rec["case_id"]] = rec
        print(f"Resuming: {sum(len(r['runs']) for r in by_id.values())} (case,run) on disk")

    client = build_client(api_key, prov["base_url"])
    token_key = "max_completion_tokens" if args.provider == "openai" else "max_tokens"
    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        rec = by_id.get(cid) or {"case_id": cid, "runs": []}
        while len(rec["runs"]) < args.runs:
            pred, tel = call_model(client, model, build_prompt(case), json_mode=json_mode,
                                   temperature=(None if args.no_temperature else args.temperature),
                                   top_p=args.top_p, max_tokens=args.max_tokens, token_key=token_key)
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
    sc.update({"model": model, "provider": args.provider, "dataset": args.dataset,
               "prompt_version": PROMPT_VERSION,
               "sampling": ("provider_default" if args.no_temperature
                            else f"temperature={args.temperature}"
                            + (f", top_p={args.top_p}" if args.top_p is not None else ""))})
    sc["performance"] = summarize(records, price_in=args.price_in, price_out=args.price_out)
    scorecard_path.write_text(json.dumps(sc, indent=2))

    print("\n=== Scorecard ===")
    print(f"Model: {model} ({args.provider})   Runs: {args.runs}   "
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
