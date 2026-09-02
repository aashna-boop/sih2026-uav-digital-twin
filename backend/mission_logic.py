from __future__ import annotations


def time_to_safe_recovery_min(altitude_m: float, airspeed_mps: float) -> float:
    # Prototype proxy: diversion distance increases modestly with mission altitude.
    distance_km = 4.0 + max(0.0, altitude_m) * 0.010
    cruise_mps = max(18.0, airspeed_mps)
    return max(3.0, distance_km * 1000.0 / cruise_mps / 60.0 + 2.0)


def recommendation(
    health: float,
    fault: str,
    confidence: float,
    rul_low: float,
    recovery_min: float,
) -> tuple[str, float]:
    margin = rul_low - recovery_min
    if fault == "HEALTHY" and health >= 80 and margin > 8:
        return "CONTINUE", margin
    if health < 45 or margin <= 0:
        return "ABORT_LAND", margin
    return "MONITOR_RTB", margin


def traditional_status(actual: dict[str, float]) -> str:
    if (
        actual["cht_c"] >= 260
        or actual["oil_temp_c"] >= 145
        or actual["oil_pressure_bar"] <= 1.8
        or actual["vibration"] >= 3.2
    ):
        return "ALARM"
    return "NORMAL"
