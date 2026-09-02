export default function LogList({ logs }) {
  return (
    <div className="log-list">
      {logs.map((l, i) => (
        <div key={i} className={`log-entry ${l.cls || ''}`}>
          <span className="log-time">{l.time}</span>
          <span className="log-msg">{l.msg}</span>
        </div>
      ))}
    </div>
  );
}
