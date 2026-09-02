// Shared telemetry contract helpers for the AegisTwin console.
//
// The backend streams one frame per tick on /ws/telemetry and exposes the
// scenario + operator-response REST endpoints on the same origin port 8000.
// Resolving the host from window.location keeps LAN demos working when the
// dashboard is served with `--host 0.0.0.0`.

const HOST = (typeof window !== "undefined" && window.location.hostname) || "127.0.0.1";

export const API = `http://${HOST}:8000`;
export const WS_TELEMETRY = `ws://${HOST}:8000/ws/telemetry`;

export const MAX_POINTS = 90;
export const MAX_PATH_POINTS = 240;
export const RECONNECT_DELAY_MS = 1400;

// `range` is the full-scale value used to draw the sensor bars; it is a display
// concern only and never feeds the residual / diagnosis maths.
export const sensorMeta = {
  rpm: { label: "RPM", unit: "rpm", color: "#00E5FF", range: 6200, digits: 0 },
  egt_c: { label: "Exhaust gas temp", unit: "°C", color: "#FF003C", range: 850, digits: 0 },
  cht_c: { label: "Cylinder head temp", unit: "°C", color: "#FF7A00", range: 220, digits: 0 },
  oil_temp_c: { label: "Oil temp", unit: "°C", color: "#B026FF", range: 110, digits: 0 },
  oil_pressure_bar: { label: "Oil pressure", unit: "bar", color: "#00FFC2", range: 5.5, digits: 2 },
  fuel_flow_lph: { label: "Fuel flow", unit: "L/h", color: "#FFD400", range: 25, digits: 1 },
  vibration: { label: "Vibration", unit: "g", color: "#FF00E5", range: 6, digits: 2 },
};

export const SENSOR_ORDER = [
  "rpm",
  "egt_c",
  "cht_c",
  "oil_temp_c",
  "oil_pressure_bar",
  "fuel_flow_lph",
  "vibration",
];

export const FAULT_LABELS = {
  HEALTHY: "Healthy baseline",
  COOLING_DEGRADATION: "Cooling degradation",
  LUBRICATION_DEGRADATION: "Lubrication degradation",
  IGNITION_MISFIRE: "Ignition / misfire",
  VALVE_WEAR: "Valve wear",
};

export const INJECTABLE_FAULTS = [
  "COOLING_DEGRADATION",
  "LUBRICATION_DEGRADATION",
  "IGNITION_MISFIRE",
  "VALVE_WEAR",
];

export const DEGRADATION_RATES = ["slow", "medium", "rapid"];

// Normalized residual magnitudes (in sigma) that colour a sensor row. These are
// presentation thresholds for the bars only - the diagnosis itself comes from
// the backend model.
export const SENSOR_DEVIATING_SIGMA = 1.5;
export const SENSOR_CRITICAL_SIGMA = 3;

export function fmt(value, digits = 1) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "--";
}

export function humanize(value) {
  return String(value || "--").replaceAll("_", " ");
}

export function formatClock(tSec) {
  if (!Number.isFinite(Number(tSec))) return "--:--";
  const total = Math.max(0, Math.floor(Number(tSec)));
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

export function healthTone(score) {
  if (!Number.isFinite(Number(score))) return "neutral";
  if (score < 45) return "danger";
  if (score < 80) return "warn";
  return "good";
}

export function actionTone(recommendation) {
  if (recommendation === "ABORT_LAND") return "danger";
  if (recommendation === "MONITOR_RTB") return "warn";
  return "good";
}
