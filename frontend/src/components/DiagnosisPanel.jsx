const FAULT_LABELS = {
  healthy: 'Healthy',
  valve_wear: 'Valve Wear',
  cooling_failure: 'Cooling Failure',
  oil_pressure_drop: 'Oil Pressure Drop',
};

const SEV_MONITOR = 0.12;
const SEV_CRITICAL = 0.25;

export default function DiagnosisPanel({ prediction, trueFault }) {
  if (!prediction) {
    return <p className="diag-empty">Connecting to model server…</p>;
  }

  const { predicted_fault, confidence, severity, rul, shap } = prediction;
  const sev = severity;

  let sevClass = 'sev-nominal', sevText = 'Nominal';
  if (sev >= SEV_MONITOR && predicted_fault !== 'healthy') {
    sevClass = 'sev-monitor'; sevText = 'Monitor';
  }
  if (sev >= SEV_CRITICAL && predicted_fault !== 'healthy') {
    sevClass = 'sev-critical'; sevText = 'Critical';
  }

  const barColor =
    sevClass === 'sev-critical' ? 'var(--accent-red)' :
    sevClass === 'sev-monitor' ? 'var(--accent-amber)' :
    'var(--accent-teal)';

  const rulSec = Math.round(rul * 160);

  const maxAbsShap = Math.max(...(shap || []).map(x => Math.abs(x.value)), 0.001);

  return (
    <div>
      <div className="diag-header">
        <span className="diag-fault-name">
          {FAULT_LABELS[predicted_fault] || predicted_fault}
        </span>
        <span className={`severity-badge ${sevClass}`}>{sevText}</span>
      </div>

      <div className="diag-truth">
        Model confidence: {(confidence * 100).toFixed(1)}% — dataset ground truth:{' '}
        {FAULT_LABELS[trueFault] || trueFault}
      </div>

      <div className="severity-track">
        <div
          className="severity-fill"
          style={{ width: `${sev * 100}%`, background: barColor }}
        />
      </div>

      <p className="shap-section-label">Real SHAP attribution (this row, this prediction)</p>

      {(shap || []).map((c, i) => {
        const w = Math.max(4, (Math.abs(c.value) / maxAbsShap) * 100);
        const col = c.value >= 0 ? 'var(--accent-amber)' : 'var(--accent-blue)';
        return (
          <div key={i} className="shap-row">
            <span className="shap-label">{c.feature.replace(/_/g, ' ')}</span>
            <div className="shap-track">
              <div className="shap-fill" style={{ width: `${w}%`, background: col }} />
            </div>
            <span className="shap-val">{c.value.toFixed(2)}</span>
          </div>
        );
      })}

      <div className="rul-box">
        <span className="rul-label">Estimated remaining useful life</span>
        <span className="rul-value">{rulSec} s</span>
      </div>
    </div>
  );
}

export { SEV_MONITOR, SEV_CRITICAL, FAULT_LABELS };
