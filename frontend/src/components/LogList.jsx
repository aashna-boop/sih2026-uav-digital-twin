const TONE_CLASS = {
  good: "ok",
  warn: "warn",
  danger: "crit",
  neutral: "",
};

export default function LogList({ events }) {
  return (
    <div className="glass-panel">
      <p className="panel-title">Human-in-the-loop audit · ground-control log</p>
      <div className="log-list">
        {events.length ? (
          events.map((entry, index) => (
            <div
              className={`log-entry ${TONE_CLASS[entry.tone] || ""}`}
              key={`${entry.time}-${index}`}
            >
              <span className="log-time">{entry.time}</span>
              <span className="log-msg">{entry.message}</span>
            </div>
          ))
        ) : (
          <p className="diag-empty">Waiting for system events…</p>
        )}
      </div>
    </div>
  );
}
