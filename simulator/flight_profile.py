from __future__ import annotations

import math
import time
from dataclasses import dataclass

from core.contracts import FlightState


class DemonstrationProfile:
    """Deterministic fixed-wing mission used by replay-first development.

    The telemetry adapter boundary intentionally matches what a future pymavlink
    adapter will emit, so the plant and twin do not depend on SITL.
    """

    def __init__(
        self,
        profile_id: str = "heldout_demo_alpha",
        ambient_offset_c: float = 25.0,
        duration_sec: float = 240.0,
    ) -> None:
        self.profile_id = profile_id
        self.ambient_offset_c = ambient_offset_c
        self.duration_sec = duration_sec
        self.origin_lat = 28.6139
        self.origin_lon = 77.2090

    def sample(self, t_sec: float) -> FlightState:
        t = t_sec % self.duration_sec

        if t < 20:
            phase = "TAKEOFF"
            throttle = 82 + 5 * math.sin(t / 4)
            altitude = 5 * t
            airspeed = 12 + 0.95 * t
            vertical_speed = 5.0
        elif t < 70:
            phase = "CLIMB"
            throttle = 79 + 3 * math.sin(t / 8)
            altitude = 100 + 22 * (t - 20)
            airspeed = 31 + 2 * math.sin(t / 7)
            vertical_speed = 22.0
        elif t < 165:
            phase = "CRUISE"
            throttle = 61 + 4 * math.sin(t / 13)
            altitude = 1200 + 35 * math.sin(t / 19)
            airspeed = 39 + 2.5 * math.sin(t / 11)
            vertical_speed = 35 / 19 * math.cos(t / 19)
        elif t < 195:
            phase = "LOITER"
            throttle = 55 + 5 * math.sin(t / 6)
            altitude = 1180 + 18 * math.sin(t / 6)
            airspeed = 34 + 3 * math.sin(t / 5)
            vertical_speed = 3 * math.cos(t / 6)
        else:
            phase = "DESCENT"
            throttle = 34 + 3 * math.sin(t / 8)
            altitude = max(80.0, 1180 - 24 * (t - 195))
            airspeed = 31 + 2 * math.sin(t / 9)
            vertical_speed = -24.0

        ambient_temp = 15.0 - 0.0065 * altitude + self.ambient_offset_c
        distance_m = airspeed * t
        lat = self.origin_lat + (distance_m / 111_320.0) * 0.62
        lon_scale = max(0.2, math.cos(math.radians(self.origin_lat)))
        lon = self.origin_lon + (distance_m / (111_320.0 * lon_scale)) * 0.78

        return FlightState(
            timestamp=time.time(),
            t_sec=t,
            profile_id=self.profile_id,
            latitude_deg=lat,
            longitude_deg=lon,
            altitude_m=altitude,
            airspeed_mps=max(0.0, airspeed),
            vertical_speed_mps=vertical_speed,
            throttle_pct=max(0.0, min(100.0, throttle)),
            ambient_temp_c=ambient_temp,
            ambient_offset_c=self.ambient_offset_c,
            flight_phase=phase,
            telemetry_source="REPLAY",
        )


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    profile_id: str
    cruise_altitude_m: float
    cruise_airspeed_mps: float
    climb_throttle_pct: float
    cruise_throttle_pct: float
    wind_mps: float


GEOMETRIES = {
    "surveillance": (3_500.0, 47.0, 82.0, 61.0),
    "high_altitude": (5_000.0, 54.0, 87.0, 68.0),
    "maritime": (2_400.0, 50.0, 79.0, 59.0),
    "relay": (4_200.0, 44.0, 84.0, 64.0),
    "loiter": (3_000.0, 38.0, 76.0, 53.0),
}
WIND_SETTINGS = {"headwind": -6.0, "calm": 0.0, "tailwind": 6.0}

PROFILE_CATALOG = tuple(
    ProfileSpec(
        profile_id=f"{geometry}_{wind_name}",
        cruise_altitude_m=values[0],
        cruise_airspeed_mps=values[1],
        climb_throttle_pct=values[2],
        cruise_throttle_pct=values[3],
        wind_mps=wind_mps,
    )
    for wind_name, wind_mps in WIND_SETTINGS.items()
    for geometry, values in GEOMETRIES.items()
)

HELDOUT_DEMO_PROFILE_IDS = {
    "high_altitude_headwind",
    "maritime_tailwind",
    "relay_calm",
}


class CatalogMissionProfile:
    """Parameterized mission profile used for leakage-safe dataset generation."""

    def __init__(self, spec: ProfileSpec, ambient_offset_c: float = 0.0) -> None:
        self.spec = spec
        self.profile_id = spec.profile_id
        self.ambient_offset_c = ambient_offset_c
        self.duration_sec = 600.0
        self.origin_lat = 20.5937
        self.origin_lon = 78.9629

    def sample(self, t_sec: float) -> FlightState:
        t = t_sec % self.duration_sec
        spec = self.spec
        if t < 30:
            phase = "TAKEOFF"
            progress = t / 30.0
            altitude = 120 * progress
            throttle = spec.climb_throttle_pct + 4 * math.sin(t / 5)
            airspeed = 12 + (spec.cruise_airspeed_mps - 12) * progress
            vertical_speed = 4.0
        elif t < 300:
            phase = "CLIMB"
            progress = (t - 30) / 270.0
            altitude = 120 + (spec.cruise_altitude_m - 120) * progress
            throttle = spec.climb_throttle_pct + 3 * math.sin(t / 17)
            airspeed = spec.cruise_airspeed_mps - 4 + 2 * math.sin(t / 13)
            vertical_speed = (spec.cruise_altitude_m - 120) / 270.0
        elif t < 450:
            phase = "CRUISE"
            altitude = spec.cruise_altitude_m + 55 * math.sin(t / 31)
            throttle = spec.cruise_throttle_pct + 4 * math.sin(t / 19)
            airspeed = spec.cruise_airspeed_mps + 2.5 * math.sin(t / 14)
            vertical_speed = 55 / 31 * math.cos(t / 31)
        elif t < 520:
            phase = "LOITER"
            altitude = spec.cruise_altitude_m - 80 + 25 * math.sin(t / 11)
            throttle = spec.cruise_throttle_pct - 6 + 5 * math.sin(t / 9)
            airspeed = spec.cruise_airspeed_mps - 7 + 2 * math.sin(t / 8)
            vertical_speed = 25 / 11 * math.cos(t / 11)
        else:
            phase = "DESCENT"
            progress = (t - 520) / 80.0
            altitude = max(100.0, spec.cruise_altitude_m * (1 - progress))
            throttle = 34 + 3 * math.sin(t / 12)
            airspeed = spec.cruise_airspeed_mps - 5
            vertical_speed = -spec.cruise_altitude_m / 80.0

        indicated_airspeed = max(10.0, airspeed + spec.wind_mps)
        ambient_temp = 15.0 - 0.0065 * altitude + self.ambient_offset_c
        distance_m = max(0.0, indicated_airspeed) * t
        lat = self.origin_lat + distance_m / 111_320.0 * 0.74
        lon = self.origin_lon + distance_m / (111_320.0 * math.cos(math.radians(self.origin_lat))) * 0.67

        return FlightState(
            timestamp=t,
            t_sec=t,
            profile_id=self.profile_id,
            latitude_deg=lat,
            longitude_deg=lon,
            altitude_m=altitude,
            airspeed_mps=indicated_airspeed,
            vertical_speed_mps=vertical_speed,
            throttle_pct=max(0.0, min(100.0, throttle)),
            ambient_temp_c=ambient_temp,
            ambient_offset_c=self.ambient_offset_c,
            flight_phase=phase,
        )
