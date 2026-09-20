import { useState, useEffect, useRef, useCallback } from 'react';
import '../index.css';

import Header from '../components/Header';
import SensorList from '../components/SensorList';
import FlightCanvas from '../components/FlightCanvas';
import DiagnosisPanel, { SEV_MONITOR, SEV_CRITICAL, FAULT_LABELS } from '../components/DiagnosisPanel';
import SafetyBanner from '../components/SafetyBanner';
import LogList from '../components/LogList';
import FaultControls from '../components/FaultControls';
import SectionF, { useMissionReports } from '../components/SectionF';
import { ADVISORIES, LEVEL_TEXT } from '../lib/missionAnalytics';

import ScenarioSelector from '../components/ScenarioSelector';
import BatteryPanel from '../components/BatteryPanel';

// The backend WebSocket URL — same host in production, explicit for dev
const WS_URL = `ws://${window.location.hostname}:8000/ws`;

function formatTime(tsec) {
  const mm = String(Math.floor(tsec / 60)).padStart(2, '0');
  const ss = String(Math.floor(tsec % 60)).padStart(2, '0');
  return `${mm}:${ss}`;
}

export default function Dashboard() {
  const [connected, setConnected] = useState(false);
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [activeFault, setActiveFault] = useState('healthy');
  const [activeScenario, setActiveScenario] = useState('dataset');
  const [responseState, setResponseState] = useState('none'); // 'none' | 'confirmed' | 'dismissed'

  const wsRef = useRef(null);
  const reports = useMissionReports();
  const { onData: recordSample, newRun, logEvent } = reports;

  const addLog = useCallback((msg, cls, tsec = 0) => {
    const time = formatTime(tsec);
    setLogs(prev => {
      const next = [...prev, { time, msg, cls }];
      if (next.length > 40) next.shift();
      return next;
    });
  }, []);

  // WebSocket connection with auto-reconnect
  useEffect(() => {
    let ws;
    let reconnectTimer;
    let isMounted = true;

    function connect() {
      ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!isMounted) return;
        setConnected(true);
        addLog('Connected to model server', 'ok', 0);
      };

      ws.onclose = () => {
        if (!isMounted) return;
        setConnected(false);
        reconnectTimer = setTimeout(connect, 1500);
      };

      ws.onerror = () => {
        ws.close();
      };

      ws.onmessage = (ev) => {
        if (!isMounted) return;
        const d = JSON.parse(ev.data);
        setData(d);
        const adv = recordSample(d);
        if (adv) {
          addLog(`Maintenance advisory: ${ADVISORIES[adv.fault]?.system || adv.fault} — ${LEVEL_TEXT[adv.level]}`,
            adv.level === 'critical' ? 'crit' : 'warn', d.t_sec);
        }
      };
    }

    connect();

    return () => {
      isMounted = false;
      clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [addLog, recordSample]);

  // ---- Actions sent to backend ----
  const send = useCallback((payload) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
    }
  }, []);

  const handleInjectFault = useCallback((fault) => {
    setActiveFault(fault);
    setResponseState('none');
    send({ action: 'inject_fault', fault });
    newRun(fault);
    addLog(`Now streaming: ${FAULT_LABELS[fault] || fault} run`, 'warn', 0);
  }, [send, addLog, newRun]);

  const handleClearFault = useCallback(() => {
    setActiveFault('healthy');
    setResponseState('none');
    send({ action: 'clear_fault' });
    newRun('healthy');
    addLog('Now streaming: healthy run', 'ok', 0);
  }, [send, addLog, newRun]);

  const handleSelectScenario = useCallback((scenarioId) => {
    setActiveScenario(scenarioId);
    setActiveFault('healthy');
    setResponseState('none');
    send({ action: 'select_scenario', scenario: scenarioId });
    newRun(scenarioId);
    addLog(`Switched environmental scenario: ${scenarioId}`, 'ok', 0);
  }, [send, addLog, newRun]);

  const handleConfirm = useCallback(() => {
    setResponseState('confirmed');
    send({ action: 'confirm_action' });
    logEvent('Operator confirmed recommended action');
    addLog('Operator confirmed emergency action', 'crit', data?.t_sec || 0);
  }, [send, addLog, data, logEvent]);

  const handleDismiss = useCallback(() => {
    setResponseState('dismissed');
    send({ action: 'dismiss_action' });
    logEvent('Operator dismissed recommendation');
    addLog('Operator dismissed safety recommendation', '', data?.t_sec || 0);
  }, [send, addLog, data, logEvent]);

  // Should the safety banner show?
  const prediction = data?.prediction;
  const severity = prediction?.severity || 0;
  const isFault = prediction?.predicted_fault !== 'healthy';
  const showBanner =
    responseState === 'none' &&
    isFault &&
    severity >= SEV_MONITOR;
  const isCritical = severity >= SEV_CRITICAL && isFault;

  return (
    <div className="app-container">
      <Header
        connected={connected}
        tSec={data?.t_sec}
        altitude={data?.altitude}
      />

      <div className="glass-panel" style={{ margin: '0 var(--space-lg) var(--space-md) var(--space-lg)' }}>
        <p className="panel-title">Environmental Scenarios (Digital Twin Simulation)</p>
        <ScenarioSelector
          activeScenario={activeScenario}
          onSelectScenario={handleSelectScenario}
        />
      </div>

      <div className="main-grid">
        {/* Left column — sensor telemetry + battery */}
        <div className="left-column" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-md)' }}>
          <div className="glass-panel">
            <p className="panel-title">Live telemetry — actual vs expected</p>
            <SensorList sensors={data?.sensors} expected={data?.expected} />
          </div>

          <div className="glass-panel">
            <BatteryPanel battery={data?.battery} />
          </div>
        </div>

        {/* Center column — flight profile + logs */}
        <div className="center-column">
          <div className="glass-panel">
            <p className="panel-title">Altitude profile</p>
            <FlightCanvas
              altitude={data?.altitude}
              glide={data?.response_confirmed}
              tSec={data?.t_sec}
            />
          </div>
          <div className="glass-panel">
            <p className="panel-title">Ground control log</p>
            <LogList logs={logs} />
          </div>
        </div>

        {/* Right column — controls + diagnosis */}
        <div className="right-column">
          <div className="glass-panel">
            <p className="panel-title">Stream real dataset run</p>
            <FaultControls
              activeFault={activeFault}
              onInjectFault={handleInjectFault}
              onClearFault={handleClearFault}
            />
          </div>
          <div className="glass-panel">
            <p className="panel-title">Model diagnosis (real inference)</p>
            <DiagnosisPanel
              prediction={prediction}
              trueFault={data?.true_fault}
            />
          </div>
        </div>
      </div>

      {/* Section F — efficiency trends, maintenance advisory, mission reports */}
      <SectionF reports={reports} />

      {/* Safety banner — overlays bottom */}
      {showBanner && (
        <SafetyBanner
          severity={severity}
          isCritical={isCritical}
          onConfirm={handleConfirm}
          onDismiss={handleDismiss}
        />
      )}
    </div>
  );
}
