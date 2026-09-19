import { useMemo } from 'react';

export default function Header({ connected, tSec, altitude }) {
  const timeStr = useMemo(() => {
    if (tSec == null) return '00:00';
    const mm = String(Math.floor(tSec / 60)).padStart(2, '0');
    const ss = String(Math.floor(tSec % 60)).padStart(2, '0');
    return `${mm}:${ss}`;
  }, [tSec]);

  const altStr = altitude != null ? `${Math.round(altitude)} m` : '--';

  return (
    <header className="app-header">
      <div className="header-brand">
        <h1>UAV Piston Engine Digital Twin</h1>
        <p>Live predictions from the trained XGBoost model — real dataset rows, real inference, real SHAP</p>
      </div>
      <div className="header-meta">
        <div className="meta-item">
          <span className="meta-label">Connection</span>
          <span className={`conn-badge ${connected ? 'live' : 'down'}`}>
            <span className="pulse-dot" />
            {connected ? 'Live' : 'Connecting'}
          </span>
        </div>
        <div className="meta-item">
          <span className="meta-label">Flight time</span>
          <span className="meta-value">{timeStr}</span>
        </div>
        <div className="meta-item">
          <span className="meta-label">Altitude</span>
          <span className="meta-value">{altStr}</span>
        </div>
      </div>
    </header>
  );
}
