import { useMemo } from "react";

import { actionTone, fmt, healthTone, humanize } from "../lib/telemetry";

const TONE_COLOR = {
  good: "#00E5FF",
  warn: "#B026FF",
  danger: "#FF003C",
  neutral: "#404050",
};

function StatCard({ label, value, sub, tone = "neutral" }) {
  return (
    <div className={`stat-card glass-panel tone-${tone}`}>
      <span className="stat-label">{label}</span>
      <strong className="stat-value">{value}</strong>
      {sub && <small className="stat-sub">{sub}</small>}
    </div>
  );
}

export default function StatusRail({ analytics, flight }) {
  const tone = healthTone(analytics?.health_score);
  const score = Number(analytics?.health_score) || 0;
  const radialStyle = useMemo(
    () => ({
      background: `conic-gradient(${TONE_COLOR[tone]} ${score}%, rgba(255,255,255,0.06) 0)`,
      boxShadow: `0 0 26px ${TONE_COLOR[tone]}33`,
    }),
    [tone, score],
  );

  const faultActive = analytics?.fault && analytics.fault !== "HEALTHY";

  return (
    <section className="status-rail">
      <div className="health-card glass-panel">
        <div className="radial" style={radialStyle}>
          <span className={tone}>{fmt(analytics?.health_score, 0)}</span>
          <small>HEALTH</small>
        </div>
        <div className="health-copy">
          <span className="stat-label">Engine state</span>
          <h2 className={tone}>{humanize(analytics?.fault)}</h2>
          <p>{fmt((analytics?.confidence || 0) * 100, 0)}% diagnostic confidence</p>
        </div>
      </div>

      <StatCard
        label="Mission action"
        value={humanize(analytics?.recommendation)}
        sub={`Safety margin ${fmt(analytics?.mission_safety_margin_min)} min`}
        tone={actionTone(analytics?.recommendation)}
      />
      <StatCard
        label="RUL envelope"
        value={`${fmt(analytics?.rul_minutes)} min`}
        sub={`${fmt(analytics?.rul_low)}–${fmt(analytics?.rul_high)} min band`}
        tone={faultActive ? "warn" : "good"}
      />
      <StatCard
        label="Flight phase"
        value={humanize(flight?.flight_phase)}
        sub={`${fmt(flight?.altitude_m, 0)} m · ${fmt(flight?.airspeed_mps)} m/s`}
      />
    </section>
  );
}
