import { useState } from "react";

import ComparisonStrip from "../components/ComparisonStrip";
import DiagnosisPanel from "../components/DiagnosisPanel";
import EnvelopePanel from "../components/EnvelopePanel";
import FlightPath from "../components/FlightPath";
import Header from "../components/Header";
import LogList from "../components/LogList";
import ResidualChart from "../components/ResidualChart";
import SafetyBanner from "../components/SafetyBanner";
import ScenarioLab from "../components/ScenarioLab";
import SensorList from "../components/SensorList";
import StatusRail from "../components/StatusRail";
import { useTelemetry } from "../hooks/useTelemetry";
import { fmt, humanize } from "../lib/telemetry";

export default function Console() {
  const {
    frame,
    connected,
    history,
    pathHistory,
    events,
    scenario,
    applyScenario,
    resetMission,
    respondToAdvisory,
  } = useTelemetry();

  const [selectedSensor, setSelectedSensor] = useState("oil_pressure_bar");

  const analytics = frame?.analytics;
  const faultActive = Boolean(analytics?.fault && analytics.fault !== "HEALTHY");
  const telemetrySource = frame?.flight.telemetry_source || "WAITING";
  const advisoryActive = Boolean(
    analytics?.recommendation && analytics.recommendation !== "CONTINUE",
  );
  // The backend clears the acknowledgement whenever the advisory changes, so it
  // stays the single source of truth for whether the operator still owes a call.
  const bannerVisible = advisoryActive && analytics?.operator_response === "PENDING";

  return (
    <div className="app-container">
      <Header connected={connected} source={telemetrySource} flight={frame?.flight} />

      <StatusRail analytics={analytics} flight={frame?.flight} />

      <ComparisonStrip frame={frame} faultActive={faultActive} />

      <div className="main-grid">
        <div className="left-column">
          <div className="glass-panel">
            <p className="panel-title">Live telemetry · observed vs twin</p>
            <SensorList
              engine={frame?.engine}
              selected={selectedSensor}
              onSelect={setSelectedSensor}
            />
          </div>
        </div>

        <div className="center-column">
          <ResidualChart
            selected={selectedSensor}
            onSelect={setSelectedSensor}
            points={history[selectedSensor] || []}
            engine={frame?.engine}
          />
          <FlightPath
            points={pathHistory}
            flight={frame?.flight}
            source={telemetrySource}
          />
          <LogList events={events} />
        </div>

        <div className="right-column">
          <ScenarioLab scenario={scenario} onApply={applyScenario} onReset={resetMission} />
          <DiagnosisPanel analytics={analytics} />
          <EnvelopePanel
            analytics={analytics}
            advisoryActive={advisoryActive}
            onRespond={respondToAdvisory}
          />
        </div>
      </div>

      {bannerVisible && (
        <>
          <SafetyBanner analytics={analytics} onRespond={respondToAdvisory} />
          <div className="banner-spacer" />
        </>
      )}

      <footer className="app-footer">
        <span>Source: {humanize(telemetrySource)}</span>
        <span>Profile: {frame?.flight.profile_id || "--"}</span>
        <span>Run: {frame?.run_id || "--"}</span>
        <span>Model: {humanize(analytics?.model_source)}</span>
        <span>Processing: {fmt(frame?.processing_latency_ms, 2)} ms</span>
        <span className="footer-flag">SIMULATION-VALIDATED · NOT FLIGHT CERTIFIED</span>
      </footer>
    </div>
  );
}
