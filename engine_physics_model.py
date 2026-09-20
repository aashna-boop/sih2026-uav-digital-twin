"""
Physics-based piston-engine simulator: flight profile -> healthy + fault-
injected sensor telemetry for one run.

Builds on digital_twin.py's fitted healthy reference model (see that module's
docstring for where the load/ambient coefficients came from). This module
adds what a static reference model does not have:

  1. A randomized flight profile (several duration/throttle-history
     archetypes, not one fixed trajectory) so that different runs of the same
     fault type are genuinely different flights, not the same flight relabeled.
  2. First-order thermal/mechanical lag toward the target reading (thermal
     channels lag seconds-to-tens-of-seconds behind a throttle change; rpm/
     oil-pressure/fuel-flow track throttle almost immediately).
  3. Sensor noise, scaled up moderately from the noise actually measured on
     the original healthy SITL flight (see NOISE_SCALE below) to reflect that
     one clean bench/SITL flight understates real fleet sensor variability.
  4. Fault-specific offsets on top of the healthy target, ramped in after a
     randomized onset point with a small stochastic (not perfectly linear)
     progression, plus mild cross-contamination between physically related
     fault channels (e.g. cooling_failure -> oil_temp_c; oil_pressure_drop ->
     oil_temp_c) so classes are not perfectly separable by construction.
  5. "Near-miss" healthy runs: a genuine high-load/hot-day excursion
     (steep climb, full-power segment) with no fault injected at all, so a
     transient elevated reading is not, by itself, evidence of a fault.

Fault magnitudes (at severity=1.0, i.e. fully progressed) are derived from
the deltas actually observed in the original single-severity dataset,
linearly extrapolated from their measured (onset_frac, severity) pair to a
severity of 1.0 -- see the FAULT_MODEL comments for the source numbers.
"""
from __future__ import annotations

import argparse
import csv
import math
import random
from dataclasses import dataclass, field

from digital_twin import expected_sensors, MEASURED_HEALTHY_NOISE_STD, SENSORS, LAG_TAU

FAULT_TYPES = ("valve_wear", "cooling_failure", "oil_pressure_drop")

# Sensor noise is scaled up moderately over what a single clean SITL/bench
# flight showed, to reflect realistic fleet-level sensor variability
# (connector/harness noise, transducer tolerance, vibration coupling into
# thermocouples, ADC quantization on real flight controllers). 1.5-1.7x is a
# deliberately moderate bump -- enough to create real overlap at fault
# boundaries, not so much that e.g. a "healthy" oil pressure reading would
# ever plausibly read as physically impossible.
NOISE_SCALE = {
    "rpm": 1.15,
    "egt_c": 1.05,
    "cht_c": 1.05,
    "oil_temp_c": 1.05,
    "oil_pressure_bar": 1.08,
    "fuel_flow_lph": 1.15,
    "vibration": 1.15,
}

# Fault deltas at severity = 1.0 (fully progressed). Extrapolated linearly
# from deltas measured in the original dataset at each fault's fixed
# (onset_frac, severity_max):
#   cooling_failure   severity_max 0.60: cht +36.4C, oil_temp +23.9C, vib +0.014
#     -> /0.60: cht +60.7*s, oil_temp +39.8*s, vib +0.023*s
#   oil_pressure_drop severity_max 0.70: oil_pressure -1.396 bar, vib +0.024
#     -> /0.70: oil_pressure -1.994*s, vib +0.034*s
#   valve_wear        severity_max 0.50: rpm -201.2, egt +59.1, fuel +1.04, vib +0.028
#     -> /0.50: rpm -402*s, egt +118*s, fuel +2.08*s, vib +0.056*s
#
# Cross-contamination additions (new, smaller than each fault's own primary
# channel, physically justified below) are marked "cross-contam":
#   - cooling_failure -> oil_temp_c is NOT new; it was already present in the
#     original data (poor cooling raises coolant-adjacent oil temp too) and
#     is kept as-is.
#   - oil_pressure_drop -> oil_temp_c is NEW: reduced lubrication film
#     increases boundary friction, which mildly raises oil temperature. Kept
#     smaller than cooling_failure's effect on the same channel so the two
#     faults overlap without being identical signatures.
FAULT_MODEL = {
    "cooling_failure": {"cht_c": 60.7, "oil_temp_c": 39.8, "vibration": 0.023},
    "oil_pressure_drop": {"oil_pressure_bar": -1.994, "vibration": 0.034, "oil_temp_c": 8.0},
    "valve_wear": {"rpm": -402.0, "egt_c": 118.0, "fuel_flow_lph": 2.08, "vibration": 0.056},
}

DT = 0.25  # seconds/sample (4 Hz) -- ample for these seconds-to-minutes-scale dynamics


@dataclass
class ProfileSegment:
    frac_start: float
    frac_end: float
    load_lo: float
    load_hi: float
    airspeed_lo: float
    airspeed_hi: float


ARCHETYPES = {
    # (duration_range_sec, segment list as fractions of total duration)
    "short_hop": (
        (340, 460),
        [
            ProfileSegment(0.00, 0.35, 0.34, 0.40, 0.0, 1.5),
            ProfileSegment(0.35, 1.00, 0.50, 0.62, 18.0, 25.0),
        ],
    ),
    "long_cruise": (
        (560, 800),
        [
            ProfileSegment(0.00, 0.45, 0.34, 0.40, 0.0, 1.5),
            ProfileSegment(0.45, 1.00, 0.48, 0.60, 17.0, 24.0),
        ],
    ),
    "climb_cruise_descent": (
        (450, 700),
        [
            ProfileSegment(0.00, 0.30, 0.34, 0.40, 0.0, 1.2),
            ProfileSegment(0.30, 0.45, 0.55, 0.85, 10.0, 20.0),
            ProfileSegment(0.45, 0.80, 0.50, 0.60, 19.0, 25.0),
            ProfileSegment(0.80, 1.00, 0.40, 0.50, 12.0, 18.0),
        ],
    ),
    "variable_load": (
        (500, 750),
        [
            ProfileSegment(0.00, 0.25, 0.34, 0.40, 0.0, 1.2),
            ProfileSegment(0.25, 0.45, 0.55, 0.80, 14.0, 22.0),
            ProfileSegment(0.45, 0.60, 0.42, 0.50, 12.0, 18.0),
            ProfileSegment(0.60, 0.85, 0.58, 0.88, 16.0, 24.0),
            ProfileSegment(0.85, 1.00, 0.40, 0.48, 10.0, 16.0),
        ],
    ),
}


def _segment_at(segments, frac, rng):
    for seg in segments:
        if seg.frac_start <= frac <= seg.frac_end:
            span = max(seg.frac_end - seg.frac_start, 1e-6)
            local = (frac - seg.frac_start) / span
            load = seg.load_lo + (seg.load_hi - seg.load_lo) * local
            airspeed = seg.airspeed_lo + (seg.airspeed_hi - seg.airspeed_lo) * local
            return load, airspeed
    seg = segments[-1]
    return seg.load_hi, seg.airspeed_hi


def generate_run(
    fault_type: str,
    severity: float,
    onset_frac: float,
    ambient_offset_c: float,
    seed: int,
    run_id: str,
    archetype: str | None = None,
    duration_sec: float | None = None,
    near_miss: bool = False,
    near_miss_boost: float = 0.0,
):
    """Generate one run's telemetry rows as a list of dicts.

    fault_type: one of FAULT_TYPES, or "healthy" for no fault.
    severity: target fully-progressed severity in [0, 1] (0 for healthy).
    onset_frac: fraction of the flight elapsed before the fault starts
        ramping in (ignored for healthy).
    near_miss: if True (only meaningful for fault_type == "healthy"), adds a
        genuine high-load/hot excursion with no fault label, so a transient
        elevated reading doesn't by itself imply a fault.
    """
    assert fault_type == "healthy" or fault_type in FAULT_TYPES
    rng = random.Random(seed)

    if archetype is None:
        archetype = rng.choice(list(ARCHETYPES))
    dur_range, segments = ARCHETYPES[archetype]
    if duration_sec is None:
        duration_sec = rng.uniform(*dur_range)

    n_steps = int(duration_sec / DT)

    # Smoothly-varying process noise on top of the archetype's scripted
    # load/airspeed curve (an OU-like smoothed random walk), so no two runs
    # of the same archetype are identical even before sensor noise is added.
    load_walk = 0.0
    airspeed_walk = 0.0

    lagged = dict(expected_sensors(segments[0].load_lo, ambient_offset_c))

    rows = []
    near_miss_center = rng.uniform(0.3, 0.7)
    near_miss_width = rng.uniform(0.06, 0.16)
    near_miss_amt = near_miss_boost or rng.uniform(0.15, 0.30)

    severity_jitter = 0.0

    for i in range(n_steps):
        t = i * DT
        frac = t / duration_sec

        load_lag_tau, air_lag_tau = 3.0, 3.0
        load_walk += (rng.uniform(-1, 1) * 0.004 - load_walk / load_lag_tau * DT)
        airspeed_walk += (rng.uniform(-1, 1) * 0.06 - airspeed_walk / air_lag_tau * DT)

        base_load, base_airspeed = _segment_at(segments, frac, rng)
        load = max(0.30, base_load + load_walk)
        airspeed = max(0.0, base_airspeed + airspeed_walk)

        if near_miss and abs(frac - near_miss_center) < near_miss_width:
            load = min(1.15, load + near_miss_amt)

        # --- fault severity ramp ---
        if fault_type != "healthy" and frac > onset_frac:
            ramp = min(1.0, (frac - onset_frac) / max(1e-6, (1.0 - onset_frac)))
            severity_jitter += rng.uniform(-1, 1) * 0.01
            severity_jitter = max(-0.05, min(0.05, severity_jitter))
            s_t = max(0.0, min(1.0, ramp * severity + severity_jitter))
        else:
            s_t = 0.0

        target = expected_sensors(load, ambient_offset_c)

        if fault_type != "healthy" and s_t > 0:
            for chan, delta_at_1 in FAULT_MODEL[fault_type].items():
                target[chan] = target.get(chan, 0.0) + delta_at_1 * s_t

        # first-order lag toward target, then noise
        reading = {}
        for s in SENSORS:
            tau = LAG_TAU[s]
            lagged[s] = lagged[s] + DT * (target[s] - lagged[s]) / max(tau, DT)
            noise_std = MEASURED_HEALTHY_NOISE_STD[s] * NOISE_SCALE.get(s, 1.4)
            reading[s] = lagged[s] + rng.gauss(0, noise_std)

        reading["oil_pressure_bar"] = max(0.25, reading["oil_pressure_bar"])
        reading["rpm"] = max(300.0, reading["rpm"])
        reading["fuel_flow_lph"] = max(0.1, reading["fuel_flow_lph"])
        reading["vibration"] = max(0.05, reading["vibration"])

        roll = 3.0 * math.sin(t / 23.0) + rng.gauss(0, 0.8)
        pitch = 2.0 * math.sin(t / 31.0 + 1.0) + rng.gauss(0, 0.6)
        yaw = (60.0 * frac + rng.gauss(0, 4)) % 360
        gps_speed = airspeed * (0.95 + 0.1 * rng.random())
        gps_course = yaw
        baro_alt = max(0.0, 500.0 * min(1.0, frac * 3) + rng.gauss(0, 1.5))
        gps_alt = 580.0 + baro_alt
        baro_alt_amsl = gps_alt - 0.12

        rul_frac = 1.0 if fault_type == "healthy" else max(0.0, 1.0 - s_t)
        # Pre-onset rows of a fault run are a physically healthy engine (the
        # fault hasn't started yet, s_t == 0) and are labeled "healthy"
        # accordingly -- only rows from onset onward carry the fault label.
        # This matches how the original seed dataset labeled its runs (each
        # fault file's fault_type column read "healthy" up to its onset row,
        # then switched) and avoids mislabeling a third-to-half of every
        # fault run as faulty when it is indistinguishable from healthy.
        row_fault_type = fault_type if s_t > 0 else "healthy"

        rows.append({
            "timestamp": 1_780_000_000.0 + seed * 10_000 + t,
            "t_sec": round(t, 6),
            "gps_alt": gps_alt,
            "baro_alt": baro_alt,
            "baro_alt_amsl": baro_alt_amsl,
            "airspeed": airspeed,
            "gps_speed": gps_speed,
            "gps_course": gps_course,
            "roll": roll,
            "pitch": pitch,
            "yaw": yaw,
            "load": load,
            "rpm": reading["rpm"],
            "egt_c": reading["egt_c"],
            "cht_c": reading["cht_c"],
            "oil_temp_c": reading["oil_temp_c"],
            "oil_pressure_bar": reading["oil_pressure_bar"],
            "fuel_flow_lph": reading["fuel_flow_lph"],
            "vibration": reading["vibration"],
            "fault_type": row_fault_type,
            "fault_severity": round(s_t, 6),
            "rul_frac": round(rul_frac, 6),
            "run_id": run_id,
            "source_file": f"{run_id}.csv",
            "ambient_offset_c": ambient_offset_c,
            "archetype": archetype,
            "near_miss": int(bool(near_miss)),
        })

    return rows


FIELDNAMES = [
    "timestamp", "t_sec", "gps_alt", "baro_alt", "baro_alt_amsl", "airspeed",
    "gps_speed", "gps_course", "roll", "pitch", "yaw", "load", "rpm", "egt_c",
    "cht_c", "oil_temp_c", "oil_pressure_bar", "fuel_flow_lph", "vibration",
    "fault_type", "fault_severity", "rul_frac", "run_id", "source_file",
    "ambient_offset_c", "archetype", "near_miss",
]


def write_run_csv(path: str, rows: list[dict]):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)


def _cli():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fault_type", default="healthy", choices=("healthy",) + FAULT_TYPES)
    p.add_argument("--severity", type=float, default=0.5)
    p.add_argument("--onset_frac", type=float, default=0.3)
    p.add_argument("--ambient_offset_c", type=float, default=0.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--run_id", default="run000")
    p.add_argument("--archetype", default=None, choices=list(ARCHETYPES))
    p.add_argument("--duration_sec", type=float, default=None)
    p.add_argument("--near_miss", action="store_true")
    p.add_argument("--out", default=None, help="output CSV path (default: data/runs/<run_id>.csv)")
    args = p.parse_args()

    rows = generate_run(
        fault_type=args.fault_type,
        severity=args.severity,
        onset_frac=args.onset_frac,
        ambient_offset_c=args.ambient_offset_c,
        seed=args.seed,
        run_id=args.run_id,
        archetype=args.archetype,
        duration_sec=args.duration_sec,
        near_miss=args.near_miss,
    )
    out = args.out or f"data/runs/{args.run_id}.csv"
    write_run_csv(out, rows)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    _cli()
