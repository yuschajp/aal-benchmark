"""
AAL-D-008 clean baseline: the published numbers.

Runs one model on the FINAL dataset under both conditions, three runs each:
  A  computed figures printed; the model judges which one the confirmation supports
     (all 250 cases)
  B  computed figures withheld, conventions stated; the model computes
     (230 cases: RL-DIS-ACCRUAL excluded, see d008_ablation.py)

Earlier three-run results predate the RECALL fix (original_notional) and are
superseded by these. One model per process, so two models can run side by
side in separate terminals without writing to the same file.

Resume-safe at (condition, case, run): rerun the same command after any stop.
A failed call is recorded as an error, never scored as a wrong answer, and is
retried on the next run. Reasoning tokens are recorded on every call.

    python3 run_d008_baseline.py --model gpt-5.6-sol
    python3 run_d008_baseline.py --model claude-opus-5-5

Writes eval_out_d008/baseline/<model>_<condition>.json and _scorecard.json.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from d008_ablation import SYSTEM_INSTRUCTION_B, build_prompt_b, cases_for_b
from d008_case_scorer import aggregate_d008, score_case_d008
from d008_eval_common import SYSTEM_INSTRUCTION, build_prompt, load_cases, strip_json_fences

RUNS = 3
OUT = Path("eval_out_d008/baseline")


def make_caller(model):
    if model.startswith("claude"):
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        def call(system, prompt):
            m = client.messages.create(model=model, max_tokens=8192, system=system,
                                       messages=[{"role": "user", "content": prompt}])
            text = next(b.text for b in m.content if b.type == "text")
            thinking = sum(len(getattr(b, "thinking", "") or "") for b in m.content
                           if b.type == "thinking")
            return text, {"output_tokens": m.usage.output_tokens,
                          "thinking_chars": thinking}
        return call

    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=300)

    def call(system, prompt):
        r = client.chat.completions.create(
            model=model, max_completion_tokens=16384,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}])
        det = getattr(r.usage, "completion_tokens_details", None)
        return (r.choices[0].message.content or ""), {
            "output_tokens": r.usage.completion_tokens,
            "reasoning_tokens": getattr(det, "reasoning_tokens", None) if det else None}
    return call


def with_retry(call, system, prompt, attempts=4):
    last = None
    for i in range(attempts):
        try:
            t0 = time.time()
            text, tel = call(system, prompt)
            tel["latency_s"] = round(time.time() - t0, 3)
            return text, tel
        except Exception as e:
            last = e
            time.sleep(5 * (i + 1))
    raise last


def value_flip(records):
    eligible = flipped = 0
    for rec in records:
        vals = [r["score"].get("value_correct") for r in rec["runs"]
                if not r["score"].get("error") and r["score"].get("value_correct") is not None]
        if len(vals) < 2:
            continue
        eligible += 1
        flipped += len(set(vals)) > 1
    return flipped, eligible


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    args = ap.parse_args()

    call = make_caller(args.model)
    slug = args.model.replace("/", "_").replace(".", "-")
    OUT.mkdir(parents=True, exist_ok=True)
    all_cases = load_cases()
    conditions = {
        "A": (SYSTEM_INSTRUCTION, build_prompt, all_cases),
        "B": (SYSTEM_INSTRUCTION_B, build_prompt_b, cases_for_b(all_cases)),
    }

    for cond, (system, builder, cases) in conditions.items():
        path = OUT / f"{slug}_{cond}.json"
        by_id = {}
        if path.exists():
            for rec in json.loads(path.read_text()):
                rec["runs"] = [r for r in rec["runs"] if not r["score"].get("error")]
                by_id[rec["case_id"]] = rec
        print(f"\n=== {args.model}  condition {cond}  ({len(cases)} cases x {RUNS} runs) ===")

        for i, c in enumerate(cases, 1):
            rec = by_id.setdefault(c["case_id"], {"case_id": c["case_id"], "runs": []})
            while len(rec["runs"]) < RUNS:
                try:
                    text, tel = with_retry(call, system, builder(c))
                    pred = json.loads(strip_json_fences(text))
                    run = {"prediction": pred, "score": score_case_d008(c, pred), "telemetry": tel}
                except Exception as e:
                    run = {"score": {"error": True, "error_detail": f"{type(e).__name__}: {e}"[:300]}}
                rec["runs"].append(run)
                path.write_text(json.dumps(list(by_id.values()), indent=2))
                if run["score"].get("error"):
                    break   # leave the gap; a rerun retries it
            n_err = sum(1 for r in rec["runs"] if r["score"].get("error"))
            print(f"  {cond} [{i:>3}/{len(cases)}] {c['case_id']}  {'ERR' if n_err else 'ok'}")

        records = list(by_id.values())
        sc = aggregate_d008(records, RUNS)
        vf, ve = value_flip(records)
        sc.update(model=args.model, condition=cond,
                  value_flip_rate={"rate": vf / ve if ve else 0.0, "flipped": vf, "eligible_cases": ve})
        (OUT / f"{slug}_{cond}_scorecard.json").write_text(json.dumps(sc, indent=2))

        print(f"\n--- {args.model}  condition {cond} ---")
        print(f"  errors               {sc['n_errors']} of {sc['n_observations']} observations")
        for k in ("detection_accuracy", "attribution_accuracy", "value_accuracy"):
            v = sc[k]
            print(f"  {k:20s} {v['accuracy']:6.1%}  CI [{v['wilson_95'][0]:.1%}, {v['wilson_95'][1]:.1%}]  n={v['n']}")
        fp = sc["false_positive_rate"]
        print(f"  {'false_positive':20s} {fp['rate']:6.1%}  n={fp['n_trap_observations']}")
        af = sc["attribution_flip_rate"]
        print(f"  {'attribution_flip':20s} {af['rate']:6.1%}  {af['flipped']}/{af['eligible_cases']}")
        print(f"  {'value_flip':20s} {vf / ve if ve else 0:6.1%}  {vf}/{ve}")
        weak = {k: v for k, v in sc["per_category_attribution"].items() if v["accuracy"] < 1.0}
        print(f"  categories below 100% attribution: {weak or 'none'}")


if __name__ == "__main__":
    main()
