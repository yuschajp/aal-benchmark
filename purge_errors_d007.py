"""
AAL-D-007 error-run purge. Removes any stored run whose score carries an error
(e.g. billing/rate-limit failures recorded before credits were topped up).
Backs up the original first; purged cases are refilled cleanly on the next run
of the driver (resume-safe).

Usage:
    python purge_errors_d007.py                                   # all eval_out_d007/eval_results_d007_*.json
    python purge_errors_d007.py eval_out_d007/eval_results_d007_claude-sonnet-5.json
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
               else [Path(p) for p in sorted(glob.glob("eval_out_d007/eval_results_d007_*.json"))])
    if not targets:
        sys.exit("no results files found under eval_out_d007/")
    for t in targets:
        purge(t)
    print("Done. Re-run the driver with the same command to refill purged cases.")


if __name__ == "__main__":
    main()
