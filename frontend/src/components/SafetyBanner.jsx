export default function SafetyBanner({ severity, isCritical, onConfirm, onDismiss }) {
  return (
    <div className={`safety-banner show ${isCritical ? 'crit' : ''}`}>
      <div className="safety-text">
        <strong>
          {isCritical
            ? 'Critical: recommend abort and RTL'
            : 'Caution: recommend reduced throttle and monitor'}
        </strong>
        <span>
          Model severity {(severity * 100).toFixed(0)}/100 — confirm to act.
        </span>
      </div>
      <div className="safety-actions">
        <button className="btn btn-dismiss" onClick={onDismiss}>Dismiss</button>
        <button className={`btn btn-confirm ${isCritical ? 'crit' : ''}`} onClick={onConfirm}>
          Confirm action
        </button>
      </div>
    </div>
  );
}
