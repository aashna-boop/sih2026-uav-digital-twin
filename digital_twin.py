"""
Physics-based healthy digital-twin reference model.

This module is the single source of truth for "what should a healthy engine's
sensors read, given the current operating point (throttle/load and ambient
temperature)?" It is deliberately a static, noise-free, lag-free model — a
simplified physical reference, not a replay of any one recorded flight.

Two places use it, and only one set of coefficients has to be maintained:

1. engine_physics_model.py adds first-order thermal/mechanical lag, sensor
   noise, and fault-specific offsets on top of these targets to *simulate*
   raw telemetry for a run.
2. train_model.py subtracts this model's prediction from the *actual* reading
   at each timestep to build the residual features the ML classifier is
   trained on — i.e. "digital twin expected value -> residual -> ML fault
   detection", matching the intended system architecture. Because the
   reference model only depends on the current (load, ambient) operating
   point and not on which run/file a row came from, it introduces no
   run-identity leakage into the residual features.

Coefficients below were fit by ordinary least squares against the real
SITL-derived healthy flight (engine_healthy.csv, single flight, n=16179) that
was the seed data for this project: each sensor regressed against `load`
alone gave R² of 0.70-0.99, confirming these are the dominant physical
relationships already present in the source telemetry. We keep those fitted
slopes/intercepts unchanged and add a mild, physically-motivated ambient-
temperature term to the three thermal channels (hotter ambient air impairs
convective cooling; colder ambient improves it) since the original seed
flight had constant ambient and could not inform that term on its own.

Section E additions (altitude & battery):
- ISA standard-atmosphere density ratio corrects engine output for altitude.
  Naturally-aspirated piston engines lose power roughly as σ (air-density
  ratio) decreases, affecting RPM, temperatures, fuel flow and oil pressure.
- Battery/alternator baseline: a simple voltage-curve + Coulomb-counting
  model produces healthy reference values for onboard electrical health
  monitoring (voltage, current, state-of-charge).
"""
from __future__ import annotations

import math

SENSORS = ("rpm", "egt_c", "cht_c", "oil_temp_c", "oil_pressure_bar", "fuel_flow_lph", "vibration")

# Fitted on load alone (OLS against engine_healthy.csv):
#   rpm               slope 4099.6   intercept 1700.1   R^2 (corr^2) 0.999
#   egt_c             slope  300.9   intercept  649.6    corr 0.988
#   cht_c             slope  114.0   intercept   31.3    corr 0.725
#   oil_temp_c        slope   77.8   intercept   23.7    corr 0.700
#   oil_pressure_bar  slope    3.50  intercept    1.50    corr 0.991
#   fuel_flow_lph     slope   22.42  intercept    0.43    corr 0.991
_LOAD_SLOPE = {
    "rpm": 4099.6,
    "egt_c": 300.9,
    "cht_c": 114.0,
    "oil_temp_c": 77.8,
    "oil_pressure_bar": 3.50,
    "fuel_flow_lph": 22.42,
}
_LOAD_INTERCEPT = {
    "rpm": 1700.1,
    "egt_c": 649.6,
    "cht_c": 31.3,
    "oil_temp_c": 23.7,
    "oil_pressure_bar": 1.50,
    "fuel_flow_lph": 0.43,
}

# Vibration has no reliable single-flight OLS fit (it's dominated by
# mechanical/structural coupling, not thermal load), so we use a simple,
# openly-approximate load-linear baseline instead of a fitted one.
_LOAD_SLOPE["vibration"] = 0.20
_LOAD_INTERCEPT["vibration"] = 0.85

# First-order thermal/mechanical lag time constants (seconds) toward the
# instantaneous load-based target above. Thermal mass makes CHT/oil-temp
# slow to respond to a throttle change; EGT responds faster (thin exhaust
# gas, low thermal mass); rpm/oil-pressure/fuel-flow/vibration are governed
# almost immediately by throttle position/mechanical linkage.
LAG_TAU = {
    "rpm": 1.2,
    "egt_c": 2.6,
    "cht_c": 9.0,
    "oil_temp_c": 15.5,
    "oil_pressure_bar": 1.0,
    "fuel_flow_lph": 1.0,
    "vibration": 0.6,
}

# Ambient offset (deg C away from a 25 C reference day) shifts only the
# thermal channels, and only mildly -- cooling effectiveness depends on
# ambient air temperature, but rpm/oil-pressure/fuel-flow governance in a
# piston aero engine is set by mixture/throttle, not ambient air temp.
# oil_temp_c raised from 0.35 to 0.45 so hot-day demos show a more visible
# temperature effect. Vibration gets a small ambient sensitivity (hotter
# lubricant → thinner film → slightly more mechanical contact noise).
_AMBIENT_COEF = {
    "egt_c": 0.30,
    "cht_c": 0.50,
    "oil_temp_c": 0.45,
    "vibration": 0.008,
}


# ─── Altitude / density-altitude model ────────────────────────────────────
# ISA troposphere model (valid to ~11 km / FL360, well above UAV ops):
#   T(h) = T₀ − L·h ,   σ(h) = (T(h)/T₀)^(g/(R·L) − 1)
# where T₀=288.15 K, L=0.0065 K/m, g=9.80665, R=287.05.
# The exponent g/(R·L)−1 ≈ 4.2559.

_ISA_T0 = 288.15           # sea-level standard temperature (K)
_ISA_LAPSE = 0.0065         # temperature lapse rate (K/m)
_ISA_EXPONENT = 4.2559      # (g / (R * L)) - 1


def density_ratio(altitude_m: float) -> float:
    """Air-density ratio σ = ρ(h)/ρ₀ via ISA standard atmosphere.

    Returns 1.0 at sea level, ~0.74 at 3000 m, ~0.69 at 3500 m.
    Clamped to [0, 11000] m (troposphere).
    """
    h = max(0.0, min(altitude_m, 11000.0))
    return max(0.1, (1.0 - _ISA_LAPSE * h / _ISA_T0) ** _ISA_EXPONENT)


# Per-sensor altitude scaling. For a naturally-aspirated piston engine:
#   RPM  — power (and therefore governed RPM) falls roughly as σ^0.5
#   EGT  — less fuel burned at altitude → lower exhaust temperature; scales ~σ^0.3
#   CHT  — less heat generated but also less cooling air → mild net decrease ~σ^0.15
#   oil_temp — less heat input, reduced cooling → near-neutral, slight decrease ~σ^0.10
#   oil_pressure — oil pump is engine-driven; lower RPM → lower pressure ~σ^0.25
#   fuel_flow — properly leaned at altitude; mixture follows air density ~σ^0.7
#   vibration — minimal altitude effect
_ALT_POWER = {
    "rpm": 0.50,
    "egt_c": 0.30,
    "cht_c": 0.15,
    "oil_temp_c": 0.10,
    "oil_pressure_bar": 0.25,
    "fuel_flow_lph": 0.70,
    "vibration": 0.0,
}


# ─── Battery / alternator baseline model ──────────────────────────────────
# Simple healthy-alternator electrical system:
#   voltage  — alternator regulator holds ~12.6 V under load (±0.4 V with load)
#   current  — proportional to electrical load (avionics + servos), roughly
#              tracks engine load as a proxy for mission intensity
#   SOC      — starts at 100%, Coulomb-counting drain; alternator recharges
#              continuously so healthy SOC stays >90% in normal ops
BATTERY_NOMINAL_V = 12.6
BATTERY_FULL_CHARGE_AH = 5.0      # 5 Ah LiPo/NiMH typical for UAV aux battery
BATTERY_BASELINE_DRAW_A = 1.2     # avionics quiescent draw
BATTERY_LOAD_DRAW_A = 2.5         # extra draw proportional to engine load
BATTERY_ALTERNATOR_CHARGE_A = 4.0 # healthy alternator output


def expected_battery(load: float, soc_pct: float = 100.0,
                     alternator_healthy: bool = True) -> dict:
    """Healthy-twin expected battery/alternator readings.

    Returns dict with battery_voltage_v, battery_current_a, battery_soc_pct.
    """
    draw = BATTERY_BASELINE_DRAW_A + BATTERY_LOAD_DRAW_A * max(0.0, load)
    charge = BATTERY_ALTERNATOR_CHARGE_A if alternator_healthy else 0.0
    net_current = draw - charge  # positive = discharging

    # Voltage: simple linear model — drops under load, rises with SOC
    soc_factor = max(0.0, min(1.0, soc_pct / 100.0))
    v = BATTERY_NOMINAL_V - 0.3 * (1.0 - soc_factor) - 0.05 * draw
    if not alternator_healthy:
        v -= 0.8  # noticeable droop without alternator
    v = max(9.0, min(14.5, v))

    return {
        "battery_voltage_v": v,
        "battery_current_a": draw,
        "battery_soc_pct": max(0.0, min(100.0, soc_pct)),
    }


def expected_sensors(load: float, ambient_offset_c: float = 0.0,
                     altitude_m: float = 0.0) -> dict:
    """Healthy-twin expected reading for every sensor at this operating point.

    `load` is the normalized throttle/power setting in ~[0.35, 1.0], matching
    the `load` column already in the dataset. `ambient_offset_c` is degrees C
    away from a 25 C reference day. `altitude_m` is altitude in meters above
    sea level (0 = sea level, default, preserves backward compatibility).
    """
    load = max(0.0, min(1.2, load))
    sigma = density_ratio(altitude_m)
    out = {}
    for s in SENSORS:
        val = _LOAD_INTERCEPT[s] + _LOAD_SLOPE[s] * load
        val += _AMBIENT_COEF.get(s, 0.0) * ambient_offset_c
        # Altitude correction: scale the load-dependent portion by σ^power
        # The intercept (idle/baseline) is less affected, so we scale the
        # deviation from intercept.
        power = _ALT_POWER.get(s, 0.0)
        if power > 0.0 and altitude_m > 10.0:
            load_portion = _LOAD_SLOPE[s] * load
            alt_factor = sigma ** power
            # Replace the load portion with its altitude-scaled version
            val = _LOAD_INTERCEPT[s] + load_portion * alt_factor
            val += _AMBIENT_COEF.get(s, 0.0) * ambient_offset_c
        out[s] = val
    return out


def reference_trajectory(t_sec, load, ambient_offset_c, altitude_m=None):
    """Lagged healthy-twin trajectory for one run, sorted by t_sec.

    A purely *static* (instantaneous) reference would flag every throttle
    change as a "residual" even on a perfectly healthy engine, because real
    sensors (and the simulator) have thermal/mechanical lag the static model
    doesn't. A digital twin that is meant to catch faults, not throttle
    transients, has to account for the same known time constants the real
    engine has -- so this applies the identical first-order lag (LAG_TAU) to
    the reference model's own output before it is compared against the
    actual reading. Takes/returns plain sequences (t_sec, load,
    ambient_offset_c all same length, already sorted ascending by t_sec).

    `altitude_m` is an optional same-length sequence of altitude values.
    If None, sea-level (0 m) is assumed for backward compatibility.
    """
    n = len(t_sec)
    out = {s: [0.0] * n for s in SENSORS}
    if n == 0:
        return out
    alt0 = altitude_m[0] if altitude_m is not None else 0.0
    first = expected_sensors(load[0], ambient_offset_c[0], alt0)
    for s in SENSORS:
        out[s][0] = first[s]
    for i in range(1, n):
        dt = max(t_sec[i] - t_sec[i - 1], 1e-6)
        alt_i = altitude_m[i] if altitude_m is not None else 0.0
        tgt = expected_sensors(load[i], ambient_offset_c[i], alt_i)
        for s in SENSORS:
            prev = out[s][i - 1]
            tau = max(LAG_TAU[s], dt)
            out[s][i] = prev + dt * (tgt[s] - prev) / tau
    return out


# Healthy-flight residual noise (std dev) measured from engine_healthy.csv.
# engine_physics_model.py scales these up moderately (see NOISE_SCALE there)
# to reflect that a single clean SITL/bench flight understates real fleet
# sensor variability (connector noise, transducer drift, vibration coupling,
# aging wiring looms, temperature-compensation error, etc.).
MEASURED_HEALTHY_NOISE_STD = {
    "rpm": 350.7,
    "egt_c": 26.2,
    "cht_c": 16.6,
    "oil_temp_c": 11.9,
    "oil_pressure_bar": 0.304,
    "fuel_flow_lph": 1.93,
    "vibration": 0.070,
}
