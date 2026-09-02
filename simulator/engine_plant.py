from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from core.contracts import EngineSensors, FlightState


FAULTS = (
    "HEALTHY",
    "COOLING_DEGRADATION",
    "LUBRICATION_DEGRADATION",
    "IGNITION_MISFIRE",
    "VALVE_WEAR",
)
DEGRADATION_RATES = {
    "slow": 0.0035,
    "medium": 0.008,
    "rapid": 0.016,
}

# The ramp above accelerates degradation for a short live demonstration. It is
# deliberately separate from the simulated life horizon reported as RUL.
RUL_HORIZON_MINUTES = {
    "slow": 75.0,
    "medium": 40.0,
    "rapid": 22.0,
}


@dataclass(slots=True)
class FaultCommand:
    fault: str = "HEALTHY"
    degradation_rate: str = "medium"
    enabled: bool = False


@dataclass(slots=True)
class ThermalState:
    cht_c: float = 48.0
    egt_c: float = 420.0
    oil_temp_c: float = 42.0


def _lag(current: float, target: float, dt: float, tau: float) -> float:
    return current + dt * (target - current) / max(tau, dt)


class VirtualEnginePlant:
    """Faultable virtual piston engine representing the monitored asset."""

    def __init__(self, seed: int = 26054) -> None:
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.thermal = ThermalState()
        self.severity = 0.0
        self.command = FaultCommand()
        self.elapsed_fault_sec = 0.0
        # Component-to-component variation is fixed for a run.
        self.offsets = {
            "rpm": self.rng.normal(0, 28),
            "cht_c": self.rng.normal(0, 1.5),
            "egt_c": self.rng.normal(0, 5.0),
            "oil_temp_c": self.rng.normal(0, 1.2),
            "oil_pressure_bar": self.rng.normal(0, 0.045),
            "fuel_flow_lph": self.rng.normal(0, 0.2),
            "vibration": self.rng.normal(0, 0.035),
        }

    def set_fault(self, command: FaultCommand) -> None:
        if command.fault not in FAULTS:
            raise ValueError(f"Unknown fault: {command.fault}")
        if command.degradation_rate not in DEGRADATION_RATES:
            raise ValueError(f"Unknown degradation rate: {command.degradation_rate}")
        if command.fault != self.command.fault or command.enabled != self.command.enabled:
            self.severity = 0.0
            self.elapsed_fault_sec = 0.0
        self.command = command

    def _advance_fault(self, dt: float) -> None:
        if not self.command.enabled or self.command.fault == "HEALTHY":
            self.severity = max(0.0, self.severity - dt * 0.05)
            return
        self.elapsed_fault_sec += dt
        rate = DEGRADATION_RATES[self.command.degradation_rate]
        self.severity = min(1.0, self.severity + rate * dt)

    def update(self, flight: FlightState, dt: float) -> EngineSensors:
        self._advance_fault(dt)
        severity = self.severity
        throttle = flight.throttle_pct / 100.0
        density_ratio = math.exp(-flight.altitude_m / 8500.0)
        ram_cooling = max(0.25, min(1.35, flight.airspeed_mps / 35.0))

        cooling_eff = 1.0
        oil_pump_eff = 1.0
        combustion_eff = 1.0
        volumetric_eff = 1.0
        ripple = 0.0

        if self.command.enabled and self.command.fault == "COOLING_DEGRADATION":
            cooling_eff = max(0.32, 1.0 - 0.68 * severity)
            ram_cooling *= max(0.45, 1.0 - 0.48 * severity)
        elif self.command.enabled and self.command.fault == "LUBRICATION_DEGRADATION":
            oil_pump_eff = max(0.22, 1.0 - 0.78 * severity)
        elif self.command.enabled and self.command.fault == "IGNITION_MISFIRE":
            combustion_eff = max(0.58, 1.0 - 0.40 * severity)
            ripple = severity * math.sin(flight.t_sec * 5.7)
        elif self.command.enabled and self.command.fault == "VALVE_WEAR":
            # Progressive valve-seat/clearance wear reduces cylinder filling
            # and compression efficiency and introduces a smaller cyclic
            # imbalance. Sensor effects emerge downstream from these physical
            # coefficients instead of being added to an output array.
            volumetric_eff = max(0.66, 1.0 - 0.34 * severity)
            combustion_eff = max(0.82, 1.0 - 0.18 * severity)
            ripple = 0.32 * severity * math.sin(flight.t_sec * 4.3)

        load = min(1.15, throttle * (0.78 + 0.28 / max(density_ratio, 0.35)))
        rpm_target = (
            850
            + 2750
            * throttle
            * (0.90 + 0.10 * density_ratio)
            * combustion_eff
            * volumetric_eff
        )
        rpm = rpm_target + 150 * ripple + self.offsets["rpm"] + self.rng.normal(0, 20 + 20 * severity)

        cht_target = flight.ambient_temp_c + 74 + (112 * load) / max(0.45, cooling_eff * ram_cooling)
        egt_target = flight.ambient_temp_c + 520 + 245 * load
        oil_target = flight.ambient_temp_c + 48 + 58 * load

        if self.command.fault == "COOLING_DEGRADATION" and self.command.enabled:
            egt_target += 34 * severity
            oil_target += 38 * severity
        if self.command.fault == "LUBRICATION_DEGRADATION" and self.command.enabled:
            oil_target += 64 * severity
            cht_target += 18 * severity
        if self.command.fault == "IGNITION_MISFIRE" and self.command.enabled:
            egt_target += 42 * severity * math.sin(flight.t_sec * 2.8)
            cht_target -= 12 * severity
        if self.command.fault == "VALVE_WEAR" and self.command.enabled:
            egt_target += 62 * severity
            cht_target += 10 * severity
            oil_target += 9 * severity

        self.thermal.cht_c = _lag(self.thermal.cht_c, cht_target, dt, 8.5)
        self.thermal.egt_c = _lag(self.thermal.egt_c, egt_target, dt, 2.2)
        self.thermal.oil_temp_c = _lag(self.thermal.oil_temp_c, oil_target, dt, 15.0)

        oil_pressure = (
            0.75
            + 3.75 * min(max(rpm, 0.0) / 3600.0, 1.15) * oil_pump_eff
            - 0.008 * max(0.0, self.thermal.oil_temp_c - 85)
        )
        fuel_flow = 2.4 + 23.5 * load / max(combustion_eff * volumetric_eff, 0.5)
        vibration = 0.38 + 0.72 * load

        if self.command.fault == "LUBRICATION_DEGRADATION" and self.command.enabled:
            vibration += 1.35 * severity**1.35
        if self.command.fault == "IGNITION_MISFIRE" and self.command.enabled:
            vibration += 2.15 * severity + abs(ripple) * 0.75
        if self.command.fault == "COOLING_DEGRADATION" and self.command.enabled:
            vibration += 0.12 * severity
        if self.command.fault == "VALVE_WEAR" and self.command.enabled:
            vibration += 0.75 * severity**1.2 + abs(ripple) * 0.45

        return EngineSensors(
            rpm=max(0.0, rpm),
            cht_c=self.thermal.cht_c + self.offsets["cht_c"] + self.rng.normal(0, 0.8),
            egt_c=self.thermal.egt_c + self.offsets["egt_c"] + self.rng.normal(0, 3.0),
            oil_temp_c=self.thermal.oil_temp_c + self.offsets["oil_temp_c"] + self.rng.normal(0, 0.55),
            oil_pressure_bar=max(
                0.0, oil_pressure + self.offsets["oil_pressure_bar"] + self.rng.normal(0, 0.035)
            ),
            fuel_flow_lph=max(
                0.0, fuel_flow + self.offsets["fuel_flow_lph"] + self.rng.normal(0, 0.12)
            ),
            vibration=max(
                0.0, vibration + self.offsets["vibration"] + self.rng.normal(0, 0.025)
            ),
        )
