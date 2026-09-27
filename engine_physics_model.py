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

Section E additions:
  6. Altitude-dependent engine physics: density-altitude corrections shift
     sensor targets at altitude via digital_twin.density_ratio().
  7. Four new flight archetypes for environmental scenario demos:
     high_altitude_cruise, hot_weather_ops, rapid_throttle_transition,
     endurance_mission.
  8. Battery/alternator simulation: voltage, current, SOC per timestep with
     Coulomb-counting and optional alternator-failure degradation.

Fault magnitudes (at severity=1.0, i.e. fully progressed) are derived from
the deltas actually observed in the original single-severity dataset,
linearly extrapolated from their measured (onset_frac, severity) pair to a
severity of 1.0 -- see the FAULT_MODEL comments for the source numbers.

Extended fault set (DRDO PS section C: "Fault Detection & Predictive
Analytics" names sensor drift/failure, misfire, injector abnormalities and
combustion instability explicitly; the original 3 classes did not cover
them). Four new fault types, added below with the same onset+severity-ramp
mechanic as the original three, but each needs a distinct *mechanism*, not
just a new set of FAULT_MODEL numbers, because none of them is a simple
steady offset from the healthy target:

  - misfire: intermittent, COMPLETE loss of one combustion event, not a
    continuous degradation. Modeled as a scheduled sequence of short
    (MISFIRE_EVENT_DUR_S) events whose *frequency* and *per-event
    amplitude* both increase with severity -- physically, a worsening
    misfire skips more often and each skip is a bigger torque loss.
    Applied directly to the post-noise reading (not the lagged target)
    since a misfire event is a genuine mechanical/thermal impulse, not a
    slow thermal-mass-limited change: RPM dips, vibration spikes, and EGT
    rises transiently (unburnt fuel afterburning in the exhaust, a well
    documented misfire symptom on real engines).
  - injector_fault: fuel delivery INCONSISTENCY, not a clean bias. Modeled
    as a static rich-mixture mean bias (FAULT_MODEL, below) plus a slow,
    irregular random-walk oscillation added on top of the fuel_flow
    target, so no two seconds of a demo look identical. The mean bias
    deliberately moves EGT and RPM in the OPPOSITE direction from
    valve_wear's (over-fueling runs cooler and rougher, not hotter and
    leaner) -- see FAULT_MODEL comment.
  - combustion_instability: erratic combustion "without a steady
    wear-based cause" per the PS wording -- i.e. more VARIANCE, not a
    dropped-out cylinder and not a directional trend. Modeled almost
    entirely as a severity-scaled INFLATION of the sensor noise on
    vibration/rpm/egt (see INSTABILITY_NOISE_MULT), with only a small
    static mean bias. This is a deliberate, honest design choice: our
    features are a *smoothed mean* residual (see train_model.py), which
    is close to blind to pure variance. combustion_instability is
    therefore expected to be the hardest of the 7 classes to separate
    from healthy and from misfire -- see MODEL_REPORT.md for how that
    played out.
  - sensor_fault: NOT an engine fault -- the engine stays healthy; one
    randomly chosen sensor channel starts lying (drift / flatline /
    noisy, chosen once per run). This gets its own top-level fault_type
    label rather than a flag alongside engine health, because (a) DRDO's
    PS lists it as its own detectable failure mode, and (b) the correct
    operator response is different (recalibrate/replace a sensor, not
    inspect the engine) -- the same human-in-the-loop advisory framing
    the rest of this project already uses. Applied AFTER the lag+noise
    pipeline, directly to the reported reading only, since a bad sensor
    has no thermal mass -- it just reports wrong, instantly. Its one
    genuinely distinguishing structural feature vs. the engine faults:
    it never touches more than one channel, where every engine fault here
    has a multi-channel signature.
"""
from __future__ import annotations

import argparse
import csv
import math
import random
from dataclasses import dataclass, field

from digital_twin import (
    expected_sensors, MEASURED_HEALTHY_NOISE_STD, SENSORS, LAG_TAU,
    density_ratio, expected_battery,
    BATTERY_FULL_CHARGE_AH, BATTERY_ALTERNATOR_CHARGE_A,
    BATTERY_BASELINE_DRAW_A, BATTERY_LOAD_DRAW_A,
)

FAULT_TYPES = (
    "valve_wear", "cooling_failure", "oil_pressure_drop",
    "misfire", "injector_fault", "combustion_instability", "sensor_fault",
)

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
# injector_fault and combustion_instability get a static mean-bias entry
# here too (on top of their own per-timestep mechanism below), so both
# still flow through the one existing `target[chan] += delta*s_t` loop.
# misfire and sensor_fault get no entry here (empty dict via .get() below)
# -- neither one is a clean additive bias on the healthy target; both are
# implemented as their own per-timestep blocks further down.
#
#   injector_fault (leaking/dribbling injector -> chronic over-fueling):
#     richer-than-commanded mixture burns COOLER, not hotter, and less
#     efficiently -- egt DOWN, rpm mildly down, fuel_flow up. This is the
#     opposite EGT direction from valve_wear (which runs LEANER/hotter as
#     compression leaks past worn valves), a deliberate, physically-real
#     point of separation between the two classes.
#   combustion_instability: kept deliberately small and directionless on
#     rpm/vibration (see module docstring) -- only a mild general
#     inefficiency bias on egt/fuel_flow, since "erratic... without a
#     steady wear-based cause" should not itself look like a trend.
FAULT_MODEL = {
    "cooling_failure": {"cht_c": 60.7, "oil_temp_c": 39.8, "vibration": 0.023},
    "oil_pressure_drop": {"oil_pressure_bar": -1.994, "vibration": 0.034, "oil_temp_c": 8.0},
    "valve_wear": {"rpm": -402.0, "egt_c": 118.0, "fuel_flow_lph": 2.08, "vibration": 0.056},
    "injector_fault": {"fuel_flow_lph": 2.6, "egt_c": -70.0, "rpm": -120.0},
    "combustion_instability": {"egt_c": 15.0, "fuel_flow_lph": 0.4},
}

# ── Extended-fault-set tuning constants (misfire / injector / instability / sensor_fault) ──
MISFIRE_EVENT_DUR_S = 0.3          # duration of one complete-combustion-loss event
MISFIRE_INTERVAL_RANGE_S = (7.0, 1.2)   # mean inter-event time at severity 0 -> severity 1
MISFIRE_RPM_DIP = (300.0, 500.0)   # (base, +per-severity) rpm dip during an event
MISFIRE_VIBRATION_SPIKE = (0.15, 0.35)
MISFIRE_EGT_SPIKE = (40.0, 80.0)   # unburnt-fuel afterburn in the exhaust
MISFIRE_FUEL_BUMP = (0.3, 0.6)

INJECTOR_WALK_AMPLITUDE = 0.35     # per-second random-walk kick on fuel_flow target, scaled by severity
INJECTOR_WALK_DECAY_S = 2.0        # OU-style decay constant back toward 0

# At severity 1.0, vibration/rpm/egt noise std is multiplied by this factor.
# vibration is hit hardest (that's the classic "rough running engine" tell);
# rpm and egt less so, since some of their variance is masked by the
# existing first-order lag.
INSTABILITY_NOISE_MULT = {"vibration": 4.5, "rpm": 2.5, "egt_c": 1.6}

# sensor_fault: how many multiples of that channel's OWN normal noise std
# the drift/noisy corruption reaches at severity 1.0. Deliberately large
# (>=6x) so a drifting/noisy sensor is not just "a bit more noise" but a
# clearly anomalous reading -- the realistic signature of a sensor a
# maintainer would actually flag, as opposed to a borderline judgment call.
SENSOR_DRIFT_STD_MULT = 6.0
SENSOR_NOISY_STD_MULT = 8.0
SENSOR_FAULT_MODES = ("drift", "flatline", "noisy")

DT = 0.25  # seconds/sample (4 Hz) -- ample for these seconds-to-minutes-scale dynamics


@dataclass
class ProfileSegment:
    frac_start: float
    frac_end: float
    load_lo: float
    load_hi: float
    airspeed_lo: float
    airspeed_hi: float
    altitude_lo: float = 0.0   # meters AGL — Section E addition
    altitude_hi: float = 0.0


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

    # ──── Section E scenario archetypes ────────────────────────────────
    "high_altitude_cruise": (
        (600, 900),
        [
            # Climb from ground to 3000 m
            ProfileSegment(0.00, 0.10, 0.34, 0.42, 0.0, 5.0, 0.0, 100.0),
            ProfileSegment(0.10, 0.30, 0.70, 0.90, 8.0, 18.0, 100.0, 3000.0),
            # Long cruise at 3000 m
            ProfileSegment(0.30, 0.75, 0.55, 0.65, 20.0, 26.0, 3000.0, 3000.0),
            # Descent back to ground
            ProfileSegment(0.75, 0.90, 0.40, 0.50, 15.0, 22.0, 3000.0, 800.0),
            ProfileSegment(0.90, 1.00, 0.34, 0.40, 5.0, 12.0, 800.0, 50.0),
        ],
    ),
    "hot_weather_ops": (
        (500, 700),
        [
            # Normal profile but ambient_offset is high (set externally);
            # moderate-to-high load to stress thermal channels
            ProfileSegment(0.00, 0.15, 0.34, 0.42, 0.0, 3.0),
            ProfileSegment(0.15, 0.35, 0.55, 0.75, 10.0, 20.0),
            ProfileSegment(0.35, 0.70, 0.60, 0.80, 18.0, 25.0),
            ProfileSegment(0.70, 0.85, 0.65, 0.90, 16.0, 24.0),
            ProfileSegment(0.85, 1.00, 0.40, 0.50, 8.0, 15.0),
        ],
    ),
    "rapid_throttle_transition": (
        (400, 600),
        [
            # Many short segments with abrupt load changes
            ProfileSegment(0.00, 0.08, 0.34, 0.40, 0.0, 3.0),
            ProfileSegment(0.08, 0.18, 0.85, 0.95, 15.0, 24.0),
            ProfileSegment(0.18, 0.25, 0.35, 0.42, 5.0, 10.0),
            ProfileSegment(0.25, 0.35, 0.80, 0.92, 18.0, 26.0),
            ProfileSegment(0.35, 0.42, 0.38, 0.45, 8.0, 14.0),
            ProfileSegment(0.42, 0.52, 0.88, 0.98, 20.0, 28.0),
            ProfileSegment(0.52, 0.60, 0.40, 0.48, 10.0, 16.0),
            ProfileSegment(0.60, 0.72, 0.82, 0.95, 17.0, 25.0),
            ProfileSegment(0.72, 0.82, 0.35, 0.42, 6.0, 12.0),
            ProfileSegment(0.82, 0.92, 0.75, 0.88, 14.0, 22.0),
            ProfileSegment(0.92, 1.00, 0.34, 0.40, 3.0, 8.0),
        ],
    ),
    "endurance_mission": (
        (1200, 1800),
        [
            # Very long, low-to-moderate steady cruise
            ProfileSegment(0.00, 0.08, 0.34, 0.40, 0.0, 3.0),
            ProfileSegment(0.08, 0.15, 0.45, 0.55, 8.0, 16.0, 0.0, 500.0),
            ProfileSegment(0.15, 0.85, 0.48, 0.55, 16.0, 22.0, 500.0, 500.0),
            ProfileSegment(0.85, 0.95, 0.42, 0.48, 12.0, 18.0, 500.0, 100.0),
            ProfileSegment(0.95, 1.00, 0.34, 0.40, 3.0, 8.0, 100.0, 0.0),
        ],
    ),
}

# Scenario-specific default settings (ambient_offset_c, altitude base offset)
SCENARIO_DEFAULTS = {
    "high_altitude_cruise": {"ambient_offset_c": -5.0},   # cooler at altitude
    "hot_weather_ops":      {"ambient_offset_c": 30.0},    # 55°C day
    "rapid_throttle_transition": {"ambient_offset_c": 5.0},
    "endurance_mission":    {"ambient_offset_c": 0.0},
}


def _segment_at(segments, frac, rng):
    for seg in segments:
        if seg.frac_start <= frac <= seg.frac_end:
            span = max(seg.frac_end - seg.frac_start, 1e-6)
            local = (frac - seg.frac_start) / span
            load = seg.load_lo + (seg.load_hi - seg.load_lo) * local
            airspeed = seg.airspeed_lo + (seg.airspeed_hi - seg.airspeed_lo) * local
            altitude = seg.altitude_lo + (seg.altitude_hi - seg.altitude_lo) * local
            return load, airspeed, altitude
    seg = segments[-1]
    return seg.load_hi, seg.airspeed_hi, seg.altitude_hi


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
    alternator_failure_frac: float | None = None,
):
    """Generate one run's telemetry rows as a list of dicts.

    fault_type: one of FAULT_TYPES, or "healthy" for no fault.
    severity: target fully-progressed severity in [0, 1] (0 for healthy).
    onset_frac: fraction of the flight elapsed before the fault starts
        ramping in (ignored for healthy).
    near_miss: if True (only meaningful for fault_type == "healthy"), adds a
        genuine high-load/hot excursion with no fault label, so a transient
        elevated reading doesn't by itself imply a fault.
    alternator_failure_frac: if set, the alternator fails at this fraction
        of the flight, causing battery SOC to drain (Section E).
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

    lagged = dict(expected_sensors(segments[0].load_lo, ambient_offset_c,
                                   segments[0].altitude_lo))

    # Battery state — Coulomb counting
    battery_soc_pct = 100.0
    battery_soc_ah = BATTERY_FULL_CHARGE_AH

    rows = []
    near_miss_center = rng.uniform(0.3, 0.7)
    near_miss_width = rng.uniform(0.06, 0.16)
    near_miss_amt = near_miss_boost or rng.uniform(0.15, 0.30)

    severity_jitter = 0.0

    # ── Extended-fault-set per-run state ──
    # sensor_fault: which channel lies, and how (chosen once per run, not
    # per-timestep, since a real sensor doesn't switch failure modes mid-flight).
    sf_channel = rng.choice(list(SENSORS))
    sf_mode = rng.choice(SENSOR_FAULT_MODES)
    sf_drift_dir = rng.choice([-1.0, 1.0])
    sf_frozen_value = None
    # injector_fault: slow OU-style random walk on top of the static fuel bias.
    injector_walk = 0.0
    # misfire: scheduled next-event time / current-event state.
    misfire_next_event_t = None
    misfire_event_until = -1.0
    misfire_event_amp = 0.0

    for i in range(n_steps):
        t = i * DT
        frac = t / duration_sec

        load_lag_tau, air_lag_tau = 3.0, 3.0
        load_walk += (rng.uniform(-1, 1) * 0.004 - load_walk / load_lag_tau * DT)
        airspeed_walk += (rng.uniform(-1, 1) * 0.06 - airspeed_walk / air_lag_tau * DT)

        base_load, base_airspeed, base_altitude = _segment_at(segments, frac, rng)
        load = max(0.30, base_load + load_walk)
        airspeed = max(0.0, base_airspeed + airspeed_walk)
        altitude_m = max(0.0, base_altitude)

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

        target = expected_sensors(load, ambient_offset_c, altitude_m)

        if fault_type != "healthy" and s_t > 0:
            for chan, delta_at_1 in FAULT_MODEL.get(fault_type, {}).items():
                target[chan] = target.get(chan, 0.0) + delta_at_1 * s_t

            # injector_fault: irregular fuel-flow oscillation on top of the
            # static rich-mixture bias above -- an OU-style random walk so
            # the irregularity itself grows with severity, not just its bias.
            if fault_type == "injector_fault":
                injector_walk += (rng.uniform(-1, 1) * INJECTOR_WALK_AMPLITUDE * s_t
                                   - injector_walk / INJECTOR_WALK_DECAY_S * DT)
                target["fuel_flow_lph"] = target.get("fuel_flow_lph", 0.0) + injector_walk

        # misfire: schedule/advance discrete combustion-loss events. Frequency
        # AND per-event amplitude both scale with severity (see module
        # docstring). Applied to `reading`, not `target`, below -- a misfire
        # is a mechanical/thermal impulse, not something with thermal lag.
        misfire_event_active = False
        if fault_type == "misfire" and s_t > 0:
            if misfire_next_event_t is None:
                misfire_next_event_t = t + rng.uniform(1.0, 4.0)
            if t >= misfire_next_event_t and t >= misfire_event_until:
                misfire_event_until = t + MISFIRE_EVENT_DUR_S
                misfire_event_amp = 0.6 + 0.4 * rng.random()
                interval_mean = max(0.5, MISFIRE_INTERVAL_RANGE_S[0]
                                     + (MISFIRE_INTERVAL_RANGE_S[1] - MISFIRE_INTERVAL_RANGE_S[0]) * s_t)
                misfire_next_event_t = t + max(0.4, rng.gauss(interval_mean, interval_mean * 0.3))
            misfire_event_active = t < misfire_event_until

        # first-order lag toward target, then noise
        reading = {}
        for s in SENSORS:
            tau = LAG_TAU[s]
            lagged[s] = lagged[s] + DT * (target[s] - lagged[s]) / max(tau, DT)
            noise_std = MEASURED_HEALTHY_NOISE_STD[s] * NOISE_SCALE.get(s, 1.4)
            if fault_type == "combustion_instability" and s_t > 0:
                # Erratic-but-present combustion: inflate variance rather than
                # shift the mean (see module docstring on why this is the
                # hardest class for a mean-residual-only classifier to catch).
                noise_std *= 1.0 + (INSTABILITY_NOISE_MULT.get(s, 1.0) - 1.0) * s_t
            reading[s] = lagged[s] + rng.gauss(0, noise_std)

        if misfire_event_active:
            amp = misfire_event_amp
            reading["rpm"] -= (MISFIRE_RPM_DIP[0] + MISFIRE_RPM_DIP[1] * s_t) * amp
            reading["vibration"] += (MISFIRE_VIBRATION_SPIKE[0] + MISFIRE_VIBRATION_SPIKE[1] * s_t) * amp
            reading["egt_c"] += (MISFIRE_EGT_SPIKE[0] + MISFIRE_EGT_SPIKE[1] * s_t) * amp
            reading["fuel_flow_lph"] += (MISFIRE_FUEL_BUMP[0] + MISFIRE_FUEL_BUMP[1] * s_t) * amp

        reading["oil_pressure_bar"] = max(0.25, reading["oil_pressure_bar"])
        reading["rpm"] = max(300.0, reading["rpm"])
        reading["fuel_flow_lph"] = max(0.1, reading["fuel_flow_lph"])
        reading["vibration"] = max(0.05, reading["vibration"])

        # sensor_fault: applied LAST, directly to the reported reading only
        # (not target/lagged, which stay genuinely healthy -- the engine is
        # fine, one sensor is lying). Deliberately not re-clamped afterward:
        # an out-of-plausible-range reading is the realistic signature of a
        # failed sensor, not something a healthy-engine clamp should hide.
        if fault_type == "sensor_fault" and s_t > 0:
            base_std = MEASURED_HEALTHY_NOISE_STD[sf_channel] * NOISE_SCALE.get(sf_channel, 1.4)
            if sf_mode == "drift":
                reading[sf_channel] += sf_drift_dir * SENSOR_DRIFT_STD_MULT * base_std * s_t
            elif sf_mode == "flatline":
                if sf_frozen_value is None:
                    sf_frozen_value = reading[sf_channel]
                reading[sf_channel] = sf_frozen_value
            else:  # "noisy"
                reading[sf_channel] += rng.gauss(0, SENSOR_NOISY_STD_MULT * base_std * s_t)

        roll = 3.0 * math.sin(t / 23.0) + rng.gauss(0, 0.8)
        pitch = 2.0 * math.sin(t / 31.0 + 1.0) + rng.gauss(0, 0.6)
        yaw = (60.0 * frac + rng.gauss(0, 4)) % 360
        gps_speed = airspeed * (0.95 + 0.1 * rng.random())
        gps_course = yaw
        baro_alt = max(0.0, altitude_m + rng.gauss(0, 1.5))
        gps_alt = 580.0 + baro_alt
        baro_alt_amsl = gps_alt - 0.12

        # ── Battery / alternator simulation ──
        alt_healthy = True
        if alternator_failure_frac is not None and frac >= alternator_failure_frac:
            alt_healthy = False

        draw = BATTERY_BASELINE_DRAW_A + BATTERY_LOAD_DRAW_A * max(0.0, load)
        charge = BATTERY_ALTERNATOR_CHARGE_A if alt_healthy else 0.0
        net_current = draw - charge  # positive = discharging
        battery_soc_ah -= (net_current * DT / 3600.0)
        battery_soc_ah = max(0.0, min(BATTERY_FULL_CHARGE_AH, battery_soc_ah))
        battery_soc_pct = (battery_soc_ah / BATTERY_FULL_CHARGE_AH) * 100.0

        batt = expected_battery(load, battery_soc_pct, alt_healthy)
        # Add small noise to battery readings
        batt_v = batt["battery_voltage_v"] + rng.gauss(0, 0.05)
        batt_a = max(0.0, batt["battery_current_a"] + rng.gauss(0, 0.1))

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
            "altitude_m": round(altitude_m, 2),
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
            "battery_voltage_v": round(batt_v, 3),
            "battery_current_a": round(batt_a, 3),
            "battery_soc_pct": round(battery_soc_pct, 2),
            "fault_type": row_fault_type,
            "fault_severity": round(s_t, 6),
            "rul_frac": round(rul_frac, 6),
            "run_id": run_id,
            "source_file": f"{run_id}.csv",
            "ambient_offset_c": ambient_offset_c,
            "archetype": archetype,
            "near_miss": int(bool(near_miss)),
            # Diagnostic-only columns (not used as model features): which
            # channel/mode a sensor_fault run picked, so per-subtype recall
            # can be checked honestly in MODEL_REPORT.md. Blank otherwise.
            "sensor_fault_channel": sf_channel if fault_type == "sensor_fault" else "",
            "sensor_fault_mode": sf_mode if fault_type == "sensor_fault" else "",
        })

    return rows


FIELDNAMES = [
    "timestamp", "t_sec", "gps_alt", "baro_alt", "baro_alt_amsl", "altitude_m",
    "airspeed", "gps_speed", "gps_course", "roll", "pitch", "yaw", "load",
    "rpm", "egt_c", "cht_c", "oil_temp_c", "oil_pressure_bar", "fuel_flow_lph",
    "vibration", "battery_voltage_v", "battery_current_a", "battery_soc_pct",
    "fault_type", "fault_severity", "rul_frac", "run_id", "source_file",
    "ambient_offset_c", "archetype", "near_miss",
    "sensor_fault_channel", "sensor_fault_mode",
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
    p.add_argument("--alternator_failure_frac", type=float, default=None,
                   help="Fraction of flight at which alternator fails (0-1)")
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
        alternator_failure_frac=args.alternator_failure_frac,
    )
    out = args.out or f"data/runs/{args.run_id}.csv"
    write_run_csv(out, rows)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    _cli()

