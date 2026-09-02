import { DEGRADATION_RATES, FAULT_LABELS, INJECTABLE_FAULTS } from "../lib/telemetry";

export default function ScenarioLab({ scenario, onApply, onReset }) {
  const activeFault = scenario.enabled ? scenario.fault : "HEALTHY";

  return (
    <div className="glass-panel">
      <p className="panel-title">Controlled test · scenario lab</p>

      <div className="fault-list">
        {INJECTABLE_FAULTS.map((fault) => (
          <button
            type="button"
            key={fault}
            className={`fault-btn ${activeFault === fault ? "active" : ""}`}
            onClick={() => onApply({ fault, enabled: true })}
          >
            {FAULT_LABELS[fault]}
            <span className="fkey">Physical plant degradation only</span>
          </button>
        ))}
      </div>

      <button
        type="button"
        className="clear-btn"
        onClick={() => onApply({ fault: "HEALTHY", enabled: false })}
      >
        Return to healthy baseline
      </button>

      <div className="rate-group">
        <span className="rate-label">Degradation rate</span>
        <div className="segmented">
          {DEGRADATION_RATES.map((rate) => (
            <button
              type="button"
              key={rate}
              className={scenario.degradation_rate === rate ? "active" : ""}
              onClick={() => onApply({ degradation_rate: rate })}
            >
              {rate}
            </button>
          ))}
        </div>
      </div>

      <button type="button" className="clear-btn" onClick={onReset}>
        Reset mission runtime
      </button>

      <p className="scenario-note">
        Faults modify physical plant parameters only. Operator responses acknowledge an
        advisory and never command the aircraft autonomously.
      </p>
    </div>
  );
}
