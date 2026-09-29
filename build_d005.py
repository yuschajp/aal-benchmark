#!/usr/bin/env python3
"""AAL-D-005 one-shot build: generate all 5 batches (each gated by qa_assert_case),
validate + merge into AAL-D-005-v1.0.json, then run the independent recompute check.
Run from the aal-benchmark directory:  python3 build_d005.py"""
import runpy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)


def run(mod):
    print(f"\n=== {mod} ===")
    runpy.run_path(os.path.join(HERE, mod), run_name="__main__")


def main():
    for n in range(1, 6):
        run(f"generate_d005_batch{n:03d}.py")
    run("generate_d005_manifest.py")
    run(os.path.join("quality_checks", "recompute_check_d005.py"))
    print("\nAAL-D-005 build complete. Dataset at datasets/AAL-D-005/AAL-D-005-v1.0.json")


if __name__ == "__main__":
    main()
