# Deployment roadmap

*DRDO's problem statement lists "technical documentation and deployment
roadmap" under Deliverables Expected from Teams. This is that roadmap,
written to be read by a judge deciding whether this is a demo or a genuine
path to a fielded capability. It is deliberately honest about what's built
versus aspirational — a roadmap that overstates current readiness is not
useful to anyone who has to act on it.*

## Current state: a simulation-validated software prototype

What exists today, precisely:

- A **ground-station software prototype** (`app.py` / `app_live.py` +
  `static/index.html`, or the equivalent `frontend/` React app) — runs on a
  laptop, not on any drone or embedded hardware.
- Trained entirely on **physics-simulated telemetry** (`engine_physics_model.py`
  → `engine_master_dataset.csv`), not real engine sensor data. The physics
  model's coefficients are fit from one real recorded SITL flight (see
  `digital_twin.py`), so it's grounded, but it is still a simulation, not a
  test-rig-validated model.
- The "live" version (`app_live.py`) is driven by **ArduPilot SITL**
  (a real autopilot, running in simulation) over MAVLink for flight
  state (airspeed, altitude, attitude) — the *engine* itself is still the
  physics simulator (`live_engine.py`), not a real or simulated ECU/engine
  test rig. SITL provides realistic flight dynamics; it does not provide a
  real piston engine.
- Inference is a set of 3 trained XGBoost models (~1–5 MB `.joblib` files),
  currently loaded and run in a Python process on a regular laptop/desktop.
  No embedded/onboard deployment, quantization, or real-time OS integration
  has been attempted.
- Human-in-the-loop is real in the software sense (the dashboard requires
  operator confirmation before any simulated response is "sent", and in
  `app_live.py`'s case, a small number of confirmations do send genuine
  MAVLink commands to the SITL aircraft — throttle overrides, RTL, a GUIDED
  altitude command). None of this has been exercised against a real
  aircraft's flight controller or a real engine ECU.

**What this means plainly**: this is a credible, physically-grounded
proof-of-concept for the detection *algorithm* and the *human-in-the-loop
decision-support pattern*. It is not yet validated against any real sensor,
any real engine, or any real GCS hardware. The gap between "detects a
physics-simulated fault correctly" and "detects a real fault on a real
engine" is the entire subject of this roadmap.

## What would need to change for real sensor/CAN-bus/ECU integration

Today, every number in the pipeline (`rpm`, `egt_c`, `cht_c`, `oil_temp_c`,
`oil_pressure_bar`, `fuel_flow_lph`, `vibration`) comes from
`engine_physics_model.py`'s simulation, not a wire. Replacing that with real
telemetry means:

1. **A real telemetry ingestion layer.** Most piston aero-engine ECUs (and
   many UAV engine controllers) expose sensor data over **CAN bus** (e.g.
   J1939-style framing, or a manufacturer-specific CAN protocol) or a serial/
   UART link, not MAVLink — MAVLink here currently carries only *flight
   state* (from SITL), not engine data. `app_live.py`'s `drain_mavlink()`
   would need a sibling function reading and decoding real CAN/serial engine
   frames, mapped to the same 7 sensor names the model expects. This is a
   real, nontrivial integration task specific to whichever engine/ECU is
   chosen (Rotax 912-class ECUs, UAVEngine, or similar all have their own
   framing) — we have not started this and do not have real hardware to
   test it against yet.
2. **Recomputing (or replacing) the digital-twin reference model.**
   `digital_twin.py`'s formulas were fit by regression against one real
   recorded flight of the physics simulator's *own* generated healthy data —
   they encode this project's simulated engine's behavior, not necessarily a
   specific real engine's. Before this reference model can be trusted
   against real sensor data, it needs to be **re-fit against real healthy
   telemetry from the actual target engine** (same regression methodology,
   different, real input data). This is expected, planned work — not a
   flaw in the current approach, just an explicit prerequisite we're naming
   rather than skipping past.
3. **Retraining on real (or real+synthetic-augmented) fault data.** The
   current models have never seen a real fault of any kind. A real
   deployment needs either (a) real fault-injection test-rig data per fault
   class, or (b) a validated argument that the physics-simulated faults
   transfer to real sensor behavior closely enough to deploy directly, with
   a defined validation gate before doing so (see Phase 2 below). We are not
   assuming (b) without evidence.
4. **Sensor fault handling gets more important, not less, with real
   hardware.** Real ADC noise, connector corrosion, and wiring faults are
   exactly what `sensor_fault` (this revision's new class) is meant to
   generalize toward — but it was designed and validated against simulated
   sensor corruption only. Real-world validation of this specific class is
   one of the highest-value, most concrete next steps once real hardware
   exists.

## Phased plan

### Phase 1 — Simulation-validated prototype *(current, complete)*
- Physics-based digital twin (`digital_twin.py`) fit to one real recorded
  flight.
- 8-class fault simulation and labeled dataset generation
  (`engine_physics_model.py`, `generate_batch.py`, `combine_datasets.py`).
- Trained XGBoost fault classifier + severity/RUL regressors
  (`train_model.py`), honestly evaluated on a per-run chronological
  held-out split (see `MODEL_REPORT.md`).
- Two working dashboards: dataset replay (`app.py`) and SITL-flight-state-
  driven live simulation (`app_live.py`), both with human-in-the-loop
  confirmation before any action.
- **Status: done.** This is what can be demoed today.

### Phase 2 — Hardware-in-the-loop (HIL) testing with a real sensor rig
- Instrument a real Rotax-912-class (or equivalent) piston engine test rig
  with the same 7 sensor channels, feeding real telemetry into
  `digital_twin.py`'s reference-fitting process and then into the trained
  pipeline.
- Re-fit `digital_twin.py`'s coefficients against real healthy-engine
  telemetry from the rig (same regression methodology as today, real data
  instead of simulated).
- Run **controlled real fault injection** on the rig for at least the 3
  mechanically straightforward classes (`valve_wear`-equivalent via induced
  compression loss, `cooling_failure` via restricted airflow,
  `oil_pressure_drop` via a controlled bleed) to get a first real-fault
  validation signal — the electrical/instrumentation classes
  (`sensor_fault`) and the harder combustion classes (`misfire`,
  `injector_fault`, `combustion_instability`) are more involved to inject
  safely and would follow once the rig and process are proven.
- Explicit go/no-go gate: **do not treat Phase 1's simulated-fault accuracy
  numbers as predictive of real-fault performance until this phase produces
  real comparison data.** This is the single most important honesty point
  in this whole roadmap.
- **Status: not started.** Needs physical test-rig access, instrumentation
  budget, and safety sign-off for fault injection on real hardware — outside
  what a software team can schedule or commit to unilaterally.

### Phase 3 — Edge deployment considerations (onboard inference)
- The current models are small (`model_fault_classifier.joblib` is a few MB;
  the two regressors under 1 MB each) and XGBoost inference is fast, but
  none of this has been profiled or adapted for an embedded/onboard target
  (a companion computer, not a ground station) yet. Concrete steps this
  would require:
  - **Quantization/pruning of the XGBoost models** — reducing tree
    count/depth and/or converting to a lower-precision representation to
    shrink memory footprint and inference latency for an embedded ARM
    target; XGBoost supports exporting to ONNX, which has established
    embedded-inference toolchains.
  - **Replacing the Python/FastAPI stack** with a lightweight embedded
    inference runtime (e.g. a C++/ONNX-Runtime or TFLite-equivalent
    pipeline) — the current `app_live.py`/`live_engine.py` pipeline is a
    ground-station prototype, not designed for onboard resource constraints.
  - **Feature computation onboard**: the 2.5s trailing-mean smoothing and
    digital-twin residual computation (`digital_twin.reference_trajectory()`)
    would need a lightweight onboard implementation — algorithmically simple
    (already streaming-friendly, see `live_engine.py`'s design), but needs
    porting off Python/NumPy for a real embedded target.
  - **Deciding what stays onboard vs. on the ground.** A reasonable split:
    feature computation + inference onboard (low latency, works without a
    live downlink), with the human-in-the-loop confirmation step and
    detailed SHAP explanation still surfaced at the ground station over the
    existing telemetry link — onboard autonomy for *detection*, not for
    *action*, consistent with this project's existing human-in-the-loop
    principle.
- **Status: not started; architecturally straightforward, not yet
  attempted.** This is realistic near-term work once Phase 2 provides a
  real-data-validated model worth deploying onboard.

### Phase 4 — Fleet-level aggregation
- Once multiple aircraft/engines are instrumented and streaming (even just
  to a ground station, not necessarily onboard-autonomous per Phase 3), the
  natural next step is aggregating detections and trends across the fleet:
  cross-aircraft comparison of the same fault signature, fleet-wide
  maintenance scheduling driven by RUL trends rather than fixed intervals,
  and using fleet data to retrain/re-validate the models on real (not
  simulated) fault progressions at scale — directly addressing this
  project's own honestly-stated limitation that today's dataset has only a
  handful of runs per class from one simulated engine.
- This phase depends entirely on Phases 1–3 existing first (real,
  validated, per-aircraft detection) and on organizational/data-pipeline
  decisions (where fleet data is stored, who has access, aggregation
  cadence) that are outside this project's current scope to specify in
  detail.
- **Status: aspirational.** Named here because DRDO's PS deliverables ask
  for a full roadmap, not because we have designed this phase in any
  concrete way yet.

## Summary table

| Phase | What it needs | What's actually done today |
|---|---|---|
| 1. Simulation-validated prototype | — | **Complete** |
| 2. Hardware-in-the-loop testing | Real test rig, sensor instrumentation, safety-approved fault injection | Not started |
| 3. Edge deployment | Phase 2's validated model, embedded toolchain, porting effort | Not started (architecturally clear) |
| 4. Fleet aggregation | Multiple instrumented aircraft, real fleet data, Phases 1–3 | Not started (roadmap-level only) |

**The honest bottom line**: what's demoable today is a real, physically-
grounded, honestly-evaluated *algorithm and decision-support pattern* — not
a fielded system, and not yet validated against a single real engine sensor.
Every phase past Phase 1 requires resources (hardware, lab access, and in
Phase 2's case, safety sign-off for fault injection) that a software-only
team building for a hackathon/demo timeline does not have, and we are not
claiming otherwise.
