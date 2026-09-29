"""
AAL-D-007 pre-publication gate. Stdlib only.

Runs three integrity checks and exits non-zero on any failure:
  1. dataset recompute check (independent second implementation)
  2. scorer contract tests (perfect / trap-overflag / wrong-category / malformed)
  3. per-model error & null-prediction envelope (any scorecards under eval_out_d007/)

Usage:  python gate_d007.py [--require model1,model2,...]
"""
from __future__ import annotations

import glob
import json
import subprocess
import sys
from pathlib import Path

from d007_common import build_master_plan, render_case
from d007_eval_common import build_prompt
from score_d007 import aggregate_d007, score_case_d007

HERE = Path(__file__).parent


def check_recompute() -> bool:
    print("== dataset recompute check ==")
    r = subprocess.run([sys.executable, "quality_checks/recompute_check_d007.py"],
                       cwd=HERE, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode == 0


def check_scorer() -> bool:
    print("\n== scorer contract tests ==")
    plan = build_master_plan()
    cases = [render_case(i + 1, plan[i]) for i in range(len(plan))]
    trap = next(c for c in cases if not c["ground_truth"]["dispute_exists"])
    disputed = next(c for c in cases if c["ground_truth"]["dispute_exists"])
    ok = True

    def expect(name, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}: {name}")
        ok = ok and cond

    # (a) no value leakage: the prompt must not print the ground-truth verdict
    p = build_prompt(disputed)
    prompt_body = p.split("Return ONLY")[0]
    expect("prompt hides primary_dispute_category",
           disputed["ground_truth"]["primary_dispute_category"] not in prompt_body)
    expect("prompt hides ground_truth block entirely", '"ground_truth"' not in prompt_body)
    expect("prompt hides meta/rw_scale", '"rw_scale"' not in prompt_body and '"meta"' not in prompt_body)
    expect("prompt hides perturbed_side (v1.1)", '"perturbed_side"' not in prompt_body)

    # (b) perfect prediction on a disputed case scores all-correct
    gt = disputed["ground_truth"]
    perfect = {"dispute_exists": True, "primary_dispute_category": gt["primary_dispute_category"],
               "offending_component": gt["offending_component"], "correct_im_amount": gt["correct_im_amount"],
               "dispute_difference": gt["dispute_difference"], "should_escalate": True,
               "escalation_target": "IM disputes desk"}
    s = score_case_d007(disputed, perfect)
    expect("perfect->detection", s["detection_correct"])
    expect("perfect->category", s["category_correct"])
    expect("perfect->component", s["component_correct"])
    expect("perfect->value", s["value_correct"])
    expect("perfect->difference", s["difference_correct"])
    expect("perfect->escalation", s["escalation_correct"])

    # (c) over-flagging a trap (clean case) is caught as a false flag
    s2 = score_case_d007(trap, {"dispute_exists": True, "primary_dispute_category": "IM-DIS-SENS",
                                "offending_component": "RATES_FX:USD:RF-001", "correct_im_amount": 0,
                                "dispute_difference": 0, "should_escalate": True, "escalation_target": "x"})
    expect("trap over-flag -> false_flag", s2.get("false_flag") is True)

    # (d) wrong category still counts detection but fails category
    s3 = score_case_d007(disputed, {**perfect, "primary_dispute_category": "IM-DIS-FX"
                                    if gt["primary_dispute_category"] != "IM-DIS-FX" else "IM-DIS-SENS"})
    expect("wrong category discrimination", s3["detection_correct"] and not s3["category_correct"])

    # (e) malformed prediction -> error
    expect("malformed -> error", score_case_d007(disputed, {"_error": "x"})["error"] is True)
    return ok


def check_balance() -> bool:
    """v1.1 guard: correct_im_amount must not be structurally tied to one
    physical side. Regenerates the plan and asserts the firm/counterparty
    split is close to the intended exact 125/125 -- if a future edit
    reintroduces v1.0's bug (always "firm"), this fails loudly instead of
    silently letting value_accuracy become a copy-task again."""
    print("\n== correct-side balance check (v1.1 fix) ==")
    plan = build_master_plan()
    cases = [render_case(i + 1, plan[i]) for i in range(len(plan))]
    firm_correct = sum(1 for c in cases
                       if abs(c["ground_truth"]["correct_im_amount"] - c["margin_breakdown"]["firm"]["total_im"]) < 0.01)
    cpty_correct = len(cases) - firm_correct
    ok = firm_correct == 125 and cpty_correct == 125
    print(f"  firm correct: {firm_correct}  counterparty correct: {cpty_correct}  "
          f"({'PASS' if ok else 'FAIL — expected exactly 125/125'})")
    return ok


def check_models(require) -> bool:
    print("\n== per-model envelope ==")
    paths = sorted(glob.glob(str(HERE / "eval_out_d007" / "scorecard_d007_*.json")))
    if not paths:
        print("  (no model scorecards yet — skipping)")
        return not require
    ok = True
    seen = set()
    for p in paths:
        sc = json.loads(Path(p).read_text())
        model = sc.get("model", Path(p).stem)
        seen.add(model)
        errs = sc.get("n_errors", 0)
        det = (sc.get("detection_accuracy") or {}).get("pooled", {}).get("accuracy")
        bad = errs > sc.get("n_runs_total", 0) * 0.05
        print(f"  {model}: errors={errs} detection={det}  {'FAIL' if bad else 'ok'}")
        ok = ok and not bad
    if require:
        missing = [m for m in require if m not in seen]
        if missing:
            print(f"  FAIL: required models missing: {missing}")
            ok = False
    return ok


def main():
    require = []
    if "--require" in sys.argv:
        require = sys.argv[sys.argv.index("--require") + 1].split(",")
    results = [check_recompute(), check_scorer(), check_balance(), check_models(require)]
    print("\nGATE:", "PASS — dataset, scorer, and results are clean." if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
