"""
AAL-D-008 — manual ground-truth verification harness.

This exists to make the spec's MANDATORY human check practical:

    "Ground-truth-vs-source-data check, independent of the scorer. For each
     category, manually verify a sample of cases against the underlying
     source data -- confirm the case as presented actually contains
     sufficient information to derive the labeled ground truth."

That check is NOT "does the scorer agree with the scorecard" (both derive
from the same generator step, which is exactly how CALCDATE slipped through).
It is a human reading the two counterparty records as a model would see them
and answering one question:

    Could an analyst, given ONLY these two records, arrive at the labeled
    root cause and the labeled correct value?

If no, the case is a generator defect, not a hard case. Mark it rejected.

Verification decisions persist to a JSON file so review can happen across
multiple sessions without starting over. Nothing here sets
manually_verified_against_source on a GroundTruth object automatically at
generation time -- the flag is applied from this file's record of an
explicit human decision.

Usage:
    python review_d008.py --sample 3            # 3 cases per category (default)
    python review_d008.py --sample 5 --seed 8   # match a generator seed
    python review_d008.py --category RL-DIS-DAYCOUNT   # one category only
    python review_d008.py --status              # show progress, review nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from generate_d008 import generate_batch, GeneratedCase
from schemas.d008 import RootCauseCategory

DECISIONS_PATH = Path("eval_out_d008/verification_decisions.json")


def load_decisions() -> dict:
    if DECISIONS_PATH.exists():
        return json.loads(DECISIONS_PATH.read_text())
    return {}


def save_decisions(decisions: dict) -> None:
    DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DECISIONS_PATH.write_text(json.dumps(decisions, indent=2, sort_keys=True) + "\n")


def _fmt_record(label: str, rec) -> str:
    """Print a counterparty record the way a model sees it -- every field,
    no highlighting of which one was perturbed. A reviewer who is told where
    to look is not running the same check the model runs."""
    lines = [f"  {label}:"]
    # Every populated field, in schema order. A fixed list silently hid fields
    # added later (required_collateral, accrued_interest), so the reviewer saw
    # less than the model did.
    for field, val in rec.model_dump(mode="json").items():
        if val is None:
            continue
        lines.append(f"    {field:32s} {val}")
    return "\n".join(lines)


def review_case(case: GeneratedCase, index: int, total: int) -> str | None:
    ci, gt = case.case_input, case.ground_truth

    print("\n" + "=" * 78)
    print(f"[{index}/{total}]  {ci.case_id}   instrument={ci.instrument_type}")
    print("=" * 78)
    print("\nWHAT THE MODEL SEES (both records, verbatim, no hints):\n")
    print(_fmt_record("COUNTERPARTY A", ci.counterparty_a))
    print()
    print(_fmt_record("COUNTERPARTY B", ci.counterparty_b))

    gov = ci.governing_record
    print()
    if gov is None:
        print("  GOVERNING RECORD: none — nothing authoritative adjudicates this case.")
        print("    (Expected for NO-DISPUTE, INSUFFICIENT-DATA, and RECALL.)")
    else:
        print("  GOVERNING RECORD (trade confirmation, shown to the model):")
        print(f"    {'confirmation_id':32s} {gov.confirmation_id}")
        for field, val in gov.model_dump(mode="json").items():
            if val is None or field == "confirmation_id":
                continue
            print(f"    {field:32s} {val}")

    print("\n" + "-" * 78)
    print("LABELED GROUND TRUTH (generator-side, never shown to the model):")
    print("-" * 78)
    print(f"  discrepancy_detected   {gt.correct_discrepancy_detected}")
    print(f"  root_cause             {gt.correct_root_cause.value}")
    print(f"  correct_rate           {gt.correct_rate}")
    print(f"  correct_amount         {gt.correct_amount}")
    print(f"  boundary_distance      {gt.boundary_distance}")
    print(f"  is_trap (internal)     {ci.is_trap}")

    print("\n" + "-" * 78)
    print("THE QUESTION (not 'is this hard' -- 'is this ANSWERABLE'):")
    print("-" * 78)
    print("  Given ONLY the two records above, could an analyst derive both the")
    print("  labeled root cause AND the labeled correct value? If the evidence")
    print("  for the label is not present in the records, this is a generator")
    print("  defect -- the CALCDATE failure mode -- not a hard case.")
    print()
    print("  Note: for RL-DIS-INSUFFICIENT-DATA the correct answer is INVERTED --")
    print("  verify the case genuinely CANNOT be attributed from what is shown.")

    print("\n  [y] verified answerable   [n] defect, reject   [s] skip   [q] quit")
    while True:
        choice = input("  > ").strip().lower()
        if choice in ("y", "n", "s", "q"):
            return choice
        print("  enter y, n, s, or q")


def print_status(decisions: dict) -> None:
    by_cat = defaultdict(lambda: {"verified": 0, "rejected": 0})
    for rec in decisions.values():
        key = "verified" if rec["decision"] == "verified" else "rejected"
        by_cat[rec["category"]][key] += 1

    if not by_cat:
        print("No verification decisions recorded yet.")
        return

    print("=== D-008 Verification Status ===")
    for cat in sorted(by_cat):
        c = by_cat[cat]
        total = c["verified"] + c["rejected"]
        print(f"{cat:30s} {c['verified']:3d} verified, {c['rejected']:3d} rejected  ({total} reviewed)")

    total_rejected = sum(c["rejected"] for c in by_cat.values())
    print()
    if total_rejected:
        print(f"WARNING: {total_rejected} case(s) rejected as generator defects.")
        print("Fix the generator for those categories before running the pilot audit.")
    else:
        print("No defects found so far in reviewed cases.")


def main():
    ap = argparse.ArgumentParser(description="Manual ground-truth verification for AAL-D-008.")
    ap.add_argument("--sample", type=int, default=3,
                    help="cases to review per category (default 3)")
    ap.add_argument("--seed", type=int, default=8,
                    help="generator seed -- must match the batch you intend to publish")
    ap.add_argument("--n-cases", type=int, default=250,
                    help="size of the generated batch to sample from")
    ap.add_argument("--category", default=None,
                    help="review only this category (e.g. RL-DIS-DAYCOUNT)")
    ap.add_argument("--status", action="store_true",
                    help="print verification progress and exit")
    args = ap.parse_args()

    decisions = load_decisions()

    if args.status:
        print_status(decisions)
        return

    batch = generate_batch(n_cases=args.n_cases, seed=args.seed)

    # Group by category, then take the first N unreviewed from each -- so a
    # second session continues where the last one stopped instead of
    # re-presenting cases already decided.
    by_category: dict[RootCauseCategory, list[GeneratedCase]] = defaultdict(list)
    for case in batch:
        by_category[case.ground_truth.correct_root_cause].append(case)

    queue: list[GeneratedCase] = []
    for category, cases in by_category.items():
        if args.category and category.value != args.category:
            continue
        unreviewed = [c for c in cases if c.case_input.case_id not in decisions]
        queue.extend(unreviewed[:args.sample])

    if not queue:
        print("Nothing left to review for that selection.")
        print_status(decisions)
        return

    print(f"Reviewing {len(queue)} case(s). Decisions save after each one.")

    for i, case in enumerate(queue, 1):
        choice = review_case(case, i, len(queue))
        if choice == "q":
            print("\nStopped. Progress saved.")
            break
        if choice == "s":
            continue
        decisions[case.case_input.case_id] = {
            "decision": "verified" if choice == "y" else "rejected",
            "category": case.ground_truth.correct_root_cause.value,
            "seed": args.seed,
            "n_cases": args.n_cases,
        }
        save_decisions(decisions)

    print()
    print_status(decisions)
    print(f"\nDecisions file: {DECISIONS_PATH}")
    print("These record human judgments. They do NOT auto-set")
    print("manually_verified_against_source -- wiring that flag from this file")
    print("is a deliberate, separate step, per the spec.")


if __name__ == "__main__":
    main()
