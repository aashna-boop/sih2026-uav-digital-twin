"""
FastAPI backend for the UAV engine digital twin dashboard.

Loads the three trained models (fault classifier, severity regressor,
RUL regressor) and streams REAL rows from engine_master_dataset.csv through
them live over a WebSocket, exactly as the dashboard would in production if
fed by real (or SITL-simulated) telemetry. No hand-coded fault logic here —
every prediction, probability, and SHAP attribution comes from the trained
model.

Run with:  uvicorn app:app --reload
Then open: http://localhost:8000
"""
import asyncio
import json
import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

DATA_PATH = "engine_master_dataset.csv"

SENSORS = ["rpm", "egt_c", "cht_c", "oil_temp_c", "oil_pressure_bar", "fuel_flow_lph", "vibration"]
FLIGHT_STATE = ["airspeed", "load", "roll", "pitch"]
FEATURES = (
    SENSORS + FLIGHT_STATE
    + [f"{s}_resid" for s in SENSORS]
    + [f"{s}_resid_pct" for s in SENSORS]
)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Load models once at startup ----------
clf = joblib.load("model_fault_classifier.joblib")
reg_sev = joblib.load("model_severity_regressor.joblib")
reg_rul = joblib.load("model_rul_regressor.joblib")
# NOTE: we compute SHAP values via XGBoost's own native pred_contribs (exact
# TreeSHAP, built into the library) instead of the external `shap` package's
# TreeExplainer. TreeExplainer parses the booster's internal serialized
# format directly, which breaks across xgboost versions (e.g. a model
# trained on xgboost 2.x fails to load in shap on a machine with a
# different xgboost/shap version pairing). pred_contribs sidesteps that
# entirely since it's computed by the same library that saved the model.

# ---------- Load dataset and rebuild the same residual features used in training ----------
raw = pd.read_csv(DATA_PATH)
healthy = raw[raw.source_file == "engine_healthy.csv"].sort_values("t_sec").reset_index(drop=True)
baseline = healthy[SENSORS].copy()

RUNS = {}  # fault_key -> dataframe with features, indexed 0..N-1
FILE_MAP = {
    "healthy": "engine_healthy.csv",
    "valve_wear": "engine_valve_wear.csv",
    "cooling_failure": "engine_cooling_failure.csv",
    "oil_pressure_drop": "engine_oil_pressure_drop.csv",
}
for key, fname in FILE_MAP.items():
    run = raw[raw.source_file == fname].sort_values("t_sec").reset_index(drop=True).copy()
    for s in SENSORS:
        run[f"{s}_expected"] = baseline[s].values[: len(run)]
        run[f"{s}_resid"] = run[s] - run[f"{s}_expected"]
        run[f"{s}_resid_pct"] = run[f"{s}_resid"] / (run[f"{s}_expected"].abs() + 1e-6)
    RUNS[key] = run

RUN_LEN = len(RUNS["healthy"])
CLASS_LABELS = sorted(raw.fault_type.unique().tolist())

# ---------- Simulation state (shared across connected clients) ----------
state = {
    "idx": 0,
    "active_fault": "healthy",   # which dataset run is currently being streamed
    "speed": 50,                 # rows advanced per tick (real data is 25Hz -> ~6x playback speed, full 647s run in ~2min)
    "response_confirmed": False,
    "response_start_idx": None,
}

def predict_row(row_features: pd.Series):
    # NOTE: the classifier was trained on integer-encoded labels
    # (0..3, alphabetically sorted fault_type strings) — CLASS_LABELS
    # below reproduces that exact sorted order to map predictions back
    # to real fault names. Getting this mapping wrong silently returns
    # nonsense labels, so it's worth double-checking against
    # model_report.json's "classes" list if you ever retrain.
    X = row_features[FEATURES].to_frame().T.astype(float)
    proba = clf.predict_proba(X)[0]
    pred_idx = int(np.argmax(proba))
    pred_class = CLASS_LABELS[pred_idx]
    severity = float(np.clip(reg_sev.predict(X)[0], 0, 1))
    rul = float(np.clip(reg_rul.predict(X)[0], 0, 1))

    # Native XGBoost TreeSHAP (exact, no external shap-library version
    # dependency). Shape for multiclass: (1, num_class, num_features + 1);
    # the last column per class is the bias term, which we drop.
    dmat = xgb.DMatrix(X, feature_names=FEATURES)
    contribs = clf.get_booster().predict(dmat, pred_contribs=True)
    contribs = np.array(contribs)
    if contribs.ndim == 3:
        class_shap = contribs[0, pred_idx, :-1]
    else:
        class_shap = contribs[0, :-1]
    contributions = sorted(
        zip(FEATURES, class_shap), key=lambda x: -abs(x[1])
    )[:5]

    return {
        "predicted_fault": pred_class,
        "confidence": float(proba[pred_idx]),
        "probabilities": {CLASS_LABELS[i]: float(p) for i, p in enumerate(proba)},
        "severity": severity,
        "rul": rul,
        "shap": [{"feature": f, "value": float(v)} for f, v in contributions],
    }

async def broadcast(websockets, payload):
    dead = []
    for ws in websockets:
        try:
            await ws.send_json(payload)
        except Exception as e:
            print(f"[broadcast] dropping a client, send failed: {e}")
            dead.append(ws)
    for ws in dead:
        websockets.remove(ws)

connected = []

@app.on_event("startup")
async def start_loop():
    asyncio.create_task(simulation_loop())

async def simulation_loop():
    while True:
        await asyncio.sleep(0.4)
        if not connected:
            continue

        run = RUNS[state["active_fault"]]
        idx = state["idx"] % RUN_LEN
        row = run.iloc[idx]

        pred = predict_row(row)

        if state["response_confirmed"]:
            elapsed = state["idx"] - state["response_start_idx"]
            glide_ticks = 60  # ~24s of wall-clock glide after confirmation
            p = min(1.0, elapsed / glide_ticks)
            alt = float(row["gps_alt"]) - 40 * p  # simple descent toward RTL
        else:
            alt = float(row["gps_alt"])

        payload = {
            "t_sec": float(row["t_sec"]),
            "true_fault": row["fault_type"],
            "true_severity": float(row["fault_severity"]),
            "sensors": {s: float(row[s]) for s in SENSORS},
            "expected": {s: float(row[f"{s}_expected"]) for s in SENSORS},
            "altitude": alt,
            "prediction": pred,
            "response_confirmed": state["response_confirmed"],
        }
        await broadcast(connected, payload)
        state["idx"] += state["speed"]

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected.append(websocket)
    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            action = data.get("action")
            if action == "inject_fault":
                state["active_fault"] = data.get("fault", "healthy")
                state["idx"] = 0
                state["response_confirmed"] = False
                state["response_start_idx"] = None
            elif action == "clear_fault":
                state["active_fault"] = "healthy"
                state["idx"] = 0
                state["response_confirmed"] = False
                state["response_start_idx"] = None
            elif action == "confirm_action":
                state["response_confirmed"] = True
                state["response_start_idx"] = state["idx"]
            elif action == "dismiss_action":
                pass
    except WebSocketDisconnect:
        if websocket in connected:
            connected.remove(websocket)

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html") as f:
        return f.read()
