# AegisTwin integration report: our prototype + Isha branch

**Problem statement:** SIH26054 — AI-enabled real-time digital twin for MALE UAV piston-engine health monitoring, fault prediction and mission reliability  
**Integration date:** 2 September 2026  
**Reference branch:** [aashna-boop/sih2026-uav-digital-twin — `isha`](https://github.com/aashna-boop/sih2026-uav-digital-twin/tree/isha)  
**Final working project:** `D:\Projects\SIH`

---

## 1. Executive summary

We did not replace our AegisTwin prototype with the Isha branch. We used the Isha implementation as a design and feature reference, then integrated its strongest ideas into our more rigorous plant/twin, data-generation, evaluation and mission-reliability architecture.

The most useful ideas taken from the Isha branch were:

- XGBoost fault classification
- genuine TreeSHAP explanations
- valve wear as an additional fault class
- a visible flight-path panel
- operator confirm/dismiss controls
- a ground-control event log
- model-evaluation artifacts such as a confusion matrix

Those ideas were reimplemented inside our existing architecture instead of copying the Isha runtime or trained artifacts. This was necessary because our system already had stronger profile-separated validation, calibrated RUL intervals, a strict faultable-plant/healthy-twin separation, a mission reliability envelope, and replay/SITL source failover.

The final result is a five-class, real-time digital-twin prototype with exact TreeSHAP evidence, human-in-the-loop acknowledgement, live ArduPilot flight state, automatic replay fallback, portable model artifacts and reproducible profile-separated metrics.

---

## 2. What each project contributed

### 2.1 Our original AegisTwin foundation

Before this integration, our project already contained:

- A faultable virtual piston-engine plant
- A separate healthy-engine digital twin that never receives fault inputs
- Context-normalized residuals between observed and expected sensor behavior
- Physical cooling, lubrication and ignition/misfire degradation
- A deterministic health score derived from residual evidence
- A physics-informed dataset generator with multiple flight profiles, ambient conditions, degradation rates and noise seeds
- Profile-level train/validation/test separation
- Three additional held-out profiles reserved for demonstrations
- A trained Random Forest fault classifier
- Calibrated quantile Remaining Useful Life models with lower and upper bounds
- Mission Reliability Envelope logic:

```text
mission safety margin = conservative RUL lower bound - safe recovery time
```

- Side-by-side conventional-threshold and digital-twin status
- FastAPI REST and WebSocket APIs
- A React dashboard
- Five-hertz deterministic replay
- A live ArduPilot/WSL telemetry adapter with replay fallback
- PowerShell launchers for replay and live operation

This architecture remained the backbone of the final project.

### 2.2 Strong ideas found in the Isha branch

The Isha branch demonstrated several useful presentation and modeling ideas:

- XGBoost was used for nonlinear multi-class engine-fault classification.
- Native TreeSHAP contributions were produced for individual predictions.
- Valve wear was included as a visible fault scenario.
- The dashboard contained a flight visualization, operator controls and an event log.
- Model reports and confusion-matrix/feature-importance images were provided.
- The documentation clearly explained the model, limitations and difference between replay and live operation.

These additions made the Isha branch visually convincing and easier to explain to a judge. We retained those strengths while changing their implementation to fit our data contract and reliability architecture.

---

## 3. Integration strategy

The strategy was:

```text
Keep our architecture and evaluation discipline
                +
Adopt the best model/UI ideas from the Isha branch
                +
Fix portability, feature-contract and live-failover risks
                =
Final AegisTwin prototype
```

The Isha checkout was kept in `team-isha/` during integration and has since been removed; it is
recoverable with `git clone -b isha https://github.com/aashna-boop/sih2026-uav-digital-twin.git`
(reviewed at commit `762934e`). Its tracked project files were not used as the production runtime
and its model artifacts were not copied into our model bundle. The team `main` frontend reviewed
for the design port is commit `2444f96` on the same remote.

---

## 4. Detailed integration matrix

| Area | Isha branch idea | What we implemented | Why this implementation is stronger |
|---|---|---|---|
| Classifier | XGBoost classifier | Replaced our Random Forest with a five-class XGBoost classifier | Retains fast tree inference and nonlinear interactions while using our profile-separated data |
| Explainability | Native per-row TreeSHAP | Exact native XGBoost `pred_contribs` TreeSHAP for every online diagnosis | Real signed model attribution without the external `shap` package's version-sensitive model parser |
| Valve fault | Valve-wear scenario | Added `VALVE_WEAR` to plant, dataset, classifier, fallback diagnosis, API, tests and dashboard | It is a complete vertical feature rather than only a UI option |
| Valve physics | Direct output perturbations in the live reference engine | Degraded volumetric and combustion efficiency, allowing effects to propagate into RPM, EGT, fuel demand, temperatures and vibration | A judge can trace the fault from a physical parameter to correlated sensors |
| Operator control | Confirm/dismiss buttons | Added a validated REST operator-response API and streamed acknowledgement state | Human action is represented in the system contract and not only in browser-local state |
| Operator safety | Confirmation visually changes the flight display | Confirmation is advisory-only and never changes or commands the aircraft | Avoids implying an unimplemented autonomous flight-control action |
| Flight display | Canvas-based flight visualization | Recent GPS ground-track SVG driven by real replay or ArduPilot coordinates | Displays the actual telemetry source rather than a synthetic post-confirmation glide |
| Event history | Browser event log | Ground-control audit log for connection, source, diagnosis, advisory, threshold, scenario and operator changes | Makes the demo sequence explainable and exposes system instability during testing |
| Model files | Pickled/joblib XGBoost estimators | Portable XGBoost UBJ classifier plus a versioned manifest; joblib is retained only for compatible RUL models and metadata | Reduces XGBoost serialization/version failures and records artifact provenance |
| Dependencies | Unpinned package list | Explicit XGBoost and scikit-learn versions plus complete backend requirements | Makes judge-laptop setup more deterministic |
| Evaluation visuals | Static confusion matrix and feature plot | Reproducible SVG confusion matrix generated directly from authoritative metrics | The visual cannot silently drift away from the current trained artifact |
| Runtime modes | Separate replay and live applications | One backend with a source adapter and automatic live-to-replay fallback | The dashboard and API remain available if SITL or venue networking fails |

---

## 5. Changes made in detail

### 5.1 Random Forest to XGBoost

#### What changed

The original AegisTwin Random Forest classifier was replaced with an `XGBClassifier`. The model now predicts five classes:

1. `HEALTHY`
2. `COOLING_DEGRADATION`
3. `IGNITION_MISFIRE`
4. `LUBRICATION_DEGRADATION`
5. `VALVE_WEAR`

The training pipeline uses class-balanced sample weights and profile-disjoint train, validation and test sets.

#### Why

XGBoost was one of the strongest technical choices in the Isha branch. Tree boosting is well suited to nonlinear interactions such as low oil pressure combined with rising oil temperature, flight load and vibration. It is also fast enough for online inference and supports native TreeSHAP.

#### How it improved the project

- Added real per-prediction explainability.
- Preserved low-latency inference.
- Expanded the classifier from four health/fault states to five.
- Kept the harder and more defensible profile-separated evaluation used by our project.
- Removed dependence on hand-written feature weights for the primary online diagnosis.

### 5.2 Exact native TreeSHAP explanations

#### What changed

For every inference frame, the XGBoost booster computes signed feature contributions using:

```python
booster.predict(matrix, pred_contribs=True, strict_shape=True)
```

The five largest absolute contributions are streamed with:

- feature name
- human-readable label
- current feature value
- signed SHAP value
- absolute magnitude
- explanation type (`tree_shap`)

#### Why

The Isha branch correctly moved away from hand-assigned explanation weights and used actual TreeSHAP. Its documentation also identified that the external `shap.TreeExplainer` can be sensitive to XGBoost serialization versions.

We therefore adopted the native XGBoost implementation, which calculates the same tree attribution inside the library that owns the model.

#### How it improved the project

- The explanation now reflects what the classifier actually used.
- Positive and negative evidence is visible separately on the dashboard.
- The external `shap` package is not required.
- If the ML model is unavailable, the system still has an honestly labelled normalized-residual explanation fallback.

### 5.3 Physically propagated valve wear

#### What changed

`VALVE_WEAR` was added throughout the vertical slice:

- fault command catalog
- engine plant
- generated dataset
- training labels
- inference output
- deterministic fallback classifier
- explanations
- scenario API
- dashboard controls
- automated tests

Inside the plant, valve wear reduces volumetric efficiency and combustion efficiency and introduces cyclic disturbance. These physical changes then affect several dependent channels:

- RPM decreases
- EGT increases
- fuel demand increases
- vibration increases
- smaller CHT and oil-temperature effects appear

#### Why

Valve wear was a useful Isha scenario and is relevant to piston-engine degradation. However, directly subtracting from RPM or adding to EGT at the final sensor-output layer would look like signal editing rather than a digital-twin plant fault.

#### How it improved the project

- Increased problem coverage without weakening the physics story.
- Created a distinct cross-sensor signature for classification.
- Gives the team a strong second demo after lubrication degradation.
- Makes the difference between a fault parameter and a sensor alarm easier to explain.

### 5.4 Human-in-the-loop operator acknowledgement

#### What changed

The dashboard now exposes `Confirm advisory` and `Dismiss` actions. These call:

```text
POST /api/operator-response
```

The backend validates the response, records its timestamp and streams the state in every analytics frame. The acknowledgement resets when:

- a new scenario is selected
- the mission is reset
- the recommendation changes

#### Why

The Isha interface correctly emphasized that a safety-critical system should keep the operator involved. We moved that state into the backend so it is part of the auditable system rather than a temporary browser variable.

#### How it improved the project

- Makes the human decision visible and testable.
- Prevents a confirmation for an older advisory from appearing valid after conditions change.
- Maintains an advisory-only boundary: no flight-control command is sent.
- Aligns with a realistic progression from decision support to future certified integration.

### 5.5 Ground-control event log

#### What changed

The dashboard records visible events for:

- WebSocket connection/disconnection
- replay/SITL source changes
- diagnosis changes
- mission recommendation changes
- conventional threshold alarm onset
- selected/injected scenarios
- operator confirmation/dismissal
- mission reset

#### Why

The Isha dashboard's log made the demonstration easier to follow. A live event history is also extremely useful during development because it reveals flickering classifications, source switching and repeated recommendations that may be missed in a single status card.

#### How it improved the project

The log directly exposed recommendation chatter during final visual testing. That led to a new mission-action hysteresis rule: once `ABORT_LAND` is reached, the recommendation cannot downgrade while a fault remains active.

The log therefore became both a presentation feature and a debugging instrument.

### 5.6 Flight-path visualization

#### What changed

A recent ground-track panel was added using replay or live `latitude_deg` and `longitude_deg`. It also displays:

- active telemetry source
- latitude and longitude
- flight mode
- altitude

#### Why

The Isha dashboard connected engine health to the aircraft's mission context visually. That is important because this problem is not only fault classification; it is mission reliability for a flying UAV.

#### How it improved the project

- Makes live ArduPilot integration immediately visible.
- Shows that the engine twin is conditioned by a moving flight state.
- Helps judges understand why altitude, airspeed and recovery time affect the recommendation.
- Keeps the path honest: operator confirmation does not invent a descent or alter telemetry.

### 5.7 Portable and versioned model artifacts

#### What changed

The classifier is now stored as:

```text
ml/models/fault_classifier.ubj
```

The RUL regressors and metadata remain in:

```text
ml/models/model_bundle.joblib
```

A new manifest records:

- schema version
- classifier filename and format
- feature count
- ordered class labels
- XGBoost version
- scikit-learn version
- dataset row count
- dataset SHA-256
- train/validation/test/held-out profile lists

#### Why

The Isha model files were joblib-pickled XGBoost objects. In the received checkout, the installed XGBoost runtime could not reliably deserialize the classifier (`input stream corrupted`). A native UBJ model is less coupled to Python pickle internals.

The manifest also prevents a model from being loaded against an unknown feature order or untraceable dataset.

#### How it improved the project

- More portable across judging machines.
- Easier to diagnose version mismatches.
- Reproducible artifact-to-dataset traceability.
- Safer feature and label ordering at inference time.

### 5.8 Fixed feature-contract mismatch risk

The Isha training script declares 21 classifier features:

```text
7 raw sensors + 7 absolute residuals + 7 percentage residuals
```

Its live application constructs 25 features by additionally including airspeed, load, roll and pitch. As received, this can create a runtime mismatch between the trained model and live inference.

Our final system stores the exact ordered feature list in the bundle and builds the online DataFrame using that same order. The current classifier has 36 engineered inputs comprising normalized sensors, rolling statistics/trends, flight context and flight phase indicators.

This made the training/serving contract explicit instead of relying on duplicated lists in separate files.

### 5.9 Four-frame temporal persistence

#### What changed

We evaluated consecutive-confirmation settings from three to eight inference frames. Four frames gave the best operational balance:

| Persistence | Macro-F1 | Healthy false alarms/hour | Mean warning lead |
|---:|---:|---:|---:|
| 3 frames | 0.9879 | 1.0645 | 87.8 s |
| **4 frames** | **0.9853** | **0.0** | **86.8 s** |
| 5 frames | 0.9813 | 0.0 | 85.4 s |

#### Why

Adding valve wear slightly increased momentary false predictions. For a mission system, eliminating false alarm events was worth approximately one second of warning lead and a small F1 reduction.

#### How it improved the project

- Zero measured healthy false-alarm events per simulated hour on the test profiles.
- More stable dashboard alerts.
- The chosen value is evidence-based rather than arbitrary.

### 5.10 Mission-action hysteresis

#### What changed

The recommendation still begins with the Mission Reliability Envelope, but the final displayed action now obeys safety hysteresis:

- `MONITOR_RTB` cannot briefly fall back to `CONTINUE` while a fault remains active.
- `ABORT_LAND` cannot downgrade while a fault remains active.
- A genuinely healthy/reset condition can return the system to `CONTINUE`.

#### Why

RUL estimates naturally move slightly from frame to frame. Without hysteresis, a safety-margin value close to zero could alternate between monitor and abort states.

#### How it improved the project

- Prevents alert chatter.
- Makes operator acknowledgement meaningful.
- Produces a calmer and more credible ground-control display.
- Better matches safety-system design practice.

### 5.11 Improved live ArduPilot startup, fallback and reconnection

#### What changed

The live launcher now assigns AegisTwin's dedicated TCP port 5770 to ArduPlane `SERIAL0`, not `SERIAL1`. With `--no-mavproxy`, the default primary MAVLink serial endpoint otherwise waits first and prevents the secondary endpoint from opening.

The telemetry adapter also:

- disables pymavlink's potentially blocking internal TCP auto-reconnect
- uses a bounded TCP connection timeout
- owns one-second retry timing
- detects stale streams
- closes stale sockets
- falls back to replay without blocking the backend
- reconnects automatically when SITL returns

#### Why

The Isha live application had a separate SITL-dependent entry point and waited up to 15 seconds for a heartbeat. Our objective was a single demo stack that never loses the dashboard when the live source disappears.

#### How it improved the project

The following sequence was verified:

```text
SITL unavailable -> replay remains active
SITL starts       -> source changes to ARDUPILOT_SITL
SITL stops        -> source changes back to REPLAY
SITL restarts     -> adapter reconnects and returns to ARDUPILOT_SITL
```

This is one of the most important judging-day reliability improvements.

### 5.12 Dashboard request-race fix

#### What changed

Scenario controls now send only the field that changed instead of resending an entire potentially stale React state object.

#### Why

During rapid selection of `VALVE_WEAR` followed by the `rapid` rate, the second request could contain the previous fault value and undo the first selection.

#### How it improved the project

- Rapid UI changes no longer overwrite each other.
- API requests are smaller and clearer.
- The event log and backend scenario remain consistent.

### 5.13 Reproducible evaluation outputs

The new `ml.render_evaluation_report` command reads the authoritative metrics JSON and generates:

- `reports/confusion_matrix.svg`
- `reports/model_evaluation.md`

This idea came from the useful model-report assets in the Isha branch, but our version is regenerated from the current model's metrics so the figure cannot refer to an older training run.

---

## 6. What we deliberately kept from our project

### 6.1 Strict plant/twin separation

This remains the most important architectural decision:

```text
same flight state
      |
      +--> faultable virtual engine plant --> observed sensors
      |
      +--> healthy engine twin ------------> expected sensors
                                                |
observed - expected ----------------------------+--> residuals
```

The fault command reaches only the plant. The healthy twin never receives the fault type or severity.

This is more defensible than constructing an expected row by indexing a matching recorded healthy trajectory, because it supports unseen live flight states and preserves the causal separation expected from a digital twin.

### 6.2 Profile-separated data evaluation

The Isha dataset contains four runs of the same flight trajectory and evaluates later timesteps from those same runs. That is useful for testing progression across time, but it does not establish generalization to a different mission profile.

Our generator uses 12 development profiles and separates entire profile IDs across train, validation and test. Three more profile IDs remain reserved for demonstrations.

We kept this because adjacent or repeated flight rows can make random or within-flight evaluation appear unrealistically strong.

### 6.3 Multiple operating environments

Our data retains:

- distinct flight geometries and wind cases
- ISA, hot-day and cold-day ambient offsets
- flight phase
- altitude
- airspeed
- vertical speed
- throttle
- independent noise seeds
- component baseline variation

This makes the model learn fault residual patterns under different contexts rather than associating one point in one mission with one fault.

### 6.4 Calibrated RUL uncertainty

We kept three quantile gradient-boosting regressors for low, median and high RUL. The low/high interval is calibrated on validation profiles before evaluation on test profiles.

The mission decision uses the conservative lower bound, not only the median:

```text
safety margin = RUL lower bound - time to safe recovery
```

This is more useful for mission reliability than presenting an uncalibrated `rul_frac` alone.

### 6.5 Deterministic health score

Health score remains derived from normalized physical residuals. It is not a separate learned output that can disagree unpredictably with the sensor evidence.

The classifier answers **what fault pattern is present**. The residual health score answers **how far the engine has moved away from its healthy envelope**.

### 6.6 Traditional threshold comparison

The dashboard still places conventional thresholds beside the context-aware twin. This is the shortest way to demonstrate the project's value:

```text
Legacy threshold: NORMAL
Digital twin: EARLY DEGRADATION
```

### 6.7 Replay-first judging path

Replay remains the default because it is deterministic and requires no venue networking or SITL process. Live ArduPilot is an optional proof of integration, and both paths use the same backend contract and dashboard.

### 6.8 Existing engineering discipline

We retained:

- oil pressure in bar throughout the contract
- five-hertz streaming
- explicit run IDs and noise seeds
- reset/pause/ambient controls
- FastAPI REST documentation
- WebSocket telemetry
- automated vertical-slice tests
- PowerShell/WSL launcher boundary
- honest simulation-only and advisory-only labeling

---

## 7. What we did not copy from the Isha branch, and why

### 7.1 We did not copy its trained joblib models

Reasons:

- The reference model was coupled to a different feature contract.
- The received artifact failed to deserialize reliably with the current XGBoost runtime.
- It was trained on four repetitions of one flight trace rather than our multi-profile dataset.
- Its class taxonomy did not include our ignition/misfire scenario.

We retrained on our dataset instead.

### 7.2 We did not use the reported 100% accuracy as our headline metric

The Isha score is based on a chronological split within the same underlying flight trajectory. It is not directly comparable to testing on completely different profile IDs.

Our 0.9853 macro-F1 is slightly lower numerically but more defensible because the test flight profiles are disjoint from training.

### 7.3 We did not copy the single-file HTML dashboard

Our React dashboard and frozen telemetry contract were already integrated with REST, WebSocket, live/replay source status, mission logic and multiple chart panels. We rebuilt the useful Isha UI ideas as React components rather than maintaining two frontends.

### 7.4 We did not let operator confirmation modify the aircraft path

The Isha demo visually subtracts altitude after confirmation to show a glide. That is effective theatrically, but it can imply that a real command was sent.

Our confirmation is explicitly advisory-only. A future autonomous action requires a separate safety case, permissions model, command acknowledgement and flight-controller integration.

### 7.5 We did not add a separate live backend

A separate `app_live.py` and replay application increase the chance of different schemas or behavior. Our one backend chooses live telemetry when fresh and replay otherwise.

### 7.6 We did not use a point RUL value without uncertainty

The problem statement asks for prediction and mission reliability, not merely a severity score. We retained calibrated RUL bounds and use the lower bound for decisions.

---

## 8. Final architecture

```text
Held-out replay profile                 ArduPlane SITL in WSL2
          |                                      |
          |                           MAVLink TCP SERIAL0:5770
          |                                      |
          +----------> flight source adapter <---+
                              |
                    current flight context
                              |
              +---------------+---------------+
              |                               |
              v                               v
    Faultable virtual engine            Healthy engine twin
    cooling / lubrication /             no injected fault
    ignition / valve wear                      |
              |                               |
              +---------- residuals ----------+
                              |
             normalized features + rolling context
                              |
         +--------------------+--------------------+
         |                    |                    |
       XGBoost          deterministic         calibrated
    fault classifier      health score        quantile RUL
         |                    |                    |
         +----------- mission reliability --------+
                              |
           threshold comparison + action hysteresis
                              |
                  FastAPI REST + WebSocket
                              |
        React mission console / path / log / operator
```

---

## 9. Dataset and model results after integration

All results below are synthetic/simulation-based.

| Item | Final result |
|---|---:|
| Development flight profiles | 12 |
| Additional held-out demonstration profiles | 3 |
| Generated trajectories | 360 |
| Generated samples | 86,856 |
| Fault trajectories reaching failure | 288 |
| Classification classes | 5 |
| Profile-separated test rows | 14,476 |
| Classification macro-F1 | 0.9853 |
| Healthy false-alarm events/hour | 0.0 |
| Mean warning lead before threshold | 86.8 s |
| Median warning lead before threshold | 69.5 s |
| RUL median MAE | 2.06 simulated min |
| Calibrated RUL interval coverage | 92.0% |
| RUL test samples | 7,264 |
| Runtime benchmark | 1.84 ms mean / 9.27 ms p95 |
| Telemetry/dashboard stream | 5 Hz |

The previous model's macro-F1 was approximately 0.9866 across four classes. The small numeric change to 0.9853 is not a regression in practical capability: the final model handles an additional physical fault class, supplies exact TreeSHAP, maintains zero measured healthy false-alarm events and improves RUL interval coverage from approximately 90% to 92%.

---

## 10. File-level traceability

| File | Integration work |
|---|---|
| `simulator/engine_plant.py` | Added physically propagated valve wear |
| `ml/generate_dataset.py` | Added valve-wear generation scenarios |
| `ml/train_models.py` | Added XGBoost, portable output, class balancing, four-frame persistence and versioned metrics |
| `ml/inference.py` | Loads native XGBoost artifact and computes exact per-frame TreeSHAP |
| `ml/models/fault_classifier.ubj` | Portable trained five-class classifier |
| `ml/models/model_bundle.joblib` | Feature schema, calibrated RUL models and inference metadata |
| `ml/models/model_manifest.json` | Dataset/model provenance and compatibility metadata |
| `ml/models/metrics.json` | Authoritative profile-separated evaluation results |
| `ml/render_evaluation_report.py` | Regenerates evaluation visuals from current metrics |
| `reports/confusion_matrix.svg` | Held-out profile confusion matrix |
| `reports/model_evaluation.md` | Compact evaluation report |
| `twin/health_index.py` | Valve fallback evidence and exact-SHAP-aware explanations |
| `core/contracts.py` | Explanation method and operator-response fields |
| `core/runtime.py` | Model integration, operator lifecycle and recommendation hysteresis |
| `backend/main.py` | Operator response endpoint and scenario metadata |
| `frontend/src/App.jsx` | Route shell: Spline landing page (lazy) and the mission console |
| `frontend/src/pages/` | `LandingPage` (two-stage Spline + Lenis) and `Console` (three-column operations layout) |
| `frontend/src/components/` | Header, status rail, sensor bars, residual chart, flight path, scenario lab, diagnosis, envelope, log and safety banner |
| `frontend/src/hooks/useTelemetry.js` | Telemetry socket, rolling history, audit log and scenario/operator commands |
| `frontend/src/lib/telemetry.js` | Sensor metadata, fault labels, formatting and endpoint resolution |
| `frontend/src/styles.css` | Black/neon glass design system ported from the team `main` frontend |
| `telemetry/mavlink_adapter.py` | Bounded non-blocking TCP retry, stale detection and reconnect |
| `scripts/start-sitl.sh` | Corrected dedicated ArduPlane stream to `SERIAL0` TCP 5770 |
| `requirements.txt` | Pinned XGBoost/scikit-learn compatibility and complete dependencies |
| `tests/test_vertical_slice.py` | Valve, ML explanation, operator lifecycle and action-hysteresis tests |
| `README.md` | Updated architecture, commands, artifacts and API |
| `PROTOTYPE_STATUS.md` | Current results and verified runtime behavior |
| `JUDGE_METRICS_ONE_PAGER.md` | Printable evidence and demonstration sequence |

---

## 11. Verification performed

### Automated verification

- 11/11 Python vertical-slice tests pass.
- All Python modules compile.
- The React/Vite production build passes.
- The generated SVG parses as valid XML.
- Browser console inspection showed no warnings or errors.

### Replay verification

- REST health and scenario APIs respond.
- WebSocket streams complete telemetry frames.
- Valve wear can be selected from the dashboard.
- The XGBoost model detects valve wear.
- Five TreeSHAP contributors are streamed.
- Conventional thresholds can remain normal while the twin reports early degradation.
- Operator acknowledgement is recorded and later reset when the advisory changes.
- Scenario reset returns the dashboard to a clean healthy state.

### Live ArduPilot verification

- ArduPlane SITL starts inside Ubuntu 24.04 on WSL2.
- The Windows backend connects to TCP port 5770.
- Actual SITL GPS coordinates, airspeed, altitude and mode reach the dashboard contract.
- Stopping SITL activates replay fallback without stopping the API.
- The backend remains responsive after the stream disappears.
- Restarting SITL reconnects automatically and restores the live source.

---

## 12. Why the combined project is better overall

### Better technical credibility

The project now combines a strict digital-twin plant/reference separation with a real trained boosted-tree model and mathematically exact explanations. It is neither a purely rule-based animation nor a black-box classifier without physics.

### Better evaluation credibility

The final metrics come from flight profiles excluded from training. This is a stronger claim than testing later rows of the same recorded profile, while still preserving chronological and temporal realism inside each trajectory.

### Better mission relevance

RUL is not displayed in isolation. It is compared against estimated recovery time, giving the operator an actionable mission-safety margin.

### Better human factors

The operator sees the source, fault, confidence, explanation, RUL range, safety margin, conventional threshold state, recent flight path and chronological event history before responding.

### Better judging reliability

Replay is deterministic, live SITL is demonstrable, and failure of the live source does not take down the dashboard. Model artifacts are versioned and dependencies are explicit.

### Better honesty

The system states that results are simulation-based and not flight-certified. Operator responses are acknowledgements, not pretend autonomous commands.

---

## 13. Important remaining gaps

These are not blockers for the prototype round, but the team should be ready to discuss them.

### 13.1 Unknown-fault and sensor-fault rejection

The classifier must currently choose one of its five known classes. A stronger next version should include:

- out-of-distribution detection
- an `UNKNOWN_DEGRADATION` state
- sensor stuck/bias/drift scenarios
- confidence calibration

This is especially relevant because the problem statement mentions sensor drift and combustion instability.

### 13.2 Real ECU protocol integration

The flight context is real SITL telemetry, but engine channels are generated by our physics plant because standard ArduPlane SITL does not emulate this MALE piston-engine ECU.

The next integration target should be MAVLink `EFI_STATUS` or a hardware-in-the-loop ECU adapter. This would preserve the analytics pipeline while replacing simulated engine sensors with ECU data.

### 13.3 Real-engine calibration

Before any operational claim, the team needs:

- dynamometer data
- multiple engines or component baselines
- repeat runs at altitude/load/ambient combinations
- maintenance-confirmed fault labels
- censored-life handling for engines that do not fail during observation
- calibration of healthy residual distributions

### 13.4 Persistent audit storage

The current event log is session-local in the dashboard. A later version should persist:

- raw telemetry hashes or files
- model version
- scenario/configuration
- diagnosis and confidence
- recommendation changes
- operator identity and response timestamps

This matters for post-flight investigation and model governance.

### 13.5 Security and deployment architecture

For defence deployment, add:

- authenticated APIs
- signed model artifacts
- encrypted telemetry
- role-based operator permissions
- offline/air-gapped deployment support
- tamper-evident audit logs
- resource and latency testing on the intended onboard/ground hardware

### 13.6 RUL validation beyond synthetic horizons

Our RUL label is still defined by the simulated degradation horizon. The interval calibration is valid for this simulation distribution, not yet for real engine lifetime.

### 13.7 Longer stability testing

Before the final round, run:

- a 30–60 minute healthy soak test
- repeated SITL disconnect/reconnect cycles
- all faults across all degradation rates
- dashboard reconnection with multiple browser clients
- a clean-machine installation rehearsal

---

## 14. Recommended prototype-round demonstration

### Primary deterministic demo

1. Start with `start-all.ps1` on the held-out replay profile.
2. Point out that observed and expected sensor traces agree during healthy operation.
3. Select rapid lubrication degradation or valve wear.
4. Show `Legacy thresholds: NORMAL` versus `Twin: EARLY DEGRADATION`.
5. Open the TreeSHAP evidence and explain the correlated sensor pattern.
6. Show the RUL interval decreasing.
7. Explain the safety margin: lower RUL bound minus recovery time.
8. Wait for the recommendation to move from continue to monitor/RTB and then abort/land.
9. Confirm the advisory and emphasize that no autonomous command is sent.
10. Reset to healthy.

### Optional live proof

1. Start with `start-live.ps1`.
2. Show `ARDUPILOT SITL` in the source indicator and real GPS coordinates on the path.
3. Stop SITL and show automatic `REPLAY` fallback.
4. Restart SITL and show automatic reconnection.

Do not make live SITL the only judging path.

---

## 15. Recommended pitch wording

> AegisTwin uses live flight context to run two synchronized engine paths: a faultable virtual plant and an independent healthy reference twin. Their normalized residuals feed an XGBoost classifier with exact TreeSHAP explanations and calibrated remaining-life bounds. We then compare conservative remaining life with safe recovery time, so the output is not just a fault label but a human-reviewed mission decision. The current evidence is simulation-based, profile-separated and replay-safe, with live ArduPilot integration as an optional proof.

Avoid saying:

- “The model is 100% accurate.”
- “The AI controls or lands the aircraft.”
- “This is flight-certified.”
- “These are real MALE engine failures.”
- “SHAP proves the model is correct.”

Prefer saying:

- “The five-class classifier achieved 0.9853 macro-F1 on held-out flight profiles.”
- “We measured zero healthy false-alarm events per simulated hour on the current test profiles.”
- “The twin warned an average of 86.8 simulated seconds before conventional limits.”
- “TreeSHAP explains which engineered features pushed this specific model prediction.”
- “Recommendations are advisory and require operator acknowledgement.”

---

## 16. Reproduction commands

### Install dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Rebuild the dataset

```powershell
.\.venv\Scripts\python.exe -m ml.generate_dataset
```

### Retrain models

```powershell
.\.venv\Scripts\python.exe -m ml.train_models
```

### Regenerate evaluation report

```powershell
.\.venv\Scripts\python.exe -m ml.render_evaluation_report
```

### Run tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

### Run deterministic replay

```powershell
.\start-all.ps1
```

### Run live ArduPilot mode

```powershell
.\start-live.ps1
```

### Stop all managed processes

```powershell
.\stop-all.ps1
```

---

## 17. Final conclusion

The Isha branch contributed valuable ideas in explainable XGBoost modeling, valve-wear coverage and operator-focused dashboard design. Our project contributed the stronger digital-twin separation, broader synthetic data design, profile-level evaluation, calibrated RUL uncertainty, mission safety margin and resilient replay/live architecture.

The integration is better than either starting point on its own because it combines:

```text
physical causality
+ profile-separated machine learning
+ exact model explanations
+ conservative mission reasoning
+ human acknowledgement
+ judging-day resilience
```

The result should be presented as a credible simulation-validated decision-support prototype and a clear pathway toward dynamometer, ECU and fleet-data validation—not as a finished flight-certified system.
