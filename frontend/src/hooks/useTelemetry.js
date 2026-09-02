import { useCallback, useEffect, useRef, useState } from "react";

import {
  API,
  MAX_PATH_POINTS,
  MAX_POINTS,
  RECONNECT_DELAY_MS,
  WS_TELEMETRY,
  humanize,
  sensorMeta,
} from "../lib/telemetry";

// Owns the live link to the mission runtime: the telemetry socket, the rolling
// per-sensor history used by the charts, the ground-control audit log, and the
// scenario / operator-response commands. Every command is a REST call - the
// socket is read-only, so the dashboard can never issue an aircraft command.
export function useTelemetry() {
  const [frame, setFrame] = useState(null);
  const [connected, setConnected] = useState(false);
  const [history, setHistory] = useState({});
  const [pathHistory, setPathHistory] = useState([]);
  const [events, setEvents] = useState([]);
  const [scenario, setScenario] = useState({
    fault: "HEALTHY",
    degradation_rate: "medium",
    enabled: false,
  });

  const reconnectRef = useRef(null);
  const previousRef = useRef({});

  const addEvent = useCallback((message, tone = "neutral") => {
    const time = new Date().toLocaleTimeString([], {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    setEvents((current) => [{ time, message, tone }, ...current].slice(0, 28));
  }, []);

  useEffect(() => {
    fetch(`${API}/api/scenarios`)
      .then((response) => response.json())
      .then((data) => setScenario(data.current))
      .catch(() => {});

    let socket;
    let active = true;

    const connect = () => {
      socket = new WebSocket(WS_TELEMETRY);
      socket.onopen = () => {
        setConnected(true);
        addEvent("Telemetry WebSocket connected", "good");
      };
      socket.onmessage = (event) => {
        const next = JSON.parse(event.data);
        setFrame(next);
        setHistory((current) => {
          const updated = { ...current };
          Object.keys(sensorMeta).forEach((sensor) => {
            const list = updated[sensor] ? [...updated[sensor]] : [];
            list.push({
              actual: next.engine.actual[sensor],
              expected: next.engine.expected[sensor],
            });
            updated[sensor] = list.slice(-MAX_POINTS);
          });
          return updated;
        });
        if (
          Number.isFinite(next.flight.latitude_deg) &&
          Number.isFinite(next.flight.longitude_deg)
        ) {
          setPathHistory((current) =>
            [
              ...current,
              { lat: next.flight.latitude_deg, lon: next.flight.longitude_deg },
            ].slice(-MAX_PATH_POINTS),
          );
        }

        const previous = previousRef.current;
        const source = next.flight.telemetry_source;
        const fault = next.analytics.fault;
        const recommendation = next.analytics.recommendation;
        if (previous.source && previous.source !== source) {
          addEvent(
            `Telemetry source changed to ${humanize(source)}`,
            source === "REPLAY" ? "warn" : "good",
          );
        }
        if (previous.fault && previous.fault !== fault) {
          addEvent(
            `Diagnosis changed to ${humanize(fault)}`,
            fault === "HEALTHY" ? "good" : "warn",
          );
        }
        if (previous.recommendation && previous.recommendation !== recommendation) {
          addEvent(
            `Mission advisory changed to ${humanize(recommendation)}`,
            recommendation === "ABORT_LAND" ? "danger" : "warn",
          );
        }
        if (previous.threshold === "NORMAL" && next.traditional_status === "ALARM") {
          addEvent("Legacy engine threshold entered ALARM", "danger");
        }
        previousRef.current = {
          source,
          fault,
          recommendation,
          threshold: next.traditional_status,
        };
      };
      socket.onclose = () => {
        setConnected(false);
        addEvent("Telemetry WebSocket disconnected", "danger");
        if (active) reconnectRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
      };
    };

    connect();
    return () => {
      active = false;
      clearTimeout(reconnectRef.current);
      socket?.close();
    };
  }, [addEvent]);

  const applyScenario = useCallback(
    async (patch) => {
      const eventFault = patch.fault || scenario.fault;
      setScenario((current) => ({ ...current, ...patch }));
      await fetch(`${API}/api/scenario`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (patch.fault) {
        addEvent(
          `Scenario selected: ${humanize(patch.fault)}`,
          patch.fault === "HEALTHY" ? "good" : "warn",
        );
      }
      if (Object.hasOwn(patch, "enabled")) {
        addEvent(
          patch.enabled
            ? `Fault injection enabled: ${humanize(eventFault)}`
            : "Fault injection stopped",
          patch.enabled ? "warn" : "good",
        );
      }
    },
    [addEvent, scenario.fault],
  );

  const resetMission = useCallback(async () => {
    setHistory({});
    setPathHistory([]);
    await fetch(`${API}/api/scenario`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reset: true }),
    });
    addEvent("Mission runtime reset", "good");
  }, [addEvent]);

  const respondToAdvisory = useCallback(
    async (response) => {
      await fetch(`${API}/api/operator-response`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ response }),
      });
      addEvent(
        `Operator ${response.toLowerCase()} the current advisory`,
        response === "CONFIRMED" ? "good" : "warn",
      );
    },
    [addEvent],
  );

  return {
    frame,
    connected,
    history,
    pathHistory,
    events,
    scenario,
    applyScenario,
    resetMission,
    respondToAdvisory,
  };
}
