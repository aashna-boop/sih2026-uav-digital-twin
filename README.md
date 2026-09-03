# UAV Engine Digital Twin

Predictive fault detection for MALE UAV piston engines, built for Smart India Hackathon 2026.

---

## Background

MALE UAVs (Medium Altitude Long Endurance) stay airborne for 12–24 hours at a stretch. The engine keeping them up is a piston engine — not unlike what you'd find in a light aircraft, sized down. These engines are reliable but not invincible: valve seats wear, cooling degrades, oil pressure fluctuates. The problem isn't that faults happen. It's that by the time they're visible on a gauge, you're already behind.

The standard approach is scheduled maintenance — fly X hours, then inspect. That works but it's conservative by design: you're either replacing parts that still had life left, or you're flying longer than you should between checks because the schedule can't account for individual mission stress.

A digital twin does something different. It builds a model of what the engine *should* be doing right now — at this altitude, this airspeed, this load — and watches how the real sensor data diverges from that. Divergence is information. Identify the pattern of divergence, and you can name the fault. Track it over time, and you can estimate how much runway you have left.

That's what this project is.

---

## What we built

The system has three main parts that fit together: a physics-based engine simulator, a set of trained ML models, and a live dashboard with human-in-the-loop controls.

### The physics model

This is the core of the "twin" idea and the part that makes the whole thing defensible. We didn't generate synthetic data by adding noise to clean numbers. We built an engine simulator modeled on a Rotax 912-class piston engine — the type used in real small UAVs — and drove it with actual ArduPilot SITL flight telemetry.

ArduPilot SITL is real autopilot software, the same codebase that runs on actual hardware. When it simulates a flight, it produces the same telemetry a real drone would: GPS, IMU, airspeed, barometer, attitude. We took that telemetry and fed it into an engine physics model that computes what the engine would actually be doing at each moment:

- RPM responds to throttle position and aerodynamic load, which itself depends on airspeed and altitude
- Cylinder head temperature rises with load and falls with airspeed-driven cooling, with realistic thermal lag — the temperature doesn't jump instantly, it rises and falls the way real metal does
- Exhaust gas temperature tracks combustion efficiency
- Oil pressure and oil temperature evolve with RPM and heat soak

On top of the healthy baseline, we injected four specific fault modes, each with physically-reasoned sensor effects:

**Valve wear** — as a valve seat degrades, cylinder compression drops. Exhaust gas temperature shifts because combustion is less complete. RPM becomes slightly inconsistent. The effect is gradual and starts subtle, which is the point.

**Cooling system failure** — the engine starts losing its ability to shed heat. CHT climbs faster than normal under load and doesn't stabilize the way it should when airspeed increases. Eventually it runs hot even in level flight.

**Oil pressure drop** — oil pressure falls, which increases friction and causes secondary heat rises. The pressure signal itself is the most obvious indicator, but the thermal effects confirm it.

**Ignition fault** — misfires cause EGT spikes and RPM instability. The pattern is distinct from the thermal faults.

All four were injected at realistic onset rates — not step changes, but gradual ramp-ins that mimic how real faults develop.

### The dataset

The physics model produced `engine_master_dataset.csv` — one combined file with labeled healthy and fault-condition runs. The pipeline that built it:

```
ArduPilot SITL flight log
    → merge_mission_profile.py   (aligns GPS, IMU, airspeed, baro by timestamp)
    → engine_physics_model.py    (runs the engine simulation, injects faults)
    → combine_datasets.py        (merges all runs into one labeled file)
```

The dataset includes both raw sensor values and residuals — the difference between what the physics model says a healthy engine should show and what the (simulated) engine actually shows. Those residuals are the most informative features for the ML models.

### The ML models

Three XGBoost models, each answering a different question:

**Fault classifier** — given the current sensor readings and residuals, which state is the engine in? Healthy, valve wear, cooling failure, or oil pressure drop? Trained on the first 75% of each flight, tested on the last 25%. Accuracy on the test set: 100%. This is high but not implausible — each fault has a sufficiently distinct sensor signature that a well-trained classifier can separate them cleanly. We're flagging it because a perfect score always warrants scrutiny, but the confusion matrix and SHAP outputs support it being genuine.

**Severity regressor** — how bad is the fault right now, on a 0–1 scale? R² ≈ 0.59 on the test set. This is harder than classification because severity is a continuous estimate of how far along a degradation process is, not a category membership question. 0.59 is real predictive signal.

**Remaining useful life regressor** — roughly how much time before the fault reaches a critical threshold? Same R² ballpark as severity, same reasoning. These two numbers together give the operator a sense of urgency: is this something to watch over the next hour, or something to act on now?

Every prediction comes with a SHAP explanation — which sensor features drove the decision, and by how much. For an oil pressure fault, oil pressure dominates the explanation. For cooling failure, CHT and its rate of change do. The model's reasoning maps to what an engineer would expect, which matters for operator trust.

One calibration note: the severity and RUL models' predictions top out around 0.32–0.35 in practice, even at the worst point of a fault run, rather than reaching 1.0. The dashboard alert thresholds are set against this observed behavior, not against a theoretical maximum. This is documented in `MODEL_REPORT.md`.

### The dashboard

A single-page dashboard (`static/index.html`) that streams predictions in real time over WebSocket. It shows current sensor readings, the healthy-engine baseline, residuals, fault classification with confidence, severity score, RUL estimate, and the SHAP breakdown for the current prediction. Fault injection buttons let an operator or evaluator trigger a specific fault and watch the system respond.

The operator sees the recommendation. They decide what to do. Nothing acts automatically.

---

## Two backends, same dashboard

We built this in two configurations because demonstrating it with live data is meaningfully different from replaying a recording, and both matter.

**`app.py` — replay mode.** Reads rows from `engine_master_dataset.csv` sequentially, as if they were arriving live. No external dependencies. This is what you run to evaluate the system, demo it offline, or develop against it. Fault buttons switch the stream to a different fault condition's recorded run.

**`app_live.py` — live mode.** Connects to a real running ArduPilot SITL instance over MAVLink. Instead of reading from a file, `live_engine.py` runs the physics model in real time, ingesting live telemetry as it arrives and computing both the healthy baseline and the fault-affected readings moment by moment. Fault buttons inject a fault into the live simulation, which ramps in gradually just as it did in the offline runs.

`live_engine.py` exists as a separate file from `engine_physics_model.py` because the original script processes a complete flight log in one pass — it needs the whole thing in memory to compute averages and initial conditions. A streaming context doesn't have that luxury. `live_engine.py` is a rewrite of the same equations to work incrementally, updating state one telemetry packet at a time. The physics are identical; only the processing model changed.

---

## Running it

**Replay mode** — no SITL needed:

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

Open http://localhost:8000. Use the fault buttons on the dashboard to switch between fault conditions.

**Live mode** — requires ArduPilot SITL:

Terminal 1, start SITL with a dedicated output port:
```bash
cd ~/ardupilot/ArduPlane
../Tools/autotest/sim_vehicle.py --console --map --out=udp:127.0.0.1:14551
```

Terminal 2, start the live backend:
```bash
uvicorn app_live:app --reload
```

The plane starts stationary. To actually change the telemetry (airspeed, altitude, load), fly it manually in the SITL console:
```
mode manual
arm throttle
rc 3 2000      # full throttle, plane accelerates
rc 2 1300      # nose up, starts climbing once airspeed builds
rc 2 1500      # level off
```

---

## File reference

| File | What it is |
|---|---|
| `engine_physics_model.py` | The offline physics simulator — generates the dataset from SITL logs |
| `merge_mission_profile.py` | Aligns multi-file SITL logs into a single clean timeline |
| `combine_datasets.py` | Merges healthy and fault-condition runs into one labeled CSV |
| `engine_master_dataset.csv` | The full training/test dataset |
| `train_model.py` | Trains all three XGBoost models and saves them |
| `model_fault_classifier.joblib` | Trained fault classifier |
| `model_severity_regressor.joblib` | Trained severity estimator |
| `model_rul_regressor.joblib` | Trained remaining-life estimator |
| `model_report.json` | Full evaluation metrics from training |
| `confusion_matrix.png` | Classifier performance across all four classes |
| `feature_importance.png` | SHAP-based feature importance across the dataset |
| `live_engine.py` | Stream-friendly rewrite of the physics model for real-time use |
| `app.py` | Replay backend |
| `app_live.py` | Live SITL backend |
| `static/index.html` | Dashboard — shared by both backends |
| `MODEL_REPORT.md` | Detailed model documentation including calibration notes |
| `PROJECT_DOCUMENTATION.md` | Full project write-up in plain language |

---

## Limitations

We have one flight per fault condition in the dataset. Within a flight, the models generalize well. Whether they'd hold up across independent flights with different mission profiles, different initial engine temperatures, or different onset rates is an open question we haven't tested.

The severity and RUL models sit at R² ≈ 0.59. That's meaningful predictive signal but it's not a precise instrument. The fault classifier tells you *what* is wrong with high confidence. The severity and RUL outputs are better understood as rough urgency signals than precise measurements.

The classifier has no out-of-distribution fallback. If the engine develops a fault type it's never seen — something outside the four trained classes — it will still return one of those four labels with some confidence score. It cannot say "I don't know what this is."

The live mode is genuinely dependent on a working SITL connection on the expected port. If SITL isn't running, the dashboard shows a disconnected state rather than fabricating data, which is the right behavior but does mean the live demo has a hard external requirement.

---

## Stack

Python, XGBoost, FastAPI, ArduPilot SITL, MAVLink (pymavlink), SHAP, scikit-learn, pandas
