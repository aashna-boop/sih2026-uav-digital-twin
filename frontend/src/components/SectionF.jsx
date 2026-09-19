/*
 * Section F — Engine efficiency trends, Maintenance advisory,
 * Mission-wise health reports (with post-flight analysis + export).
 *
 * All logic and markup come from ../lib/missionAnalytics.js, the same code
 * static/index.html inlines, so both dashboards show identical content.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import * as MA from '../lib/missionAnalytics';
import './sectionF.css';

const MAX_REPORTS = 12;

/**
 * Keeps the mission recorders outside React state (they are mutated on
 * every sample) and bumps a version counter so the UI re-renders.
 * Returned callbacks are stable, so they are safe to call from the
 * WebSocket onmessage closure.
 */
export function useMissionReports() {
  const store = useRef(null);
  if (!store.current) store.current = { current: new MA.MissionRecorder(null), done: [], lastPred: null, lastAdvisory: '' };
  const [version, setVersion] = useState(0);
  const bump = useCallback(() => setVersion((v) => v + 1), []);

  const archive = useCallback(() => {
    const s = store.current;
    s.current.finish();
    if (s.current.samples.length >= 5) {
      s.done.push(s.current);
      if (s.done.length > MAX_REPORTS) s.done.shift();
    }
  }, []);

  const newRun = useCallback((key) => {
    archive();
    store.current.current = new MA.MissionRecorder(key);
    store.current.lastAdvisory = '';
    bump();
  }, [archive, bump]);

  // Returns the advisory if it changed with this sample (for the GCS log).
  const onData = useCallback((d) => {
    const s = store.current;
    const S = s.current.samples;
    if (S.length && d.t_sec < S[S.length - 1].t - 5) {
      // Replay looped back to t=0: close this report, keep recording the same run.
      const k = s.current.runKey;
      archive();
      s.current = new MA.MissionRecorder(k);
      s.lastAdvisory = '';
    }
    s.current.add(d);
    s.lastPred = d.prediction;
    const adv = s.current.advisory;
    const sig = adv ? adv.fault + adv.level : '';
    const changed = sig !== s.lastAdvisory;
    s.lastAdvisory = sig;
    bump();
    return changed ? adv : null;
  }, [archive, bump]);

  const logEvent = useCallback((msg) => {
    const rec = store.current.current;
    const S = rec.samples;
    rec.logEvent(S.length ? S[S.length - 1].t : 0, 'operator', msg);
    bump();
  }, [bump]);

  return { store: store.current, version, onData, newRun, logEvent };
}

function download(rec, kind) {
  const base = `mission_report_${rec.id}_${rec.summary().run_key}`;
  if (kind === 'csv') MA.downloadText(`${base}.csv`, rec.toCSV(), 'text/csv');
  else MA.downloadText(`${base}.json`, rec.toJSON(), 'application/json');
}

export function EfficiencyTrends({ store, version }) {
  const [metric, setMetric] = useState('fuel');
  const canvasRef = useRef(null);
  const rec = store.current;

  useLayoutEffect(() => {
    MA.drawTrend(canvasRef.current, rec, metric);
  }, [rec, metric, version]);

  useEffect(() => {
    const onResize = () => MA.drawTrend(canvasRef.current, store.current, metric);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [store, metric]);

  const pick = (e) => {
    const t = e.target.closest('[data-metric]');
    if (t) setMetric(t.dataset.metric);
  };

  return (
    <div className="glass-panel">
      <p className="panel-title">Engine efficiency trends</p>
      <div className="tabs" onClick={pick} dangerouslySetInnerHTML={{ __html: MA.renderTrendTabs(metric) }} />
      <canvas ref={canvasRef} />
      <div className="trend-help">{MA.TREND_METRICS[metric].help}</div>
      <div className="chips" onClick={pick} dangerouslySetInnerHTML={{ __html: MA.renderTrendChips(rec) }} />
      <div className="footnote">
        Indices compare measured values with the digital twin&apos;s healthy-engine expectation for the same flight
        conditions. Smoothed (EMA); slope is per minute of flight time over the last 20 samples. Coloured bands are
        dashboard display bands set from this dataset, not manufacturer limits.
      </div>
    </div>
  );
}

export function MaintenanceAdvisory({ store }) {
  return (
    <div className="glass-panel">
      <p className="panel-title">Maintenance advisory</p>
      <div dangerouslySetInnerHTML={{ __html: MA.renderAdvisory(store.current, store.lastPred) }} />
      <div className="footnote">
        Advisory latches once the model confirms a fault ({MA.CONFIRM_N} consecutive predictions) and only escalates
        within a run. Decision support only — follow the engine maintenance manual.
      </div>
    </div>
  );
}

export function MissionReports({ store, version }) {
  const [openId, setOpenId] = useState(null);
  const cardRef = useRef(null);
  const all = [...store.done, store.current];
  const openRec = openId == null ? null : all.find((r) => r.id === openId) || null;

  useLayoutEffect(() => {
    if (openRec && cardRef.current) MA.drawReportCharts(cardRef.current, openRec);
  }, [openRec, version]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') setOpenId(null); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const act = (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const rec = all.find((r) => r.id === +b.dataset.id);
    switch (b.dataset.act) {
      case 'view': setOpenId(+b.dataset.id); break;
      case 'csv': if (rec) download(rec, 'csv'); break;
      case 'json': if (rec) download(rec, 'json'); break;
      case 'print': window.print(); break;
      case 'close': setOpenId(null); break;
      default:
    }
  };

  return (
    <>
      <div className="glass-panel reports-panel">
        <p className="panel-title">Mission-wise health reports &amp; post-flight analysis</p>
        <div className="overflow-x">
          <table className="rep-table">
            <thead>
              <tr><th>#</th><th>Run</th><th>Flight time</th><th>Model detection</th><th>Latency vs truth</th><th>Peak severity</th><th>Outcome</th><th /></tr>
            </thead>
            <tbody onClick={act} dangerouslySetInnerHTML={{ __html: MA.renderReportRows(store.done, store.current) }} />
          </table>
        </div>
        <div className="footnote">
          A new report starts whenever a run is switched or the replay loops back to t=0. Reports live in this browser
          tab only; export CSV/JSON to keep them.
        </div>
      </div>
      {openRec && (
        <div className="modal show" onClick={(e) => { if (e.target === e.currentTarget) setOpenId(null); }}>
          <div className="modal-card" ref={cardRef} onClick={act} dangerouslySetInnerHTML={{ __html: MA.renderReport(openRec) }} />
        </div>
      )}
    </>
  );
}

export default function SectionF({ reports }) {
  const { store, version } = reports;
  return (
    <div className="sf">
      <div className="row2">
        <EfficiencyTrends store={store} version={version} />
        <MaintenanceAdvisory store={store} version={version} />
      </div>
      <MissionReports store={store} version={version} />
    </div>
  );
}
