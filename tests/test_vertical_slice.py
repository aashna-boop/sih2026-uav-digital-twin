from __future__ import annotations

import unittest

from core.contracts import FlightState
from core.runtime import SimulationRuntime


def advance(runtime: SimulationRuntime, seconds: float):
    frame = None
    for _ in range(int(seconds / runtime.dt)):
        frame = runtime.step()
    assert frame is not None
    return frame


class VerticalSliceTests(unittest.TestCase):
    def test_external_flight_state_drives_runtime_without_bypassing_engine_twin(self) -> None:
        runtime = SimulationRuntime(seed=7, enable_ml=False)
        flight = FlightState(
            timestamp=1_700_000_000.0,
            t_sec=12.5,
            profile_id="sitl_arduplane_live",
            latitude_deg=28.6,
            longitude_deg=77.2,
            altitude_m=850.0,
            airspeed_mps=38.0,
            vertical_speed_mps=1.2,
            throttle_pct=64.0,
            ambient_temp_c=34.5,
            ambient_offset_c=25.0,
            flight_phase="CLIMB",
            flight_mode="AUTO",
            telemetry_source="ARDUPILOT_SITL",
        )
        frame = runtime.step(flight=flight)
        self.assertEqual(frame.flight.telemetry_source, "ARDUPILOT_SITL")
        self.assertEqual(frame.flight.profile_id, "sitl_arduplane_live")
        self.assertGreater(frame.actual.rpm, 0)
        self.assertGreater(frame.expected.rpm, 0)

    def test_contract_and_healthy_operation(self) -> None:
        runtime = SimulationRuntime(seed=11, enable_ml=False)
        frame = advance(runtime, 90)
        payload = frame.to_dict()
        self.assertEqual(payload["traditional_status"], "NORMAL")
        self.assertIn("flight", payload)
        self.assertIn("oil_pressure_bar", payload["engine"]["actual"])
        self.assertIn("rul_low", payload["analytics"])
        self.assertGreater(payload["analytics"]["health_score"], 75)

    def test_lubrication_fault_is_correlated_and_detected(self) -> None:
        runtime = SimulationRuntime(seed=22, enable_ml=False)
        advance(runtime, 45)
        runtime.configure(fault="LUBRICATION_DEGRADATION", degradation_rate="rapid", enabled=True)
        frame = advance(runtime, 35)
        self.assertLess(frame.residuals["oil_pressure_bar"], -0.25)
        self.assertGreater(frame.residuals["oil_temp_c"], 2.0)
        self.assertGreater(frame.residuals["vibration"], 0.20)
        self.assertEqual(frame.analytics.fault, "LUBRICATION_DEGRADATION")

    def test_cooling_fault_propagates_to_multiple_channels(self) -> None:
        runtime = SimulationRuntime(seed=33, enable_ml=False)
        advance(runtime, 45)
        runtime.configure(fault="COOLING_DEGRADATION", degradation_rate="rapid", enabled=True)
        frame = advance(runtime, 35)
        self.assertGreater(frame.residuals["cht_c"], 10)
        self.assertGreater(frame.residuals["oil_temp_c"], 2)
        self.assertEqual(frame.analytics.fault, "COOLING_DEGRADATION")

    def test_misfire_affects_combustion_and_vibration(self) -> None:
        runtime = SimulationRuntime(seed=44, enable_ml=False)
        advance(runtime, 45)
        runtime.configure(fault="IGNITION_MISFIRE", degradation_rate="rapid", enabled=True)
        frame = advance(runtime, 35)
        self.assertGreater(frame.residuals["vibration"], 0.5)
        self.assertGreater(frame.residuals["fuel_flow_lph"], 1.0)
        self.assertEqual(frame.analytics.fault, "IGNITION_MISFIRE")

    def test_valve_wear_propagates_from_efficiency_loss(self) -> None:
        runtime = SimulationRuntime(seed=45, enable_ml=False)
        advance(runtime, 45)
        runtime.configure(fault="VALVE_WEAR", degradation_rate="rapid", enabled=True)
        frame = advance(runtime, 35)
        self.assertLess(frame.residuals["rpm"], -100)
        self.assertGreater(frame.residuals["egt_c"], 8)
        self.assertGreater(frame.residuals["fuel_flow_lph"], 0.8)
        self.assertGreater(frame.residuals["vibration"], 0.3)
        self.assertEqual(frame.analytics.fault, "VALVE_WEAR")

    def test_rul_and_mission_margin_decline(self) -> None:
        runtime = SimulationRuntime(seed=55, enable_ml=False)
        advance(runtime, 45)
        runtime.configure(fault="LUBRICATION_DEGRADATION", degradation_rate="rapid", enabled=True)
        early = advance(runtime, 8)
        late = advance(runtime, 50)
        self.assertLess(late.analytics.rul_minutes, early.analytics.rul_minutes)
        self.assertLess(
            late.analytics.mission_safety_margin_min,
            early.analytics.mission_safety_margin_min,
        )

    def test_trained_profile_split_model_runs_online(self) -> None:
        runtime = SimulationRuntime(seed=66, enable_ml=True)
        advance(runtime, 45)
        runtime.configure(fault="LUBRICATION_DEGRADATION", degradation_rate="rapid", enabled=True)
        frame = advance(runtime, 35)
        self.assertEqual(frame.analytics.model_source, "profile_split_xgboost_treeshap")
        self.assertEqual(frame.analytics.fault, "LUBRICATION_DEGRADATION")
        self.assertEqual(frame.analytics.explanation_method, "xgboost_native_tree_shap")
        self.assertTrue(frame.analytics.top_contributors)
        self.assertLessEqual(frame.analytics.rul_low, frame.analytics.rul_minutes)
        self.assertLessEqual(frame.analytics.rul_minutes, frame.analytics.rul_high)
        if frame.analytics.mission_safety_margin_min > 0 and frame.analytics.health_score >= 45:
            self.assertNotEqual(frame.analytics.recommendation, "ABORT_LAND")

    def test_operator_response_is_recorded_and_cleared_by_new_scenario(self) -> None:
        runtime = SimulationRuntime(seed=77, enable_ml=False)
        state = runtime.record_operator_response("confirmed")
        self.assertEqual(state["response"], "CONFIRMED")
        self.assertIsNotNone(state["responded_at"])
        frame = runtime.step()
        self.assertEqual(frame.analytics.operator_response, "CONFIRMED")
        runtime.configure(fault="VALVE_WEAR", enabled=True)
        self.assertEqual(runtime.operator_state()["response"], "PENDING")

    def test_operator_response_is_cleared_when_recommendation_changes(self) -> None:
        runtime = SimulationRuntime(seed=88, enable_ml=False)
        advance(runtime, 45)
        runtime.configure(
            fault="LUBRICATION_DEGRADATION",
            degradation_rate="rapid",
            enabled=True,
        )
        first_action = None
        for _ in range(int(120 / runtime.dt)):
            frame = runtime.step()
            if frame.analytics.recommendation != "CONTINUE":
                first_action = frame.analytics.recommendation
                break
        self.assertIsNotNone(first_action)
        runtime.record_operator_response("confirmed")
        for _ in range(int(120 / runtime.dt)):
            frame = runtime.step()
            if frame.analytics.recommendation != first_action:
                self.assertEqual(frame.analytics.operator_response, "PENDING")
                return
        self.fail("Expected the mission recommendation to change during rapid degradation")

    def test_abort_recommendation_does_not_chatter_during_active_fault(self) -> None:
        runtime = SimulationRuntime(seed=99, enable_ml=False)
        advance(runtime, 45)
        runtime.configure(
            fault="VALVE_WEAR", degradation_rate="rapid", enabled=True
        )
        abort_seen = False
        for _ in range(int(180 / runtime.dt)):
            frame = runtime.step()
            if frame.analytics.recommendation == "ABORT_LAND":
                abort_seen = True
            if abort_seen:
                self.assertEqual(frame.analytics.recommendation, "ABORT_LAND")
        self.assertTrue(abort_seen)


if __name__ == "__main__":
    unittest.main()
