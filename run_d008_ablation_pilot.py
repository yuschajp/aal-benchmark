"""
AAL-D-008 ablation pilot. Two models, one run, the four categories where a
computed figure decides the value: DAYCOUNT, SETTLEDATE, RECALL, HAIRCUT.
Each case is run under condition A (figures printed) and condition B (figures
withheld, conventions stated), so the comparison is on identical cases.

Needs OPENAI_API_KEY and ANTHROPIC_API_KEY in the environment.
Writes raw results to eval_out_d008/ablation_pilot.json.

    python3 run_d008_ablation_pilot.py
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import anthropic
from openai import OpenAI

from d008_ablation import SYSTEM_INSTRUCTION_B, build_prompt_b
from d008_case_scorer import score_case_d008
from d008_eval_common import SYSTEM_INSTRUCTION, build_prompt, load_cases, strip_json_fences

PILOT = ("RL-DIS-DAYCOUNT", "RL-DIS-SETTLEDATE", "RL-DIS-RECALL", "RL-DIS-HAIRCUT")
OUT = Path("eval_out_d008/ablation_pilot.json")

oa = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
an = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def call_gpt(system, prompt):
    r = oa.chat.completions.create(
        model="gpt-5.6-sol",
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": prompt}],
        max_completion_tokens=16384, response_format={"type": "json_object"})
    return r.choices[0].message.content


def call_opus(system, prompt):
    m = an.messages.create(model="claude-opus-5-5", max_tokens=8192, system=system,
                           messages=[{"role": "user", "content": prompt}])
    return next(b.text for b in m.content if b.type == "text")


MODELS = {"gpt-5.6-sol": call_gpt, "claude-opus-5-5": call_opus}
CONDITIONS = {"A": (SYSTEM_INSTRUCTION, build_prompt),
              "B": (SYSTEM_INSTRUCTION_B, build_prompt_b)}

cases = [c for c in load_cases() if c["ground_truth"]["correct_root_cause"] in PILOT]
print(f"{len(cases)} cases x {len(CONDITIONS)} conditions x {len(MODELS)} models "
      f"= {len(cases) * len(CONDITIONS) * len(MODELS)} calls\n")

records = []
for model, call in MODELS.items():
    for cond, (system, builder) in CONDITIONS.items():
        for i, c in enumerate(cases, 1):
            rec = {"model": model, "condition": cond, "case_id": c["case_id"],
                   "category": c["ground_truth"]["correct_root_cause"]}
            try:
                pred = json.loads(strip_json_fences(call(system, builder(c))))
                s = score_case_d008(c, pred)   # scored against the ORIGINAL case
                rec.update(prediction=pred, score=s)
            except Exception as e:
                rec.update(score={"error": True, "error_detail": f"{type(e).__name__}: {e}"[:300]})
            records.append(rec)
            print(f"  {model:16s} {cond}  [{i:>3}/{len(cases)}] {c['case_id']}  "
                  f"{'ERR' if rec['score'].get('error') else 'ok'}")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(records, indent=2))   # checkpoint after each block

# ---- summary ----------------------------------------------------------------
tally = defaultdict(lambda: {"n": 0, "attr": 0, "val": 0, "val_n": 0, "err": 0})
for r in records:
    t = tally[(r["model"], r["condition"], r["category"])]
    s = r["score"]
    if s.get("error"):
        t["err"] += 1
        continue
    t["n"] += 1
    t["attr"] += bool(s["attribution_correct"])
    if s.get("value_correct") is not None:
        t["val_n"] += 1
        t["val"] += bool(s["value_correct"])

print("\n=== D-008 ablation pilot: value accuracy, A (printed) vs B (computed) ===")
for model in MODELS:
    print(f"\n{model}")
    print(f"  {'category':22s} {'A value':>10s} {'B value':>10s} {'B attr':>10s} {'B err':>6s}")
    for cat in PILOT:
        a, b = tally[(model, "A", cat)], tally[(model, "B", cat)]
        fa = f"{a['val']}/{a['val_n']}" if a["val_n"] else "-"
        fb = f"{b['val']}/{b['val_n']}" if b["val_n"] else "-"
        print(f"  {cat:22s} {fa:>10s} {fb:>10s} {b['attr']:>5d}/{b['n']:<4d} {b['err']:>6d}")

print("\nSample condition-B value misses (model figure vs ground truth):")
shown = 0
for r in records:
    s = r["score"]
    if r["condition"] == "B" and not s.get("error") and s.get("value_correct") is False:
        print(f"  {r['model']:16s} {r['category']:20s} {r['case_id']}  "
              f"model {r['prediction'].get('corrected_amount')}  "
              f"gt {next(c for c in cases if c['case_id'] == r['case_id'])['ground_truth']['correct_amount']}")
        shown += 1
        if shown == 8:
            break
if not shown:
    print("  none")
print(f"\nRaw results: {OUT}")
