# SIH26054 — Prototype Roadmap v2 (merged)

**AI-Enabled Real-Time Digital Twin System for Health Monitoring, Fault Prediction and Mission Reliability Enhancement of Aero Piston Engines used in MALE UAVs**

DRDO / Dept. of Defence Production / iDEX · Software · Robotics and Drones · Team: Claude's Plan

> This merges the 4-day team roadmap with the earlier plan. The team roadmap's structure, plant/twin split, JSON contract, mission-reliability envelope and honesty framing are kept as the backbone. Corrections and additions are marked **[FIX]** and **[ADD]**.

## Implementation outcome — 2 September 2026

The prototype roadmap has now been implemented as an end-to-end judging slice:

- 360 physics-generated trajectories and 86,856 samples, split by flight profile
- Four physical degradation modes: cooling, lubrication, ignition/misfire, and valve wear
- Portable five-class XGBoost artifact with exact native TreeSHAP evidence
- Calibrated quantile RUL interval and mission safety-margin logic
- 5 Hz FastAPI/WebSocket stream with replay-first operation
- Live ArduPlane SITL input through WSL2 and verified automatic replay fallback
- Dashboard flight path, event log, scenario controls, and advisory operator acknowledgement
- Zero measured healthy false-alarm events/hour on the held-out test profiles after four-frame persistence

See `PROTOTYPE_STATUS.md` for the current verified metrics and remaining production limitations. The unchecked items below are retained as historical planning context, not as the current completion record.

---

## 0. Day 0 — pre-work, do this before Day 1 starts **[ADD]**

The 4-day plan puts "SITL running + full vertical slice" inside the first six hours. An ArduPilot source build is 10–30 minutes on a good day and an afternoon on a bad one (submodules, venv Python, gcc version, `$PATH` not finding MAVProxy). Do not spend Day 1 morning on it.

- [ ] One person clones ArduPilot and completes the first build, by hand, following the official Linux setup page
- [ ] On Windows: **use WSL2**. Native Windows SITL is not worth the trouble
- [ ] **[FIX]** `start-all.ps1` in the repo structure implies PowerShell, but SITL lives in WSL. Decide now: either run the whole stack inside WSL with a bash launcher, or write the PowerShell script to shell into WSL for the SITL process only. A mixed environment discovered on Day 4 is a demo-day failure
- [ ] Verify `sim_vehicle.py -v ArduPlane --speedup 20` starts and MAVProxy connects
- [ ] Commit one recorded mission CSV to `data/missions/` so every other track can start against real data on Day 1 morning

If SITL is not working by the end of Day 0, proceed anyway with hand-authored flight profiles (four columns: time, altitude, airspeed, throttle). The engine model and fault physics carry the marks; SITL provenance is a bonus, not a dependency.

---

## 1. Objective and golden demo

Unchanged from the team roadmap, and it is the right target:

```
Healthy mission begins            -> actual and expected sensors agree
Progressive lubrication fault     -> oil-pressure residual begins growing
Traditional threshold: NORMAL     -> digital twin: EARLY DEGRADATION
Confidence and severity rise      -> RUL and its lower bound fall
RUL < safe recovery time          -> recommendation changes to RTB / ABORT
```

**[ADD] Optional finale.** After the recommendation flips, set `SIM_ENGINE_MUL 0` in SITL so the aircraft actually loses power and the flight path visibly degrades. Prediction followed by the real thing happening is a stronger closing 30 seconds than an alert alone. Only do this if the live-SITL path is stable; skip it in replay mode.

---

## 2. Architecture

Keep the team roadmap's separation exactly as written — it is better than a single-model design:

```
ArduPilot SITL ──MAVLink──> Flight Telemetry Adapter
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
        Virtual Engine Plant                Healthy Engine Twin
          + Fault Injector                 (no fault input, ever)
                    │                               │
                    └──────────► Residuals ◄────────┘
                                    │
                      Fault Classifier + RUL Model
                                    │
                        Mission Reliability Logic
                                    │
                    FastAPI/WebSocket ──> React Dashboard
```

The plant is what you are monitoring; the twin is the healthy reference. The fault must never reach the twin.

### **[FIX]** Bound the plant/twin coefficient mismatch

The roadmap says "do not use exactly the same coefficients" — correct instinct, but unbounded mismatch produces biased residuals in healthy flight, which fails the Day 4 test "healthy climb: no false fault." Specify it:

- Mismatch of roughly **2–5%** on plant coefficients relative to twin
- **Calibrate the twin on healthy data** so residuals are approximately zero-mean across the whole healthy envelope (climb, cruise, loiter, descent), not just cruise
- Record the healthy residual standard deviation per sensor — it becomes your detection threshold and your false-alarm denominator

### Fault injection — coefficient level, not signal level

Degrade a *physical parameter* in the plant, never add an offset to an output array:

| Fault | Degrade | Sensors that move |
|---|---|---|
| Cooling degradation | Cylinder heat-transfer coefficient ↓ (and its airspeed sensitivity ↓) | CHT ↑↑, EGT ↑, oil temp ↑ |
| Lubrication degradation | Oil pump efficiency / viscosity ↓ | Oil pressure ↓↓, oil temp ↑, vibration ↑ |
| Ignition / misfire | Per-cylinder combustion efficiency ↓ | RPM ripple, EGT disturbed, vibration ↑, fuel-to-power ↑ |
| Injector fouling *(optional)* | Injector flow coefficient ↓ | Fuel flow ↓, EGT ↑ (lean), power ↓ |

One fault must propagate into several *correlated* channels through the physics. If a fault moves exactly one sensor and leaves the rest bit-identical, the classifier is detecting the ramp you drew, and "how does the fault reach the other sensors?" has no answer.

---

## 3. Data contract — corrections

Keep the frozen JSON contract and 2–5 Hz streaming. Two fixes:

**[FIX] Unit collision.** The contract says `oil_pressure_psi`. The existing `engine_master_dataset.csv` is in **bar** (mean 2.99, range 1.89–5.16). Pick one now and change the other. Recommend **bar**, since the generator and all existing data already use it. Renaming after generating 250 runs costs an hour you will not have.

**[FIX] `ambient_temp_c` has no source.** SITL will not give you a meaningful one. Compute it in the adapter from altitude via ISA plus a per-run offset parameter:

```
ambient_temp_c = 15.0 - 0.0065 * altitude_m + ambient_offset_c
```

`ambient_offset_c ∈ {0, +25, -20}` gives you ISA / hot day / cold soak. This is also how you satisfy the PS's explicit hot-weather and high-altitude requirement without recording extra SITL flights.

**[ADD] Log `profile_id`, `ambient_offset_c`, `run_id` and `noise_seed` as columns** in every generated trajectory. You need them for the split in §4 and for reproducibility.

---

## 4. Dataset — the biggest change from the team roadmap

The roadmap asks for **50–100 trajectories from at least 3 mission profiles**. That is too few profiles, and it repeats the flaw already present in the current dataset.

### The current dataset's problems (measured)

- All four source files share **identical** airspeed (9.88 m/s mean) and altitude (20.7 m mean) traces. 64,716 rows, but effectively **four samples**
- 9.9 m/s at 20 m is a low hop, not a MALE mission — no high-altitude or hot-weather coverage
- Time-aligned against healthy, cooling failure shifts CHT 1.39σ and oil temp 1.31σ while every other channel moves **exactly 0.00σ**. Too clean
- `rul_frac` bottoms out at 0.3, severity at 0.7 — **nothing ever fails**, so RUL labels are censored and the regressor never sees its target

The generator itself is sound and the fault signatures are physically sensible. Keep it; change the loop around it.

### **[FIX] Split by profile, not just by mission**

"Split by complete missions, not random rows" is right but insufficient. Two trajectories generated from the *same* recorded flight profile share the identical altitude/airspeed/throttle sequence — splitting them across train and test still leaks. With only 3 profiles this is guaranteed.

**Rule: hold out entire profiles.** Train profiles and test profiles must be disjoint sets.

### **[FIX] Targets

| | Team roadmap | v2 |
|---|---|---|
| Flight profiles | ≥ 3 | **12–15** (5 geometries × 3 wind settings) |
| Ambient conditions | ≥ 3 | 3 (ISA / +25 / −20, applied in the model) |
| Mandatory faults | 2 | **3** (cooling, lubrication, ignition/misfire) |
| Trajectories | 50–100 | **200–300** |
| Split | by mission | **by profile**, 70/15/15 |
| Fault end state | severity → 1.0 | **run every fault trajectory through to the failure event** |

Generation is a nested loop over `profile × ambient × fault × degradation_rate × seed`. At ~300 NumPy runs this is minutes, not hours — run it as a batch overnight on Day 2.

### **[ADD] Realism fixes**
- Independent noise seed per run
- Small per-run baseline offsets on each sensor (component-to-component variation) so the model cannot key on an exact healthy baseline
- Let faults perturb secondary channels through the physics, not only the primary sensor

### **[ADD] Held-out demo profiles**
Reserve 2–3 profiles the model has never seen and **run the live demo from those**. When a judge asks "is this in your training data?", the answer is a clean no. Label them clearly in `data/missions/`.

### **[ADD] Why 3 mandatory faults, not 2**
The PS names misfire, injector faults, lubrication issues, sensor drift and combustion instability. Two fault classes plus healthy is a three-class problem that looks thin next to the statement. Ignition/misfire is the most-cited of the list and shares no primary sensor with the other two, so it is cheap to add and visibly distinct. Promote it from optional to mandatory; keep injector fouling optional.

---

## 5. Models and metrics

Team roadmap's choices are correct — Random Forest / XGBoost for classification, gradient boosting for RUL, quantile regressors for the interval, weighted normalized residuals for health score. Keep the feature list (rolling residual mean, slope, std, cross-sensor relationships, flight phase).

**[FIX] Health score must be derived, not independent.** The dashboard shows health score, severity and RUL simultaneously. If health is a separate model output it will eventually disagree with severity on screen mid-demo. Define it as a deterministic function of the weighted normalized residuals, and let severity and RUL come from the models.

**[ADD] Report RUL in both `rul_minutes` and `rul_frac`.** The contract has minutes; the existing dataset has fraction. Compute one from the other rather than maintaining two label pipelines.

Metrics list from Day 4 is good and should not be trimmed:

- Classification precision / recall / macro-F1 + confusion matrix
- **False alarms per healthy simulated mission-hour**
- **Detection lead time** before conventional threshold (the headline number)
- RUL MAE and interval coverage
- End-to-end latency, telemetry in to recommendation out
- All labelled synthetic / simulation-based

---

## 6. What the team roadmap does better than the earlier plan — keep all of it

1. **Plant/twin separation.** Cleaner than injecting into the same model that produces expectations. This is the correct digital-twin architecture and it is what makes the residual story credible
2. **Frozen JSON contract with explicit field names.** Much stronger than "freeze the schema"
3. **Mission Reliability Envelope** — `safety margin = conservative RUL lower bound − safe recovery time`. This maps directly onto "Mission Reliability Enhancement" in the PS title. It is the most distinctive idea in the whole plan; make sure it gets 20 seconds of the 3-minute demo
4. **Traditional-threshold vs digital-twin comparison shown side by side.** Best single demo device in the document
5. **RUL uncertainty interval** rather than a point estimate
6. **Honesty framing** — "simulation-defined RUL", advisory not flight-certified, migration path to dynamometer calibration. DRDO judges will respond well to this. Do not soften it
7. **SHAP fallback** — largest normalized residual contributors, not called SHAP. Correct risk management
8. **Test matrix including healthy-climb false-alarm case.** Easy to forget, and it is the case that fails if the plant/twin mismatch is unbounded
9. **Six-member allocation** with integration jointly owned

---

## 7. Additions worth folding in from the earlier plan

- **Emit telemetry as MAVLink `EFI_STATUS`.** That message already carries RPM, cylinder head temperature, exhaust gas temperature, fuel flow, injection timing, ignition timing, fuel pressure, engine load and throttle position, and ArduPilot has a real EFI driver layer plus a MegaSquirt EFI simulation in SITL. Using the real protocol field-for-field makes §Day-4's "migration path" claim concrete: swap simulated EFI for a real ECU and nothing downstream changes. Low cost, high credibility
- **Do not run live SITL at judging.** The roadmap already mandates replay as fallback — go further and make **replay the default demo path**, with live SITL as the optional flourish if the venue cooperates. A SITL crash in front of judges is an unrecoverable five minutes
- **`SIM_ENGINE_MUL` finale** (§1)
- **Ambient temperature as a model input, not a SITL output** (§3) — this multiplies your profiles 3× for free
- **Present the data generator as a deliverable in its own right**, not as an apology for lacking real data. Real MALE UAV engine telemetry is defence-controlled; a physics-informed generator is the correct engineering response, not a workaround

---

## 8. Revised 4-day plan

Structure and acceptance criteria are the team roadmap's. Changes marked.

### Day 0 (pre-work) **[ADD]**
ArduPilot built, WSL boundary decided, one mission CSV committed.

### Day 1 — data pipeline
As written, plus:
- **[FIX]** Resolve the bar/psi unit collision in the morning freeze, before any code is written
- **[FIX]** Add `ambient_offset_c` to the flight block and implement the ISA lapse formula in the adapter
- **[ADD]** Calibrate the twin against healthy data and record per-sensor healthy residual σ
- **[ADD]** Record 12–15 profiles in the evening, not 1

Acceptance criteria unchanged, plus: healthy residuals are zero-mean across all flight phases.

### Day 2 — faults, dataset, models
As written, plus:
- **[FIX]** Ignition/misfire is mandatory, implemented alongside cooling and lubrication
- **[FIX]** Every fault trajectory runs through to the failure event
- **[FIX]** 200–300 trajectories, split **by profile**
- **[ADD]** Per-run noise seeds and baseline offsets
- **[ADD]** Reserve 2–3 profiles as held-out demo missions

### Day 3 — dashboard and mission intelligence
As written. No changes — the layout, explanation panel, scenario controls, mission reliability envelope and threshold comparison are all correct.

Consider promoting the **traditional-vs-twin comparison** to the top strip rather than a lower panel. It is the fastest way to make a judge understand the value in five seconds.

### Day 4 — validation, hardening, rehearsal
As written. Add to the hardening checklist:
- [ ] **[ADD]** Confirm the demo replay uses a held-out profile, and be ready to say so
- [ ] **[ADD]** Print the metrics table on paper — one page, for the judges to hold

---

## 9. Open decisions to settle on Day 1 morning

1. Oil pressure: **bar** or psi? (recommend bar — matches existing data)
2. WSL-only stack, or PowerShell launcher that shells into WSL?
3. `rul_minutes` or `rul_frac` as the primary label? (compute the other from it)
4. Is injector fouling in scope, or is misfire the third and final fault?
5. Who owns the held-out profile list, and where is it recorded?

---

## Appendix — reference datasets

For benchmarking and citation only; none has a CHT column to degrade, so none can be a substrate for injection.

- **ALFA (CMU AirLab)** — real fixed-wing UAV flights with labelled faults; 47 flights, 23 sudden full engine failure scenarios plus 24 across seven control-surface faults, with ground-truth fault times. `theairlab.org/alfa-dataset`
- **3500-DEFault (Mendeley)** — diesel engine fault diagnosis from in-cylinder pressure and crankshaft torsional vibration; 84 features × 3500 samples across conditions and severity levels. `data.mendeley.com/datasets/k22zxz29kr`
- **NASA C-MAPSS / N-CMAPSS (PCoE)** — the standard RUL benchmark, run-to-failure trajectories, 21 sensor channels, FD001–FD004. Turbofan, so use it to validate RUL methodology, then transfer
- **Mendeley journal-bearing vibration set** (`3fcrrdjjvk`) — real tri-axial accelerometer data, healthy vs faulty IC engine
