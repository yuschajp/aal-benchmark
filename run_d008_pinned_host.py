"""
AAL-D-008 pinned-host test for DeepSeek V4 Pro.

The OpenRouter ablation pilot found DeepSeek's condition-B errors clustering
by upstream host: Azure 0/5, DeepInfra 0/3, while seven other hosts were
28/28. Routing was random, so each host saw only a handful of cases.

This removes the routing. Every condition-B case is sent to each named host
with fallbacks disabled, so all hosts answer the identical 96 cases. If the
difference is the host, it shows up on matched cases. If it was the luck of
which cases each host drew, it disappears.

Every call's reported host is checked against the host requested. A call
served by anything else is recorded as a routing mismatch and excluded from
scoring, never silently counted.

Needs OPENROUTER_API_KEY. Writes eval_out_d008/pinned_host_deepseek.json.

    python3 run_d008_pinned_host.py --limit 2      # smoke test: confirms slugs route
    python3 run_d008_pinned_host.py                # full run
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path

from openai import OpenAI

from d008_ablation import SYSTEM_INSTRUCTION_B, build_prompt_b
from d008_case_scorer import score_case_d008
from d008_eval_common import load_cases, strip_json_fences

MODEL = "deepseek/deepseek-v4-pro"
PILOT = ("RL-DIS-DAYCOUNT", "RL-DIS-SETTLEDATE", "RL-DIS-RECALL", "RL-DIS-HAIRCUT")
# OpenRouter provider slug -> the name OpenRouter reports back in responses
HOSTS = {"azure": "Azure", "deepinfra": "DeepInfra",
         "digitalocean": "DigitalOcean", "novita": "Novita"}
OUT = Path("eval_out_d008/pinned_host_deepseek.json")

client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"],
                base_url="https://openrouter.ai/api/v1", timeout=300)


def call(slug, prompt, attempts=4):
    last = None
    for i in range(attempts):
        try:
            r = client.chat.completions.create(
                model=MODEL, max_tokens=16384,
                messages=[{"role": "system", "content": SYSTEM_INSTRUCTION_B},
                          {"role": "user", "content": prompt}],
                extra_body={"provider": {"order": [slug], "allow_fallbacks": False}})
            extra = getattr(r, "model_extra", None) or {}
            return (r.choices[0].message.content or ""), extra.get("provider")
        except Exception as e:
            last = e
            time.sleep(5 * (i + 1))
    raise last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="cases per host; use 2 first to confirm each slug routes")
    args = ap.parse_args()

    cases = [c for c in load_cases() if c["ground_truth"]["correct_root_cause"] in PILOT]
    if args.limit:
        cases = cases[:args.limit]

    records = json.loads(OUT.read_text()) if OUT.exists() and not args.limit else []
    done = {(r["host_requested"], r["case_id"]) for r in records
            if not r["score"].get("error") and not r.get("routing_mismatch")}
    records = [r for r in records
               if not r["score"].get("error") and not r.get("routing_mismatch")]
    print(f"{len(cases)} cases x {len(HOSTS)} hosts; {len(done)} already scored\n")

    for slug, expected in HOSTS.items():
        for i, c in enumerate(cases, 1):
            if (slug, c["case_id"]) in done:
                continue
            rec = {"host_requested": slug, "case_id": c["case_id"],
                   "category": c["ground_truth"]["correct_root_cause"]}
            try:
                text, served_by = call(slug, build_prompt_b(c))
                rec["host_served"] = served_by
                if served_by != expected:
                    rec["routing_mismatch"] = True
                    rec["score"] = {"error": True,
                                    "error_detail": f"requested {expected}, served by {served_by}"}
                else:
                    pred = json.loads(strip_json_fences(text))
                    rec.update(prediction=pred, score=score_case_d008(c, pred))
            except Exception as e:
                rec["score"] = {"error": True, "error_detail": f"{type(e).__name__}: {e}"[:300]}
            records.append(rec)
            if not args.limit:
                OUT.parent.mkdir(parents=True, exist_ok=True)
                OUT.write_text(json.dumps(records, indent=2))
            flag = ("MISROUTED" if rec.get("routing_mismatch")
                    else "ERR" if rec["score"].get("error") else "ok")
            print(f"  {expected:13s} [{i:>3}/{len(cases)}] {c['case_id']}  {flag}")

    # ---- summary -----------------------------------------------------------
    t = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    issues = defaultdict(lambda: {"misrouted": 0, "error": 0})
    for r in records:
        s = r["score"]
        if r.get("routing_mismatch"):
            issues[r["host_requested"]]["misrouted"] += 1
            continue
        if s.get("error"):
            issues[r["host_requested"]]["error"] += 1
            continue
        if s.get("value_correct") is None:
            continue
        cell = t[r["host_requested"]][r["category"].replace("RL-DIS-", "")]
        cell[1] += 1
        cell[0] += bool(s["value_correct"])

    cats = [p.replace("RL-DIS-", "") for p in PILOT]
    print("\n=== DeepSeek V4 Pro, condition B value accuracy, pinned by host ===")
    print(f"  {'host':13s} " + " ".join(f"{c:>11s}" for c in cats) + f" {'TOTAL':>11s}  misrouted/err")
    for slug, name in HOSTS.items():
        row, tc, tn = [], 0, 0
        for c in cats:
            ok, n = t[slug][c]
            tc += ok; tn += n
            row.append(f"{ok}/{n}" if n else "-")
        tot = f"{tc}/{tn} {tc/tn:.0%}" if tn else "-"
        print(f"  {name:13s} " + " ".join(f"{x:>11s}" for x in row) + f" {tot:>11s}  "
              f"{issues[slug]['misrouted']}/{issues[slug]['error']}")
    if not args.limit:
        print(f"\nRaw results: {OUT}")


if __name__ == "__main__":
    main()
