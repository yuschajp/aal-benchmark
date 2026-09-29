"""
AAL-D-008 eval driver — OpenAI-compatible (GPT-5.6 Sol first-party; Kimi K3 /
DeepSeek / Qwen via OpenRouter or DashScope). Resume-safe at (case, run).
Deterministic scorer. Same plumbing as the D-006 drivers; only the
dataset/prompt/scorer imports change. Telemetry (latency/tokens/cost) is
wired in from this dataset onward via aal_telemetry.py.

  python run_d008_openai.py --provider openai     --model gpt-5.6-sol --runs 3
  python run_d008_openai.py --provider openrouter --model moonshotai/kimi-k3  --runs 3 \
         --temperature 1.0 --top-p 0.95 --sleep 5
  python run_d008_openai.py --provider dashscope  --model qwen3.8-max --runs 3 \
         --temperature 0.7 --top-p 0.8 --sleep 3

Reasoning models: use vendor-recommended sampling (not temperature 0).
Claude/Gemini: run_d008_claude.py / run_d008_gemini.py (same plumbing).
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
from d008_eval_common import (DATASET_DEFAULT, PROMPT_VERSION, SYSTEM_INSTRUCTION,
                              build_prompt, load_cases, strip_json_fences)
from d008_case_scorer import aggregate_d008, score_case_d008

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
# Set in main() for OpenRouter. Hosts behind one model name default reasoning
# on or off independently: DeepSeek V4 Pro scored 99% on hosts defaulting on and
# 5-7% on hosts defaulting off, identical cases (D-008 pinned-host test, Sept 25).
# Requesting reasoning explicitly removes that variance from the measurement.
OPENROUTER_EXTRA = None


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
        if OPENROUTER_EXTRA:
            k["extra_body"] = OPENROUTER_EXTRA
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

            # Capture which UPSTREAM host actually served this call, not just
            # which gateway we asked. OpenRouter routes a single model name
            # across many third-party hosts and returns the one it used in a
            # non-standard `provider` field; the OpenAI SDK keeps unknown
            # response fields on model_extra. Without this, a results file can
            # only say "openrouter" and the actual build serving the request
            # is unrecoverable after the fact -- exactly the provenance gap
            # the inference-backend literature (arXiv:2608.04714,
            # arXiv:2605.19537) identifies as a source of score variance.
            try:
                extra = getattr(resp, "model_extra", None) or {}
                upstream = extra.get("provider")
                if upstream:
                    tel["upstream_provider"] = upstream
                # resp.model is the build string the host reports back, which
                # can differ from the model name we requested (e.g. a dated
                # build suffix). Record it whenever it is not identical.
                served_model = getattr(resp, "model", None)
                if served_model and served_model != model:
                    tel["served_model"] = served_model
            except Exception:
                pass   # never let provenance capture break a scoring run
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
    ap.add_argument("--out-dir", default="./eval_out_d008")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--top-p", dest="top_p", type=float, default=None)
    ap.add_argument("--max-tokens", dest="max_tokens", type=int, default=MAX_TOKENS)
    ap.add_argument("--no-temperature", action="store_true")
    ap.add_argument("--json-mode", dest="json_mode", action="store_true", default=None)
    ap.add_argument("--no-json-mode", dest="json_mode", action="store_false")
    ap.add_argument("--api-key-env", default=None)
    ap.add_argument("--pin-host", default=None,
                    help="OpenRouter provider slug to pin every call to, fallbacks off")
    ap.add_argument("--no-reasoning-request", action="store_true",
                    help="OpenRouter: do not request reasoning (for non-reasoning models)")
    ap.add_argument("--base-url", dest="base_url", default=None,
                    help="override the provider's default base_url. Needed for DashScope "
                         "workspace-scoped keys (sk-ws-...), which only authenticate against "
                         "their own dedicated workspace domain, not dashscope-intl.aliyuncs.com")
    ap.add_argument("--price-in", dest="price_in", type=float, default=None,
                    help="USD per 1M input tokens (list price) for cost reporting")
    ap.add_argument("--price-out", dest="price_out", type=float, default=None,
                    help="USD per 1M output tokens (list price) for cost reporting")
    args = ap.parse_args()

    prov = PROVIDERS[args.provider]
    global OPENROUTER_EXTRA
    if args.provider == "openrouter" and not args.no_reasoning_request:
        OPENROUTER_EXTRA = {"reasoning": {"enabled": True},
                            "provider": {"require_parameters": True}}
        if args.pin_host:
            OPENROUTER_EXTRA["provider"].update(order=[args.pin_host], allow_fallbacks=False)
        print(f"OpenRouter request extras: {OPENROUTER_EXTRA}")
    model = args.model or prov["default_model"]
    json_mode = prov["json_mode"] if args.json_mode is None else args.json_mode
    api_key = os.environ.get(args.api_key_env or prov["api_key_env"])
    if not api_key:
        sys.exit(f"Set your API key:  export {args.api_key_env or prov['api_key_env']}=...")

    cases = load_cases(args.dataset, limit=args.limit)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    slug = model.replace("/", "_").replace(".", "-")
    results_path = out_dir / f"eval_results_d008_{slug}.json"
    scorecard_path = out_dir / f"scorecard_d008_{slug}.json"

    by_id = {}
    if results_path.exists():
        for rec in json.loads(results_path.read_text()):
            by_id[rec["case_id"]] = rec
        print(f"Resuming: {sum(len(r['runs']) for r in by_id.values())} (case,run) on disk")

    base_url = args.base_url or prov["base_url"]
    if args.base_url:
        print(f"base_url override: {base_url}")
    client = build_client(api_key, base_url)
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
            rec["runs"].append({"prediction": pred, "score": score_case_d008(case, pred),
                                "telemetry": tel})
            by_id[cid] = rec
            results_path.write_text(json.dumps(list(by_id.values()), indent=2))
            if args.sleep:
                time.sleep(args.sleep)
        errs = sum(1 for r in rec["runs"] if r["score"].get("error"))
        print(f"[{i:>3}/{len(cases)}] {cid}  {'ERR' if errs else 'ok'}")

    records = list(by_id.values())
    sc = aggregate_d008(records, args.runs)

    # Record what was ACTUALLY sent, not what was requested. The adaptive retry
    # loop drops params the endpoint rejects (_DROP) and remembers that for the
    # rest of the run — GPT-6 Astra, for one, refuses temperature outright. A
    # scorecard that reports the requested value would misstate the run
    # conditions, which is the one thing a reproducibility claim cannot do.
    parts = []
    if args.no_temperature or "temperature" in _DROP:
        parts.append("temperature=provider_default")
    else:
        parts.append(f"temperature={args.temperature}")
    if args.top_p is not None:
        parts.append("top_p=provider_default" if "top_p" in _DROP
                     else f"top_p={args.top_p}")
    sampling = ", ".join(parts)

    dropped = sorted(_DROP)
    if dropped:
        sampling += f" [endpoint rejected: {', '.join(dropped)}]"

    sc.update({"model": model, "provider": args.provider, "dataset": args.dataset,
               "prompt_version": PROMPT_VERSION,
               "sampling": sampling,
               "sampling_requested": ("provider_default" if args.no_temperature
                                      else f"temperature={args.temperature}"
                                      + (f", top_p={args.top_p}" if args.top_p is not None else "")),
               "params_rejected_by_endpoint": dropped,
               "openrouter_request_extras": OPENROUTER_EXTRA})
    sc["performance"] = summarize(records, price_in=args.price_in, price_out=args.price_out)
    scorecard_path.write_text(json.dumps(sc, indent=2))

    print("\n=== Scorecard ===")
    print(f"Model: {model} ({args.provider})   Runs: {args.runs}   "
          f"Cases: {sc['n_cases']}   Errors: {sc['n_errors']}")
    for k in ("detection_accuracy", "attribution_accuracy", "value_accuracy"):
        v = sc.get(k)
        if v and v.get("n"):
            print(f"{k:<28}{v['accuracy']:.1%}  95% CI "
                  f"[{v['wilson_95'][0]:.1%}, {v['wilson_95'][1]:.1%}]  n={v['n']}")

    fp = sc.get("false_positive_rate") or {}
    if fp.get("n_trap_observations"):
        print(f"{'false_positive_rate':<28}{fp['rate']:.1%}  "
              f"n={fp['n_trap_observations']} trap observations")

    fl = sc.get("attribution_flip_rate") or {}
    if fl.get("eligible_cases"):
        print(f"{'attribution_flip_rate':<28}{fl['rate']:.1%}  "
              f"{fl['flipped']}/{fl['eligible_cases']} eligible cases")

    if sc.get("brier_score") is not None:
        print(f"{'brier_score':<28}{sc['brier_score']:.4f}  (lower is better)")
    print_performance(sc.get("performance"))
    print(f"\nWrote {results_path}\nWrote {scorecard_path}")


if __name__ == "__main__":
    main()
