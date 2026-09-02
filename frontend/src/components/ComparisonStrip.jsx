import { humanize } from "../lib/telemetry";

export default function ComparisonStrip({ frame, faultActive }) {
  const legacyAlarm = frame?.traditional_status === "ALARM";

  return (
    <section className="comparison glass-panel">
      <div className="comparison-side">
        <span className="stat-label">Legacy thresholds</span>
        <strong className={legacyAlarm ? "danger" : "good"}>
          {frame?.traditional_status || "--"}
        </strong>
      </div>
      <div className="versus">VS</div>
      <div className="comparison-side">
        <span className="stat-label">Context-aware twin</span>
        <strong className={faultActive ? "warn" : "good"}>
          {humanize(frame?.twin_status)}
        </strong>
      </div>
      <p className="comparison-note">
        {faultActive && !legacyAlarm
          ? "Early degradation identified while all absolute limits remain normal."
          : "Twin residual monitoring is synchronized with the current flight envelope."}
      </p>
    </section>
  );
}
