import { useMemo } from "react";

import { fmt, humanize } from "../lib/telemetry";

export default function FlightPath({ points, flight, source }) {
  const geometry = useMemo(() => {
    if (!points.length) return { polyline: "", x: 50, y: 50 };
    const lats = points.map((point) => point.lat);
    const lons = points.map((point) => point.lon);
    const minLat = Math.min(...lats);
    const maxLat = Math.max(...lats);
    const minLon = Math.min(...lons);
    const maxLon = Math.max(...lons);
    const latSpan = Math.max(maxLat - minLat, 0.00008);
    const lonSpan = Math.max(maxLon - minLon, 0.00008);
    const projected = points.map((point) => ({
      x: 7 + ((point.lon - minLon) / lonSpan) * 86,
      y: 93 - ((point.lat - minLat) / latSpan) * 86,
    }));
    const last = projected.at(-1);
    return {
      polyline: projected
        .map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`)
        .join(" "),
      x: last.x,
      y: last.y,
    };
  }, [points]);

  return (
    <div className="glass-panel">
      <div className="panel-head">
        <p className="panel-title">Recent ground track</p>
        <span className="source-chip">{humanize(source)}</span>
      </div>

      <div className="path-body">
        <svg viewBox="0 0 100 100" aria-label="Recent aircraft ground track">
          <defs>
            <pattern id="mapGrid" width="10" height="10" patternUnits="userSpaceOnUse">
              <path d="M 10 0 L 0 0 0 10" className="map-grid" />
            </pattern>
            <filter id="trackGlow">
              <feGaussianBlur stdDeviation="1.4" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          <rect width="100" height="100" fill="url(#mapGrid)" />
          {geometry.polyline && (
            <polyline points={geometry.polyline} className="ground-track" />
          )}
          <circle
            cx={geometry.x}
            cy={geometry.y}
            r="2.2"
            className="aircraft-dot"
            filter="url(#trackGlow)"
          />
          <path
            d={`M${geometry.x - 4},${geometry.y} L${geometry.x + 4},${geometry.y} M${geometry.x},${geometry.y - 4} L${geometry.x},${geometry.y + 4}`}
            className="aircraft-cross"
          />
        </svg>
        <div className="path-meta">
          <span>
            LAT <strong>{fmt(flight?.latitude_deg, 5)}</strong>
          </span>
          <span>
            LON <strong>{fmt(flight?.longitude_deg, 5)}</strong>
          </span>
          <span>
            MODE <strong>{flight?.flight_mode || "--"}</strong>
          </span>
          <span>
            ALT <strong>{fmt(flight?.altitude_m, 0)} m</strong>
          </span>
        </div>
      </div>
    </div>
  );
}
