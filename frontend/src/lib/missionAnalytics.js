/*
 * missionAnalytics.js — Section F (dashboard completion) logic:
 *   1. Engine efficiency trends   (twin-relative indices + slopes)
 *   2. Maintenance advisory       (fault-specific, severity-graded, latched)
 *   3. Mission-wise health report (per-run recorder, summary, CSV/JSON export)
 *
 * Everything here is computed from the WebSocket payload the backends
 * already send (sensors, expected, prediction, true_fault, true_severity).
 * No values are invented: every number in a report traces back to a
 * streamed sample.
 *
 * NOTE: static/index.html carries an inline copy of this same code (the
 * FastAPI backends serve that file without a static mount). If you change
 * logic here, mirror it there.
 */

// Same thresholds the rest of the dashboard uses (see MODEL_REPORT.md —
// calibrated to the severity regressor's observed 0.32-0.36 peak).
export const SEV_MONITOR = 0.12;
export const SEV_CRITICAL = 0.25;

// Detection is "confirmed" only after this many consecutive non-healthy
// predictions of the same class. The raw classifier flickers near fault
// onset (up to ~13 class flips over a valve-wear replay), so a single
// sample would produce noisy advisories.
export const CONFIRM_N = 3;

const EMA_ALPHA = 0.2;
const SLOPE_WINDOW = 20; // samples used for the trend slope

export const FAULT_LABELS = {
  healthy: 'Healthy',
  valve_wear: 'Valve wear',
  cooling_failure: 'Cooling failure',
  oil_pressure_drop: 'Oil pressure drop',
  misfire: 'Misfire',
  injector_fault: 'Injector fault',
  combustion_instability: 'Combustion instability',
  sensor_fault: 'Sensor fault',
};

const SENSOR_LABELS = {
  rpm: 'RPM', egt_c: 'EGT', cht_c: 'CHT', oil_temp_c: 'Oil temp',
  oil_pressure_bar: 'Oil pressure', fuel_flow_lph: 'Fuel flow', vibration: 'Vibration',
};

/* ------------------------------------------------------------------ */
/* 1. Efficiency trend metrics                                         */
/* ------------------------------------------------------------------ */

// Display bands were chosen from this dataset: fault-run samples before
// fault onset stay inside the "nominal" band after smoothing. They are
// dashboard display bands, NOT engine-manufacturer limits.
export const TREND_METRICS = {
  fuel: {
    label: 'Fuel efficiency index', short: 'Fuel eff.', unit: '%',
    help: 'RPM per L/h of fuel, actual vs twin-expected (100 = matches healthy twin)',
    higherIsBetter: true, watch: 95, degraded: 90, yMin: 80, yMax: 110,
  },
  lube: {
    label: 'Lubrication index', short: 'Lubrication', unit: '%',
    help: 'Oil pressure, actual vs twin-expected (100 = matches healthy twin)',
    higherIsBetter: true, watch: 90, degraded: 75, yMin: 50, yMax: 110,
  },
  thermal: {
    label: 'CHT excess over twin', short: 'CHT excess', unit: '°C',
    help: 'Cylinder head temperature above the twin-expected value',
    higherIsBetter: false, watch: 10, degraded: 25, yMin: -10, yMax: 50,
  },
  health: {
    label: 'Model health index', short: 'Health', unit: '',
    help: '100 − model severity × 100 (ML severity regressor output)',
    higherIsBetter: true, watch: 100 - SEV_MONITOR * 100, degraded: 100 - SEV_CRITICAL * 100, yMin: 50, yMax: 102,
  },
  rul: {
    label: 'Remaining life index', short: 'RUL', unit: '%',
    help: 'ML-predicted remaining useful life as % of full life (ML RUL regressor output × 100)',
    // The trained RUL target is exactly 1 - severity, so use the same bands as the health index (derived from SEV_*).
    higherIsBetter: true, watch: 100 - SEV_MONITOR * 100, degraded: 100 - SEV_CRITICAL * 100, yMin: 40, yMax: 102,
  },
};
export const TREND_KEYS = ['fuel', 'lube', 'thermal', 'health', 'rul'];

export function rawIndices(d) {
  const s = d.sensors, e = d.expected;
  const sev = d.prediction ? d.prediction.severity : 0;
  const rul = d.prediction ? d.prediction.rul : null;
  const safe = (x) => (Number.isFinite(x) ? x : null);
  return {
    fuel: safe(((s.rpm / s.fuel_flow_lph) / (e.rpm / e.fuel_flow_lph)) * 100),
    lube: safe((s.oil_pressure_bar / e.oil_pressure_bar) * 100),
    thermal: safe(s.cht_c - e.cht_c),
    health: safe(100 - sev * 100),
    rul: rul == null ? null : safe(rul * 100),
  };
}

export function bandOf(key, v) {
  const m = TREND_METRICS[key];
  if (v == null) return 'nominal';
  if (m.higherIsBetter) {
    if (v < m.degraded) return 'degraded';
    if (v < m.watch) return 'watch';
    return 'nominal';
  }
  if (v > m.degraded) return 'degraded';
  if (v > m.watch) return 'watch';
  return 'nominal';
}

// Least-squares slope, returned in units per minute of flight time.
export function slopePerMin(ts, vs) {
  const n = ts.length;
  if (n < 5) return null;
  let st = 0, sv = 0;
  for (let i = 0; i < n; i++) { st += ts[i]; sv += vs[i]; }
  const mt = st / n, mv = sv / n;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) { num += (ts[i] - mt) * (vs[i] - mv); den += (ts[i] - mt) ** 2; }
  if (den === 0) return null;
  return (num / den) * 60;
}

/* ------------------------------------------------------------------ */
/* 2. Maintenance advisory content                                     */
/* ------------------------------------------------------------------ */

// Generic, typical checks for each fault class on a Rotax-912-class
// piston engine. Decision support only — the engine manufacturer's
// maintenance manual is the authority.
export const ADVISORIES = {
  oil_pressure_drop: {
    system: 'Lubrication system',
    early: [
      'Early oil-pressure deviation from twin. Note for post-flight inspection.',
      'Watch oil temperature alongside oil pressure for correlated rise.',
    ],
    monitor: [
      'Inspect oil system before next flight: oil level, filter, external leaks at lines and fittings.',
      'Cross-check oil pressure sender against a mechanical gauge to rule out sensor fault.',
      'Reduce high-power operation for the remainder of the sortie.',
    ],
    critical: [
      'Recommend return to base; minimise power demand en route.',
      'Ground engine after landing: inspect oil pump, pressure-relief valve and filter for blockage.',
      'Do not release for flight until oil pressure is verified within limits on ground run.',
    ],
  },
  cooling_failure: {
    system: 'Cooling system',
    early: [
      'Early CHT / oil-temperature rise over twin. Note for post-flight inspection.',
      'Prefer higher airspeed / lower climb rate to improve cooling airflow.',
    ],
    monitor: [
      'Inspect cooling path before next flight: cowling inlets, baffles, radiator and coolant hoses.',
      'Check coolant level and radiator for blockage; verify CHT probe seating.',
      'Limit sustained climb and high-power settings.',
    ],
    critical: [
      'Recommend reduced throttle and return to base before CHT limit is reached.',
      'Ground engine after landing: full cooling-system inspection and coolant pressure test.',
      'Inspect cylinder heads for heat damage before release to flight.',
    ],
  },
  valve_wear: {
    system: 'Valve train / combustion',
    early: [
      'Early RPM loss with EGT and fuel-flow rise vs twin. Note for post-flight inspection.',
      'Trend fuel efficiency index over the next sorties.',
    ],
    monitor: [
      'Schedule cylinder differential compression (leak-down) test before next flight.',
      'Check valve clearances; borescope-inspect valves and seats.',
      'Review EGT trend for combustion inefficiency.',
    ],
    critical: [
      'Recommend return to base; engine is producing reduced power for the fuel burned.',
      'Ground engine after landing: compression test and valve/seat inspection before further flight.',
    ],
  },
  misfire: {
    system: 'Ignition / combustion (misfire)',
    early: [
      'Early intermittent RPM/vibration deviation with periodic EGT spikes. Note for post-flight inspection.',
      'Log whether the deviation correlates with a specific throttle setting or spans the power range.',
    ],
    monitor: [
      'Inspect ignition system before next flight: spark plugs, leads, magneto timing.',
      'Check for intermittent fuel starvation (filter, pump pressure) as a contributing cause.',
      'Reduce high-power operation; a sustained misfire under load risks a hot exhaust event.',
    ],
    critical: [
      'Recommend return to base; avoid full-power settings where a skipped cylinder is most disruptive.',
      'Ground engine after landing: full ignition and fuel-delivery inspection before further flight.',
      'Borescope-inspect the affected cylinder for plug fouling or valve damage before release.',
    ],
  },
  injector_fault: {
    system: 'Fuel injection',
    early: [
      'Early irregular fuel-flow deviation from twin, trending rich (lower EGT than expected). Note for post-flight inspection.',
      'Watch for a widening fuel-flow oscillation rather than a single-direction trend.',
    ],
    monitor: [
      "Inspect the injector(s) before next flight for dribble/leak-past; bench-test flow if available.",
      'Check the fuel pressure regulator and mixture control for a stuck-rich condition.',
      'Trend fuel efficiency index -- a rich-running injector shows as a sustained efficiency loss.',
    ],
    critical: [
      'Recommend return to base; a badly over-fueling injector risks plug fouling and rough running.',
      'Ground engine after landing: injector replacement/overhaul and fuel-system inspection before further flight.',
    ],
  },
  combustion_instability: {
    system: 'Combustion stability',
    early: [
      'Early erratic vibration/RPM variance with no clear directional trend. Note for post-flight inspection.',
      'Distinguish from misfire: combustion is present but irregular, not fully dropping out.',
    ],
    monitor: [
      'Inspect mixture distribution, air filter and intake for uneven cylinder-to-cylinder combustion.',
      'Check engine mounts and propeller balance -- mechanical vibration sources can present similarly.',
      'Increase monitoring frequency: this class is harder for the model to catch early (see MODEL_REPORT.md).',
    ],
    critical: [
      'Recommend reduced throttle and return to base; sustained rough running accelerates mechanical wear.',
      'Ground engine after landing: full combustion-system inspection (mixture, timing, compression) before release.',
    ],
  },
  sensor_fault: {
    system: 'Sensor / instrumentation',
    early: [
      'Early single-channel reading deviation with no corresponding change on related channels. Note for post-flight inspection.',
      'Cross-check the flagged channel against a second independent indication before acting on it.',
    ],
    monitor: [
      'Do not act on the flagged channel alone; verify with a backup gauge/sensor or a ground check.',
      'Inspect the sensor, its wiring harness and connector for damage, corrosion or a loose pin.',
      'This is a suspected SENSOR fault, not an engine fault -- do not schedule engine teardown from this alone.',
    ],
    critical: [
      'Recommend return to base if the flagged channel is safety-relevant and cannot be cross-verified in flight.',
      'Ground the affected sensor circuit after landing: recalibrate or replace before trusting that channel again.',
    ],
  },
};

export function severityLevel(fault, sev) {
  if (!fault || fault === 'healthy') return 'nominal';
  if (sev >= SEV_CRITICAL) return 'critical';
  if (sev >= SEV_MONITOR) return 'monitor';
  return 'early';
}

export const LEVEL_TEXT = {
  nominal: 'No action', early: 'Early indication', monitor: 'Inspect before next flight', critical: 'Critical',
};
const LEVEL_RANK = { nominal: 0, early: 1, monitor: 2, critical: 3 };

// Top SHAP attributions that point at an engine sensor (flight-state
// features like roll/pitch/airspeed are excluded from "evidence" because
// they describe the flight, not the engine).
export function sensorEvidence(shap) {
  if (!shap) return [];
  const out = [];
  for (const c of shap) {
    // NOTE: features are `{sensor}_resid_smooth` since the 7-feature model
    // retrain -- this used to strip `_resid`/`_resid_pct`, a leftover from
    // the old 25-feature model, which meant this always returned [] (no
    // suffix ever matched) and the "Model evidence" line in the maintenance
    // advisory panel was silently empty. Fixed here.
    const base = c.feature.replace(/_resid_smooth$/, '');
    if (!SENSOR_LABELS[base] || c.value <= 0) continue;
    out.push({ feature: c.feature, label: SENSOR_LABELS[base] + ' deviation', value: c.value });
  }
  return out.slice(0, 3);
}

/* ------------------------------------------------------------------ */
/* 3. Mission recorder                                                 */
/* ------------------------------------------------------------------ */

let runCounter = 0;

export class MissionRecorder {
  constructor(runKey = null) {
    runCounter += 1;
    this.id = runCounter;
    this.runKey = runKey;
    this.startedAt = new Date();
    this.endedAt = null;
    this.samples = [];
    this.events = [];     // operator actions + advisory escalations
    this.ema = {};
    this.streak = { cls: null, n: 0 };
    this.detection = null;   // {fault, t}
    this.advisory = null;    // latched {fault, level, since, evidence}
  }

  add(d) {
    const p = d.prediction || {};
    const raw = rawIndices(d);
    const smooth = {};
    for (const k of TREND_KEYS) {
      if (raw[k] == null) { smooth[k] = this.ema[k] ?? null; continue; }
      this.ema[k] = this.ema[k] == null ? raw[k] : EMA_ALPHA * raw[k] + (1 - EMA_ALPHA) * this.ema[k];
      smooth[k] = this.ema[k];
    }

    // Debounced detection: CONFIRM_N consecutive same-class predictions.
    const cls = p.predicted_fault || 'healthy';
    if (cls === this.streak.cls) this.streak.n += 1; else this.streak = { cls, n: 1 };
    const confirmedCls = this.streak.n >= CONFIRM_N ? cls : null;
    if (!this.detection && confirmedCls && confirmedCls !== 'healthy') {
      this.detection = { fault: confirmedCls, t: d.t_sec };
      this.events.push({ t: d.t_sec, kind: 'detect', msg: `Fault confirmed: ${FAULT_LABELS[confirmedCls] || confirmedCls}` });
    }

    // Advisory latches once a fault is confirmed and only escalates within
    // a run (a maintenance finding does not disappear because the
    // classifier flickers back to "healthy" for a sample).
    if (confirmedCls && confirmedCls !== 'healthy') {
      const level = severityLevel(confirmedCls, p.severity ?? 0);
      const cur = this.advisory;
      if (!cur || cur.fault !== confirmedCls || LEVEL_RANK[level] > LEVEL_RANK[cur.level]) {
        this.advisory = { fault: confirmedCls, level, since: d.t_sec, evidence: sensorEvidence(p.shap) };
        this.events.push({ t: d.t_sec, kind: 'advisory', level, fault: confirmedCls,
          msg: `Advisory (${LEVEL_TEXT[level]}): ${ADVISORIES[confirmedCls]?.system || confirmedCls}` });
      } else if (cur.fault === confirmedCls) {
        cur.evidence = sensorEvidence(p.shap).length ? sensorEvidence(p.shap) : cur.evidence;
      }
    }

    const sample = {
      t: d.t_sec,
      true_fault: d.true_fault,
      true_severity: d.true_severity,
      predicted: cls,
      confidence: p.confidence ?? null,
      severity: p.severity ?? null,
      rul: p.rul ?? null,
      altitude: d.altitude,
      sensors: { ...d.sensors },
      expected: { ...d.expected },
      idx: smooth,
    };
    this.samples.push(sample);
    return sample;
  }

  logEvent(t, kind, msg) { this.events.push({ t, kind, msg }); }

  finish() { if (!this.endedAt) this.endedAt = new Date(); }

  trend(key) {
    const tail = this.samples.slice(-SLOPE_WINDOW).filter((s) => s.idx[key] != null);
    const last = tail.length ? tail[tail.length - 1].idx[key] : null;
    return {
      value: last,
      slope: slopePerMin(tail.map((s) => s.t), tail.map((s) => s.idx[key])),
      band: bandOf(key, last),
    };
  }

  summary() {
    const S = this.samples;
    if (!S.length) return null;
    const first = (fn) => S.find(fn) || null;
    const nonHealthyConfirmedFault = this.detection ? this.detection.fault : null;
    const peak = S.reduce((a, b) => ((b.severity ?? -1) > (a.severity ?? -1) ? b : a), S[0]);
    const minRul = S.reduce((a, b) => ((b.rul ?? 2) < (a.rul ?? 2) ? b : a), S[0]);
    // Onset is only observable if the recording started before it (a
    // dashboard that joins a stream mid-fault can't measure latency).
    const onsetSeenFromStart = (S[0].true_severity ?? 0) === 0;
    const trueOnset = onsetSeenFromStart ? first((s) => (s.true_severity ?? 0) > 0) : null;
    const truthFaults = [...new Set(S.map((s) => s.true_fault).filter((f) => f && f !== 'healthy'))];
    const firstMonitor = first((s) => s.predicted !== 'healthy' && (s.severity ?? 0) >= SEV_MONITOR);
    const firstCritical = first((s) => s.predicted !== 'healthy' && (s.severity ?? 0) >= SEV_CRITICAL);

    const classShare = {};
    for (const s of S) classShare[s.predicted] = (classShare[s.predicted] || 0) + 1;
    for (const k in classShare) classShare[k] = classShare[k] / S.length;

    // Agreement with dataset/simulator ground truth, sample by sample.
    const withTruth = S.filter((s) => s.true_fault);
    const agree = withTruth.filter((s) => s.true_fault === s.predicted).length;

    const indexStats = {};
    for (const k of TREND_KEYS) {
      const vals = S.map((s) => s.idx[k]).filter((v) => v != null);
      if (!vals.length) continue;
      const m = TREND_METRICS[k];
      indexStats[k] = {
        start: vals[0], end: vals[vals.length - 1],
        worst: m.higherIsBetter ? Math.min(...vals) : Math.max(...vals),
        endBand: bandOf(k, vals[vals.length - 1]),
      };
    }

    const worstLevel = this.events
      .filter((e) => e.kind === 'advisory')
      .reduce((a, e) => (LEVEL_RANK[e.level] > LEVEL_RANK[a] ? e.level : a), 'nominal');

    // Label from what was actually streamed (ground truth) where possible,
    // falling back to the run the operator selected.
    const key = truthFaults[0] || this.runKey || 'healthy';
    const joined = !this.runKey && S[0].t > 5;

    let latency = null;
    if (this.detection && trueOnset) latency = this.detection.t - trueOnset.t;

    return {
      run_id: this.id,
      run_label: (FAULT_LABELS[key] || key) + (joined ? ' (joined in progress)' : ''),
      run_key: key,
      truth_onset_observed: onsetSeenFromStart,
      started_at: this.startedAt.toISOString(),
      ended_at: this.endedAt ? this.endedAt.toISOString() : null,
      complete: !!this.endedAt,
      t_start: S[0].t,
      t_end: S[S.length - 1].t,
      n_samples: S.length,
      outcome_level: worstLevel,
      detected_fault: nonHealthyConfirmedFault,
      detection_t: this.detection ? this.detection.t : null,
      truth_faults: truthFaults,
      true_onset_t: trueOnset ? trueOnset.t : null,
      detection_latency_s: latency,
      first_monitor_t: firstMonitor ? firstMonitor.t : null,
      first_critical_t: firstCritical ? firstCritical.t : null,
      peak_severity: peak.severity,
      peak_severity_t: peak.t,
      min_rul: minRul.rul,
      min_rul_t: minRul.t,
      class_share: classShare,
      truth_agreement: withTruth.length ? agree / withTruth.length : null,
      index_stats: indexStats,
      advisory: this.advisory,
      events: this.events.slice(),
    };
  }

  toCSV() {
    const sensors = ['rpm', 'egt_c', 'cht_c', 'oil_temp_c', 'oil_pressure_bar', 'fuel_flow_lph', 'vibration'];
    const head = ['t_sec', 'altitude_m', 'true_fault', 'true_severity', 'predicted_fault', 'confidence', 'severity', 'rul_frac',
      ...sensors, ...sensors.map((s) => s + '_expected'), ...TREND_KEYS.map((k) => 'idx_' + k)];
    const f = (v) => (v == null ? '' : typeof v === 'number' ? +v.toFixed(4) : String(v));
    const rows = this.samples.map((s) => [s.t, s.altitude, s.true_fault, s.true_severity, s.predicted, s.confidence, s.severity, s.rul,
      ...sensors.map((k) => s.sensors[k]), ...sensors.map((k) => s.expected[k]), ...TREND_KEYS.map((k) => s.idx[k])].map(f).join(','));
    return [head.join(','), ...rows].join('\n');
  }

  toJSON() {
    return JSON.stringify({
      generated_by: 'UAV Engine Digital Twin dashboard — mission health report',
      note: 'Simulated / replayed telemetry. Model outputs are decision support, not certified diagnostics.',
      summary: this.summary(),
      samples: this.samples,
    }, null, 2);
  }
}

export function fmtT(t) {
  if (t == null || !Number.isFinite(t)) return '—';
  const mm = String(Math.floor(t / 60)).padStart(2, '0');
  const ss = String(Math.floor(t % 60)).padStart(2, '0');
  return `${mm}:${ss}`;
}

export function downloadText(filename, text, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ------------------------------------------------------------------ */
/* Canvas line chart (shared by trends panel and report)               */
/* ------------------------------------------------------------------ */

// Sizes from CSS width + a fixed CSS height every call, so repeated
// redraws never feed a previous backing-store size back into layout.
export function drawLineChart(canvas, { xs, series, yMin, yMax, bands = [], markers = [], xLabel = true, cssHeight = 180, unit = '' }) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const W = Math.max(1, canvas.clientWidth || canvas.parentElement?.clientWidth || 600);
  const H = cssHeight;
  canvas.style.height = H + 'px';
  canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const padL = 40, padR = 10, padT = 10, padB = xLabel ? 20 : 8;
  const pw = W - padL - padR, ph = H - padT - padB;
  const n = xs.length;
  const x0 = n ? xs[0] : 0, x1 = n > 1 ? xs[n - 1] : x0 + 1;
  const X = (t) => padL + ((t - x0) / (x1 - x0 || 1)) * pw;
  const Y = (v) => padT + (1 - (Math.min(yMax, Math.max(yMin, v)) - yMin) / (yMax - yMin)) * ph;

  for (const b of bands) {
    ctx.fillStyle = b.color;
    const ya = Y(b.to), yb = Y(b.from);
    ctx.fillRect(padL, Math.min(ya, yb), pw, Math.abs(yb - ya));
  }
  ctx.strokeStyle = '#22364F'; ctx.lineWidth = 1;
  ctx.fillStyle = '#5A6C82'; ctx.font = '10px Space Mono, monospace';
  for (let i = 0; i <= 4; i++) {
    const v = yMax - ((yMax - yMin) * i) / 4, y = Y(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.fillText((yMax - yMin < 10 ? v.toFixed(2) : Math.round(v)) + unit, 2, y + 3);
  }
  if (xLabel && n > 1) {
    ctx.fillText(fmtT(x0), padL, H - 5);
    const lbl = fmtT(x1); ctx.fillText(lbl, W - padR - ctx.measureText(lbl).width, H - 5);
  }
  for (const s of series) {
    ctx.beginPath(); let started = false;
    for (let i = 0; i < n; i++) {
      const v = s.values[i];
      if (v == null || !Number.isFinite(v)) { started = false; continue; }
      const x = X(xs[i]), y = Y(v);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 1.8;
    ctx.setLineDash(s.dash || []); ctx.stroke(); ctx.setLineDash([]);
  }
  markers.forEach((m, mi) => {
    if (m.t == null) return;
    const x = X(m.t);
    ctx.strokeStyle = m.color; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, padT + ph); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = m.color; ctx.fillText(m.label, Math.min(x + 3, W - padR - ctx.measureText(m.label).width), padT + 10 + mi * 12);
  });
}

// Background bands for a trend metric chart.
export function metricBands(key) {
  const m = TREND_METRICS[key];
  const warn = 'rgba(242,169,60,0.08)', bad = 'rgba(229,72,77,0.10)';
  if (m.higherIsBetter) return [{ from: m.degraded, to: m.watch, color: warn }, { from: m.yMin, to: m.degraded, color: bad }];
  return [{ from: m.watch, to: m.degraded, color: warn }, { from: m.degraded, to: m.yMax, color: bad }];
}

/* ------------------------------------------------------------------ */
/* HTML renderers — shared by static/index.html and the React app so   */
/* both dashboards show identical content.                             */
/* ------------------------------------------------------------------ */

export function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const pct = (v) => (v == null ? '—' : (v * 100).toFixed(0));
const num = (v, d = 1) => (v == null || !Number.isFinite(v) ? '—' : v.toFixed(d));
const LEVEL_CLASS = { nominal: 'adv-nominal', early: 'adv-early', monitor: 'adv-monitor', critical: 'adv-critical' };
const METRIC_COLORS = { fuel: '#4A9FE0', lube: '#2FBF8F', thermal: '#F2A93C', health: '#C38AF0', rul: '#3FB8C4' };

export function renderTrendTabs(activeKey) {
  return TREND_KEYS.map((k) => `<button class="tab ${k === activeKey ? 'active' : ''}" data-metric="${k}">${esc(TREND_METRICS[k].label)}</button>`).join('');
}

export function renderTrendChips(rec) {
  return TREND_KEYS.map((k) => {
    const m = TREND_METRICS[k];
    const tr = rec ? rec.trend(k) : { value: null, slope: null, band: 'nominal' };
    const slope = tr.slope == null ? '—' : `${tr.slope >= 0 ? '+' : ''}${tr.slope.toFixed(2)}${m.unit}/min`;
    return `<div class="chip ${tr.band}" data-metric="${k}"><div class="c-lbl">${esc(m.short)}</div>
      <div class="c-val">${num(tr.value, 1)}${m.unit}</div><div class="c-slope">${slope} · ${tr.band}</div></div>`;
  }).join('');
}

export function drawTrend(canvas, rec, key, cssHeight = 190) {
  const m = TREND_METRICS[key];
  const S = rec ? rec.samples : [];
  drawLineChart(canvas, {
    xs: S.map((s) => s.t),
    series: [{ values: S.map((s) => s.idx[key]), color: METRIC_COLORS[key], width: 2 }],
    yMin: m.yMin, yMax: m.yMax, bands: metricBands(key), cssHeight,
    unit: m.unit === '%' ? '' : m.unit === '°C' ? '°' : '',
    markers: rec && rec.detection ? [{ t: rec.detection.t, color: '#E5484D', label: 'fault confirmed' }] : [],
  });
}

export function renderAdvisory(rec, latestPred) {
  const adv = rec && rec.advisory;
  if (!adv) {
    const live = latestPred && latestPred.predicted_fault !== 'healthy'
      ? `<div class="adv-meta">Model currently suggests ${esc(FAULT_LABELS[latestPred.predicted_fault] || latestPred.predicted_fault)} — awaiting confirmation (${CONFIRM_N} consecutive samples).</div>` : '';
    return `<span class="adv-level adv-nominal">No action</span>
      <p class="adv-system">Engine nominal vs digital twin</p>${live}
      <ul class="adv-list"><li>No maintenance action indicated by the model this run.</li><li>Continue routine scheduled checks.</li></ul>`;
  }
  const A = ADVISORIES[adv.fault];
  const items = A ? A[adv.level] || [] : [`Fault class ${adv.fault}: no advisory text defined.`];
  const ev = adv.evidence && adv.evidence.length
    ? `<div class="adv-evidence">Model evidence (top positive SHAP, engine sensors only): ${adv.evidence.map((e) => esc(e.label)).join(', ')}</div>` : '';
  return `<span class="adv-level ${LEVEL_CLASS[adv.level]}">${esc(LEVEL_TEXT[adv.level])}</span>
    <p class="adv-system">${esc(A ? A.system : adv.fault)} — ${esc(FAULT_LABELS[adv.fault] || adv.fault)}</p>
    <div class="adv-meta">Latched at flight time ${fmtT(adv.since)}</div>
    <ul class="adv-list">${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>${ev}`;
}

function outcomeTag(level) {
  const color = { nominal: 'var(--teal)', early: 'var(--blue)', monitor: 'var(--amber)', critical: 'var(--red)' }[level] || 'var(--text-dim)';
  return `<span class="tag" style="color:${color};border:1px solid ${color}">${esc(LEVEL_TEXT[level] || level)}</span>`;
}

function latencyText(s) {
  if (s.detection_latency_s == null) {
    if (!s.truth_onset_observed) return 'onset before recording';
    if (s.true_onset_t == null) return s.complete ? 'no fault in truth' : 'no fault (yet)';
    return s.complete ? 'not detected' : 'not detected (yet)';
  }
  const v = s.detection_latency_s;
  return `${v >= 0 ? '+' : ''}${v.toFixed(0)} s`;
}

export function renderReportRows(recs, current) {
  const all = [...recs, current].filter((r) => r && r.samples.length);
  if (!all.length) return '<tr><td colspan="8" style="color:var(--text-faint);font-family:var(--sans)">No telemetry yet.</td></tr>';
  return all.slice().reverse().map((r) => {
    const s = r.summary();
    const live = r === current ? '<span class="live-dot" title="recording"></span>' : '';
    return `<tr><td>${s.run_id}</td><td class="name">${live}${esc(s.run_label)}${r === current ? ' <span style="color:var(--text-faint);font-weight:400">(recording)</span>' : ''}</td>
      <td>${fmtT(s.t_start)}–${fmtT(s.t_end)}</td>
      <td>${s.detected_fault ? esc(FAULT_LABELS[s.detected_fault] || s.detected_fault) + ' @ ' + fmtT(s.detection_t) : '—'}</td>
      <td>${latencyText(s)}</td><td>${pct(s.peak_severity)}/100</td><td>${outcomeTag(s.outcome_level)}</td>
      <td class="rep-actions"><button data-act="view" data-id="${s.run_id}">View</button><button data-act="csv" data-id="${s.run_id}">CSV</button><button data-act="json" data-id="${s.run_id}">JSON</button></td></tr>`;
  }).join('');
}

export function renderReport(rec) {
  const s = rec.summary();
  const kv = (k, v, sub = '') => `<div class="kv"><div class="k">${k}</div><div class="v">${v}</div>${sub ? `<div class="s">${sub}</div>` : ''}</div>`;
  const shares = Object.entries(s.class_share).sort((a, b) => b[1] - a[1])
    .map(([c, v]) => `${esc(FAULT_LABELS[c] || c)} ${(v * 100).toFixed(0)}%`).join(' · ');
  const idxRows = TREND_KEYS.filter((k) => s.index_stats[k]).map((k) => {
    const m = TREND_METRICS[k], st = s.index_stats[k];
    return `<tr><td class="name">${esc(m.label)}</td><td>${num(st.start)}${m.unit}</td><td>${num(st.end)}${m.unit}</td><td>${num(st.worst)}${m.unit}</td><td>${esc(st.endBand)}</td></tr>`;
  }).join('');
  const events = s.events.length
    ? s.events.map((e) => `<div><span class="t">${fmtT(e.t)}</span><span>${esc(e.msg)}</span></div>`).join('')
    : '<div><span class="t">—</span><span>No alerts, advisories or operator actions this run.</span></div>';
  const truth = !s.truth_onset_observed ? 'fault already present when recording started' : s.true_onset_t != null
    ? `${esc(s.truth_faults.map((f) => FAULT_LABELS[f] || f).join(', '))} onset @ ${fmtT(s.true_onset_t)}` : 'No fault in ground truth';
  return `
  <div class="modal-head">
    <div><h2>Mission health report #${s.run_id} — ${esc(s.run_label)}</h2>
      <p>Flight time ${fmtT(s.t_start)}–${fmtT(s.t_end)} · ${s.n_samples} samples · recorded ${esc(new Date(s.started_at).toLocaleString())}${s.complete ? '' : ' · still recording'}</p>
      <p>Simulated / replayed telemetry. Model outputs are decision support, not certified diagnostics.</p></div>
    <div class="rep-actions no-print"><button data-act="csv" data-id="${s.run_id}">Export CSV</button><button data-act="json" data-id="${s.run_id}">Export JSON</button><button data-act="print">Print / PDF</button><button data-act="close">Close</button></div>
  </div>
  <div class="kv-grid">
    ${kv('Outcome', outcomeTag(s.outcome_level))}
    ${kv('Model detection', s.detected_fault ? esc(FAULT_LABELS[s.detected_fault] || s.detected_fault) : 'None', s.detection_t != null ? 'confirmed @ ' + fmtT(s.detection_t) : '')}
    ${kv('Ground truth', s.true_onset_t != null ? fmtT(s.true_onset_t) : '—', truth)}
    ${kv('Detection latency', latencyText(s), 'confirmed detection − true onset')}
    ${kv('Peak severity', pct(s.peak_severity) + '/100', '@ ' + fmtT(s.peak_severity_t))}
    ${kv('Min RUL fraction', s.min_rul == null ? '—' : (s.min_rul * 100).toFixed(0) + '%', 'model estimate @ ' + fmtT(s.min_rul_t))}
    ${kv('First Monitor / Critical', `${fmtT(s.first_monitor_t)} / ${fmtT(s.first_critical_t)}`, `severity ≥ ${SEV_MONITOR} / ≥ ${SEV_CRITICAL}`)}
    ${kv('Agreement with truth', s.truth_agreement == null ? '—' : (s.truth_agreement * 100).toFixed(1) + '%', 'per-sample predicted vs true class')}
  </div>
  <p class="rep-h">Severity over time (0–100)</p>
  <canvas id="repSevCanvas"></canvas>
  <div class="rep-legend"><span><i style="background:#E5484D"></i>Model severity</span><span><i style="background:#8FA0B5"></i>Ground-truth severity</span><span><i style="background:#F2A93C"></i>Monitor / critical thresholds</span></div>
  <p class="rep-h">Efficiency indices over time</p>
  <canvas id="repIdxCanvas"></canvas>
  <div class="rep-legend"><span><i style="background:${METRIC_COLORS.fuel}"></i>Fuel efficiency %</span><span><i style="background:${METRIC_COLORS.lube}"></i>Lubrication %</span><span><i style="background:${METRIC_COLORS.health}"></i>Model health</span></div>
  <p class="rep-h">Efficiency summary</p>
  <div class="overflow-x"><table class="rep-table"><thead><tr><th>Index</th><th>Start</th><th>End</th><th>Worst</th><th>End band</th></tr></thead><tbody>${idxRows}</tbody></table></div>
  <p class="rep-h">Model classification share</p>
  <div style="font-family:var(--mono);font-size:12px">${shares}</div>
  <p class="rep-h">Maintenance advisory at end of run</p>
  <div>${renderAdvisory(rec, null)}</div>
  <p class="rep-h">Event timeline</p>
  <div class="timeline">${events}</div>`;
}

export function drawReportCharts(root, rec) {
  const S = rec.samples, xs = S.map((s) => s.t);
  const markers = [];
  const s = rec.summary();
  if (s.true_onset_t != null) markers.push({ t: s.true_onset_t, color: '#8FA0B5', label: 'true onset' });
  if (s.detection_t != null) markers.push({ t: s.detection_t, color: '#E5484D', label: 'detected' });
  drawLineChart(root.querySelector('#repSevCanvas'), {
    xs, yMin: 0, yMax: 80, cssHeight: 170, markers,
    series: [
      { values: S.map((x) => (x.true_severity == null ? null : x.true_severity * 100)), color: '#8FA0B5', dash: [4, 3], width: 1.5 },
      { values: S.map((x) => (x.severity == null ? null : x.severity * 100)), color: '#E5484D', width: 2 },
      { values: S.map(() => SEV_MONITOR * 100), color: 'rgba(242,169,60,0.6)', width: 1, dash: [2, 4] },
      { values: S.map(() => SEV_CRITICAL * 100), color: 'rgba(229,72,77,0.6)', width: 1, dash: [2, 4] },
    ],
  });
  drawLineChart(root.querySelector('#repIdxCanvas'), {
    xs, yMin: 50, yMax: 110, cssHeight: 170, markers,
    series: ['fuel', 'lube', 'health'].map((k) => ({ values: S.map((x) => x.idx[k]), color: METRIC_COLORS[k], width: 1.8 })),
  });
}
