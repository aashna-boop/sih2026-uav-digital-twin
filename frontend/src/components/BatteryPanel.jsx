import React from 'react';

export default function BatteryPanel({ battery }) {
  if (!battery) {
    return (
      <div className="battery-panel">
        <div className="battery-placeholder">Battery data offline</div>
      </div>
    );
  }

  const { voltage_v = 0, current_a = 0, soc_pct = 100 } = battery;

  // Determine status / color theme based on SOC and voltage
  let statusClass = 'ok';
  let statusText = 'HEALTHY';
  if (soc_pct < 20 || voltage_v < 11.5) {
    statusClass = 'crit';
    statusText = 'CRITICAL';
  } else if (soc_pct < 40 || voltage_v < 12.2) {
    statusClass = 'warn';
    statusText = 'LOW CHARGE';
  }

  const isCharging = current_a > 0;

  return (
    <div className="battery-panel">
      <div className="battery-header">
        <div className="battery-title">
          <span className="battery-icon">⚡</span>
          <span>Electrical System</span>
        </div>
        <div className={`battery-status ${statusClass}`}>{statusText}</div>
      </div>

      <div className="battery-main-stat">
        <div className="soc-gauge">
          <div className="soc-bar-bg">
            <div
              className={`soc-bar-fill ${statusClass}`}
              style={{ width: `${Math.max(0, Math.min(100, soc_pct))}%` }}
            />
          </div>
          <span className="soc-text">{soc_pct.toFixed(1)}%</span>
        </div>
      </div>

      <div className="battery-stats-grid">
        <div className="stat-box">
          <div className="stat-label">Bus Voltage</div>
          <div className="stat-value">{voltage_v.toFixed(2)} <span className="unit">V</span></div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Net Current</div>
          <div className="stat-value">
            {isCharging ? `+${current_a.toFixed(1)}` : current_a.toFixed(1)} <span className="unit">A</span>
          </div>
        </div>
      </div>
    </div>
  );
}
