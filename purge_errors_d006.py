"""
AAL-D-006 error-run purge. Removes any stored run whose score carries an error
(e.g. the GPT-5.6 empty-output runs recorded before the driver self-healed).
Backs up the original first; purged cases are refilled cleanly on the next run
of the driver (resume-safe).

Usage:
    python purge_errors_d006.py                                   # all eval_out_d006/eval_results_d006_*.json
    python purge_errors_d006.py eval_out_d006/eval_results_d006_gpt-5-6-sol.json
"""
import glob
import json
import sys
from pathlib import Path


def purge(path: Path) -> None:
    original = path.read_text()
    records = json.loads(original)
    purged = 0
    for rec in records:
        before = len(rec["runs"])
        rec["runs"] = [r for r in rec["runs"] if not r.get("score", {}).get("error")]
        purged += before - len(rec["runs"])
    if purged == 0:
        print(f"{path.name}: no error-runs found, nothing to purge.")
        return
    path.with_name(path.name + ".prepurge.bak").write_text(original)
    path.write_text(json.dumps(records, indent=2))
    emptied = sum(1 for rec in records if len(rec["runs"]) == 0)
    print(f"{path.name}: purged {purged} error-run(s); {emptied} case(s) now empty "
          f"and will be re-run on resume. Backup written.")


def main():
    targets = ([Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1
               else [Path(p) for p in sorted(glob.glob("eval_out_d006/eval_results_d006_*.json"))])
    if not targets:
        sys.exit("no results files found under eval_out_d006/")
    for t in targets:
        purge(t)
    print("Done. Re-run the driver with the same command to refill purged cases.")


if __name__ == "__main__":
    main()
