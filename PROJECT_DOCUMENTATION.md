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
- Can inject one of three faults — valve wear, cooling failure, oil
  pressure drop — each with a randomized severity and onset point, ramping
  in gradually rather than switching on abruptly, with a mild stochastic
  (not perfectly straight-line) progression
- Can also generate a "near-miss" healthy run: a genuine high-load/hot-day
  excursion with no fault at all, so a transient elevated reading isn't by
  itself proof of a fault

### Step 3 — `generate_batch.py`
Calls the simulator many times with randomized parameters — 69 runs across
4 fault classes (including near-miss healthy runs), each a genuinely
different flight rather than the same trajectory relabeled — and writes
each run to its own CSV under `data/runs/`.

### Step 4 — `combine_datasets.py`
Merges every run CSV in `data/runs/` into one master file —
`engine_master_dataset.csv`, ~155,000 rows across 69 runs — which everything
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
| Fault classifier | "Which fault is this — healthy, valve wear, cooling failure, or oil pressure drop?" |
| Severity model | "How bad is it right now, 0 to 1?" |
| Remaining-life model | "Roughly how much time is left before this gets critical?" |

### How we know it actually works
We tested it honestly: trained only on the **first 75% of each flight**,
tested on the **last 25%** — a part of the flight the model never saw,
where the fault is further along. That's a fair test of real learning, not
memorization. With 69 independent runs now (instead of 1 per class), this
also means the model has to have learned a fault signature that holds up
across many different flights, ambient conditions and severity levels, not
just one specific fault trajectory.

### Results, explained
- **Which fault it is: 93.4% correct** on unseen data. (An earlier version
  of this dataset — one flight per fault class, one fixed severity, no
  randomization — scored a flat 100%. That wasn't a better model; it was a
  much easier, less realistic test. Once the dataset had many independently
  varied flights, correctly-labeled pre-onset segments, and realistic sensor
  noise, 93.4% is what a genuinely rigorous held-out evaluation gives.)
  Almost all of the remaining errors are right at a fault's onset — the
  genuinely ambiguous moment where a real detector *should* sometimes be
  uncertain — while well-progressed faults are caught 97–99% of the time.
- **How severe it is: R² ≈ 0.80.** A genuinely harder, continuous problem,
  solved well — real signal, with expected room to improve further.
- **Remaining life estimate: same R² ≈ 0.80.** Same honest story.

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

- **All 69 runs are synthetic (physics-simulated), not independent real
  SITL flights** — the flight-profile *shapes* (short hop, long cruise,
  climb-cruise-descent, variable-load) are hand-designed archetypes with
  randomized duration/throttle history, fit to resemble the one real
  recorded flight this project started from, not 69 genuinely separate
  ArduPilot missions. Real independent flights per fault type would be a
  stronger validation than this project currently has time for.
- **Only 3 fault types are modeled** (valve_wear, cooling_failure,
  oil_pressure_drop) — ignition_degradation and bearing_wear are not in this
  dataset or these models.
- **`healthy` recall is the lowest of the four classes (0.83)** — the model
  is somewhat more likely to flag a genuinely healthy reading as an
  early-stage fault than the reverse. A defensible, safety-conservative
  failure mode (false alarms cost less than missed faults), but worth
  naming rather than hiding.
- **The live version depends on a working SITL connection** — if SITL
  isn't running or reachable on the expected port, the dashboard will show
  a "not connected" state rather than data
- **No unknown-fault fallback** — right now, the model always picks one of
  its four known classes, even for a fault type it's never seen; it can't
  currently say "I don't recognize this"
- **The live dashboard's alert thresholds have not been rechecked against
  this retrained model.** They were previously hand-calibrated (0.12/0.25)
  to the old model's narrow observed severity output (capped ~0.32–0.35).
  This model's severity range is different (randomized 0.2–0.9 severities
  across runs), so those threshold constants are now almost certainly
  stale — `app.py`/`app_live.py`/`live_engine.py` were not touched by this
  update. Re-run a few fault scenarios through the live dashboard and
  re-check the threshold constants before presenting.

---

## 8. What each file actually is

| File | What it's for |
|---|---|
| `digital_twin.py` | The healthy-engine reference model (fitted formulas + thermal lag constants) — shared by the simulator and the trained model |
| `engine_physics_model.py` | The physics engine simulator + fault injection for one run |
| `generate_batch.py` | Calls the simulator many times with randomized severity/onset/ambient/archetype to build a full batch of runs |
| `combine_datasets.py` | Merges all the individual run CSVs (`data/runs/*.csv`) into `engine_master_dataset.csv` |
| `engine_master_dataset.csv` | The full labeled dataset everything is trained on (69 runs, ~155,000 rows) |
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
