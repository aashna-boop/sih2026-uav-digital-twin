import { FAULT_LABELS } from './DiagnosisPanel';

export default function FaultControls({ activeFault, onInjectFault, onClearFault }) {
  const faults = ['oil_pressure_drop', 'cooling_failure', 'valve_wear'];

  return (
    <div>
      {faults.map(f => (
        <button
          key={f}
          className={`fault-btn ${activeFault === f ? 'active' : ''}`}
          onClick={() => onInjectFault(f)}
        >
          {FAULT_LABELS[f] || f}
          <span className="fkey">Real recorded fault run</span>
        </button>
      ))}
      <button className="clear-btn" onClick={onClearFault}>
        Back to healthy run
      </button>
    </div>
  );
}
