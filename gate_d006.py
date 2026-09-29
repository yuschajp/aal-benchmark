"""
AAL-D-006 pre-publication gate. Stdlib only.

Runs three integrity checks and exits non-zero on any failure:
  1. dataset recompute check (independent second implementation)
  2. scorer contract tests (perfect / trap-overflag / wrong-category / malformed)
  3. per-model error & null-prediction envelope (any scorecards under eval_out_d006/)

Usage:  python gate_d006.py [--require model1,model2,...]
"""
from __future__ import annotations

import glob
import json
import subprocess
import sys
from pathlib import Path

from d006_common import build_master_plan, render_case
from d006_eval_common import build_prompt
from score_d006 import aggregate_d006, score_case_d006

HERE = Path(__file__).parent


def check_recompute() -> bool:
    print("== dataset recompute check ==")
    r = subprocess.run([sys.executable, "quality_checks/recompute_check_d006.py"],
                       cwd=HERE, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode == 0


def check_scorer() -> bool:
    print("\n== scorer contract tests ==")
    plan = build_master_plan()
    cases = [render_case(i + 1, plan[i]) for i in range(len(plan))]
    trap = next(c for c in cases if c["ground_truth"]["eligible"])
    inelig = next(c for c in cases if not c["ground_truth"]["eligible"])
    ok = True

    def expect(name, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}: {name}")
        ok = ok and cond

    # (a) no value leakage: the prompt must not print the ground-truth verdict
    p = build_prompt(inelig)
    expect("prompt hides ground_truth", ("reason_category" not in p.split("Return ONLY")[0]
                                         or inelig["ground_truth"]["reason_category"] not in p.split("Return ONLY")[0]))

    # (b) perfect prediction on an ineligible case scores all-correct
    gt = inelig["ground_truth"]
    perfect = {"eligible": False, "reason_category": gt["reason_category"],
               "offending_field": gt["offending_field"], "exception_value": gt["exception_value"],
               "substitution_recommendation": gt["valid_substitute_asset_types"][0], "should_escalate": True}
    s = score_case_d006(inelig, perfect)
    expect("perfect->detection", s["detection_correct"])
    expect("perfect->category", s["category_correct"])
    expect("perfect->value", s["value_correct"])
    expect("perfect->substitution", s["substitution_valid"])

    # (c) over-flagging a trap is caught as a false break
    s2 = score_case_d006(trap, {"eligible": False, "reason_category": "ELIG-RATING",
                                "offending_field": "rating", "exception_value": 0,
                                "substitution_recommendation": "CASH", "should_escalate": True})
    expect("trap over-flag -> false_break", s2.get("false_break") is True)

    # (d) wrong category still counts detection but fails category
    s3 = score_case_d006(inelig, {**perfect, "reason_category": "ELIG-DENOM"})
    expect("wrong category discrimination", s3["detection_correct"] and not s3["category_correct"])

    # (e) malformed prediction -> error
    expect("malformed -> error", score_case_d006(inelig, {"_error": "x"})["error"] is True)
    return ok


def check_models(require) -> bool:
    print("\n== per-model envelope ==")
    paths = sorted(glob.glob(str(HERE / "eval_out_d006" / "scorecard_d006_*.json")))
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
    results = [check_recompute(), check_scorer(), check_models(require)]
    print("\nGATE:", "PASS — dataset, scorer, and results are clean." if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
