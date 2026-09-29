"""
AAL-D-007 telemetry split at an adaptive-budget boundary.

Why this exists
---------------
`run_d007_openai.py` carries a runtime adaptation: on an empty completion it
permanently raises the output-token budget for the remainder of the process
(`_STATE["min_tokens"] = 32000`, line ~94). This is remembered across cases, so
every call after the trigger runs under a different configuration than every call
before it.

That makes a single pooled latency figure for such a run misleading: it averages
two different configurations. D-006 recorded this as a methodology gap. This
script closes it by reporting the two segments separately, so the pre-adaptation
segment stays comparable to models that never adapted, and the post-adaptation
segment is labelled as what it is.

Accuracy metrics are unaffected by the budget change and are not touched here.
This script reports timing and token telemetry only.

The boundary is supplied explicitly via --adapt-case rather than inferred, because
the trigger is announced in the driver's stdout ("[adapt] empty output -> raising
output budget to 32000") and guessing it from the data would be less reliable than
reading it from the log. Runs whose completion_tokens exceed --base-cap are
reported as corroborating evidence, not used to set the boundary.

Usage:
    python telemetry_split_d007.py eval_out_d007/eval_results_d007_qwen_qwen3-8-max.json \
        --adapt-case 36 --out
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

SCORER_VERSION = "d007-telemetry-split-v1.0.0"
BASE_CAP_DEFAULT = 16384


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def summarize(latencies, completions, reasonings):
    if not latencies:
        return None
    return {
        "n_runs": len(latencies),
        "latency_s": {
            "p50": pct(latencies, 50),
            "p95": pct(latencies, 95),
            "mean": round(statistics.fmean(latencies), 3),
            "max": round(max(latencies), 3),
            "total": round(sum(latencies), 1),
        },
        "completion_tokens_mean": round(statistics.fmean(completions), 1) if completions else None,
        "reasoning_tokens_mean": round(statistics.fmean(reasonings), 1) if reasonings else None,
    }


def split(path: Path, adapt_case: int, base_cap: int) -> dict:
    data = json.loads(path.read_text())
    segs = {"pre": ([], [], []), "post": ([], [], [])}
    over_cap = []

    for idx, case in enumerate(data, 1):
        seg = "pre" if idx <= adapt_case else "post"
        for run in case.get("runs", []):
            if run.get("score", {}).get("error"):
                continue
            t = run.get("telemetry") or {}
            lat = t.get("latency_s")
            if lat is None:
                continue
            ct = t.get("completion_tokens") or 0
            rt = t.get("reasoning_tokens") or 0
            segs[seg][0].append(lat)
            segs[seg][1].append(ct)
            segs[seg][2].append(rt)
            if ct and ct > base_cap:
                over_cap.append({"case_id": case.get("case_id"), "case_index": idx,
                                 "completion_tokens": ct, "segment": seg})

    pre = summarize(*segs["pre"])
    post = summarize(*segs["post"])

    return {
        "scorer_version": SCORER_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": path.name,
        "n_cases_on_disk": len(data),
        "adapt_case": adapt_case,
        "base_output_cap": base_cap,
        "adapted_output_cap": 32000,
        "segments": {
            "pre_adaptation": {
                "cases": f"1-{adapt_case}",
                "output_budget": base_cap,
                "comparable_to_other_models": True,
                **(pre or {}),
            },
            "post_adaptation": {
                "cases": f"{adapt_case + 1}-{len(data)}",
                "output_budget": 32000,
                "comparable_to_other_models": False,
                **(post or {}),
            },
        },
        "runs_exceeding_base_cap": {
            "count": len(over_cap),
            "note": ("Runs whose reported completion_tokens exceed the base cap. Reported as "
                     "an observation only. completion_tokens may include reasoning tokens "
                     "depending on the provider, so this is not by itself proof that a run "
                     "executed under the raised budget."),
            "runs": over_cap[:50],
        },
        "publication_note": (
            "Pooled timing across both segments would average two different configurations "
            "and should not be published as a single figure. Report the pre-adaptation "
            "segment when comparing against models that did not adapt; report the "
            "post-adaptation segment separately and labelled. Accuracy metrics (detection, "
            "category, component, value, escalation) are unaffected by the output budget "
            "and remain comparable across the full run."
        ),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", help="eval_results_d007_*.json file")
    ap.add_argument("--adapt-case", type=int, required=True,
                    help="1-based case index of the LAST case before the budget was raised "
                         "(read it from the driver's [adapt] log line)")
    ap.add_argument("--base-cap", type=int, default=BASE_CAP_DEFAULT,
                    help=f"output budget before adaptation (default {BASE_CAP_DEFAULT})")
    ap.add_argument("--out", nargs="?", const="", default=None, metavar="PATH",
                    help="also write a JSON artifact (default: alongside the results file)")
    args = ap.parse_args()

    path = Path(args.results)
    if not path.exists():
        sys.exit(f"not found: {path}")
    if args.adapt_case < 1:
        sys.exit("--adapt-case must be >= 1")

    art = split(path, args.adapt_case, args.base_cap)
    pre = art["segments"]["pre_adaptation"]
    post = art["segments"]["post_adaptation"]

    print(f"{path.name}   {art['n_cases_on_disk']} cases on disk")
    print(f"adaptation boundary: after case {args.adapt_case} "
          f"({args.base_cap} -> {art['adapted_output_cap']} output tokens)\n")

    for label, seg in (("PRE  (comparable)", pre), ("POST (not comparable)", post)):
        if not seg.get("n_runs"):
            print(f"{label}: no scoreable runs")
            continue
        l = seg["latency_s"]
        print(f"{label}  cases {seg['cases']}  n={seg['n_runs']}")
        print(f"    latency p50 {l['p50']}s / p95 {l['p95']}s / max {l['max']}s")
        print(f"    completion tokens mean {seg['completion_tokens_mean']}  "
              f"reasoning mean {seg['reasoning_tokens_mean']}")

    oc = art["runs_exceeding_base_cap"]
    if oc["count"]:
        print(f"\n{oc['count']} run(s) reported completion_tokens above the "
              f"{args.base_cap} base cap (observation only — see artifact note)")

    if args.out is not None:
        out = Path(args.out) if args.out else path.with_name(
            path.name.replace("eval_results_", "telemetry_split_"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(art, indent=2) + "\n")
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
