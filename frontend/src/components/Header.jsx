import { Link } from "react-router-dom";

import { fmt, formatClock, humanize } from "../lib/telemetry";

export default function Header({ connected, source, flight }) {
  return (
    <header className="app-header">
      <div className="header-brand">
        <span className="brand-mark">AT</span>
        <div>
          <h1>AegisTwin</h1>
          <p>SIH26054 · UAV piston-engine mission reliability console</p>
        </div>
      </div>

      <div className="header-meta">
        <div className="meta-item">
          <span className="meta-label">Connection</span>
          <span className={`conn-badge ${connected ? "live" : "down"}`}>
            <span className="pulse-dot" />
            {connected ? "Live · 5 Hz" : "Reconnecting"}
          </span>
        </div>
        <div className="meta-item">
          <span className="meta-label">Source</span>
          <span className="meta-value small">{humanize(source)}</span>
        </div>
        <div className="meta-item">
          <span className="meta-label">Flight time</span>
          <span className="meta-value">{formatClock(flight?.t_sec)}</span>
        </div>
        <div className="meta-item">
          <span className="meta-label">Altitude</span>
          <span className="meta-value">{fmt(flight?.altitude_m, 0)} m</span>
        </div>
        <Link className="header-link" to="/">
          Landing
        </Link>
      </div>
    </header>
  );
}
