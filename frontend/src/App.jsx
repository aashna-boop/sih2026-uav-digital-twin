import { useState, useEffect, useRef, useCallback } from 'react';
import './index.css';

import Header from './components/Header';
import SensorList from './components/SensorList';
import FlightCanvas from './components/FlightCanvas';
import DiagnosisPanel, { SEV_MONITOR, SEV_CRITICAL, FAULT_LABELS } from './components/DiagnosisPanel';
import SafetyBanner from './components/SafetyBanner';
import LogList from './components/LogList';
import FaultControls from './components/FaultControls';

// The backend WebSocket URL — same host in production, explicit for dev
const WS_URL = `ws://${window.location.hostname}:8000/ws`;

function formatTime(tsec) {
  const mm = String(Math.floor(tsec / 60)).padStart(2, '0');
  const ss = String(Math.floor(tsec % 60)).padStart(2, '0');
  return `${mm}:${ss}`;
}

export default function App() {
  const [connected, setConnected] = useState(false);
  const [data, setData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [activeFault, setActiveFault] = useState('healthy');
  const [responseState, setResponseState] = useState('none'); // 'none' | 'confirmed' | 'dismissed'

  const wsRef = useRef(null);

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
      };
    }

    connect();

    return () => {
      isMounted = false;
      clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [addLog]);

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
    addLog(`Now streaming: ${FAULT_LABELS[fault] || fault} run`, 'warn', 0);
  }, [send, addLog]);

  const handleClearFault = useCallback(() => {
    setActiveFault('healthy');
    setResponseState('none');
    send({ action: 'clear_fault' });
    addLog('Now streaming: healthy run', 'ok', 0);
  }, [send, addLog]);

  const handleConfirm = useCallback(() => {
    setResponseState('confirmed');
    send({ action: 'confirm_action' });
    addLog('Operator confirmed emergency action', 'crit', data?.t_sec || 0);
  }, [send, addLog, data]);

  const handleDismiss = useCallback(() => {
    setResponseState('dismissed');
    send({ action: 'dismiss_action' });
    addLog('Operator dismissed safety recommendation', '', data?.t_sec || 0);
  }, [send, addLog, data]);

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

      <div className="main-grid">
        {/* Left column — sensor telemetry */}
        <div className="glass-panel">
          <p className="panel-title">Live telemetry — actual vs expected</p>
          <SensorList sensors={data?.sensors} expected={data?.expected} />
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
