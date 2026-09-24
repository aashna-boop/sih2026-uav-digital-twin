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

Extended fault set: misfire / injector_fault / combustion_instability /
sensor_fault need their own per-tick mechanism (event scheduling, a random
walk, noise inflation, single-channel corruption) beyond the generic
FAULT_MODEL additive-bias loop that already covers the original 3 faults
plus injector_fault's/combustion_instability's static component -- see
engine_physics_model.py's module docstring for the physical reasoning
behind each, mirrored here for the live/streaming case.
"""
import numpy as np

from digital_twin import expected_sensors, LAG_TAU, SENSORS, MEASURED_HEALTHY_NOISE_STD, expected_battery
from engine_physics_model import (
    FAULT_MODEL, NOISE_SCALE,
    MISFIRE_EVENT_DUR_S, MISFIRE_INTERVAL_RANGE_S, MISFIRE_RPM_DIP,
    MISFIRE_VIBRATION_SPIKE, MISFIRE_EGT_SPIKE, MISFIRE_FUEL_BUMP,
    INJECTOR_WALK_AMPLITUDE, INJECTOR_WALK_DECAY_S,
    INSTABILITY_NOISE_MULT, SENSOR_DRIFT_STD_MULT, SENSOR_NOISY_STD_MULT,
    SENSOR_FAULT_MODES,
)

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
        # Extended-fault-set per-session state (see engine_physics_model.py
        # for the offline equivalent of each of these).
        self.sf_channel = None
        self.sf_mode = None
        self.sf_drift_dir = 1.0
        self.sf_frozen_value = None
        self.injector_walk = 0.0
        self.misfire_next_event_t = None
        self.misfire_event_until = -1.0
        self.misfire_event_amp = 0.0

    def inject_fault(self, fault_type):
        self.fault_type = fault_type
        self.fault_start_t = None  # set on next update() once we know current t
        # Re-roll sensor_fault's channel/mode and reset misfire/injector state
        # fresh on every injection, same as a real failure only "choosing"
        # once when it starts, not resetting mid-fault.
        self.sf_channel = str(np.random.choice(SENSORS)) if fault_type == "sensor_fault" else None
        self.sf_mode = str(np.random.choice(SENSOR_FAULT_MODES)) if fault_type == "sensor_fault" else None
        self.sf_drift_dir = float(np.random.choice([-1.0, 1.0]))
        self.sf_frozen_value = None
        self.injector_walk = 0.0
        self.misfire_next_event_t = None
        self.misfire_event_until = -1.0
        self.misfire_event_amp = 0.0

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
        target = expected_sensors(load, AMBIENT_OFFSET_C, alt)
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

        misfire_event_active = False
        if self.fault_type is not None:
            if self.fault_start_t is None:
                self.fault_start_t = t_sec
            elapsed = max(t_sec - self.fault_start_t, 0)
            severity = min(elapsed / FAULT_ONSET_RAMP_SEC, 1.0) * FAULT_TARGET_SEVERITY
            fault_label = self.fault_type
            for chan, delta_at_1 in FAULT_MODEL.get(self.fault_type, {}).items():
                actual[chan] = actual.get(chan, 0.0) + delta_at_1 * severity

            # injector_fault: irregular fuel-flow wobble on top of the static bias.
            if self.fault_type == "injector_fault":
                self.injector_walk += (np.random.uniform(-1, 1) * INJECTOR_WALK_AMPLITUDE * severity
                                        - self.injector_walk / INJECTOR_WALK_DECAY_S * dt)
                actual["fuel_flow_lph"] = actual.get("fuel_flow_lph", 0.0) + self.injector_walk

            # misfire: schedule/advance discrete combustion-loss events (see
            # engine_physics_model.py's generate_run for the offline twin of
            # this same logic).
            if self.fault_type == "misfire" and severity > 0:
                if self.misfire_next_event_t is None:
                    self.misfire_next_event_t = t_sec + np.random.uniform(1.0, 4.0)
                if t_sec >= self.misfire_next_event_t and t_sec >= self.misfire_event_until:
                    self.misfire_event_until = t_sec + MISFIRE_EVENT_DUR_S
                    self.misfire_event_amp = 0.6 + 0.4 * np.random.random()
                    interval_mean = max(0.5, MISFIRE_INTERVAL_RANGE_S[0]
                                         + (MISFIRE_INTERVAL_RANGE_S[1] - MISFIRE_INTERVAL_RANGE_S[0])
                                         * (severity / FAULT_TARGET_SEVERITY))
                    self.misfire_next_event_t = t_sec + max(0.4, np.random.normal(interval_mean, interval_mean * 0.3))
                misfire_event_active = t_sec < self.misfire_event_until

        for s in SENSORS:
            noise_std = MEASURED_HEALTHY_NOISE_STD[s] * NOISE_SCALE.get(s, 1.15)
            if self.fault_type == "combustion_instability" and severity > 0:
                # Erratic-but-present combustion: inflate variance, not mean
                # (see engine_physics_model.py's module docstring).
                noise_std *= 1.0 + (INSTABILITY_NOISE_MULT.get(s, 1.0) - 1.0) * (severity / FAULT_TARGET_SEVERITY)
            actual[s] = actual[s] + np.random.normal(0, noise_std)

        if misfire_event_active:
            amp = self.misfire_event_amp
            actual["rpm"] -= (MISFIRE_RPM_DIP[0] + MISFIRE_RPM_DIP[1] * severity) * amp
            actual["vibration"] += (MISFIRE_VIBRATION_SPIKE[0] + MISFIRE_VIBRATION_SPIKE[1] * severity) * amp
            actual["egt_c"] += (MISFIRE_EGT_SPIKE[0] + MISFIRE_EGT_SPIKE[1] * severity) * amp
            actual["fuel_flow_lph"] += (MISFIRE_FUEL_BUMP[0] + MISFIRE_FUEL_BUMP[1] * severity) * amp

        actual["oil_pressure_bar"] = max(0.25, actual["oil_pressure_bar"])
        actual["rpm"] = max(300.0, actual["rpm"])
        actual["fuel_flow_lph"] = max(0.1, actual["fuel_flow_lph"])
        actual["vibration"] = max(0.05, actual["vibration"])

        # sensor_fault: applied LAST, directly to the reported reading only --
        # the engine (target/lagged/actual-so-far) stays genuinely healthy.
        # Not re-clamped afterward, same reasoning as the offline generator.
        if self.fault_type == "sensor_fault" and severity > 0 and self.sf_channel is not None:
            chan = self.sf_channel
            base_std = MEASURED_HEALTHY_NOISE_STD[chan] * NOISE_SCALE.get(chan, 1.15)
            if self.sf_mode == "drift":
                actual[chan] += self.sf_drift_dir * SENSOR_DRIFT_STD_MULT * base_std * severity
            elif self.sf_mode == "flatline":
                if self.sf_frozen_value is None:
                    self.sf_frozen_value = actual[chan]
                actual[chan] = self.sf_frozen_value
            else:  # "noisy"
                actual[chan] += np.random.normal(0, SENSOR_NOISY_STD_MULT * base_std * severity)

        # ---- Battery / alternator telemetry baseline ----
        # NOTE: fixed a crash here -- this used to call
        # expected_battery(load, rpm_val, dt), which doesn't match that
        # function's real signature (load, soc_pct, alternator_healthy), and
        # then unpacked its return dict positionally (batt_v, batt_i, batt_soc
        # = {...}), which iterates dict KEYS, not values. Every engine.update()
        # call threw TypeError: type str doesn't define __round__ method,
        # so the live dashboard could not produce a single prediction.
        # Unrelated to this fault-set change; fixed opportunistically while
        # touching this file, since it otherwise breaks the live path entirely.
        # There's no live per-session SOC/alternator-health tracking (unlike
        # the offline generator's Coulomb counting), so this reports a fixed
        # healthy-alternator, full-SOC baseline plus small measurement noise --
        # app_live.py doesn't currently use this field anyway (it broadcasts
        # the real SITL battery telemetry instead), so this is a correctness
        # fix, not a feature.
        batt = expected_battery(load, 100.0, True)
        batt_v = batt["battery_voltage_v"] + np.random.normal(0, 0.05)
        batt_i = batt["battery_current_a"] + np.random.normal(0, 0.1)
        batt_soc = batt["battery_soc_pct"]

        return {
            "t_sec": t_sec, "load": load, "altitude": alt,
            "expected": expected, "actual": actual,
            "battery": {
                "voltage_v": round(batt_v, 2),
                "current_a": round(batt_i, 2),
                "soc_pct": round(batt_soc, 1),
            },
            "true_fault": fault_label, "true_severity": severity,
        }
