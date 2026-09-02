import {
  SENSOR_CRITICAL_SIGMA,
  SENSOR_DEVIATING_SIGMA,
  SENSOR_ORDER,
  fmt,
  sensorMeta,
} from "../lib/telemetry";

// The deviation colour comes from the backend's normalized residual (sigma),
// not from an absolute limit - that is the whole point of the twin.
function deviationClass(z) {
  const magnitude = Math.abs(Number(z) || 0);
  if (magnitude >= SENSOR_CRITICAL_SIGMA) return "critical";
  if (magnitude >= SENSOR_DEVIATING_SIGMA) return "deviating";
  return "";
}

export default function SensorList({ engine, selected, onSelect }) {
  return (
    <div className="sensor-list">
      {SENSOR_ORDER.map((key) => {
        const meta = sensorMeta[key];
        const actual = engine?.actual?.[key];
        const expected = engine?.expected?.[key];
        const z = engine?.normalized_residuals?.[key];
        const pct =
          actual != null
            ? Math.max(0, Math.min(100, (Number(actual) / meta.range) * 100))
            : 0;

        return (
          <button
            type="button"
            key={key}
            className={`sensor-card ${deviationClass(z)} ${selected === key ? "active" : ""}`}
            onClick={() => onSelect(key)}
            aria-pressed={selected === key}
          >
            <div className="sensor-row">
              <span className="sensor-name">{meta.label}</span>
              <span className="sensor-value">{fmt(actual, meta.digits)}</span>
            </div>
            <div className="bar-track">
              <div
                className="bar-fill"
                style={{ width: `${pct}%`, "--sensor-accent": meta.color }}
              />
            </div>
            <div className="sensor-sub">
              <span>twin {fmt(expected, meta.digits)}</span>
              <span>
                {fmt(z, 2)}σ · {meta.unit}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}
