"""
Batch-generate many randomized runs from engine_physics_model.py.

Replaces the old workflow of calling the simulator once per fault type with
fixed --severity/--onset_frac. Each run here draws its own severity, onset,
ambient temperature, flight-profile archetype and RNG seed, so the dataset
contains many distinct flights per class instead of one.

  severity    ~ Uniform(0.2, 0.9)    -- per SIH2026 project spec
  onset_frac  ~ Uniform(0.1, 0.55)   -- per SIH2026 project spec, narrowed from
                                         the originally proposed 0.1-0.7 upper
                                         bound: these are fixed-duration check
                                         flights (~7-13 min), and a fault whose
                                         onset lands past ~0.55 of the flight
                                         has too little of the flight left for
                                         it to plausibly have been *caught* on
                                         that flight at all -- an onset near
                                         the very end is a fault that gets
                                         flagged on the *next* flight, not
                                         this one, so it isn't this dataset's
                                         labeled event.
  ambient_offset_c ~ Uniform(-10, 30)  (deg C away from a 25 C reference day)
  archetype   ~ random choice of the 4 flight-profile archetypes
  near-miss healthy runs: a genuine high-load/hot excursion, no fault label

Writes one CSV per run into data/runs/, then combine_datasets.py concatenates
them into engine_master_dataset.csv.
"""
from __future__ import annotations

import argparse
import os
import random

from engine_physics_model import ARCHETYPES, FAULT_TYPES, generate_run, write_run_csv

# Only the original 4 "generic flight" archetypes are used for the labeled
# training dataset. engine_physics_model.ARCHETYPES also contains 4 Section-E
# environmental-scenario archetypes (high_altitude_cruise, hot_weather_ops,
# rapid_throttle_transition, endurance_mission) added later for the live demo's
# scenario picker -- those are meant to showcase one specific environmental
# condition each (e.g. endurance_mission runs 1200-1800s, ~4x a normal run),
# not to be sampled generically into the fault/healthy training set, where
# they would silently balloon dataset size and skew flight-profile balance
# per class. engine_master_dataset.csv has always been built from just these 4.
TRAINING_ARCHETYPES = ["short_hop", "long_cruise", "climb_cruise_descent", "variable_load"]


def build_plan(master_seed: int, n_healthy_normal: int, n_healthy_near_miss: int, n_per_fault: int):
    rng = random.Random(master_seed)
    plan = []
    counter = 0

    for _ in range(n_healthy_normal):
        counter += 1
        plan.append(dict(
            fault_type="healthy", severity=0.0, onset_frac=1.0,
            ambient_offset_c=rng.uniform(-10, 30),
            seed=master_seed * 1000 + counter,
            run_id=f"healthy_{counter:03d}",
            archetype=rng.choice(TRAINING_ARCHETYPES),
            near_miss=False,
        ))

    for _ in range(n_healthy_near_miss):
        counter += 1
        plan.append(dict(
            fault_type="healthy", severity=0.0, onset_frac=1.0,
            ambient_offset_c=rng.uniform(5, 30),
            seed=master_seed * 1000 + counter,
            run_id=f"healthy_nearmiss_{counter:03d}",
            archetype=rng.choice(["climb_cruise_descent", "variable_load"]),
            near_miss=True,
        ))

    for fault in FAULT_TYPES:
        for _ in range(n_per_fault):
            counter += 1
            plan.append(dict(
                fault_type=fault,
                severity=rng.uniform(0.2, 0.9),
                onset_frac=rng.uniform(0.1, 0.55),
                ambient_offset_c=rng.uniform(-10, 30),
                seed=master_seed * 1000 + counter,
                run_id=f"{fault}_{counter:03d}",
                archetype=rng.choice(TRAINING_ARCHETYPES),
                near_miss=False,
            ))

    rng.shuffle(plan)
    return plan


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--master_seed", type=int, default=42)
    p.add_argument("--out_dir", default="data/runs")
    p.add_argument("--healthy_normal", type=int, default=18)
    p.add_argument("--healthy_near_miss", type=int, default=6)
    p.add_argument("--per_fault", type=int, default=15)
    p.add_argument("--clean", action="store_true", help="remove existing CSVs in out_dir first")
    args = p.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    if args.clean:
        for f in os.listdir(args.out_dir):
            if f.endswith(".csv"):
                os.remove(os.path.join(args.out_dir, f))

    plan = build_plan(args.master_seed, args.healthy_normal, args.healthy_near_miss, args.per_fault)

    total_rows = 0
    for spec in plan:
        rows = generate_run(
            fault_type=spec["fault_type"],
            severity=spec["severity"],
            onset_frac=spec["onset_frac"],
            ambient_offset_c=spec["ambient_offset_c"],
            seed=spec["seed"],
            run_id=spec["run_id"],
            archetype=spec["archetype"],
            near_miss=spec["near_miss"],
        )
        write_run_csv(os.path.join(args.out_dir, f"{spec['run_id']}.csv"), rows)
        total_rows += len(rows)
        tag = "NEAR-MISS" if spec["near_miss"] else ""
        print(f"  {spec['run_id']:28s} fault={spec['fault_type']:18s} "
              f"severity={spec['severity']:.2f} onset={spec['onset_frac']:.2f} "
              f"ambient={spec['ambient_offset_c']:+.1f}C archetype={spec['archetype']:20s} "
              f"rows={len(rows)} {tag}")

    print(f"\nGenerated {len(plan)} runs, {total_rows} total rows, in {args.out_dir}/")


if __name__ == "__main__":
    main()
