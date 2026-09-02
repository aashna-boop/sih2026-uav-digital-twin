import { fmt, humanize } from "../lib/telemetry";

// The badge is driven by the backend's own mission recommendation rather than a
// UI-side severity threshold, so the panel can never disagree with the advisory
// the operator is being asked to act on. The advisory is checked first: the
// classifier can still read HEALTHY while the health score has collapsed, and
// showing "Nominal" underneath a live abort call would be the wrong way to be
// wrong.
function severityBadge(analytics) {
  if (!analytics) {
    return { cls: "sev-nominal", text: "Nominal" };
  }
  if (analytics.recommendation === "ABORT_LAND") {
    return { cls: "sev-critical", text: "Critical" };
  }
  if (analytics.recommendation === "MONITOR_RTB" || analytics.fault !== "HEALTHY") {
    return { cls: "sev-monitor", text: "Monitor" };
  }
  return { cls: "sev-nominal", text: "Nominal" };
}

const BAR_COLOR = {
  "sev-nominal": "var(--accent-teal)",
  "sev-monitor": "var(--accent-amber)",
  "sev-critical": "var(--accent-red)",
};

export default function DiagnosisPanel({ analytics }) {
  if (!analytics) {
    return (
      <div className="glass-panel">
        <p className="panel-title">Model diagnosis</p>
        <p className="diag-empty">Waiting for the mission runtime…</p>
      </div>
    );
  }

  const badge = severityBadge(analytics);
  const contributors = analytics.top_contributors || [];
  const maxContribution = Math.max(
    ...contributors.map((item) => Number(item.magnitude) || 0),
    0.001,
  );
  const isTreeShap = contributors[0]?.kind === "tree_shap";

  return (
    <div className="glass-panel">
      <p className="panel-title">Model diagnosis · {humanize(analytics.model_source)}</p>

      <div className="diag-header">
        <span className="diag-fault-name">{humanize(analytics.fault)}</span>
        <span className={`severity-badge ${badge.cls}`}>{badge.text}</span>
      </div>
      <div className="diag-truth">
        {fmt(analytics.confidence * 100, 1)}% confidence · severity{" "}
        {fmt(analytics.severity * 100, 0)}/100
      </div>

      <div className="severity-track">
        <div
          className="severity-fill"
          style={{
            width: `${Math.max(0, Math.min(100, (analytics.severity || 0) * 100))}%`,
            background: BAR_COLOR[badge.cls],
          }}
        />
      </div>

      <div className="diag-explanation">
        {(analytics.explanation?.length ? analytics.explanation : ["Waiting for telemetry…"]).map(
          (line) => (
            <p key={line}>{line}</p>
          ),
        )}
      </div>

      <p className="shap-section-label">
        {isTreeShap
          ? "Native TreeSHAP attribution (this frame, this prediction)"
          : "Normalized residual evidence (this frame)"}
      </p>

      {contributors.map((item) => (
        <div key={item.sensor} className="shap-row">
          <span className="shap-label">{item.label}</span>
          <div className="shap-track">
            <div
              className="shap-fill"
              style={{
                width: `${Math.max(4, ((Number(item.magnitude) || 0) / maxContribution) * 100)}%`,
                background:
                  (item.shap_value ?? item.z_score ?? 0) < 0
                    ? "var(--accent-blue)"
                    : "var(--accent-amber)",
              }}
            />
          </div>
          <span className="shap-val">
            {isTreeShap ? fmt(item.shap_value, 3) : `${fmt(item.z_score, 2)}σ`}
          </span>
        </div>
      ))}
    </div>
  );
}
