"""
live_engine.py

A LIVE, streaming version of engine_physics_model.py's healthy-engine
simulation and fault-injection logic. The offline version (engine_physics_model.py)
worked on a whole recorded flight array at once (np.gradient, rolling windows,
etc.) — this version does the exact same physics equations, but one flight
sample at a time, as SITL telemetry arrives in real time.

Two outputs are computed on every update:
  - "expected": what a HEALTHY engine would show right now, for the current
    live flight state (this is the digital-twin reference)
  - "actual": what the engine shows right now, INCLUDING any fault currently
    injected (this is what a real sensor would report)

This is actually a cleaner residual setup than replaying a separate
pre-recorded healthy flight: the healthy baseline is computed fresh, live,
for the exact same moment as the (possibly faulty) reading, using the same
physics model your team already built and validated when generating
engine_master_dataset.csv.

Reference constants and fault equations are copied verbatim from
engine_physics_model.py so behavior matches the dataset the ML models were
trained on.
"""
import numpy as np

RPM_IDLE = 1700.0
RPM_MAX = 5800.0
EGT_BASELINE_C = 650.0
EGT_MAX_RISE_C = 300.0
CHT_AMBIENT_C = 25.0
CHT_MAX_RISE_C = 140.0
CHT_TIME_CONSTANT_S = 45.0
OIL_TEMP_AMBIENT_C = 25.0
OIL_TEMP_MAX_RISE_C = 90.0
OIL_TEMP_TIME_CONSTANT_S = 90.0
OIL_PRESSURE_IDLE_BAR = 1.5
OIL_PRESSURE_MAX_BAR = 5.0
FUEL_FLOW_IDLE_LPH = 3.0
FUEL_FLOW_MAX_LPH = 24.0

# Calibrated from engine_master_dataset.csv's healthy run (max airspeed
# observed ~28.3 m/s). The offline script normalized airspeed by the max
# seen across the WHOLE recorded flight, which isn't available live —
# this fixed constant is the live equivalent of that normalization.
ASSUMED_MAX_AIRSPEED = 28.0

FAULT_ONSET_RAMP_SEC = 45.0   # wall-clock seconds for an injected fault to reach full target severity
FAULT_TARGET_SEVERITY = 0.6   # matches engine_physics_model.py's --severity default


class LiveEngine:
    def __init__(self):
        self.prev_alt = None
        self.prev_t = None
        self.climb_rate_smoothed = 0.0
        self.cht_expected = CHT_AMBIENT_C
        self.cht_actual = CHT_AMBIENT_C
        self.oil_temp_expected = OIL_TEMP_AMBIENT_C
        self.oil_temp_actual = OIL_TEMP_AMBIENT_C
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
        # Exponential smoothing (live equivalent of the offline centered rolling mean)
        self.climb_rate_smoothed = 0.85 * self.climb_rate_smoothed + 0.15 * climb_rate
        self.prev_alt, self.prev_t = alt, t

        airspeed_norm = float(np.clip(airspeed / ASSUMED_MAX_AIRSPEED, 0, 1))
        climb_norm = float(np.clip(self.climb_rate_smoothed / 3.0, -1, 1))
        climb_component = max(climb_norm, 0)

        load = 0.35 + 0.4 * climb_component + 0.25 * airspeed_norm
        return float(np.clip(load, 0.15, 1.0))

    def update(self, t_sec, alt, airspeed, roll, pitch, yaw, dt=0.4):
        load = self._load_from_state(t_sec, alt, airspeed)

        # ---- Expected (healthy) sensor values ----
        rpm_exp = RPM_IDLE + load * (RPM_MAX - RPM_IDLE) + np.random.normal(0, 15)
        egt_exp = EGT_BASELINE_C + load * EGT_MAX_RISE_C + np.random.normal(0, 5)
        alpha_cht = dt / (CHT_TIME_CONSTANT_S + dt)
        self.cht_expected += alpha_cht * ((CHT_AMBIENT_C + load * CHT_MAX_RISE_C) - self.cht_expected)
        cht_exp = self.cht_expected + np.random.normal(0, 1.5)
        alpha_oil = dt / (OIL_TEMP_TIME_CONSTANT_S + dt)
        self.oil_temp_expected += alpha_oil * ((OIL_TEMP_AMBIENT_C + load * OIL_TEMP_MAX_RISE_C) - self.oil_temp_expected)
        oil_temp_exp = self.oil_temp_expected + np.random.normal(0, 1.0)
        oil_pressure_exp = OIL_PRESSURE_IDLE_BAR + (rpm_exp - RPM_IDLE) / (RPM_MAX - RPM_IDLE) * (
            OIL_PRESSURE_MAX_BAR - OIL_PRESSURE_IDLE_BAR
        ) + np.random.normal(0, 0.05)
        fuel_flow_exp = FUEL_FLOW_IDLE_LPH + (load ** 1.3) * (FUEL_FLOW_MAX_LPH - FUEL_FLOW_IDLE_LPH) + np.random.normal(0, 0.3)
        vibration_exp = 0.5 + (rpm_exp / RPM_MAX) * 0.8 + np.random.normal(0, 0.05)

        expected = {
            "rpm": rpm_exp, "egt_c": egt_exp, "cht_c": cht_exp,
            "oil_temp_c": oil_temp_exp, "oil_pressure_bar": oil_pressure_exp,
            "fuel_flow_lph": fuel_flow_exp, "vibration": vibration_exp,
        }

        # ---- Actual sensor values (apply fault perturbation if active) ----
        actual = dict(expected)
        severity = 0.0
        fault_label = "healthy"

        if self.fault_type is not None:
            if self.fault_start_t is None:
                self.fault_start_t = t_sec
            elapsed = max(t_sec - self.fault_start_t, 0)
            ramp = min(elapsed / FAULT_ONSET_RAMP_SEC, 1.0) * FAULT_TARGET_SEVERITY
            severity = ramp
            fault_label = self.fault_type

            if self.fault_type == "valve_wear":
                actual["rpm"] -= ramp * 400
                actual["egt_c"] += ramp * 120
                actual["fuel_flow_lph"] += ramp * 2.0
            elif self.fault_type == "cooling_failure":
                actual["cht_c"] += ramp * 60
                actual["oil_temp_c"] += ramp * 40
            elif self.fault_type == "oil_pressure_drop":
                actual["oil_pressure_bar"] = max(actual["oil_pressure_bar"] - ramp * 2.0, 0.1)

        return {
            "t_sec": t_sec, "load": load, "altitude": alt,
            "expected": expected, "actual": actual,
            "true_fault": fault_label, "true_severity": severity,
        }
