"""
app_live.py

LIVE version of the dashboard backend — connects to a running ArduPilot
SITL instance over MAVLink, drives your team's own physics-based engine
model (live_engine.py, ported from engine_physics_model.py) with real
flight telemetry, and runs every reading through the SAME trained models
used by the CSV-replay version (app.py).

This does NOT touch app.py or its dataset-replay behavior — app.py still
works standalone as a fallback/reference. This is a separate entry point.

BEFORE RUNNING THIS:
1. Start ArduPilot SITL with an extra MAVLink output port dedicated to this
   script, so it doesn't fight with MAVProxy's own console/map connection:

       cd ~/ardupilot/ArduPlane
       ../Tools/autotest/sim_vehicle.py --console --map --out=udp:127.0.0.1:14551

   (If you don't add --out=udp:127.0.0.1:14551, try MAVLINK_CONNECTION_STRING
   below set to 'udp:127.0.0.1:14550' instead, but that port is often already
   used by MAVProxy's own GCS link and may fail to connect.)

2. Run this from the SAME machine/WSL environment as SITL (they need to be
   able to reach each other over localhost):

       uvicorn app_live:app --reload

3. Open http://localhost:8000 exactly as before.

Run `uvicorn app_live:app --reload` from the folder containing this file,
model_*.joblib, static/index.html, and live_engine.py.
"""
import asyncio
import time
import math
import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pymavlink import mavutil

from live_engine import LiveEngine

MAVLINK_CONNECTION_STRING = "udp:127.0.0.1:14551"

SENSORS = ["rpm", "egt_c", "cht_c", "oil_temp_c", "oil_pressure_bar", "fuel_flow_lph", "vibration"]
FLIGHT_STATE = ["airspeed", "load", "roll", "pitch"]
FEATURES = (
    SENSORS + FLIGHT_STATE
    + [f"{s}_resid" for s in SENSORS]
    + [f"{s}_resid_pct" for s in SENSORS]
)
CLASS_LABELS = ["cooling_failure", "healthy", "oil_pressure_drop", "valve_wear"]  # alphabetical, matches training

app = FastAPI()

clf = joblib.load("model_fault_classifier.joblib")
reg_sev = joblib.load("model_severity_regressor.joblib")
reg_rul = joblib.load("model_rul_regressor.joblib")

connected = []
engine = LiveEngine()
flight_state = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "airspeed": 0.0, "alt": 0.0}
mav_connection = None
start_time = None
response_confirmed = False
response_start_t = None


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


def connect_mavlink():
    global mav_connection
    print(f"[app_live] Connecting to SITL at {MAVLINK_CONNECTION_STRING} ...")
    mav_connection = mavutil.mavlink_connection(MAVLINK_CONNECTION_STRING)
    print("[app_live] Waiting for heartbeat (make sure SITL is running)...")
    mav_connection.wait_heartbeat(timeout=15)
    print(f"[app_live] Heartbeat received from system {mav_connection.target_system}. Connected.")


def drain_mavlink():
    """Non-blocking: pull in every pending message and update the latest flight_state."""
    if mav_connection is None:
        return
    while True:
        msg = mav_connection.recv_match(type=["ATTITUDE", "VFR_HUD"], blocking=False)
        if msg is None:
            break
        if msg.get_type() == "ATTITUDE":
            flight_state["roll"] = math.degrees(msg.roll)
            flight_state["pitch"] = math.degrees(msg.pitch)
            flight_state["yaw"] = math.degrees(msg.yaw) % 360
        elif msg.get_type() == "VFR_HUD":
            flight_state["airspeed"] = msg.airspeed
            flight_state["alt"] = msg.alt


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
              f"Will keep retrying in the background — start SITL if you haven't.")
    start_time = time.time()
    asyncio.create_task(simulation_loop())


async def simulation_loop():
    global mav_connection, response_confirmed, response_start_t
    while True:
        await asyncio.sleep(0.4)

        if mav_connection is None:
            try:
                connect_mavlink()
            except Exception:
                continue

        if not connected:
            drain_mavlink()  # keep state fresh even with no viewers, cheap
            continue

        drain_mavlink()
        t_sec = time.time() - start_time
        result = engine.update(
            t_sec, flight_state["alt"], flight_state["airspeed"],
            flight_state["roll"], flight_state["pitch"], flight_state["yaw"],
        )

        feat = {}
        for s in SENSORS:
            feat[s] = result["actual"][s]
            feat[f"{s}_resid"] = result["actual"][s] - result["expected"][s]
            feat[f"{s}_resid_pct"] = feat[f"{s}_resid"] / (abs(result["expected"][s]) + 1e-6)
        feat["airspeed"] = flight_state["airspeed"]
        feat["load"] = result["load"]
        feat["roll"] = flight_state["roll"]
        feat["pitch"] = flight_state["pitch"]

        pred = predict_row(feat)

        alt = result["altitude"]
        if response_confirmed:
            elapsed = t_sec - response_start_t
            p = min(1.0, elapsed / 24.0)
            alt = alt - 40 * p

        payload = {
            "t_sec": t_sec,
            "true_fault": result["true_fault"],
            "true_severity": result["true_severity"],
            "sensors": result["actual"],
            "expected": result["expected"],
            "altitude": alt,
            "prediction": pred,
            "response_confirmed": response_confirmed,
        }
        await broadcast(payload)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    global response_confirmed, response_start_t
    await websocket.accept()
    connected.append(websocket)
    try:
        while True:
            msg = await websocket.receive_json()
            action = msg.get("action")
            if action == "inject_fault":
                engine.inject_fault(msg.get("fault"))
                response_confirmed = False
                response_start_t = None
            elif action == "clear_fault":
                engine.clear_fault()
                response_confirmed = False
                response_start_t = None
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
