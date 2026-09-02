import { fmt } from "../lib/telemetry";

export default function SafetyBanner({ analytics, onRespond }) {
  const isCritical = analytics?.recommendation === "ABORT_LAND";

  return (
    <div className={`safety-banner ${isCritical ? "crit" : ""}`} role="alert">
      <div className="safety-text">
        <strong>
          {isCritical
            ? "Critical — recommend abort and land"
            : "Caution — recommend monitor and return to base"}
        </strong>
        <span>
          Conservative RUL {fmt(analytics?.rul_low)} min vs {" "}
          {fmt(analytics?.time_to_safe_recovery_min)} min to safe recovery · margin{" "}
          {fmt(analytics?.mission_safety_margin_min)} min. Advisory only — acknowledgement
          is logged, no command is sent to the aircraft.
        </span>
      </div>
      <div className="safety-actions">
        <button type="button" className="btn btn-dismiss" onClick={() => onRespond("DISMISSED")}>
          Dismiss
        </button>
        <button
          type="button"
          className={`btn btn-confirm ${isCritical ? "crit" : ""}`}
          onClick={() => onRespond("CONFIRMED")}
        >
          Confirm advisory
        </button>
      </div>
    </div>
  );
}
