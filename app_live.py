"""
app_live.py

LIVE backend — connects to a running ArduPilot SITL instance over MAVLink,
drives your team's own physics-based engine model (live_engine.py) with real
flight telemetry, and runs every reading through the trained models
(fault classifier, severity regressor, RUL regressor).

This is a pure live pipeline: there is no dataset-replay path here. The
dashboard will not display anything until it has received real telemetry
from a connected SITL instance (see TELEMETRY_READY_TYPES below).

BEFORE RUNNING THIS:
1. Make sure no old SITL/uvicorn processes are still holding ports 14551 or
   8000 from a previous run (see PROJECT_DOCUMENTATION / chat notes for the
   exact ps/kill commands) — stale listeners on 14551 are the #1 cause of
   "connected but no telemetry".

2. Start ArduPilot SITL with an extra MAVLink output port dedicated to this
   script, so it doesn't fight with MAVProxy's own console/map connection:

       cd ~/ardupilot/ArduPlane
       ../Tools/autotest/sim_vehicle.py --console --map --out=udp:127.0.0.1:14551

3. Run this from the SAME machine/WSL environment as SITL. For a live demo,
   run WITHOUT --reload (auto-reload on a synced/OneDrive folder can restart
   the process unexpectedly and drop the live MAVLink connection):

       PYTHONUNBUFFERED=1 uvicorn app_live:app --host 127.0.0.1 --port 8000

   (--reload is fine for day-to-day dev work, just not for the demo itself.)

4. Open http://localhost:8000 — the page will show a "Waiting for ArduPilot
   SITL..." overlay until real telemetry starts arriving, then reveal the
   dashboard automatically.
"""
import asyncio
import os
import time
import math
from collections import deque

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pymavlink import mavutil

from live_engine import LiveEngine
from digital_twin import SENSORS

# "udpin:" is explicit about direction (we BIND and LISTEN on this port —
# SITL's --out=udp:127.0.0.1:14551 sends TO us). The old bare "udp:" form
# relied on pymavlink's default input=True, which works but is easy to get
# wrong if this code is ever copied elsewhere. Override via env var for a
# different machine/port without editing code.
MAVLINK_CONNECTION_STRING = os.environ.get("MAVLINK_CONNECTION_STRING", "udpin:127.0.0.1:14551")

FEATURES = [f"{s}_resid_smooth" for s in SENSORS]  # must match train_model.py's FEATURES exactly
SMOOTH_WINDOW_SEC = 2.5  # matches train_model.py's SMOOTH_WINDOW_SAMPLES (10) at the dataset's dt=0.25s
CLASS_LABELS = ["cooling_failure", "healthy", "oil_pressure_drop", "valve_wear"]  # alphabetical, matches training

# Rolling buffer of (t_sec, resid) per sensor, so the live feature stream
# gets the same short-window smoothing the offline model was trained on
# (see train_model.py's module docstring for why: a real health monitor
# never diagnoses off one noisy instantaneous reading). Deques, not a fixed-
# size window, because the live loop's tick rate isn't guaranteed constant.
resid_history = {s: deque() for s in SENSORS}

# Rolling per-session trend of the model's predicted severity / RUL, sent with
# every WebSocket payload as `history`. Capped so a long session can't grow
# memory without bound; reset whenever the operator injects/clears a fault.
TREND_HISTORY_MAX = 300
trend_history = deque(maxlen=TREND_HISTORY_MAX)


def smoothed_resid(t_sec, raw_resid):
    """Update the rolling buffers with this tick's raw residuals and return
    the trailing SMOOTH_WINDOW_SEC-second mean for each sensor."""
    out = {}
    for s in SENSORS:
        buf = resid_history[s]
        buf.append((t_sec, raw_resid[s]))
        while buf and t_sec - buf[0][0] > SMOOTH_WINDOW_SEC:
            buf.popleft()
        out[f"{s}_resid_smooth"] = sum(v for _, v in buf) / len(buf)
    return out

# A real fault response commands this many meters of REAL altitude loss via a
# genuine GUIDED-mode MAVLink command to the running SITL plane.
FAULT_RESPONSE_ALT_DROP_M = 40.0
FAULT_RESPONSE_MIN_ALT_M = 20.0  # never command below this relative altitude

# We only start broadcasting once we've received at least one real message of
# each of these types — this is what makes the dashboard "no fallback": it
# will not show anything until it has genuine data from a connected SITL.
TELEMETRY_READY_TYPES = {"ATTITUDE", "VFR_HUD", "GLOBAL_POSITION_INT", "SYS_STATUS"}

app = FastAPI()

clf = joblib.load("model_fault_classifier.joblib")
reg_sev = joblib.load("model_severity_regressor.joblib")
reg_rul = joblib.load("model_rul_regressor.joblib")

connected = []
engine = LiveEngine()
flight_state = {
    "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
    "airspeed": 0.0, "groundspeed": 0.0,
    "relative_alt": 0.0, "alt_msl": 0.0,
    "lat": None, "lon": None,
    "battery_pct": None, "battery_v": None, "battery_a": None,
}
seen_types = set()
mav_connection = None
start_time = None
response_confirmed = False
response_start_t = None
descend_command_sent = False


def telemetry_ready():
    return TELEMETRY_READY_TYPES.issubset(seen_types)


def predict_row(feature_dict):
    X = pd.DataFrame([feature_dict])[FEATURES].astype(float)
    proba = clf.predict_proba(X)[0]
    pred_idx = int(np.argmax(proba))
    pred_class = CLASS_LABELS[pred_idx]
    severity = float(np.clip(reg_sev.predict(X)[0], 0, 1))
    rul = float(np.clip(reg_rul.predict(X)[0], 0, 1))

    dmat = xgb.DMatrix(X, feature_names=FEATURES)
    contribs = np.array(clf.get_booster().predict(dmat, pred_contribs=True))
    class_shap = contribs[0, pred_idx, :-1] if contribs.ndim == 3 else contribs[0, :-1]
    contributions = sorted(zip(FEATURES, class_shap), key=lambda x: -abs(x[1]))[:5]

    return {
        "predicted_fault": pred_class,
        "confidence": float(proba[pred_idx]),
        "probabilities": {CLASS_LABELS[i]: float(p) for i, p in enumerate(proba)},
        "severity": severity,
        "rul": rul,
        "shap": [{"feature": f, "value": float(v)} for f, v in contributions],
    }


def connect_mavlink(timeout=15):
    global mav_connection
    print(f"[app_live] Connecting to SITL at {MAVLINK_CONNECTION_STRING} ...", flush=True)
    mav_connection = mavutil.mavlink_connection(MAVLINK_CONNECTION_STRING)
    print("[app_live] Waiting for heartbeat (make sure SITL is running)...", flush=True)
    mav_connection.wait_heartbeat(timeout=timeout)
    print(f"[app_live] Heartbeat received from system {mav_connection.target_system}. Connected.", flush=True)


def drain_mavlink():
    """Non-blocking: pull in every pending message and update the latest flight_state."""
    if mav_connection is None:
        return
    while True:
        msg = mav_connection.recv_match(
            type=["ATTITUDE", "VFR_HUD", "GLOBAL_POSITION_INT", "SYS_STATUS"],
            blocking=False,
        )
        if msg is None:
            break
        mtype = msg.get_type()
        seen_types.add(mtype)
        if mtype == "ATTITUDE":
            flight_state["roll"] = math.degrees(msg.roll)
            flight_state["pitch"] = math.degrees(msg.pitch)
            flight_state["yaw"] = math.degrees(msg.yaw) % 360
        elif mtype == "VFR_HUD":
            flight_state["airspeed"] = msg.airspeed
            flight_state["groundspeed"] = msg.groundspeed
            flight_state["alt_msl"] = msg.alt
        elif mtype == "GLOBAL_POSITION_INT":
            flight_state["lat"] = msg.lat  # degE7, int — same units SET_POSITION_TARGET_GLOBAL_INT expects
            flight_state["lon"] = msg.lon  # degE7, int
            flight_state["relative_alt"] = msg.relative_alt / 1000.0  # mm -> m, matches MAVProxy's "Alt"
        elif mtype == "SYS_STATUS":
            if msg.battery_remaining >= 0:
                flight_state["battery_pct"] = msg.battery_remaining
            if msg.voltage_battery not in (0, 65535):
                flight_state["battery_v"] = msg.voltage_battery / 1000.0
            if msg.current_battery not in (-1,):
                flight_state["battery_a"] = msg.current_battery / 100.0


def command_altitude_change(new_relative_alt_m):
    """
    Send a REAL MAVLink command to the running SITL plane, commanding it to
    a new relative altitude in GUIDED mode. Per ArduPilot's own docs, this is
    done with SET_POSITION_TARGET_GLOBAL_INT (holding current lat/lon, only
    controlling altitude) — see:
    https://ardupilot.org/dev/docs/plane-commands-in-guided-mode.html
    """
    if mav_connection is None or flight_state["lat"] is None or flight_state["lon"] is None:
        print("[app_live] Cannot send altitude command: no live position yet.")
        return False
    try:
        mode_mapping = mav_connection.mode_mapping()
        if mode_mapping and "GUIDED" in mode_mapping:
            mav_connection.set_mode(mode_mapping["GUIDED"])
            print("[app_live] Real command: switched to GUIDED mode.")
    except Exception as e:
        print(f"[app_live] WARNING: could not switch to GUIDED mode: {e}")

    type_mask = (
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_VX_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VY_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VZ_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
        | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
    )
    mav_connection.mav.set_position_target_global_int_send(
        0,  # time_boot_ms — ignored by ArduPilot
        mav_connection.target_system, mav_connection.target_component,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        type_mask,
        flight_state["lat"], flight_state["lon"], new_relative_alt_m,
        0, 0, 0,  # vx, vy, vz (ignored)
        0, 0, 0,  # afx, afy, afz (ignored)
        0, 0,     # yaw, yaw_rate (ignored)
    )
    print(f"[app_live] Real command sent: target relative altitude {new_relative_alt_m:.0f}m (GUIDED).")
    return True


def command_rtl():
    """Real command: switch the aircraft straight to RTL (Return To Launch)."""
    if mav_connection is None:
        print("[app_live] Cannot send RTL: no MAVLink connection.")
        return False
    try:
        mode_mapping = mav_connection.mode_mapping()
        if mode_mapping and "RTL" in mode_mapping:
            mav_connection.set_mode(mode_mapping["RTL"])
            print("[app_live] Real command sent: switched to RTL (Return To Launch).")
            return True
        print("[app_live] WARNING: RTL not found in mode mapping.")
    except Exception as e:
        print(f"[app_live] WARNING: could not switch to RTL: {e}")
    return False


def command_throttle_override(pwm):
    """
    Real command: RC_CHANNELS_OVERRIDE on the throttle channel (channel 3 for
    Plane), cutting commanded power to a lower, fixed PWM value. 0 on any
    channel means "don't override that channel" — only channel 3 is touched.
    """
    if mav_connection is None:
        print("[app_live] Cannot send throttle override: no MAVLink connection.")
        return False
    mav_connection.mav.rc_channels_override_send(
        mav_connection.target_system, mav_connection.target_component,
        0, 0, pwm, 0, 0, 0, 0, 0,  # chan1..chan8 — only chan3 (throttle) set
    )
    print(f"[app_live] Real command sent: RC throttle override to {pwm}us.")
    return True


def command_oil_pressure_response():
    """
    Real published emergency procedure order (e.g. Cessna 172 checklist:
    'LOSS OF PRESS, HIGH TEMP — REDUCE POWER, SELECT FORCED LANDING AREA';
    AOPA: reduce power to prolong engine operation, then find a landing
    site) — reduce power FIRST to protect the engine, THEN head back.

    Caveat: once in RTL, ArduPilot's own TECS throttle control may adjust
    throttle again to hold the RTL climb/cruise profile, which can work
    against a forced-low RC override. This ordering is a reasonable
    simplification for a demo, not a substitute for a real power-management
    integration in a production system.
    """
    reduced_ok = command_throttle_override(1200)
    rtl_ok = command_rtl()
    return reduced_ok and rtl_ok


# Fault-specific real response, dispatched when the operator confirms the
# safety banner. Each fault gets the response actually appropriate to it,
# rather than one generic action for everything.
FAULT_RESPONSES = {
    "oil_pressure_drop": lambda: command_oil_pressure_response(),
    "cooling_failure": lambda: command_throttle_override(1300),
    "valve_wear": lambda: command_throttle_override(1450),
}


def send_fault_response(fault_type):
    action = FAULT_RESPONSES.get(fault_type)
    if action is None:
        # Fallback for an unrecognized/no fault: the old generic descend.
        target_alt = max(flight_state["relative_alt"] - FAULT_RESPONSE_ALT_DROP_M, FAULT_RESPONSE_MIN_ALT_M)
        return command_altitude_change(target_alt)
    return action()


async def broadcast(payload):
    dead = []
    for ws in connected:
        try:
            await ws.send_json(payload)
        except Exception as e:
            print(f"[app_live] dropping a client, send failed: {e}")
            dead.append(ws)
    for ws in dead:
        connected.remove(ws)


@app.on_event("startup")
async def start_loop():
    global start_time
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, connect_mavlink)
    except Exception as e:
        print(f"[app_live] WARNING: could not connect to SITL yet ({e}). "
              f"Will keep retrying in the background — start SITL if you haven't.", flush=True)
    start_time = time.time()
    asyncio.create_task(simulation_loop())


@app.on_event("shutdown")
async def stop_loop():
    """Close the MAVLink socket explicitly so a restart (or --reload) doesn't
    leave port 14551 held by a dying process while a new one tries to bind it."""
    global mav_connection
    if mav_connection is not None:
        try:
            mav_connection.close()
            print("[app_live] MAVLink connection closed on shutdown.", flush=True)
        except Exception as e:
            print(f"[app_live] WARNING: error closing MAVLink connection: {e}", flush=True)


async def simulation_loop():
    global mav_connection, response_confirmed, response_start_t, descend_command_sent
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(0.4)

        if mav_connection is None:
            # IMPORTANT: run this in the executor, same as startup does.
            # Calling connect_mavlink() directly here would block the WHOLE
            # asyncio event loop (up to `timeout` seconds) on every retry —
            # freezing the websocket/API for every connected client each time.
            try:
                await loop.run_in_executor(None, connect_mavlink, 3)
            except Exception as e:
                print(f"[app_live] Reconnect attempt failed: {e}", flush=True)
                await asyncio.sleep(2)  # don't hammer a port that's stuck
            continue

        drain_mavlink()  # keep state fresh even with no viewers, cheap

        if not telemetry_ready():
            continue  # never broadcast placeholder/default data — real telemetry only

        if not connected:
            continue

        if response_confirmed and not descend_command_sent:
            send_fault_response(engine.fault_type)
            descend_command_sent = True

        t_sec = time.time() - start_time
        result = engine.update(
            t_sec, flight_state["relative_alt"], flight_state["airspeed"],
            flight_state["roll"], flight_state["pitch"], flight_state["yaw"],
        )

        raw_resid = {s: result["actual"][s] - result["expected"][s] for s in SENSORS}
        feat = smoothed_resid(t_sec, raw_resid)

        pred = predict_row(feat)
        trend_history.append({"timestamp": t_sec, "severity": pred["severity"], "rul": pred["rul"]})

        payload = {
            "t_sec": t_sec,
            "true_fault": result["true_fault"],
            "true_severity": result["true_severity"],
            "sensors": result["actual"],
            "expected": result["expected"],
            "altitude": result["altitude"],  # real relative altitude, live from SITL
            "lat": flight_state["lat"] / 1e7,
            "lon": flight_state["lon"] / 1e7,
            "heading": flight_state["yaw"],
            "roll": flight_state["roll"],
            "pitch": flight_state["pitch"],
            "airspeed": flight_state["airspeed"],
            "groundspeed": flight_state["groundspeed"],
            "battery_pct": flight_state["battery_pct"],
            "battery_v": flight_state["battery_v"],
            "battery_a": flight_state["battery_a"],
            "prediction": pred,
            "history": list(trend_history),
            "response_confirmed": response_confirmed,
        }
        await broadcast(payload)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    global response_confirmed, response_start_t, descend_command_sent
    await websocket.accept()
    connected.append(websocket)
    try:
        while True:
            msg = await websocket.receive_json()
            action = msg.get("action")
            if action == "inject_fault":
                engine.inject_fault(msg.get("fault"))
                trend_history.clear()
                response_confirmed = False
                response_start_t = None
                descend_command_sent = False
            elif action == "clear_fault":
                engine.clear_fault()
                trend_history.clear()
                response_confirmed = False
                response_start_t = None
                descend_command_sent = False
            elif action == "confirm_action":
                response_confirmed = True
                response_start_t = time.time() - start_time
            elif action == "dismiss_action":
                pass
    except WebSocketDisconnect:
        if websocket in connected:
            connected.remove(websocket)


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html") as f:
        return f.read()
