from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SENSOR_NAMES = (
    "rpm",
    "cht_c",
    "egt_c",
    "oil_temp_c",
    "oil_pressure_bar",
    "fuel_flow_lph",
    "vibration",
)


@dataclass(slots=True)
class FlightState:
    timestamp: float
    t_sec: float
    profile_id: str
    latitude_deg: float
    longitude_deg: float
    altitude_m: float
    airspeed_mps: float
    vertical_speed_mps: float
    throttle_pct: float
    ambient_temp_c: float
    ambient_offset_c: float
    flight_phase: str
    flight_mode: str = "AUTO"
    telemetry_source: str = "REPLAY"


@dataclass(slots=True)
class EngineSensors:
    rpm: float
    cht_c: float
    egt_c: float
    oil_temp_c: float
    oil_pressure_bar: float
    fuel_flow_lph: float
    vibration: float

    def rounded(self) -> dict[str, float]:
        return {name: round(float(getattr(self, name)), 3) for name in SENSOR_NAMES}


@dataclass(slots=True)
class Analytics:
    health_score: float
    fault: str
    confidence: float
    severity: float
    rul_frac: float
    rul_minutes: float
    rul_low: float
    rul_high: float
    time_to_safe_recovery_min: float
    mission_safety_margin_min: float
    recommendation: str
    model_source: str
    explanation_method: str = "normalized_residual_evidence"
    operator_response: str = "PENDING"
    operator_response_at: float | None = None
    explanation: list[str] = field(default_factory=list)
    top_contributors: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class TelemetryFrame:
    timestamp: float
    run_id: str
    noise_seed: int
    flight: FlightState
    actual: EngineSensors
    expected: EngineSensors
    residuals: dict[str, float]
    normalized_residuals: dict[str, float]
    analytics: Analytics
    traditional_status: str
    twin_status: str
    processing_latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": round(self.timestamp, 3),
            "run_id": self.run_id,
            "noise_seed": self.noise_seed,
            "flight": asdict(self.flight),
            "engine": {
                "actual": self.actual.rounded(),
                "expected": self.expected.rounded(),
                "residuals": {k: round(float(v), 3) for k, v in self.residuals.items()},
                "normalized_residuals": {
                    k: round(float(v), 3) for k, v in self.normalized_residuals.items()
                },
            },
            "analytics": asdict(self.analytics),
            "traditional_status": self.traditional_status,
            "twin_status": self.twin_status,
            "processing_latency_ms": round(self.processing_latency_ms, 3),
        }
