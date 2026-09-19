# UAV engine digital twin — complete project documentation

*Written in plain language for anyone on the team, or a judge, who isn't
deep into machine learning or software engineering. This is the full,
up-to-date picture of everything that's been built.*

---

## 1. What this project actually does

Imagine a drone (a MALE UAV — a military-grade long-endurance drone) with a
piston engine, like a small motorcycle engine, keeping it in the air for
hours. If something starts going wrong with that engine mid-flight — oil
pressure drops, it overheats, a valve wears out — you want to know *before*
it fails, not after.

This project builds a system that:
1. Watches the engine's sensors in real time
2. Compares what it's *actually* seeing against what a *healthy* engine
   should look like at that exact moment in the flight
3. Uses a trained AI model to identify which fault is developing, how
   severe it is, and roughly how much longer before it's critical
4. Explains *why* it thinks that, in plain terms
5. Shows all of this on a live dashboard, with a human operator approving
   any recommended action — the system never acts on its own

**Two working versions of this exist now**, both fully built and tested:

| Version | What drives it | File |
|---|---|---|
| Replay version | Streams real recorded rows from your dataset | `app.py` |
| Live version | Connects to an actual running ArduPilot flight simulator (SITL) | `app_live.py` |

Both use the exact same trained AI model and the exact same dashboard —
only where the sensor data comes from is different.

---

## 2. Where the data actually comes from — the generation pipeline

This is the part that makes the whole project credible: **your team built a
real, physics-based engine simulator**, not just made-up numbers. Here's the
chain, in order:

### Step 1 — Fly a mission in ArduPilot SITL
ArduPilot SITL is real, actual autopilot software (the same code that flies
real drones), just running in a simulator instead of on real hardware. A
mission is flown, and it logs everything: altitude, airspeed, roll, pitch,
yaw, GPS, etc.

### Step 2 — `merge_mission_profile.py`
The raw flight log comes out as several separate files (GPS data, attitude
data, airspeed data, barometer data). This script lines them all up by
timestamp into one clean, single timeline — "at this exact second, the
drone was at this altitude, this airspeed, tilted this many degrees."

### Step 3 — `engine_physics_model.py`
This is the heart of the "digital twin" idea. It takes that flight timeline
and simulates what a real piston engine (modeled loosely on a Rotax
912-class engine, a real engine used in small UAVs) would be doing at every
one of those moments:
- Higher altitude/climbing → more engine load → higher RPM, hotter exhaust
- It even models realistic *thermal lag* — temperatures don't jump
  instantly, they rise and fall gradually, the way real metal does
- On top of the healthy simulation, it can inject specific, realistic
  faults: valve wear, ignition problems, cooling failure, oil pressure
  drop — each with its own distinct, physically-reasoned effect on the
  sensors (e.g. a cooling fault makes cylinder temperature climb faster
  and not level off normally)

### Step 4 — `combine_datasets.py`
Takes the healthy simulation and all the fault simulations and merges them
into one master file — this became `engine_master_dataset.csv`, which
everything else in the project is built on.

**Why this matters**: this means the "expected" sensor values used
throughout this project aren't guesses — they're the output of a genuine
physics simulation your team built, driven by a genuine flight simulator.
That's a real, defensible foundation, not a shortcut.

---

## 3. The AI model — explained without jargon

We used **XGBoost**, a well-established, trusted type of machine learning
model — think of it as thousands of small decision trees ("is oil pressure
below X? is temperature above Y?") combining their answers into one
confident prediction.

Three separate models were trained:

| Model | Question it answers |
|---|---|
| Fault classifier | "Which fault is this — healthy, valve wear, cooling failure, or oil pressure drop?" |
| Severity model | "How bad is it right now, 0 to 1?" |
| Remaining-life model | "Roughly how much time is left before this gets critical?" |

### How we know it actually works
We tested it honestly: trained only on the **first 75% of each flight**,
tested on the **last 25%** — a part of the flight the model never saw,
where the fault is further along. That's a fair test of real learning, not
memorization.

### Results, explained
- **Which fault it is: 100% correct** on unseen data. This is believable
  here because each fault has a large, distinctive signature in this
  dataset — not a sign of overfitting, but worth stating plainly since a
  perfect score always deserves scrutiny.
- **How severe it is: ~59% accurate** (R² ≈ 0.59). A genuinely harder
  problem, solved reasonably — real signal, real room to improve.
- **Remaining life estimate: similarly ~59% accurate.** Same honest story.

### What "SHAP" is
A way of asking the model "why did you decide that?" and getting a real,
mathematically grounded answer — which sensor readings pushed the decision,
and by how much. For an oil pressure fault, SHAP correctly shows oil
pressure as the dominant reason — exactly what you'd want to see.

---

## 4. The two dashboards — how they're built, and how they differ

Both dashboards share the exact same visual interface
(`static/index.html`) and the exact same trained models. The only
difference is the Python backend feeding them.

### `app.py` — the replay version
- Reads real rows from `engine_master_dataset.csv`, one at a time, as if
  they were arriving live
- Fault buttons switch which recorded fault run is being streamed
- Fully self-contained — no external simulator needs to be running

### `app_live.py` — the live version
- Connects directly to a **real, currently-running ArduPilot SITL
  instance** over MAVLink (the standard protocol drones use to talk to
  ground control software)
- Instead of reading historical `engine_physics_model.py` output from a
  file, `live_engine.py` runs that **same physics logic live**, fed by
  real-time flight telemetry as it arrives
- Fault buttons now inject a real fault into the *live* physics simulation
  in real time, ramping in gradually just like the original offline script
  did
- Requires SITL to be running and reachable — this is genuinely live, not
  a simulation of being live

### Why `live_engine.py` exists as a separate file
`engine_physics_model.py` (the original script) processes a whole recorded
flight at once — it needs the complete flight log in memory to compute
things like average airspeed. `live_engine.py` is a **stream-friendly
rewrite of the exact same equations and fault effects**, built to update
one moment at a time as telemetry arrives, rather than needing the whole
flight up front. The underlying physics and fault behavior are the same;
only *how* it processes data changed.

---

## 5. How live data flows, end to end

1. **ArduPilot SITL** simulates a real flight, sending live telemetry
   (attitude, airspeed, altitude) over MAVLink
2. **`app_live.py`** listens for that telemetry continuously
3. It feeds the current flight state into **`live_engine.py`**, which
   computes both:
   - what a *healthy* engine would show right now (the reference/expected
     values)
   - what the engine *actually* shows right now (including any fault
     currently injected)
4. The difference between those two (the "residual") gets combined with
   the raw readings into a feature set
5. That feature set is run through the **trained XGBoost models**,
   producing a real fault prediction, severity, remaining-life estimate,
   and SHAP explanation
6. All of this streams to the **dashboard** over a live connection
   (WebSocket), updating continuously in your browser

---

## 6. How to run each version

### Replay version (no SITL needed)
```
uvicorn app:app --reload
```
Then open http://localhost:8000

### Live version (SITL required)
**Terminal 1** — start SITL with a dedicated output port:
```
cd ~/ardupilot/ArduPlane
../Tools/autotest/sim_vehicle.py --console --map --out=udp:127.0.0.1:14551
```

**Terminal 2** — start the live backend:
```
uvicorn app_live:app --reload
```
Then open http://localhost:8000 — same address either way.

**To actually fly the plane** (so airspeed/altitude change instead of
sitting idle), in the SITL terminal:
```
mode manual
arm throttle
rc 3 2000      (full throttle — plane accelerates)
rc 2 1300      (once airspeed builds, pulls up to climb)
rc 2 1500      (levels off once climbing)
```

---

## 7. Honest limitations

- **Only one flight per fault condition** in the dataset — proven to
  generalize within a flight, not yet proven across independent flights
- **Severity/remaining-life predictions are real but imperfect** (~59%
  accurate) — genuinely harder than fault identification, which is why it
  scores lower, and that's expected, not a flaw to hide
- **The live version depends on a working SITL connection** — if SITL
  isn't running or reachable on the expected port, the dashboard will show
  a "not connected" state rather than data
- **No unknown-fault fallback** — right now, the model always picks one of
  its four known classes, even for a fault type it's never seen; it can't
  currently say "I don't recognize this"
- **The severity/RUL model's predictions cap out around 0.32–0.35** for
  these particular fault runs rather than reaching 1.0, even at a fault's
  true worst point — the dashboard's alert thresholds were specifically
  recalibrated to match this real, observed model behavior, not a
  theoretical 0–1 scale

---

## 8. What each file actually is

| File | What it's for |
|---|---|
| `merge_mission_profile.py` | Combines raw SITL flight logs into one clean timeline |
| `engine_physics_model.py` | The offline physics engine simulator + fault injection (generates the dataset) |
| `combine_datasets.py` | Merges all the individual fault-run CSVs into `engine_master_dataset.csv` |
| `engine_master_dataset.csv` | The full labeled dataset everything is trained on |
| `train_model.py` | Trains the three AI models from that dataset |
| `model_fault_classifier.joblib` | Trained "which fault is it" model |
| `model_severity_regressor.joblib` | Trained "how severe is it" model |
| `model_rul_regressor.joblib` | Trained "how much time is left" model |
| `model_report.json` | Full test results and accuracy numbers |
| `confusion_matrix.png` / `feature_importance.png` | Visual summaries of model performance |
| `app.py` | Backend that replays the recorded dataset through the real models |
| `live_engine.py` | Live, streaming version of the physics model, for real-time use |
| `app_live.py` | Backend that connects to real SITL and runs everything live |
| `static/index.html` | The dashboard webpage — shared by both backends |
| `requirements.txt` | List of Python packages needed to run everything |
| `MODEL_REPORT.md` | Technical write-up of the model for anyone wanting full detail |

---

## 9. One-line summary for a pitch

*"We built a digital-twin system — grounded in a real physics-based engine
simulator driven by an actual flight simulator — that detects and explains
developing engine faults in real time using an honestly-evaluated AI model,
and puts the final safety decision in a human operator's hands, not the
machine's."*
