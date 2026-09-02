import { fmt } from "../lib/telemetry";

export default function EnvelopePanel({ analytics, advisoryActive, onRespond }) {
  const response = analytics?.operator_response || "PENDING";
  const marginWidth = Math.max(
    2,
    Math.min(100, 50 + (analytics?.mission_safety_margin_min || 0) * 2),
  );

  return (
    <div className="glass-panel">
      <p className="panel-title">Mission reliability envelope</p>

      <div className="envelope-values">
        <span>
          Conservative RUL
          <strong>{fmt(analytics?.rul_low)} min</strong>
        </span>
        <span>
          Safe recovery
          <strong>{fmt(analytics?.time_to_safe_recovery_min)} min</strong>
        </span>
      </div>

      <div className="margin-track">
        <i style={{ width: `${marginWidth}%` }} />
      </div>
      <p className="envelope-note">
        The decision uses the conservative lower RUL bound, never the point estimate.
      </p>

      <div className="rul-box">
        <span className="rul-label">Remaining useful life</span>
        <span className="rul-value">{fmt(analytics?.rul_minutes)} min</span>
      </div>

      <div className="operator-state">
        <span>Operator</span>
        <strong
          className={
            response === "CONFIRMED" ? "good" : response === "DISMISSED" ? "warn" : ""
          }
        >
          {response}
        </strong>
      </div>
      <div className="operator-actions">
        <button
          type="button"
          className="btn btn-dismiss"
          disabled={!advisoryActive}
          onClick={() => onRespond("DISMISSED")}
        >
          Dismiss
        </button>
        <button
          type="button"
          className="btn btn-confirm"
          disabled={!advisoryActive}
          onClick={() => onRespond("CONFIRMED")}
        >
          Confirm advisory
        </button>
      </div>
    </div>
  );
}
