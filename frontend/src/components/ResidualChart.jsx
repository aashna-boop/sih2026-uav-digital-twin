import { SENSOR_ORDER, fmt, sensorMeta } from "../lib/telemetry";

function TrendChart({ points, meta, gradientId }) {
  const values = points.flatMap((point) => [point.actual, point.expected]);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 1);
  const spread = Math.max(max - min, 1);
  const path = (key) =>
    points
      .map((point, index) => {
        const x = points.length <= 1 ? 0 : (index / (points.length - 1)) * 100;
        const y = 95 - ((point[key] - min) / spread) * 88;
        return `${index ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");

  return (
    <div className="chart-wrap">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label={`${meta.label} trend`}>
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor={meta.color} stopOpacity="0.32" />
            <stop offset="1" stopColor={meta.color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[20, 40, 60, 80].map((y) => (
          <line key={y} x1="0" x2="100" y1={y} y2={y} className="chart-grid" />
        ))}
        {points.length > 1 && (
          <>
            <path d={`${path("actual")} L100,100 L0,100 Z`} fill={`url(#${gradientId})`} />
            <path d={path("expected")} className="expected-line" />
            <path
              d={path("actual")}
              className="actual-line"
              style={{ stroke: meta.color, filter: `drop-shadow(0 0 4px ${meta.color}88)` }}
            />
          </>
        )}
      </svg>
      <div className="chart-legend">
        <span>
          <i style={{ background: meta.color }} />
          Observed
        </span>
        <span>
          <i className="expected-key" />
          Twin expected
        </span>
        <span className="range">
          {fmt(min)}–{fmt(max)} {meta.unit}
        </span>
      </div>
    </div>
  );
}

export default function ResidualChart({ selected, onSelect, points, engine }) {
  const meta = sensorMeta[selected];

  return (
    <div className="glass-panel">
      <div className="panel-head">
        <p className="panel-title">Digital twin residual · {meta.label}</p>
        <select
          className="sensor-select"
          value={selected}
          onChange={(event) => onSelect(event.target.value)}
          aria-label="Select sensor channel"
        >
          {SENSOR_ORDER.map((key) => (
            <option key={key} value={key}>
              {sensorMeta[key].label}
            </option>
          ))}
        </select>
      </div>

      <div className="reading-row">
        <div>
          <span>Observed</span>
          <strong>
            {fmt(engine?.actual?.[selected], meta.digits)} {meta.unit}
          </strong>
        </div>
        <div>
          <span>Twin expected</span>
          <strong>
            {fmt(engine?.expected?.[selected], meta.digits)} {meta.unit}
          </strong>
        </div>
        <div>
          <span>Normalized residual</span>
          <strong>{fmt(engine?.normalized_residuals?.[selected], 2)}σ</strong>
        </div>
      </div>

      <TrendChart points={points} meta={meta} gradientId={`residual-${selected}`} />
    </div>
  );
}
