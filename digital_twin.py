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
"""
from __future__ import annotations

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
_AMBIENT_COEF = {
    "egt_c": 0.30,
    "cht_c": 0.50,
    "oil_temp_c": 0.35,
}


def expected_sensors(load: float, ambient_offset_c: float = 0.0) -> dict:
    """Healthy-twin expected reading for every sensor at this operating point.

    `load` is the normalized throttle/power setting in ~[0.35, 1.0], matching
    the `load` column already in the dataset. `ambient_offset_c` is degrees C
    away from a 25 C reference day.
    """
    load = max(0.0, min(1.2, load))
    out = {}
    for s in SENSORS:
        val = _LOAD_INTERCEPT[s] + _LOAD_SLOPE[s] * load
        val += _AMBIENT_COEF.get(s, 0.0) * ambient_offset_c
        out[s] = val
    return out


def reference_trajectory(t_sec, load, ambient_offset_c):
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
    """
    n = len(t_sec)
    out = {s: [0.0] * n for s in SENSORS}
    if n == 0:
        return out
    first = expected_sensors(load[0], ambient_offset_c[0])
    for s in SENSORS:
        out[s][0] = first[s]
    for i in range(1, n):
        dt = max(t_sec[i] - t_sec[i - 1], 1e-6)
        tgt = expected_sensors(load[i], ambient_offset_c[i])
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
