from __future__ import annotations

from dataclasses import dataclass
import math

from core.contracts import EngineSensors, FlightState


@dataclass(slots=True)
class TwinThermalState:
    cht_c: float = 48.0
    egt_c: float = 420.0
    oil_temp_c: float = 42.0


def _lag(current: float, target: float, dt: float, tau: float) -> float:
    return current + dt * (target - current) / max(tau, dt)


class HealthyEngineTwin:
    """Independent healthy reference with bounded 2-5% model mismatch."""

    def __init__(self) -> None:
        self.thermal = TwinThermalState()

    def update(self, flight: FlightState, dt: float) -> EngineSensors:
        throttle = flight.throttle_pct / 100.0
        density_ratio = math.exp(-flight.altitude_m / 8500.0)
        ram_cooling = max(0.25, min(1.35, flight.airspeed_mps / 35.0))
        load = min(1.15, throttle * (0.80 + 0.27 / max(density_ratio, 0.35)))

        rpm = 858 + 2705 * throttle * (0.905 + 0.095 * density_ratio)
        cht_target = flight.ambient_temp_c + 76 + (109 * load) / max(0.45, ram_cooling)
        egt_target = flight.ambient_temp_c + 516 + 239 * load
        oil_target = flight.ambient_temp_c + 49 + 56 * load

        self.thermal.cht_c = _lag(self.thermal.cht_c, cht_target, dt, 8.7)
        self.thermal.egt_c = _lag(self.thermal.egt_c, egt_target, dt, 2.25)
        self.thermal.oil_temp_c = _lag(self.thermal.oil_temp_c, oil_target, dt, 15.4)

        oil_pressure = 0.76 + 3.70 * min(rpm / 3600.0, 1.15) - 0.0082 * max(
            0.0, self.thermal.oil_temp_c - 85
        )
        fuel_flow = 2.45 + 23.0 * load
        vibration = 0.39 + 0.70 * load

        return EngineSensors(
            rpm=rpm,
            cht_c=self.thermal.cht_c,
            egt_c=self.thermal.egt_c,
            oil_temp_c=self.thermal.oil_temp_c,
            oil_pressure_bar=max(0.0, oil_pressure),
            fuel_flow_lph=fuel_flow,
            vibration=vibration,
        )

