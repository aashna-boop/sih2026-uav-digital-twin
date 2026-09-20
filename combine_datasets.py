"""
Merge individual run CSVs (data/runs/*.csv) into engine_master_dataset.csv.
"""
from __future__ import annotations

import argparse
import glob
import os

import pandas as pd


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs_dir", default="data/runs")
    p.add_argument("--out", default="engine_master_dataset.csv")
    args = p.parse_args()

    paths = sorted(glob.glob(os.path.join(args.runs_dir, "*.csv")))
    if not paths:
        raise SystemExit(f"No CSVs found in {args.runs_dir}")

    frames = [pd.read_csv(p) for p in paths]
    combined = pd.concat(frames, ignore_index=True)
    combined.to_csv(args.out, index=False)

    print(f"Combined {len(paths)} run files into {args.out}")
    print(f"Total rows: {len(combined)}")
    print("\nPer fault_type row counts:")
    print(combined["fault_type"].value_counts())
    print("\nPer run_id fault_type (first 5 and last 5 runs):")
    per_run = combined.groupby("run_id")["fault_type"].first()
    print(per_run.head())
    print("...")
    print(per_run.tail())
    print(f"\nTotal distinct runs: {combined['run_id'].nunique()}")


if __name__ == "__main__":
    main()
