from __future__ import annotations

import argparse
import csv
from pathlib import Path

from simulator.engine_plant import (
    DEGRADATION_RATES,
    RUL_HORIZON_MINUTES,
    FaultCommand,
    VirtualEnginePlant,
)
from simulator.flight_profile import (
    HELDOUT_DEMO_PROFILE_IDS,
    PROFILE_CATALOG,
    CatalogMissionProfile,
)
from twin.healthy_twin import HealthyEngineTwin
from twin.residuals import calculate_residuals


FAULT_SCENARIOS = (
    "HEALTHY",
    "COOLING_DEGRADATION",
    "LUBRICATION_DEGRADATION",
    "IGNITION_MISFIRE",
    "VALVE_WEAR",
)
AMBIENT_OFFSETS = (-20.0, 0.0, 25.0)
SEEDS = (26054, 26154)


def rate_for(profile_index: int, ambient_index: int, fault_index: int, seed_index: int) -> str:
    rates = tuple(DEGRADATION_RATES)
    return rates[(profile_index + ambient_index + fault_index + seed_index) % len(rates)]


def generate(output: Path, dt: float = 1.0) -> dict[str, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    profiles = [spec for spec in PROFILE_CATALOG if spec.profile_id not in HELDOUT_DEMO_PROFILE_IDS]
    fieldnames = [
        "timestamp",
        "t_sec",
        "profile_id",
        "ambient_offset_c",
        "run_id",
        "noise_seed",
        "flight_phase",
        "altitude_m",
        "airspeed_mps",
        "vertical_speed_mps",
        "throttle_pct",
        "ambient_temp_c",
        "fault_type",
        "fault_scenario",
        "degradation_rate",
        "fault_severity",
        "rul_frac",
        "rul_minutes",
        "failed",
    ]
    sensor_names = (
        "rpm",
        "cht_c",
        "egt_c",
        "oil_temp_c",
        "oil_pressure_bar",
        "fuel_flow_lph",
        "vibration",
    )
    for prefix in ("actual", "expected", "residual", "z"):
        fieldnames.extend(f"{prefix}_{name}" for name in sensor_names)

    runs = 0
    rows = 0
    failed_runs = 0
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for p_idx, spec in enumerate(profiles):
            for a_idx, ambient in enumerate(AMBIENT_OFFSETS):
                for f_idx, fault in enumerate(FAULT_SCENARIOS):
                    for s_idx, base_seed in enumerate(SEEDS):
                        noise_seed = base_seed + p_idx * 101 + a_idx * 17 + f_idx * 7
                        rate_name = rate_for(p_idx, a_idx, f_idx, s_idx)
                        rate = DEGRADATION_RATES[rate_name]
                        run_id = f"{spec.profile_id}__a{ambient:+.0f}__{fault.lower()}__{rate_name}__s{s_idx}"
                        profile = CatalogMissionProfile(spec, ambient_offset_c=ambient)
                        plant = VirtualEnginePlant(seed=noise_seed)
                        twin = HealthyEngineTwin()
                        warmup_sec = 45.0
                        if fault == "HEALTHY":
                            duration_sec = 360.0
                        else:
                            duration_sec = warmup_sec + (1.0 / rate) + 8.0

                        t = 0.0
                        run_failed = False
                        while t <= duration_sec:
                            enabled = fault != "HEALTHY" and t >= warmup_sec
                            plant.set_fault(
                                FaultCommand(
                                    fault=fault,
                                    degradation_rate=rate_name,
                                    enabled=enabled,
                                )
                            )
                            flight = profile.sample(t)
                            actual = plant.update(flight, dt)
                            expected = twin.update(flight, dt)
                            residuals, normalized = calculate_residuals(actual, expected)
                            severity = plant.severity if enabled else 0.0
                            observed_label = fault if severity >= 0.04 else "HEALTHY"
                            horizon = RUL_HORIZON_MINUTES[rate_name]
                            rul_frac = max(0.0, 1.0 - severity)
                            rul_minutes = rul_frac * horizon
                            failed = fault != "HEALTHY" and severity >= 0.999
                            run_failed = run_failed or failed

                            row = {
                                "timestamp": round(t, 3),
                                "t_sec": round(t, 3),
                                "profile_id": spec.profile_id,
                                "ambient_offset_c": ambient,
                                "run_id": run_id,
                                "noise_seed": noise_seed,
                                "flight_phase": flight.flight_phase,
                                "altitude_m": round(flight.altitude_m, 4),
                                "airspeed_mps": round(flight.airspeed_mps, 4),
                                "vertical_speed_mps": round(flight.vertical_speed_mps, 4),
                                "throttle_pct": round(flight.throttle_pct, 4),
                                "ambient_temp_c": round(flight.ambient_temp_c, 4),
                                "fault_type": observed_label,
                                "fault_scenario": fault,
                                "degradation_rate": rate_name,
                                "fault_severity": round(severity, 6),
                                "rul_frac": round(rul_frac, 6),
                                "rul_minutes": round(rul_minutes, 6),
                                "failed": int(failed),
                            }
                            for name in sensor_names:
                                row[f"actual_{name}"] = round(float(getattr(actual, name)), 6)
                                row[f"expected_{name}"] = round(float(getattr(expected, name)), 6)
                                row[f"residual_{name}"] = round(float(residuals[name]), 6)
                                row[f"z_{name}"] = round(float(normalized[name]), 6)
                            writer.writerow(row)
                            rows += 1
                            t += dt

                        runs += 1
                        failed_runs += int(run_failed)

    return {"profiles": len(profiles), "runs": runs, "rows": rows, "failed_runs": failed_runs}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate physics-informed SIH26054 trajectories")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/generated/engine_trajectories.csv"),
    )
    parser.add_argument("--dt", type=float, default=1.0)
    args = parser.parse_args()
    summary = generate(args.output, dt=args.dt)
    print({"output": str(args.output), **summary})


if __name__ == "__main__":
    main()
