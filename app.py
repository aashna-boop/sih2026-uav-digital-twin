"""
FastAPI backend for the UAV engine digital twin dashboard.

Loads the three trained models (fault classifier, severity regressor,
RUL regressor) and streams REAL rows from engine_master_dataset.csv through
them live over a WebSocket, exactly as the dashboard would in production if
fed by real (or SITL-simulated) telemetry. No hand-coded fault logic here —
every prediction, probability, and SHAP attribution comes from the trained
model.

Section E additions:
- Environmental scenario runs: 4 pre-generated healthy flights under
  different environmental conditions (high altitude, hot weather, rapid
  throttle transitions, endurance), showing how the digital twin's expected
  values adapt to each environment.
- Battery/alternator data included in the broadcast payload.
- 'select_scenario' WebSocket action to switch between scenario runs.

Run with:  uvicorn app:app --reload
Then open: http://localhost:8000
"""
import asyncio
import json
from collections import deque
import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

DATA_PATH = "engine_master_dataset.csv"

# Model input = the 7 smoothed digital-twin residuals, built exactly as in
# train_model.py (same reference model, same trailing window, same warm-up
# drop). Keep these two constants in sync with train_model.py.
from digital_twin import SENSORS, reference_trajectory

SMOOTH_WINDOW_SAMPLES = 10  # 2.5s trailing mean at the dataset's 4 Hz sample rate
WARMUP_DROP = 4             # first rows of a run, before the smoothing window has filled
FEATURES = [f"{s}_resid_smooth" for s in SENSORS]

# Trend of predicted severity / RUL sent with each payload as `history`.
TREND_HISTORY_MAX = 300

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
if "ambient_offset_c" not in raw.columns:
    raw["ambient_offset_c"] = 0.0

FAULT_KEYS = ["healthy", "valve_wear", "cooling_failure", "oil_pressure_drop"]
REPLAY_TARGET_SEVERITY = 0.6   # same as live_engine's injected target severity
REPLAY_TARGET_DURATION_S = 600.0


def pick_run_id(fault_key):
    """The dataset now holds many independent runs per class. Replay one
    representative, deterministic run per fault: not a near-miss run, and the
    one whose peak severity (or, for healthy, whose length) is closest to the
    demo target, so onset and progression are visible in a ~2 minute replay."""
    runs = raw[raw.run_id.str.rsplit("_", n=1).str[0] == fault_key]
    runs = runs[runs.near_miss == 0] if "near_miss" in runs.columns else runs
    g = runs.groupby("run_id").agg(T=("t_sec", "max"), sev=("fault_severity", "max"))
    g = g[(g["T"] >= 450) & (g["T"] <= 800)]
    if fault_key == "healthy":
        score = (g["T"] - REPLAY_TARGET_DURATION_S).abs()
    else:
        score = (g["sev"] - REPLAY_TARGET_SEVERITY).abs()
    return score.sort_values(kind="stable").index[0]


def build_run(run_id):
    g = raw[raw.run_id == run_id].sort_values("t_sec").reset_index(drop=True).copy()
    alt_col = g["altitude_m"].tolist() if "altitude_m" in g.columns else None
    ref = reference_trajectory(g["t_sec"].tolist(), g["load"].tolist(),
                               g["ambient_offset_c"].tolist(), alt_col)
    for s in SENSORS:
        g[f"{s}_expected"] = ref[s]
        g[f"{s}_resid"] = g[s] - g[f"{s}_expected"]
        g[f"{s}_resid_smooth"] = g[f"{s}_resid"].rolling(SMOOTH_WINDOW_SAMPLES, min_periods=WARMUP_DROP).mean()
    return g.iloc[WARMUP_DROP:].reset_index(drop=True)


RUNS = {key: build_run(pick_run_id(key)) for key in FAULT_KEYS}   # fault_key -> dataframe with features
for key, run in RUNS.items():
    print(f"[app] replay run for {key!r}: {run.run_id.iloc[0]} ({len(run)} rows)")

# ---------- Section E: Pre-generate scenario runs ----------
from engine_physics_model import (
    generate_run as gen_run, SCENARIO_DEFAULTS, ARCHETYPES,
)

SCENARIO_KEYS = ["high_altitude_cruise", "hot_weather_ops",
                 "rapid_throttle_transition", "endurance_mission"]

SCENARIO_META = {
    "high_altitude_cruise": {
        "label": "High-Altitude Cruise",
        "icon": "🏔️",
        "description": "Climb to 3000 m, cruise at altitude, descent. Cooler ambient, reduced air density.",
        "cruise_alt_m": 3000,
    },
    "hot_weather_ops": {
        "label": "Hot-Weather Ops",
        "icon": "🌡️",
        "description": "Moderate altitude, 55°C ambient day. Stress-tests thermal channels.",
        "cruise_alt_m": 0,
    },
    "rapid_throttle_transition": {
        "label": "Rapid Throttle Transition",
        "icon": "⚡",
        "description": "Aggressive maneuvers with sharp load swings (0.35↔0.95). Tests transient response.",
        "cruise_alt_m": 0,
    },
    "endurance_mission": {
        "label": "Endurance Mission",
        "icon": "🔋",
        "description": "Extended 20-30 min flight at moderate cruise. Tests sustained thermal soak.",
        "cruise_alt_m": 500,
    },
}


def build_scenario_run(scenario_key):
    """Generate a healthy run under scenario-specific environmental conditions."""
    defaults = SCENARIO_DEFAULTS.get(scenario_key, {})
    ambient = defaults.get("ambient_offset_c", 0.0)
    rows = gen_run(
        fault_type="healthy",
        severity=0.0,
        onset_frac=1.0,
        ambient_offset_c=ambient,
        seed=hash(scenario_key) % 100000,
        run_id=f"scenario_{scenario_key}",
        archetype=scenario_key,
    )
    # Convert to DataFrame and build residual features
    df = pd.DataFrame(rows)
    alt_col = df["altitude_m"].tolist() if "altitude_m" in df.columns else None
    ref = reference_trajectory(df["t_sec"].tolist(), df["load"].tolist(),
                               df["ambient_offset_c"].tolist(), alt_col)
    for s in SENSORS:
        df[f"{s}_expected"] = ref[s]
        df[f"{s}_resid"] = df[s] - df[f"{s}_expected"]
        df[f"{s}_resid_smooth"] = df[f"{s}_resid"].rolling(
            SMOOTH_WINDOW_SAMPLES, min_periods=WARMUP_DROP).mean()
    return df.iloc[WARMUP_DROP:].reset_index(drop=True)


SCENARIO_RUNS = {}
for sk in SCENARIO_KEYS:
    SCENARIO_RUNS[sk] = build_scenario_run(sk)
    print(f"[app] scenario run for {sk!r}: {len(SCENARIO_RUNS[sk])} rows")


CLASS_LABELS = sorted(raw.fault_type.unique().tolist())

# ---------- Simulation state (shared across connected clients) ----------
state = {
    "idx": 0,
    "active_fault": "healthy",   # which dataset run is currently being streamed
    "active_scenario": None,     # Section E: active environmental scenario (overrides active_fault)
    "speed": 8,                  # rows advanced per tick (data is 4Hz, tick is 0.4s -> 5x playback speed, a ~600s run in ~2min)
    "history": deque(maxlen=TREND_HISTORY_MAX),   # rolling {timestamp, severity, rul} for the current run
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

        # Determine which run to replay: scenario overrides fault
        if state["active_scenario"] and state["active_scenario"] in SCENARIO_RUNS:
            run = SCENARIO_RUNS[state["active_scenario"]]
            scenario_key = state["active_scenario"]
        else:
            run = RUNS[state["active_fault"]]
            scenario_key = None

        run_len = len(run)
        if state["idx"] >= run_len:
            # Replay reached the end of the recorded run and loops: new session, fresh trend.
            state["idx"] = 0
            state["history"].clear()
        row = run.iloc[state["idx"]]

        pred = predict_row(row)
        state["history"].append({"timestamp": float(row["t_sec"]), "severity": pred["severity"], "rul": pred["rul"]})

        if state["response_confirmed"]:
            elapsed_ticks = (state["idx"] - state["response_start_idx"]) / state["speed"]
            p = min(1.0, elapsed_ticks / 60)  # 60 ticks x 0.4s = ~24s of wall-clock glide after confirmation
            alt = float(row["gps_alt"]) - 40 * p  # simple descent toward RTL
        else:
            alt = float(row["gps_alt"])

        # Battery data (Section E): use columns if present, else defaults
        batt_v = float(row["battery_voltage_v"]) if "battery_voltage_v" in row.index else 12.6
        batt_a = float(row["battery_current_a"]) if "battery_current_a" in row.index else 1.5
        batt_soc = float(row["battery_soc_pct"]) if "battery_soc_pct" in row.index else 100.0
        altitude_m = float(row["altitude_m"]) if "altitude_m" in row.index else 0.0

        payload = {
            "t_sec": float(row["t_sec"]),
            "true_fault": row["fault_type"],
            "true_severity": float(row["fault_severity"]),
            "sensors": {s: float(row[s]) for s in SENSORS},
            "expected": {s: float(row[f"{s}_expected"]) for s in SENSORS},
            "altitude": alt,
            "altitude_m": altitude_m,
            "prediction": pred,
            "history": list(state["history"]),
            "response_confirmed": state["response_confirmed"],
            # Section E: battery health
            "battery_voltage_v": batt_v,
            "battery_current_a": batt_a,
            "battery_soc_pct": batt_soc,
            # Section E: scenario metadata
            "scenario": scenario_key,
            "scenario_meta": SCENARIO_META.get(scenario_key) if scenario_key else None,
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
                state["active_scenario"] = None
                state["idx"] = 0
                state["history"].clear()
                state["response_confirmed"] = False
                state["response_start_idx"] = None
            elif action == "clear_fault":
                state["active_fault"] = "healthy"
                state["active_scenario"] = None
                state["idx"] = 0
                state["history"].clear()
                state["response_confirmed"] = False
                state["response_start_idx"] = None
            elif action == "select_scenario":
                scenario = data.get("scenario")
                if scenario in SCENARIO_RUNS:
                    state["active_scenario"] = scenario
                    state["active_fault"] = "healthy"
                    state["idx"] = 0
                    state["history"].clear()
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

