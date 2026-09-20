"""
live_engine.py

A LIVE, streaming version of engine_physics_model.py's healthy-engine
simulation and fault-injection logic. The offline version works on a whole
recorded run at once; this version does the exact same physics, one flight
sample at a time, as SITL telemetry arrives in real time.

Two outputs are computed on every update:
  - "expected": what a HEALTHY engine would show right now, for the current
    live flight state (this is the digital-twin reference)
  - "actual": what the engine shows right now, INCLUDING any fault currently
    injected (this is what a real sensor would report)

Reference formulas, lag time constants, fault deltas and noise levels are
all imported directly from digital_twin.py / engine_physics_model.py (not
re-derived here) so a live demo run is governed by the exact same physics
the trained models were fit against -- previously this file had its own
hand-approximated copies of those numbers, which had quietly drifted from
the real ones.
"""
import numpy as np

from digital_twin import expected_sensors, LAG_TAU, SENSORS, MEASURED_HEALTHY_NOISE_STD
from engine_physics_model import FAULT_MODEL, NOISE_SCALE

# Calibrated from engine_master_dataset.csv (max airspeed observed in the
# original seed flight ~28.3 m/s). The offline generator normalizes/derives
# load from a scripted flight-profile archetype; that's not available live,
# so this is the live equivalent: infer load from climb rate + airspeed.
ASSUMED_MAX_AIRSPEED = 28.0

FAULT_ONSET_RAMP_SEC = 15.0   # wall-clock seconds for an injected fault to ramp to full target severity (demo pacing, not the dataset's randomized onset -- a live demo needs a fast, visible ramp)
FAULT_TARGET_SEVERITY = 0.6   # mid-range of the training severity distribution (0.2-0.9)
AMBIENT_OFFSET_C = 0.0        # live SITL has no ambient-temperature telemetry; assume the 25C reference day


class LiveEngine:
    def __init__(self):
        self.prev_alt = None
        self.prev_t = None
        self.climb_rate_smoothed = 0.0
        self.lagged = dict(expected_sensors(0.35, AMBIENT_OFFSET_C))
        self.fault_type = None       # None or one of the known fault names
        self.fault_start_t = None

    def inject_fault(self, fault_type):
        self.fault_type = fault_type
        self.fault_start_t = None  # set on next update() once we know current t

    def clear_fault(self):
        self.fault_type = None
        self.fault_start_t = None

    def _load_from_state(self, t, alt, airspeed):
        if self.prev_alt is None or self.prev_t is None:
            climb_rate = 0.0
        else:
            dt = max(t - self.prev_t, 1e-3)
            climb_rate = (alt - self.prev_alt) / dt
        # Exponential smoothing (live equivalent of the offline profile's
        # scripted, already-smooth load curve).
        self.climb_rate_smoothed = 0.85 * self.climb_rate_smoothed + 0.15 * climb_rate
        self.prev_alt, self.prev_t = alt, t

        airspeed_norm = float(np.clip(airspeed / ASSUMED_MAX_AIRSPEED, 0, 1))
        climb_norm = float(np.clip(self.climb_rate_smoothed / 3.0, -1, 1))
        climb_component = max(climb_norm, 0)

        load = 0.35 + 0.4 * climb_component + 0.25 * airspeed_norm
        return float(np.clip(load, 0.15, 1.2))

    def update(self, t_sec, alt, airspeed, roll, pitch, yaw, dt=0.4):
        load = self._load_from_state(t_sec, alt, airspeed)

        # ---- Expected (healthy) sensor values: same reference model + lag
        # constants used everywhere else in the project (digital_twin.py) ----
        target = expected_sensors(load, AMBIENT_OFFSET_C)
        expected = {}
        for s in SENSORS:
            tau = LAG_TAU[s]
            self.lagged[s] = self.lagged[s] + dt * (target[s] - self.lagged[s]) / max(tau, dt)
            expected[s] = self.lagged[s]

        # ---- Actual sensor values: expected + fault delta (if any) + sensor
        # noise (same noise model as engine_physics_model.py, so live
        # residuals land in the same distribution the models were trained
        # on) ----
        severity = 0.0
        fault_label = "healthy"
        actual = dict(expected)

        if self.fault_type is not None:
            if self.fault_start_t is None:
                self.fault_start_t = t_sec
            elapsed = max(t_sec - self.fault_start_t, 0)
            severity = min(elapsed / FAULT_ONSET_RAMP_SEC, 1.0) * FAULT_TARGET_SEVERITY
            fault_label = self.fault_type
            for chan, delta_at_1 in FAULT_MODEL[self.fault_type].items():
                actual[chan] = actual.get(chan, 0.0) + delta_at_1 * severity

        for s in SENSORS:
            noise_std = MEASURED_HEALTHY_NOISE_STD[s] * NOISE_SCALE.get(s, 1.15)
            actual[s] = actual[s] + np.random.normal(0, noise_std)

        actual["oil_pressure_bar"] = max(0.25, actual["oil_pressure_bar"])
        actual["rpm"] = max(300.0, actual["rpm"])
        actual["fuel_flow_lph"] = max(0.1, actual["fuel_flow_lph"])
        actual["vibration"] = max(0.05, actual["vibration"])

        return {
            "t_sec": t_sec, "load": load, "altitude": alt,
            "expected": expected, "actual": actual,
            "true_fault": fault_label, "true_severity": severity,
        }
