# A. Digital Twin Core Framework — modular architecture argument

**Owner:** Rijul — Documentation & Architecture  
**Audience:** judges and engineers  
**Baseline:** `main` at `11986638fd5f56beb4b1e38cacdbeb388704dc85`, reviewed 19 September 2026.

The exact official text of Section A has not been supplied. This document interprets the supplied title and modularity objective; it is not a statement of verified compliance with unseen criteria.

## 1. The claim we can defend

> The prototype separates engine simulation, model artifacts, telemetry presentation and mission analytics into identifiable components. Replay and live backends emit the same payload shape, and the reporting module serves both dashboard implementations. These are practical foundations for modularity. Explicit adapter contracts, a shared inference service and isolated per-engine state are the next steps needed to demonstrate reliable component replacement and fleet scaling.

Modularity means a component can change behind a defined interface with known effects on its consumers. Multiple files alone do not prove it. Our argument is strongest when each claimed boundary has source evidence, a compatibility rule and a replacement test.

## 2. Current architecture and evidence

```text
CSV runs + aligned healthy baseline       MAVLink ATTITUDE / VFR_HUD
                |                                     |
             app.py                         app_live.py + LiveEngine
                |                                     |
     features + predict_row                 features + predict_row
                |                                     |
                +---- common payload shape over /ws --+
                                      |
                     React UI or static/index.html
                                      |
                  missionAnalytics.js / synchronized copy
                                      |
                trends, maintenance advice, mission exports
```

The two `predict_row` implementations are duplicated, not calls into an existing shared service. The two modes are separate processes/entry points, not a hot-swappable runtime adapter framework.

| Boundary | Source evidence | What it proves | Current limit |
|---|---|---|---|
| Live engine simulation | [`LiveEngine`](../live_engine.py) accepts flight state and returns dictionaries | Engine simulation can be invoked apart from HTTP/UI | Hardcoded coefficients; expected and simulated actual share one class; actual begins as a copy of expected |
| Saved predictor artifacts | `model_* .joblib` files, [`train_model.py`](../train_model.py) | Training outputs are loaded separately from UI code | Format/version/feature/class compatibility is not enforced by a manifest |
| Telemetry presentation | [`app.py`](../app.py), [`app_live.py`](../app_live.py) `/ws` payloads | UI depends on data fields rather than engine equations | No versioned schema; simulator fields and identity omissions limit production use |
| Dashboard components | [`frontend/src/components`](../frontend/src/components) | Sensors, diagnosis, controls and reports have separate UI responsibilities | Some decisions and thresholds live in the browser |
| Reusable mission analytics | [`missionAnalytics.js`](../frontend/src/lib/missionAnalytics.js), [`sync_dashboard_analytics.py`](../scripts/sync_dashboard_analytics.py) | Shared reporting logic has an explicit synchronization route to static UI | Static code is an inlined copy; sync must be checked and reports lack a durable backend source of truth |
| Replay vs live inputs | CSV selection versus MAVLink plus engine simulation | Two source paths can serve the same UI shape | Acquisition, feature construction, prediction and transport remain coupled within each backend |

Current state is global to each backend process. Multiple connected browsers observe the same simulated engine. More browser connections do not equal multiple independently monitored aircraft.

## 3. Proposed component contracts

The following are **design interfaces**, not implemented classes. Initially they may run inside one process; separate network services are optional deployment choices, not a requirement for modularity.

| Component | Input → output | State ownership | Replacement rule |
|---|---|---|---|
| `TelemetrySource` | Replay/MAVLink/ECU bytes → source samples | Connection, source clock, sequence | Decode vendor fields without changing downstream sensor meaning |
| `Normalizer` | Source samples + calibration → `EngineObservation` | Timing buffers and quality checks per source | Preserve units, timing, origin and invalid/missing markers |
| `HealthyTwin` | Flight/engine operating context + engine configuration → `ExpectedState` | Thermal/dynamic state per engine | Maintain sensor meaning and validated operating envelope; no fault label input |
| `FeatureBuilder` | Measured + expected + history → `FeatureVector` | Per-engine rolling history | Exact named order, cadence, window definitions and feature version |
| `Predictor` | Feature vector + artifact → `DiagnosticResult` | Loaded model and declared history if needed | Output labels/units/uncertainty/quality meet contract; validate or retrain |
| `AdvisoryPolicy` | Diagnosis + quality + policy configuration → advisory | Persistence, escalation and acknowledgement per engine/mission | Changes are versioned and tested separately from classifier outputs |
| `MissionRecorder` | Observations, predictions and actions → durable events/reports | Per-mission ordering and summaries | Storage implementation preserves event identity and audit history |
| `TelemetryPublisher` | Validated events → UI/ground/fleet consumers | Bounded delivery buffers per consumer | Transport changes do not stall acquisition or alter source values |

Split `LiveEngine` into a healthy predictor and a simulation-only faultable plant before real acquisition replaces synthetic actual sensors. On real hardware, observed sensors must originate from acquisition, not be derived from the reference twin. Calibrate the reference using healthy engine data and define startup/warm-up behavior.

## 4. Proposed data contract

Current messages contain `t_sec`, `sensors`, `expected`, `altitude`, `prediction`, simulator truth and a confirmation flag. The new contract should wrap comparable content with explicit identity and provenance, retaining a UI compatibility adapter during migration.

| Record | Required fields and semantics |
|---|---|
| Envelope | `schema_version`, `aircraft_id`, `engine_id`, `mission_id`, `boot_id`, `sequence`, source mode, acquisition/arrival timestamps and clock quality |
| Observation | Channel values, units, source, acquisition age and quality; distinguish measured, simulated and estimated values |
| Expected state | Twin/configuration versions, expected channels, envelope/warm-up status |
| Features | Ordered names, feature schema/version, sample cadence, missing-input policy; simulator truth excluded |
| Diagnosis | Model version, label vocabulary, score/confidence semantics, severity definition, RUL representation, explanation type and validity |
| Advisory/action | Advisory ID/revision, policy version, affected mission/engine, operator identity, acknowledgement and timestamp |
| Ground truth | Optional evaluation-only metadata; absent on real flights; never an input to diagnosis |

Illustrative design fragment, not a currently accepted API:

```json
{
  "schema_version": "2.0",
  "aircraft_id": "test-aircraft-01",
  "engine_id": "test-engine-01",
  "mission_id": "bench-run-001",
  "boot_id": "session-001",
  "sequence": 42,
  "source_mode": "bench",
  "channels": {
    "oil_pressure": {"value": 3.1, "unit": "bar", "quality": "valid"},
    "injection_start_angle": {"value": null, "unit": "deg", "quality": "unsupported"}
  }
}
```

Add source timestamps and channel age before implementing the full contract. Null is distinct from a measured zero. Missing required features make the diagnosis unavailable unless the model has a validated missing-data strategy. Scores from different models are not comparable merely because they share a `confidence` field.

The current UI converts a RUL fraction to seconds using a multiplier of 160. The proposed interface must identify whether RUL is a fraction, a calibrated duration or unavailable, along with its horizon and validation basis. Replacing the estimator cannot silently reinterpret those units.

## 5. How components can be swapped

| Change | What stays stable | What changes | Proof required |
|---|---|---|---|
| CSV replay → ECU acquisition | Canonical observation consumer and UI schema | Source adapter, decoding/time alignment, calibration | Identical canonical fixture produces equivalent downstream behavior; real-engine validation remains separate |
| One engine family → another | Twin interface and dashboard field semantics where applicable | Engine parameters/equations, sensor map, healthy calibration, likely ML model | Healthy/fault tests on that engine's operating envelope; no claim of zero-calibration portability |
| XGBoost → different predictor | Diagnosis output contract | Artifact loader, features if needed, explanation adapter | Class/units compatibility, independent evaluation, latency and missing-data tests |
| React → another ground UI | Versioned published events | Presentation layer | Contract replay renders equivalent values, quality flags and actions |
| Browser reports → durable recorder | Report/event meaning | Backend event store, retrieval/export API and synchronization | Refresh/restart loses no committed records; two clients obtain the same report |
| One engine → multiple engines | Same interfaces per engine | State registry, routing, persistence and capacity | Interleaved streams equal isolated-run results and have independent fault/control state |

Replacement sometimes requires calibration or retraining. Modularity limits the location and impact of those changes; it does not remove the need for validation. A replacement model without SHAP must identify its actual explanation method, not retain a misleading label.

## 6. Scaling argument and deployment shape

```text
Per-engine path (one isolated instance for each engine)
source → normalization → healthy twin → features → diagnosis → advisory
              |                                      |
              +----------- durable recorder ---------+
                                                     |
                                          bounded publication
                                                     |
                                 ground UI + fleet ingestion
```

Start with explicit per-engine objects inside one process. Move independent engine workloads to workers only when measurements justify it. Thermal state and rolling features make the processing stateful: use engine-affine routing and either validated checkpoints or declared warm-up after a worker restart. Blind load balancing between arbitrary workers would lose this state.

Bound each queue; assign overload policy by stream type. A latest-value UI queue may discard intermediate samples, while the recorder must declare any evidence loss and prioritize significant events. Slow consumers cannot block source acquisition. Separate current-state queries from historical report queries as load grows.

Fleet capacity is an acceptance result, not a function of having a WebSocket broadcast loop. The [deployment roadmap](DEPLOYMENT_ROADMAP.md#8-fleet-level-monitoring-vision) defines progressive multi-engine tests and illustrative sizing. Browser-only state, sequential broadcast and processing tied to connected viewers must be addressed first.

## 7. Refactoring sequence and verification

1. Freeze fixtures for existing input/features/output, record artifact compatibility and document current behavior.
2. Extract the common feature builder, class mapping and predictor into shared modules; check numerical parity for both backends.
3. Introduce source adapters and a canonical schema with invalid/stale handling. Keep old dashboard compatibility during transition.
4. Split simulated actual sensors from healthy reference prediction and move engine-specific configuration out of globals.
5. Create per-engine session state, backend advisory/recording services, then keep React/static reports consistent with that source of truth.
6. Add secure publication and deployment packaging; measure resource needs before distributing workers.

Meaningful modularity tests should include an adapter replacement with equivalent fixtures, two interleaved engines with different faults, a schema/model mismatch that fails explicitly, a delayed or missing channel, a model replacement with changed label ordering, and a disconnected viewer that does not stop the recorder.

For a convincing judge demonstration, show two adapters driving the same core/UI, then two engines whose state cannot contaminate each other. Until implemented and tested, describe these as acceptance demonstrations to build.

## 8. Judge questions and precise answers

**What is modular today?** The live engine class, separately loaded ML artifacts, common backend payload shape, UI components and shared mission-reporting module are visible boundaries in source.

**Can any engine or FADEC be connected immediately?** No. Vendor decoding and engine calibration are prerequisites. The proposed adapter and engine-configuration boundaries confine those changes.

**Can the ML model be replaced?** The artifact boundary makes replacement possible, but feature order, label vocabulary, units, explanation semantics and performance must be validated. The current code lacks a formal compatibility manifest.

**Is fleet scaling already demonstrated?** No. Current backend globals represent one shared session. Per-engine state isolation, durable ingestion and capacity tests are planned.

**Does the monitor control injection timing?** No. Injection timing is acknowledged future telemetry/model scope. The current fault-injection UI is unrelated to injector timing control.

**What is the engineering value of this architecture?** An adapter, engine model, predictor or interface can evolve with a defined compatibility boundary and focused validation, reducing the amount of unrelated code that must change. The test plan turns that design claim into evidence.

## 9. Claim limits

This source review establishes structural evidence, not a fresh runtime qualification. Current shared state, duplicated inference and unversioned messages prevent a claim of fully interchangeable production modules. Engine equations and synthetic faults need calibration; existing chronological test scores do not establish performance across unseen engines or missions. No old-checkout metrics or features are carried into this claim.
