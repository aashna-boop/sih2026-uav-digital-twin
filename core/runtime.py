from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
import uuid

from backend.mission_logic import recommendation, time_to_safe_recovery_min, traditional_status
from core.contracts import Analytics, FlightState, TelemetryFrame
from simulator.engine_plant import (
    DEGRADATION_RATES,
    RUL_HORIZON_MINUTES,
    FaultCommand,
    VirtualEnginePlant,
)
from simulator.flight_profile import DemonstrationProfile
from twin.health_index import classify_fault, explain_fault, health_score
from twin.healthy_twin import HealthyEngineTwin
from twin.residuals import calculate_residuals


@dataclass(slots=True)
class ScenarioState:
    fault: str = "HEALTHY"
    degradation_rate: str = "medium"
    enabled: bool = False
    paused: bool = False
    ambient_offset_c: float = 25.0


class SimulationRuntime:
    def __init__(self, seed: int = 26054, dt: float = 0.2, enable_ml: bool = True) -> None:
        self.seed = seed
        self.dt = dt
        self.run_id = f"demo-{uuid.uuid4().hex[:8]}"
        self.scenario = ScenarioState()
        self.profile = DemonstrationProfile(ambient_offset_c=self.scenario.ambient_offset_c)
        self.plant = VirtualEnginePlant(seed=seed)
        self.twin = HealthyEngineTwin()
        self.t_sec = 0.0
        self.last_frame: TelemetryFrame | None = None
        self.operator_response = "PENDING"
        self.operator_response_at: float | None = None
        self.last_recommendation: str | None = None
        self.inference = self._load_inference() if enable_ml else None

    @staticmethod
    def _load_inference():
        model_path = Path(__file__).resolve().parents[1] / "ml" / "models" / "model_bundle.joblib"
        if not model_path.exists():
            return None
        try:
            from ml.inference import ModelInference

            return ModelInference(model_path)
        except (ImportError, ModuleNotFoundError):
            return None

    def reset(self) -> None:
        self.run_id = f"demo-{uuid.uuid4().hex[:8]}"
        self.profile = DemonstrationProfile(ambient_offset_c=self.scenario.ambient_offset_c)
        self.plant = VirtualEnginePlant(seed=self.seed)
        self.twin = HealthyEngineTwin()
        self.t_sec = 0.0
        self.last_frame = None
        self.last_recommendation = None
        self.clear_operator_response()
        if self.inference is not None:
            self.inference.reset()
        self._apply_fault()

    def configure(
        self,
        fault: str | None = None,
        degradation_rate: str | None = None,
        enabled: bool | None = None,
        paused: bool | None = None,
        ambient_offset_c: float | None = None,
        reset: bool = False,
    ) -> ScenarioState:
        decision_context_changed = any(
            value is not None for value in (fault, degradation_rate, enabled)
        ) or reset
        if fault is not None:
            self.scenario.fault = fault
        if degradation_rate is not None:
            if degradation_rate not in DEGRADATION_RATES:
                raise ValueError(f"Unknown degradation rate: {degradation_rate}")
            self.scenario.degradation_rate = degradation_rate
        if enabled is not None:
            self.scenario.enabled = enabled
        if paused is not None:
            self.scenario.paused = paused
        if ambient_offset_c is not None:
            self.scenario.ambient_offset_c = float(ambient_offset_c)
            self.profile.ambient_offset_c = float(ambient_offset_c)
        self._apply_fault()
        if decision_context_changed:
            self.clear_operator_response()
        if reset:
            self.reset()
        return self.scenario

    def record_operator_response(self, response: str) -> dict[str, float | str | None]:
        normalized = response.upper()
        if normalized not in {"CONFIRMED", "DISMISSED"}:
            raise ValueError(f"Unsupported operator response: {response}")
        self.operator_response = normalized
        self.operator_response_at = time.time()
        return self.operator_state()

    def clear_operator_response(self) -> None:
        self.operator_response = "PENDING"
        self.operator_response_at = None

    def operator_state(self) -> dict[str, float | str | None]:
        return {
            "response": self.operator_response,
            "responded_at": self.operator_response_at,
        }

    def _apply_fault(self) -> None:
        self.plant.set_fault(
            FaultCommand(
                fault=self.scenario.fault,
                degradation_rate=self.scenario.degradation_rate,
                enabled=self.scenario.enabled,
            )
        )

    def step(self, flight: FlightState | None = None) -> TelemetryFrame:
        started = time.perf_counter()
        if not self.scenario.paused:
            self.t_sec += self.dt

        if flight is None:
            flight = self.profile.sample(self.t_sec)
        else:
            self.t_sec = max(self.t_sec, flight.t_sec)
        actual = self.plant.update(flight, self.dt if not self.scenario.paused else 0.0)
        expected = self.twin.update(flight, self.dt if not self.scenario.paused else 0.0)
        residuals, normalized = calculate_residuals(actual, expected)
        health = health_score(normalized)
        rule_fault, rule_confidence, contributors = classify_fault(normalized)
        model_result = self.inference.update(normalized, flight) if self.inference is not None else None
        if model_result is not None:
            detected_fault = model_result.fault
            confidence = model_result.confidence
            model_source = "profile_split_xgboost_treeshap"
        else:
            detected_fault = rule_fault
            confidence = rule_confidence
            model_source = "physics_residual_baseline"
        if model_result is not None and model_result.contributors:
            contributors = model_result.contributors
        explanation = explain_fault(detected_fault, contributors)

        if model_result is not None and detected_fault != "HEALTHY" and model_result.rul_median is not None:
            severity = max(0.0, min(1.0, (100.0 - health) / 70.0))
            rul_minutes = model_result.rul_median
            rul_low = model_result.rul_low or 0.0
            rul_high = model_result.rul_high or rul_minutes
            horizon = RUL_HORIZON_MINUTES[self.scenario.degradation_rate]
            rul_frac = max(0.0, min(1.0, rul_minutes / horizon))
        elif model_result is None and self.scenario.enabled and self.scenario.fault != "HEALTHY":
            severity = max(0.0, min(1.0, (100.0 - health) / 70.0))
            horizon = RUL_HORIZON_MINUTES[self.scenario.degradation_rate]
            rul_frac = max(0.0, 1.0 - self.plant.severity)
            rul_minutes = max(0.0, rul_frac * horizon)
            uncertainty = 0.16 + 0.10 * (1.0 - confidence)
            rul_low = max(0.0, rul_minutes * (1.0 - uncertainty))
            rul_high = rul_minutes * (1.0 + uncertainty)
        else:
            severity = 0.0
            rul_frac = 1.0
            rul_minutes = 240.0
            rul_low = 210.0
            rul_high = 270.0

        recovery = time_to_safe_recovery_min(flight.altitude_m, flight.airspeed_mps)
        proposed_action, margin = recommendation(
            health, detected_fault, confidence, rul_low, recovery
        )
        action = proposed_action
        if detected_fault != "HEALTHY" and self.last_recommendation == "ABORT_LAND":
            action = "ABORT_LAND"
        elif (
            detected_fault != "HEALTHY"
            and self.last_recommendation == "MONITOR_RTB"
            and proposed_action == "CONTINUE"
        ):
            action = "MONITOR_RTB"
        if self.last_recommendation is not None and action != self.last_recommendation:
            self.clear_operator_response()
        self.last_recommendation = action
        actual_dict = actual.rounded()
        threshold = traditional_status(actual_dict)
        twin_status = "EARLY_DEGRADATION" if detected_fault != "HEALTHY" else "NORMAL"
        latency = (time.perf_counter() - started) * 1000

        frame = TelemetryFrame(
            timestamp=flight.timestamp,
            run_id=self.run_id,
            noise_seed=self.seed,
            flight=flight,
            actual=actual,
            expected=expected,
            residuals=residuals,
            normalized_residuals=normalized,
            analytics=Analytics(
                health_score=round(health, 2),
                fault=detected_fault,
                confidence=round(confidence, 3),
                severity=round(severity, 3),
                rul_frac=round(rul_frac, 3),
                rul_minutes=round(rul_minutes, 2),
                rul_low=round(rul_low, 2),
                rul_high=round(rul_high, 2),
                time_to_safe_recovery_min=round(recovery, 2),
                mission_safety_margin_min=round(margin, 2),
                recommendation=action,
                model_source=model_source,
                explanation_method=(
                    "xgboost_native_tree_shap"
                    if model_result is not None and model_result.contributors
                    else "normalized_residual_evidence"
                ),
                operator_response=self.operator_response,
                operator_response_at=self.operator_response_at,
                explanation=explanation,
                top_contributors=contributors,
            ),
            traditional_status=threshold,
            twin_status=twin_status,
            processing_latency_ms=latency,
        )
        self.last_frame = frame
        return frame
