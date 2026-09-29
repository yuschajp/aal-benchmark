"""
AAL-D-006 eval driver — Google Gemini via the REST API using only Python stdlib
(urllib). No google SDK, no pydantic — avoids the SDK/pydantic dependency
conflicts entirely.

  export GEMINI_API_KEY=...            # or GOOGLE_API_KEY
  python run_d006_gemini.py --list-models          # see exact model IDs your key can use
  python run_d006_gemini.py --model gemini-3.1-pro --runs 3 --sleep 6

If a model 404s, run --list-models and use the exact name it prints (drop the
leading "models/"). Try --api-version v1 if a model isn't on v1beta.
Resume-safe at (case, run); deterministic scorer; same scorecard shape as the
other D-006 drivers.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from aal_telemetry import (Timer, extract_usage, price_for, print_performance,
                           summarize)
from d006_eval_common import (DATASET_DEFAULT, PROMPT_VERSION, SYSTEM_INSTRUCTION,
                              build_prompt, load_cases, strip_json_fences)
from score_d006 import aggregate_d006, score_case_d006

MAX_TOKENS = 16384


def _base(api_version):
    return f"https://generativelanguage.googleapis.com/{api_version}"


def list_models(api_key, api_version):
    url = f"{_base(api_version)}/models?key={api_key}&pageSize=200"
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.loads(r.read())
    print(f"Models supporting generateContent ({api_version}):")
    for m in data.get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            name = m["name"].replace("models/", "")
            if "gemini" in name:
                print(f"  {name:40s} {m.get('displayName','')}")


def call_model(api_key, api_version, model, prompt, *, temperature, top_p,
               max_tokens=MAX_TOKENS, max_retries=6):
    url = f"{_base(api_version)}/models/{model}:generateContent?key={api_key}"
    gen = {"maxOutputTokens": max_tokens, "responseMimeType": "application/json"}
    if temperature is not None:
        gen["temperature"] = temperature
    if top_p is not None:
        gen["topP"] = top_p
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": gen,
    }
    last_err, text = None, ""
    tel = {"latency_s": 0.0, "attempts": 0}
    for attempt in range(max_retries):
        try:
            tel["attempts"] = attempt + 1
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with Timer() as _t:
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.loads(r.read())
            tel["latency_s"] = round(tel["latency_s"] + _t.elapsed, 4)
            _um = data.get("usageMetadata") or {}
            if _um:
                tel["prompt_tokens"] = _um.get("promptTokenCount")
                tel["completion_tokens"] = _um.get("candidatesTokenCount")
                tel["total_tokens"] = _um.get("totalTokenCount")
                if _um.get("thoughtsTokenCount") is not None:
                    tel["reasoning_tokens"] = _um.get("thoughtsTokenCount")
            parts = data["candidates"][0]["content"].get("parts", [])
            text = strip_json_fences("".join(p.get("text", "") for p in parts).strip())
            # raw_decode parses the first JSON object and ignores any trailing
            # junk (Gemini sometimes appends a stray extra brace).
            obj, _ = json.JSONDecoder().raw_decode(text)
            return obj, tel
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            last_err = f"HTTP {e.code}: {detail}"
            print(f"  [retry {attempt+1}/{max_retries}] {last_err}")
        except (KeyError, IndexError) as e:
            last_err = f"unexpected response shape: {e}; raw={str(data)[:200]}"
            print(f"  [retry {attempt+1}/{max_retries}] {last_err}")
        except json.JSONDecodeError as e:
            last_err = f"JSON parse error: {e}; raw={text[:200]!r}"
            print(f"  [retry {attempt+1}/{max_retries}] {last_err}")
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print(f"  [retry {attempt+1}/{max_retries}] {last_err}")
        time.sleep(min(2 ** attempt, 60) + attempt * 2)
    return {"_error": last_err}, tel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=DATASET_DEFAULT)
    ap.add_argument("--model", default="gemini-3.1-pro")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out-dir", default="./eval_out_d006")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=6.0)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--top-p", dest="top_p", type=float, default=None)
    ap.add_argument("--max-tokens", dest="max_tokens", type=int, default=MAX_TOKENS)
    ap.add_argument("--no-temperature", action="store_true")
    ap.add_argument("--api-version", default="v1beta")
    ap.add_argument("--api-key-env", default=None)
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--price-in", dest="price_in", type=float, default=None,
                    help="USD per 1M input tokens (list price) for cost reporting")
    ap.add_argument("--price-out", dest="price_out", type=float, default=None,
                    help="USD per 1M output tokens (list price) for cost reporting")
    args = ap.parse_args()

    api_key = os.environ.get(args.api_key_env or "GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        sys.exit("Set your API key:  export GEMINI_API_KEY=...")

    if args.list_models:
        list_models(api_key, args.api_version)
        return

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

    temp = None if args.no_temperature else args.temperature
    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        rec = by_id.get(cid) or {"case_id": cid, "runs": []}
        while len(rec["runs"]) < args.runs:
            pred, tel = call_model(api_key, args.api_version, args.model, build_prompt(case),
                                   temperature=temp, top_p=args.top_p, max_tokens=args.max_tokens)
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
    sc.update({"model": args.model, "provider": "google", "dataset": args.dataset,
               "prompt_version": PROMPT_VERSION,
               "sampling": ("provider_default" if temp is None
                            else f"temperature={args.temperature}"
                            + (f", top_p={args.top_p}" if args.top_p is not None else ""))})
    sc["performance"] = summarize(records, price_in=args.price_in, price_out=args.price_out)
    scorecard_path.write_text(json.dumps(sc, indent=2))

    print("\n=== Scorecard ===")
    print(f"Model: {args.model} (google)   Runs: {args.runs}   "
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
