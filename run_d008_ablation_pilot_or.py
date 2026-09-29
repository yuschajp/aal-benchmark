"""
AAL-D-008 ablation pilot, non-frontier models via OpenRouter.

Same design as run_d008_ablation_pilot.py: the four categories where a
computed figure decides the value, each case under condition A (figures
printed) and condition B (figures withheld, conventions stated), one run.

GPT-5.6 Sol and Claude Opus 5.5 scored 100% in both conditions. This asks
whether models below the frontier also do. If they fail condition B, D-008
separates frontier models from the rest of the market. If they don't, the
dataset does not discriminate and becomes a methodology paper.

Records OpenRouter's upstream host on every call. D-007 could not say which
host served its DeepSeek results; this does not repeat that gap.

Needs OPENROUTER_API_KEY. Writes eval_out_d008/ablation_pilot_openrouter.json.

    python3 run_d008_ablation_pilot_or.py
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

from openai import OpenAI

from d008_ablation import SYSTEM_INSTRUCTION_B, build_prompt_b
from d008_case_scorer import score_case_d008
from d008_eval_common import SYSTEM_INSTRUCTION, build_prompt, load_cases, strip_json_fences

PILOT = ("RL-DIS-DAYCOUNT", "RL-DIS-SETTLEDATE", "RL-DIS-RECALL", "RL-DIS-HAIRCUT")
MODELS = ("deepseek/deepseek-v4-pro", "moonshotai/kimi-k3")
OUT = Path("eval_out_d008/ablation_pilot_openrouter.json")

client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"],
                base_url="https://openrouter.ai/api/v1", timeout=300)
CONDITIONS = {"A": (SYSTEM_INSTRUCTION, build_prompt),
              "B": (SYSTEM_INSTRUCTION_B, build_prompt_b)}


def call(model, system, prompt, attempts=4):
    """Provider-default sampling, as a pilot. Retries transient connection
    errors with backoff; anything still failing is recorded as an error,
    never scored as a wrong answer."""
    last = None
    for i in range(attempts):
        try:
            r = client.chat.completions.create(
                model=model, max_tokens=16384,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}])
            extra = getattr(r, "model_extra", None) or {}
            return (r.choices[0].message.content or ""), extra.get("provider"), getattr(r, "model", None)
        except Exception as e:
            last = e
            time.sleep(5 * (i + 1))
    raise last


cases = [c for c in load_cases() if c["ground_truth"]["correct_root_cause"] in PILOT]
print(f"{len(cases)} cases x {len(CONDITIONS)} conditions x {len(MODELS)} models "
      f"= {len(cases) * len(CONDITIONS) * len(MODELS)} calls\n")

records = json.loads(OUT.read_text()) if OUT.exists() else []
done = {(r["model"], r["condition"], r["case_id"]) for r in records
        if not r["score"].get("error")}
records = [r for r in records if not r["score"].get("error")]   # retry prior errors
if done:
    print(f"Resuming: {len(done)} scored observations on disk\n")

for model in MODELS:
    for cond, (system, builder) in CONDITIONS.items():
        for i, c in enumerate(cases, 1):
            if (model, cond, c["case_id"]) in done:
                continue
            rec = {"model": model, "condition": cond, "case_id": c["case_id"],
                   "category": c["ground_truth"]["correct_root_cause"]}
            try:
                text, host, served = call(model, system, builder(c))
                rec.update(upstream_provider=host, served_model=served)
                pred = json.loads(strip_json_fences(text))
                rec.update(prediction=pred, score=score_case_d008(c, pred))
            except Exception as e:
                rec.update(score={"error": True, "error_detail": f"{type(e).__name__}: {e}"[:300]})
            records.append(rec)
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(records, indent=2))   # checkpoint every call
            print(f"  {model:26s} {cond}  [{i:>3}/{len(cases)}] {c['case_id']}  "
                  f"{'ERR' if rec['score'].get('error') else 'ok'}")

# ---- summary ----------------------------------------------------------------
tally = defaultdict(lambda: {"n": 0, "attr": 0, "val": 0, "val_n": 0, "err": 0})
hosts = defaultdict(Counter)
for r in records:
    t = tally[(r["model"], r["condition"], r["category"])]
    s = r["score"]
    if r.get("upstream_provider"):
        hosts[r["model"]][r["upstream_provider"]] += 1
    if s.get("error"):
        t["err"] += 1
        continue
    t["n"] += 1
    t["attr"] += bool(s["attribution_correct"])
    if s.get("value_correct") is not None:
        t["val_n"] += 1
        t["val"] += bool(s["value_correct"])

print("\n=== D-008 ablation pilot (OpenRouter): A (printed) vs B (computed) ===")
for model in MODELS:
    print(f"\n{model}")
    print(f"  {'category':22s} {'A value':>9s} {'B value':>9s} {'A attr':>9s} {'B attr':>9s} {'err':>4s}")
    for cat in PILOT:
        a, b = tally[(model, "A", cat)], tally[(model, "B", cat)]
        f = lambda t, k, n: f"{t[k]}/{t[n]}" if t[n] else "-"
        print(f"  {cat:22s} {f(a,'val','val_n'):>9s} {f(b,'val','val_n'):>9s} "
              f"{f(a,'attr','n'):>9s} {f(b,'attr','n'):>9s} {a['err'] + b['err']:>4d}")
    print(f"  upstream hosts: {dict(hosts[model]) or 'not reported'}")

print("\nSample condition-B value misses (model figure vs ground truth):")
gt = {c["case_id"]: c["ground_truth"]["correct_amount"] for c in cases}
shown = 0
for r in records:
    s = r["score"]
    if r["condition"] == "B" and not s.get("error") and s.get("value_correct") is False:
        print(f"  {r['model']:26s} {r['category']:20s} {r['case_id']}  "
              f"model {r['prediction'].get('corrected_amount')}  gt {gt[r['case_id']]}")
        shown += 1
        if shown == 10:
            break
if not shown:
    print("  none")
print(f"\nRaw results: {OUT}")
