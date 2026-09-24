# UAV engine digital twin — complete project documentation

*Written in plain language for anyone on the team, or a judge, who isn't
deep into machine learning or software engineering. This is the full,
up-to-date picture of everything that's been built.*

---

## 1. What this project actually does

Imagine a drone (a MALE UAV — a military-grade long-endurance drone) with a
piston engine, like a small motorcycle engine, keeping it in the air for
hours. If something starts going wrong with that engine mid-flight — oil
pressure drops, it overheats, a valve wears out, a cylinder starts
misfiring, a fuel injector starts dribbling, combustion turns rough, or even
a sensor itself starts lying — you want to know *before* it fails, not
after. DRDO's problem statement (section C, "Fault Detection & Predictive
Analytics") names all of these explicitly; this project now detects all of
them, not just the original three.

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

### Step 1 — `digital_twin.py`: the healthy reference model
The physical core of the whole project. It's a small set of formulas — fit
by regression against a real recorded healthy flight — for what every
sensor (RPM, EGT, CHT, oil temp, oil pressure, fuel flow, vibration) *should*
read at a given throttle/load setting and ambient temperature, plus the
first-order thermal/mechanical time constants for how fast each sensor
actually catches up to a throttle change (CHT and oil temperature lag
seconds-to-tens-of-seconds behind a change, the way real metal thermal mass
behaves; RPM and oil pressure track almost immediately). Both the simulator
(step 2) and the trained model (step 4) use this exact same reference — the
simulator adds noise and faults on top of it to generate telemetry, and the
trained model subtracts it from real telemetry to compute "how far off from
expected is this reading" (the residual), so there's only one healthy-engine
model to keep consistent, not two.

### Step 2 — `engine_physics_model.py`: the simulator
Takes a randomized flight profile (one of 4 archetypes — short hop, long
cruise, climb-cruise-descent, variable-load — each with randomized duration
and a smoothly-varying throttle/airspeed history) and, using the reference
model from step 1, generates one run's full sensor telemetry:
- Applies the thermal/mechanical lag from step 1, so a throttle change
  produces a realistic gradual sensor response, not an instant jump
- Adds sensor noise, moderately increased over what was actually measured
  on the original recorded flight (reflecting that a single clean flight
  understates real fleet sensor variability)
- Can inject one of seven faults — valve wear, cooling failure, oil pressure
  drop, misfire, injector fault, combustion instability, or sensor fault —
  each with a randomized severity and onset point. The original three ramp
  in as a gradual offset from the healthy target; the four newer ones each
  needed their own mechanism instead (see `MODEL_REPORT.md` for the full
  physical reasoning): misfire is scheduled short, complete-combustion-loss
  events whose frequency and size both grow with severity; injector fault
  adds an irregular fuel-flow wobble on top of a rich-mixture bias;
  combustion instability inflates sensor *noise* rather than shifting the
  mean; sensor fault corrupts one randomly chosen sensor channel's *reported
  reading* only, leaving the engine itself genuinely healthy
- Can also generate a "near-miss" healthy run: a genuine high-load/hot-day
  excursion with no fault at all, so a transient elevated reading isn't by
  itself proof of a fault

### Step 3 — `generate_batch.py`
Calls the simulator many times with randomized parameters — 73 runs across
8 classes (7 faults + healthy, including near-miss healthy runs), each a
genuinely different flight rather than the same trajectory relabeled — and
writes each run to its own CSV under `data/runs/`.

### Step 4 — `combine_datasets.py`
Merges every run CSV in `data/runs/` into one master file —
`engine_master_dataset.csv`, ~164,000 rows across 73 runs — which everything
else in the project is built on.

**Why this matters**: this means the "expected" sensor values used
throughout this project aren't guesses — they're the output of a genuine
physics model your team built (with coefficients fit from real recorded
flight data, not invented), and the dataset it drives now spans dozens of
independently-varied flights instead of one. That's a real, defensible
foundation, not a shortcut.

*(Earlier project notes described a `merge_mission_profile.py` step that
consumed raw multi-file ArduPilot SITL logs directly. That script was never
actually part of the committed pipeline — verified by checking every branch
and fork on GitHub, not just the local checkout. The 4 flight-profile
archetypes above replace it: same role (turning a flight timeline into the
load/airspeed history the physics model needs), generated programmatically
with randomized variety instead of requiring a fresh SITL flight per run.)*

---

## 3. The AI model — explained without jargon

We used **XGBoost**, a well-established, trusted type of machine learning
model — think of it as thousands of small decision trees ("is oil pressure
below X? is temperature above Y?") combining their answers into one
confident prediction.

Three separate models were trained:

| Model | Question it answers |
|---|---|
| Fault classifier | "Which fault is this — healthy, valve wear, cooling failure, oil pressure drop, misfire, injector fault, combustion instability, or sensor fault?" |
| Severity model | "How bad is it right now, 0 to 1?" |
| Remaining-life model | "Roughly how much time is left before this gets critical?" |

### How we know it actually works
We tested it honestly: trained only on the **first 75% of each flight**,
tested on the **last 25%** — a part of the flight the model never saw,
where the fault is further along. That's a fair test of real learning, not
memorization. With 73 independent runs across all 8 classes, this also
means the model has to have learned a fault signature that holds up across
many different flights, ambient conditions and severity levels, not just
one specific fault trajectory.

### Results, explained — now with 4 more, genuinely harder, fault classes
- **Which fault it is: 66.1% correct overall** across all 8 classes (down
  from 93.4% on the original 4-class problem, reported honestly rather than
  only showing the easier number). The original 3 fault classes are
  essentially untouched by adding 4 more — `cooling_failure`,
  `oil_pressure_drop` and `valve_wear` all still sit at 97–100% recall.
  **All of the accuracy drop is concentrated in the 4 new classes**, and it
  was expected: `misfire`, `combustion_instability` and one sub-mode of
  `sensor_fault` were deliberately built to be physically hard to catch
  using only *mean* residual features (see `MODEL_REPORT.md` for the full,
  honest breakdown, including exactly which classes are hardest and why).
  `injector_fault`, which we deliberately gave a clean directional signal
  (fuel up, EGT *down* — the opposite direction from valve wear), is caught
  correctly 59% of the time — proof the approach works when a fault has a
  genuine mean-residual signature, and a useful contrast with the classes
  that don't.
- **How severe it is: this has gotten meaningfully worse with 8 classes**
  (overall R² collapsed to 0.02, from 0.80 on the 3-fault version) — see
  `MODEL_REPORT.md`'s "Severity/RUL regression has effectively collapsed"
  section for the full explanation and per-class numbers. Short version: one
  regressor predicting a single 0–1 "severity" from 7 mean-residual features
  no longer works well once the fault classes stop sharing a comparable
  physical meaning of "severity" — this is real, useful information about
  where the current design breaks, not something we're hiding.
- **Remaining life estimate: same story as severity** (they're the same
  number under the hood — `rul_frac` is defined as `1 − fault_severity`).

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

- **All 73 runs are synthetic (physics-simulated), not independent real
  SITL flights** — the flight-profile *shapes* (short hop, long cruise,
  climb-cruise-descent, variable-load) are hand-designed archetypes with
  randomized duration/throttle history, fit to resemble the one real
  recorded flight this project started from, not 73 genuinely separate
  ArduPilot missions. Real independent flights per fault type would be a
  stronger validation than this project currently has time for.
- **`combustion_instability` and the "noisy" sub-mode of `sensor_fault` are,
  on the current feature set, closer to "mostly not detected" than
  "detected imperfectly"** (25% and 16% recall respectively, both mostly
  confused with "healthy," not with each other). Both were deliberately
  built to manifest as increased sensor-reading *variance* rather than a
  mean shift, and this project's features are a smoothed *mean* residual
  only — there is no variance/spectral feature in the model. This is a
  genuine limitation of the current feature set, not a training or
  labeling bug; see `MODEL_REPORT.md` for the full analysis.
- **Severity/RUL should not be presented as reliable across all 8 classes.**
  It's still meaningful for `cooling_failure`/`oil_pressure_drop`/
  `valve_wear`; pooling one 0–1 regression target across all 7 fault
  mechanisms broke down once they stopped sharing a comparable physical
  notion of "severity" (see `MODEL_REPORT.md`).
- **`healthy` recall (0.77) is the lowest of the 8 classes**, and dropped
  further from the 3-fault version's 0.83 — largely because misfire/
  instability/noisy-sensor-fault rows that the model can't confidently place
  elsewhere often land on "healthy" instead. Still a safety-conservative
  direction to be wrong in one sense (no false engine-fault alarm), but a
  concerning one in another (a real developing fault reads as nothing
  wrong) — worth naming plainly rather than only citing the aggregate
  accuracy number.
- **The live version depends on a working SITL connection** — if SITL
  isn't running or reachable on the expected port, the dashboard will show
  a "not connected" state rather than data
- **No unknown-fault fallback** — right now, the model always picks one of
  its eight known classes, even for a fault type it's never seen; it can't
  currently say "I don't recognize this"
- **The live dashboard's alert thresholds have not been rechecked against
  this retrained model.** They were previously hand-calibrated (0.12/0.25)
  to an earlier model's narrower observed severity output, and flagged as
  likely stale even before this revision. This revision doesn't resolve
  that, and — per the point above — severity itself is now only reliable
  for 3 of 8 classes regardless of where the threshold ends up. See
  `MODEL_REPORT.md` for the exact locations of these constants.

---

## 8. What each file actually is

| File | What it's for |
|---|---|
| `digital_twin.py` | The healthy-engine reference model (fitted formulas + thermal lag constants) — shared by the simulator and the trained model |
| `engine_physics_model.py` | The physics engine simulator + fault injection for one run |
| `generate_batch.py` | Calls the simulator many times with randomized severity/onset/ambient/archetype to build a full batch of runs |
| `combine_datasets.py` | Merges all the individual run CSVs (`data/runs/*.csv`) into `engine_master_dataset.csv` |
| `engine_master_dataset.csv` | The full labeled dataset everything is trained on (73 runs, ~164,000 rows, 8 classes) |
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
| `RESEARCH_GROUNDING.md` | How this project's approach connects to prior published work (NASA C-MAPSS methodology, physics-informed/digital-twin fault diagnosis) |
| `DEPLOYMENT_ROADMAP.md` | Honest current-state-to-deployment plan: what's built vs. aspirational, phased path to real hardware |

---

## 9. One-line summary for a pitch

*"We built a digital-twin system — grounded in a real physics-based engine
simulator driven by an actual flight simulator — that detects and explains
developing engine faults in real time using an honestly-evaluated AI model,
and puts the final safety decision in a human operator's hands, not the
machine's."*
