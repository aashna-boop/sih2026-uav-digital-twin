from __future__ import annotations

from core.contracts import EngineSensors, SENSOR_NAMES


# Initial healthy-envelope standard deviations. Day 1 calibration will replace
# these values with estimates generated from all healthy flight phases.
HEALTHY_SIGMA = {
    "rpm": 72.0,
    "cht_c": 5.5,
    "egt_c": 13.0,
    "oil_temp_c": 4.0,
    "oil_pressure_bar": 0.13,
    "fuel_flow_lph": 0.58,
    "vibration": 0.10,
}


def calculate_residuals(
    actual: EngineSensors, expected: EngineSensors
) -> tuple[dict[str, float], dict[str, float]]:
    residuals = {
        name: float(getattr(actual, name) - getattr(expected, name)) for name in SENSOR_NAMES
    }
    normalized = {name: residuals[name] / HEALTHY_SIGMA[name] for name in SENSOR_NAMES}
    return residuals, normalized

